"""
SQLite 데이터베이스 — 거래 기록 저장/조회.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from src.models.trade import TradeRecord, DailySummary, ExitReason

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trades.db"


class Database:
    """SQLite CRUD."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        conn = self._connect()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                avg_buy_price REAL DEFAULT 0,
                total_buy_qty INTEGER DEFAULT 0,
                total_buy_amount INTEGER DEFAULT 0,
                buy_count INTEGER DEFAULT 0,
                first_buy_time TEXT,
                avg_sell_price REAL DEFAULT 0,
                total_sell_amount INTEGER DEFAULT 0,
                sell_time TEXT,
                exit_reason TEXT NOT NULL,
                pnl REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                holding_minutes REAL DEFAULT 0,
                rolling_high INTEGER DEFAULT 0,
                entry_trigger_price INTEGER DEFAULT 0,
                target_price REAL DEFAULT 0,
                trade_mode TEXT DEFAULT 'dry_run',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS daily_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary_date TEXT UNIQUE NOT NULL,
                trade_mode TEXT DEFAULT 'dry_run',
                candidates_count INTEGER DEFAULT 0,
                targets_count INTEGER DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                no_entry_count INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                max_single_loss REAL DEFAULT 0,
                max_single_gain REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
            CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
        """)
        conn.commit()
        conn.close()
        logger.debug(f"DB 초기화 완료: {self.db_path}")

    # ── 거래 기록 ───────────────────────────────────────────────

    def save_trade(self, trade: TradeRecord) -> int:
        """거래 기록 저장. 반환값: row id."""
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO trades (
                    trade_date, code, name, market,
                    avg_buy_price, total_buy_qty, total_buy_amount, buy_count, first_buy_time,
                    avg_sell_price, total_sell_amount, sell_time,
                    exit_reason, pnl, pnl_pct, holding_minutes,
                    rolling_high, entry_trigger_price, target_price, trade_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(trade.trade_date),
                    trade.code, trade.name, trade.market,
                    trade.avg_buy_price, trade.total_buy_qty, trade.total_buy_amount,
                    trade.buy_count,
                    trade.first_buy_time.isoformat() if trade.first_buy_time else None,
                    trade.avg_sell_price, trade.total_sell_amount,
                    trade.sell_time.isoformat() if trade.sell_time else None,
                    trade.exit_reason.value, trade.pnl, trade.pnl_pct,
                    trade.holding_minutes,
                    trade.rolling_high, trade.entry_trigger_price, trade.target_price,
                    trade.trade_mode,
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
            logger.debug(f"거래 저장: #{row_id} {trade.name} {trade.exit_reason.value}")
            return row_id
        finally:
            conn.close()

    def get_trades_by_date(self, trade_date: date) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE trade_date = ? ORDER BY first_buy_time",
                (str(trade_date),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── 일일 요약 ───────────────────────────────────────────────

    def save_daily_summary(self, summary: DailySummary) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_summary (
                    summary_date, trade_mode,
                    candidates_count, targets_count,
                    total_trades, winning_trades, losing_trades, no_entry_count,
                    total_pnl, max_single_loss, max_single_gain
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(summary.summary_date), summary.trade_mode,
                    summary.candidates_count, summary.targets_count,
                    summary.total_trades, summary.winning_trades,
                    summary.losing_trades, summary.no_entry_count,
                    summary.total_pnl, summary.max_single_loss, summary.max_single_gain,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_summary_range(self, start: date, end: date) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM daily_summary WHERE summary_date BETWEEN ? AND ? ORDER BY summary_date",
                (str(start), str(end)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── 통계 ────────────────────────────────────────────────────

    def get_stats(self, days: int = 30) -> dict:
        """최근 N일 통계 (KST 기준)."""
        from src.utils.market_calendar import now_kst
        from datetime import timedelta

        start_date = (now_kst().date() - timedelta(days=days)).isoformat()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl) as total_pnl,
                    AVG(pnl_pct) as avg_pnl_pct,
                    MIN(pnl) as max_loss,
                    MAX(pnl) as max_gain
                FROM trades
                WHERE trade_date >= ?
                  AND exit_reason != 'NO_ENTRY'
                """,
                (start_date,),
            ).fetchone()

            if row is None:
                return {}
            return dict(row)
        finally:
            conn.close()
