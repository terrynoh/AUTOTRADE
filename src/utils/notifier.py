"""
텔레그램 알림 — 매수/매도/손절/일일 리포트 등.
텔레그램 명령 수신 — /target, /clear 명령 처리.

python-telegram-bot v20+ (async) 사용.
Qt 이벤트 루프와 공존하기 위해 동기 방식으로 래핑.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Optional, Callable

from loguru import logger

try:
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot 미설치 — 텔레그램 알림 비활성")


class Notifier:
    """텔레그램 알림 발송."""

    def __init__(self, bot_token: str = "", chat_id: str = "", stock_master=None):
        self.bot_token = bot_token
        # 쉼표로 구분된 복수 ID 지원: "111111,222222,333333"
        self._chat_ids: list[str] = [c.strip() for c in chat_id.split(",") if c.strip()]
        self._stock_master = stock_master
        self._bot: Optional[Bot] = None

        if TELEGRAM_AVAILABLE and bot_token and self._chat_ids:
            self._bot = Bot(token=bot_token)
            logger.info(f"텔레그램 알림 활성화 ({len(self._chat_ids)}명)")
        else:
            logger.info("텔레그램 알림 비활성 (토큰 미설정 또는 라이브러리 미설치)")

    def _send(self, message: str) -> None:
        """메시지 전송 (등록된 모든 ID에 발송). 실행 중인 루프 유무에 따라 자동 분기."""
        if self._bot is None:
            logger.debug(f"[알림 미전송] {message}")
            return

        async def _send_all():
            for chat_id in self._chat_ids:
                try:
                    await self._bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"텔레그램 전송 실패 (ID:{chat_id}): {e}")

        try:
            loop = asyncio.get_running_loop()
            # 이미 실행 중인 루프가 있으면 fire-and-forget 태스크로 예약
            loop.create_task(_send_all())
        except RuntimeError:
            # 실행 중인 루프 없음 → 새 루프로 동기 실행
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_send_all())
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

    # ── 텔레그램 명령 수신 ─────────────────────────────────────

    def setup_commands(
        self,
        on_target: Optional[Callable[[list[str]], None]] = None,
        on_clear: Optional[Callable[[], None]] = None,
        on_get_targets: Optional[Callable[[], list[str]]] = None,
        on_screen: Optional[Callable[[], asyncio.Future]] = None,
        on_status: Optional[Callable[[], dict]] = None,
        on_stop: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        텔레그램 명령 핸들러 설정.

        on_target: /target 006400,247540 → 종목코드 리스트 콜백
        on_clear: /clear → 종목코드 초기화 콜백
        on_get_targets: /target (인자 없음) → 현재 종목코드 조회 콜백
        on_screen: /screen → 수동 스크리닝 실행 (async 콜백)
        on_status: /status → 현재 상태 조회 콜백
        on_stop: /stop → 매매 중단 콜백
        """
        self._on_target = on_target
        self._on_clear = on_clear
        self._on_get_targets = on_get_targets
        self._on_screen = on_screen
        self._on_status = on_status
        self._on_stop = on_stop

    async def start_polling(self) -> None:
        """텔레그램 봇 폴링 시작 (명령 수신용). asyncio 태스크로 실행."""
        if not TELEGRAM_AVAILABLE or not self.bot_token or not self._chat_ids:
            logger.info("텔레그램 명령 수신 비활성 (토큰 미설정 또는 라이브러리 미설치)")
            return

        try:
            self._app = Application.builder().token(self.bot_token).build()

            # 명령 핸들러 등록
            self._app.add_handler(CommandHandler("target", self._cmd_target))
            self._app.add_handler(CommandHandler("clear", self._cmd_clear))
            self._app.add_handler(CommandHandler("screen", self._cmd_screen))
            self._app.add_handler(CommandHandler("status", self._cmd_status))
            self._app.add_handler(CommandHandler("stop", self._cmd_stop))
            self._app.add_handler(CommandHandler("help", self._cmd_help))

            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
            logger.info("텔레그램 명령 수신 시작 (polling)")
        except Exception as e:
            logger.error(f"텔레그램 폴링 시작 실패: {e}")

    async def stop_polling(self) -> None:
        """텔레그램 봇 폴링 중지."""
        if hasattr(self, '_app') and self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
                logger.info("텔레그램 명령 수신 중지")
            except Exception as e:
                logger.warning(f"텔레그램 폴링 중지 실패: {e}")

    def _is_authorized(self, chat_id: int) -> bool:
        """발신자가 등록된 chat_id인지 확인."""
        return str(chat_id) in self._chat_ids

    async def _cmd_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        /target 명령 처리.

        /target 006400,247540,020150 → 종목코드 설정
        /target 삼성SDI,에코프로비엠 → 종목명도 지원
        /target → 현재 설정된 종목코드 조회
        """
        if not update.message or not self._is_authorized(update.message.chat_id):
            if update.message:
                logger.warning(
                    f"비인가 텔레그램 명령 시도: chat_id={update.message.chat_id}"
                )
            return

        args_text = update.message.text.replace("/target", "").strip()

        if not args_text:
            # 인자 없음 → 현재 종목코드 조회
            if hasattr(self, '_on_get_targets') and self._on_get_targets:
                codes = self._on_get_targets()
                if codes:
                    await update.message.reply_text(
                        f"현재 타겟 종목: {', '.join(codes)} ({len(codes)}종목)"
                    )
                else:
                    await update.message.reply_text("설정된 타겟 종목 없음")
            else:
                await update.message.reply_text("설정된 타겟 종목 없음")
            return

        # 종목코드/종목명 파싱: 쉼표로 분리
        inputs = [c.strip() for c in args_text.split(",") if c.strip()]

        if not inputs:
            await update.message.reply_text("사용법: /target 006400,삼성SDI,에코프로비엠")
            return

        # 종목명/종목코드 변환 (StockMaster whitelist 패턴)
        resolved = []
        invalid = []
        if self._stock_master is not None:
            for inp in inputs:
                code = self._stock_master.lookup_code(inp)
                if code:
                    resolved.append(code)
                else:
                    invalid.append(inp)
        else:
            resolved = inputs

        if invalid:
            await update.message.reply_text(
                f"인식 불가 종목: {', '.join(invalid)}"
            )
            if not resolved:
                return

        # 콜백 호출
        if hasattr(self, '_on_target') and self._on_target:
            self._on_target(resolved)
            await update.message.reply_text(
                f"✅ 타겟 종목 설정: {', '.join(resolved)} ({len(resolved)}종목)"
            )
        else:
            await update.message.reply_text("명령 처리 불가 (핸들러 미설정)")

    async def _cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/clear 명령 처리 — 수동 종목코드 초기화."""
        if not update.message or not self._is_authorized(update.message.chat_id):
            if update.message:
                logger.warning(
                    f"비인가 텔레그램 명령 시도: chat_id={update.message.chat_id}"
                )
            return

        if hasattr(self, '_on_clear') and self._on_clear:
            self._on_clear()
            await update.message.reply_text("✅ 타겟 종목 초기화 완료")
        else:
            await update.message.reply_text("명령 처리 불가 (핸들러 미설정)")

    async def _cmd_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/screen 명령 — 수동 스크리닝 실행."""
        if not update.message or not self._is_authorized(update.message.chat_id):
            if update.message:
                logger.warning(
                    f"비인가 텔레그램 명령 시도: chat_id={update.message.chat_id}"
                )
            return

        if hasattr(self, '_on_screen') and self._on_screen:
            await update.message.reply_text("🔍 스크리닝 실행 중...")
            try:
                result = self._on_screen()
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    result = await result
                if result:
                    lines = [f"📊 <b>스크리닝 결과: {len(result)}종목</b>"]
                    for t in result:
                        s = t.stock
                        lines.append(
                            f"  ★ {s.name}({s.code}) {s.market.value}\n"
                            f"    등락 {s.price_change_pct:+.2f}%"
                        )
                    await update.message.reply_text(
                        "\n".join(lines), parse_mode="HTML"
                    )
                else:
                    await update.message.reply_text("스크리닝 결과 없음 — 조건 충족 종목 없음")
            except Exception as e:
                await update.message.reply_text(f"❌ 스크리닝 실패: {e}")
        else:
            await update.message.reply_text("명령 처리 불가 (핸들러 미설정)")

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/status 명령 — 현재 매매 상태 조회."""
        if not update.message or not self._is_authorized(update.message.chat_id):
            if update.message:
                logger.warning(
                    f"비인가 텔레그램 명령 시도: chat_id={update.message.chat_id}"
                )
            return

        if hasattr(self, '_on_status') and self._on_status:
            try:
                info = self._on_status()
                lines = [
                    f"📋 <b>AUTOTRADE 상태</b>",
                    f"모드: {info.get('trade_mode', 'N/A')}",
                    f"예수금: {info.get('available_cash', 0):,}원",
                    f"당일 매매: {info.get('daily_trades', 0)}회",
                    f"당일 P&L: {info.get('daily_pnl', 0):+,.0f}원",
                ]
                targets = info.get('manual_codes', [])
                if targets:
                    lines.append(f"타겟 종목: {', '.join(targets)}")
                monitors = info.get('monitors', [])
                if monitors:
                    lines.append(f"\n<b>감시 중 ({len(monitors)}종목)</b>")
                    for m in monitors:
                        lines.append(
                            f"  {m['name']}({m['code']}) "
                            f"상태={m['state']} "
                            f"고가={m.get('intraday_high', 0):,}"
                        )
                await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            except Exception as e:
                await update.message.reply_text(f"❌ 상태 조회 실패: {e}")
        else:
            await update.message.reply_text("명령 처리 불가 (핸들러 미설정)")

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/stop 명령 — 당일 매매 중단."""
        if not update.message or not self._is_authorized(update.message.chat_id):
            if update.message:
                logger.warning(
                    f"비인가 텔레그램 명령 시도: chat_id={update.message.chat_id}"
                )
            return

        if hasattr(self, '_on_stop') and self._on_stop:
            self._on_stop()
            await update.message.reply_text("🛑 당일 매매 중단됨. 미체결 주문 취소 처리 중.")
        else:
            await update.message.reply_text("명령 처리 불가 (핸들러 미설정)")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/help 명령 — 사용 가능한 명령어 안내."""
        if not update.message or not self._is_authorized(update.message.chat_id):
            if update.message:
                logger.warning(
                    f"비인가 텔레그램 명령 시도: chat_id={update.message.chat_id}"
                )
            return

        msg = (
            "📖 <b>AUTOTRADE 텔레그램 명령어</b>\n\n"
            "<b>종목 설정</b>\n"
            "/target 006400,247540 — 종목코드로 설정\n"
            "/target 삼성SDI,에코프로비엠 — 종목명으로 설정\n"
            "/target — 현재 설정 종목 조회\n"
            "/clear — 종목 설정 초기화\n\n"
            "<b>매매 제어</b>\n"
            "/screen — 스크리닝 실행 (설정된 종목 검증)\n"
            "/stop — 당일 매매 중단\n\n"
            "<b>모니터링</b>\n"
            "/status — 현재 상태 조회\n"
            "/help — 이 도움말"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
