"""
종목 데이터 모델 — 스크리닝 후보, 매매 타겟.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class MarketType(str, Enum):
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"


@dataclass
class StockCandidate:
    """09:50 스크리닝 후보 종목."""

    code: str
    name: str
    market: MarketType
    trading_volume_krw: int              # 거래대금 (원)
    program_net_buy: int                 # 프로그램 순매수 금액 (원)
    price_change_pct: float              # 주가 등락률 (%)
    current_price: int                   # 현재가

    @property
    def program_net_buy_ratio(self) -> float:
        """프로그램순매수비중(%) = 프로그램순매수 / 거래대금 × 100."""
        if self.trading_volume_krw <= 0:
            return 0.0
        return (self.program_net_buy / self.trading_volume_krw) * 100

    def __repr__(self) -> str:
        return (
            f"<{self.name}({self.code}) {self.market.value} "
            f"거래대금={self.trading_volume_krw/1e8:.0f}억 "
            f"프로그램비중={self.program_net_buy_ratio:.1f}% "
            f"등락={self.price_change_pct:+.2f}%>"
        )


@dataclass
class TradeTarget:
    """매매 타겟 — 스크리닝 통과 후 실시간 감시 대상."""

    stock: StockCandidate

    # ── 고가 추적 ──
    intraday_high: int = 0                       # 당일 최고가
    intraday_high_time: Optional[datetime] = None
    new_high_achieved: bool = False               # 9:55 이후 신고가 달성 여부
    high_confirmed: bool = False                  # 고가 확정 (1% 하락 트리거)

    # ── 선물 추적 ──
    futures_price_at_high: float = 0.0            # 종목 고점 시각의 선물 가격 (소수점 포함)

    # ── 매수 주문 ──
    buy1_placed: bool = False                    # 1차 매수 주문 접수 여부
    buy2_placed: bool = False                    # 2차 매수 주문 접수 여부
    buy1_order_id: str = ""                      # 1차 매수 주문번호 (취소용)
    buy2_order_id: str = ""                      # 2차 매수 주문번호 (취소용)
    buy1_filled: bool = False                    # 1차 체결
    buy2_filled: bool = False                    # 2차 체결

    # ── 매수 실적 ──
    total_buy_amount: int = 0
    total_buy_qty: int = 0

    # ── 최저가 추적 (20분 타이머용) ──
    post_entry_low: int = 0
    post_entry_low_time: Optional[datetime] = None

    # ── 상태 ──
    exited: bool = False
    exit_reason: str = ""

    @property
    def avg_price(self) -> float:
        if self.total_buy_qty <= 0:
            return 0.0
        return self.total_buy_amount / self.total_buy_qty

    @property
    def has_position(self) -> bool:
        return self.total_buy_qty > 0 and not self.exited

    @property
    def target_price(self) -> float:
        """목표가 = (고가 + 눌림 최저가) / 2."""
        if self.post_entry_low <= 0:
            return 0.0
        return (self.intraday_high + self.post_entry_low) / 2

    def buy1_price(self, params) -> int:
        """1차 매수 지정가 (고가 대비 N% 하락)."""
        pct = params.entry.kospi_buy1_pct if self.stock.market == MarketType.KOSPI else params.entry.kosdaq_buy1_pct
        return int(self.intraday_high * (1 - pct / 100))

    def buy2_price(self, params) -> int:
        """2차 매수 지정가 (고가 대비 N% 하락)."""
        pct = params.entry.kospi_buy2_pct if self.stock.market == MarketType.KOSPI else params.entry.kosdaq_buy2_pct
        return int(self.intraday_high * (1 - pct / 100))

    def hard_stop_pct(self, params) -> float:
        if self.stock.market == MarketType.KOSPI:
            return params.exit.kospi_hard_stop_pct
        return params.exit.kosdaq_hard_stop_pct

    def hard_stop_price(self, params) -> int:
        return int(self.intraday_high * (1 - self.hard_stop_pct(params) / 100))

    def update_intraday_high(self, price: int, ts: datetime) -> bool:
        """고가 갱신. 갱신됐으면 True 반환."""
        if price > self.intraday_high:
            self.intraday_high = price
            self.intraday_high_time = ts
            self.high_confirmed = False  # 신고가 → 확정 리셋
            return True
        return False

    def update_post_entry_low(self, price: int, ts: datetime) -> None:
        if self.post_entry_low == 0 or price < self.post_entry_low:
            self.post_entry_low = price
            self.post_entry_low_time = ts
