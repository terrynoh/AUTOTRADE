"""
AUTOTRADE 웹 대시보드 — FastAPI + WebSocket 실시간 모니터링.

실행:
    python -m src.dashboard.app
    → http://localhost:8501
"""
from __future__ import annotations

import asyncio
import hmac
import os
import sqlite3
from pathlib import Path

from src.utils.market_calendar import now_kst

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from src.core.watcher import Watcher

ADMIN_TOKEN = os.getenv("DASHBOARD_ADMIN_TOKEN", "")
DB_PATH = Path(__file__).resolve().parents[2] / "data" / "trades.db"

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

# CORS 미들웨어 — localhost + Cloudflare Tunnel + hwrim.trade 허용
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://hwrim\.trade|https://.*\.trycloudflare\.com|https://.*\.cloudflareaccess\.com|http://localhost:\d+|http://127\.0\.0\.1:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,  # 쿠키 허용 (CF_Authorization)
)


def _check_admin(token: str = Header(None, alias="X-Admin-Token")) -> None:
    """관리자 토큰 검증 (timing-safe). 토큰 미설정 시 제한 없음."""
    if ADMIN_TOKEN and not hmac.compare_digest(token or "", ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="관리자 권한 필요")

# ── 글로벌 상태 ──────────────────────────────────────────────

