"""
백테스트 실행 스크립트 — KIS API 분봉 데이터로 당일 전략 시뮬레이션.

사용법:
    python run_backtest.py 008350 KOSPI 남선알미늄
    python run_backtest.py 008350,011930,049080

지정한 종목의 가장 최근 거래일 분봉 데이터를 KIS API에서 가져와
현재 전략 로직을 시뮬레이션합니다.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from config.settings import StrategyParams
from src.kis_api.kis import KISAPI
from src.kis_api.constants import EP_MINUTE_CHART, TR_MINUTE_CHART
from src.backtest.simulator import Simulator, SimResult, BacktestResult
from src.backtest.report import print_report
from src.models.trade import ExitReason


async def get_full_minute_chart(api: KISAPI, code: str) -> list[dict]:
    """KIS API에서 하루 전체 분봉 수집 (페이지네이션)."""
    all_candles: dict[str, dict] = {}
    hour = "153000"

    for _ in range(20):
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": hour,
            "FID_PW_DATA_INCU_YN": "Y",
        }
        data = await api._get(EP_MINUTE_CHART, TR_MINUTE_CHART, params)
        items = data.get("output2", [])
        if not items:
            break

        new_count = 0
        last_time = None
        for item in items:
            t = item.get("stck_cntg_hour", "")
            if t not in all_candles:
                all_candles[t] = {
                    "time": t,
                    "open": int(item.get("stck_oprc", "0")),
                    "high": int(item.get("stck_hgpr", "0")),
                    "low": int(item.get("stck_lwpr", "0")),
                    "close": int(item.get("stck_prpr", "0")),
                    "volume": int(item.get("cntg_vol", "0")),
                }
                new_count += 1
            last_time = t

        if new_count == 0 or not last_time:
            break

        # 다음 페이지: 마지막 시간에서 1초 빼기
        h, m, s = int(last_time[:2]), int(last_time[2:4]), int(last_time[4:6])
        total_sec = h * 3600 + m * 60 + s - 1
        if total_sec < 32400:  # 09:00:00
            break
        hour = f"{total_sec // 3600:02d}{(total_sec % 3600) // 60:02d}{total_sec % 60:02d}"
        await asyncio.sleep(0.1)

    return sorted(all_candles.values(), key=lambda x: x["time"])


async def get_stock_info(api: KISAPI, code: str) -> dict:
    """종목 현재가 조회 → 종목명, 시장 구분."""
    info = await api.get_current_price(code)
    market_name = info.get("market_name", "")
    market = "KOSDAQ" if any(k in market_name.upper() for k in ("KOSDAQ", "KSQ")) else "KOSPI"
    return {
        "code": code,
        "name": info.get("name", code),
        "market": market,
        "current_price": info.get("current_price", 0),
        "change_pct": info.get("change_pct", 0),
    }


async def run_backtest(codes: list[str]):
    """메인 백테스트 실행."""
    api = KISAPI(
        app_key=os.getenv("KIS_APP_KEY", ""),
        app_secret=os.getenv("KIS_APP_SECRET", ""),
        account_no=os.getenv("KIS_ACCOUNT_NO", "") + "01",
        is_paper=False,
    )
    await api.connect()

    params = StrategyParams.load()
    sim = Simulator(params)
    today = date.today()

    results: list[SimResult] = []

    for code in codes:
        code = code.strip()
        if not code:
            continue

        # 종목 정보
        logger.info(f"{'='*60}")
        info = await get_stock_info(api, code)
        logger.info(
            f"종목: {info['name']}({code}) {info['market']} "
            f"현재가={info['current_price']:,} 등락={info['change_pct']:+.2f}%"
        )

        # 분봉 수집
        logger.info(f"분봉 수집 중...")
        candles = await get_full_minute_chart(api, code)
        logger.info(f"분봉 {len(candles)}건 수집 완료")

        if not candles:
            logger.warning(f"분봉 데이터 없음 — 스킵")
            continue

        # 분봉 요약
        max_high = max(c["high"] for c in candles)
        max_t = [c for c in candles if c["high"] == max_high][0]["time"]
        logger.info(f"당일 고가: {max_high:,} (시각: {max_t[:2]}:{max_t[2:4]})")

        # 시뮬레이션
        result = sim.simulate_day(
            trade_date=today,
            candidate_info={"code": code, "name": info["name"], "market": info["market"]},
            candles=candles,
        )
        results.append(result)

        # 개별 결과 출력
        _print_sim_result(result)
        await asyncio.sleep(0.2)

    # ── 전체 리포트 ──────────────────────────────────────────
    bt_result = BacktestResult(
        params=params,
        start_date=today,
        end_date=today,
        results=results,
    )
    print_report(bt_result)

    await api.disconnect()


def _print_sim_result(r: SimResult):
    """개별 시뮬레이션 결과 출력."""
    logger.info(f"── 시뮬레이션 결과: {r.name}({r.code}) ──")
    logger.info(f"  당일 고가: {r.intraday_high:,}")

    if r.exit_reason == ExitReason.NO_ENTRY:
        logger.info(f"  결과: 눌림 미발생 → 매매 안 함")
        return

    logger.info(f"  고가 확정 시각: {_fmt_time(r.high_confirmed_at)}")
    logger.info(f"  1차 매수가: {r.buy1_price:,} {'✅체결' if r.buy1_filled else '❌미체결'}")
    logger.info(f"  2차 매수가: {r.buy2_price:,} {'✅체결' if r.buy2_filled else '❌미체결'}")
    logger.info(f"  평균 진입가: {r.avg_entry_price:,}")
    logger.info(f"  진입 시각: {_fmt_time(r.entry_time)}")
    logger.info(f"  눌림 최저가: {r.pullback_low:,}")
    logger.info(f"  목표가: {r.target_price:,}")
    logger.info(f"  청산가: {r.exit_price:,}")
    logger.info(f"  청산 사유: {r.exit_reason.value}")
    logger.info(f"  청산 시각: {_fmt_time(r.exit_time)}")

    emoji = "🟢" if r.pnl_pct > 0 else "🔴" if r.pnl_pct < 0 else "⚪"
    logger.info(f"  수익률: {emoji} {r.pnl_pct:+.2f}% (거래비용 0.5% 차감)")


def _fmt_time(t: str | None) -> str:
    if not t:
        return "-"
    if len(t) >= 4:
        return f"{t[:2]}:{t[2:4]}"
    return t


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python run_backtest.py 008350,011930,049080")
        print("       python run_backtest.py 008350")
        sys.exit(1)

    codes_input = sys.argv[1]
    codes = [c.strip() for c in codes_input.split(",") if c.strip()]

    logger.info(f"백테스트 시작: {', '.join(codes)}")
    asyncio.run(run_backtest(codes))
