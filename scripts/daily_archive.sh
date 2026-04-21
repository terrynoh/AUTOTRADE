#!/bin/bash
# daily_archive.sh — 매일 15:35 cron 실행, 당일 운영 로그 전체 압축 보관
# crontab: 35 15 * * 1-5 /home/ubuntu/AUTOTRADE/scripts/daily_archive.sh >> /home/ubuntu/AUTOTRADE/logs/cron_archive.log 2>&1
set -euo pipefail

PROJECT=/home/ubuntu/AUTOTRADE
ARCHIVE_DIR="$PROJECT/archive"
LOG_DIR="$PROJECT/logs"
DATA_DIR="$PROJECT/data"
SERVICE=autotrade
TODAY=$(date +%Y-%m-%d)
OUTPUT="$ARCHIVE_DIR/${TODAY}.tar.gz"
WORK_DIR=$(mktemp -d /tmp/autotrade_archive_XXXXXX)

cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

mkdir -p "$ARCHIVE_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== AUTOTRADE daily_archive.sh START ==="
log "Date: $TODAY  Output: $OUTPUT"

# --- 1. App logs ---
log "[1/5] App logs"
cp "$LOG_DIR/autotrade_${TODAY}.log" "$WORK_DIR/" 2>/dev/null \
  || { log "WARN: autotrade_${TODAY}.log not found (placeholder)"; touch "$WORK_DIR/autotrade_${TODAY}.log"; }
cp "$LOG_DIR/trades_${TODAY}.log" "$WORK_DIR/" 2>/dev/null \
  || { log "WARN: trades_${TODAY}.log not found (placeholder)"; touch "$WORK_DIR/trades_${TODAY}.log"; }
cp "$DATA_DIR/trades.db" "$WORK_DIR/" 2>/dev/null \
  || { log "WARN: trades.db not found (placeholder)"; touch "$WORK_DIR/trades.db"; }

# --- 2. Journal logs ---
log "[2/5] systemd journal"
{ sudo journalctl -u "$SERVICE" --since today --no-pager 2>&1 || true; } \
  > "$WORK_DIR/journal_today.log"
{ sudo journalctl -u "$SERVICE" -b --no-pager 2>&1 || true; } \
  > "$WORK_DIR/journal_boot.log"

# --- 3. Ops snapshot ---
log "[3/5] Ops snapshot"
{
  echo "=== systemctl status $SERVICE ==="
  systemctl status "$SERVICE" --no-pager 2>&1 || true
  echo ""
  echo "=== uptime & load ==="
  uptime; cat /proc/loadavg
  echo ""
  echo "=== memory (free -h) ==="
  free -h
  echo ""
  echo "=== disk (df -h) ==="
  df -h /
  echo ""
  echo "=== process (autotrade) ==="
  ps aux | grep -i autotrade | grep -v grep || true
  echo ""
  echo "=== token cache metadata ==="
  ls -la "$PROJECT/token_live.json" 2>/dev/null || echo "token_live.json: not found"
  echo ""
  echo "=== dmesg (last 50 lines) ==="
  sudo dmesg -T 2>&1 | tail -50 || true
} > "$WORK_DIR/ops_snapshot.txt" 2>&1

# --- 4. Service restart history (24h) ---
log "[4/5] Service restart history"
{ sudo journalctl -u "$SERVICE" --since "24 hours ago" --no-pager 2>/dev/null || true; } \
  | { grep -E 'Started|Stopped|Stopping|Starting|Failed|Main process exited|SIGTERM|SIGKILL|OOM' || true; } \
  | tail -50 > "$WORK_DIR/service_restart_history.log"

# --- 5. Manifest ---
log "[5/5] manifest.json"
AT_VERSION=$(grep -m1 '버전' "$PROJECT/CLAUDE.md" 2>/dev/null | grep -oE 'v[A-Za-z0-9.]+' | head -1 || echo "unknown")

WORK_DIR="$WORK_DIR" TODAY="$TODAY" AT_VERSION="$AT_VERSION" python3 - << 'PYEOF'
import json, hashlib, os
from datetime import datetime, timezone

work_dir = os.environ["WORK_DIR"]
today    = os.environ["TODAY"]
at_ver   = os.environ.get("AT_VERSION", "unknown")

files = sorted(f for f in os.listdir(work_dir) if os.path.isfile(os.path.join(work_dir, f)))
sizes, hashes = {}, {}
for fname in files:
    path = os.path.join(work_dir, fname)
    sizes[fname] = os.path.getsize(path)
    with open(path, "rb") as fh:
        hashes[fname] = hashlib.sha256(fh.read()).hexdigest()

manifest = {
    "date": today,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "file_sizes": sizes,
    "sha256": hashes,
    "git_commit": "N/A",
    "autotrade_version": at_ver,
}
with open(os.path.join(work_dir, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
print("manifest.json OK")
PYEOF

# --- 아카이브 생성 (기존 파일 overwrite) ---
tar -czf "$OUTPUT" -C "$WORK_DIR" .
log "Archive size: $(du -sh "$OUTPUT" | cut -f1)"

# --- 30일 보존 정책 ---
find "$ARCHIVE_DIR" -name "*.tar.gz" -mtime +30 -delete
log "Retention cleanup done."

# --- systemd stdout/stderr 초기화 (journal에 이미 포함, 디스크 누수 방지) ---
truncate -s 0 "$LOG_DIR/autotrade.log" 2>/dev/null || true
truncate -s 0 "$LOG_DIR/autotrade.err" 2>/dev/null || true
log "Truncated autotrade.log / autotrade.err"

log "=== Done: $OUTPUT ==="
