"""
오늘 장중 데이터로 멀티 트레이드 시뮬레이션.

09:50 스크리닝 후보 5종목에 대해:
  - 분봉 데이터 수집
  - 전략 상태머신 재현 (신고가 → 고가확정 → 매수 → 청산)
  - 멀티 트레이드 흐름 (수익 청산 → 다음 종목)
  - P&L 계산
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from config.settings import Settings, StrategyParams
from src.kis_api.kis import KISAPI


# ── 시뮬레이션용 모델 ────────────────────────────────────────

@dataclass
class SimCandle:
    time_str: str           # "HHMMSS"
    open: int
    high: int
    low: int
    close: int
    volume: int

    @property
    def time_obj(self) -> time:
        h, m, s = int(self.time_str[:2]), int(self.time_str[2:4]), int(self.time_str[4:6])
        return time(h, m, s)

    @property
    def dt(self) -> datetime:
        return datetime.combine(datetime.today(), self.time_obj)


@dataclass
class SimTrade:
    """시뮬레이션 매매 기록."""
    code: str
    name: str
    market: str

    # 고가 추적
    intraday_high: int = 0
    intraday_high_time: str = ""
    high_confirmed: bool = False
    high_confirmed_price: int = 0

    # 매수
    buy1_price: int = 0
    buy2_price: int = 0
    buy1_filled: bool = False
    buy2_filled: bool = False
    buy1_fill_time: str = ""
    buy2_fill_time: str = ""
    avg_price: float = 0.0
    total_qty: int = 0
    total_amount: int = 0

    # 청산
    exit_price: int = 0
    exit_time: str = ""
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0

    # 상태
    state: str = "WATCHING"
    high_confirmed_time: str = ""      # 고가 확정 시각
    post_entry_low: int = 0
    post_entry_low_time: str = ""
    minute_lows: list = field(default_factory=list)
    last_minute: str = ""

    # 신고가 전 고가 (9:55 이전)
    pre_955_high: int = 0


async def fetch_all_candles(api: KISAPI, code: str) -> list[SimCandle]:
    """분봉 데이터 전체 수집 (09:00~현재). KIS API는 한번에 약 30개 반환."""
    all_candles = []
    seen_times = set()

    # 30분 간격으로 촘촘히 호출 (KIS API는 한번에 ~30개 = 30분치만 반환)
    time_points = [
        "153000", "150000", "143000", "140000", "133000", "130000",
        "123000", "120000", "113000", "110000", "103000", "100000",
        "093000",
    ]

    for tp in time_points:
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": tp,
            "FID_PW_DATA_INCU_YN": "N",
        }
        try:
            from src.kis_api.constants import EP_MINUTE_CHART, TR_MINUTE_CHART
            data = await api._get(EP_MINUTE_CHART, TR_MINUTE_CHART, params)
            for item in data.get("output2", []):
                t = item.get("stck_cntg_hour", "")
                if t and t not in seen_times:
                    seen_times.add(t)
                    all_candles.append(SimCandle(
                        time_str=t,
                        open=int(item.get("stck_oprc", "0")),
                        high=int(item.get("stck_hgpr", "0")),
                        low=int(item.get("stck_lwpr", "0")),
                        close=int(item.get("stck_prpr", "0")),
                        volume=int(item.get("cntg_vol", "0")),
                    ))
        except Exception as e:
            logger.warning(f"분봉 조회 실패 ({code} {tp}): {e}")
        await asyncio.sleep(0.6)  # rate limit

    # 시간순 정렬 (오름차순)
    all_candles.sort(key=lambda c: c.time_str)
    return all_candles


def _tick_size(price: int) -> int:
    if price < 2_000: return 1
    elif price < 5_000: return 5
    elif price < 20_000: return 10
    elif price < 50_000: return 50
    elif price < 200_000: return 100
    elif price < 500_000: return 500
    else: return 1_000


def _upper_limit_price(prev_close: int) -> int:
    raw = prev_close * 1.30
    tick = _tick_size(int(raw))
    return int(raw // tick) * tick


def simulate_strategy(
    candles: list[SimCandle],
    code: str,
    name: str,
    market: str,
    params: StrategyParams,
    available_cash: int,
    prev_close: int = 0,
) -> SimTrade:
    """단일 종목에 대해 전략 시뮬레이션."""
    trade = SimTrade(code=code, name=name, market=market)
    ep = params.entry
    ex = params.exit

    # 상한가 계산
    upper_limit = _upper_limit_price(prev_close) if prev_close > 0 else 0

    # 매수 비율
    is_kospi = market == "KOSPI"
    buy1_pct = ep.kospi_buy1_pct if is_kospi else ep.kosdaq_buy1_pct
    buy2_pct = ep.kospi_buy2_pct if is_kospi else ep.kosdaq_buy2_pct
    hard_stop_pct = ex.kospi_hard_stop_pct if is_kospi else ex.kosdaq_hard_stop_pct
    drop_confirm_pct = ep.high_confirm_drop_pct

    watch_start = time(9, 55)
    entry_deadline = time.fromisoformat(params.entry.entry_deadline)

    for candle in candles:
        ct = candle.time_obj
        price_high = candle.high
        price_low = candle.low
        price_close = candle.close

        # ── WATCHING: 신고가 감시 ──
        if trade.state == "WATCHING":
            if ct < watch_start:
                # 9:55 이전: 고가만 기록
                if price_high > trade.pre_955_high:
                    trade.pre_955_high = price_high
                    trade.intraday_high = price_high
                    trade.intraday_high_time = candle.time_str
                continue

            # 9:55 이후: 신고가 달성 체크
            if price_high > trade.pre_955_high:
                trade.intraday_high = price_high
                trade.intraday_high_time = candle.time_str
                trade.state = "TRACKING"
                # 이 캔들 내에서 하락 체크도 진행
            else:
                # 고가 갱신은 계속
                if price_high > trade.intraday_high:
                    trade.intraday_high = price_high
                    trade.intraday_high_time = candle.time_str
                continue

        # ── TRACKING: 고가 추적 + 1% 하락 트리거 ──
        if trade.state == "TRACKING":
            # ★ 매수 진입 마감 시각 체크
            if ct >= entry_deadline:
                trade.state = "DEADLINE"
                trade.exit_reason = f"진입마감({params.entry.entry_deadline})"
                continue

            # 이 캔들에서 고가 갱신?
            if price_high > trade.intraday_high:
                trade.intraday_high = price_high
                trade.intraday_high_time = candle.time_str

            # ★ 상한가 도달 체크 → 매매 불가
            if upper_limit > 0 and trade.intraday_high >= upper_limit:
                trade.state = "LIMIT_HIT"
                trade.exit_reason = f"상한가도달({upper_limit:,})"
                continue

            # 1% 하락 체크 (캔들 저가 기준)
            trigger_price = int(trade.intraday_high * (1 - drop_confirm_pct / 100))
            if price_low <= trigger_price:
                trade.high_confirmed = True
                trade.high_confirmed_price = trade.intraday_high
                trade.high_confirmed_time = candle.time_str
                trade.state = "HIGH_CONFIRMED"

                # 매수 지정가 계산
                trade.buy1_price = int(trade.intraday_high * (1 - buy1_pct / 100))
                trade.buy2_price = int(trade.intraday_high * (1 - buy2_pct / 100))

                # 이 캔들에서 체결 시뮬
                if price_low <= trade.buy1_price:
                    buy1_amount = int(available_cash * ep.buy1_ratio / 100)
                    buy1_qty = max(1, buy1_amount // trade.buy1_price) if trade.buy1_price > 0 else 0
                    trade.buy1_filled = True
                    trade.buy1_fill_time = candle.time_str
                    trade.total_amount += trade.buy1_price * buy1_qty
                    trade.total_qty += buy1_qty

                if price_low <= trade.buy2_price:
                    buy2_amount = int(available_cash * ep.buy2_ratio / 100)
                    buy2_qty = max(1, buy2_amount // trade.buy2_price) if trade.buy2_price > 0 else 0
                    trade.buy2_filled = True
                    trade.buy2_fill_time = candle.time_str
                    trade.total_amount += trade.buy2_price * buy2_qty
                    trade.total_qty += buy2_qty

                if trade.total_qty > 0:
                    trade.avg_price = trade.total_amount / trade.total_qty
                    trade.state = "ENTERED"
                    trade.post_entry_low = price_low
                    trade.post_entry_low_time = candle.time_str
                continue

        # ── HIGH_CONFIRMED: 매수 대기 (아직 체결 안 됨) ──
        if trade.state == "HIGH_CONFIRMED":
            # ★ 매수 진입 마감 시각 체크
            if ct >= entry_deadline:
                trade.state = "DEADLINE"
                trade.exit_reason = f"진입마감({params.entry.entry_deadline})"
                continue

            # ★ 고가 확정 후 N분 타임아웃 → 모멘텀 소멸
            timeout_min = params.entry.high_confirm_timeout_min
            if trade.high_confirmed_time:
                hc_t = time(int(trade.high_confirmed_time[:2]),
                           int(trade.high_confirmed_time[2:4]),
                           int(trade.high_confirmed_time[4:6]) if len(trade.high_confirmed_time) >= 6 else 0)
                hc_dt = datetime.combine(datetime.today(), hc_t)
                if (candle.dt - hc_dt).total_seconds() >= timeout_min * 60:
                    trade.state = "MOMENTUM_LOST"
                    trade.exit_reason = f"고가확정후 {timeout_min}분 미체결"
                    continue

            # 고가 갱신 → 리셋
            if price_high > trade.intraday_high:
                trade.intraday_high = price_high
                trade.intraday_high_time = candle.time_str

                # ★ 상한가 도달 체크
                if upper_limit > 0 and trade.intraday_high >= upper_limit:
                    trade.state = "LIMIT_HIT"
                    trade.exit_reason = f"상한가도달({upper_limit:,})"
                    continue

                trade.buy1_price = int(trade.intraday_high * (1 - buy1_pct / 100))
                trade.buy2_price = int(trade.intraday_high * (1 - buy2_pct / 100))
                trade.state = "TRACKING"
                # 이 캔들에서 다시 하락 체크
                trigger_price = int(trade.intraday_high * (1 - drop_confirm_pct / 100))
                if price_low <= trigger_price:
                    trade.state = "HIGH_CONFIRMED"
                    trade.high_confirmed_time = candle.time_str  # 타임아웃 기준 리셋
                continue

            # 체결 시뮬
            if not trade.buy1_filled and price_low <= trade.buy1_price:
                buy1_amount = int(available_cash * ep.buy1_ratio / 100)
                buy1_qty = max(1, buy1_amount // trade.buy1_price) if trade.buy1_price > 0 else 0
                trade.buy1_filled = True
                trade.buy1_fill_time = candle.time_str
                trade.total_amount += trade.buy1_price * buy1_qty
                trade.total_qty += buy1_qty

            if not trade.buy2_filled and price_low <= trade.buy2_price:
                buy2_amount = int(available_cash * ep.buy2_ratio / 100)
                buy2_qty = max(1, buy2_amount // trade.buy2_price) if trade.buy2_price > 0 else 0
                trade.buy2_filled = True
                trade.buy2_fill_time = candle.time_str
                trade.total_amount += trade.buy2_price * buy2_qty
                trade.total_qty += buy2_qty

            if trade.total_qty > 0:
                trade.avg_price = trade.total_amount / trade.total_qty
                trade.state = "ENTERED"
                trade.post_entry_low = price_low
                trade.post_entry_low_time = candle.time_str
            continue

        # ── ENTERED: 청산 조건 모니터링 ──
        if trade.state == "ENTERED":
            # 최저가 갱신/재터치 → minute_lows 리셋 (눌림 최저가 "이후" higher lows만 추적)
            # <= 사용: 더블바텀(같은 가격 재터치)도 바닥 형성 중으로 간주
            if price_low <= trade.post_entry_low:
                trade.post_entry_low = price_low
                trade.post_entry_low_time = candle.time_str
                trade.minute_lows = []  # 최저가 갱신/재터치 시 리셋
                trade.last_minute = ""

            # 1분봉 저가 추적 (추세 이탈용) — 최저가 이후 분봉만
            current_minute = candle.time_str[:4]  # HHMM
            if current_minute != trade.last_minute:
                trade.minute_lows.append(price_low)
                trade.last_minute = current_minute
            else:
                if trade.minute_lows and price_low < trade.minute_lows[-1]:
                    trade.minute_lows[-1] = price_low

            # ① 하드 손절
            hard_stop_price = int(trade.intraday_high * (1 - hard_stop_pct / 100))
            if price_low <= hard_stop_price:
                trade.exit_price = hard_stop_price
                trade.exit_time = candle.time_str
                trade.exit_reason = "하드손절"
                trade.state = "EXITED"
                break

            # ② 추세 이탈 (higher lows 깨짐) — 최소 3개 분봉
            if len(trade.minute_lows) >= 3:
                if trade.minute_lows[-1] < trade.minute_lows[-2]:
                    trade.exit_price = price_close
                    trade.exit_time = candle.time_str
                    trade.exit_reason = "추세이탈"
                    trade.state = "EXITED"
                    break

            # ③ 25분 타임아웃
            if trade.post_entry_low_time:
                low_dt = datetime.combine(datetime.today(), time(
                    int(trade.post_entry_low_time[:2]),
                    int(trade.post_entry_low_time[2:4]),
                    int(trade.post_entry_low_time[4:6]) if len(trade.post_entry_low_time) >= 6 else 0
                ))
                now_dt = candle.dt
                if (now_dt - low_dt).total_seconds() >= ex.timeout_from_low_min * 60:
                    trade.exit_price = price_close
                    trade.exit_time = candle.time_str
                    trade.exit_reason = "25분타임아웃"
                    trade.state = "EXITED"
                    break

            # ④ 목표가 = (고가 + 눌림최저가) / 2
            target_price = (trade.intraday_high + trade.post_entry_low) / 2
            if target_price > 0 and price_high >= target_price:
                trade.exit_price = int(target_price)
                trade.exit_time = candle.time_str
                trade.exit_reason = "목표가도달"
                trade.state = "EXITED"
                break

            # ⑥ 15:20 강제 청산
            if ct >= time(15, 20):
                trade.exit_price = price_close
                trade.exit_time = candle.time_str
                trade.exit_reason = "강제청산(15:20)"
                trade.state = "EXITED"
                break

    # 아직 ENTERED 상태 (장중 미청산)
    if trade.state == "ENTERED" and candles:
        trade.exit_price = candles[-1].close
        trade.exit_time = candles[-1].time_str
        trade.exit_reason = "장중(미청산)"

    # P&L 계산
    if trade.total_qty > 0 and trade.exit_price > 0:
        trade.pnl = (trade.exit_price - trade.avg_price) * trade.total_qty
        trade.pnl_pct = (trade.exit_price - trade.avg_price) / trade.avg_price * 100

    return trade


async def run_simulation():
    """오늘 데이터로 멀티 트레이드 시뮬레이션."""
    settings = Settings()
    params = StrategyParams.load()
    api = KISAPI(
        app_key=settings.kis_app_key,
        app_secret=settings.kis_app_secret,
        account_no=settings.account_no,
        is_paper=settings.is_paper_mode,
    )

    await api.connect()

    # 후보 5종목 (강화 필터 적용 스크리닝 결과)
    candidates = [
        {"code": "010170", "name": "대한광통신", "market": "KOSPI"},
        {"code": "093370", "name": "후성", "market": "KOSPI"},
        {"code": "010820", "name": "퍼스텍", "market": "KOSPI"},
        {"code": "475150", "name": "SK이터닉스", "market": "KOSPI"},
        {"code": "001250", "name": "GS글로벌", "market": "KOSPI"},
    ]

    # 예수금 (시뮬레이션용 1000만원)
    try:
        balance = await api.get_balance()
        available_cash = balance.get("available_cash", 10_000_000)
    except:
        available_cash = 10_000_000

    print("=" * 70)
    print(f" 오늘({datetime.now().strftime('%Y-%m-%d')}) 멀티 트레이드 시뮬레이션")
    print(f" 예수금: {available_cash:,}원 | 최대 3회 | 수익청산 시에만 다음 종목")
    print("=" * 70)

    # 전일종가 + 분봉 수집
    prev_closes = {}
    all_candles = {}
    for c in candidates:
        # 전일종가 = 현재가 / (1 + 등락률/100)
        price_info = await api.get_current_price(c["code"])
        cur = price_info.get("current_price", 0)
        pct = price_info.get("change_pct", 0.0)
        prev_close = int(cur / (1 + pct / 100)) if pct != 0 else cur
        prev_closes[c["code"]] = prev_close
        upper_lim = _upper_limit_price(prev_close)
        high_today = price_info.get("high", 0)
        print(f"\n{c['name']}({c['code']}): 전일종가≈{prev_close:,} 상한가={upper_lim:,} 금일고가={high_today:,}", end="")
        if high_today >= upper_lim:
            print(" ★상한가도달!", end="")
        await asyncio.sleep(0.5)

        print(f"\n  분봉 수집 중...", end=" ", flush=True)
        candles = await fetch_all_candles(api, c["code"])
        all_candles[c["code"]] = candles
        print(f"{len(candles)}개")
        await asyncio.sleep(0.5)

    await api.disconnect()

    # ── A) 개별 종목 독립 시뮬레이션 (전부 09:00부터) ────────

    print("\n" + "=" * 70)
    print(" [A] 개별 종목 독립 시뮬레이션 (각각 09:00부터 풀가동)")
    print("=" * 70)

    indiv_trades: list[SimTrade] = []
    for cand in candidates:
        candles = all_candles.get(cand["code"], [])
        if not candles:
            continue
        result = simulate_strategy(
            candles, cand["code"], cand["name"], cand["market"],
            params, available_cash, prev_closes.get(cand["code"], 0),
        )
        indiv_trades.append(result)

        status_icon = ""
        if result.total_qty > 0:
            if result.pnl > 0:
                status_icon = "✓수익"
            elif result.pnl < 0:
                status_icon = "✗손실"
            else:
                status_icon = "→무변동"

            print(f"  {cand['name']:10s} | 고가 {result.intraday_high:>7,}({result.intraday_high_time[:4]}) "
                  f"| 매수 {result.avg_price:>7,.0f}({result.buy1_fill_time[:4]}) "
                  f"| 청산 {result.exit_price:>7,}({result.exit_time[:4]}) "
                  f"| {result.exit_reason:10s} | {result.pnl:>+10,.0f}원({result.pnl_pct:>+.2f}%) {status_icon}")
        else:
            reason = {
                "WATCHING": "신고가 미달성",
                "TRACKING": "1%하락 미발생",
                "HIGH_CONFIRMED": "매수가 미도달",
                "LIMIT_HIT": "상한가도달→제외",
                "DEADLINE": "진입마감→매매안함",
                "MOMENTUM_LOST": "모멘텀소멸→취소",
            }.get(result.state, result.state)
            print(f"  {cand['name']:10s} | 고가 {result.intraday_high:>7,}({result.intraday_high_time[:4] if result.intraday_high_time else '----'}) "
                  f"| {reason}")

    indiv_pnl = sum(t.pnl for t in indiv_trades if t.total_qty > 0)
    indiv_count = sum(1 for t in indiv_trades if t.total_qty > 0)
    print(f"\n  → 개별 합산: {indiv_count}종목 매매, P&L 합계 {indiv_pnl:+,.0f}원")

    # ── B) 멀티 트레이드 시뮬레이션 (순차적, 수익 시에만 다음) ──

    print("\n" + "=" * 70)
    print(" [B] 멀티 트레이드 시뮬레이션 (순차 진행, 수익→다음, 손실→중단)")
    print("=" * 70)

    mt = params.multi_trade
    repeat_start = time.fromisoformat(mt.repeat_start)
    repeat_end = time.fromisoformat(mt.repeat_end)

    trades: list[SimTrade] = []
    trade_count = 0
    current_cash = available_cash
    total_pnl = 0.0

    print("\n" + "=" * 70)
    print(" 매매 시뮬레이션 결과")
    print("=" * 70)

    for i, cand in enumerate(candidates):
        if trade_count >= mt.max_daily_trades:
            print(f"\n  ※ 일일 최대 {mt.max_daily_trades}회 도달 → 중단")
            break

        code = cand["code"]
        name = cand["name"]
        market = cand["market"]
        candles = all_candles.get(code, [])

        if not candles:
            print(f"\n#{i+1} {name}({code}): 분봉 데이터 없음 — 건너뜀")
            continue

        # 2번째 종목부터: 이전 청산 시각 이후 캔들만 사용
        if trades and trades[-1].exit_time:
            exit_t = trades[-1].exit_time
            candles = [c for c in candles if c.time_str >= exit_t]
            if not candles:
                print(f"\n#{i+1} {name}: 이전 청산 후 남은 캔들 없음 → 건너뜀")
                continue

        # 2번째 종목부터: 시간 범위 체크
        if trade_count > 0:
            first_candle_time = candles[0].time_obj if candles else time(15, 30)
            if first_candle_time > repeat_end:
                print(f"\n  ※ 멀티 트레이드 시간({mt.repeat_start}~{mt.repeat_end}) 초과 → 중단")
                break

        # 시뮬레이션 실행
        result = simulate_strategy(candles, code, name, market, params, current_cash,
                                   prev_closes.get(code, 0))
        trades.append(result)

        # 결과 출력
        print(f"\n{'─' * 60}")
        print(f"  #{i+1} {name}({code}) [{market}]")
        print(f"{'─' * 60}")

        # 캔들 시간 범위
        if candles:
            print(f"  분봉 범위: {candles[0].time_str[:4]}~{candles[-1].time_str[:4]} ({len(candles)}개)")

        if result.state == "WATCHING":
            print(f"  결과: 9:55 이후 신고가 미달성 → 매매 안 함")
            print(f"  9:55 이전 고가: {result.pre_955_high:,}원")
            continue

        if result.state == "LIMIT_HIT":
            ulp = _upper_limit_price(prev_closes.get(code, 0))
            print(f"  결과: 장중 상한가 도달 → 매매 제외")
            print(f"  당일 고가: {result.intraday_high:,}원 ≥ 상한가: {ulp:,}원")
            continue

        if result.state == "DEADLINE":
            print(f"  결과: 매수 진입 마감({params.entry.entry_deadline}) → 매매 안 함")
            print(f"  당일 고가: {result.intraday_high:,}원 ({result.intraday_high_time[:4] if result.intraday_high_time else '----'})")
            continue

        if result.state == "MOMENTUM_LOST":
            print(f"  결과: 고가 확정 후 {params.entry.high_confirm_timeout_min}분 미체결 → 모멘텀 소멸")
            print(f"  고가: {result.intraday_high:,}원 ({result.intraday_high_time[:4]}) → "
                  f"확정: {result.high_confirmed_time[:4]} → 매수가 {result.buy1_price:,}원 미도달")
            continue

        if result.state == "TRACKING":
            print(f"  결과: 신고가 달성 후 1% 하락 미발생 → 매수 안 함")
            print(f"  당일 고가: {result.intraday_high:,}원 ({result.intraday_high_time[:4]})")
            continue

        if result.state in ("HIGH_CONFIRMED",):
            print(f"  결과: 고가 확정 후 매수가 미도달 → 체결 안 됨")
            print(f"  당일 고가: {result.intraday_high:,}원")
            print(f"  1차 매수가: {result.buy1_price:,}원 | 2차 매수가: {result.buy2_price:,}원")
            continue

        # 매수 체결됨 (ENTERED or EXITED)
        print(f"  당일 고가: {result.intraday_high:,}원 ({result.intraday_high_time[:4]})")
        print(f"  고가 확정: {result.high_confirmed_price:,}원")

        if result.buy1_filled:
            print(f"  1차 매수: {result.buy1_price:,}원 ({result.buy1_fill_time[:4]})")
        if result.buy2_filled:
            print(f"  2차 매수: {result.buy2_price:,}원 ({result.buy2_fill_time[:4]})")

        print(f"  평균단가: {result.avg_price:,.0f}원 × {result.total_qty}주 = {result.total_amount:,}원")

        if result.exit_reason:
            print(f"  눌림 최저가: {result.post_entry_low:,}원 ({result.post_entry_low_time[:4]})")
            target_p = (result.intraday_high + result.post_entry_low) / 2
            print(f"  목표가: {target_p:,.0f}원 (고가+최저가)/2")
            print(f"  청산: {result.exit_price:,}원 ({result.exit_time[:4]}) — {result.exit_reason}")
            print(f"  P&L: {result.pnl:+,.0f}원 ({result.pnl_pct:+.2f}%)")

            trade_count += 1
            total_pnl += result.pnl
            current_cash = available_cash + int(total_pnl)  # 누적 P&L 반영

            # 손실 시 중단 (profit_only=true)
            if mt.profit_only and result.pnl < 0 and result.exit_reason != "목표가도달":
                print(f"\n  ※ 손실 청산 → 멀티 트레이드 중단 (당일 매매 종료)")
                break

            # 수익 청산이 아닌 경우에도 중단
            if result.exit_reason != "목표가도달":
                print(f"\n  ※ 비수익 청산({result.exit_reason}) → 멀티 트레이드 중단")
                break
        else:
            print(f"  상태: {result.state} (매수 후 청산조건 미충족)")

    # ── 최종 요약 ──
    print("\n" + "=" * 70)
    print(f" 당일 최종 요약")
    print("=" * 70)
    print(f"  총 매매 횟수: {trade_count}회")
    print(f"  누적 P&L: {total_pnl:+,.0f}원 ({total_pnl/available_cash*100:+.2f}%)")
    print()

    traded = [t for t in trades if t.total_qty > 0]
    not_traded = [t for t in trades if t.total_qty == 0]

    if traded:
        print("  매매 실행 종목:")
        for t in traded:
            status = "✓수익" if t.pnl > 0 else "✗손실" if t.pnl < 0 else "→무변동"
            print(f"    {t.name}({t.code}): 평단{t.avg_price:,.0f} → 청산{t.exit_price:,} "
                  f"| {t.exit_reason} | {t.pnl:+,.0f}원({t.pnl_pct:+.2f}%) {status}")

    if not_traded:
        print("  매매 미실행 종목:")
        for t in not_traded:
            reason = {
                "WATCHING": "신고가 미달성",
                "TRACKING": "1% 하락 미발생",
                "HIGH_CONFIRMED": "매수가 미도달",
                "LIMIT_HIT": "상한가 도달→제외",
                "DEADLINE": "진입마감→매매안함",
                "MOMENTUM_LOST": "모멘텀소멸→취소",
            }.get(t.state, t.state)
            print(f"    {t.name}({t.code}): {reason}")

    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_simulation())
