"""리스크 관리 — 일일 손실 한도, 포지션 사이징, 하드손절 strict/halt 2단계 기반 매매 관리.

R-12 재설계 (2026-04-22):
- 하드손절 1회 → strict 모드 (KOSPI AND program_ratio ≥18% 만 신규 매수 허용)
- 하드손절 2회 → trading_halted (당일 매매 전면 중단)
- 지수 급락 -1.5% halt 폐기 (관련 메서드 제거)
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

        # R-12 재설계: 하드손절 2단계 추적
        self._hard_stop_count: int = 0
        self.strict_mode: bool = False

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

    # ── R-12 재설계: 하드손절 2단계 (strict / halt) ──────────────

    def record_hard_stop(self) -> dict:
        """하드 손절 발생 시 호출.

        Returns:
            dict: {"entered_strict": bool, "halted": bool}
        """
        self._hard_stop_count += 1
        limit = self.params.risk.max_hard_stops_daily
        logger.info(f"손절 #{self._hard_stop_count} (한도: {limit}회)")

        entered_strict = False
        halted = False

        if self._hard_stop_count == 1 and not self.strict_mode:
            self.strict_mode = True
            entered_strict = True
            logger.warning(
                f"하드손절 1회 → 당일 strict 모드 진입 "
                f"(KOSPI 비중 ≥{self.params.risk.strict_mode_program_ratio_threshold}% 만 허용)"
            )

        if self._hard_stop_count >= limit:
            self.trading_halted = True
            self.halt_reason = f"하드손절 {self._hard_stop_count}회 ≥ {limit}회 → 당일 매매 중단"
            logger.warning(self.halt_reason)
            halted = True

        return {"entered_strict": entered_strict, "halted": halted}

    # ── 일일 초기화 ──────────────────────────────────────────────

    def reset_daily(self) -> None:
        """일일 초기화."""
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.trading_halted = False
        self.halt_reason = ""
        self._hard_stop_count = 0
        self.strict_mode = False
