"""
오늘 실거래 데이터 → R-10 포맷 텔레그램 발송 테스트.

data/trades.db (구 스키마 또는 신 스키마 자동 감지)에서
오늘 날짜 거래를 읽어 텔레그램으로 전송한다.

실행:
    cd /home/ubuntu/AUTOTRADE
    source venv/bin/activate
    python3 scripts/test_telegram_report.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.models.trade import TradeRecord, ExitReason
from src.storage.trade_logger import DailySummaryR10
from src.utils.notifier import Notifier

KST = timezone(timedelta(hours=9))
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trades.db"
CAPITAL = int(os.getenv("DRY_RUN_CASH", "50000000"))


# ── 헬퍼 ──────────────────────────────────────────────────

def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def _map_exit_reason(s: str) -> ExitReason:
    return {
        "TARGET":          ExitReason.TARGET,
        "HARD_STOP":       ExitReason.HARD_STOP,
        "TIMEOUT":         ExitReason.TIMEOUT,
        "FUTURES_STOP":    ExitReason.FUTURES_STOP,
        "FORCE_LIQUIDATE": ExitReason.FORCE_LIQUIDATE,
        "MANUAL":          ExitReason.MANUAL,
        # 구 스키마 값
        "target":          ExitReason.TARGET,
        "hard_stop":       ExitReason.HARD_STOP,
        "timeout":         ExitReason.TIMEOUT,
        "futures_stop":    ExitReason.FUTURES_STOP,
        "force":           ExitReason.FORCE_LIQUIDATE,
    }.get(s or "", ExitReason.NO_ENTRY)


def _detect_schema(conn: sqlite3.Connection) -> str:
    """'r10' (trades_r10 테이블), 'new' (trade_id 있음), 'old'."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "trades_r10" in tables:
        return "r10"
    cols = [c[1] for c in conn.execute("PRAGMA table_info(trades)").fetchall()]
    return "new" if "trade_id" in cols else "old"


def row_to_record_old(row: sqlite3.Row, today: date) -> TradeRecord:
    """구 스키마 → TradeRecord."""
    d = dict(row)
    holding_min = d.get("holding_minutes") or 0.0
    holding_sec = int(holding_min * 60)
    pnl = d.get("pnl") or 0.0
    capital_pnl_pct = round(pnl / CAPITAL * 100, 2)

    return TradeRecord(
        trade_date=today,
        code=d.get("code", ""),
        name=d.get("name", ""),
        market=d.get("market", ""),
        avg_buy_price=d.get("avg_buy_price") or 0.0,
        total_buy_qty=d.get("total_buy_qty") or 0,
        total_buy_amount=d.get("total_buy_amount") or 0,
        avg_sell_price=d.get("avg_sell_price") or 0.0,
        total_sell_amount=d.get("total_sell_amount") or 0,
        sell_time=_parse_dt(d.get("sell_time")),
        exit_reason=_map_exit_reason(d.get("exit_reason", "")),
        pnl=pnl,
        pnl_pct=d.get("pnl_pct") or 0.0,
        capital_pnl_pct=capital_pnl_pct,
        holding_minutes=holding_min,
        holding_seconds=holding_sec,
        capital=CAPITAL,
        trade_mode=d.get("trade_mode", "dry_run"),
    )


def row_to_record_new(row: sqlite3.Row, today: date) -> TradeRecord:
    """신 스키마 (R-10 trade_logger, trades 테이블) → TradeRecord."""
    d = dict(row)
    return TradeRecord(
        trade_date=today,
        code=d.get("code", ""),
        name=d.get("name", ""),
        market=d.get("market", ""),
        new_high_price=d.get("new_high_price") or 0,
        new_high_time=_parse_dt(d.get("new_high_time")),
        avg_buy_price=d.get("entry_price") or 0.0,
        total_buy_qty=d.get("entry_qty") or 0,
        total_buy_amount=d.get("entry_amount") or 0,
        avg_sell_price=d.get("exit_price") or 0.0,
        exit_reason=_map_exit_reason(d.get("exit_reason", "")),
        pnl=d.get("pnl") or 0.0,
        pnl_pct=d.get("pnl_pct") or 0.0,
        capital_pnl_pct=d.get("capital_pnl_pct") or 0.0,
        holding_seconds=d.get("holding_seconds") or 0,
        capital=d.get("capital") or CAPITAL,
        trade_mode=d.get("trade_mode", "dry_run"),
    )


