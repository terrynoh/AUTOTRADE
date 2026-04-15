"""리스크 관리 — 일일 손실 한도, 포지션 사이징, 지수 급락/손절 횟수 기반 매매 중단.

R-12 추가 (2026-04-15):
- 지수(선물) 당일 고점 대비 N% 하락 시 매매 중단
- 일일 손절 횟수 한도 도달 시 매매 중단
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

        # R-12: 지수 급락 추적
        self._futures_high: float = 0.0  # 당일 선물 고점

        # R-12: 손절 횟수 추적
        self._hard_stop_count: int = 0

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

    # ── R-12: 지수 급락 체크 ──────────────────────────────────────

    def update_futures_price(self, price: float) -> bool:
        """선물 가격 수신 시 호출. 고점 갱신 + 하락 체크.

        Returns:
            True if 매매 중단 발동됨, False otherwise.
        """
        if price <= 0:
            return False

        # 이미 중단 상태면 고점 갱신 안 함
        if self.trading_halted:
            return True

        # 고점 갱신
        if price > self._futures_high:
            self._futures_high = price
            return False

        # 하락폭 체크
        if self._futures_high > 0:
            drop_pct = (self._futures_high - price) / self._futures_high * 100
            limit_pct = self.params.risk.index_drop_halt_pct

            if drop_pct >= limit_pct:
                self.trading_halted = True
                self.halt_reason = (
                    f"지수 급락: 고점 {self._futures_high:.2f} → "
                    f"현재 {price:.2f} ({drop_pct:.2f}% ≥ {limit_pct}%)"
                )
                logger.warning(self.halt_reason)
                return True

        return False

    # ── R-12: 손절 횟수 체크 ──────────────────────────────────────

    def record_hard_stop(self) -> bool:
        """하드 손절 발생 시 호출. 횟수 증가 + 한도 체크.

        Returns:
            True if 매매 중단 발동됨, False otherwise.
        """
        self._hard_stop_count += 1
        limit = self.params.risk.max_hard_stops_daily

        logger.info(f"손절 #{self._hard_stop_count} (한도: {limit}회)")

        if self._hard_stop_count >= limit:
            self.trading_halted = True
            self.halt_reason = f"손절 횟수 한도 도달: {self._hard_stop_count}회 ≥ {limit}회"
            logger.warning(self.halt_reason)
            return True

        return False

    # ── 일일 초기화 ──────────────────────────────────────────────

    def reset_daily(self) -> None:
        """일일 초기화."""
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.trading_halted = False
        self.halt_reason = ""
        self._futures_high = 0.0
        self._hard_stop_count = 0
