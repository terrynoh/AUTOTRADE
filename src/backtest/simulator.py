"""
백테스트 시뮬레이터 — 과거 데이터로 전략 시뮬레이션.

분봉 데이터를 시간순으로 재생하면서:
1. 11시 기준 스크리닝 통과 여부 확인
2. rolling high 계산
3. 조정 발생 → 가상 매수
4. 청산 조건 → 가상 매도
5. 결과 기록
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from config.settings import StrategyParams
from src.models.trade import TradeRecord, ExitReason


@dataclass
class SimResult:
    """단일 매매 시뮬레이션 결과."""
    trade_date: date
    code: str
    name: str
    market: str
    rolling_high: int = 0
    entry_price: int = 0
    exit_price: int = 0
    exit_reason: ExitReason = ExitReason.NO_ENTRY
    pnl_pct: float = 0.0
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None


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
    """백테스트 시뮬레이터."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def simulate_day(
        self,
        trade_date: date,
        candidate_info: dict,
        minute_df: pd.DataFrame,
    ) -> SimResult:
        """
        하루치 시뮬레이션.

        Args:
            trade_date: 거래일
            candidate_info: {"code", "name", "market", ...}
            minute_df: 분봉 DataFrame (index=시간, columns=[시가,고가,저가,종가,거래량])
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

        if minute_df.empty:
            return result

        # 분봉을 시간순으로 정렬
        minute_df = minute_df.sort_index()

        # ── Rolling High (10:50~11:10) ──────────────────────────
        window_start = time(
            int(ep.high_window_start.split(":")[0]),
            int(ep.high_window_start.split(":")[1]),
        )
        window_end = time(
            int(ep.high_window_end.split(":")[0]),
            int(ep.high_window_end.split(":")[1]),
        )

        rolling_high = 0
        for idx, row in minute_df.iterrows():
            t = _to_time(idx)
            if t is None:
                continue
            if window_start <= t <= window_end:
                high = int(row.get("고가", row.get("high", 0)))
                if high > rolling_high:
                    rolling_high = high

        if rolling_high <= 0:
            return result

        result.rolling_high = rolling_high

        # 조정 임계값
        drop_pct = ep.kospi_drop_pct if market == "KOSPI" else ep.kosdaq_drop_pct
        trigger_price = int(rolling_high * (1 - drop_pct / 100))

        # 하드 손절가
        hard_pct = xp.kospi_hard_stop_pct if market == "KOSPI" else xp.kosdaq_hard_stop_pct
        hard_stop = int(rolling_high * (1 - hard_pct / 100))

        # ── 조정 감지 + 가상 매수 ──────────────────────────────
        entered = False
        entry_price = 0
        entry_time = None
        low_since_entry = 0
        low_time = None
        prev_minute_low = None

        for idx, row in minute_df.iterrows():
            t = _to_time(idx)
            if t is None:
                continue

            # 11시 이후만
            if t <= window_end:
                continue

            close = abs(int(row.get("종가", row.get("close", 0))))
            low = abs(int(row.get("저가", row.get("low", 0))))
            high = abs(int(row.get("고가", row.get("high", 0))))

            if not entered:
                # 조정 감지 (저가 기준)
                if low <= trigger_price:
                    entered = True
                    # 분할매수 평균 추정: trigger_price 근처
                    entry_price = trigger_price
                    entry_time = _to_datetime(trade_date, idx)
                    low_since_entry = low
                    low_time = entry_time
                    prev_minute_low = low

                    result.entry_price = entry_price
                    result.entry_time = entry_time

                    target_price = (rolling_high + entry_price) / 2

            else:
                # 최저가 갱신
                if low < low_since_entry:
                    low_since_entry = low
                    low_time = _to_datetime(trade_date, idx)

                # ① 하드 손절
                if low <= hard_stop:
                    result.exit_price = hard_stop
                    result.exit_reason = ExitReason.HARD_STOP
                    result.exit_time = _to_datetime(trade_date, idx)
                    break

                # ② 추세 이탈 (higher lows 깨짐)
                if xp.trend_break_check and prev_minute_low is not None:
                    if low < prev_minute_low:
                        # 최소 진입 후 3분은 지나야 판단
                        if entry_time and _to_datetime(trade_date, idx):
                            elapsed = (_to_datetime(trade_date, idx) - entry_time).total_seconds()
                            if elapsed > 180:
                                result.exit_price = close
                                result.exit_reason = ExitReason.TREND_BREAK
                                result.exit_time = _to_datetime(trade_date, idx)
                                break

                # ③ 25분 타임아웃
                if low_time:
                    now_dt = _to_datetime(trade_date, idx)
                    if now_dt and (now_dt - low_time).total_seconds() >= xp.timeout_from_low_min * 60:
                        result.exit_price = close
                        result.exit_reason = ExitReason.TIMEOUT
                        result.exit_time = now_dt
                        break

                # ④ 목표가 도달
                if high >= target_price:
                    result.exit_price = int(target_price)
                    result.exit_reason = ExitReason.TARGET
                    result.exit_time = _to_datetime(trade_date, idx)
                    break

                # ⑤ 강제 청산 시각
                force_h, force_m = map(int, xp.force_liquidate_time.split(":"))
                if t >= time(force_h, force_m):
                    result.exit_price = close
                    result.exit_reason = ExitReason.FORCE_LIQUIDATE
                    result.exit_time = _to_datetime(trade_date, idx)
                    break

                prev_minute_low = low

        # P&L 계산
        if entered and result.exit_price > 0:
            # 거래비용 추정 (0.5%)
            cost_pct = 0.5
            raw_pnl = (result.exit_price - entry_price) / entry_price * 100
            result.pnl_pct = raw_pnl - cost_pct

        return result


# ── 유틸리티 ────────────────────────────────────────────────────

def _to_time(idx) -> Optional[time]:
    """인덱스를 time으로 변환."""
    try:
        if isinstance(idx, datetime):
            return idx.time()
        if isinstance(idx, str):
            return datetime.strptime(idx[:5], "%H:%M").time()
        if isinstance(idx, pd.Timestamp):
            return idx.time()
    except Exception:
        pass
    return None


def _to_datetime(d: date, idx) -> Optional[datetime]:
    """인덱스를 datetime으로 변환."""
    t = _to_time(idx)
    if t:
        return datetime.combine(d, t)
    return None
