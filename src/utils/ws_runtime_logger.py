"""
W-31 — WebSocket H0UNCNT0 런타임 tick 로거 (임시, 검증 종료 후 삭제).

목적: KIS WebSocket tick 의 레이어별 수신/라우팅/Watcher 도달 추적.
원칙:
- fire-and-forget. 어떤 예외도 매매 로직에 0영향 (모든 호출부 + 내부 try/except 이중 격리).
- 주 logger (loguru) 와 완전 분리. main log 오염 방지.
- thread-safe (WS handler + asyncio event loop + REST polling task 동시 접근).
- 검증 종료 후: 본 파일 + sink 디렉터리 + 호출 블록 모두 철거.

sink 경로: <project_root>/logs/ws_runtime/ws_tick_YYYY-MM-DD.jsonl
listing_scope: <project_root>/config/krx_nxt_listing.json (1회 로드, in-memory 캐시)

W-31 문서: Obsidian/W-31_WebSocket_런타임로깅_검증.md §4.3/§4.6
"""
from __future__ import annotations

import itertools
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── 내부 상태 ────────────────────────────────────────────────
_KST = timezone(timedelta(hours=9))   # 로컬 시스템 tz 무관, 항상 KST 기록 (개발=Bangkok, 운영=GCP Seoul)
_LOCK = threading.Lock()
_SEQ = itertools.count(1)
_SINK_DIR = Path(__file__).parent.parent.parent / "logs" / "ws_runtime"

_SCOPE_CACHE: dict[str, str] | None = None
_SCOPE_CACHE_LOCK = threading.Lock()


# ── listing_scope 판정 ──────────────────────────────────────
def _load_scope_cache() -> dict[str, str]:
    """config/krx_nxt_listing.json 1회 로드. 파일 없거나 형식 오류 시 빈 dict."""
    global _SCOPE_CACHE
    if _SCOPE_CACHE is not None:
        return _SCOPE_CACHE
    with _SCOPE_CACHE_LOCK:
        if _SCOPE_CACHE is not None:
            return _SCOPE_CACHE
        path = Path(__file__).parent.parent.parent / "config" / "krx_nxt_listing.json"
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            _SCOPE_CACHE = data.get("scope", {}) or {}
        except Exception:
            _SCOPE_CACHE = {}
        return _SCOPE_CACHE


def get_listing_scope(code: str) -> str:
    """W-31 listing_scope 판정. 미등재 코드는 'unknown'."""
    try:
        return _load_scope_cache().get(code, "unknown")
    except Exception:
        return "unknown"


# ── JSONL 쓰기 ──────────────────────────────────────────────
def log_event(record: dict[str, Any]) -> None:
    """fire-and-forget JSONL 기록.

    어떤 예외도 삼켜 매매 로직에 0영향.
    """
    try:
        _SINK_DIR.mkdir(parents=True, exist_ok=True)
        record["seq_local"] = next(_SEQ)
        _now_kst = datetime.now(_KST)
        record.setdefault("ts", _now_kst.isoformat(timespec="milliseconds"))
        date_str = _now_kst.strftime("%Y-%m-%d")
        path = _SINK_DIR / f"ws_tick_{date_str}.jsonl"
        with _LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
