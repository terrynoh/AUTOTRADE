#!/usr/bin/env python3
"""
매매 완료 리포트 + What-if 시나리오 시뮬 리포트 생성 (오프라인/배치).

실시간 텔레그램 전송과 동일한 포맷으로 파일 생성:
    logs/trades/YYYY-MM-DD/NNN_{name}_{reason}_report.txt

데이터 소스:
    1. DB: data/trades.db (sync_logs.bat Step 1 에서 동기화됨)
    2. 분봉: logs/minute_charts/YYYY-MM-DD/{code}_{name}.json
       → 운영 서버에서 _save_minute_chart_snapshot 으로 저장된 스냅샷

사용:
    python scripts/generate_scenario_report.py             # 오늘 날짜
    python scripts/generate_scenario_report.py 2026-04-23  # 특정 날짜
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from datetime import datetime, date
from pathlib import Path

# Windows cp949 콘솔에서도 이모지/한글 안 깨지게 stdout 을 UTF-8 로 재구성
# (파일 쓰기는 항상 UTF-8 이므로 영향 없음. 콘솔 표시용만 조정)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    # 이전 Python (3.6 이하) 또는 비표준 stdout → 무시
    pass


ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "trades.db"
TRADES_DIR = ROOT / "logs" / "trades"
MINUTE_DIR = ROOT / "logs" / "minute_charts"

# src/ 를 import path 에 추가 (scenario_sim import 용)
sys.path.insert(0, str(ROOT))


# ── 헬퍼 ──────────────────────────────────────────────────


def _parse_iso(dt_str: str | None):
    """DB 저장 포맷 (isoformat) → datetime."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return None


def _exit_reason_name(reason: str) -> str:
    return {
        "TARGET": "목표가 도달",
        "HARD_STOP": "하드 손절",
        "TIMEOUT": "20분 타임아웃",
        "FUTURES_STOP": "선물 급락",
        "FORCE_LIQUIDATE": "강제 청산",
        "MANUAL": "수동 청산",
        "NO_ENTRY": "미진입",
    }.get(reason, reason)


def _exit_reason_tag(reason: str) -> str:
    """텔레그램 포맷과 동일한 이모지 사용 (파일은 UTF-8)."""
    if reason == "TARGET":
        return "✅"
    if reason in ("HARD_STOP", "TIMEOUT", "FUTURES_STOP", "FORCE_LIQUIDATE"):
        return "🔴"
    return "⚪"


def _holding_str_from_seconds(sec: int) -> str:
    mins, secs = divmod(int(sec or 0), 60)
    return f"{mins}분 {secs}초" if mins > 0 else f"{secs}초"


def _load_minute_chart(trade_date_str: str, code: str) -> list[dict]:
    """로컬 분봉 스냅샷 파일에서 분봉 로드. 없으면 빈 리스트."""
    d = MINUTE_DIR / trade_date_str
    if not d.exists():
        return []
    # {code}_*.json 패턴으로 검색
    for f in d.glob(f"{code}_*.json"):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            candles = payload.get("candles", [])
            if isinstance(candles, list):
                return candles
        except Exception as e:
            print(f"  [WARN] {f.name} 파싱 실패: {e}")
    return []


def _format_actual_block(row: dict) -> str:
    """실제 50% 매매 블록 (상단)."""
    name = row.get("name", "?")
    new_high_price = row.get("new_high_price", 0) or 0
    new_high_time = _parse_iso(row.get("new_high_time"))
    buy1_price = row.get("buy1_price", 0) or 0
    buy1_qty = row.get("buy1_qty", 0) or 0
    buy1_time = _parse_iso(row.get("buy1_time"))
    buy2_price = row.get("buy2_price", 0) or 0
    buy2_qty = row.get("buy2_qty", 0) or 0
    buy2_time = _parse_iso(row.get("buy2_time"))
    target_buy2_price = row.get("target_buy2_price", 0) or 0
    exit_price = row.get("exit_price", 0) or 0
    exit_reason = row.get("exit_reason", "NO_ENTRY") or "NO_ENTRY"
    pnl = int(row.get("pnl", 0) or 0)
    pnl_pct = float(row.get("pnl_pct", 0.0) or 0.0)
    capital_pnl_pct = float(row.get("capital_pnl_pct", 0.0) or 0.0)
    capital = int(row.get("capital", 50_000_000) or 50_000_000)
    holding_seconds = int(row.get("holding_seconds", 0) or 0)

    reason_name = _exit_reason_name(exit_reason)
    reason_tag = _exit_reason_tag(exit_reason)

    high_time_str = new_high_time.strftime("%H:%M:%S") if new_high_time else "-"
    buy1_time_str = buy1_time.strftime("%H:%M:%S") if buy1_time else "-"

    line_buy1 = f"체결    │ 1차 {buy1_time_str} {buy1_price:,}원 × {buy1_qty}주"
    if buy2_qty > 0 and buy2_time is not None:
        buy2_time_str = buy2_time.strftime("%H:%M:%S")
        line_buy2 = f"        │ 2차 {buy2_time_str} {buy2_price:,}원 × {buy2_qty}주"
    else:
        line_buy2 = f"        │ 2차 {target_buy2_price:,}원 매수가 미도달"

    pnl_sign = "+" if pnl >= 0 else ""
    capital_str = f"{capital // 10000:,}만원"

    return (
        f"[{name}] 거래 완료\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"신고가  │ {new_high_price:,}원 ({high_time_str})\n"
        f"{line_buy1}\n"
        f"{line_buy2}\n"
        f"청산    │ {exit_price:,}원 — {reason_name} {reason_tag}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"손익    │ {pnl_sign}{pnl:,}원 ({pnl_sign}{pnl_pct:.2f}%)\n"
        f"투자금比│ {pnl_sign}{capital_pnl_pct:.2f}% ({capital_str} 기준)\n"
        f"보유시간│ {_holding_str_from_seconds(holding_seconds)}"
    )


