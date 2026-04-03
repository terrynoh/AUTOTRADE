"""
주문 실행 — 지정가 매수 2건 배치, 취소/재주문, 청산.

dry_run: 가상 체결 기록
paper: 모의투자 실제 주문
live: 실매매 주문
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from loguru import logger

from config.settings import Settings, StrategyParams
from src.kis_api.kis import KISAPI
from src.kis_api.constants import ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET
from src.models.stock import TradeTarget
from src.models.order import Order, OrderSide, OrderStatus, Position


class Trader:
    """주문 실행 엔진."""

    def __init__(self, api: KISAPI, settings: Settings, params: StrategyParams):
        self.api = api
        self.settings = settings
        self.params = params
        self.position: Optional[Position] = None
        self.pending_buy_orders: list[Order] = []

    # ── 매수 주문 배치 (지정가 2건) ────────────────────────

    async def place_buy_orders(self, target: TradeTarget, available_cash: int) -> None:
        """고가 확정 후 매수 지정가 2건 배치."""
        ep = self.params.entry
        buy1_price = target.buy1_price(self.params)
        buy2_price = target.buy2_price(self.params)

        buy1_amount = int(available_cash * ep.buy1_ratio / 100)
        buy2_amount = int(available_cash * ep.buy2_ratio / 100)

        buy1_qty = max(1, buy1_amount // buy1_price) if buy1_price > 0 else 0
        buy2_qty = max(1, buy2_amount // buy2_price) if buy2_price > 0 else 0

        now = datetime.now()
        self.pending_buy_orders = []

        # 1차 매수
        order1 = await self._send_buy_order(
            target.stock.code, buy1_qty, buy1_price, "buy1", now
        )
        if order1:
            target.buy1_order_id = order1.order_id
            target.buy1_placed = True
            self.pending_buy_orders.append(order1)
            logger.info(
                f"[{target.stock.name}] 1차 매수 주문: {buy1_price:,}원 × {buy1_qty}주 "
                f"(고가 {target.intraday_high:,}원 대비 -{ep.kospi_buy1_pct if target.stock.market.value == 'KOSPI' else ep.kosdaq_buy1_pct}%)"
            )

        # 2차 매수
        order2 = await self._send_buy_order(
            target.stock.code, buy2_qty, buy2_price, "buy2", now
        )
        if order2:
            target.buy2_order_id = order2.order_id
            target.buy2_placed = True
            self.pending_buy_orders.append(order2)
            logger.info(
                f"[{target.stock.name}] 2차 매수 주문: {buy2_price:,}원 × {buy2_qty}주 "
                f"(고가 {target.intraday_high:,}원 대비 -{ep.kospi_buy2_pct if target.stock.market.value == 'KOSPI' else ep.kosdaq_buy2_pct}%)"
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

        if self.settings.is_dry_run:
            order.order_id = f"DRY_{label}_{ts.strftime('%H%M%S')}"
            order.status = OrderStatus.PENDING
            logger.debug(f"[DRY_RUN] 매수주문 시뮬: {label} {price:,}원 × {qty}주")
        else:
            try:
                result = await self.api.buy_order(
                    code=code, qty=qty, price=price, price_type=ORDER_TYPE_LIMIT
                )
                order.order_id = result.get("order_no", "")
                order.status = OrderStatus.PENDING
            except Exception as e:
                logger.error(f"매수주문 실패 [{label}]: {e}")
                order.status = OrderStatus.REJECTED
                return None

        return order

    # ── 매수 주문 취소 (고가 갱신 시) ──────────────────────

    async def cancel_buy_orders(self, target: TradeTarget) -> None:
        """미체결 매수 주문 전량 취소."""
        for order in self.pending_buy_orders:
            if not order.is_active:
                continue

            if self.settings.is_dry_run:
                order.status = OrderStatus.CANCELLED
                logger.debug(f"[DRY_RUN] 주문 취소: {order.label} {order.order_id}")
            else:
                try:
                    await self.api.cancel_order(order.order_id, order.code)
                    order.status = OrderStatus.CANCELLED
                    logger.info(f"주문 취소: {order.label} {order.order_id}")
                except Exception as e:
                    logger.error(f"주문 취소 실패 [{order.label}]: {e}")

        target.buy1_placed = False
        target.buy2_placed = False
        target.buy1_order_id = ""
        target.buy2_order_id = ""
        self.pending_buy_orders = []

    # ── 매수 체결 처리 ────────────────────────────────────

    def on_buy_filled(self, label: str, filled_price: int, filled_qty: int, ts: datetime) -> None:
        """매수 체결 통보 → 포지션 생성/갱신."""
        order = self._find_pending_order(label)
        if order:
            order.filled_price = filled_price
            order.filled_qty = filled_qty
            order.filled_at = ts
            order.status = OrderStatus.FILLED

        if self.position is None:
            self.position = Position(
                code=order.code if order else "",
                opened_at=ts,
            )

        if order:
            self.position.add_buy(order)

    def _find_pending_order(self, label: str) -> Optional[Order]:
        for o in self.pending_buy_orders:
            if o.label == label and o.is_active:
                return o
        return None

    # ── 청산 주문 ─────────────────────────────────────────

    async def execute_exit(
        self, target: TradeTarget, reason: str, price: int = 0
    ) -> Optional[Order]:
        """전량 청산. reason에 따라 시장가/지정가 결정."""
        if self.position is None or self.position.total_qty <= 0:
            return None

        # 먼저 미체결 매수 주문 취소
        await self.cancel_buy_orders(target)

        qty = self.position.total_qty
        code = target.stock.code
        now = datetime.now()

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

        if self.settings.is_dry_run:
            sell_price = self._current_price(target) if use_market else price
            order.order_id = f"DRY_{reason}_{now.strftime('%H%M%S')}"
            order.filled_price = sell_price
            order.filled_qty = qty
            order.filled_at = now
            order.status = OrderStatus.FILLED
            self.position.add_sell(order)
            logger.info(
                f"[DRY_RUN] 청산: {reason} {sell_price:,}원 × {qty}주 "
                f"(P&L {self.position.pnl():+,.0f}원)"
            )
        else:
            try:
                price_type = ORDER_TYPE_MARKET if use_market else ORDER_TYPE_LIMIT
                result = await self.api.sell_order(
                    code=code, qty=qty, price=price,
                    price_type=price_type,
                )
                order.order_id = result.get("order_no", "")
                order.status = OrderStatus.PENDING
                logger.info(f"매도주문 접수: {reason} {qty}주 ({'시장가' if use_market else f'{price:,}원'})")
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
                        order.status = OrderStatus.PENDING
                    except Exception as e2:
                        logger.critical(f"{reason} 재시도 실패: {e2}")
                        order.status = OrderStatus.REJECTED

        return order

    def _current_price(self, target: TradeTarget) -> int:
        """현재가 (dry_run 시뮬레이션용)."""
        return target.stock.current_price

    # ── DRY_RUN 체결 시뮬레이션 ───────────────────────────

    def simulate_fills(self, target: TradeTarget, current_price: int, ts: datetime) -> list[str]:
        """
        DRY_RUN 모드: 현재가가 지정가에 도달하면 가상 체결.
        체결된 label 리스트 반환.
        """
        if not self.settings.is_dry_run:
            return []

        filled_labels = []
        for order in self.pending_buy_orders:
            if not order.is_active:
                continue
            if current_price <= order.price:
                order.filled_price = order.price
                order.filled_qty = order.qty
                order.filled_at = ts
                order.status = OrderStatus.FILLED

                if self.position is None:
                    self.position = Position(code=order.code, opened_at=ts)
                self.position.add_buy(order)

                filled_labels.append(order.label)
                logger.info(
                    f"[DRY_RUN] {order.label} 가상 체결: {order.price:,}원 × {order.qty}주"
                )

        return filled_labels

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
