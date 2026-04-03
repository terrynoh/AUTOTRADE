"""
로깅 설정 — loguru 기반.

콘솔 + 파일 로그. 일별 로테이션.
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logger(log_level: str = "DEBUG", log_dir: str | Path | None = None):
    """
    로거 초기화.

    Args:
        log_level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR)
        log_dir: 로그 파일 디렉토리 (None이면 프로젝트/logs/)
    """
    # 기본 핸들러 제거
    logger.remove()

    if log_dir is None:
        log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # 콘솔 출력 (컬러)
    logger.add(
        sys.stdout,
        level=log_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # 전체 로그 파일 (일별 로테이션)
    logger.add(
        log_dir / "autotrade_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="00:00",       # 매일 자정 로테이션
        retention="30 days",
        encoding="utf-8",
    )

    # 거래 전용 로그 (매수/매도/손절만)
    logger.add(
        log_dir / "trades_{time:YYYY-MM-DD}.log",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
        filter=lambda record: any(
            kw in record["message"]
            for kw in ["매수", "매도", "손절", "청산", "체결", "DCA", "P&L"]
        ),
        rotation="00:00",
        retention="90 days",
        encoding="utf-8",
    )

    # 에러 전용 로그
    logger.add(
        log_dir / "errors_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}\n{exception}",
        rotation="00:00",
        retention="90 days",
        encoding="utf-8",
    )

    logger.info(f"로거 초기화 완료 (level={log_level}, dir={log_dir})")
