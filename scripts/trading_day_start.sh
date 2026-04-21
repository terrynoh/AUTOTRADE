#!/bin/bash
# 06:35 cron — 거래일이면 autotrade.service 시작, 비거래일이면 skip
# 책임: service 기동 전담. URL 발송은 trading_day_url.sh 가 담당.

set -e
cd /home/ubuntu/AUTOTRADE
source venv/bin/activate

if python scripts/check_trading_day.py; then
    /usr/bin/sudo /bin/systemctl start autotrade
    logger -t autotrade_cron "W-17 거래일 확인 → autotrade.service start"
else
    logger -t autotrade_cron "W-17 비거래일 → autotrade.service 시작 건너뜀"
fi
