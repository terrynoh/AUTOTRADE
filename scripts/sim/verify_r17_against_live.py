"""R-17 검증 — 운영 ws_tick 로그를 R-17 ChannelResolver 가설과 대조.

가설 (R-17 evidence):
  - H0UNCNT0 (UN) 은 NXT 활성 종목만 송신
  - KRX_only 종목 → UN 에서 tick 0건 → R-17 의 ChannelResolver 가 ST 채택

검증 데이터:
  - logs/ws_runtime/ws_tick_2026-04-23.jsonl (운영 H0UNCNT0 단일 채널 수신 raw)
  - logs/ws_runtime/nxt_codes.txt (PDF 25.03.31, 795 NXT 종목)
  - logs/ws_runtime/krx_only_codes.txt (1981 KRX 단독 종목)

분석:
  1. ws_handler layer 의 종목별 tick 수
  2. nxt_codes / krx_only_codes 와 cross-reference
  3. KRX_only 인데 UN tick 수신한 종목 → R-17 가설 위반 후보 식별
  4. listing_scope=unknown 종목의 실제 분류
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

LOGS = Path(r"C:\Users\terryn\AUTOTRADE\logs\ws_runtime")
WS_TICK = LOGS / "ws_tick_2026-04-23.jsonl"
NXT_FILE = LOGS / "nxt_codes.txt"
KRX_ONLY_FILE = LOGS / "krx_only_codes.txt"

# === 종목 분류 데이터 로드 ===
nxt_set: set[str] = set()
with NXT_FILE.open(encoding="utf-8") as f:
    for line in f:
        code = line.strip().split("\t")[0].split()[0] if line.strip() else None
        if code and code.isdigit():
            nxt_set.add(code.zfill(6))

krx_only_set: set[str] = set()
with KRX_ONLY_FILE.open(encoding="utf-8") as f:
    for line in f:
        code = line.strip().split("\t")[0].split()[0] if line.strip() else None
        if code and code.isdigit():
            krx_only_set.add(code.zfill(6))

print(f"[데이터] NXT 종목: {len(nxt_set)}개")
print(f"[데이터] KRX_only 종목: {len(krx_only_set)}개")
print(f"[데이터] 교집합: {len(nxt_set & krx_only_set)}개 (모순 — 정상 0이어야 함)")
print()

# === ws_handler tick 수집 (종목별) ===
# 메모리: 종목당 카운트만 보관, raw 안 보관
ws_handler_count: Counter = Counter()         # code → tick 수
coord_route_count: Counter = Counter()        # code → 라우팅 수
watcher_tick_count: Counter = Counter()       # code → watcher 도달
listing_scope_per_code: dict[str, set] = defaultdict(set)  # code → {scope1, scope2, ...}

# layer 별 종목 unique
unique_codes_per_layer: dict[str, set] = defaultdict(set)

with WS_TICK.open(encoding="utf-8") as f:
    for line in f:
        try:
            o = json.loads(line)
        except Exception:
            continue
        layer = o.get("layer")
        code = o.get("code")
        scope = o.get("listing_scope")
        if not code:
            continue
        unique_codes_per_layer[layer].add(code)
        if scope:
            listing_scope_per_code[code].add(scope)
        if layer == "ws_handler":
            ws_handler_count[code] += 1
        elif layer == "coord_route":
            coord_route_count[code] += 1
        elif layer == "watcher_tick":
            watcher_tick_count[code] += 1

print("=== layer 별 unique 종목 수 ===")
for layer, codes in unique_codes_per_layer.items():
    print(f"  {layer}: {len(codes)}개")
print()

# === ws_handler 수신 종목을 분류 그룹화 ===
ws_codes = set(ws_handler_count.keys())

group_nxt = ws_codes & nxt_set
group_krx_only = ws_codes & krx_only_set
group_unknown = ws_codes - nxt_set - krx_only_set

print("=== ws_handler 수신 종목 — 분류 데이터 cross-reference ===")
print(f"  총 수신 종목: {len(ws_codes)}개")
print(f"  ├ NXT 활성 (nxt_codes 매핑): {len(group_nxt)}개")
print(f"  ├ KRX_only (krx_only_codes 매핑): {len(group_krx_only)}개")
print(f"  └ unknown (양쪽 데이터 없음): {len(group_unknown)}개")
print()

# === 각 그룹의 tick 분포 통계 ===
def stats(codes_subset, counter):
    if not codes_subset:
        return None
    counts = [counter[c] for c in codes_subset]
    counts.sort()
    n = len(counts)
    return {
        "n": n,
        "total_tick": sum(counts),
        "mean": sum(counts) / n,
        "median": counts[n // 2],
        "min": counts[0],
        "max": counts[-1],
        "zero_count": sum(1 for c in counts if c == 0),
    }

print("=== 그룹별 tick 분포 (ws_handler) ===")
for label, gset in [
    ("NXT 활성", group_nxt),
    ("KRX_only", group_krx_only),
    ("unknown", group_unknown),
]:
    s = stats(gset, ws_handler_count)
    if s is None:
        continue
    print(f"\n[{label}] n={s['n']}")
    print(f"  total_tick = {s['total_tick']:,}")
    print(f"  mean = {s['mean']:.1f} / median = {s['median']} / min = {s['min']} / max = {s['max']:,}")

# === KRX_only 인데 UN 에서 tick 받은 종목 — R-17 가설 위반 후보 ===
print("\n\n=== KRX_only 종목인데 UN(H0UNCNT0) tick 수신 (R-17 가설 위반 후보) ===")
violators = sorted(group_krx_only, key=lambda c: -ws_handler_count[c])
print(f"위반 후보 종목: {len(violators)}개 (총 tick {sum(ws_handler_count[c] for c in violators):,})")
print(f"\n상위 20 종목:")
print(f"{'rank':>4} {'code':>8} {'tick':>10} {'listing_scope':<20}")
for i, c in enumerate(violators[:20], 1):
    scopes = ",".join(sorted(listing_scope_per_code[c]))
    print(f"{i:>4} {c:>8} {ws_handler_count[c]:>10,} {scopes:<20}")

# === unknown 중에서 tick 많이 받은 종목 ===
print("\n\n=== listing_scope=unknown 중 상위 tick 수신 종목 ===")
unknowns = sorted(group_unknown, key=lambda c: -ws_handler_count[c])
print(f"unknown 종목 수: {len(unknowns)} / 총 tick {sum(ws_handler_count[c] for c in unknowns):,}")
print(f"\n상위 20:")
for i, c in enumerate(unknowns[:20], 1):
    scopes = ",".join(sorted(listing_scope_per_code[c]))
    print(f"{i:>4} {c:>8} {ws_handler_count[c]:>10,} {scopes:<20}")

# === 종합 결론 ===
print("\n\n=== R-17 가설 검증 결론 ===")
nxt_total = sum(ws_handler_count[c] for c in group_nxt)
krx_only_total = sum(ws_handler_count[c] for c in group_krx_only)
unknown_total = sum(ws_handler_count[c] for c in group_unknown)
total = nxt_total + krx_only_total + unknown_total

print(f"  H0UNCNT0 총 수신 tick: {total:,}")
print(f"  ├ NXT 활성 종목 수신: {nxt_total:,} ({nxt_total / total * 100:.1f}%)")
print(f"  ├ KRX_only 종목 수신: {krx_only_total:,} ({krx_only_total / total * 100:.1f}%)")
print(f"  └ unknown 종목 수신: {unknown_total:,} ({unknown_total / total * 100:.1f}%)")
print()
zero_krx_only = sum(1 for c in group_krx_only if ws_handler_count[c] == 0)
nonzero_krx_only = len(group_krx_only) - zero_krx_only
print(f"  KRX_only 종목 중 tick > 0: {nonzero_krx_only}/{len(group_krx_only)} ({nonzero_krx_only / max(len(group_krx_only), 1) * 100:.1f}%)")
print(f"    → R-17 evidence 의 88.2% 0건율 vs 라이브 검증 결과 비교 필요")
