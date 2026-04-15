"""
TradeLogger — 매매 기록 저장 + 로그 출력.

설계 원칙:
- 단일 진실: SQLite trades.db가 모든 거래의 source of truth
- 명확한 기록: 신고가 달성 → 체결 → 청산 → 결과
- 분석 용이: 종목별 + 일별 요약

사용:
    logger = TradeLogger()
    record = logger.record_trade(watcher, trader)
    logger.update_daily_summary(date.today())
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from loguru import logger as log

from src.models.trade import TradeRecord, ExitReason
from src.utils.market_calendar import now_kst


# ── 상수 ──────────────────────────────────────────────────

DEFAULT_CAPITAL = 50_000_000  # 기본 투자금 5천만원

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trades.db"


# ── R-10 일별 요약 (trade_logger 전용) ─────────────────────


@dataclass
class DailySummaryR10:
    """R-10 일별 요약 (텔레그램/로그 출력 전용).
    
    src/models/trade.py의 DailySummary와 별개로 관리.
    청산 사유별 카운트 등 상세 정보 포함.
    """
    summary_date: date
    
    total_trades: int
    success_count: int
    fail_count: int
    
    hard_stop_count: int
    timeout_count: int
    futures_stop_count: int
    force_count: int
    
    total_pnl: int
    capital_pnl_pct: float
    
    trade_mode: str
    capital: int


# ── ExitReason 헬퍼 ────────────────────────────────────────


def _exit_reason_display_name(reason: ExitReason) -> str:
    """ExitReason → 한글 표시명."""
    return {
        ExitReason.TARGET: "목표가 도달",
        ExitReason.HARD_STOP: "하드 손절",
        ExitReason.TIMEOUT: "20분 타임아웃",
        ExitReason.FUTURES_STOP: "선물 급락",
        ExitReason.FORCE_LIQUIDATE: "강제 청산",
        ExitReason.MANUAL: "수동 청산",
        ExitReason.NO_ENTRY: "미진입",
    }.get(reason, reason.value)


def _exit_reason_emoji(reason: ExitReason) -> str:
    """ExitReason → 이모지."""
    if reason == ExitReason.TARGET:
        return "✅"
    elif reason in (ExitReason.HARD_STOP, ExitReason.TIMEOUT, 
                    ExitReason.FUTURES_STOP, ExitReason.FORCE_LIQUIDATE):
        return "🔴"
    return "⚪"


# ── TradeLogger ───────────────────────────────────────────


class TradeLogger:
    """매매 기록 저장 + 로그 출력."""

    def __init__(self, db_path: Optional[Path] = None, capital: int = DEFAULT_CAPITAL):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.capital = capital
        self._init_db()
        self._trade_counter: dict[date, int] = {}  # 일별 거래 번호

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """DB 테이블 초기화."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades_r10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date DATE NOT NULL,
                trade_id TEXT UNIQUE,
                
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                market TEXT,
                
                new_high_price INTEGER,
                new_high_time DATETIME,
                
                buy1_price INTEGER,
                buy1_qty INTEGER,
                buy1_time DATETIME,
                buy2_price INTEGER,
                buy2_qty INTEGER,
                buy2_time DATETIME,
                
                entry_price INTEGER,
                entry_qty INTEGER,
                entry_amount INTEGER,
                entry_time DATETIME,
                
                exit_price INTEGER,
                exit_time DATETIME,
                exit_reason TEXT,
                
                pnl INTEGER,
                pnl_pct REAL,
                capital_pnl_pct REAL,
                holding_seconds INTEGER,
                
                confirmed_high INTEGER,
                target_buy1_price INTEGER,
                target_buy2_price INTEGER,
                hard_stop_price INTEGER,
                target_price INTEGER,
                
                trade_mode TEXT DEFAULT 'dry_run',
                capital INTEGER DEFAULT 50000000,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS daily_summary_r10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary_date DATE UNIQUE NOT NULL,
                
                total_trades INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                
                hard_stop_count INTEGER DEFAULT 0,
                timeout_count INTEGER DEFAULT 0,
                futures_stop_count INTEGER DEFAULT 0,
                force_count INTEGER DEFAULT 0,
                
                total_pnl INTEGER DEFAULT 0,
                capital_pnl_pct REAL DEFAULT 0,
                
                trade_mode TEXT DEFAULT 'dry_run',
                capital INTEGER DEFAULT 50000000,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_trades_r10_date ON trades_r10(trade_date);
            CREATE INDEX IF NOT EXISTS idx_trades_r10_code ON trades_r10(code);
        """)
        conn.commit()
        conn.close()
        log.debug(f"TradeLogger DB 초기화: {self.db_path}")

    def _next_trade_id(self, trade_date: date) -> str:
        """일별 거래 ID 생성. 예: '2026-04-14_001'"""
        if trade_date not in self._trade_counter:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades_r10 WHERE trade_date = ?",
                (str(trade_date),)
            ).fetchone()
            conn.close()
            self._trade_counter[trade_date] = row["cnt"] if row else 0
        
        self._trade_counter[trade_date] += 1
        return f"{trade_date}_{self._trade_counter[trade_date]:03d}"

    # ── 거래 기록 ─────────────────────────────────────────

    def record_trade(self, watcher, trader, trade_mode: str = "dry_run") -> Optional[TradeRecord]:
        """Watcher + Trader 정보로 거래 기록 생성 + 저장 + 로그 출력.
        
        Args:
            watcher: Watcher 인스턴스
            trader: Trader 인스턴스
            trade_mode: dry_run / paper / live
            
        Returns:
            TradeRecord: 저장된 거래 기록 (미진입 시 None)
        """
        if watcher.total_buy_qty <= 0:
            return None

        today = now_kst().date()
        trade_id = self._next_trade_id(today)

        # 평균 매수가
        entry_price = watcher.total_buy_amount // watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0
        entry_amount = watcher.total_buy_amount
        entry_qty = watcher.total_buy_qty

        # 청산 정보
        exit_reason = self._map_exit_reason(watcher.exit_reason)
        exit_price = watcher.exit_price or watcher.current_price
        exit_time = watcher.exited_at

        # 손익 계산
        pnl = (exit_price - entry_price) * entry_qty if entry_qty > 0 else 0
        pnl_pct = (pnl / entry_amount * 100) if entry_amount > 0 else 0.0
        capital_pnl_pct = (pnl / self.capital * 100)

        # 보유 시간
        holding_seconds = 0
        if watcher.entered_at and exit_time:
            holding_seconds = int((exit_time - watcher.entered_at).total_seconds())

        # TradeRecord 생성 (src/models/trade.py의 클래스 사용)
        record = TradeRecord(
            trade_date=today,
            code=watcher.code,
            name=watcher.name,
            market=watcher.market.value if hasattr(watcher.market, 'value') else str(watcher.market),
            
            # R-10 필드
            new_high_price=watcher.confirmed_high,
            new_high_time=watcher.confirmed_high_time,
            buy1_price=watcher.buy1_price,
            buy1_qty=entry_qty if watcher.buy1_filled else 0,
            buy1_time=watcher.entered_at if watcher.buy1_filled else None,
            buy2_price=watcher.buy2_price,
            buy2_qty=0,
            buy2_time=None,
            
            # 기존 필드
            avg_buy_price=float(entry_price),
            total_buy_qty=entry_qty,
            total_buy_amount=entry_amount,
            buy_count=int(watcher.buy1_filled) + int(watcher.buy2_filled),
            first_buy_time=watcher.entered_at,
            
            avg_sell_price=float(exit_price),
            total_sell_amount=exit_price * entry_qty,
            sell_time=exit_time,
            
            exit_reason=exit_reason,
            pnl=float(pnl),
            pnl_pct=round(pnl_pct, 2),
            capital_pnl_pct=round(capital_pnl_pct, 2),
            holding_minutes=holding_seconds / 60,
            holding_seconds=holding_seconds,
            
            rolling_high=watcher.intraday_high,
            entry_trigger_price=watcher.target_buy1_price,
            target_price=float(watcher.target_price),
            hard_stop_price=watcher.hard_stop_price_value,
            
            trade_mode=trade_mode,
            capital=self.capital,
        )

        # DB 저장
        self._save_trade(record, trade_id)

        # 로그 출력
        self._print_trade_log(record)

        return record

    def _map_exit_reason(self, reason_str: str) -> ExitReason:
        """문자열 → ExitReason 변환."""
        mapping = {
            "target": ExitReason.TARGET,
            "hard_stop": ExitReason.HARD_STOP,
            "timeout": ExitReason.TIMEOUT,
            "futures_stop": ExitReason.FUTURES_STOP,
            "force": ExitReason.FORCE_LIQUIDATE,
        }
        return mapping.get(reason_str.lower(), ExitReason.NO_ENTRY)

    def _save_trade(self, record: TradeRecord, trade_id: str) -> int:
        """DB에 거래 저장."""
        conn = self._connect()
        try:
            cur = conn.execute("""
                INSERT INTO trades_r10 (
                    trade_date, trade_id, code, name, market,
                    new_high_price, new_high_time,
                    buy1_price, buy1_qty, buy1_time,
                    buy2_price, buy2_qty, buy2_time,
                    entry_price, entry_qty, entry_amount, entry_time,
                    exit_price, exit_time, exit_reason,
                    pnl, pnl_pct, capital_pnl_pct, holding_seconds,
                    confirmed_high, target_buy1_price, target_buy2_price,
                    hard_stop_price, target_price,
                    trade_mode, capital
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(record.trade_date), trade_id, record.code, record.name, record.market,
                record.new_high_price, record.new_high_time.isoformat() if record.new_high_time else None,
                record.buy1_price, record.buy1_qty, record.buy1_time.isoformat() if record.buy1_time else None,
                record.buy2_price, record.buy2_qty, record.buy2_time.isoformat() if record.buy2_time else None,
                int(record.avg_buy_price), record.total_buy_qty, record.total_buy_amount,
                record.first_buy_time.isoformat() if record.first_buy_time else None,
                int(record.avg_sell_price), record.sell_time.isoformat() if record.sell_time else None,
                record.exit_reason.value,
                int(record.pnl), record.pnl_pct, record.capital_pnl_pct, record.holding_seconds,
                record.rolling_high, record.entry_trigger_price, record.buy2_price,
                record.hard_stop_price, int(record.target_price),
                record.trade_mode, record.capital,
            ))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _print_trade_log(self, record: TradeRecord):
        """개별 거래 로그 출력."""
        emoji = "📈" if record.pnl >= 0 else "📉"
        reason_emoji = _exit_reason_emoji(record.exit_reason)
        reason_name = _exit_reason_display_name(record.exit_reason)

        # 보유 시간 포맷
        mins, secs = divmod(record.holding_seconds, 60)
        holding_str = f"{mins}분 {secs}초" if mins > 0 else f"{secs}초"

        # 시간 포맷
        high_time_str = record.new_high_time.strftime("%H:%M:%S") if record.new_high_time else "-"
        entry_time_str = record.first_buy_time.strftime("%H:%M:%S") if record.first_buy_time else "-"
        exit_time_str = record.sell_time.strftime("%H:%M:%S") if record.sell_time else "-"

        pnl_sign = "+" if record.pnl >= 0 else ""
        
        log.info(
            f"\n{'━' * 55}\n"
            f"{emoji} [{record.name}] 거래 완료\n"
            f"{'━' * 55}\n"
            f"신고가    │ {record.new_high_price:,}원 ({high_time_str})\n"
            f"체결      │ {int(record.avg_buy_price):,}원 × {record.total_buy_qty}주 ({entry_time_str})\n"
            f"청산      │ {int(record.avg_sell_price):,}원 ({exit_time_str}) — {reason_name} {reason_emoji}\n"
            f"손익      │ {pnl_sign}{int(record.pnl):,}원 ({pnl_sign}{record.pnl_pct:.2f}%)\n"
            f"투자금比  │ {pnl_sign}{record.capital_pnl_pct:.2f}% ({record.capital//10000:,}만원 기준)\n"
            f"보유시간  │ {holding_str}\n"
            f"{'━' * 55}"
        )

    # ── 일별 요약 ─────────────────────────────────────────

    def update_daily_summary(self, summary_date: Optional[date] = None, trade_mode: str = "dry_run") -> Optional[DailySummaryR10]:
        """일별 요약 계산 + 저장 + 출력."""
        if summary_date is None:
            summary_date = now_kst().date()

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM trades_r10 WHERE trade_date = ? AND exit_reason != 'NO_ENTRY'",
                (str(summary_date),)
            ).fetchall()

            total_trades = len(rows)
            success_count = sum(1 for r in rows if r["exit_reason"] == "TARGET")
            hard_stop_count = sum(1 for r in rows if r["exit_reason"] == "HARD_STOP")
            timeout_count = sum(1 for r in rows if r["exit_reason"] == "TIMEOUT")
            futures_stop_count = sum(1 for r in rows if r["exit_reason"] == "FUTURES_STOP")
            force_count = sum(1 for r in rows if r["exit_reason"] == "FORCE_LIQUIDATE")
            fail_count = hard_stop_count + timeout_count + futures_stop_count + force_count

            total_pnl = sum(r["pnl"] for r in rows)
            capital_pnl_pct = round(total_pnl / self.capital * 100, 2)

            conn.execute("""
                INSERT OR REPLACE INTO daily_summary_r10 (
                    summary_date, total_trades, success_count, fail_count,
                    hard_stop_count, timeout_count, futures_stop_count, force_count,
                    total_pnl, capital_pnl_pct, trade_mode, capital
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(summary_date), total_trades, success_count, fail_count,
                hard_stop_count, timeout_count, futures_stop_count, force_count,
                total_pnl, capital_pnl_pct, trade_mode, self.capital,
            ))
            conn.commit()

            summary = DailySummaryR10(
                summary_date=summary_date,
                total_trades=total_trades,
                success_count=success_count,
                fail_count=fail_count,
                hard_stop_count=hard_stop_count,
                timeout_count=timeout_count,
                futures_stop_count=futures_stop_count,
                force_count=force_count,
                total_pnl=total_pnl,
                capital_pnl_pct=capital_pnl_pct,
                trade_mode=trade_mode,
                capital=self.capital,
            )

            self._print_daily_summary(summary)
            return summary

        finally:
            conn.close()

    def _print_daily_summary(self, summary: DailySummaryR10):
        """일별 요약 로그 출력."""
        pnl_sign = "+" if summary.total_pnl >= 0 else ""

        fail_details = []
        if summary.hard_stop_count > 0:
            fail_details.append(f"하드손절 {summary.hard_stop_count}")
        if summary.timeout_count > 0:
            fail_details.append(f"타임아웃 {summary.timeout_count}")
        if summary.futures_stop_count > 0:
            fail_details.append(f"선물급락 {summary.futures_stop_count}")
        if summary.force_count > 0:
            fail_details.append(f"강제청산 {summary.force_count}")
        fail_str = " / ".join(fail_details) if fail_details else "-"

        log.info(
            f"\n{'═' * 55}\n"
            f"📊 {summary.summary_date} 매매 일지 ({summary.trade_mode.upper()})\n"
            f"{'═' * 55}\n"
            f"총 거래   │ {summary.total_trades}건\n"
            f"성공      │ {summary.success_count}건 (목표가 도달)\n"
            f"실패      │ {summary.fail_count}건 ({fail_str})\n"
            f"{'─' * 55}\n"
            f"총 손익   │ {pnl_sign}{summary.total_pnl:,}원\n"
            f"투자금比  │ {pnl_sign}{summary.capital_pnl_pct:.2f}% ({summary.capital//10000:,}만원 기준)\n"
            f"{'═' * 55}"
        )

    # ── 조회 ──────────────────────────────────────────────

    def get_trades_by_date(self, trade_date: date) -> list[dict]:
        """특정일 거래 조회."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM trades_r10 WHERE trade_date = ? ORDER BY entry_time",
                (str(trade_date),)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_daily_summary(self, summary_date: date) -> Optional[dict]:
        """특정일 요약 조회."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM daily_summary_r10 WHERE summary_date = ?",
                (str(summary_date),)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
