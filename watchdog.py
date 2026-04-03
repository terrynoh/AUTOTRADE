"""
AUTOTRADE 워치독 — 프로세스 감시 + 자동 재시작.

사용법:
    python watchdog.py

동작:
    1. src.main을 자식 프로세스로 실행
    2. 프로세스 종료 감지 → 즉시 자동 재시작
    3. 비정상 종료 시 텔레그램 긴급 알림
    4. 15:35 이후 재시작 중단 (장 종료)
    5. 연속 5회 비정상 종료 시 중단 (근본 문제 판단)
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, time as dt_time

from loguru import logger

# 설정
MAX_RESTART = 5          # 연속 비정상 종료 최대 허용 횟수
RESTART_DELAY = 5        # 재시작 대기 시간 (초)
MARKET_CLOSE = dt_time(15, 35)  # 이 시각 이후 재시작 안 함


def send_telegram_alert(message: str) -> None:
    """텔레그램 긴급 알림 (별도 프로세스에서 전송)."""
    try:
        import os
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            return

        import urllib.request
        import urllib.parse
        url = (
            f"https://api.telegram.org/bot{bot_token}/sendMessage?"
            f"chat_id={chat_id}&text={urllib.parse.quote(message)}"
        )
        urllib.request.urlopen(url, timeout=10)
    except Exception as e:
        logger.error(f"텔레그램 알림 실패: {e}")


def main():
    logger.info("=" * 50)
    logger.info("AUTOTRADE 워치독 시작")
    logger.info(f"최대 연속 재시작: {MAX_RESTART}회")
    logger.info(f"장 종료 후 중단: {MARKET_CLOSE}")
    logger.info("=" * 50)

    consecutive_failures = 0

    while True:
        # 장 종료 후 재시작 중단
        if datetime.now().time() > MARKET_CLOSE:
            logger.info("장 종료 시각 경과 — 워치독 종료")
            break

        # 연속 비정상 종료 한도 체크
        if consecutive_failures >= MAX_RESTART:
            msg = f"🚨 AUTOTRADE 연속 {MAX_RESTART}회 비정상 종료 — 워치독 중단. 수동 확인 필요!"
            logger.critical(msg)
            send_telegram_alert(msg)
            break

        # 메인 프로세스 실행
        logger.info(f"AUTOTRADE 프로세스 시작 (재시작 #{consecutive_failures})")
        start_time = time.time()

        try:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "src.main"],
                cwd=str(__import__("pathlib").Path(__file__).parent),
            )
            exit_code = result.returncode
        except KeyboardInterrupt:
            logger.info("워치독 수동 종료 (Ctrl+C)")
            break
        except Exception as e:
            logger.error(f"프로세스 실행 실패: {e}")
            exit_code = -1

        elapsed = time.time() - start_time

        # 정상 종료 (exit code 0 또는 장 마감 후)
        if exit_code == 0:
            logger.info(f"AUTOTRADE 정상 종료 (실행 {elapsed:.0f}초)")
            consecutive_failures = 0

            if datetime.now().time() > MARKET_CLOSE:
                logger.info("장 종료 — 워치독 종료")
                break
            continue

        # 비정상 종료
        consecutive_failures += 1
        msg = (
            f"🚨 AUTOTRADE 비정상 종료!\n"
            f"exit code: {exit_code}\n"
            f"실행 시간: {elapsed:.0f}초\n"
            f"연속 실패: {consecutive_failures}/{MAX_RESTART}\n"
            f"{RESTART_DELAY}초 후 재시작"
        )
        logger.warning(msg)
        send_telegram_alert(msg)

        # 너무 빨리 죽는 경우 (1분 미만) → 대기 시간 증가
        if elapsed < 60:
            wait = RESTART_DELAY * consecutive_failures
            logger.info(f"빠른 종료 감지 — {wait}초 대기 후 재시작")
            time.sleep(wait)
        else:
            # 오래 실행 후 죽은 경우 → 일시적 문제일 가능성
            consecutive_failures = max(0, consecutive_failures - 1)
            time.sleep(RESTART_DELAY)

    logger.info("워치독 종료")


if __name__ == "__main__":
    main()
