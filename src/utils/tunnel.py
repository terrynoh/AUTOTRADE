"""
Cloudflare Quick Tunnel — 대시보드 원격 접속 URL 자동 발급 + 텔레그램 발송.

봇 시작 시 cloudflared를 백그라운드로 실행하고,
출력에서 trycloudflare.com URL을 캡처해 텔레그램으로 발송한다.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

_URL_PATTERN = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")

# winget 설치 경로 포함 후보 목록
def _build_cloudflared_candidates() -> list[str]:
    """OS 환경에 맞는 cloudflared 후보 경로 생성."""
    import os
    candidates = ["cloudflared", r"C:\Program Files\cloudflared\cloudflared.exe"]
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        winget_path = os.path.join(
            local_app,
            "Microsoft", "WinGet", "Packages",
            "Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe",
            "cloudflared.exe",
        )
        candidates.append(winget_path)
    return candidates

_CLOUDFLARED_CANDIDATES = _build_cloudflared_candidates()


def _find_cloudflared() -> Optional[str]:
    """실행 가능한 cloudflared 경로 반환."""
    import shutil
    for candidate in _CLOUDFLARED_CANDIDATES:
        if shutil.which(candidate) or (candidate != "cloudflared" and Path(candidate).exists()):
            return candidate
    return None


class CloudflareTunnel:
    """cloudflared quick tunnel 래퍼."""

    def __init__(self, port: int = 0):
        import os
        port = port or int(os.getenv("DASHBOARD_PORT", "8503"))
        self.port = port
        self._proc: Optional[asyncio.subprocess.Process] = None
        self.url: Optional[str] = None

    async def start(self) -> Optional[str]:
        """터널 시작 → URL 반환 (최대 30초 대기). 실패 시 None."""
        exe = _find_cloudflared()
        if not exe:
            logger.warning("cloudflared 미설치 — 원격 모니터링 비활성")
            return None
        try:
            self._proc = await asyncio.create_subprocess_exec(
                exe, "tunnel", "--url", f"http://localhost:{self.port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("cloudflared 실행 실패 — 원격 모니터링 비활성")
            return None

        # stderr에서 URL 캡처 (cloudflared는 URL을 stderr로 출력)
        self.url = await self._capture_url(timeout=30)
        if self.url:
            logger.info(f"Cloudflare Tunnel 시작: {self.url}")
        else:
            logger.warning("Cloudflare Tunnel URL 캡처 실패")
        return self.url

    async def _capture_url(self, timeout: int) -> Optional[str]:
        """stderr 스트림에서 trycloudflare.com URL 추출."""
        assert self._proc and self._proc.stderr
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                line = await asyncio.wait_for(
                    self._proc.stderr.readline(), timeout=2.0
                )
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            text = line.decode(errors="ignore")
            m = _URL_PATTERN.search(text)
            if m:
                return m.group()
        return None

    async def stop(self) -> None:
        """터널 프로세스 종료."""
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                pass
            self._proc = None
        self.url = None
