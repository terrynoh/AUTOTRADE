"""
주문 실행 — 지정가 매수 2건 배치, 취소/재주문, 청산.

R16: LIVE 전용 (paper/dry_run 폐기).
모든 체결은 KIS 체결통보(H0STCNI0) 경로로 비동기 수신.
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Optional

from src.utils.market_calendar import now_kst

from loguru import logger

from config.settings import Settings, StrategyParams
from src.kis_api.kis import KISAPI
from src.kis_api.constants import ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET
from src.core.watcher import Watcher
from src.models.order import Order, OrderSide, OrderStatus, Position


class Trader:
    """주문 실행 엔진 (LIVE 전용)."""

    def __init__(self, api: KISAPI, settings: Settings, params: StrategyParams):
        self.api = api
        self.settings = settings
        self.params = params
        self.position: Optional[Position] = None
        self.pending_buy_orders: list[Order] = []
        self.pending_sell_orders: list[Order] = []  # R15-005: LIVE 체결통보 매칭용

    # ── 매수 주문 배치 (지정가 2건) ────────────────────────

    async def place_buy_orders(self, watcher: Watcher, available_cash: int) -> None:
        """고가 확정 후 매수 지정가 2건 배치."""
        # === W-11e: Last-line defense (repeat_end 시한 위반 방어) ===
        # 매매 철학상 11:00 이후는 반등 조건 소멸. 어떤 경로로도 이 시각 이후
        # 매수 발주가 일어나면 안 됨. 정상 흐름에서는 _is_in_entry_window (10:55)
        # 가 차단하지만, 비정상 호출 경로에 대한 최후 방어선.
        _repeat_end_time = dtime.fromisoformat(self.params.multi_trade.repeat_end)
        _now = now_kst()
        if _now.time() >= _repeat_end_time:
            logger.error(
                f"[Trader] CRITICAL: 매매 철학 시한 위반 시도 "
                f"({_now.time()} >= repeat_end {_repeat_end_time}). 발주 거부. "
                f"종목: {watcher.code} ({watcher.name})"
            )
            return

        ep = self.params.entry
        buy1_price = watcher.target_buy1_price
        buy2_price = watcher.target_buy2_price

        buy1_amount = int(available_cash * ep.buy1_ratio / 100)
        buy2_amount = int(available_cash * ep.buy2_ratio / 100)

        buy1_qty = max(1, buy1_amount // buy1_price) if buy1_price > 0 else 0
        buy2_qty = max(1, buy2_amount // buy2_price) if buy2_price > 0 else 0

        now = now_kst()
        self.pending_buy_orders = []

        # 1차 매수
        order1 = await self._send_buy_order(
            watcher.code, buy1_qty, buy1_price, "buy1", now
        )
        if order1:
            watcher.buy1_order_id = order1.order_id
            watcher.buy1_placed = True
            self.pending_buy_orders.append(order1)
            logger.info(
                f"[{watcher.name}] 1차 매수 주문: {buy1_price:,}원 × {buy1_qty}주 "
                f"(고가 {watcher.intraday_high:,}원 대비 -{ep.kospi_buy1_pct if watcher.market.value == 'KOSPI' else ep.kosdaq_buy1_pct}%)"
            )

        # 2차 매수
        order2 = await self._send_buy_order(
            watcher.code, buy2_qty, buy2_price, "buy2", now
        )
        if order2:
            watcher.buy2_order_id = order2.order_id
            watcher.buy2_placed = True
            self.pending_buy_orders.append(order2)
            logger.info(
                f"[{watcher.name}] 2차 매수 주문: {buy2_price:,}원 × {buy2_qty}주 "
                f"(고가 {watcher.intraday_high:,}원 대비 -{ep.kospi_buy2_pct if watcher.market.value == 'KOSPI' else ep.kosdaq_buy2_pct}%)"
            )

    async def _send_buy_order(
        self, code: str, qty: int, price: int, label: str, ts: datetime
    ) -> Optional[Order]:
        """단일 매수 주문 전송."""
        if qty <= 0 or price <= 0:
            return None

        order = Order(
            code=code,
            side=OrderSide.BUY,
            price=price,
            qty=qty,
            label=label,
            created_at=ts,
        )

        try:
            result = await self.api.buy_order(
                code=code, qty=qty, price=price, price_type=ORDER_TYPE_LIMIT
            )
            order.order_id = result.get("order_no", "")
            # R15-005: LIVE 경로 상태 전이 — REST 발주 완료 후 체결통보 대기
            order.status = OrderStatus.SUBMITTED
            order.submitted_at = now_kst()
            logger.info(
                f"[R15-005] LIVE 매수 SUBMITTED: {label} order_id={order.order_id!r} "
                f"{price:,}원 × {qty}주 (submitted_at={order.submitted_at})"
            )
        except Exception as e:
            logger.error(f"매수주문 실패 [{label}]: {e}")
            order.status = OrderStatus.REJECTED
            return None

        return order

    # ── 매수 주문 취소 (고가 갱신 시) ──────────────────────

    async def cancel_buy_orders(self, watcher: Watcher) -> None:
        """미체결 매수 주문 전량 취소."""
        for order in self.pending_buy_orders:
            if not order.is_active:
                continue

            try:
                await self.api.cancel_order(order.order_id, order.code)
                order.status = OrderStatus.CANCELLED
                logger.info(f"주문 취소: {order.label} {order.order_id}")
            except Exception as e:
                logger.error(f"주문 취소 실패 [{order.label}]: {e}")

        watcher.buy1_placed = False
        watcher.buy2_placed = False
        watcher.buy1_order_id = ""
        watcher.buy2_order_id = ""
        self.pending_buy_orders = []

    # ── R-13: 2차 매수 개별 취소 + 재발주 ─────────────────

    async def cancel_and_reorder_buy2(
        self,
        watcher: Watcher,
        new_price: int,
        available_cash: int
    ) -> bool:
        """R-13: 2차 매수 주문만 취소 후 새 가격으로 재발주.

        1차 체결 후 비중 변경 시 호출.

        Args:
            watcher: 대상 Watcher
            new_price: 새 2차 매수가
            available_cash: 가용 현금

        Returns:
            True: 재발주 성공
            False: 취소 또는 재발주 실패
        """
        # 이미 체결됐으면 재발주 불필요
        if watcher.buy2_filled:
            logger.debug(f"[{watcher.name}] buy2 이미 체결됨 — 재발주 스킵")
            return False

        # buy2 주문이 없으면 재발주 불필요
        if not watcher.buy2_order_id:
            logger.debug(f"[{watcher.name}] buy2 주문 없음 — 재발주 스킵")
            return False

        old_order = None
        for order in self.pending_buy_orders:
            if order.label == "buy2" and order.is_active:
                old_order = order
                break

        if old_order is None:
            logger.debug(f"[{watcher.name}] buy2 미체결 주문 없음 — 재발주 스킵")
            return False

        old_price = old_order.price
        old_qty = old_order.qty

        # ── 1. 기존 buy2 취소 ──
        try:
            await self.api.cancel_order(watcher.buy2_order_id, watcher.code)
            old_order.status = OrderStatus.CANCELLED
            logger.info(f"[{watcher.name}] buy2 취소 완료: {old_price:,}원")
        except Exception as e:
            logger.error(f"[{watcher.name}] buy2 취소 실패: {e}")
            return False

        # ── 2. 새 buy2 발주 ──
        ep = self.params.entry
        buy2_amount = int(available_cash * ep.buy2_ratio / 100)
        new_qty = max(1, buy2_amount // new_price) if new_price > 0 else 0

        if new_qty <= 0:
            logger.warning(f"[{watcher.name}] 새 buy2 수량 0 — 재발주 취소")
            watcher.buy2_order_id = ""
            watcher.buy2_placed = False
            return False

        now = now_kst()
        new_order = await self._send_buy_order(
            watcher.code, new_qty, new_price, "buy2", now
        )

        if new_order:
            # 기존 주문 목록에서 old_order 제거, new_order 추가
            self.pending_buy_orders = [
                o for o in self.pending_buy_orders if o.label != "buy2"
            ]
            self.pending_buy_orders.append(new_order)

            watcher.buy2_order_id = new_order.order_id
            watcher.buy2_placed = True

            logger.info(
                f"[{watcher.name}] buy2 재발주: {old_price:,} → {new_price:,}원 "
                f"(수량 {old_qty} → {new_qty}주)"
            )
            return True
        else:
            logger.error(f"[{watcher.name}] buy2 재발주 실패")
            watcher.buy2_order_id = ""
            watcher.buy2_placed = False
            return False

    # ── 청산 주문 ─────────────────────────────────────────

    async def execute_exit(
        self, watcher: Watcher, reason: str, price: int = 0
    ) -> Optional[Order]:
        """전량 청산. reason에 따라 시장가/지정가 결정."""
        if self.position is None or self.position.total_qty <= 0:
            return None

        # 먼저 미체결 매수 주문 취소
        await self.cancel_buy_orders(watcher)

        qty = self.position.total_qty
        code = watcher.code
        now = now_kst()

        # 시장가: hard_stop, futures_stop, force
        use_market = reason in ("hard_stop", "futures_stop", "force")

        order = Order(
            code=code,
            side=OrderSide.SELL,
            price=0 if use_market else price,
            qty=qty,
            label=reason,
            created_at=now,
        )

        try:
            price_type = ORDER_TYPE_MARKET if use_market else ORDER_TYPE_LIMIT
            result = await self.api.sell_order(
                code=code, qty=qty, price=price,
                price_type=price_type,
            )
            order.order_id = result.get("order_no", "")
            # R15-005: LIVE 경로 상태 전이 + pending_sell_orders 추적
            order.status = OrderStatus.SUBMITTED
            order.submitted_at = now_kst()
            self.pending_sell_orders.append(order)
            logger.info(
                f"[R15-005] LIVE 매도 SUBMITTED: {reason} order_id={order.order_id!r} "
                f"{qty}주 ({'시장가' if use_market else f'{price:,}원'})"
            )
        except Exception as e:
            logger.error(f"매도주문 실패 [{reason}]: {e}")
            # 하드 손절 실패 시 재시도
            if reason in ("hard_stop", "futures_stop"):
                logger.warning(f"{reason} 재시도 (시장가)")
                try:
                    result = await self.api.sell_order(
                        code=code, qty=qty, price=0,
                        price_type=ORDER_TYPE_MARKET,
                    )
                    order.order_id = result.get("order_no", "")
                    # R15-005: 재시도 성공 경로도 SUBMITTED + pending_sell_orders 추적
                    order.status = OrderStatus.SUBMITTED
                    order.submitted_at = now_kst()
                    self.pending_sell_orders.append(order)
                except Exception as e2:
                    logger.critical(f"{reason} 재시도 실패: {e2}")
                    order.status = OrderStatus.REJECTED

        return order

    # ── R15-005: LIVE 체결통보 처리 ────────────────────

    def _find_order_by_id(self, order_id: str) -> Optional[Order]:
        """R15-005: order_id 로 pending_buy_orders / pending_sell_orders 전체 검색.

        체결통보의 ODER_NO 는 10자리 zero-padded string.
        KIS REST 발주 응답의 ODNO 와 동일 포맷.
        """
        if not order_id:
            return None
        for o in self.pending_buy_orders:
            if o.order_id == order_id:
                return o
        for o in self.pending_sell_orders:
            if o.order_id == order_id:
                return o
        return None

    def on_live_acknowledged(self, order_id: str, ts: datetime) -> Optional[Order]:
        """R15-005: CNTG_YN=1 (접수 확정) 수신 처리.

        상태 전이: SUBMITTED → ACKNOWLEDGED. acknowledged_at 갱신.
        이미 ACKNOWLEDGED/PARTIAL/FILLED/CANCELLED/REJECTED 상태면 no-op.
        
        Returns:
            Order: 발견된 주문
            None: order_id 매칭 실패 (외부 개입 의심)
        """
        order = self._find_order_by_id(order_id)
        if order is None:
            logger.warning(
                f"[R15-005] 접수통보 order_id={order_id!r} 매칭 실패 — 외부 개입 의심"
            )
            return None

        if order.status == OrderStatus.SUBMITTED:
            order.status = OrderStatus.ACKNOWLEDGED
            order.acknowledged_at = ts
            if order.submitted_at is not None:
                lag = (ts - order.submitted_at).total_seconds()
                lag_suffix = f" 지연={lag:.3f}s"
            else:
                lag_suffix = ""
            logger.info(
                f"[R15-005] ACKNOWLEDGED: {order.label or order.side.value} "
                f"order_id={order_id} code={order.code}{lag_suffix}"
            )
        else:
            logger.debug(
                f"[R15-005] 접수통보 no-op: order_id={order_id} "
                f"current_status={order.status.value}"
            )
        return order

    def on_live_buy_filled(
        self, order_id: str, filled_price: int, filled_qty: int, ts: datetime
    ) -> Optional[Order]:
        """R15-005: 매수 체결 (CNTG_YN=2, SELN_BYOV_CLS=02) 수신 처리.

        책임:
        1. order_id 로 pending_buy_orders 에서 Order 찾기
        2. Order 필드 갱신 (filled_price/qty/at, status → PARTIAL 또는 FILLED)
        3. Position 생성/갱신
        
        Coordinator.on_buy_filled 호출은 main.py 가 담당 (분리 역할).
        
        Returns:
            Order: 갱신된 주문
            None: order_id 매칭 실패
        """
        order = self._find_order_by_id(order_id)
        if order is None:
            logger.error(
                f"[R15-005] 매수체결통보 order_id={order_id!r} 매칭 실패 — 무시"
            )
            return None

        if order.side != OrderSide.BUY:
            logger.error(
                f"[R15-005] on_live_buy_filled: order_id={order_id} 이 BUY 아님 "
                f"(side={order.side.value}) — 무시"
            )
            return None

        # 누적 체결 반영 (부분체결 지원)
        order.filled_qty += filled_qty
        order.filled_price = filled_price  # 마지막 체결가
        order.filled_at = ts

        if order.filled_qty >= order.qty:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIAL

        # Position 생성/갱신
        if self.position is None:
            self.position = Position(code=order.code, opened_at=ts)
            logger.debug(f"[R15-005] Position 생성: {order.code}")

        # 이번 체결분을 Position 에 누적
        self.position.total_buy_amount += filled_price * filled_qty
        self.position.total_qty += filled_qty

        logger.info(
            f"[R15-005] LIVE 매수 체결: {order.label} order_id={order_id} "
            f"{filled_price:,}원 × {filled_qty}주 (누적 {order.filled_qty}/{order.qty}, "
            f"status={order.status.value})"
        )
        return order

    def on_live_sell_filled(
        self, order_id: str, filled_price: int, filled_qty: int, ts: datetime
    ) -> Optional[Order]:
        """R15-005: 매도 체결 (CNTG_YN=2, SELN_BYOV_CLS=01) 수신 처리.

        책임:
        1. order_id 로 pending_sell_orders 에서 Order 찾기
        2. Order 필드 갱신
        3. Position 매도 반영 (전량 체결 시 is_open=False)
        
        Coordinator.on_sell_filled 호출은 main.py 가 담당.
        """
        order = self._find_order_by_id(order_id)
        if order is None:
            logger.error(
                f"[R15-005] 매도체결통보 order_id={order_id!r} 매칭 실패 — 무시"
            )
            return None

        if order.side != OrderSide.SELL:
            logger.error(
                f"[R15-005] on_live_sell_filled: order_id={order_id} 이 SELL 아님 "
                f"(side={order.side.value}) — 무시"
            )
            return None

        # 누적 체결 반영
        order.filled_qty += filled_qty
        order.filled_price = filled_price
        order.filled_at = ts

        if order.filled_qty >= order.qty:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIAL

        # Position 에 매도 반영
        if self.position is None:
            logger.error(
                f"[R15-005] 매도체결 수신했으나 Position 없음 — Trader 상태 이상"
            )
        else:
            # 이번 체결분만 직접 반영
            if order not in self.position.sell_orders:
                self.position.sell_orders.append(order)
            self.position.total_qty -= filled_qty
            if self.position.total_qty <= 0:
                self.position.is_open = False
                self.position.closed_at = ts

        logger.info(
            f"[R15-005] LIVE 매도 체결: {order.label} order_id={order_id} "
            f"{filled_price:,}원 × {filled_qty}주 (누적 {order.filled_qty}/{order.qty}, "
            f"status={order.status.value})"
        )
        return order

    def on_live_rejected(self, order_id: str, ts: datetime) -> Optional[Order]:
        """R15-005: 거부 (RFUS_YN=1) 수신 처리.

        Order 상태를 REJECTED 로 전이. main.py 가 텔레그램 알림 발송.
        """
        order = self._find_order_by_id(order_id)
        if order is None:
            logger.warning(
                f"[R15-005] 거부통보 order_id={order_id!r} 매칭 실패"
            )
            return None
        order.status = OrderStatus.REJECTED
        logger.error(
            f"[R15-005] LIVE 주문 거부: {order.label} order_id={order_id} "
            f"side={order.side.value} code={order.code}"
        )
        return order

    # ── 상태 ──────────────────────────────────────────────

    def has_position(self) -> bool:
        return self.position is not None and self.position.is_open

    def get_pnl(self, current_price: int = 0) -> float:
        if self.position is None:
            return 0.0
        return self.position.pnl(current_price)

    def reset(self) -> None:
        """일일 초기화."""
        self.position = None
        self.pending_buy_orders = []
        self.pending_sell_orders = []  # R15-005
