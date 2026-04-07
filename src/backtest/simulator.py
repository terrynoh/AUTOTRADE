"""
백테스트 시뮬레이터 — KIS API 분봉 데이터로 전략 시뮬레이션.

현재 전략 로직 (CLAUDE.md 기준):
1. 09:55 이후 당일 신고가 감시
2. 고가에서 1% 하락 → 고가 확정
3. 고가 확정 후 분할매수 (KOSPI -2.5%/-3.5%, KOSDAQ -3.75%/-4.25%)
4. 고가 갱신 시 → 기존 주문 취소, 새 고가 기준 재주문
5. 5개 청산 조건 동시 모니터링
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from config.settings import StrategyParams
from src.models.trade import ExitReason


@dataclass
class SimResult:
    """단일 매매 시뮬레이션 결과."""
    trade_date: date
    code: str
    name: str
    market: str
    intraday_high: int = 0
    high_confirmed_at: str = ""      # 고가 확정 시각
    buy1_price: int = 0
    buy2_price: int = 0
    buy1_filled: bool = False
    buy2_filled: bool = False
    avg_entry_price: int = 0
    exit_price: int = 0
    exit_reason: ExitReason = ExitReason.NO_ENTRY
    pnl_pct: float = 0.0
    entry_time: Optional[str] = None
    exit_time: Optional[str] = None
    pullback_low: int = 0            # 눌림 최저가
    target_price: int = 0


@dataclass
class BacktestResult:
    """전체 백테스트 결과."""
    params: StrategyParams
    start_date: date
    end_date: date
    results: list[SimResult] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return sum(1 for r in self.results if r.exit_reason != ExitReason.NO_ENTRY)

    @property
    def winning_trades(self) -> int:
        return sum(1 for r in self.results if r.pnl_pct > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for r in self.results if r.pnl_pct < 0 and r.exit_reason != ExitReason.NO_ENTRY)

    @property
    def win_rate(self) -> float:
        total = self.total_trades
        return (self.winning_trades / total * 100) if total > 0 else 0

    @property
    def avg_pnl_pct(self) -> float:
        traded = [r.pnl_pct for r in self.results if r.exit_reason != ExitReason.NO_ENTRY]
        return sum(traded) / len(traded) if traded else 0

    @property
    def no_entry_days(self) -> int:
        return sum(1 for r in self.results if r.exit_reason == ExitReason.NO_ENTRY)


class Simulator:
    """백테스트 시뮬레이터 — 현재 전략 로직 완전 반영."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def simulate_day(
        self,
        trade_date: date,
        candidate_info: dict,
        candles: list[dict],
    ) -> SimResult:
        """
        하루치 시뮬레이션.

        Args:
            trade_date: 거래일
            candidate_info: {"code", "name", "market"}
            candles: KIS API 분봉 [{time, open, high, low, close, volume}, ...]
                     시간순 정렬 (090000 → 153000)
        """
        code = candidate_info["code"]
        name = candidate_info.get("name", code)
        market = candidate_info.get("market", "KOSPI")
        ep = self.params.entry
        xp = self.params.exit

        result = SimResult(
            trade_date=trade_date,
            code=code,
            name=name,
            market=market,
        )

        if not candles:
            return result

        # 시간순 정렬
        candles = sorted(candles, key=lambda c: c["time"])

        # KOSDAQ 판별: "KOSDAQ", "KSQ" 등 포함 시 KOSDAQ
        is_kosdaq = any(k in market.upper() for k in ("KOSDAQ", "KSQ"))

        # 파라미터
        high_confirm_drop = ep.high_confirm_drop_pct / 100   # 1% → 0.01
        if not is_kosdaq:
            buy1_drop = ep.kospi_buy1_pct / 100               # 2.5% → 0.025
            buy2_drop = ep.kospi_buy2_pct / 100               # 3.5% → 0.035
            hard_stop_drop = xp.kospi_hard_stop_pct / 100     # 4.1% → 0.041
        else:
            buy1_drop = ep.kosdaq_buy1_pct / 100               # 3.75%
            buy2_drop = ep.kosdaq_buy2_pct / 100               # 5.25%
            hard_stop_drop = xp.kosdaq_hard_stop_pct / 100     # 6.2%

        # ── 상태 머신 ─────────────────────────────────────────
        # Phase 1: 09:55 이후 신고가 추적
        intraday_high = 0
        high_confirmed = False
        high_confirm_price = 0

        # Phase 2: 매수 대기
        buy1_price = 0
        buy2_price = 0
        buy1_filled = False
        buy2_filled = False
        avg_entry = 0
        entry_time = None

        # Phase 3: 청산 모니터링
        entered = False
        pullback_low = 0
        pullback_low_time = None
        target_price = 0
        hard_stop_price = 0

        # 진입 마감 시각
        entry_deadline = ep.entry_deadline.replace(":", "")  # "11:00" → "1100"
        entry_deadline += "00"  # → "110000"

        # 강제 청산 시각
        force_time = xp.force_liquidate_time.replace(":", "") + "00"

        for candle in candles:
            t = candle["time"]
            o = candle["open"]
            h = candle["high"]
            low = candle["low"]
            c = candle["close"]

            # ── Phase 1: 09:55 이후 신고가 추적 ──────────────
            if t < "095500":
                # 09:55 이전 고가도 기록 (기준점)
                if h > intraday_high:
                    intraday_high = h
                continue

            if not entered:
                # 진입 마감 이후 신규 매수 불가
                if t > entry_deadline and not buy1_filled and not buy2_filled:
                    continue

                # 고가 갱신 체크
                if h > intraday_high:
                    intraday_high = h
                    high_confirmed = False
                    # 기존 미체결 매수 주문 취소 (시뮬레이션)
                    if not buy1_filled:
                        buy1_price = 0
                    if not buy2_filled:
                        buy2_price = 0

                # 고가 확정 체크: 현재가가 고가 대비 1% 이상 하락
                if not high_confirmed and intraday_high > 0:
                    high_confirm_price = int(intraday_high * (1 - high_confirm_drop))
                    if c <= high_confirm_price or low <= high_confirm_price:
                        high_confirmed = True
                        result.high_confirmed_at = t

                        # 매수 지정가 설정
                        buy1_price = int(intraday_high * (1 - buy1_drop))
                        buy2_price = int(intraday_high * (1 - buy2_drop))
                        hard_stop_price = int(intraday_high * (1 - hard_stop_drop))

                # 매수 체결 체크
                if high_confirmed and not buy1_filled and buy1_price > 0:
                    if low <= buy1_price:
                        buy1_filled = True
                        entry_time = t

                if high_confirmed and not buy2_filled and buy2_price > 0:
                    if low <= buy2_price:
                        buy2_filled = True
                        if not entry_time:
                            entry_time = t

                # 매수 체결 후 청산 모드 진입
                if buy1_filled or buy2_filled:
                    if buy1_filled and buy2_filled:
                        avg_entry = (buy1_price + buy2_price) // 2
                    elif buy1_filled:
                        avg_entry = buy1_price
                    else:
                        avg_entry = buy2_price

                    entered = True
                    # 눌림 최저가 초기화
                    pullback_low = low
                    pullback_low_time = t
                    # 목표가: (고가 + 눌림저가) / 2 → 일단 현재 low 기준, 이후 갱신
                    target_price = (intraday_high + pullback_low) // 2

                    result.intraday_high = intraday_high
                    result.buy1_price = buy1_price
                    result.buy2_price = buy2_price
                    result.buy1_filled = buy1_filled
                    result.buy2_filled = buy2_filled
                    result.avg_entry_price = avg_entry
                    result.entry_time = entry_time
                    result.pullback_low = pullback_low
                    result.target_price = target_price

                    # 2차 미체결 상태에서도 일단 진입 처리 → 이후 봉에서 2차 체결 가능

            # ── Phase 3: 청산 모니터링 ────────────────────────
            if entered:
                # 2차 매수 추가 체결 체크
                if not buy2_filled and buy2_price > 0 and low <= buy2_price:
                    buy2_filled = True
                    avg_entry = (buy1_price + buy2_price) // 2
                    result.buy2_filled = True
                    result.avg_entry_price = avg_entry

                # 눌림 최저가 갱신
                if low < pullback_low:
                    pullback_low = low
                    pullback_low_time = t
                    # 목표가 재계산
                    target_price = (intraday_high + pullback_low) // 2
                    result.pullback_low = pullback_low
                    result.target_price = target_price

                # ① 하드 손절
                if low <= hard_stop_price:
                    result.exit_price = hard_stop_price
                    result.exit_reason = ExitReason.HARD_STOP
                    result.exit_time = t
                    break

                # ② 타임아웃: 눌림 최저가 시점부터 N분
                if pullback_low_time:
                    low_sec = _time_to_sec(pullback_low_time)
                    now_sec = _time_to_sec(t)
                    if now_sec - low_sec >= xp.timeout_from_low_min * 60:
                        result.exit_price = c
                        result.exit_reason = ExitReason.TIMEOUT
                        result.exit_time = t
                        break

                # ③ 목표가 도달
                if h >= target_price:
                    result.exit_price = target_price
                    result.exit_reason = ExitReason.TARGET
                    result.exit_time = t
                    break

                # ④ 강제 청산
                if t >= force_time:
                    result.exit_price = c
                    result.exit_reason = ExitReason.FORCE_LIQUIDATE
                    result.exit_time = t
                    break

        # P&L 계산
        if entered and result.exit_price > 0 and avg_entry > 0:
            cost_pct = 0.5  # 거래비용 0.5%
            raw_pnl = (result.exit_price - avg_entry) / avg_entry * 100
            result.pnl_pct = round(raw_pnl - cost_pct, 2)

        # 미체결(눌림 미발생)
        if not entered:
            result.intraday_high = intraday_high

        return result


def _time_to_sec(t: str) -> int:
    """HHMMSS → 초 변환."""
    if len(t) < 6:
        t = t.ljust(6, "0")
    h, m, s = int(t[:2]), int(t[2:4]), int(t[4:6])
    return h * 3600 + m * 60 + s
