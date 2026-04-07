"""
거래일 확인 — pykrx 기반.
KST 시간 유틸리티 포함.

공휴일, 주말 등 비거래일에는 프로그램을 실행하지 않는다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from loguru import logger

# ── KST 시간대 ──────────────────────────────────────────
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """현재 한국시간(KST) 반환. 시스템 시간대와 무관."""
    return datetime.now(KST)


def today_kst() -> date:
    """오늘 날짜(KST) 반환."""
    return now_kst().date()


try:
    from pykrx import stock as pykrx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False
    logger.warning("pykrx 미설치 — 거래일 확인 불가, 평일만 체크합니다")


def is_trading_day(target_date: date | None = None) -> bool:
    """
    해당 날짜가 거래일인지 확인.

    pykrx가 있으면 KRX 캘린더 기반, 없으면 평일만 체크.
    """
    if target_date is None:
        target_date = today_kst()

    # 주말 체크 (빠른 필터)
    if target_date.weekday() >= 5:
        logger.info(f"{target_date} — 주말 (비거래일)")
        return False

    if not PYKRX_AVAILABLE:
        logger.debug(f"{target_date} — pykrx 미설치, 평일이므로 거래일로 간주")
        return True

    try:
        # pykrx로 해당 월의 거래일 목록 조회
        start = target_date.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)

        trading_days = pykrx_stock.get_previous_business_days(
            fromdate=start.strftime("%Y%m%d"),
            todate=end.strftime("%Y%m%d"),
        )

        # Timestamp → date 변환
        trading_dates = {d.date() for d in trading_days}

        is_open = target_date in trading_dates
        logger.info(
            f"{target_date} — {'거래일' if is_open else '비거래일 (공휴일)'}"
        )
        return is_open

    except Exception as e:
        logger.error(f"거래일 확인 실패: {e} — 평일이므로 거래일로 간주")
        return target_date.weekday() < 5


def get_next_trading_day(from_date: date | None = None) -> date:
    """다음 거래일 반환."""
    if from_date is None:
        from_date = today_kst()

    check = from_date + timedelta(days=1)
    for _ in range(10):  # 최대 10일 탐색
        if is_trading_day(check):
            return check
        check += timedelta(days=1)

    return check


def is_half_day(target_date: date | None = None) -> bool:
    """
    반일장 여부 확인.

    ⚠️ pykrx로는 반일장 판별이 어려움.
    설/추석 전날 등은 수동으로 관리하거나, 별도 캘린더 파일 사용.
    현재는 False 반환 (일반장으로 간주).
    """
    # TODO: 반일장 날짜 목록 관리 (YAML 또는 DB)
    return False
