"""고급 DRY_RUN 시뮬레이터 (R-14: W-32).

LIVE 환경과 동일한 조건으로 DRY_RUN 테스트:
- 예수금 확인 + 동결
- 호가 단위 검증
- 부분 체결 (확률 기반)
- 체결 지연 (시간 기반)
- 주문 거부 시뮬레이션
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from loguru import logger

from config.settings import SimulationParams  # W-34: settings.py로 이동
from src.models.order import Order, OrderStatus
from src.utils.price_utils import floor_to_tick

if TYPE_CHECKING:
    from src.core.cash_manager import CashManager
    from src.core.watcher import Watcher


# ── AdvancedSimulator 클래스 ─────────────────────────────


class AdvancedSimulator:
    """고급 DRY_RUN 시뮬레이터.
    
    책임:
    1. 주문 시 예수금 확인 + 호가 검증 + 동결
    2. 체결 조건 확인 (가격 + 지연)
    3. 부분체결/전량체결 결정
    4. 체결 시 예수금 확정
    
    사용 위치:
    - FillManager (W-35)에서 DRY_RUN 모드 핸들러로 사용
    
    Example:
        simulator = AdvancedSimulator(cash_manager, params)
        
        # 주문 시
        ok, reason = simulator.try_place_order(order)
        if not ok:
            logger.warning(f"주문 거부: {reason}")
        
        # 매 틱마다
        filled = simulator.check_fills(watcher, orders, ts)
        for label, price, qty in filled:
            watcher.on_buy_filled(label, price, qty, ts)
    """
    
    def __init__(
        self, 
        cash_manager: "CashManager",
        params: Optional[SimulationParams] = None,
    ):
        """
        Args:
            cash_manager: 예수금 관리자 (W-30)
            params: 시뮬레이션 파라미터 (None이면 기본값)
        """
        self.cash = cash_manager
        self.params = params or SimulationParams()
    
    # ── 주문 처리 ─────────────────────────────────────────
    
    def try_place_order(self, order: Order) -> tuple[bool, str]:
        """주문 시도. 예수금 확인 + 호가 검증 + 동결.
        
        Args:
            order: 주문 객체 (price, qty 설정 필수)
            
        Returns:
            (True, "OK"): 주문 접수 성공, order.status = PENDING
            (False, reason): 주문 거부, order.status = REJECTED
            
        Side Effects:
            - 성공 시: CashManager.freeze() 호출
            - 실패 시: order.status = REJECTED
        """
        amount = order.price * order.qty
        
        # 1. 예수금 확인
        if self.params.reject_on_insufficient_cash:
            can, reason = self.cash.can_order(amount)
            if not can:
                order.status = OrderStatus.REJECTED
                logger.warning(
                    f"[Simulator] 주문 거부 (잔고): {order.label} "
                    f"{order.price:,}원 × {order.qty}주 = {amount:,}원 | {reason}"
                )
                return False, reason
        
        # 2. 호가 단위 검증
        if self.params.reject_on_invalid_tick:
            if not self._validate_tick_size(order.price):
                order.status = OrderStatus.REJECTED
                reason = f"호가 단위 오류: {order.price:,}원"
                logger.warning(f"[Simulator] 주문 거부 (호가): {order.label} | {reason}")
                return False, reason
        
        # 3. 예수금 동결
        frozen = self.cash.freeze(
            amount,
            order_id=order.order_id,
            reason=f"{order.label} 주문",
        )
        if not frozen:
            # can_order 통과했는데 freeze 실패 = 동시성 문제 (이론상 발생 안 함)
            order.status = OrderStatus.REJECTED
            reason = "예수금 동결 실패 (동시성 오류)"
            logger.error(f"[Simulator] {reason}")
            return False, reason
        
        # 4. 주문 접수 완료
        order.status = OrderStatus.PENDING
        logger.debug(
            f"[Simulator] 주문 접수: {order.label} "
            f"{order.price:,}원 × {order.qty}주 (동결 {amount:,}원)"
        )
        
        return True, "OK"
    
    # ── 체결 처리 ─────────────────────────────────────────
    
    def check_fills(
        self,
        watcher: "Watcher",
        orders: list[Order],
        ts: datetime,
    ) -> list[tuple[str, int, int]]:
        """체결 조건 확인 + 시뮬레이션.
        
        Args:
            watcher: Watcher 객체 (current_price 참조)
            orders: 체결 대상 주문 목록
            ts: 현재 시각
            
        Returns:
            [(label, filled_price, filled_qty), ...] 체결된 주문 정보
            
        Side Effects:
            - 체결 시: order 필드 갱신, CashManager.confirm() 호출
        """
        filled_data: list[tuple[str, int, int]] = []
        current_price = watcher.current_price
        
        for order in orders:
            # 활성 주문만 처리
            if order.status not in (OrderStatus.PENDING, OrderStatus.PARTIAL):
                continue
            
            # 1. 가격 조건 체크 (매수: 현재가 <= 주문가)
            if current_price > order.price:
                continue
            
            # 2. 체결 지연 체크
            if not self._check_delay(order, ts):
                continue
            
            # 3. 체결 수량 결정 (부분/전량)
            remaining = order.remaining_qty  # W-33: property 사용
            fill_qty = self._calc_fill_qty(order, remaining)
            fill_price = order.price  # 지정가 체결
            
            # 4. 주문 상태 갱신
            order.filled_qty += fill_qty
            order.filled_price = fill_price
            order.filled_at = ts
            
            if order.filled_qty >= order.qty:
                order.status = OrderStatus.FILLED
            else:
                order.status = OrderStatus.PARTIAL
                # remaining_qty는 W-33에서 추가 예정
                # 현재는 filled_qty로 계산 가능
            
            # 5. 예수금 확정 (동결 → 사용)
            fill_amount = fill_price * fill_qty
            self.cash.confirm(
                fill_amount,
                order_id=order.order_id,
                reason=f"{order.label} 체결",
            )
            
            # 6. 결과 기록
            filled_data.append((order.label, fill_price, fill_qty))
            
            logger.info(
                f"[Simulator] 체결: {order.label} "
                f"{fill_price:,}원 × {fill_qty}주 "
                f"({'전량' if order.status == OrderStatus.FILLED else f'부분 {order.filled_qty}/{order.qty}'})"
            )
        
        return filled_data
    
    def cancel_order(self, order: Order) -> bool:
        """주문 취소. 동결 해제.
        
        Args:
            order: 취소할 주문
            
        Returns:
            True: 취소 성공
            False: 취소 불가 (이미 체결 등)
        """
        if order.status not in (OrderStatus.PENDING, OrderStatus.PARTIAL):
            logger.debug(f"[Simulator] 취소 불가: {order.label} status={order.status}")
            return False
        
        # 미체결 금액 = 미체결수량 × 주문가
        unfreeze_amount = order.price * order.remaining_qty  # W-33: property 사용
        
        # 동결 해제
        self.cash.unfreeze(
            unfreeze_amount,
            order_id=order.order_id,
            reason=f"{order.label} 취소",
        )
        
        order.status = OrderStatus.CANCELLED
        
        logger.info(
            f"[Simulator] 취소: {order.label} "
            f"(미체결 {order.remaining_qty}주, 해제 {unfreeze_amount:,}원)"
        )
        
        return True
    
    # ── 내부 헬퍼 ─────────────────────────────────────────
    
    def _validate_tick_size(self, price: int) -> bool:
        """호가 단위 검증.
        
        가격이 호가 단위에 맞는지 확인.
        floor_to_tick(price) == price 이면 유효.
        """
        return price == floor_to_tick(price)
    
    def _check_delay(self, order: Order, ts: datetime) -> bool:
        """체결 지연 체크.
        
        fill_delay_enabled=True인 경우:
        주문 시점(created_at) + delay_sec 이후에만 체결 가능.
        """
        if not self.params.fill_delay_enabled:
            return True
        
        if order.created_at is None:
            return True  # created_at 없으면 지연 체크 스킵
        
        delay = timedelta(seconds=self.params.fill_delay_sec)
        ready_at = order.created_at + delay
        
        if ts < ready_at:
            logger.debug(
                f"[Simulator] 지연 대기: {order.label} "
                f"(ready_at={ready_at.strftime('%H:%M:%S')})"
            )
            return False
        
        return True
    
    def _calc_fill_qty(self, order: Order, remaining: int) -> int:
        """부분체결 수량 결정.
        
        partial_fill_enabled=True인 경우:
        partial_fill_prob 확률로 부분체결 발생.
        부분체결 시 50~100% 범위에서 랜덤 결정.
        """
        if not self.params.partial_fill_enabled:
            return remaining  # 전량 체결
        
        if remaining <= 1:
            return remaining  # 1주 이하면 전량
        
        # 확률 기반 부분체결
        if random.random() < self.params.partial_fill_prob:
            # min_ratio ~ 100% 범위에서 랜덤
            ratio = random.uniform(self.params.partial_fill_min_ratio, 1.0)
            fill_qty = max(1, int(remaining * ratio))
            
            # 전량이 아닌 경우에만 부분체결
            if fill_qty < remaining:
                logger.debug(
                    f"[Simulator] 부분체결 결정: {order.label} "
                    f"{fill_qty}/{remaining}주 ({ratio:.1%})"
                )
                return fill_qty
        
        return remaining  # 전량 체결
    
    # ── 상태 조회 ─────────────────────────────────────────
    
    def get_params_summary(self) -> dict:
        """현재 시뮬레이션 파라미터 요약."""
        return {
            "partial_fill_enabled": self.params.partial_fill_enabled,
            "partial_fill_prob": self.params.partial_fill_prob,
            "fill_delay_enabled": self.params.fill_delay_enabled,
            "fill_delay_sec": self.params.fill_delay_sec,
            "reject_on_insufficient_cash": self.params.reject_on_insufficient_cash,
            "reject_on_invalid_tick": self.params.reject_on_invalid_tick,
        }
