#!/usr/bin/env python3
"""W-31 WebSocket runtime log 분석 (D-1 / 2026-04-23).

§4.8 산출물:
  - code × layer groupby tick 통계 (count, median, p95, max_gap)
  - KRX_only vs KRX_NXT 그룹 비교
  - WS prpr max vs REST rest_high 시점별 괴리
  - 011930 신성이엔지 09:54~09:56 경계 tick 추적
  - §5 판정 매트릭스 입력값 산출
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

OBSERVE_CODES: dict[str, tuple[str, str]] = {
    "001250": ("GS글로벌",   "KRX_only"),
    "011930": ("신성이엔지", "KRX_only"),
    "092190": ("KEC",        "KRX_only"),
    "097230": ("HJ중공업",   "KRX_only"),
    "278470": ("에이피알",   "KRX_NXT"),
    "010140": ("삼성중공업", "KRX_NXT"),
}

LAYERS = ("ws_handler", "coord_route", "watcher_tick", "rest_snapshot")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_records(path: Path) -> dict[str, dict[str, list[dict]]]:
    """파일 1회 스트리밍, layer × code 별 list 반환."""
    bucket: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    n_total = 0
    n_bad = 0
    for line in path.open(encoding="utf-8"):
        n_total += 1
        try:
            r = json.loads(line)
        except Exception:
            n_bad += 1
            continue
        layer = r.get("layer")
        code = r.get("code")
        if not layer or not code:
            n_bad += 1
            continue
        try:
            r["_ts"] = parse_ts(r["ts"])
        except Exception:
            n_bad += 1
            continue
        bucket[layer][code].append(r)
    print(f"[Load] total={n_total:,}  bad={n_bad:,}  layers={sorted(bucket.keys())}")
    return bucket


def percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def tick_stats(records: list[dict]) -> dict:
    """ts 정렬 후 인접 간격(초) 통계."""
    if len(records) < 2:
        return {"count": len(records), "median_s": 0.0, "p95_s": 0.0, "max_gap_s": 0.0}
    ts_sorted = sorted(r["_ts"] for r in records)
    intervals = [(ts_sorted[i] - ts_sorted[i - 1]).total_seconds() for i in range(1, len(ts_sorted))]
    intervals_sorted = sorted(intervals)
    return {
        "count": len(records),
        "median_s": statistics.median(intervals),
        "p95_s": percentile(intervals_sorted, 95),
        "max_gap_s": max(intervals),
        "first_ts": ts_sorted[0].isoformat(),
        "last_ts": ts_sorted[-1].isoformat(),
    }


def section_per_code(bucket):
    print("\n" + "=" * 100)
    print("[1] 관측 6종목 × layer 통계")
    print("=" * 100)
    print(f"{'code':<8} {'name':<10} {'scope':<9} {'layer':<14} {'count':>7} {'med(s)':>8} {'p95(s)':>8} {'max_gap':>9}")
    print("-" * 100)
    for code, (name, scope) in OBSERVE_CODES.items():
        for layer in LAYERS:
            recs = bucket[layer].get(code, [])
            s = tick_stats(recs)
            print(
                f"{code:<8} {name:<10} {scope:<9} {layer:<14} "
                f"{s['count']:>7} {s['median_s']:>8.2f} {s['p95_s']:>8.2f} {s['max_gap_s']:>9.2f}"
            )
        print()


def section_group_compare(bucket):
    print("=" * 100)
    print("[2] 그룹 비교 (KRX_only vs KRX_NXT)")
    print("=" * 100)
    krx_only = [c for c, (_, s) in OBSERVE_CODES.items() if s == "KRX_only"]
    krx_nxt = [c for c, (_, s) in OBSERVE_CODES.items() if s == "KRX_NXT"]

    for layer in ("ws_handler", "coord_route", "watcher_tick"):
        print(f"\nLayer: {layer}")
        print(f"  {'group':<10} {'codes':<3} {'sum_count':>10} {'avg_count':>10} {'med(s)':>8} {'p95(s)':>8} {'max_gap':>9}")
        for label, codes in (("KRX_only", krx_only), ("KRX_NXT", krx_nxt)):
            counts = []
            all_intervals = []
            for code in codes:
                recs = bucket[layer].get(code, [])
                counts.append(len(recs))
                if len(recs) >= 2:
                    ts_sorted = sorted(r["_ts"] for r in recs)
                    all_intervals.extend((ts_sorted[i] - ts_sorted[i - 1]).total_seconds() for i in range(1, len(ts_sorted)))
            sum_c = sum(counts)
            avg_c = statistics.mean(counts) if counts else 0
            if all_intervals:
                ints_sorted = sorted(all_intervals)
                med = statistics.median(all_intervals)
                p95 = percentile(ints_sorted, 95)
                mx = max(all_intervals)
            else:
                med = p95 = mx = 0.0
            print(f"  {label:<10} {len(codes):<3} {sum_c:>10,} {avg_c:>10.0f} {med:>8.2f} {p95:>8.2f} {mx:>9.2f}")


def section_ws_vs_rest(bucket):
    print("\n" + "=" * 100)
    print("[3] WS intraday max prpr vs REST rest_high (관측 6종목)")
    print("=" * 100)
    print(f"{'code':<8} {'name':<10} {'scope':<9} {'ws_max':>8} {'rest_high':>10} {'rest_prpr_max':>14} {'ws-rest_high':>14}")
    print("-" * 100)
    for code, (name, scope) in OBSERVE_CODES.items():
        ws_recs = bucket["ws_handler"].get(code, [])
        rest_recs = bucket["rest_snapshot"].get(code, [])
        ws_max = max((r.get("prpr", 0) for r in ws_recs), default=0)
        rest_high = max((r.get("rest_high", 0) for r in rest_recs), default=0)
        rest_prpr_max = max((r.get("rest_prpr", 0) for r in rest_recs), default=0)
        gap = ws_max - rest_high if rest_high else None
        gap_str = f"{gap:>+}" if gap is not None else "N/A"
        print(f"{code:<8} {name:<10} {scope:<9} {ws_max:>8} {rest_high:>10} {rest_prpr_max:>14} {gap_str:>14}")


def section_011930_window(bucket):
    print("\n" + "=" * 100)
    print("[4] 011930 신성이엔지 09:54~09:56 ws_handler tick (극미 돌파 +10원 검증)")
    print("=" * 100)
    target = "011930"
    recs = bucket["ws_handler"].get(target, [])
    window = sorted(
        (r for r in recs if "2026-04-23T09:54:00" <= r["ts"] <= "2026-04-23T09:56:30"),
        key=lambda x: x["_ts"],
    )
    if not window:
        print("  (no tick in window)")
        return
    print(f"  {'ts':<27} {'prpr':>8} {'cntg_vol':>10} {'tick_time':>10} {'vi_stnd':>10}")
    print("-" * 80)
    prev_prpr = None
    for r in window:
        marker = ""
        if prev_prpr is not None and r["prpr"] != prev_prpr:
            marker = f"  Δ{r['prpr'] - prev_prpr:+d}"
        print(f"  {r['ts']:<27} {r['prpr']:>8} {r.get('cntg_vol',0):>10} {r.get('tick_time',''):>10} {str(r.get('vi_stnd_prc','')):>10}{marker}")
        prev_prpr = r["prpr"]
    intraday_max_in_win = max(r["prpr"] for r in window)
    print(f"\n  → 윈도우 내 ws_handler prpr 최대치: {intraday_max_in_win}")


def section_routing_consistency(bucket):
    """ws_handler vs coord_route 1:1 매칭 검증."""
    print("\n" + "=" * 100)
    print("[5] ws_handler vs coord_route 라우팅 일관성 (관측 6종목)")
    print("=" * 100)
    print(f"{'code':<8} {'name':<10} {'ws_count':>10} {'route_count':>12} {'diff':>8}")
    print("-" * 60)
    for code, (name, _) in OBSERVE_CODES.items():
        ws_n = len(bucket["ws_handler"].get(code, []))
        rt_n = len(bucket["coord_route"].get(code, []))
        diff = ws_n - rt_n
        print(f"{code:<8} {name:<10} {ws_n:>10} {rt_n:>12} {diff:>+8}")


def section_judgment(bucket):
    """§5 판정 매트릭스 입력값 산출."""
    print("\n" + "=" * 100)
    print("[6] §5 판정 매트릭스 입력값")
    print("=" * 100)

    krx_only = [c for c, (_, s) in OBSERVE_CODES.items() if s == "KRX_only"]
    krx_nxt = [c for c, (_, s) in OBSERVE_CODES.items() if s == "KRX_NXT"]

    def group_metrics(layer, codes):
        intervals = []
        counts = []
        for code in codes:
            recs = bucket[layer].get(code, [])
            counts.append(len(recs))
            if len(recs) >= 2:
                ts_sorted = sorted(r["_ts"] for r in recs)
                intervals.extend((ts_sorted[i] - ts_sorted[i - 1]).total_seconds() for i in range(1, len(ts_sorted)))
        if not intervals:
            return None
        ints_sorted = sorted(intervals)
        return {
            "avg_count": statistics.mean(counts),
            "median": statistics.median(intervals),
            "p95": percentile(ints_sorted, 95),
            "max_gap": max(intervals),
            "ge_5sec_gap": sum(1 for x in intervals if x >= 5.0),
        }

    for layer in ("ws_handler", "watcher_tick"):
        print(f"\nLayer: {layer}")
        a = group_metrics(layer, krx_only)
        b = group_metrics(layer, krx_nxt)
        if not a or not b:
            print("  (insufficient data)")
            continue
        print(f"  KRX_only:  avg_count={a['avg_count']:>6.0f}  median={a['median']:.3f}s  p95={a['p95']:.3f}s  max_gap={a['max_gap']:.2f}s  gap≥5s={a['ge_5sec_gap']}")
        print(f"  KRX_NXT:   avg_count={b['avg_count']:>6.0f}  median={b['median']:.3f}s  p95={b['p95']:.3f}s  max_gap={b['max_gap']:.2f}s  gap≥5s={b['ge_5sec_gap']}")
        ratio = a["median"] / b["median"] if b["median"] > 0 else float("inf")
        print(f"  median ratio (KRX_only / KRX_NXT) = {ratio:.2f}x  (§5 H-WS-1 임계: ≥2.0x or gap≥5s 발견)")

    # H-WS-2: WS prpr max vs REST rest_high 괴리
    print("\nH-WS-2 검증 (WS prpr max < REST rest_high):")
    violations = []
    for code, (name, scope) in OBSERVE_CODES.items():
        ws_max = max((r.get("prpr", 0) for r in bucket["ws_handler"].get(code, [])), default=0)
        rest_high = max((r.get("rest_high", 0) for r in bucket["rest_snapshot"].get(code, [])), default=0)
        if rest_high > 0 and ws_max < rest_high:
            violations.append((code, name, scope, ws_max, rest_high, rest_high - ws_max))
    if violations:
        print(f"  [!] 위반 {len(violations)}건:")
        for code, name, scope, w, r, diff in violations:
            print(f"    {code} {name} ({scope}): ws_max={w} < rest_high={r}  diff={diff}")
    else:
        print("  [OK] 위반 0건 (모든 종목 ws_max >= rest_high)")


def section_screening_unknown_check(bucket):
    """매매 스크리닝 종목 (listing_scope=unknown) 중 KRX_only 의심군 색출.

    스크리닝 종목 = watcher_tick 레이어에 등장한 unique code.
    KRX_only 의심 = ws_handler tick 0건 또는 비정상적으로 적음.
    """
    print("\n" + "=" * 100)
    print("[7] 매매 스크리닝 종목 중 KRX_only 의심군 (★ systemic risk 검증)")
    print("=" * 100)

    screening_codes = set(bucket["watcher_tick"].keys())
    print(f"  스크리닝 종목 수: {len(screening_codes)}")

    rows = []
    for code in screening_codes:
        ws_recs = bucket["ws_handler"].get(code, [])
        wt_recs = bucket["watcher_tick"].get(code, [])
        ws_n = len(ws_recs)
        wt_n = len(wt_recs)
        first = wt_recs[0] if wt_recs else {}
        name = first.get("name", "?")
        market = first.get("market_type", "?")
        scope = first.get("listing_scope", "?")
        rows.append((code, name, market, scope, ws_n, wt_n))

    # 1. ws_handler 0건 = KRX_only 강력 의심
    zero_ws = [r for r in rows if r[4] == 0]
    print(f"\n  [A] ws_handler tick 0건 종목: {len(zero_ws)}  ★ KRX_only 강력 의심")
    if zero_ws:
        print(f"      {'code':<8} {'name':<14} {'market':<8} {'scope':<10} {'ws_n':>7} {'wt_n':>7}")
        for code, name, market, scope, ws_n, wt_n in sorted(zero_ws):
            print(f"      {code:<8} {name:<14} {market:<8} {scope:<10} {ws_n:>7} {wt_n:>7}")
    else:
        print("      (none)")

    # 2. 전체 분포 (ws_handler count 적은 순)
    rows_sorted = sorted(rows, key=lambda r: r[4])
    print(f"\n  [B] 전체 스크리닝 종목 (ws_handler 적은 순):")
    print(f"      {'code':<8} {'name':<14} {'market':<8} {'scope':<10} {'ws_n':>7} {'wt_n':>7}")
    for code, name, market, scope, ws_n, wt_n in rows_sorted:
        print(f"      {code:<8} {name:<14} {market:<8} {scope:<10} {ws_n:>7} {wt_n:>7}")


def main(jsonl_path: str) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = Path(jsonl_path)
    if not p.exists():
        print(f"ERROR: {p} not found", file=sys.stderr)
        return 1
    print(f"[File] {p}  ({p.stat().st_size / 1e6:.1f} MB)")
    bucket = load_records(p)
    section_per_code(bucket)
    section_group_compare(bucket)
    section_routing_consistency(bucket)
    section_ws_vs_rest(bucket)
    section_011930_window(bucket)
    section_judgment(bucket)
    section_screening_unknown_check(bucket)
    return 0


if __name__ == "__main__":
    default_path = r"C:\Users\terryn\AUTOTRADE\logs\ws_runtime\ws_tick_2026-04-23.jsonl"
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else default_path))
