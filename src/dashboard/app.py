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

from src.utils.market_calendar import now_kst
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

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

# CORS 미들웨어 — localhost + Cloudflare Tunnel 허용
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.trycloudflare\.com|https://.*\.cloudflareaccess\.com|http://localhost:\d+|http://127\.0\.0\.1:\d+",
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

        # 수동 종목 입력
        self.manual_codes: list[str] = []

        # 종목명 캐시: {code: name} + {name_upper: code}
        self._stock_name_cache: dict[str, str] = {}
        self._stock_name_reverse: dict[str, str] = {}

        from collections import deque
        self.log_messages: deque[dict] = deque(maxlen=200)
        self._ws_clients: list[WebSocket] = []

    def cache_stock(self, code: str, name: str) -> None:
        """종목 캐시에 추가."""
        if code and name:
            self._stock_name_cache[code] = name
            self._stock_name_reverse[name.upper()] = code

    def search_stock(self, query: str) -> list[dict]:
        """종목명/코드로 검색. 부분 일치."""
        query = query.strip().upper()
        if not query:
            return []
        results = []
        for code, name in self._stock_name_cache.items():
            if query in name.upper() or query in code:
                results.append({"code": code, "name": name})
        return results[:20]

    def resolve_input(self, text: str) -> str | None:
        """입력이 6자리 코드면 그대로, 종목명이면 코드로 변환. 못 찾으면 None."""
        text = text.strip()
        if len(text) == 6 and text.isdigit():
            return text
        # ETN/ETF 코드 (Q로 시작하는 경우)
        if text.upper().startswith("Q") and len(text) >= 6:
            return text
        # 종목명으로 검색 (정확 일치 우선)
        upper = text.upper()
        if upper in self._stock_name_reverse:
            return self._stock_name_reverse[upper]
        # 부분 일치 (1건만 있으면 반환)
        matches = self.search_stock(text)
        if len(matches) == 1:
            return matches[0]["code"]
        return None

    def add_log(self, level: str, msg: str) -> None:
        entry = {
            "time": now_kst().strftime("%H:%M:%S"),
            "level": level,
            "msg": msg,
        }
        # deque(maxlen=N)이 자동으로 오래된 항목 제거
        if self.params and self.log_messages.maxlen != self.params.infra.dashboard_log_buffer_size:
            from collections import deque
            self.log_messages = deque(self.log_messages, maxlen=self.params.infra.dashboard_log_buffer_size)
        self.log_messages.append(entry)

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
            "manual_codes": self.manual_codes,
            "logs": list(self.log_messages)[-(self.params.infra.dashboard_log_return_size if self.params else 100):],
            "server_time": now_kst().strftime("%H:%M:%S"),
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

_cached_html_template: str = ""

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """메인 페이지 — localhost는 관리자, 외부는 읽기 전용."""
    global _cached_html_template
    html_path = TEMPLATES_DIR / "index.html"
    if not _cached_html_template:
        _cached_html_template = html_path.read_text(encoding="utf-8")
    html = _cached_html_template

    client_ip = request.client.host if request.client else ""
    is_local = client_ip in ("127.0.0.1", "::1", "localhost")

    # 원격 접속 시 ?token= 쿼리파라미터로 관리자 인증
    token_param = request.query_params.get("token", "")
    is_admin = is_local or (ADMIN_TOKEN and hmac.compare_digest(token_param, ADMIN_TOKEN))

    if is_admin:
        html = html.replace("/*__ADMIN__*/", "const IS_ADMIN = true;")
        html = html.replace("/*__TOKEN__*/", f'const ADMIN_TOKEN = "{ADMIN_TOKEN}";')
    else:
        html = html.replace("/*__ADMIN__*/", "const IS_ADMIN = false;")
        html = html.replace("/*__TOKEN__*/", 'const ADMIN_TOKEN = "";')
    return HTMLResponse(html)


@app.get("/api/status")
async def api_status():
    """상태 조회 — 읽기 전용, 토큰 불필요."""
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

        if state.settings.is_dry_run and state.settings.dry_run_cash > 0:
            state.available_cash = state.settings.dry_run_cash
            state.total_eval = state.settings.dry_run_cash
            state.add_log("INFO", f"[DRY_RUN] 가상 예수금: {state.available_cash:,}원")
        else:
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
        screener = Screener(state.api, state.params, is_live=(state.trade_mode == "live"), use_live_data=state.settings.use_live_data)

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