def row_to_record_r10(row: sqlite3.Row, today: date) -> TradeRecord:
    """trades_r10 스키마 → TradeRecord (buy1/2 상세 포함)."""
    d = dict(row)
    entry_price = d.get("entry_price") or 0.0
    entry_qty = d.get("entry_qty") or 0
    entry_amount = d.get("entry_amount") or 0
    if entry_amount == 0 and entry_price > 0 and entry_qty > 0:
        entry_amount = int(entry_price) * entry_qty
    return TradeRecord(
        trade_date=today,
        code=d.get("code", ""),
        name=d.get("name", ""),
        market=d.get("market", ""),
        new_high_price=d.get("new_high_price") or 0,
        new_high_time=_parse_dt(d.get("new_high_time")),
        buy1_price=d.get("buy1_price") or 0,
        buy1_qty=d.get("buy1_qty") or 0,
        buy1_time=_parse_dt(d.get("buy1_time")),
        buy2_price=d.get("buy2_price") or 0,
        buy2_qty=d.get("buy2_qty") or 0,
        buy2_time=_parse_dt(d.get("buy2_time")),
        avg_buy_price=float(entry_price),
        total_buy_qty=entry_qty,
        total_buy_amount=entry_amount,
        avg_sell_price=float(d.get("exit_price") or 0.0),
        sell_time=_parse_dt(d.get("exit_time")),
        exit_reason=_map_exit_reason(d.get("exit_reason", "")),
        pnl=d.get("pnl") or 0.0,
        pnl_pct=d.get("pnl_pct") or 0.0,
        capital_pnl_pct=d.get("capital_pnl_pct") or 0.0,
        holding_seconds=d.get("holding_seconds") or 0,
        entry_trigger_price=d.get("target_buy1_price") or 0,
        target_buy2_price=d.get("target_buy2_price") or 0,
        hard_stop_price=d.get("hard_stop_price") or 0,
        capital=d.get("capital") or CAPITAL,
        trade_mode=d.get("trade_mode", "dry_run"),
    )


# ── 메인 ──────────────────────────────────────────────────

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("ERROR: .env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정")
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"ERROR: DB 없음 - {DB_PATH}")
        sys.exit(1)

    notifier = Notifier(bot_token=token, chat_id=chat_id)
    today = date.today()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    schema = _detect_schema(conn)
    print(f"DB 스키마: {schema} / 경로: {DB_PATH}")

    effective_schema = schema
    if schema == "r10":
        rows = conn.execute(
            "SELECT * FROM trades_r10 WHERE trade_date = ? AND exit_reason != 'NO_ENTRY' ORDER BY id",
            (str(today),)
        ).fetchall()
        # trades_r10 데이터 없으면 구 trades 테이블 fallback
        if not rows:
            print("trades_r10 데이터 없음 - 구 trades 테이블로 fallback")
            rows = conn.execute(
                "SELECT * FROM trades WHERE trade_date = ? AND exit_reason != 'NO_ENTRY' ORDER BY rowid",
                (str(today),)
            ).fetchall()
            effective_schema = "old"
    else:
        rows = conn.execute(
            "SELECT * FROM trades WHERE trade_date = ? AND exit_reason != 'NO_ENTRY' ORDER BY rowid",
            (str(today),)
        ).fetchall()
    conn.close()

    if not rows:
        print(f"오늘({today}) 거래 데이터 없음 - DB를 확인하세요")
        sys.exit(0)

    print(f"오늘 거래 {len(rows)}건 발견 (스키마: {effective_schema})\n")

    records: list[TradeRecord] = []
    for row in rows:
        if effective_schema == "r10":
            rec = row_to_record_r10(row, today)
        elif effective_schema == "new":
            rec = row_to_record_new(row, today)
        else:
            rec = row_to_record_old(row, today)
        records.append(rec)

        name = rec.name or dict(row).get("name", "?")
        print(f"  전송: [{name}] {rec.exit_reason.value}  pnl={int(rec.pnl):+,}원")
        notifier.notify_trade_complete(rec)

    # 일일 요약
    total_pnl = sum(int(r.pnl) for r in records)
    success = sum(1 for r in records if r.exit_reason == ExitReason.TARGET)
    fail    = sum(1 for r in records if r.exit_reason != ExitReason.TARGET)
    hs_cnt  = sum(1 for r in records if r.exit_reason == ExitReason.HARD_STOP)
    to_cnt  = sum(1 for r in records if r.exit_reason == ExitReason.TIMEOUT)
    fs_cnt  = sum(1 for r in records if r.exit_reason == ExitReason.FUTURES_STOP)
    fc_cnt  = sum(1 for r in records if r.exit_reason == ExitReason.FORCE_LIQUIDATE)
    mode    = records[0].trade_mode if records else "dry_run"

    summary = DailySummaryR10(
        summary_date=today,
        total_trades=len(records),
        success_count=success,
        fail_count=fail,
        hard_stop_count=hs_cnt,
        timeout_count=to_cnt,
        futures_stop_count=fs_cnt,
        force_count=fc_cnt,
        total_pnl=total_pnl,
        capital_pnl_pct=round(total_pnl / CAPITAL * 100, 2),
        trade_mode=mode,
        capital=CAPITAL,
    )

    print(f"\n  일일 요약 전송: {len(records)}건 / 손익 {total_pnl:+,}원")
    notifier.notify_daily_summary(summary)

    print("\n전송 완료. 텔레그램을 확인하세요.")


if __name__ == "__main__":
    main()
