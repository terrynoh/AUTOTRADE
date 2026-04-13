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
    intraday_high: int = 0               # 당일 고가 (R-09: API 조회값, pre_955_high 초기화용)

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
