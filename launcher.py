"""
AUTOTRADE 런처 — 매일 09:00 KST 자동 시작 + 대시보드 URL 텔레그램 발송.

용도:
  - Windows 시작 프로그램 또는 작업 스케줄러에 등록하여 PC 부팅 시 자동 실행
  - 매일 09:00 KST에 대시보드 서버 시작 + Cloudflare Tunnel URL 텔레그램 발송
  - 기존 프로세스 정리 후 시작 (포트 충돌 방지)

사용법:
  python launcher.py              # 즉시 시작 (대기 없음)
  python launcher.py --schedule   # 매일 09:00 KST에 자동 시작 (데몬)
"""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time as _time
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from loguru import logger

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def _kill_port(port: int) -> None:
    """지정 포트를 점유 중인 프로세스 종료 (Windows)."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit() and int(pid) > 0:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        logger.info(f"포트 {port} 점유 프로세스 종료 (PID={pid})")
                        _time.sleep(1)
                    except (OSError, ProcessLookupError):
                        pass
    except Exception as e:
        logger.debug(f"포트 정리 실패: {e}")


def _is_port_available(port: int) -> bool:
    """포트가 사용 가능한지 체크."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind(("0.0.0.0", port))
            return True
    except OSError:
        return False


async def launch_dashboard() -> None:
    """대시보드 서버 시작 + Cloudflare Tunnel + 텔레그램 URL 발송."""
    from config.settings import Settings
    from src.utils.notifier import Notifier
    from src.utils.tunnel import CloudflareTunnel

    settings = Settings()
    port = settings.dashboard_port

    # 1. 기존 프로세스 정리
    if not _is_port_available(port):
        logger.warning(f"포트 {port} 사용 중 — 기존 프로세스 정리")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _kill_port, port)
        await asyncio.sleep(2)

        if not _is_port_available(port):
            logger.error(f"포트 {port} 여전히 사용 중 — 시작 불가")
            return

    # 2. 대시보드 서버 시작 (subprocess)
    logger.info(f"대시보드 서버 시작 (port={port})")
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.dashboard.app"],
        cwd=str(Path(__file__).resolve().parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    logger.info(f"대시보드 PID={proc.pid}")

    # 서버 시작 대기
    for _ in range(15):
        await asyncio.sleep(1)
        if not _is_port_available(port):
            break
    else:
        logger.error("대시보드 서버 시작 실패 (15초 타임아웃)")
        return

    # 3. Cloudflare Tunnel 시작
    tunnel = CloudflareTunnel(port=port)
    tunnel_url = await tunnel.start()

    # 4. 텔레그램 발송
    notifier = Notifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    admin_token = os.getenv("DASHBOARD_ADMIN_TOKEN", "")
    now = now_kst().strftime("%Y-%m-%d %H:%M")

    if tunnel_url:
        admin_url = f"{tunnel_url}?token={admin_token}" if admin_token else tunnel_url
        msg = (
            f"🖥️ <b>AUTOTRADE 대시보드 시작</b>\n"
            f"시각: {now} KST\n"
            f"모드: {settings.trade_mode}\n\n"
            f"📎 관리자 접속:\n{admin_url}\n\n"
            f"📎 읽기 전용:\n{tunnel_url}"
        )
    else:
        msg = (
            f"🖥️ <b>AUTOTRADE 대시보드 시작</b>\n"
            f"시각: {now} KST\n"
            f"모드: {settings.trade_mode}\n\n"
            f"📎 로컬 접속: http://localhost:{port}\n"
            f"⚠️ Cloudflare Tunnel 실패 — 원격 접속 불가"
        )

    notifier.notify_system(msg)
    logger.info("텔레그램 URL 발송 완료")

    # 5. 프로세스 유지 (tunnel + dashboard)
    try:
        while True:
            # 대시보드 프로세스 생존 체크
            if proc.poll() is not None:
                logger.error(f"대시보드 프로세스 종료됨 (exit={proc.returncode})")
                break
            await asyncio.sleep(30)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await tunnel.stop()
        proc.terminate()
        logger.info("런처 종료")


async def schedule_loop() -> None:
    """매일 09:00 KST에 launch_dashboard 실행."""
    while True:
        now = now_kst()
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)

        # 이미 09:00 이후면 내일 09:00
        if now >= target:
            target += timedelta(days=1)

        wait_sec = (target - now).total_seconds()
        logger.info(f"다음 시작: {target.strftime('%Y-%m-%d %H:%M KST')} ({wait_sec/3600:.1f}시간 후)")
        await asyncio.sleep(wait_sec)

        logger.info("=" * 50)
        logger.info("09:00 KST — 대시보드 자동 시작")
        logger.info("=" * 50)
        await launch_dashboard()


if __name__ == "__main__":
    if "--schedule" in sys.argv:
        logger.info("AUTOTRADE 런처 (스케줄 모드) — 매일 09:00 KST 자동 시작")
        asyncio.run(schedule_loop())
    else:
        logger.info("AUTOTRADE 런처 — 즉시 시작")
        asyncio.run(launch_dashboard())
