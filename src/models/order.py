"""
주문 / 포지션 데이터 모델.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"       # 미체결
    FILLED = "FILLED"         # 체결
    PARTIAL = "PARTIAL"       # 부분 체결
    CANCELLED = "CANCELLED"   # 취소
    REJECTED = "REJECTED"     # 거부


@dataclass
class Order:
    """개별 주문."""

    order_id: str = ""                  # 주문번호
    code: str = ""                      # 종목코드
    side: OrderSide = OrderSide.BUY
    price: int = 0                      # 주문가
    qty: int = 0                        # 주문수량
    filled_price: int = 0               # 체결가
    filled_qty: int = 0                 # 체결수량
    status: OrderStatus = OrderStatus.PENDING
    created_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    label: str = ""                     # "buy1", "buy2", "target", "hard_stop", "trend_break", "timeout", "futures_stop", "force"

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.PARTIAL)


@dataclass
class Position:
    """보유 포지션 (1종목 = 1포지션)."""

    code: str = ""
    name: str = ""

    # 매수 내역
    buy_orders: list[Order] = field(default_factory=list)
    total_buy_amount: int = 0           # 총 매수 금액
    total_qty: int = 0                  # 총 보유 수량

    # 매도 내역
    sell_orders: list[Order] = field(default_factory=list)

    # 상태
    is_open: bool = True
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    @property
    def avg_price(self) -> float:
        if self.total_qty <= 0:
            return 0.0
        return self.total_buy_amount / self.total_qty

    @property
    def total_sell_amount(self) -> int:
        return sum(o.filled_price * o.filled_qty for o in self.sell_orders if o.is_filled)

    def pnl(self, current_price: int = 0) -> float:
        """실현 + 미실현 손익."""
        if not self.is_open:
            return self.total_sell_amount - self.total_buy_amount
        unrealized = current_price * self.total_qty
        realized_sell = self.total_sell_amount
        return realized_sell + unrealized - self.total_buy_amount

    def pnl_pct(self, current_price: int = 0) -> float:
        """손익률(%)."""
        if self.total_buy_amount <= 0:
            return 0.0
        return (self.pnl(current_price) / self.total_buy_amount) * 100

    def add_buy(self, order: Order) -> None:
        """매수 체결 반영."""
        self.buy_orders.append(order)
        self.total_buy_amount += order.filled_price * order.filled_qty
        self.total_qty += order.filled_qty
        if self.opened_at is None:
            self.opened_at = order.filled_at

    def add_sell(self, order: Order) -> None:
        """매도 체결 반영."""
        self.sell_orders.append(order)
        self.total_qty -= order.filled_qty
        if self.total_qty <= 0:
            self.is_open = False
            self.closed_at = order.filled_at