class DashboardState:
    """대시보드에서 공유하는 전역 상태."""

    def __init__(self):
        self.autotrader = None  # AutoTrader 인스턴스 (attach_autotrader로 설정)
        self.connected: bool = False
        self.trade_mode: str = ""
        self.available_cash: int = 0
        self.monthly_pnl: int = 0
        self.monthly_trades: int = 0
        self.monthly_days: int = 0

        self.monitors: list[Watcher] = []

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
        self.log_messages.append(entry)

    def to_dict(self) -> dict:
        at = self.autotrader

        # AutoTrader 미연결 시 빈 데이터 반환
        if not at:
            return {
                "connected": False,
                "trade_mode": "",
                "available_cash": 0,
                "daily_pnl": 0,
                "daily_trades": 0,
                "monthly_pnl": 0,
                "monthly_trades": 0,
                "monthly_days": 0,
                "monitors": [],
                "manual_codes": [],
                "logs": list(self.log_messages)[-100:],
                "server_time": now_kst().strftime("%H:%M:%S"),
            }

        params = at.params
        monitors_data = []
        for watcher in self.monitors:
            monitors_data.append({
                "code": watcher.code,
                "name": watcher.name,
                "market": watcher.market.value,
                "current_price": watcher.current_price,
                "change_pct": 0,
                "intraday_high": watcher.intraday_high,
                "new_high_achieved": watcher.new_high_achieved,
                "high_confirmed": watcher.high_confirmed_at is not None,
                "state": watcher.state.value,
                "buy1_placed": watcher.buy1_placed,
                "buy2_placed": watcher.buy2_placed,
                "buy1_filled": watcher.buy1_filled,
                "buy2_filled": watcher.buy2_filled,
                "avg_price": round(watcher.total_buy_amount / watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0),
                "total_qty": watcher.total_buy_qty,
                "target_price": round(watcher.target_price),
                "post_entry_low": watcher.post_entry_low,
                "exit_reason": watcher.exit_reason,
                "buy1_price": watcher.target_buy1_price,
                "buy2_price": watcher.target_buy2_price,
                "hard_stop_price": watcher.hard_stop_price_value,
                "pnl": round(at.trader.get_pnl(watcher.current_price)),
                "channel_used": watcher.channel_used,  # R-17
                "channel_decided_at": watcher.channel_decided_at.isoformat() if watcher.channel_decided_at else None,
                "un_push_count": watcher.un_push_count_at_decision,
                "st_push_count": watcher.st_push_count_at_decision,
            })

        log_size = params.infra.dashboard_log_return_size
        return {
            "connected": self.connected,
            "trade_mode": self.trade_mode,
            "available_cash": self.available_cash,
            "daily_pnl": round(at.risk.daily_pnl),
            "daily_trades": at.risk.daily_trades,
            "monthly_pnl": self.monthly_pnl,
            "monthly_trades": self.monthly_trades,
            "monthly_days": self.monthly_days,
            "monitors": monitors_data,
            "manual_codes": list(at._manual_codes),
            "logs": list(self.log_messages)[-log_size:],
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

    # 관리자 인증: Cloudflare Access 헤더 우선, fallback ?token= 쿼리파라미터
    cf_email = request.headers.get("CF-Access-Authenticated-User-Email", "")
    token_param = request.query_params.get("token", "")
    is_admin = is_local or bool(cf_email) or (ADMIN_TOKEN and hmac.compare_digest(token_param, ADMIN_TOKEN))

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



@app.post("/api/set-targets")
async def api_set_targets(request: Request, token: str = Header(None, alias="X-Admin-Token")):
    """수동 타겟 종목 설정 — 종목코드(6자리) 또는 종목명 모두 지원."""
    _check_admin(token)
    if not state.autotrader:
        return {"ok": False, "msg": "AutoTrader 미연결"}

    try:
        body = await request.json()
        inputs = body.get("codes", [])
        if not inputs or not isinstance(inputs, list):
            return {"ok": False, "msg": "종목코드/종목명 리스트 필요"}

        cleaned = [c.strip() for c in inputs if c.strip()]
        if not cleaned:
            return {"ok": False, "msg": "유효한 입력 없음"}

        # StockMaster 로컬 캐시로 종목 검증 (KIS API 호출 X)
        sm = state.autotrader._stock_master
        resolved: list[dict] = []
        errors: list[str] = []

        for raw in cleaned:
            if raw.isdigit():
                # 종목코드 입력
                name = sm.lookup_name(raw)
                if name is None:
                    errors.append(f"'{raw}' → 종목 못 찾음")
                    continue
                state.cache_stock(raw, name)
                resolved.append({"code": raw, "name": name})
            else:
                # 종목명 입력 → 코드 변환
                code = sm.lookup_code(raw)
                if code is None:
                    errors.append(f"'{raw}' → 종목 못 찾음")
                    continue
                name = sm.lookup_name(code) or raw
                state.cache_stock(code, name)
                resolved.append({"code": code, "name": name})

        if not resolved:
            msg = "유효한 종목 없음"
            if errors:
                msg += " | " + ", ".join(errors)
            return {"ok": False, "msg": msg}

        # AutoTrader에 최종 검증된 종목코드 일괄 설정
        final_codes = [r["code"] for r in resolved]
        state.autotrader.set_manual_codes(final_codes)

        display = ", ".join(f'{r["name"]}({r["code"]})' for r in resolved)
        state.add_log("INFO", f"수동 타겟 종목 설정: {display} ({len(resolved)}종목)")

        result = {"ok": True, "msg": f"{len(resolved)}종목 설정 완료", "codes": final_codes, "stocks": resolved}
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
    if not results and len(q_stripped) == 6 and q_stripped.isdigit() and state.autotrader and state.autotrader.api:
        try:
            info = await state.autotrader.api.get_current_price(q_stripped)
            if info.get("current_price", 0) > 0:
                name = info.get("name", "") or q_stripped
                state.cache_stock(q_stripped, name)
                results = [{"code": q_stripped, "name": name}]
        except Exception:
            pass

    return {"results": results}


@app.get("/api/trades/recent")
async def api_trades_recent(since_id: int = 0, date: str = ""):
    """청산 거래 조회 — 로컬 sync용. 토큰 불필요.

    Args:
        since_id: 이 id 초과 건만 반환 (전역 autoincrement id).
        date: YYYY-MM-DD 필터 (생략 시 전체).
    """
    if not DB_PATH.exists():
        return {"ok": False, "msg": "DB 없음", "trades": []}

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        if date:
            rows = conn.execute(
                "SELECT * FROM trades_r10 WHERE id > ? AND trade_date = ? "
                "AND exit_reason NOT IN ('NO_ENTRY') ORDER BY id",
                (since_id, date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades_r10 WHERE id > ? "
                "AND exit_reason NOT IN ('NO_ENTRY') ORDER BY id",
                (since_id,),
            ).fetchall()
        conn.close()

        trades = [dict(row) for row in rows]
        return {"ok": True, "count": len(trades), "trades": trades}

    except Exception as e:
        logger.error(f"/api/trades/recent 오류: {e}")
        return {"ok": False, "msg": str(e), "trades": []}


@app.post("/api/run-manual-screening")
async def api_run_manual_screening(token: str = Header(None, alias="X-Admin-Token")):
    """수동 입력 종목으로 스크리닝 실행 — AutoTrader에 위임."""
    _check_admin(token)
    if not state.autotrader:
        return {"ok": False, "msg": "AutoTrader 미연결"}

    if not state.autotrader._manual_codes:
        return {"ok": False, "msg": "수동 타겟 종목 미설정 (/api/set-targets 먼저 호출)"}

    autotrader = state.autotrader

    # AutoTrader 의 loop 가 살아있는지 확인
    if autotrader._loop is None or not autotrader._loop.is_running():
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"ok": False, "error": "AutoTrader loop 가 가동 중이 아님"},
            status_code=503
        )

    # AutoTrader loop 에 위임 (KIS API 의 aiohttp 컨텍스트 정상)
    future = asyncio.run_coroutine_threadsafe(
        autotrader._on_screening(),
        autotrader._loop
    )

    # 결과 대기 (max 60초)
    try:
        await asyncio.wrap_future(future)
    except asyncio.TimeoutError:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"ok": False, "error": "수동 스크리닝 timeout (60초)"},
            status_code=504
        )
    except Exception as e:
        logger.error(f"수동 스크리닝 실패: {e}")
        state.add_log("ERROR", "수동 스크리닝 실패")
        return {"ok": False, "msg": "수동 스크리닝 중 오류 발생"}

    # R-07: _monitors → _coordinator.watchers
    count = len(autotrader._coordinator.watchers)
    state.add_log("INFO", f"수동 스크리닝 완료: {count}종목 선정")
    return {"ok": True, "msg": f"{count}종목 선정"}