def _build_report_for_row(row: dict) -> str:
    """단일 거래에 대한 전체 리포트 (실측 + 60/70 시나리오)."""
    from src.utils.scenario_sim import run_scenarios, format_scenario_block_plain

    head = _format_actual_block(row)

    # 시뮬 조건 확인
    total_buy_qty = int(row.get("entry_qty", 0) or 0)
    buy1_time = _parse_iso(row.get("buy1_time"))
    confirmed_high = int(row.get("new_high_price", 0) or 0)
    buy1_price = int(row.get("buy1_price", 0) or 0)
    entry_price = int(row.get("entry_price", 0) or 0)  # avg_buy_price
    capital = int(row.get("capital", 50_000_000) or 50_000_000)
    trade_date_str = str(row.get("trade_date", ""))
    code = str(row.get("code", ""))

    if not (total_buy_qty > 0 and buy1_time and confirmed_high > 0 and buy1_price > 0):
        return head + "\n\n※ 시나리오 시뮬 불가 (진입 정보 부족)"

    # 분봉 스냅샷 로드
    candles = _load_minute_chart(trade_date_str, code)
    if not candles:
        return (
            head
            + "\n\n※ 시나리오 시뮬 불가 "
            f"(분봉 스냅샷 없음: logs/minute_charts/{trade_date_str}/{code}_*.json)"
        )

    # force_time — strategy_params.yaml 의 exit.force_liquidate_time 을 기본값 "11:20" 로 사용
    # 배치에서는 yaml 로드 복잡성 피하려고 하드코딩
    force_time = "11:20"

    scenarios = run_scenarios(
        minute_chart=candles,
        confirmed_high=confirmed_high,
        initial_low=buy1_price,
        buy_time=buy1_time,
        recovery_pcts=[60.0, 70.0],
        force_time=force_time,
    )

    lines = [head, "", "※ What-if (분봉 해상도, v1: target/강제청산)"]
    avg_buy = float(entry_price) if entry_price > 0 else float(buy1_price)
    for pct in sorted(scenarios.keys()):
        sim = scenarios[pct]
        block = format_scenario_block_plain(
            recovery_pct=pct,
            sim=sim,
            avg_buy_price=avg_buy,
            total_buy_qty=total_buy_qty,
            buy_time=buy1_time,
            capital=capital,
        )
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(block)

    return "\n".join(lines)


def generate(target_date: str | None = None) -> int:
    """리포트 생성. 지정 날짜 거래 건별로 *_report.txt 작성.

    Args:
        target_date: "YYYY-MM-DD" 또는 None (= 오늘)

    Returns:
        생성된 리포트 수
    """
    if target_date is None:
        target_date = date.today().isoformat()

    if not DB.exists():
        print(f"[오류] DB 없음: {DB}")
        return 0

    out_dir = TRADES_DIR / target_date
    if not out_dir.exists():
        # extract_trades_local.py 가 먼저 돌아야 생성됨. 없어도 진행 가능 (리포트만 만듦).
        out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades_r10'")
    if not cur.fetchone():
        print("[오류] trades_r10 테이블 없음")
        conn.close()
        return 0

    cur.execute(
        "SELECT * FROM trades_r10 WHERE trade_date = ? ORDER BY id",
        (target_date,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not rows:
        print(f"[정보] {target_date} 거래 없음")
        return 0

    count = 0
    for i, row in enumerate(rows, 1):
        name = (row.get("name") or "UNKNOWN").replace(" ", "_")
        reason = (row.get("exit_reason") or "NONE").upper()
        fname = f"{i:03d}_{name}_{reason}_report.txt"
        out_path = out_dir / fname

        try:
            report = _build_report_for_row(row)
            out_path.write_text(report + "\n", encoding="utf-8")
            print(f"  [RPT] {target_date}/{fname}")
            count += 1
        except Exception as e:
            print(f"  [ERR] {fname}: {e}")

    print(f"\n완료: 총 {count}건 → {out_dir}")
    return count


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    generate(arg)
