"""
거래 기록 / 일일 요약 모델.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class ExitReason(str, Enum):
    TARGET = "TARGET"                   # 목표가 도달
    HARD_STOP = "HARD_STOP"             # 하드 손절 (구조 붕괴)
    TREND_BREAK = "TREND_BREAK"         # (삭제됨, DB 하위호환 전용)
    TIMEOUT = "TIMEOUT"                 # 20분 타임아웃
    FUTURES_STOP = "FUTURES_STOP"       # 선물 급락 청산
    FORCE_LIQUIDATE = "FORCE_LIQUIDATE" # 15:20 강제 청산
    MANUAL = "MANUAL"                   # 수동 청산
    NO_ENTRY = "NO_ENTRY"               # 조정 미발생, 매매 미진입


@dataclass
class TradeRecord:
    """개별 매매 기록."""

    trade_date: date
    code: str
    name: str
    market: str                         # KOSPI / KOSDAQ

    # 매수
    avg_buy_price: float = 0.0
    total_buy_qty: int = 0
    total_buy_amount: int = 0
    buy_count: int = 0                  # 매수 횟수 (초기 + 분할)
    first_buy_time: Optional[datetime] = None

    # 매도
    avg_sell_price: float = 0.0
    total_sell_amount: int = 0
    sell_time: Optional[datetime] = None

    # 결과
    exit_reason: ExitReason = ExitReason.NO_ENTRY
    pnl: float = 0.0                   # 손익 금액
    pnl_pct: float = 0.0               # 손익률(%)
    holding_minutes: float = 0.0        # 보유 시간(분)

    # 기준값
    rolling_high: int = 0
    entry_trigger_price: int = 0        # 매수 트리거가
    target_price: float = 0.0          # 목표가

    # 메타
    trade_mode: str = "dry_run"         # dry_run | paper | live


@dataclass
class DailySummary:
    """일일 매매 요약."""

    summary_date: date
    trade_mode: str = "dry_run"

    # 스크리닝
    candidates_count: int = 0           # 스크리닝 후보 수
    targets_count: int = 0              # 타겟 종목 수

    # 매매
    trades: list[TradeRecord] = field(default_factory=list)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    no_entry_count: int = 0             # 조정 미발생 종목 수

    # 손익
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_single_loss: float = 0.0
    max_single_gain: float = 0.0

    @property
    def win_rate(self) -> float:
        """승률(%)."""
        total = self.winning_trades + self.losing_trades
        if total == 0:
            return 0.0
        return (self.winning_trades / total) * 100

    def add_trade(self, trade: TradeRecord) -> None:
        self.trades.append(trade)
        if trade.exit_reason == ExitReason.NO_ENTRY:
            self.no_entry_count += 1
            return

        self.total_trades += 1
        self.total_pnl += trade.pnl

        if trade.pnl > 0:
            self.winning_trades += 1
            self.max_single_gain = max(self.max_single_gain, trade.pnl)
        elif trade.pnl < 0:
            self.losing_trades += 1
            self.max_single_loss = min(self.max_single_loss, trade.pnl)
