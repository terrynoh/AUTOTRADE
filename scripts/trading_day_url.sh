#!/bin/bash
# 06:40 cron — 거래일이면 send_dashboard_url.py 실행, 비거래일이면 skip
# 책임: URL 발송 전담. service 기동은 trading_day_start.sh 가 담당.

set -e
cd /home/ubuntu/AUTOTRADE
source venv/bin/activate

if python scripts/check_trading_day.py; then
    python scripts/send_dashboard_url.py
    logger -t autotrade_cron "W-17 거래일 확인 → URL 발송 완료"
else
    logger -t autotrade_cron "W-17 비거래일 → URL 발송 건너뜀"
fi
