"""
최근 5영업일 백테스트 — FDR 일봉 기반 간이 시뮬레이션.

KIS 분봉 API가 당일만 지원하므로, 일봉(OHLCV)으로 전략 시뮬레이션.
  - 일봉 High = 당일 고가 (신고가 달성 여부 + 고가 확정 기준)
  - 일봉 Low = 당일 저가 (매수가 도달 여부, 손절가 도달 여부)
  - 목표가 = (High + 매수가) / 2
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, time, timedelta
from dataclasses import dataclass
from typing import Optional
from collections import Counter

import pandas as pd
from loguru import logger

import FinanceDataReader as fdr
from config.settings import Settings, StrategyParams

# ── 설정 ──────────────────────────────────────────────────────

INITIAL_CASH = 100_000_000
COST_PCT = 0.5  # %
VOLUME_MIN = 50_000_000_000  # 500억
MAX_CHANGE_PCT = 29.5
TOP_UNIVERSE = 300

ETF_KEYWORDS = [
    "KODEX", "TIGER", "KOSEF", "KBSTAR", "ARIRANG", "HANARO",
    "SOL", "PLUS", "ACE", "BNK", "파워", "레버리지", "인버스",
    "ETN", "스팩", "SPAC",
]


@dataclass
class TradeResult:
    trade_date: date
    code: str
    name: str
    market: str
    high: int = 0
    buy1_price: int = 0
    buy2_price: int = 0
    entry_price: int = 0
    exit_price: int = 0
    exit_reason: str = "NO_ENTRY"
    pnl_pct: float = 0.0
    pnl_amount: float = 0.0


def get_universe() -> pd.DataFrame:
    """현재 KRX 상장종목 중 시총 상위 N개 (ETF/우선주 제외)."""
    df = fdr.StockListing("KRX")
    mask = ~df["Name"].str.contains("|".join(ETF_KEYWORDS), na=False)
    mask &= ~df["Code"].str[-1].isin(["5", "7", "8", "9"])
    mask &= ~df["Name"].str.endswith(("우", "우B", "우C"))
    df = df[mask].copy()
    df = df.nlargest(TOP_UNIVERSE, "Marcap")
    return df[["Code", "Name", "Market"]].reset_index(drop=True)


def get_trading_days(n: int = 5) -> list[str]:
    """최근 N 영업일 (YYYYMMDD 리스트)."""
    end = date.today()
    start = end - timedelta(days=20)
    df = fdr.DataReader("005930", start.strftime("%Y-%m-%d"), (end - timedelta(days=1)).strftime("%Y-%m-%d"))
    dates = [d.strftime("%Y%m%d") for d in df.index[-n:]]
    return dates


def screen_day(universe: pd.DataFrame, ohlcv_cache: dict, date_str: str) -> list[dict]:
    """해당일 스크리닝 — 거래대금 500억+, 상승, 등락률 상위."""
    candidates = []
    target_date = pd.Timestamp(datetime.strptime(date_str, "%Y%m%d"))

    for _, row in universe.iterrows():
        code = row["Code"]
        name = row["Name"]
        market = row["Market"]

        if code not in ohlcv_cache or ohlcv_cache[code].empty:
            continue

        df = ohlcv_cache[code]
        if target_date not in df.index:
            continue

        r = df.loc[target_date]
        close = float(r.get("Close", 0))
        opn = float(r.get("Open", 0))
        high = float(r.get("High", 0))
        low = float(r.get("Low", 0))
        volume = float(r.get("Volume", 0))

        if close <= 0 or opn <= 0:
            continue

        avg_p = (high + low) / 2
        volume_krw = volume * avg_p
        change_pct = (close / opn - 1) * 100

        if volume_krw < VOLUME_MIN:
            continue
        if change_pct >= MAX_CHANGE_PCT or change_pct <= 0:
            continue

        candidates.append({
            "code": code,
            "name": name,
            "market": market,
            "change_pct": round(change_pct, 2),
            "volume_krw": int(volume_krw),
            "open": int(opn),
            "high": int(high),
            "low": int(low),
            "close": int(close),
        })

    candidates.sort(key=lambda x: x["change_pct"], reverse=True)
    return candidates[:3]


def simulate_daily(
    trade_date: date,
    candidate: dict,
    params: StrategyParams,
) -> TradeResult:
    """일봉 기반 전략 시뮬레이션.

    일봉으로 시뮬레이션하는 한계:
    - 분봉 순서를 알 수 없으므로 보수적 가정 적용
    - 신고가 = 일봉 High > Open (장중 고가 달성)
    - 매수 트리거: Low가 buy1/buy2 가격 이하까지 내려왔는지
    - 청산: Low가 hard_stop 이하 → 손절 / High가 target 이상 → 목표도달
    - 둘 다 해당 시: 보수적으로 손절 우선 판정
    """
    code = candidate["code"]
    name = candidate["name"]
    market = candidate["market"]
    ep = params.entry
    xp = params.exit

    result = TradeResult(trade_date=trade_date, code=code, name=name, market=market)

    opn = candidate["open"]
    high = candidate["high"]
    low = candidate["low"]
    close = candidate["close"]

    # 고가가 시가보다 높아야 신고가 가능
    if high <= opn:
        return result

    result.high = high

    # 고가 확정 조건: 종가가 고가 대비 1% 이상 하락
    high_confirm_drop = ep.high_confirm_drop_pct / 100
    if close > high * (1 - high_confirm_drop):
        # 고가에서 1% 이상 안 빠짐 → 고가 미확정
        result.exit_reason = "NO_ENTRY"
        return result

    # 매수 가격 계산
    if market == "KOSPI":
        buy1_drop = ep.kospi_buy1_pct / 100
        buy2_drop = ep.kospi_buy2_pct / 100
        hard_stop_pct = xp.kospi_hard_stop_pct / 100
    else:
        buy1_drop = ep.kosdaq_buy1_pct / 100
        buy2_drop = ep.kosdaq_buy2_pct / 100
        hard_stop_pct = xp.kosdaq_hard_stop_pct / 100

    buy1_price = int(high * (1 - buy1_drop))
    buy2_price = int(high * (1 - buy2_drop))
    hard_stop_price = int(high * (1 - hard_stop_pct))

    result.buy1_price = buy1_price
    result.buy2_price = buy2_price

    # 매수 체결 여부
    buy1_filled = low <= buy1_price
    buy2_filled = low <= buy2_price

    if not buy1_filled and not buy2_filled:
        # 눌림 부족 → 매매 없음
        return result

    # 평균 매수가 계산
    buy1_ratio = ep.buy1_ratio / 100
    buy2_ratio = ep.buy2_ratio / 100

    entry_amount = 0
    entry_qty = 0

    if buy1_filled:
        qty1 = int(INITIAL_CASH * buy1_ratio / buy1_price) if buy1_price > 0 else 0
        entry_amount += buy1_price * qty1
        entry_qty += qty1

    if buy2_filled:
        qty2 = int(INITIAL_CASH * buy2_ratio / buy2_price) if buy2_price > 0 else 0
        entry_amount += buy2_price * qty2
        entry_qty += qty2

    if entry_qty == 0:
        return result

    avg_price = entry_amount // entry_qty
    result.entry_price = avg_price

    # 청산 판정 (일봉 기반, 보수적)
    # 눌림 최저 = low (최악의 경우)
    target_price = (high + low) / 2

    # Case 1: 하드 손절 (low가 hard stop 이하)
    if low <= hard_stop_price:
        # 목표가도 동시에 달성 가능한지 체크
        # 보수적: 손절 우선
        result.exit_price = hard_stop_price
        result.exit_reason = "HARD_STOP"

    # Case 2: 목표가 도달 (close 또는 반등으로 target 도달)
    elif close >= target_price:
        result.exit_price = int(target_price)
        result.exit_reason = "TARGET"

    # Case 3: 종가 청산 (목표가 미도달, 강제 청산)
    else:
        result.exit_price = close
        result.exit_reason = "FORCE_LIQUIDATE"

    # P&L 계산
    if result.entry_price > 0 and result.exit_price > 0:
        raw_pnl = (result.exit_price - result.entry_price) / result.entry_price * 100
        result.pnl_pct = round(raw_pnl - COST_PCT, 2)
        result.pnl_amount = round(INITIAL_CASH * result.pnl_pct / 100)

    return result


def run_backtest():
    params = StrategyParams.load()

    print("=" * 70)
    print("  AUTOTRADE 백테스트 — 최근 5영업일 (일봉 기반)")
    print("=" * 70)

    days = get_trading_days(5)
    print(f"  기간: {days[0]} ~ {days[-1]}")
    print(f"  투자금: {INITIAL_CASH:,}원 | 거래비용: {COST_PCT}%")
    print(f"  * KIS 분봉 API는 당일만 지원 → 일봉 OHLCV 기반 시뮬레이션")

    # 유니버스
    print(f"\n  유니버스 구성 중 (시총 상위 {TOP_UNIVERSE}개)...")
    universe = get_universe()
    print(f"  유니버스: {len(universe)}종목")

    # 일봉 수집
    start_date = (datetime.strptime(days[0], "%Y%m%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = datetime.strptime(days[-1], "%Y%m%d").strftime("%Y-%m-%d")
    print(f"  일봉 데이터 수집 중 ({start_date} ~ {end_date})...")

    ohlcv_cache = {}
    fail_count = 0
    for i, row in universe.iterrows():
        code = row["Code"]
        try:
            df = fdr.DataReader(code, start_date, end_date)
            ohlcv_cache[code] = df
        except Exception:
            fail_count += 1
            ohlcv_cache[code] = pd.DataFrame()
        if (i + 1) % 50 == 0:
            print(f"    {i + 1}/{len(universe)} 수집 완료...")

    print(f"  일봉 수집 완료: {len(ohlcv_cache) - fail_count}종목 성공, {fail_count} 실패")
    print("=" * 70)

    all_results: list[TradeResult] = []

    for date_str in days:
        d = datetime.strptime(date_str, "%Y%m%d").date()
        print(f"\n{'─' * 70}")
        print(f"  [{d}] 스크리닝 중...")

        candidates = screen_day(universe, ohlcv_cache, date_str)
        if not candidates:
            print(f"  [{d}] 조건 충족 종목 없음")
            all_results.append(TradeResult(trade_date=d, code="-", name="-", market="-"))
            continue

        target = candidates[0]
        print(f"  [{d}] 타겟: {target['name']}({target['code']}) "
              f"{target['market']} 등락{target['change_pct']:+.2f}% "
              f"거래대금{target['volume_krw'] / 1e8:,.0f}억")
        print(f"         시가{target['open']:,} 고가{target['high']:,} "
              f"저가{target['low']:,} 종가{target['close']:,}")

        result = simulate_daily(d, target, params)
        all_results.append(result)

        if result.exit_reason == "NO_ENTRY":
            print(f"  [{d}] 눌림 미발생 — 매매 없음")
        else:
            sign = "+" if result.pnl_pct > 0 else "-"
            print(f"  [{d}] [{sign}] 고가 {result.high:,} | "
                  f"매수1 {result.buy1_price:,} 매수2 {result.buy2_price:,}")
            print(f"         평균매수 {result.entry_price:,}원 "
                  f"→ 청산 {result.exit_price:,}원 ({result.exit_reason})")
            print(f"         P&L: {result.pnl_pct:+.2f}% ({result.pnl_amount:+,.0f}원)")

    # ── 리포트 ────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"  백테스트 결과 요약")
    print(f"{'=' * 70}")

    traded = [r for r in all_results if r.exit_reason != "NO_ENTRY"]
    no_entry = [r for r in all_results if r.exit_reason == "NO_ENTRY"]
    wins = [r for r in traded if r.pnl_pct > 0]
    losses = [r for r in traded if r.pnl_pct <= 0]

    print(f"  총 영업일: {len(days)}일")
    print(f"  매매 실행: {len(traded)}건 | 미진입: {len(no_entry)}건")

    if traded:
        win_rate = len(wins) / len(traded) * 100
        total_pnl = sum(r.pnl_pct for r in traded)
        total_amount = sum(r.pnl_amount for r in traded)
        avg_pnl = total_pnl / len(traded)

        print(f"  승률: {win_rate:.0f}% ({len(wins)}승 {len(losses)}패)")
        print(f"  총 수익률: {total_pnl:+.2f}%")
        print(f"  총 P&L: {total_amount:+,.0f}원")
        print(f"  평균 수익률: {avg_pnl:+.2f}%")
        if wins:
            print(f"  최대 수익: {max(r.pnl_pct for r in wins):+.2f}%")
        if losses:
            print(f"  최대 손실: {min(r.pnl_pct for r in losses):+.2f}%")

        print(f"\n  청산 사유:")
        reasons = Counter(r.exit_reason for r in traded)
        for reason, count in reasons.most_common():
            avg = sum(r.pnl_pct for r in traded if r.exit_reason == reason) / count
            print(f"    {reason:20s}: {count}건 (평균 {avg:+.2f}%)")
    else:
        print(f"  매매 없음")

    print(f"\n  일별 상세:")
    print(f"  {'날짜':12s} {'종목':12s} {'시장':6s} {'고가':>10s} {'진입가':>10s} "
          f"{'청산가':>10s} {'사유':18s} {'P&L':>8s}")
    print(f"  {'─' * 86}")
    for r in all_results:
        if r.exit_reason == "NO_ENTRY":
            print(f"  {str(r.trade_date):12s} {r.name:12s} {r.market:6s} "
                  f"{'─':>10s} {'─':>10s} {'─':>10s} {'미진입':18s} {'─':>8s}")
        else:
            print(f"  {str(r.trade_date):12s} {r.name:12s} {r.market:6s} "
                  f"{r.high:>10,d} {r.entry_price:>10,d} {r.exit_price:>10,d} "
                  f"{r.exit_reason:18s} {r.pnl_pct:>+7.2f}%")

    print(f"\n{'=' * 70}")
    cumulative = INITIAL_CASH
    print(f"  누적 자산 (초기 {INITIAL_CASH:,}원):")
    for r in all_results:
        if r.exit_reason != "NO_ENTRY":
            cumulative += r.pnl_amount
        print(f"    {str(r.trade_date):12s} → {cumulative:>15,.0f}원")
    final_pct = (cumulative / INITIAL_CASH - 1) * 100
    print(f"\n  최종 자산: {cumulative:,.0f}원 (수익률 {final_pct:+.2f}%)")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    run_backtest()
