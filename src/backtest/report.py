"""
백테스트 리포트 — 시뮬레이션 결과 집계/출력.
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from src.backtest.simulator import BacktestResult, SimResult
from src.models.trade import ExitReason


def print_report(result: BacktestResult) -> str:
    """
    백테스트 결과를 텍스트 리포트로 출력.

    Returns:
        리포트 문자열
    """
    lines = []
    lines.append("=" * 70)
    lines.append("  백테스트 리포트")
    lines.append("=" * 70)
    lines.append(f"기간: {result.start_date} ~ {result.end_date}")
    lines.append(f"총 거래일: {len(result.results)}일")
    lines.append("")

    # ── 전체 통계 ───────────────────────────────────────────
    lines.append("[ 전체 통계 ]")
    lines.append(f"  총 매매 수: {result.total_trades}건")
    lines.append(f"  승리: {result.winning_trades}건")
    lines.append(f"  패배: {result.losing_trades}건")
    lines.append(f"  승률: {result.win_rate:.1f}%")
    lines.append(f"  평균 수익률: {result.avg_pnl_pct:+.2f}% (거래비용 0.5% 차감 후)")
    lines.append(f"  조정 미발생일: {result.no_entry_days}일")
    lines.append("")

    # 거래된 건만 필터
    traded = [r for r in result.results if r.exit_reason != ExitReason.NO_ENTRY]

    if traded:
        pnls = [r.pnl_pct for r in traded]
        lines.append(f"  최대 수익: {max(pnls):+.2f}%")
        lines.append(f"  최대 손실: {min(pnls):+.2f}%")
        lines.append("")

        # ── 청산 사유별 분류 ────────────────────────────────
        lines.append("[ 청산 사유별 ]")
        reason_counts: dict[str, list[float]] = {}
        for r in traded:
            key = r.exit_reason.value
            if key not in reason_counts:
                reason_counts[key] = []
            reason_counts[key].append(r.pnl_pct)

        for reason, pnl_list in sorted(reason_counts.items()):
            cnt = len(pnl_list)
            avg = sum(pnl_list) / cnt
            lines.append(f"  {reason}: {cnt}건 | 평균 수익률 {avg:+.2f}%")
        lines.append("")

        # ── 시장별 분류 ─────────────────────────────────────
        lines.append("[ 시장별 ]")
        for mkt in ["KOSPI", "KOSDAQ"]:
            mkt_trades = [r for r in traded if r.market == mkt]
            if mkt_trades:
                wins = sum(1 for r in mkt_trades if r.pnl_pct > 0)
                total = len(mkt_trades)
                avg_pnl = sum(r.pnl_pct for r in mkt_trades) / total
                lines.append(
                    f"  {mkt}: {total}건 | 승률 {wins/total*100:.0f}% | "
                    f"평균 수익률 {avg_pnl:+.2f}%"
                )
        lines.append("")

    # ── 검증 기준 판정 ──────────────────────────────────────
    lines.append("[ 검증 기준 ]")
    target_hit_rate = result.win_rate  # 50% 회복 도달률 ≈ 승률
    lines.append(f"  50% 회복 달성률: {target_hit_rate:.1f}%")

    if target_hit_rate >= 80:
        lines.append("  → ✅ 실구현 진행 가능 (≥80%)")
    elif target_hit_rate >= 70:
        lines.append("  → ⚠️ 파라미터 튜닝 필요 (70~80%)")
    else:
        lines.append("  → ❌ 전략 수정 필요 (<70%)")

    avg_net = result.avg_pnl_pct
    if avg_net > 0:
        lines.append(f"  거래비용 차감 후 평균 순수익률: {avg_net:+.2f}% → ✅ 양수")
    else:
        lines.append(f"  거래비용 차감 후 평균 순수익률: {avg_net:+.2f}% → ❌ 음수")

    lines.append("=" * 70)

    report_text = "\n".join(lines)
    logger.info("\n" + report_text)
    return report_text


def export_csv(result: BacktestResult, path: str) -> None:
    """결과를 CSV로 내보내기."""
    import csv

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "날짜", "종목코드", "종목명", "시장",
            "rolling_high", "매수가", "매도가", "청산사유",
            "수익률(%)", "매수시각", "매도시각",
        ])
        for r in result.results:
            writer.writerow([
                str(r.trade_date), r.code, r.name, r.market,
                r.rolling_high, r.entry_price, r.exit_price,
                r.exit_reason.value, f"{r.pnl_pct:.2f}",
                r.entry_time, r.exit_time,
            ])

    logger.info(f"CSV 내보내기: {path}")
