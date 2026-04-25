#!/usr/bin/env bash
# verify_dashboard_r16_dead.sh
# 목적: R16 LIVE-only 전환 후 대시보드 mode-badge / trade_mode 관련
#       dead 코드 잔존 검증 + 외부 의존성 확인.
# 실행: bash scripts/verify_dashboard_r16_dead.sh
# 권장 시점: 월요일 (2026-04-27) 장 시작 후 dashboard 가동 중.

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DASHBOARD_PORT="${DASHBOARD_PORT:-8503}"
PASS=0; FAIL=0; INFO=0

green() { printf '\e[32m%s\e[0m\n' "$1"; }
red()   { printf '\e[31m%s\e[0m\n' "$1"; }
yel()   { printf '\e[33m%s\e[0m\n' "$1"; }
hr()    { printf '%.0s-' {1..70}; echo; }

ok()   { green "[ PASS ] $1"; PASS=$((PASS+1)); }
ng()   { red   "[ FAIL ] $1"; FAIL=$((FAIL+1)); }
note() { yel   "[ INFO ] $1"; INFO=$((INFO+1)); }

cd "$ROOT" || { echo "ROOT 진입 실패"; exit 2; }

echo "=== R16 dead 검증 시작: $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "ROOT = $ROOT"
echo "PORT = $DASHBOARD_PORT"
hr

# ─────────────────────────────────────────────
# 1. 정적: index.html dead 라인 존재 확인
# ─────────────────────────────────────────────
echo "[1/6] index.html dead 라인 grep"
HTML="src/dashboard/templates/index.html"

if grep -nE "mode-dry_run|mode-paper" "$HTML" >/dev/null; then
  COUNT=$(grep -cE "mode-dry_run|mode-paper" "$HTML")
  note "mode-dry_run / mode-paper 잔존 ${COUNT}건"
  grep -nE "mode-dry_run|mode-paper" "$HTML" | sed 's/^/    /'
else
  ok "mode-dry_run / mode-paper 0건"
fi

if grep -nE "\.mode-live" "$HTML" >/dev/null; then
  ok ".mode-live CSS 룰 존재 ($(grep -nE "\.mode-live" "$HTML"))"
else
  note ".mode-live CSS 룰 없음 (안 B/C 진행 시 추가 필요)"
fi
hr

# ─────────────────────────────────────────────
# 2. 정적: app.py trade_mode 필드 잔존
# ─────────────────────────────────────────────
echo "[2/6] app.py trade_mode 필드 grep"
APP="src/dashboard/app.py"
TRADE_MODE_LINES=$(grep -cE "trade_mode" "$APP" || true)
note "app.py trade_mode 참조 ${TRADE_MODE_LINES}건"
grep -nE "trade_mode" "$APP" | sed 's/^/    /'
hr

# ─────────────────────────────────────────────
# 3. 정적: 외부 클라이언트 trade_mode 키 의존
# ─────────────────────────────────────────────
echo "[3/6] 외부 (scripts/, src/, tests/) trade_mode 키 의존"
EXT_HITS=$(grep -rnE "trade_mode" --include='*.py' --include='*.sh' --include='*.html' --include='*.js' \
  scripts/ src/ tests/ 2>/dev/null | grep -v "src/dashboard/" | grep -v "config/settings.py" || true)
if [[ -z "$EXT_HITS" ]]; then
  ok "외부 trade_mode 의존 0건 → 안 C 안전"
else
  CNT=$(echo "$EXT_HITS" | wc -l | tr -d ' ')
  note "외부 의존 ${CNT}건 (안 C 진행 전 검토)"
  echo "$EXT_HITS" | sed 's/^/    /'
fi
hr

# ─────────────────────────────────────────────
# 4. 정적: R16 폐기 키워드 전수
# ─────────────────────────────────────────────
echo "[4/6] R16 폐기 키워드 잔존 검사"
KEYWORDS=("DRY_RUN" "USE_LIVE_API" "DRY_RUN_CASH" "KIS_ACCOUNT_NO_PAPER" "is_paper_mode" "fill_manager" "cash_manager" "simulator")
for kw in "${KEYWORDS[@]}"; do
  HITS=$(grep -rnE "\b${kw}\b" --include='*.py' --include='*.html' --include='*.js' \
    src/ scripts/ tests/ 2>/dev/null | grep -v "/__pycache__/" | grep -v "\.pre-staleness" | grep -v "\.bak_" | grep -v "/_archive/" || true)
  if [[ -z "$HITS" ]]; then
    ok "${kw} 0건"
  else
    CNT=$(echo "$HITS" | wc -l | tr -d ' ')
    note "${kw} ${CNT}건"
    echo "$HITS" | sed 's/^/    /' | head -10
    [[ $CNT -gt 10 ]] && echo "    ... (+$((CNT-10))건 생략)"
  fi
done
hr

# ─────────────────────────────────────────────
# 5. 정적: settings.py 의 trade_mode property 잔존 (호환 목적)
# ─────────────────────────────────────────────
echo "[5/6] settings.py trade_mode / is_live property 잔존 확인"
if grep -nE "(def trade_mode|def is_live)" config/settings.py >/dev/null 2>&1; then
  note "config/settings.py 에 호환 property 잔존 (R16 정리 후 의도된 보존)"
  grep -nE "(def trade_mode|def is_live)" config/settings.py | sed 's/^/    /'
else
  ok "config/settings.py 에 호환 property 없음"
fi
hr

# ─────────────────────────────────────────────
# 6. 동적: 가동 중 dashboard 의 trade_mode 송신값 (curl)
# ─────────────────────────────────────────────
echo "[6/6] 동적: dashboard /state API trade_mode 값"
ENDPOINT="http://localhost:${DASHBOARD_PORT}/state"
if command -v curl >/dev/null 2>&1; then
  RESP=$(curl -fsS --max-time 3 "$ENDPOINT" 2>/dev/null || true)
  if [[ -z "$RESP" ]]; then
    note "dashboard 미가동 (curl 실패) — 월요일 장중 재실행 필요. ENDPOINT=$ENDPOINT"
  else
    if echo "$RESP" | grep -qE '"trade_mode"'; then
      VAL=$(echo "$RESP" | grep -oE '"trade_mode"\s*:\s*"[^"]*"' | head -1)
      note "송신 trade_mode 값: $VAL (LIVE-only 면 \"live\" 고정 예상)"
    else
      ok "송신 payload 에 trade_mode 키 없음 (이미 정리됨)"
    fi
  fi
else
  note "curl 미설치 — 동적 검증 skip"
fi
hr

# ─────────────────────────────────────────────
# 결과 요약 + 안 결정 가이드
# ─────────────────────────────────────────────
echo "=== 결과 요약 ==="
echo "  PASS=${PASS}  FAIL=${FAIL}  INFO=${INFO}"
echo
echo "=== 안 결정 가이드 ==="
echo "  안 A (CSS 만 정리)           : 항상 안전. dead CSS 2 라인 + HTML 초기값 1 라인."
echo "  안 B (A + JS 분기 정리)      : 항상 안전. JS 분기 제거, app.py 필드 유지."
echo "  안 C (mode-badge 전체 제거)  : [3/6] 외부 의존 0건 + [6/6] 송신값 \"live\" 고정 시에만 안전."
echo
echo "  → [3/6] 결과 + [6/6] 결과 종합 후 판단."
echo "=== 종료: $(date '+%Y-%m-%d %H:%M:%S') ==="

exit 0
