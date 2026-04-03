"""
AUTOTRADE 웹 대시보드 — FastAPI + WebSocket 실시간 모니터링.

실행:
    python -m src.dashboard.app
    → http://localhost:8501
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from config.settings import Settings, StrategyParams
from src.kis_api.kis import KISAPI
from src.core.screener import Screener
from src.core.monitor import TargetMonitor, MonitorState
from src.core.trader import Trader
from src.core.risk_manager import RiskManager

ADMIN_TOKEN = os.getenv("DASHBOARD_ADMIN_TOKEN", "")

# 관리자 토큰 미설정 시 경고
if not ADMIN_TOKEN:
    logger.warning("DASHBOARD_ADMIN_TOKEN 미설정 — 모든 요청이 관리자 권한으로 처리됩니다")

import sys as _sys

if getattr(_sys, "frozen", False):
    # PyInstaller exe: 번들된 리소스 경로
    _BASE = Path(_sys._MEIPASS)
    TEMPLATES_DIR = _BASE / "src" / "dashboard" / "templates"
else:
    TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="AUTOTRADE Dashboard")

# CORS 미들웨어 — localhost 대시보드만 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_admin(token: str = Header(None, alias="X-Admin-Token")) -> None:
    """관리자 토큰 검증 (timing-safe). 토큰 미설정 시 제한 없음."""
    if ADMIN_TOKEN and not hmac.compare_digest(token or "", ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="관리자 권한 필요")

# ── 글로벌 상태 ──────────────────────────────────────────────

class DashboardState:
    """대시보드에서 공유하는 전역 상태."""

    def __init__(self):
        self.settings: Optional[Settings] = None
        self.params: Optional[StrategyParams] = None
        self.api: Optional[KISAPI] = None
        self.connected: bool = False
        self.trade_mode: str = ""
        self.available_cash: int = 0
        self.total_eval: int = 0

        self.monitors: list[TargetMonitor] = []
        self.trader: Optional[Trader] = None
        self.risk: Optional[RiskManager] = None

        self.log_messages: list[dict] = []
        self._ws_clients: list[WebSocket] = []

    def add_log(self, level: str, msg: str) -> None:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "msg": msg,
        }
        self.log_messages.append(entry)
        if len(self.log_messages) > 200:
            self.log_messages = self.log_messages[-200:]

    def to_dict(self) -> dict:
        monitors_data = []
        for mon in self.monitors:
            t = mon.target
            monitors_data.append({
                "code": t.stock.code,
                "name": t.stock.name,
                "market": t.stock.market.value,
                "current_price": t.stock.current_price,
                "change_pct": t.stock.price_change_pct,
                "intraday_high": t.intraday_high,
                "new_high_achieved": t.new_high_achieved,
                "high_confirmed": t.high_confirmed,
                "state": mon.state.value,
                "buy1_placed": t.buy1_placed,
                "buy2_placed": t.buy2_placed,
                "buy1_filled": t.buy1_filled,
                "buy2_filled": t.buy2_filled,
                "avg_price": round(t.avg_price),
                "total_qty": t.total_buy_qty,
                "target_price": round(t.target_price),
                "post_entry_low": t.post_entry_low,
                "exit_reason": t.exit_reason,
                "buy1_price": t.buy1_price(self.params) if self.params and t.intraday_high > 0 else 0,
                "buy2_price": t.buy2_price(self.params) if self.params and t.intraday_high > 0 else 0,
                "hard_stop_price": t.hard_stop_price(self.params) if self.params and t.intraday_high > 0 else 0,
                "pnl": round(self.trader.get_pnl(t.stock.current_price)) if self.trader else 0,
            })

        return {
            "connected": self.connected,
            "trade_mode": self.trade_mode,
            "available_cash": self.available_cash,
            "total_eval": self.total_eval,
            "daily_pnl": round(self.risk.daily_pnl) if self.risk else 0,
            "daily_trades": self.risk.daily_trades if self.risk else 0,
            "monitors": monitors_data,
            "logs": self.log_messages[-100:],
            "server_time": datetime.now().strftime("%H:%M:%S"),
        }

    async def broadcast(self, data: dict) -> None:
        dead = []
        for ws in self._ws_clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_clients.remove(ws)


state = DashboardState()


# ── API 라우트 ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """메인 페이지 — 관리자 토큰은 HTML에 포함하지 않음."""
    html_path = TEMPLATES_DIR / "index.html"
    html = html_path.read_text(encoding="utf-8")
    # 관리자 여부는 클라이언트에서 토큰 입력 후 판단
    html = html.replace("/*__ADMIN__*/", "const IS_ADMIN = false;")
    html = html.replace("/*__TOKEN__*/", 'const ADMIN_TOKEN = "";')
    return HTMLResponse(html)


@app.get("/api/status")
async def api_status(token: str = Header(None, alias="X-Admin-Token")):
    """상태 조회 — 관리자 토큰 필요."""
    _check_admin(token)
    return state.to_dict()


@app.post("/api/connect")
async def api_connect(token: str = Header(None, alias="X-Admin-Token")):
    """KIS API 연결."""
    _check_admin(token)
    try:
        state.settings = Settings()
        state.params = StrategyParams.load()
        state.trade_mode = state.settings.trade_mode

        state.api = KISAPI(
            app_key=state.settings.kis_app_key,
            app_secret=state.settings.kis_app_secret,
            account_no=state.settings.account_no,
            is_paper=state.settings.is_paper_mode,
        )
        await state.api.connect()
        state.connected = True

        balance = await state.api.get_balance()
        state.available_cash = balance.get("available_cash", 0)
        state.total_eval = balance.get("total_eval", 0)

        state.risk = RiskManager(state.params)
        state.trader = Trader(state.api, state.settings, state.params)

        state.add_log("INFO", f"KIS 연결 완료 ({state.api.get_server_type()})")
        state.add_log("INFO", f"예수금: {state.available_cash:,}원")
        return {"ok": True, "msg": "연결 성공"}

    except Exception as e:
        logger.error(f"연결 실패: {e}")
        state.add_log("ERROR", "연결 실패")
        return {"ok": False, "msg": "연결 중 오류 발생"}


@app.post("/api/screening")
async def api_screening(token: str = Header(None, alias="X-Admin-Token")):
    """수동 스크리닝 실행."""
    _check_admin(token)
    if not state.connected or not state.api:
        return {"ok": False, "msg": "API 미연결"}

    try:
        screener = Screener(state.api, state.params, is_live=(state.trade_mode == "live"))

        # loguru 로그를 대시보드 UI에도 표시
        def _log_sink(message):
            record = message.record
            state.add_log(record["level"].name, record["message"])

        sink_id = logger.add(_log_sink, level="DEBUG", format="{message}")
        try:
            targets = await screener.run()
        finally:
            logger.remove(sink_id)

        state.monitors = []
        for target in targets:
            mon = TargetMonitor(target, state.params)
            state.monitors.append(mon)

        state.add_log("INFO", f"스크리닝 완료: {len(targets)}종목 선정")
        for t in targets:
            state.add_log("INFO", f"  타겟: {t.stock.name}({t.stock.code}) 등락{t.stock.price_change_pct:+.2f}%")

        return {"ok": True, "msg": f"{len(targets)}종목 선정"}

    except Exception as e:
        logger.error(f"스크리닝 실패: {e}")
        state.add_log("ERROR", "스크리닝 실패")
        return {"ok": False, "msg": "스크리닝 중 오류 발생"}


@app.post("/api/disconnect")
async def api_disconnect(token: str = Header(None, alias="X-Admin-Token")):
    """KIS API 연결 해제."""
    _check_admin(token)
    if state.api:
        await state.api.disconnect()
    state.connected = False
    state.monitors = []
    state.add_log("INFO", "연결 해제")
    return {"ok": True}


# ── WebSocket (실시간 업데이트) ───────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket — 읽기 전용 상태 브로드캐스트. 제어 명령 불가."""
    await ws.accept()
    state._ws_clients.append(ws)
    try:
        while True:
            # 1초마다 상태 전송 (읽기 전용, 민감 데이터 제외)
            await ws.send_json(state.to_dict())
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        state._ws_clients.remove(ws)
    except Exception:
        if ws in state._ws_clients:
            state._ws_clients.remove(ws)


# ── 실행 ──────────────────────────────────────────────────────

def run_dashboard(host: str = "127.0.0.1", port: int = 8501):
    import uvicorn
    logger.info(f"대시보드 시작: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run_dashboard()
