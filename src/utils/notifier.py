"""
텔레그램 알림 — 매수/매도/손절/일일 리포트 등.

python-telegram-bot v20+ (async) 사용.
Qt 이벤트 루프와 공존하기 위해 동기 방식으로 래핑.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger

try:
    from telegram import Bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot 미설치 — 텔레그램 알림 비활성")


class Notifier:
    """텔레그램 알림 발송."""

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._bot: Optional[Bot] = None

        if TELEGRAM_AVAILABLE and bot_token and chat_id:
            self._bot = Bot(token=bot_token)
            logger.info("텔레그램 알림 활성화")
        else:
            logger.info("텔레그램 알림 비활성 (토큰 미설정 또는 라이브러리 미설치)")

    def _send(self, message: str) -> None:
        """동기 방식 메시지 전송."""
        if self._bot is None:
            logger.debug(f"[알림 미전송] {message}")
            return

        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                self._bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode="HTML",
                )
            )
            loop.close()
        except Exception as e:
            logger.error(f"텔레그램 전송 실패: {e}")

    # ── 알림 타입별 메서드 ──────────────────────────────────────

    def notify_screening_result(self, targets: list, candidates_count: int) -> None:
        """스크리닝 결과 알림."""
        if not targets:
            msg = f"📊 11시 스크리닝 완료\n후보 {candidates_count}종목 중 타겟 없음"
        else:
            lines = [f"📊 <b>11시 스크리닝 완료</b>"]
            for t in targets:
                s = t.stock
                lines.append(
                    f"  ★ {s.name}({s.code}) {s.market.value}\n"
                    f"    등락 {s.price_change_pct:+.2f}% | "
                    f"프로그램비중 {s.program_net_buy_ratio:.1f}%"
                )
            msg = "\n".join(lines)

        self._send(msg)

    def notify_entry(self, name: str, price: int, qty: int, label: str) -> None:
        """매수 알림."""
        emoji = "🟢" if label == "initial" else "🔵"
        msg = (
            f"{emoji} <b>매수 ({label})</b>\n"
            f"{name} | {qty}주 @ {price:,}원\n"
            f"금액: {price * qty:,}원"
        )
        self._send(msg)

    def notify_exit(self, name: str, price: int, qty: int, reason: str, pnl: float, pnl_pct: float) -> None:
        """매도/청산 알림."""
        emoji = "🎯" if pnl > 0 else "🔴"
        msg = (
            f"{emoji} <b>매도 ({reason})</b>\n"
            f"{name} | {qty}주 @ {price:,}원\n"
            f"P&L: {pnl:+,.0f}원 ({pnl_pct:+.2f}%)"
        )
        self._send(msg)

    def notify_skip(self, name: str, reason: str) -> None:
        """매매 미진입 알림."""
        msg = f"⏭️ {name}: {reason}"
        self._send(msg)

    def notify_error(self, error_msg: str) -> None:
        """에러 알림."""
        msg = f"🚨 <b>에러 발생</b>\n{error_msg}"
        self._send(msg)

    def notify_daily_report(
        self,
        trade_date: str,
        mode: str,
        total_trades: int,
        winning: int,
        losing: int,
        total_pnl: float,
        details: str = "",
    ) -> None:
        """일일 리포트."""
        win_rate = (winning / (winning + losing) * 100) if (winning + losing) > 0 else 0
        emoji = "📈" if total_pnl >= 0 else "📉"

        msg = (
            f"{emoji} <b>일일 리포트</b> ({trade_date})\n"
            f"모드: {mode}\n"
            f"매매: {total_trades}건 (승 {winning} / 패 {losing})\n"
            f"승률: {win_rate:.0f}%\n"
            f"총 P&L: {total_pnl:+,.0f}원"
        )
        if details:
            msg += f"\n\n{details}"

        self._send(msg)

    def notify_system(self, message: str) -> None:
        """시스템 메시지."""
        self._send(f"ℹ️ {message}")
