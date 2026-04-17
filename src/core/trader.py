"""
주문 실행 — 지정가 매수 2건 배치, 취소/재주문, 청산.

dry_run: 가상 체결 기록
paper: 모의투자 실제 주문
live: 실매매 주문
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from typing import TYPE_CHECKING, Optional

from src.utils.market_calendar import now_kst

from loguru import logger

from config.settings import Settings, StrategyParams
from src.kis_api.kis import KISAPI
from src.kis_api.constants import ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET
from src.core.watcher import Watcher
from src.models.order import Order, OrderSide, OrderStatus, Position

if TYPE_CHECKING:
    from src.core.fill_manager import FillManager


class Trader:
    """주문 실행 엔진."""

    def __init__(self, api: KISAPI, settings: Settings, params: StrategyParams):
        self.api = api
        self.settings = settings
        self.params = params
        self.position: Optional[Position] = None
        self.pending_buy_orders: list[Order] = []
        self._fill_manager: Optional["FillManager"] = None  # R-14: W-36

    def set_fill_manager(self, fill_manager: "FillManager") -> None:
        """FillManager 주입 (R-14: W-36).
        
        main.py에서 Trader 생성 후 호출.
        """
        self._fill_manager = fill_manager
        logger.debug("[Trader] FillManager 연결 완료")

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

        if self.settings.is_dry_run:
            order.order_id = f"DRY_{label}_{ts.strftime('%H%M%S')}"
            
            # R-14: FillManager로 예수금 검증 + 동결
            if self._fill_manager is not None:
                ok, reason = self._fill_manager.try_place_order(order)
                if not ok:
                    logger.warning(f"[DRY_RUN] 주문 거부 ({label}): {reason}")
                    return None
                # try_place_order가 order.status = PENDING 설정
            else:
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

    async def cancel_buy_orders(self, watcher: Watcher) -> None:
        """미체결 매수 주문 전량 취소."""
        for order in self.pending_buy_orders:
            if not order.is_active:
                continue

            if self.settings.is_dry_run:
                # R-14: FillManager로 동결 해제
                if self._fill_manager is not None:
                    self._fill_manager.cancel_order(order)
                    # cancel_order가 order.status = CANCELLED 설정
                else:
                    order.status = OrderStatus.CANCELLED
                logger.debug(f"[DRY_RUN] 주문 취소: {order.label} {order.order_id}")
            else:
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
        if self.settings.is_dry_run:
            # R-14: FillManager로 동결 해제
            if self._fill_manager is not None:
                self._fill_manager.cancel_order(old_order)
            else:
                old_order.status = OrderStatus.CANCELLED
            logger.info(f"[DRY_RUN] buy2 취소: {old_price:,}원 → 재발주 준비")
        else:
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

        if self.settings.is_dry_run:
            sell_price = self._current_price(watcher) if use_market else price
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

    def _current_price(self, watcher: Watcher) -> int:
        """현재가 (dry_run 시뮬레이션용)."""
        return watcher.current_price

    # ── DRY_RUN 체결 시뮬레이션 (R-14: W-36 FillManager 위임) ───

    def simulate_fills(self, watcher: Watcher, current_price: int, ts: datetime) -> list[tuple[str, int, int]]:
        """
        DRY_RUN 모드: 현재가가 지정가에 도달하면 가상 체결.
        
        R-14 (W-36): FillManager가 있으면 위임, 없으면 기존 로직.
        
        Returns:
            list[tuple[str, int, int]]: [(label, filled_price, filled_qty), ...]
        """
        if not self.settings.is_dry_run:
            return []

        # R-14: FillManager 사용 (고급 시뮬레이션)
        if self._fill_manager is not None:
            filled_data = self._fill_manager.check_fills(watcher, self.pending_buy_orders, ts)
            
            # R-14 디버그: position 생성 추적
            logger.debug(
                f"[simulate_fills] 체결 전: filled_data={len(filled_data)}건, "
                f"position={self.position is not None}, "
                f"pending_orders={len(self.pending_buy_orders)}"
            )
            
            # R-14 버그픽스: 부분체결(PARTIAL)도 Position에 반영
            # 기존: _find_filled_order가 FILLED만 찾아서 PARTIAL 시 Position 미생성
            # 수정: filled_data의 (label, price, qty)를 직접 Position에 추가
            for label, price, qty in filled_data:
                if self.position is None:
                    self.position = Position(code=watcher.code, opened_at=ts)
                    logger.debug(f"[simulate_fills] Position 생성: {watcher.code}")
                # 이번 체결분만 직접 추가 (PARTIAL/FILLED 상태와 무관)
                self.position.total_buy_amount += price * qty
                self.position.total_qty += qty
            
            if filled_data:
                logger.debug(
                    f"[simulate_fills] 체결 후: position.total_qty={self.position.total_qty if self.position else 0}"
                )
            
            return filled_data

        # 기존 로직 (하위 호환)
        filled_data = []
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

                filled_data.append((order.label, order.price, order.qty))
                logger.info(
                    f"[DRY_RUN] {order.label} 가상 체결: {order.price:,}원 × {order.qty}주"
                )

        return filled_data

    def _find_filled_order(self, label: str) -> Optional[Order]:
        """체결 완료된 주문 찾기 (R-14: W-36)."""
        for o in self.pending_buy_orders:
            if o.label == label and o.status == OrderStatus.FILLED:
                return o
        return None

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
