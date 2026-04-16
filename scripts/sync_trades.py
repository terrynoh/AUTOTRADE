"""
로컬 거래 기록 동기화 스크립트.

운영 서버의 /api/trades/recent 를 호출해 신규 청산 건을 로컬에 저장한다.

저장 구조:
    logs/trades/
    ├── .sync_state.json          ← 마지막 동기화 since_id 기록
    ├── 2026-04-16/
    │   ├── 001_삼성전자_TARGET.json
    │   ├── 002_SKC_HARD_STOP.json
    │   └── daily_summary.json
    └── 2026-04-17/
        └── ...

실행:
    python scripts/sync_trades.py
    python scripts/sync_trades.py --date 2026-04-16   # 특정 날짜만
    python scripts/sync_trades.py --since-id 0        # 전체 재동기화
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# 프로젝트 루트
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").rstrip("/")
TRADES_DIR = ROOT / "logs" / "trades"
STATE_FILE = TRADES_DIR / ".sync_state.json"


# ── 헬퍼 ──────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"since_id": 0}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _date_dir(trade_date: str) -> Path:
    d = TRADES_DIR / trade_date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seq_no(trade_date: str) -> int:
    """해당 날짜 폴더의 현재 거래 파일 수 + 1."""
    d = TRADES_DIR / trade_date
    if not d.exists():
        return 1
    existing = [f for f in d.iterdir() if f.name != "daily_summary.json" and f.suffix == ".json"]
    return len(existing) + 1


def _save_trade(trade: dict) -> Path:
    trade_date = trade.get("trade_date", str(date.today()))
    name = trade.get("name", "unknown").replace(" ", "_")
    reason = trade.get("exit_reason", "UNKNOWN")
    seq = _seq_no(trade_date)

    filename = f"{seq:03d}_{name}_{reason}.json"
    filepath = _date_dir(trade_date) / filename

    trade["_synced_at"] = datetime.now().isoformat()
    filepath.write_text(json.dumps(trade, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return filepath


def _update_summary(trade_date: str) -> None:
    """해당 날짜의 daily_summary.json 재집계."""
    d = TRADES_DIR / trade_date
    if not d.exists():
        return

    files = sorted(f for f in d.iterdir() if f.name != "daily_summary.json" and f.suffix == ".json")
    trades = [json.loads(f.read_text(encoding="utf-8")) for f in files]

    total_pnl = sum(int(t.get("pnl") or 0) for t in trades)
    capital = trades[0].get("capital", 50_000_000) if trades else 50_000_000
    summary = {
        "summary_date": trade_date,
        "total_trades": len(trades),
        "success_count": sum(1 for t in trades if t.get("exit_reason") == "TARGET"),
        "hard_stop_count": sum(1 for t in trades if t.get("exit_reason") == "HARD_STOP"),
        "timeout_count": sum(1 for t in trades if t.get("exit_reason") == "TIMEOUT"),
        "futures_stop_count": sum(1 for t in trades if t.get("exit_reason") == "FUTURES_STOP"),
        "force_count": sum(1 for t in trades if t.get("exit_reason") == "FORCE_LIQUIDATE"),
        "total_pnl": total_pnl,
        "capital_pnl_pct": round(total_pnl / capital * 100, 2) if capital else 0,
        "capital": capital,
        "updated_at": datetime.now().isoformat(),
    }
    summary_path = d / "daily_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 메인 ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="거래 기록 로컬 동기화")
    parser.add_argument("--date", default="", help="특정 날짜만 동기화 (YYYY-MM-DD)")
    parser.add_argument("--since-id", type=int, default=None, help="since_id 직접 지정 (0이면 전체)")
    args = parser.parse_args()

    if not DASHBOARD_URL:
        print("ERROR: .env 에 DASHBOARD_URL 미설정 (예: DASHBOARD_URL=https://xxx.trycloudflare.com)")
        sys.exit(1)

    TRADES_DIR.mkdir(parents=True, exist_ok=True)

    state = _load_state()
    since_id = args.since_id if args.since_id is not None else state["since_id"]
    target_date = args.date

    url = f"{DASHBOARD_URL}/api/trades/recent"
    params: dict = {"since_id": since_id}
    if target_date:
        params["date"] = target_date

    print(f"동기화 시작: {url} (since_id={since_id}" + (f", date={target_date}" if target_date else "") + ")")

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ERROR: API 호출 실패 — {e}")
        sys.exit(1)

    if not data.get("ok"):
        print(f"ERROR: 서버 응답 오류 — {data.get('msg')}")
        sys.exit(1)

    trades = data.get("trades", [])
    if not trades:
        print("신규 거래 없음.")
        return

    print(f"신규 거래 {len(trades)}건 발견")
    affected_dates: set[str] = set()

    for trade in trades:
        path = _save_trade(trade)
        trade_date = trade.get("trade_date", "")
        affected_dates.add(trade_date)
        name = trade.get("name", "?")
        reason = trade.get("exit_reason", "?")
        pnl = int(trade.get("pnl") or 0)
        print(f"  저장: {path.name}  [{name}] {reason} pnl={pnl:+,}원")

    # daily_summary 갱신
    for trade_date in affected_dates:
        _update_summary(trade_date)
        print(f"  요약 갱신: {trade_date}/daily_summary.json")

    # since_id 업데이트 (최대 id)
    max_id = max(int(t.get("id") or 0) for t in trades)
    state["since_id"] = max_id
    state["last_synced_at"] = datetime.now().isoformat()
    _save_state(state)

    print(f"\n동기화 완료. since_id → {max_id}")


if __name__ == "__main__":
    main()
