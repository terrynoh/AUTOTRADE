#!/usr/bin/env python3
"""
W-DIAG-HTTP500-01: KIS REST API HTTP 500 분석 스크립트.

Day 1/2/3+ 3일치 비교를 기본으로, 단일 파일 분석도 지원.
출력: stdout + logs/diag/http500_report_YYYYMMDD_HHMMSS.txt

집계 항목:
  [1] 총량 (전체/500/재시도 1차/2차/최대 초과/복구율)
  [2] 엔드포인트(TR_ID)별 분포
  [3] 시간대 분포 (도메인 경계: 09:49 / 11:00 / 11:20)
  [4] Rate limit 대조 (공식 20 QPS / 계좌)
  [5] Case 판정 가이드 (A/B/C)

사용법:
  python scripts/diag_http500_analyze.py <log1> [<log2> ...]
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from datetime import datetime, time as dtime
from pathlib import Path


# Windows 콘솔 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# KIS 공식 rate limit (src/kis_api/constants.py::RATE_LIMIT_REAL)
RATE_LIMIT_REAL_QPS = 20

# 로그 라인 예시:
#   2026-04-21 09:20:04.768 | WARNING  | src.kis_api.kis:_request:340 | HTTP 500 [FHKST01010100], 재시도 1/2
LINE_PAT = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\.(?P<ms>\d+)\s+\|\s*"
    r"(?P<level>\w+)\s*\|"
)
HTTP500_PAT = re.compile(
    r"HTTP\s+5\d{2}\s+\[(?P<tr>[A-Z0-9_]+)\]"
    r"(?:\s*,\s*재시도\s*(?P<attempt>\d+)\s*/\s*(?P<max>\d+))?"
)
RETRY_FAIL_PAT = re.compile(r"최대 재시도 횟수 초과")

# 도메인 시간 경계
SCREENING_END = dtime(9, 49)
BUY_DEADLINE = dtime(11, 0)
FORCE_LIQUIDATE = dtime(11, 20)
MARKET_CLOSE = dtime(15, 30)


def _bucket_of(t: dtime) -> str:
    if t < SCREENING_END:
        return "pre_screening(~09:49)"
    if t < BUY_DEADLINE:
        return "core(09:49~11:00)"
    if t < FORCE_LIQUIDATE:
        return "post_deadline(11:00~11:20)"
    if t < MARKET_CLOSE:
        return "post_liquidate(11:20~15:30)"
    return "outside(others)"


def analyze_file(log_path: Path) -> dict:
    stats = {
        "file": str(log_path),
        "date": None,
        "total_lines": 0,
        "http500_total": 0,
        "retry_max_exceeded": 0,
        "retry_1_of_2": 0,
        "retry_2_of_2": 0,
        "retry_other": 0,
        "by_tr_id": Counter(),
        "by_minute": Counter(),  # "HH:MM" -> count
        "by_second": Counter(),  # "HH:MM:SS" -> count
        "by_bucket": Counter(),
        "first_500_ts": None,
        "last_500_ts": None,
    }

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stats["total_lines"] += 1
            m = LINE_PAT.match(line)
            if not m:
                continue
            if stats["date"] is None:
                stats["date"] = m.group("date")

            if "HTTP 5" in line:
                h = HTTP500_PAT.search(line)
                if h:
                    tr_id = h.group("tr")
                    attempt = h.group("attempt")
                    stats["http500_total"] += 1
                    stats["by_tr_id"][tr_id] += 1

                    if attempt == "1":
                        stats["retry_1_of_2"] += 1
                    elif attempt == "2":
                        stats["retry_2_of_2"] += 1
                    else:
                        stats["retry_other"] += 1

                    hms = m.group("time")  # HH:MM:SS
                    hm = hms[:5]
                    stats["by_minute"][hm] += 1
                    stats["by_second"][hms] += 1

                    hh, mm, ss = int(hms[:2]), int(hms[3:5]), int(hms[6:8])
                    stats["by_bucket"][_bucket_of(dtime(hh, mm, ss))] += 1

                    ts_str = f"{m.group('date')} {hms}"
                    if stats["first_500_ts"] is None:
                        stats["first_500_ts"] = ts_str
                    stats["last_500_ts"] = ts_str

            if RETRY_FAIL_PAT.search(line):
                stats["retry_max_exceeded"] += 1

    # 복구율: (1차 재시도 발생 - 최대초과) / 1차 재시도 발생
    # 재시도 1/2 로그 = 1차 재시도 발생 (1차 요청 실패). 이 중 2/2 까지 소진된 건수를 제외한 비율이 1차 or 2차 재시도로 복구된 비율.
    base = stats["retry_1_of_2"]
    recovered = base - stats["retry_max_exceeded"]
    stats["recovery_rate"] = (recovered / base * 100) if base > 0 else 0.0
    stats["retry_2_2_soakage_rate"] = (
        stats["retry_2_of_2"] / base * 100 if base > 0 else 0.0
    )

    if stats["by_minute"]:
        peak_min, peak_cnt = stats["by_minute"].most_common(1)[0]
        stats["peak_minute"] = peak_min
        stats["peak_minute_count"] = peak_cnt
    else:
        stats["peak_minute"] = None
        stats["peak_minute_count"] = 0

    if stats["by_second"]:
        peak_sec, peak_sec_cnt = stats["by_second"].most_common(1)[0]
        stats["peak_second"] = peak_sec
        stats["peak_second_count"] = peak_sec_cnt
    else:
        stats["peak_second"] = None
        stats["peak_second_count"] = 0

    return stats


def format_report(all_stats: list[dict]) -> str:
    lines: list[str] = []
    sep = "=" * 84
    bar = "-" * 84
    lines.append(sep)
    lines.append("W-DIAG-HTTP500-01: KIS REST API HTTP 500 분석 리포트")
    lines.append(f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"분석 대상: {len(all_stats)}개 로그")
    lines.append(sep)
    lines.append("")

    # [1] 총량
    lines.append("[1] 총량 집계 (일자별 비교)")
    lines.append(bar)
    lines.append(
        f"{'날짜':<12}{'전체 라인':>10}{'HTTP 5xx':>10}{'재시도 1/2':>12}"
        f"{'재시도 2/2':>12}{'최대 초과':>10}{'복구율':>9}{'2/2 소진율':>12}"
    )
    for s in all_stats:
        lines.append(
            f"{s['date'] or 'N/A':<12}"
            f"{s['total_lines']:>10,}"
            f"{s['http500_total']:>10,}"
            f"{s['retry_1_of_2']:>12,}"
            f"{s['retry_2_of_2']:>12,}"
            f"{s['retry_max_exceeded']:>10,}"
            f"{s['recovery_rate']:>8.1f}%"
            f"{s['retry_2_2_soakage_rate']:>11.1f}%"
        )
    lines.append("")
    lines.append("  해석:")
    lines.append("    • 복구율 = (재시도 1/2 - 최대초과) / 재시도 1/2 × 100")
    lines.append("    • 2/2 소진율 = 재시도 2/2 / 재시도 1/2 × 100 (1차 재시도 후 추가 실패 비율)")
    lines.append("    • 최대 초과 0 + 2/2 소진 ≈ 0 → KIS 인프라 상시 이슈 (Case A 근거)")
    lines.append("")

    # [2] TR_ID
    lines.append("[2] 엔드포인트 (TR_ID) 별 분포")
    lines.append(bar)
    for s in all_stats:
        lines.append(f"  ▸ {s['date']}  (총 {s['http500_total']:,}건)")
        if s["http500_total"] == 0:
            lines.append("    (HTTP 5xx 0건)")
            lines.append("")
            continue
        for tr_id, cnt in s["by_tr_id"].most_common(10):
            pct = cnt / s["http500_total"] * 100
            lines.append(f"    {tr_id:<18} {cnt:>7,}건  ({pct:>5.1f}%)")
        lines.append("")

    # [3] 시간대
    lines.append("[3] 시간대 분포 (도메인 경계: 09:49 / 11:00 / 11:20 / 15:30)")
    lines.append(bar)
    buckets = [
        "pre_screening(~09:49)",
        "core(09:49~11:00)",
        "post_deadline(11:00~11:20)",
        "post_liquidate(11:20~15:30)",
        "outside(others)",
    ]
    for s in all_stats:
        if s["http500_total"] == 0:
            lines.append(f"  ▸ {s['date']}  (0건)")
            lines.append("")
            continue
        lines.append(
            f"  ▸ {s['date']}  (총 {s['http500_total']:,}건, "
            f"첫 {s['first_500_ts']}, 마지막 {s['last_500_ts']})"
        )
        lines.append(
            f"    peak 분: {s['peak_minute']}  {s['peak_minute_count']}건/분"
            f"  | peak 초: {s['peak_second']}  {s['peak_second_count']}건/초"
        )
        for b in buckets:
            cnt = s["by_bucket"].get(b, 0)
            if cnt == 0:
                continue
            pct = cnt / s["http500_total"] * 100
            lines.append(f"    {b:<30} {cnt:>7,}건  ({pct:>5.1f}%)")
        lines.append("")

    # [4] Rate limit 대조
    lines.append("[4] KIS 공식 rate limit 대조")
    lines.append(bar)
    lines.append(f"  공식 한도 (constants.py RATE_LIMIT_REAL): {RATE_LIMIT_REAL_QPS} QPS — 계좌 기준")
    lines.append(f"  참조 문서: Obsidian/한국투자증권_오픈API_전체문서_20260416_030007.xlsx")
    lines.append(f"  TR별 개별 한도: 문서상 계좌 총합 기준, 개별 TR별 한도 미명시 추정")
    lines.append("")
    lines.append("  초당 peak (HTTP 5xx 기준):")
    for s in all_stats:
        if s["peak_second_count"] == 0:
            lines.append(f"    {s['date']}: (500 0건)")
            continue
        lines.append(
            f"    {s['date']}: peak {s['peak_second']}  "
            f"{s['peak_second_count']}건/초  (공식 한도 대비 "
            f"{s['peak_second_count']/RATE_LIMIT_REAL_QPS*100:.1f}%)"
        )
    lines.append("")
    lines.append("  ⚠️ 주의: 위 수치는 '실패(5xx) 응답' 빈도만. 실제 우리 측 QPS 는 성공 호출 포함.")
    lines.append("     성공 호출은 kis.py::_request 에 로그 없음 (무음) → 호출 패턴 역산 필요:")
    lines.append("       ① _ratio_updater: 1초 × N종목 (R-13 후)")
    lines.append("       ② 시세 폴링: WS 기반이 주, REST 는 screener/startup")
    lines.append("       ③ 체결통보: WS 이벤트 기반 (REST 아님)")
    lines.append("     N종목 × 1 QPS 가 초당 peak 에 가까우면 우리 측 경로 의심.")
    lines.append("")

    # [5] Case 판정 가이드
    lines.append("[5] Case 판정 가이드")
    lines.append(bar)
    lines.append("  Case A (KIS 인프라 상시):")
    lines.append("    ✓ 재시도 2/2 소진율 ≈ 0 (대부분 1차 재시도로 복구)")
    lines.append("    ✓ peak QPS << 20 (공식 한도 여유)")
    lines.append("    ✓ TR 분포 분산 (한 TR 집중 아님)")
    lines.append("    → P1-1 관찰 유지 + 로그 노이즈 조정만 검토 (E3 결론)")
    lines.append("")
    lines.append("  Case B (우리 측 호출 과다):")
    lines.append("    ✓ 특정 TR 이 peak 시간에 집중")
    lines.append("    ✓ peak QPS 가 공식 한도 근접/초과")
    lines.append("    ✓ 2/2 소진율 ↑ (지속적 실패)")
    lines.append("    → _ratio_updater 폴링 완화 / 캐싱 / M-HTTP500-FIX 설계 필요")
    lines.append("")
    lines.append("  Case C (장 개시 또는 특정 시간대 집중):")
    lines.append("    ✓ pre_screening 또는 core 초반 집중")
    lines.append("    ✓ 나머지 시간대는 여유")
    lines.append("    → 해당 시간대 호출 연기 또는 재시도 백오프 증가")
    lines.append("")
    lines.append(sep)

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/diag_http500_analyze.py <log1> [<log2> ...]")
        sys.exit(1)

    all_stats: list[dict] = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"⚠️ 파일 없음: {p}", file=sys.stderr)
            continue
        print(f"분석 중: {p}", file=sys.stderr)
        all_stats.append(analyze_file(p))

    if not all_stats:
        print("분석 가능한 파일 없음", file=sys.stderr)
        sys.exit(1)

    all_stats.sort(key=lambda s: s["date"] or "")
    report = format_report(all_stats)
    print(report)

    out_dir = Path(__file__).resolve().parent.parent / "logs" / "diag"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"http500_report_{ts}.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n리포트 저장: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