# ── WebSocket (실시간 업데이트) ───────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket — 실시간 상태 브로드캐스트.
    인증: Cloudflare Access 헤더 우선, fallback ?token= 쿼리파라미터.
    """
    # Cloudflare Access 헤더 체크 (WebSocket 업그레이드 시에도 전달됨)
    cf_email = ws.headers.get("cf-access-authenticated-user-email", "")
    token_param = ws.query_params.get("token", "")
    
    # 관리자 인증: CF Access 헤더 있거나, 토큰 일치하거나, 토큰 미설정
    is_admin = bool(cf_email) or (not ADMIN_TOKEN) or hmac.compare_digest(token_param, ADMIN_TOKEN)
    await ws.accept()
    state._ws_clients.append(ws)
    try:
        while True:
            data = state.to_dict()
            if not is_admin:
                # 비인증 클라이언트에는 로그/예수금/월간 P&L 제외
                data.pop("logs", None)
                data.pop("available_cash", None)
                data.pop("monthly_pnl", None)
                data.pop("monthly_trades", None)
                data.pop("monthly_days", None)
            await ws.send_json(data)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if ws in state._ws_clients:
            state._ws_clients.remove(ws)



# ── 실행 ──────────────────────────────────────────────────────


# ── AutoTrader 연결 ──────────────────────────────────────────

def attach_autotrader(autotrader) -> None:
    """AutoTrader 인스턴스를 대시보드에 연결 (순수 sync)."""
    from collections import deque
    state.autotrader = autotrader
    autotrader.on_state_update = _sync_from_autotrader
    # deque maxlen을 설정값으로 1회 재생성 (기존 로그 보존)
    buf_size = autotrader.params.infra.dashboard_log_buffer_size
    if state.log_messages.maxlen != buf_size:
        state.log_messages = deque(state.log_messages, maxlen=buf_size)


async def _sync_from_autotrader() -> None:
    """AutoTrader → DashboardState 상태 동기화 + WS broadcast."""
    at = state.autotrader
    if at is None:
        return

    state.connected = True
    state.trade_mode = at.settings.trade_mode
    state.monitors = list(at._coordinator.watchers)

    # 예수금
    state.available_cash = at._available_cash

    # 월간 누적 P&L (calendar month, 매 broadcast 마다 SQL 재집계)
    try:
        _now = now_kst()
        _m = at._trade_logger.get_monthly_summary(_now.year, _now.month)
        state.monthly_pnl = _m["total_pnl"]
        state.monthly_trades = _m["trade_count"]
        state.monthly_days = _m["day_count"]
    except Exception as _e:
        logger.debug(f"월간 누적 P&L 조회 실패: {_e}")

    # broadcast to WebSocket clients
    try:
        await state.broadcast(state.to_dict())
    except Exception as e:
        logger.debug(f"WS broadcast 실패: {e}")


def run_dashboard(host: str = "0.0.0.0", port: int = 0):
    import uvicorn
    if port == 0:
        port = int(os.getenv("DASHBOARD_PORT", "8503"))
    logger.info(f"대시보드 시작: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run_dashboard()
