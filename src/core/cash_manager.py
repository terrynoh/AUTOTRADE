"""예수금 관리 — 동결/차감/복원 시스템.

R-14: DRY_RUN과 LIVE 모두 동일한 예수금 관리 인터페이스 제공.

예수금 흐름:
1. 초기화: initial → available
2. 주문 시: available → frozen (동결)
3. 체결 시: frozen → used (확정)
4. 취소 시: frozen → available (해제)
5. 청산 시: used → available (복원 + P&L 반영)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger


@dataclass
class CashTransaction:
    """예수금 변동 기록."""
    
    timestamp: datetime
    action: str              # "freeze", "confirm", "unfreeze", "restore"
    amount: int
    order_id: str = ""
    reason: str = ""
    
    # 변동 후 상태 스냅샷
    available_after: int = 0
    frozen_after: int = 0
    used_after: int = 0


@dataclass
class CashManager:
    """예수금 관리. DRY_RUN과 LIVE 모두 동일 인터페이스.
    
    Attributes:
        initial: 최초 예수금 (세션 시작 시점)
        available: 가용 예수금 (주문 가능 금액)
        frozen: 동결 금액 (미체결 주문 금액)
        used: 사용 금액 (체결 확정 금액)
        
    Invariant:
        initial == available + frozen + used (항상 성립)
    """
    
    initial: int = 0
    available: int = 0
    frozen: int = 0
    used: int = 0
    
    # 거래 기록 (디버깅/감사용)
    _transactions: list[CashTransaction] = field(default_factory=list)
    _max_transactions: int = 1000  # 메모리 보호
    
    def __post_init__(self):
        """초기화 후 available = initial 설정."""
        if self.available == 0 and self.initial > 0:
            self.available = self.initial
    
    # ── 조회 메서드 ─────────────────────────────────────────
    
    @property
    def total(self) -> int:
        """현재 총 자산 = available + frozen + used."""
        return self.available + self.frozen + self.used
    
    @property
    def pnl(self) -> int:
        """실현 + 미실현 P&L = total - initial."""
        return self.total - self.initial
    
    def can_order(self, amount: int) -> tuple[bool, str]:
        """주문 가능 여부 확인.
        
        Args:
            amount: 주문 금액 (price × qty)
            
        Returns:
            (True, "OK") 또는 (False, 거부 사유)
        """
        if amount <= 0:
            return False, f"잘못된 주문 금액: {amount}"
        
        if amount > self.available:
            return False, f"잔고 부족: 필요 {amount:,}원 > 가용 {self.available:,}원"
        
        return True, "OK"
    
    # ── 상태 변경 메서드 ───────────────────────────────────
    
    def freeze(self, amount: int, order_id: str = "", reason: str = "") -> bool:
        """주문 시 예수금 동결.
        
        Args:
            amount: 동결 금액
            order_id: 주문번호 (추적용)
            reason: 사유 (로그용)
            
        Returns:
            True: 동결 성공
            False: 잔고 부족
        """
        if amount <= 0:
            logger.warning(f"[CashManager] freeze: 잘못된 금액 {amount}")
            return False
        
        if amount > self.available:
            logger.warning(
                f"[CashManager] freeze 실패: {amount:,} > available {self.available:,}"
            )
            return False
        
        self.available -= amount
        self.frozen += amount
        
        self._record_transaction("freeze", amount, order_id, reason)
        
        logger.info(
            f"[CashManager] 동결: {amount:,}원 "
            f"(가용 {self.available:,} / 동결 {self.frozen:,}) "
            f"[{order_id}] {reason}"
        )
        
        return True
    
    def confirm(self, amount: int, order_id: str = "", reason: str = "") -> bool:
        """체결 시 동결 → 사용 확정.
        
        Args:
            amount: 체결 금액
            order_id: 주문번호
            reason: 사유
            
        Returns:
            True: 확정 성공
            False: 동결 금액 부족 (비정상 상황)
        """
        if amount <= 0:
            logger.warning(f"[CashManager] confirm: 잘못된 금액 {amount}")
            return False
        
        if amount > self.frozen:
            # 비정상: 동결 금액보다 많은 체결은 불가능
            logger.error(
                f"[CashManager] confirm 오류: {amount:,} > frozen {self.frozen:,}"
            )
            return False
        
        self.frozen -= amount
        self.used += amount
        
        self._record_transaction("confirm", amount, order_id, reason)
        
        logger.info(
            f"[CashManager] 확정: {amount:,}원 "
            f"(동결 {self.frozen:,} / 사용 {self.used:,}) "
            f"[{order_id}] {reason}"
        )
        
        return True
    
    def unfreeze(self, amount: int, order_id: str = "", reason: str = "") -> bool:
        """취소 시 동결 해제 → 가용 복원.
        
        Args:
            amount: 해제 금액
            order_id: 주문번호
            reason: 사유 (예: "주문 취소", "신고가 갱신")
            
        Returns:
            True: 해제 성공
            False: 동결 금액 부족 (비정상 상황)
        """
        if amount <= 0:
            logger.warning(f"[CashManager] unfreeze: 잘못된 금액 {amount}")
            return False
        
        if amount > self.frozen:
            logger.error(
                f"[CashManager] unfreeze 오류: {amount:,} > frozen {self.frozen:,}"
            )
            return False
        
        self.frozen -= amount
        self.available += amount
        
        self._record_transaction("unfreeze", amount, order_id, reason)
        
        logger.info(
            f"[CashManager] 해제: {amount:,}원 "
            f"(가용 {self.available:,} / 동결 {self.frozen:,}) "
            f"[{order_id}] {reason}"
        )
        
        return True
    
    def restore(self, amount: int, order_id: str = "", reason: str = "") -> bool:
        """청산 시 사용 → 가용 복원 (P&L 반영).
        
        Args:
            amount: 청산 금액 (매도 체결 금액)
            order_id: 주문번호
            reason: 청산 사유 (예: "target", "hard_stop")
            
        Returns:
            True: 복원 성공
            
        Note:
            amount가 used보다 크면 수익 발생 (P&L > 0)
            amount가 used보다 작으면 손실 발생 (P&L < 0)
        """
        if amount < 0:
            logger.warning(f"[CashManager] restore: 음수 금액 {amount}")
            return False
        
        # used 전액 해제 + 청산금액을 available로 이동
        # (P&L이 자연스럽게 반영됨)
        pnl = amount - self.used
        
        self.available += amount
        self.used = 0
        
        self._record_transaction("restore", amount, order_id, reason)
        
        logger.info(
            f"[CashManager] 복원: {amount:,}원 (P&L {pnl:+,}원) "
            f"(가용 {self.available:,}) "
            f"[{order_id}] {reason}"
        )
        
        return True
    
    # ── 부분체결 지원 메서드 ───────────────────────────────
    
    def partial_confirm(
        self, 
        filled_amount: int, 
        remaining_frozen: int,
        order_id: str = "",
        reason: str = ""
    ) -> bool:
        """부분체결 시 일부만 확정.
        
        Args:
            filled_amount: 체결된 금액
            remaining_frozen: 남은 미체결 금액 (계속 동결)
            order_id: 주문번호
            reason: 사유
            
        Returns:
            True: 처리 성공
        """
        total_order = filled_amount + remaining_frozen
        
        if total_order > self.frozen:
            logger.error(
                f"[CashManager] partial_confirm 오류: "
                f"체결 {filled_amount:,} + 잔량 {remaining_frozen:,} > frozen {self.frozen:,}"
            )
            return False
        
        # 체결분만 확정 (나머지는 frozen에 유지)
        self.frozen -= filled_amount
        self.used += filled_amount
        
        self._record_transaction(
            "partial_confirm", filled_amount, order_id, 
            f"{reason} (잔량 {remaining_frozen:,} 동결 유지)"
        )
        
        logger.info(
            f"[CashManager] 부분확정: {filled_amount:,}원 "
            f"(동결 {self.frozen:,} / 사용 {self.used:,}) "
            f"[{order_id}] {reason}"
        )
        
        return True
    
    # ── 유틸리티 ─────────────────────────────────────────
    
    def reset(self, new_initial: Optional[int] = None) -> None:
        """일일 초기화.
        
        Args:
            new_initial: 새 초기 예수금 (None이면 기존 initial 유지)
        """
        if new_initial is not None:
            self.initial = new_initial
        
        self.available = self.initial
        self.frozen = 0
        self.used = 0
        self._transactions.clear()
        
        logger.info(f"[CashManager] 초기화: {self.initial:,}원")
    
    def get_summary(self) -> dict:
        """현재 상태 요약."""
        return {
            "initial": self.initial,
            "available": self.available,
            "frozen": self.frozen,
            "used": self.used,
            "total": self.total,
            "pnl": self.pnl,
        }
    
    def _record_transaction(
        self, action: str, amount: int, order_id: str, reason: str
    ) -> None:
        """거래 기록 저장."""
        from src.utils.market_calendar import now_kst
        
        tx = CashTransaction(
            timestamp=now_kst(),
            action=action,
            amount=amount,
            order_id=order_id,
            reason=reason,
            available_after=self.available,
            frozen_after=self.frozen,
            used_after=self.used,
        )
        
        self._transactions.append(tx)
        
        # 메모리 보호: 오래된 기록 삭제
        if len(self._transactions) > self._max_transactions:
            self._transactions = self._transactions[-self._max_transactions:]
    
    def get_recent_transactions(self, n: int = 10) -> list[CashTransaction]:
        """최근 N개 거래 기록 반환."""
        return self._transactions[-n:]
    
    def validate_invariant(self) -> bool:
        """불변식 검증: initial == available + frozen + used.
        
        Returns:
            True: 정상
            False: 불변식 위반 (버그!)
        """
        expected = self.available + self.frozen + self.used
        if expected != self.initial:
            logger.critical(
                f"[CashManager] 불변식 위반! "
                f"initial={self.initial:,} ≠ "
                f"available({self.available:,}) + frozen({self.frozen:,}) + used({self.used:,}) "
                f"= {expected:,}"
            )
            return False
        return True
