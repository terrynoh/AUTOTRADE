"""
AUTOTRADE 통합 실행기.

AutoTrader(매매 엔진) + Dashboard(웹 모니터링)를 동시에 실행한다.
대시보드만 단독 실행도 가능 (--dashboard-only).

실행:
    python run.py                  # 매매 + 대시보드
    python run.py --dashboard-only # 대시보드만 (뷰어 모드)
"""
from __future__ import annotations

import argparse
import asyncio
import threading
import sys

import uvicorn
from loguru import logger


def run_dashboard(host: str, port: int) -> None:
    """대시보드 서버를 별도 스레드에서 실행."""
    from src.dashboard.app import app
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(description="AUTOTRADE 통합 실행기")
    parser.add_argument(
        "--dashboard-only",
        action="store_true",
        help="대시보드만 실행 (매매 엔진 없이 뷰어 모드)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="대시보드 바인드 주소")
    parser.add_argument("--port", type=int, default=8501, help="대시보드 포트")
    args = parser.parse_args()

    if args.dashboard_only:
        logger.info(f"대시보드 단독 실행: http://localhost:{args.port}")
        run_dashboard(args.host, args.port)
        return

    # 대시보드를 백그라운드 스레드로 시작
    logger.info(f"대시보드 시작: http://localhost:{args.port}")
    dash_thread = threading.Thread(
        target=run_dashboard,
        args=(args.host, args.port),
        daemon=True,
    )
    dash_thread.start()

    # AutoTrader에 대시보드 state를 연결 후 실행
    from src.main import AutoTrader
    from src.dashboard.app import state as dash_state

    trader = AutoTrader()

    # loguru 로그를 대시보드 UI에도 전송
    def _log_to_dash(message):
        record = message.record
        dash_state.add_log(record["level"].name, record["message"])

    logger.add(_log_to_dash, level="INFO", format="{message}")

    # AutoTrader 실행 후 state 동기화 콜백 등록
    trader.on_state_update = lambda: _sync_to_dashboard(trader, dash_state)

    asyncio.run(trader.run())


def _sync_to_dashboard(trader, dash_state) -> None:
    """AutoTrader 상태를 대시보드 state에 동기화.

    R16: dead reference (_monitors, dash_state.trader, dash_state.risk) 정리.
    대시보드 내 DashboardState 실제 필드만 셀팅.
    실제 동기화는 main.py _start_dashboard_server 의 attach_autotrader →
    dashboard/app.py _sync_from_autotrader 에서 수행됨.
    """
    dash_state.connected = True
    dash_state.trade_mode = trader.settings.trade_mode
    dash_state.available_cash = trader._available_cash


if __name__ == "__main__":
    main()
