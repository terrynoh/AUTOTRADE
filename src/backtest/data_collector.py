"""
백테스트 데이터 수집 — pykrx로 과거 데이터 수집.

수집 항목:
- 일별 종목별 분봉 데이터 (1분봉)
- 프로그램매매 순매수
- 시총, 거래대금

⚠️ pykrx는 KRX 스크래핑이라 대량 호출 시 속도가 느림.
   하루치 데이터 수집에 수분 소요될 수 있음.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger

try:
    from pykrx import stock as pykrx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backtest"


def collect_daily_data(
    target_date: date,
    market_cap_min: int = 800_000_000_000,
    volume_min: int = 70_000_000_000,
) -> dict:
    """
    특정 거래일의 스크리닝 + 분봉 데이터 수집.

    Returns:
        {
            "date": date,
            "candidates": DataFrame (시총, 거래대금, 프로그램순매수, 등락률),
            "minute_data": {code: DataFrame (분봉)},
        }
    """
    if not PYKRX_AVAILABLE:
        raise RuntimeError("pykrx가 설치되어 있지 않습니다")

    date_str = target_date.strftime("%Y%m%d")
    logger.info(f"데이터 수집: {target_date}")

    # ── 1. 시총 상위 종목 ───────────────────────────────────────
    logger.debug("시총 데이터 조회...")
    try:
        cap_df = pykrx_stock.get_market_cap(date_str)
        cap_df = cap_df[cap_df["시가총액"] >= market_cap_min].copy()
        cap_df["종목코드"] = cap_df.index
    except Exception as e:
        logger.error(f"시총 데이터 수집 실패: {e}")
        return {"date": target_date, "candidates": pd.DataFrame(), "minute_data": {}}

    time.sleep(1)  # KRX 부하 방지

    # ── 2. 거래대금 필터 ────────────────────────────────────────
    logger.debug("거래대금 필터...")
    cap_df = cap_df[cap_df["거래대금"] >= volume_min].copy()

    # ── 3. 등락률 조회 ─────────────────────────────────────────
    logger.debug("등락률 조회...")
    try:
        ohlcv = pykrx_stock.get_market_ohlcv(date_str)
        cap_df = cap_df.join(ohlcv[["등락률"]], how="left")
    except Exception as e:
        logger.warning(f"등락률 조회 실패: {e}")
        cap_df["등락률"] = 0.0

    time.sleep(1)

    # ── 4. 프로그램매매 데이터 ─────────────────────────────────
    # ⚠️ pykrx의 프로그램매매 데이터 가용성 확인 필요
    # get_market_net_purchases_of_equities 등
    logger.debug("프로그램매매 데이터 조회...")
    try:
        # 프로그램매매는 pykrx에서 직접 제공하지 않을 수 있음
        # 대안: KRX 정보데이터시스템에서 CSV 다운로드
        # 여기서는 placeholder
        cap_df["프로그램순매수"] = 0  # TODO: 실제 데이터 소스 연결
        cap_df["프로그램비중"] = 0.0
        logger.warning("프로그램매매 데이터는 별도 수집 필요 (pykrx 미지원 가능)")
    except Exception as e:
        logger.error(f"프로그램매매 데이터 실패: {e}")

    candidates = cap_df.reset_index(drop=True)
    logger.info(f"후보 종목: {len(candidates)}개")

    # ── 5. 분봉 데이터 (상위 종목만) ───────────────────────────
    minute_data = {}
    # 등락률 상위 N개만 분봉 수집 (전체는 너무 오래 걸림)
    top_codes = candidates.nlargest(10, "등락률")["종목코드"].tolist()

    for code in top_codes:
        try:
            logger.debug(f"분봉 수집: {code}")
            df = pykrx_stock.get_market_ohlcv_by_minute(date_str, code)
            if not df.empty:
                minute_data[code] = df
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"분봉 수집 실패 {code}: {e}")

    return {
        "date": target_date,
        "candidates": candidates,
        "minute_data": minute_data,
    }


def collect_range(
    start_date: date,
    end_date: date,
    save: bool = True,
) -> list[dict]:
    """
    기간 내 거래일 데이터 일괄 수집.

    ⚠️ 수일~수십 일 수집 시 상당 시간 소요 (일당 수분).
    """
    results = []
    current = start_date

    while current <= end_date:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        try:
            data = collect_daily_data(current)
            results.append(data)

            if save:
                _save_daily(data)

        except Exception as e:
            logger.error(f"{current} 수집 실패: {e}")

        current += timedelta(days=1)
        time.sleep(2)  # 일 단위 딜레이

    logger.info(f"수집 완료: {len(results)}일")
    return results


def _save_daily(data: dict) -> None:
    """일별 데이터 파일 저장."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    d = data["date"]
    date_str = d.strftime("%Y%m%d")

    # 후보 종목 정보
    if not data["candidates"].empty:
        path = DATA_DIR / f"candidates_{date_str}.csv"
        data["candidates"].to_csv(path, index=False, encoding="utf-8-sig")

    # 분봉 데이터
    for code, df in data.get("minute_data", {}).items():
        path = DATA_DIR / f"minute_{date_str}_{code}.csv"
        df.to_csv(path, encoding="utf-8-sig")

    logger.debug(f"저장 완료: {date_str}")
