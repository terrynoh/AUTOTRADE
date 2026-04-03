"""
KIS API 응답을 비즈니스 로직에서 사용할 형태로 변환.

시총 조회는 KIS API에 적절한 엔드포인트가 없어 pykrx 유지.
"""
from __future__ import annotations

import datetime

from loguru import logger

from src.kis_api.kis import KISAPI
from src.kis_api.constants import MARKET_CODE_KOSPI, MARKET_CODE_KOSDAQ


# ── 시가총액 상위 — pykrx 사용 (KIS API에 시총순위 엔드포인트 없음) ──

def fetch_market_cap_rank(
    api,  # KISAPI | None — pykrx 사용으로 실제로 쓰이지 않음
    market: str = MARKET_CODE_KOSPI,
    min_cap: int = 800_000_000_000,
) -> list[dict]:
    """
    시가총액 상위 종목 조회 (pykrx 사용).

    프로그램 시작 시 1회 캐시 — 장중 호출 금지.

    Returns:
        list[dict] — {"code", "name", "market_cap"}
    """
    from pykrx import stock as krx

    market_str = "KOSPI" if market == MARKET_CODE_KOSPI else "KOSDAQ"

    # 최근 7일 역순으로 데이터 있는 날 탐색 (오늘 데이터 미집계/휴장 대비)
    df = None
    for delta in range(7):
        date_str = (datetime.date.today() - datetime.timedelta(days=delta)).strftime("%Y%m%d")
        try:
            candidate = krx.get_market_cap(date_str, market=market_str)
            if candidate.empty:
                continue
            if "시가총액" not in candidate.columns:
                logger.warning(f"pykrx 컬럼 불일치 ({date_str}): {list(candidate.columns)}")
                continue
            df = candidate
            logger.info(f"pykrx 시총 데이터 날짜: {date_str}")
            break
        except Exception as e:
            logger.warning(f"pykrx {date_str} 조회 실패: {e}")
            continue

    if df is None:
        logger.warning("pykrx 시총 데이터 없음 (최근 7일 조회 실패)")
        return []

    filtered = df[df["시가총액"] >= min_cap]

    results = []
    for code, row in filtered.iterrows():
        name = krx.get_market_ticker_name(code)
        results.append({
            "code": code,
            "name": name,
            "market_cap": int(row["시가총액"]),
        })

    logger.info(f"시총 상위 조회: {market_str} — {len(results)}종목 (≥{min_cap/1e8:.0f}억)")
    return results


# ── 거래대금 상위 ──────────────────────────────────────────────

async def fetch_volume_rank(
    api: KISAPI,
    market: str = MARKET_CODE_KOSPI,
    min_volume: int = 70_000_000_000,
) -> list[dict]:
    """거래대금 상위 종목 조회."""
    results = await api.get_volume_rank(market=market, min_volume=min_volume)
    logger.info(f"거래대금 상위: {market} — {len(results)}종목 (≥{min_volume/1e8:.0f}억)")
    return results


# ── 종목별 프로그램매매 ──────────────────────────────────────

async def fetch_program_trade(api: KISAPI, code: str) -> dict:
    """개별 종목 당일 프로그램매매 순매수 조회."""
    result = await api.get_program_trade(code)
    return result


# ── 주식 기본정보 ──────────────────────────────────────────────

async def fetch_stock_info(api: KISAPI, code: str) -> dict:
    """종목 기본 정보 (현재가, 등락률, 시총 등)."""
    result = await api.get_current_price(code)
    return result


# ── 분봉 차트 ──────────────────────────────────────────────────

async def fetch_minute_chart(api: KISAPI, code: str) -> list[dict]:
    """1분봉 차트 조회."""
    result = await api.get_minute_chart(code)
    return result


# ── 계좌 잔고 ──────────────────────────────────────────────────

async def fetch_account_balance(api: KISAPI) -> dict:
    """계좌 잔고 조회."""
    result = await api.get_balance()
    return result
