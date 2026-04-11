#!/usr/bin/env python
"""
거래일이면 exit 0, 비거래일이면 exit 1.
cron wrapper 용. is_trading_day() 는 pykrx 기반 KRX 캘린더 (공휴일 자동).

사용처:
  - scripts/trading_day_start.sh (06:35 service 시작 전 필터)
  - scripts/trading_day_url.sh (06:40 URL 발송 전 필터)
"""
import sys
from pathlib import Path

# cron 에서 직접 실행되므로 sys.path 에 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.market_calendar import is_trading_day

if __name__ == "__main__":
    sys.exit(0 if is_trading_day() else 1)
