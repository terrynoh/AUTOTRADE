"""
리스크 관리 — 일일 손실 한도, 포지션 사이징, 긴급 청산.
"""
from __future__ import annotations

from loguru import logger

from config.settings import StrategyParams


class RiskManager:
    """일일 리스크 관리."""

    def __init__(self, params: StrategyParams):
        self.params = params
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.trading_halted: bool = False
        self.halt_reason: str = ""

    def calculate_available_cash(self, total_assets: int) -> int:
        """매매 가용 금액 = 예수금 × max_position_size_pct."""
        pct = self.params.risk.max_position_size_pct
        return int(total_assets * pct / 100)

    def check_daily_loss_limit(self, total_assets: int) -> bool:
        """일일 손실 한도 체크. 초과 시 True 반환."""
        if total_assets <= 0:
            return False
        limit_pct = self.params.risk.daily_loss_limit_pct
        loss_pct = abs(self.daily_pnl) / total_assets * 100

        if self.daily_pnl < 0 and loss_pct >= limit_pct:
            self.trading_halted = True
            self.halt_reason = f"일일 손실 한도 도달: {loss_pct:.1f}% ≥ {limit_pct}%"
            logger.warning(self.halt_reason)
            return True
        return False

    def can_open_position(self, current_positions: int) -> bool:
        """신규 포지션 가능 여부."""
        if self.trading_halted:
            logger.warning(f"매매 중단 상태: {self.halt_reason}")
            return False
        max_pos = self.params.order.max_simultaneous_positions
        if current_positions >= max_pos:
            logger.debug(f"최대 포지션 도달: {current_positions}/{max_pos}")
            return False
        return True

    def record_trade_result(self, pnl: float) -> None:
        """거래 결과 기록."""
        self.daily_pnl += pnl
        self.daily_trades += 1
        logger.info(f"거래 #{self.daily_trades} P&L: {pnl:+,.0f}원 (당일 누적: {self.daily_pnl:+,.0f}원)")

    def reset_daily(self) -> None:
        """일일 초기화."""
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.trading_halted = False
        self.halt_reason = ""