@app.post("/api/set-targets")
async def api_set_targets(request: Request, token: str = Header(None, alias="X-Admin-Token")):
    """수동 타겟 종목 설정 — 종목코드(6자리) 또는 종목명 모두 지원."""
    _check_admin(token)
    try:
        body = await request.json()
        inputs = body.get("codes", [])
        if not inputs or not isinstance(inputs, list):
            return {"ok": False, "msg": "종목코드/종목명 리스트 필요"}

        cleaned = [c.strip() for c in inputs if c.strip()]
        if not cleaned:
            return {"ok": False, "msg": "유효한 입력 없음"}

        # 종목코드/종목명 → 코드 변환 + KIS API 검증
        resolved: list[dict] = []
        errors: list[str] = []

        for raw in cleaned:
            code = state.resolve_input(raw)
            if not code:
                errors.append(f"'{raw}' → 종목 못 찾음")
                continue

            # KIS API로 유효성 검증 + 종목명 확인
            if state.connected and state.api:
                try:
                    info = await state.api.get_current_price(code)
                    price = info.get("current_price", 0)
                    if price <= 0:
                        errors.append(f"'{raw}'({code}) → 유효하지 않은 종목")
                        continue
                    # 종목명: API 응답 → 캐시 → 코드 그대로
                    name = info.get("name", "") or state._stock_name_cache.get(code, "") or code
                    state.cache_stock(code, name)
                    resolved.append({"code": code, "name": name})
                except Exception:
                    errors.append(f"'{raw}'({code}) → KIS API 조회 실패")
            else:
                # API 미연결 시 캐시만 사용
                name = state._stock_name_cache.get(code, "")
                resolved.append({"code": code, "name": name or code})

        if not resolved:
            msg = "유효한 종목 없음"
            if errors:
                msg += " | " + ", ".join(errors)
            return {"ok": False, "msg": msg}

        state.manual_codes = [r["code"] for r in resolved]
        display = ", ".join(f'{r["name"]}({r["code"]})' for r in resolved)
        state.add_log("INFO", f"수동 타겟 종목 설정: {display} ({len(resolved)}종목)")

        result = {"ok": True, "msg": f"{len(resolved)}종목 설정 완료", "codes": state.manual_codes, "stocks": resolved}
        if errors:
            result["errors"] = errors
        return result

    except Exception as e:
        logger.error(f"수동 타겟 설정 실패: {e}")
        return {"ok": False, "msg": "종목 설정 중 오류 발생"}


@app.get("/api/search-stock")
async def api_search_stock(q: str = ""):
    """종목 검색 — 종목명/코드 부분 일치. 캐시 미스 시 KIS API 직접 조회."""
    if not q or len(q.strip()) < 1:
        return {"results": []}

    results = state.search_stock(q)

    # 캐시에 없고, 6자리 숫자 코드이면 KIS API로 직접 조회
    q_stripped = q.strip()
    if not results and len(q_stripped) == 6 and q_stripped.isdigit() and state.connected and state.api:
        try:
            info = await state.api.get_current_price(q_stripped)
            if info.get("current_price", 0) > 0:
                name = info.get("name", "") or q_stripped
                state.cache_stock(q_stripped, name)
                results = [{"code": q_stripped, "name": name}]
        except Exception:
            pass

    return {"results": results}


@app.post("/api/run-manual-screening")
async def api_run_manual_screening(token: str = Header(None, alias="X-Admin-Token")):
    """수동 입력 종목으로 스크리닝 실행."""
    _check_admin(token)
    if not state.connected or not state.api:
        return {"ok": False, "msg": "API 미연결"}

    if not state.manual_codes:
        return {"ok": False, "msg": "수동 타겟 종목 미설정 (/api/set-targets 먼저 호출)"}

    try:
        screener = Screener(state.api, state.params, is_live=(state.trade_mode == "live"), use_live_data=state.settings.use_live_data)

        # loguru 로그를 대시보드 UI에도 표시
        def _log_sink(message):
            record = message.record
            state.add_log(record["level"].name, record["message"])

        sink_id = logger.add(_log_sink, level="DEBUG", format="{message}")
        try:
            targets = await screener.run_manual(state.manual_codes)
        finally:
            logger.remove(sink_id)

        state.monitors = []
        for target in targets:
            mon = TargetMonitor(target, state.params)
            state.monitors.append(mon)

        state.add_log("INFO", f"수동 스크리닝 완료: {len(targets)}종목 선정")
        for t in targets:
            state.add_log("INFO", f"  타겟: {t.stock.name}({t.stock.code}) 등락{t.stock.price_change_pct:+.2f}%")

        return {"ok": True, "msg": f"{len(targets)}종목 선정"}

    except Exception as e:
        logger.error(f"수동 스크리닝 실패: {e}")
        state.add_log("ERROR", "수동 스크리닝 실패")
        return {"ok": False, "msg": "수동 스크리닝 중 오류 발생"}


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
    """WebSocket — 읽기 전용 상태 브로드캐스트. 제어 명령 불가.
    토큰 인증: ws://host/ws?token=xxx (관리자) 또는 미인증(읽기 전용).
    """
    token_param = ws.query_params.get("token", "")
    is_admin = not ADMIN_TOKEN or hmac.compare_digest(token_param, ADMIN_TOKEN)
    await ws.accept()
    state._ws_clients.append(ws)
    try:
        while True:
            data = state.to_dict()
            if not is_admin:
                # 비인증 클라이언트에는 로그/예수금 제외
                data.pop("logs", None)
                data.pop("available_cash", None)
            await ws.send_json(data)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if ws in state._ws_clients:
            state._ws_clients.remove(ws)


