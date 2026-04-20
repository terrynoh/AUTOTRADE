#!/usr/bin/env python3
"""
로컬 trades.db → logs/trades/YYYY-MM-DD/*.json 추출.
sync_logs.bat에서 호출됨. HTTP/Cloudflare 불필요.
"""
import sqlite3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "trades.db"
TRADES_DIR = ROOT / "logs" / "trades"


def extract():
    if not DB.exists():
        print(f"[오류] DB 없음: {DB}")
        sys.exit(1)

    TRADES_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    total = 0

    # ── trades_r10 (R-10 이후 신규 포맷) ─────────────────────────────
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades_r10'")
    if cur.fetchone():
        cur.execute("SELECT * FROM trades_r10 ORDER BY trade_date, id")
        by_date = {}
        for r in cur.fetchall():
            by_date.setdefault(r["trade_date"], []).append(dict(r))

        for date, trades in by_date.items():
            d = TRADES_DIR / date
            d.mkdir(parents=True, exist_ok=True)
            for i, t in enumerate(trades, 1):
                name = (t.get("name") or "UNKNOWN").replace(" ", "_")
                reason = (t.get("exit_reason") or "NONE").upper()
                fname = f"{i:03d}_{name}_{reason}.json"
                (d / fname).write_text(
                    json.dumps(t, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                print(f"  [R10] {date}/{fname}")
                total += 1

    # ── trades (구 포맷 → legacy/) ────────────────────────────────────
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
    if cur.fetchone():
        cur.execute("SELECT * FROM trades ORDER BY trade_date, id")
        by_date_old = {}
        for r in cur.fetchall():
            by_date_old.setdefault(r["trade_date"], []).append(dict(r))

        for date, trades in by_date_old.items():
            d = TRADES_DIR / date / "legacy"
            d.mkdir(parents=True, exist_ok=True)
            for i, t in enumerate(trades, 1):
                name = (t.get("name") or "UNKNOWN").replace(" ", "_")
                reason = (t.get("exit_reason") or "NONE").upper()
                fname = f"{i:03d}_{name}_{reason}.json"
                (d / fname).write_text(
                    json.dumps(t, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                print(f"  [OLD] {date}/legacy/{fname}")
                total += 1

    # ── daily_summary_r10 ─────────────────────────────────────────────
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_summary_r10'")
    if cur.fetchone():
        cur.execute("SELECT * FROM daily_summary_r10 ORDER BY summary_date")
        for r in cur.fetchall():
            row = dict(r)
            date = row["summary_date"]
            d = TRADES_DIR / date
            d.mkdir(parents=True, exist_ok=True)
            path = d / "daily_summary.json"
            path.write_text(
                json.dumps(row, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"  [SUM] {date}/daily_summary.json")

    conn.close()
    print(f"\n완료: 총 {total}건 → {TRADES_DIR}")


if __name__ == "__main__":
    extract()
