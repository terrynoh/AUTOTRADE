"""
scripts/dashboard_sim.py

대시보드 정상 작동 확인용 시뮬 스크립트.

목표:
1. 대시보드 UI 가 Watcher 객체를 정상 표시?
2. W-07b 발견 12 (price_change_pct=0) / 발견 13 (state name) 영향 확인?
3. to_dict 의 26개 필드 매핑이 정확?

가동:
  python scripts/dashboard_sim.py

종료:
  Ctrl+C
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from src.main import AutoTrader
from src.core.watcher import Watcher, WatcherState
from src.models.stock import MarketType


def make_fake_watchers(at: AutoTrader) -> None:
    """3개 가짜 watcher 를 Coordinator 에 주입."""
    params = at.params

    # 종목 1: 005930 삼성전자 — WATCHING (사전 고가만, 09:55 전)
    w1 = Watcher(code="005930", name="삼성전자", market=MarketType.KOSPI, params=params)
    w1.intraday_high = 71500
    w1.pre_955_high = 71500
    w1.current_price = 71200
    w1.state = WatcherState.WATCHING

    # 종목 2: 000660 SK하이닉스 — READY (트리거 발동, 1차 매수 자리)
    w2 = Watcher(code="000660", name="SK하이닉스", market=MarketType.KOSPI, params=params)
    w2.intraday_high = 125000
    w2.confirmed_high = 125000
    w2.confirmed_high_time = datetime.now()
    w2.high_confirmed_at = datetime.now()
    w2.target_buy1_price = int(125000 * (1 - params.entry.kospi_buy1_pct / 100))  # ~121,625
    w2.target_buy2_price = int(125000 * (1 - params.entry.kospi_buy2_pct / 100))  # ~120,875
    w2.hard_stop_price_value = int(125000 * (1 - params.exit.kospi_hard_stop_pct / 100))  # ~119,875
    w2.current_price = 121800  # 1차 매수가 근처
    w2.state = WatcherState.READY
    w2.new_high_achieved = True

    # 종목 3: 035720 카카오 — ENTERED (1차 체결, 보유 중)
    w3 = Watcher(code="035720", name="카카오", market=MarketType.KOSPI, params=params)
    w3.intraday_high = 52000
    w3.confirmed_high = 52000
    w3.confirmed_high_time = datetime.now() - timedelta(minutes=5)
    w3.high_confirmed_at = datetime.now() - timedelta(minutes=5)
    w3.target_buy1_price = int(52000 * (1 - params.entry.kospi_buy1_pct / 100))  # ~50,596
    w3.target_buy2_price = int(52000 * (1 - params.entry.kospi_buy2_pct / 100))  # ~50,284
    w3.hard_stop_price_value = int(52000 * (1 - params.exit.kospi_hard_stop_pct / 100))  # ~49,868
    w3.current_price = 50800
    w3.state = WatcherState.ENTERED
    w3.new_high_achieved = True
    w3.entered_at = datetime.now() - timedelta(minutes=3)
    w3.buy1_filled = True
    w3.buy1_price = 50596
    w3.total_buy_qty = 100
    w3.total_buy_amount = 5059600
    w3.post_entry_low = 50500
    w3.post_entry_low_time = datetime.now() - timedelta(minutes=2)

    at._coordinator.watchers = [w1, w2, w3]
    at._coordinator._active_code = "035720"  # 종목 3 이 active
    at._coordinator._screening_done = True

    logger.info(f"가짜 watchers 3개 주입 완료")
    logger.info(f"  1) 005930 삼성전자 — WATCHING (사전고가 71,500)")
    logger.info(f"  2) 000660 SK하이닉스 — READY (트리거 후, 매수 자리)")
    logger.info(f"  3) 035720 카카오 — ENTERED (active, 보유 100주 @ 50,596)")


async def update_prices_loop(at: AutoTrader) -> None:
    """5초 간격으로 가격 갱신 시뮬. 30회 (2.5분) 후 종료."""
    import random

    base_prices = {
        "005930": 71200,
        "000660": 121800,
        "035720": 50800,
    }

    for tick in range(30):
        await asyncio.sleep(5)

        for w in at._coordinator.watchers:
            # 랜덤 가격 변동 (±0.3%)
            base = base_prices[w.code]
            change = random.uniform(-0.003, 0.003)
            new_price = int(base * (1 + change))
            w.current_price = new_price
            base_prices[w.code] = new_price

            # 신고가 갱신 (가능하면)
            if new_price > w.intraday_high:
                w.intraday_high = new_price
                w.intraday_high_time = datetime.now()

        logger.info(
            f"[Tick {tick+1:02d}/30] "
            f"삼성={base_prices['005930']:,} / "
            f"SK={base_prices['000660']:,} / "
            f"카카오={base_prices['035720']:,}"
        )

        # 대시보드 동기화
        if at.on_state_update:
            try:
                await at.on_state_update()
            except Exception as e:
                logger.warning(f"대시보드 동기화 실패: {e}")

    logger.info("시뮬 종료 (30 tick 완료)")


async def sim_body(at: AutoTrader) -> None:
    """uvicorn 과 gather 되는 시뮬 본체. 서버 기동 후 1초 대기 후 진입."""
    await asyncio.sleep(1)  # uvicorn startup 대기

    port = at.settings.dashboard_port
    admin_token = os.getenv("DASHBOARD_ADMIN_TOKEN", "")
    logger.info(f"대시보드 서버 시작 (포트 {port})")
    logger.info(f"로컬 접속: http://localhost:{port}")
    if admin_token:
        logger.info(f"관리자 접속: http://localhost:{port}?token={admin_token}")

    # 가짜 watchers 주입
    make_fake_watchers(at)

    # 첫 동기화
    if at.on_state_update:
        try:
            await at.on_state_update()
            logger.info("초기 대시보드 동기화 완료")
        except Exception as e:
            logger.warning(f"초기 동기화 실패: {e}")

    # 가격 갱신 루프
    try:
        await update_prices_loop(at)
    except KeyboardInterrupt:
        logger.info("사용자 종료 (Ctrl+C)")
    finally:
        if hasattr(at, '_dashboard_server') and at._dashboard_server is not None:
            at._dashboard_server.should_exit = True
        await asyncio.sleep(1)
        logger.info("종료 완료")


async def main():
    logger.info("=" * 60)
    logger.info("대시보드 정상 작동 시뮬 시작")
    logger.info("=" * 60)

    # 1. AutoTrader 인스턴스 생성
    at = AutoTrader()
    logger.info(f"AutoTrader 인스턴스 생성 완료 (모드: {at.settings.trade_mode})")

    # 2. 잔고 설정
    if at.settings.is_dry_run and at.settings.dry_run_cash > 0:
        at._initial_cash = at.settings.dry_run_cash
        logger.info(f"[DRY_RUN] 가상 예수금 사용: {at._initial_cash:,}원")
    at._available_cash = at.risk.calculate_available_cash(at._initial_cash)
    at._coordinator.set_available_cash(at._available_cash)
    logger.info(f"예수금: {at._initial_cash:,}원 -> 매매가용: {at._available_cash:,}원")

    # 3. attach (uvicorn 시작 전에 state 연결)
    from src.dashboard.app import app as dashboard_app, attach_autotrader
    attach_autotrader(at)
    logger.info("attach_autotrader 완료")

    # 4. uvicorn 직접 구성 + sim_body 를 gather 로 병렬 실행
    import uvicorn
    port = at.settings.dashboard_port
    config = uvicorn.Config(dashboard_app, host="0.0.0.0", port=port,
                            log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    at._dashboard_server = server

    try:
        await asyncio.gather(
            server.serve(),
            sim_body(at),
        )
    except KeyboardInterrupt:
        logger.info("종료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("종료")