# ── 모니터 종목 실시간 가격 갱신 ────────────────────────────────

async def _price_updater():
    """모니터 종목의 현재가를 주기적으로 KIS REST API로 갱신."""
    while True:
        try:
            if state.connected and state.api and state.monitors:
                for mon in state.monitors:
                    try:
                        price_info = await state.api.get_current_price(mon.target.stock.code)
                        mon.target.stock.current_price = price_info.get("current_price", mon.target.stock.current_price)
                        mon.target.stock.price_change_pct = price_info.get("change_pct", mon.target.stock.price_change_pct)
                        high = price_info.get("high", 0)
                        if high > mon.target.intraday_high:
                            mon.target.intraday_high = high
                    except Exception as e:
                        logger.debug(f"가격 갱신 실패 ({mon.target.stock.code}): {e}")
        except Exception as e:
            logger.debug(f"가격 업데이터 오류: {e}")
        await asyncio.sleep(3)  # 3초 간격 갱신


# ── 실행 ──────────────────────────────────────────────────────

@app.on_event("startup")
async def _auto_connect():
    """서버 시작 시 KIS API 자동 연결 — 대시보드 단독 실행 시에도 데이터 표시."""
    try:
        state.settings = Settings()
        state.params = StrategyParams.load()
        state.trade_mode = state.settings.trade_mode

        state.api = KISAPI(
            app_key=state.settings.kis_app_key,
            app_secret=state.settings.kis_app_secret,
            account_no=state.settings.account_no,
            is_paper=state.settings.is_paper_mode,
            infra_params=state.params.infra,
        )
        await state.api.connect()
        state.connected = True

        if state.settings.is_dry_run and state.settings.dry_run_cash > 0:
            state.available_cash = state.settings.dry_run_cash
            state.total_eval = state.settings.dry_run_cash
            state.add_log("INFO", f"[DRY_RUN] 가상 예수금: {state.available_cash:,}원")
        else:
            balance = await state.api.get_balance()
            state.available_cash = balance.get("available_cash", 0)
            state.total_eval = balance.get("total_eval", 0)

        state.risk = RiskManager(state.params)
        state.trader = Trader(state.api, state.settings, state.params)

        state.add_log("INFO", f"KIS 자동 연결 완료 ({state.trade_mode})")
        state.add_log("INFO", f"예수금: {state.available_cash:,}원")
        logger.info(f"대시보드 자동 연결 완료 (예수금: {state.available_cash:,}원)")

        # 종목명 캐시 구축
        try:
            # 1) stock_master.json (전체 종목 ~2800건, 150KB)
            master_path = Path(__file__).resolve().parents[2] / "config" / "stock_master.json"
            if master_path.exists():
                import json as _json
                master = _json.loads(master_path.read_text(encoding="utf-8"))
                for code, name in master.items():
                    state.cache_stock(code, name)
                logger.info(f"종목 마스터 로드: {len(master)}건")

            # 2) KIS 거래량순위 (최신 종목명 반영)
            for mkt in ["J", "Q"]:
                ranks = await state.api.get_volume_rank(market=mkt)
                for r in ranks:
                    state.cache_stock(r.get("code", ""), r.get("name", ""))

            logger.info(f"종목명 캐시 총: {len(state._stock_name_cache)}건")
        except Exception as e:
            logger.warning(f"종목명 캐시 구축 실패: {e}")
    except Exception as e:
        logger.warning(f"대시보드 자동 연결 실패 (수동 연결 필요): {e}")
        state.add_log("WARNING", "자동 연결 실패 — 수동 연결 필요")

    # 모니터 종목 실시간 가격 갱신 태스크 시작
    asyncio.create_task(_price_updater())


def run_dashboard(host: str = "0.0.0.0", port: int = 0):
    import uvicorn
    if port == 0:
        port = int(os.getenv("DASHBOARD_PORT", "8503"))
    logger.info(f"대시보드 시작: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run_dashboard()
