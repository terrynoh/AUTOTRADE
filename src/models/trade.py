"""
거래 기록 / 일일 요약 모델.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class ExitReason(str, Enum):
    TARGET = "TARGET"                   # 목표가 도달
    HARD_STOP = "HARD_STOP"             # 하드 손절 (구조 붕괴)
    TREND_BREAK = "TREND_BREAK"         # (삭제됨, DB 하위호환 전용)
    TIMEOUT = "TIMEOUT"                 # 20분 타임아웃
    FUTURES_STOP = "FUTURES_STOP"       # 선물 급락 청산
    FORCE_LIQUIDATE = "FORCE_LIQUIDATE" # 15:20 강제 청산
    MANUAL = "MANUAL"                   # 수동 청산
    NO_ENTRY = "NO_ENTRY"               # 조정 미발생, 매매 미진입


@dataclass
class TradeRecord:
    """개별 매매 기록.
    
    R-10 확장: 신고가/체결/투자금 대비 수익률 필드 추가
    """

    trade_date: date
    code: str
    name: str
    market: str                         # KOSPI / KOSDAQ

    # R-10: 신고가 달성
    new_high_price: int = 0             # 09:50 이후 신고가
    new_high_time: Optional[datetime] = None

    # R-10: 1차/2차 체결 상세
    buy1_price: int = 0
    buy1_qty: int = 0
    buy1_time: Optional[datetime] = None
    buy2_price: int = 0
    buy2_qty: int = 0
    buy2_time: Optional[datetime] = None

    # 매수 요약
    avg_buy_price: float = 0.0
    total_buy_qty: int = 0
    total_buy_amount: int = 0
    buy_count: int = 0                  # 매수 횟수 (초기 + 분할)
    first_buy_time: Optional[datetime] = None

    # 매도
    avg_sell_price: float = 0.0
    total_sell_amount: int = 0
    sell_time: Optional[datetime] = None

    # 결과
    exit_reason: ExitReason = ExitReason.NO_ENTRY
    pnl: float = 0.0                   # 손익 금액
    pnl_pct: float = 0.0               # 손익률(%)
    capital_pnl_pct: float = 0.0       # R-10: 투자금 대비 수익률(%)
    holding_minutes: float = 0.0        # 보유 시간(분)
    holding_seconds: int = 0           # R-10: 보유 시간(초)

    # 기준값
    rolling_high: int = 0
    entry_trigger_price: int = 0        # 매수 트리거가 (= target_buy1_price)
    target_buy2_price: int = 0          # R10-011: 2차 매수 목표가 (미체결 알림용)
    target_price: float = 0.0          # 목표가
    hard_stop_price: int = 0           # R-10: 손절가

    # What-if 시나리오 시뮬용 (실측 50% 기준 post_entry_low)
    post_entry_low: int = 0            # 매수 후 저점 (confirmed_high + post_entry_low) / 2 의 그 값

    # 메타
    trade_mode: str = "dry_run"         # dry_run | paper | live
    capital: int = 50_000_000          # R-10: 투자금


