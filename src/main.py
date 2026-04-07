"""
AUTOTRADE 메인 진입점.

asyncio 기반으로 KIS REST/WebSocket 위에서
전체 매매 프로세스를 오케스트레이션한다.

멀티 트레이드:
  - 09:50 스크리닝 → 후보 풀 최대 5종목 선정
  - 1번 종목부터 신고가 감시 → 매수 → 청산
  - 수익 청산 시 10:00~11:00 이내면 다음 후보로 전환
  - 일일 최대 3회, 손실 시 당일 중단

타임라인:
  09:00  장 시작 → 토큰 발급, 계좌 확인
  09:50  스크리닝 → 후보 풀 선정
  09:55~ 신고가 감시 + 매수 대기
  수익청산 → 다음 후보 (10:00~11:00, 최대 3회)
  15:20  강제 청산
  15:30  장 마감 리포트
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import Settings, StrategyParams
from src.kis_api.kis import KISAPI
from src.kis_api.constants import WS_TR_PRICE, WS_TR_FUTURES
from src.core.screener import Screener
from src.core.monitor import TargetMonitor, MonitorState
from src.core.trader import Trader
from src.core.risk_manager import RiskManager
from src.models.stock import TradeTarget
from src.models.trade import TradeRecord, DailySummary, ExitReason
from src.storage.database import Database
from src.utils.notifier import Notifier
from src.utils.tunnel import CloudflareTunnel
from src.utils.market_calendar import now_kst



class AutoTrader:
    """AUTOTRADE 메인 오케스트레이터."""

    def __init__(self):
        self.settings = Settings()
        self.params = StrategyParams.load()
        self.api = KISAPI(
            app_key=self.settings.kis_app_key,
            app_secret=self.settings.kis_app_secret,
            account_no=self.settings.account_no,
            is_paper=self.settings.is_paper_mode,
            infra_params=self.params.infra,
        )
        self.screener = Screener(self.api, self.params, is_live=self.settings.is_live, use_live_data=self.settings.use_live_data)
        self.trader = Trader(self.api, self.settings, self.params)
        self.risk = RiskManager(self.params)

        # ── 모니터 관리 ──
        self._monitors: list[TargetMonitor] = []
        self._active_monitor: Optional[TargetMonitor] = None  # 현재 매매 중인 모니터
        self._subscribed_code: Optional[str] = None  # 현재 WebSocket 구독 중인 종목코드
        self._realtime_callback_registered: bool = False  # 실시간 콜백 등록 여부

        # ── 멀티 트레이드 ──
        self._candidate_pool: list[TradeTarget] = []   # 스크리닝 후보 풀
        self._candidate_index: int = 0                  # 현재 후보 인덱스
        self._completed_codes: set[str] = set()         # 이미 매매 완료된 종목코드

        # ── 수동 종목 입력 ──
        self._manual_codes: list[str] = []

        # ── 거래 기록 (멀티 트레이드 시 즉시 저장) ──
        self._trade_records: list[TradeRecord] = []

        # ── 기타 ──
        self._available_cash: int = 0
        self._initial_cash: int = 0
        self._futures_price: float = 0.0
        self._running: bool = False
        self._network_ok: bool = True       # 네트워크 정상 여부
        self._emergency_cancel_done: bool = False  # 긴급 취소 실행 여부 (중복 방지)
        self.on_state_update = None  # 대시보드 동기화 콜백 (run.py에서 설정)

        # ── 텔레그램 알림 ──
        self.notifier = Notifier(
            bot_token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
        )

        # ── DB ──
        self._db = Database()

        # ── Cloudflare Tunnel ──
        self._tunnel = CloudflareTunnel(port=self.settings.dashboard_port)

    # ── 수동 종목 설정 ────────────────────────────────────

    def set_manual_codes(self, codes: list[str]) -> None:
        """수동 타겟 종목코드 설정. 스크리닝 시 자동 스크리닝 대신 사용."""
        self._manual_codes = [c.strip() for c in codes if c.strip()]
        logger.info(f"수동 타겟 종목 설정: {', '.join(self._manual_codes)} ({len(self._manual_codes)}종목)")

    def clear_manual_codes(self) -> None:
        """수동 타겟 종목코드 초기화."""
        self._manual_codes = []
        logger.info("수동 타겟 종목 초기화")

    def get_manual_codes(self) -> list[str]:
        """현재 설정된 수동 타겟 종목코드 반환."""
        return list(self._manual_codes)

    def _get_status(self) -> dict:
        """현재 매매 상태 반환 (텔레그램 /status용)."""
        monitors = []
        for mon in self._monitors:
            t = mon.target
            monitors.append({
                "code": t.stock.code,
                "name": t.stock.name,
                "state": mon.state.value,
                "intraday_high": t.intraday_high,
            })
        return {
            "trade_mode": self.settings.trade_mode,
            "available_cash": self._available_cash,
            "daily_trades": self.risk.daily_trades,
            "daily_pnl": self.risk.daily_pnl,
            "manual_codes": self._manual_codes,
            "monitors": monitors,
        }

    def _stop_trading(self) -> None:
        """당일 매매 중단."""
        self._running = False
        logger.warning("텔레그램 /stop 명령 — 당일 매매 중단")
        self.notifier.notify_system("🛑 텔레그램 /stop 명령으로 매매 중단됨")

    # ── 메인 실행 ─────────────────────────────────────────

    async def run(self):
        """전체 매매 프로세스 실행."""
        logger.info("=" * 50)
        logger.info(f"AUTOTRADE 시작 (모드: {self.settings.trade_mode})")
        mt = self.params.multi_trade
        if mt.enabled:
            logger.info(f"멀티 트레이드: 최대 {mt.max_daily_trades}회, {mt.repeat_start}~{mt.repeat_end}")
        logger.info("=" * 50)
        self.notifier.notify_system(f"AUTOTRADE 시작 (모드: {self.settings.trade_mode})")

        # 텔레그램 명령 수신 설정
        self.notifier.setup_commands(
            on_target=self.set_manual_codes,
            on_clear=self.clear_manual_codes,
            on_get_targets=self.get_manual_codes,
            on_screen=self._on_screening,
            on_status=self._get_status,
            on_stop=self._stop_trading,
        )
        await self.notifier.start_polling()

        tasks: list[asyncio.Task] = []
        try:
            await self.api.connect()

            # 실시간 콜백 1회 등록 (중복 방지)
            if not self._realtime_callback_registered:
                self.api.add_realtime_callback(WS_TR_PRICE, self._on_realtime_price)
                self.api.add_realtime_callback(WS_TR_FUTURES, self._on_futures_price)
                self._realtime_callback_registered = True

            # WebSocket 끊김 콜백 등록 (1차 방어: 즉시 미체결 매수 취소)
            self.api.set_ws_disconnect_callback(self._on_ws_disconnect)

            # KOSPI200 선물 실시간 구독 (청산 조건 ④ 선물 급락용)
            try:
                await self.api.subscribe_futures()
            except Exception as e:
                logger.error(f"선물 구독 실패: {e}")

            # 계좌 잔고 확인
            if self.settings.is_dry_run and self.settings.dry_run_cash > 0:
                self._initial_cash = self.settings.dry_run_cash
                logger.info(f"[DRY_RUN] 가상 예수금 사용: {self._initial_cash:,}원")
            else:
                balance = await self.api.get_balance()
                self._initial_cash = balance.get("available_cash", 0)
            self._available_cash = self.risk.calculate_available_cash(self._initial_cash)
            logger.info(f"예수금: {self._initial_cash:,}원 → 매매가용: {self._available_cash:,}원")

            # 대시보드 서버 시작 (API 연결 + 잔고 확인 후 → attach_autotrader 가능)
            self._start_dashboard_server()
            await self._build_stock_name_cache()

            # Cloudflare Tunnel 시작 → URL 텔레그램 발송 (관리자 토큰 포함)
            tunnel_url = await self._tunnel.start()
            if tunnel_url:
                admin_token = os.getenv("DASHBOARD_ADMIN_TOKEN", "")
                admin_url = f"{tunnel_url}?token={admin_token}" if admin_token else tunnel_url
                self.notifier.notify_system(
                    f"대시보드 접속 URL (관리자)\n\n{admin_url}"
                )

            self._running = True
            await self._fire_state_update()

            # 스케줄 태스크 실행 — 하나라도 실패하면 나머지 취소
            tasks = [
                asyncio.create_task(self._schedule_screening(), name="screening"),
                asyncio.create_task(self._schedule_force_liquidate(), name="force_liquidate"),
                asyncio.create_task(self._schedule_market_close(), name="market_close"),
                asyncio.create_task(self._monitor_loop_runner(), name="monitor_loop"),
                asyncio.create_task(self._network_health_check(), name="health_check"),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

            # 실패한 태스크 예외 전파, 나머지 취소
            for t in pending:
                t.cancel()
            # pending 태스크가 CancelledError를 발생시킬 때까지 대기
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            # done 태스크 중 예외 확인
            for t in done:
                if t.exception() is not None:
                    raise t.exception()

        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("사용자 종료 (Ctrl+C)")
        except Exception as e:
            logger.critical(f"치명적 에러: {e}", exc_info=True)
            self.notifier.notify_error(f"치명적 에러 발생!\n{e}")
        finally:
            self._running = False
            # 아직 남아있는 태스크 정리
            for t in tasks:
                if not t.done():
                    t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.notifier.stop_polling()
            await self.api.disconnect()
            await self._tunnel.stop()
            logger.info("AUTOTRADE 종료")

    # ── 스케줄: 09:50 스크리닝 ────────────────────────────

    async def _schedule_screening(self):
        """09:50까지 대기 후 스크리닝 실행."""
        screening_time = time.fromisoformat(self.params.screening.screening_time)
        await self._wait_until(screening_time)
        await self._on_screening()

    async def _on_screening(self):
        """스크리닝 실행 → 후보 풀 선정 → 1번 종목으로 실시간 감시 시작."""
        if not self.risk.can_open_position(0):
            logger.warning("매매 불가 상태")
            return

        # 자동 스크리닝은 ISSUE-010 (수동 입력 방식 전환)으로 deprecated.
        # 종목 미입력 = 오늘 매매 안 함 (운영자에게 즉시 알림).
        if not self._manual_codes:
            logger.warning(
                "수동 입력 종목 없음 — 매매 진행 불가. "
                "자동 스크리닝은 ISSUE-010에 의해 deprecated."
            )
            self.notifier.notify_system(
                "⚠ 종목 미입력 → 오늘 매매 안 함\n\n"
                "텔레그램 /target 또는 대시보드에서 종목을 입력해주세요."
            )
            await self._fire_state_update()
            return

        logger.info(f"수동 스크리닝 실행 ({len(self._manual_codes)}종목)")
        targets = await self.screener.run_manual(self._manual_codes)

        if not targets:
            logger.info("스크리닝 결과 없음 — 당일 매매 안 함")
            await self._fire_state_update()
            return

        # 후보 풀 저장
        self._candidate_pool = targets
        self._candidate_index = 0
        self._completed_codes = set()

        mt = self.params.multi_trade
        if mt.enabled:
            logger.info(f"후보 풀 {len(targets)}종목 선정 (멀티 트레이드: 최대 {mt.max_daily_trades}회)")
            for i, t in enumerate(targets):
                logger.info(f"  #{i+1} {t.stock.name}({t.stock.code}) {t.stock.market.value} "
                            f"등락={t.stock.price_change_pct:+.2f}% 거래대금={t.stock.trading_volume_krw/1e8:.0f}억")
        else:
            logger.info(f"타겟 {len(targets)}종목 선정")

        # 1번 종목으로 감시 시작
        await self._start_monitoring_candidate(0)

    async def _start_monitoring_candidate(self, index: int) -> bool:
        """후보 풀에서 index번째 종목으로 감시 시작. 성공 시 True."""
        # 이미 매매 완료된 종목은 루프로 건너뛰기 (재귀 방지)
        while index < len(self._candidate_pool):
            target = self._candidate_pool[index]
            if target.stock.code not in self._completed_codes:
                break
            logger.info(f"[{target.stock.name}] 이미 매매 완료 → 다음 후보")
            index += 1
        else:
            logger.info("후보 풀 소진 — 추가 매매 불가")
            return False

        self._candidate_index = index

        # 이전 종목 WebSocket 구독 해제
        if self._subscribed_code:
            try:
                await self.api.unsubscribe_realtime([self._subscribed_code])
                logger.debug(f"이전 종목 구독 해제: {self._subscribed_code}")
            except Exception as e:
                logger.warning(f"이전 종목 구독 해제 실패: {e}")
            self._subscribed_code = None

        # 새 모니터 생성
        monitor = TargetMonitor(target, self.params)
        self._active_monitor = monitor
        self._monitors.append(monitor)

        code = target.stock.code

        # WebSocket 실시간 구독
        try:
            await self.api.subscribe_realtime([code])
            self._subscribed_code = code
            logger.info(f"[{target.stock.name}({code})] 실시간 감시 시작 (후보 #{index+1})")
        except Exception as e:
            logger.error(f"실시간 구독 실패: {e}")

        # 대시보드 상태 동기화
        await self._fire_state_update()

        return True

    # ── 멀티 트레이드: 수익 청산 후 다음 종목 ─────────────

    async def _try_next_candidate(self, exit_reason: str) -> None:
        """
        청산 후 다음 후보 진입 가능 여부 판단.
        - 멀티 트레이드 비활성화 → 패스
        - 손실 청산 & profit_only → 당일 중단
        - 최대 횟수 도달 → 중단
        - 시간 범위 밖 → 중단
        """
        mt = self.params.multi_trade
        if not mt.enabled:
            return

        # 수익 청산 여부 판단
        profit_reasons = {"target"}
        is_profit_exit = exit_reason in profit_reasons

        if mt.profit_only and not is_profit_exit:
            logger.info(f"손실/비수익 청산({exit_reason}) → 멀티 트레이드 중단 (당일 매매 종료)")
            return

        # 일일 최대 횟수 체크
        if self.risk.daily_trades >= mt.max_daily_trades:
            logger.info(f"일일 최대 매매 횟수 도달 ({self.risk.daily_trades}/{mt.max_daily_trades}) → 중단")
            return

        # 시간 범위 체크
        now = now_kst().time()
        repeat_start = time.fromisoformat(mt.repeat_start)
        repeat_end = time.fromisoformat(mt.repeat_end)

        if now < repeat_start or now > repeat_end:
            logger.info(f"멀티 트레이드 시간 범위 밖 ({mt.repeat_start}~{mt.repeat_end}) → 중단")
            return

        # Trader 초기화
        self.trader.reset()

        # 잔고 재확인
        try:
            if self.settings.is_dry_run and self.settings.dry_run_cash > 0:
                self._available_cash = self.risk.calculate_available_cash(self.settings.dry_run_cash)
                logger.info(f"[DRY_RUN] 가상 예수금 재설정: {self._available_cash:,}원")
            else:
                balance = await self.api.get_balance()
                self._available_cash = self.risk.calculate_available_cash(
                    balance.get("available_cash", 0)
                )
            logger.info(f"다음 매매 가용 예수금: {self._available_cash:,}원")
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return

        # 다음 후보 찾기
        next_index = self._candidate_index + 1
        logger.info(f"다음 후보 탐색 (#{next_index + 1}/{len(self._candidate_pool)})")

        if await self._start_monitoring_candidate(next_index):
            logger.info(f"멀티 트레이드 #{self.risk.daily_trades + 1}: "
                        f"{self._candidate_pool[self._candidate_index].stock.name} 감시 시작")
        else:
            logger.info("추가 후보 없음 — 당일 매매 종료")

    # ── 스케줄: 15:20 강제 청산 ───────────────────────────

    async def _schedule_force_liquidate(self):
        force_time = time.fromisoformat(self.params.exit.force_liquidate_time)
        await self._wait_until(force_time)

        for mon in self._monitors:
            if mon.state == MonitorState.ENTERED:
                mon.force_exit(now_kst())
            elif mon.state in (MonitorState.HIGH_CONFIRMED, MonitorState.TRACKING_HIGH):
                # 미매수 상태 → 주문 취소
                await self.trader.cancel_buy_orders(mon.target)
                mon.state = MonitorState.SKIPPED
                logger.info(f"[{mon.target.stock.name}] 15:20 미매수 → 주문 취소")

    # ── 스케줄: 15:30 장 마감 + 일일 리포트 ─────────────────

    async def _schedule_market_close(self):
        close_time = time.fromisoformat(self.params.market.close_time)
        await self._wait_until(close_time)
        logger.info("=" * 50)
        logger.info("장 마감 — 일일 리포트 생성")
        logger.info("=" * 50)
        self._running = False

        today = now_kst().date()

        # ── 1. 미진입 모니터의 NO_ENTRY 레코드 추가 + 이미 저장된 레코드 합산 ──
        recorded_codes = {r.code for r in self._trade_records}
        for mon in self._monitors:
            t = mon.target
            if t.stock.code not in recorded_codes:
                # 미진입 또는 아직 기록되지 않은 모니터
                record = self._build_trade_record(mon, today)
                self._trade_records.append(record)
                try:
                    self._db.save_trade(record)
                except Exception as e:
                    logger.error(f"거래 DB 저장 실패 ({t.stock.name}): {e}")

        trade_records = self._trade_records

        # ── 2. DailySummary 생성 + DB 저장 ──
        summary = DailySummary(
            summary_date=today,
            trade_mode=self.settings.trade_mode,
            candidates_count=len(self._candidate_pool),
            targets_count=len(self._monitors),
        )
        for record in trade_records:
            summary.add_trade(record)

        try:
            self._db.save_daily_summary(summary)
            logger.info(f"일일 요약 DB 저장 완료 ({today})")
        except Exception as e:
            logger.error(f"일일 요약 DB 저장 실패: {e}")

        # ── 3. 콘솔 로그 ──
        logger.info(f"당일 매매 횟수: {summary.total_trades}회 (승 {summary.winning_trades} / 패 {summary.losing_trades})")
        logger.info(f"당일 누적 P&L: {summary.total_pnl:+,.0f}원")

        for record in trade_records:
            if record.exit_reason == ExitReason.NO_ENTRY:
                logger.info(f"  [{record.name}] 매수 미발생")
            else:
                logger.info(
                    f"  [{record.name}] 평단 {record.avg_buy_price:,.0f}원 → "
                    f"청산 {record.avg_sell_price:,.0f}원 ({record.exit_reason.value}) "
                    f"P&L={record.pnl:+,.0f}원 ({record.pnl_pct:+.2f}%) "
                    f"보유 {record.holding_minutes:.0f}분"
                )

        # ── 4. 개선안 도출 ──
        analysis = self._generate_daily_analysis(trade_records, summary)

        # ── 5. 텔레그램 발송 ──
        details = self._format_report_details(trade_records, analysis)
        self.notifier.notify_daily_report(
            trade_date=str(today),
            mode=self.settings.trade_mode,
            total_trades=summary.total_trades,
            winning=summary.winning_trades,
            losing=summary.losing_trades,
            total_pnl=summary.total_pnl,
            details=details,
        )
        logger.info("일일 리포트 텔레그램 발송 완료")
        await self._fire_state_update()

    def _build_trade_record(self, mon: TargetMonitor, today) -> TradeRecord:
        """모니터에서 TradeRecord 생성."""
        t = mon.target
        pos = self.trader.position

        # 미진입
        if t.total_buy_qty <= 0:
            return TradeRecord(
                trade_date=today,
                code=t.stock.code,
                name=t.stock.name,
                market=t.stock.market.value,
                exit_reason=ExitReason.NO_ENTRY,
                rolling_high=t.intraday_high,
                trade_mode=self.settings.trade_mode,
            )

        # 매수/매도 데이터
        avg_buy = t.avg_price
        pnl = 0.0
        pnl_pct = 0.0
        avg_sell = 0.0
        sell_amount = 0
        sell_time = None
        holding_min = 0.0

        # 포지션에서 매도 데이터 추출
        if pos and pos.sell_orders:
            sell_amount = pos.total_sell_amount
            avg_sell = sell_amount / t.total_buy_qty if t.total_buy_qty > 0 else 0
            last_sell = pos.sell_orders[-1]
            sell_time = last_sell.filled_at
            pnl = sell_amount - t.total_buy_amount
            pnl_pct = (pnl / t.total_buy_amount * 100) if t.total_buy_amount > 0 else 0
        elif pos:
            # 미청산 (현재가 기준)
            pnl = pos.pnl(t.stock.current_price)
            pnl_pct = pos.pnl_pct(t.stock.current_price)
            avg_sell = t.stock.current_price

        # 보유 시간
        if pos and pos.opened_at and sell_time:
            holding_min = (sell_time - pos.opened_at).total_seconds() / 60
        elif pos and pos.opened_at:
            holding_min = (now_kst() - pos.opened_at).total_seconds() / 60

        # ExitReason 매핑
        reason_map = {
            "hard_stop": ExitReason.HARD_STOP,
            "timeout": ExitReason.TIMEOUT,
            "target": ExitReason.TARGET,
            "futures_stop": ExitReason.FUTURES_STOP,
            "force": ExitReason.FORCE_LIQUIDATE,
            "manual": ExitReason.MANUAL,
        }
        exit_reason = reason_map.get(t.exit_reason, ExitReason.NO_ENTRY)

        return TradeRecord(
            trade_date=today,
            code=t.stock.code,
            name=t.stock.name,
            market=t.stock.market.value,
            avg_buy_price=avg_buy,
            total_buy_qty=t.total_buy_qty,
            total_buy_amount=t.total_buy_amount,
            buy_count=int(t.buy1_filled) + int(t.buy2_filled),
            first_buy_time=pos.opened_at if pos else None,
            avg_sell_price=avg_sell,
            total_sell_amount=sell_amount,
            sell_time=sell_time,
            exit_reason=exit_reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_minutes=holding_min,
            rolling_high=t.intraday_high,
            entry_trigger_price=t.buy1_price(self.params),
            target_price=t.target_price,
            trade_mode=self.settings.trade_mode,
        )

    def _generate_daily_analysis(
        self, trades: list[TradeRecord], summary: DailySummary
    ) -> list[str]:
        """당일 매매 데이터 기반 개선안 도출."""
        suggestions: list[str] = []
        ep = self.params.entry
        xp = self.params.exit

        traded = [t for t in trades if t.exit_reason != ExitReason.NO_ENTRY]
        no_entry = [t for t in trades if t.exit_reason == ExitReason.NO_ENTRY]

        if not traded:
            if no_entry:
                suggestions.append("전 종목 눌림 미발생 → 매매 안 함. 매수 기준 완화 검토 또는 종목 선정 재검토")
            return suggestions

        # ── 청산 사유 분석 ──
        reason_stats: dict[str, list[float]] = {}
        for t in traded:
            key = t.exit_reason.value
            reason_stats.setdefault(key, []).append(t.pnl_pct)

        hard_stops = reason_stats.get("HARD_STOP", [])
        targets = reason_stats.get("TARGET", [])
        timeouts = reason_stats.get("TIMEOUT", [])
        futures_stops = reason_stats.get("FUTURES_STOP", [])

        # 하드 손절 비중 높을 때
        if len(hard_stops) > len(traded) * 0.5:
            suggestions.append(
                f"하드 손절 비중 과다 ({len(hard_stops)}/{len(traded)}건). "
                f"매수 기준 깊이를 넓히거나 손절 기준 완화 검토"
            )

        # 타임아웃 비중 높을 때
        if len(timeouts) > len(traded) * 0.5:
            avg_timeout_pnl = sum(timeouts) / len(timeouts) if timeouts else 0
            suggestions.append(
                f"타임아웃 비중 과다 ({len(timeouts)}/{len(traded)}건, 평균 {avg_timeout_pnl:+.2f}%). "
                f"현재 {xp.timeout_from_low_min}분 → 반등 시간 부족 시 연장 검토"
            )

        # 선물 급락 청산 발생 시
        if futures_stops:
            suggestions.append(
                f"선물 급락 청산 {len(futures_stops)}건 발생. "
                f"시장 전체 하락 리스크 → 종목 선정 시 선물 추세 확인 권장"
            )

        # ── 매수 체결 분석 ──
        buy1_only = sum(1 for t in traded if t.buy_count == 1)
        buy_both = sum(1 for t in traded if t.buy_count >= 2)

        if buy1_only > 0 and buy_both == 0:
            suggestions.append(
                f"전 매매 1차만 체결 (2차 미체결 {buy1_only}건). "
                f"눌림이 2차 기준까지 미도달 → 현 파라미터 유지 권장 (얕은 눌림에서 수익 확보)"
            )
        elif buy_both > 0 and len(hard_stops) > 0:
            suggestions.append(
                f"2차 매수 체결 {buy_both}건 중 손절 발생. "
                f"2차 매수 깊이가 손절선에 근접 → 간격 확대 검토"
            )

        # ── 보유 시간 분석 ──
        hold_times = [t.holding_minutes for t in traded if t.holding_minutes > 0]
        if hold_times:
            avg_hold = sum(hold_times) / len(hold_times)
            if avg_hold > xp.timeout_from_low_min * 0.8:
                suggestions.append(
                    f"평균 보유 {avg_hold:.0f}분, 타임아웃({xp.timeout_from_low_min}분) 근접. "
                    f"반등 속도 느린 종목군 → 타임아웃 연장 또는 종목 변경 검토"
                )
            elif avg_hold < 5 and targets:
                suggestions.append(
                    f"평균 보유 {avg_hold:.0f}분으로 매우 빠른 목표가 도달. "
                    f"목표가 상향 또는 분할 매도 검토"
                )

        # ── 미진입 종목 분석 ──
        if len(no_entry) >= len(trades) * 0.6 and len(trades) >= 3:
            suggestions.append(
                f"후보 {len(trades)}종목 중 {len(no_entry)}종목 미진입. "
                f"눌림 미발생 비율 높음 → 매수1 기준 완화 검토"
            )

        # ── 승률 기반 ──
        if summary.total_trades >= 3 and summary.win_rate < 50:
            suggestions.append(
                f"승률 {summary.win_rate:.0f}% (3건+ 기준 50% 미만). "
                f"종목 선정/진입 타이밍 재검토"
            )
        elif summary.total_trades >= 2 and summary.win_rate == 100:
            suggestions.append("전 매매 수익 — 현 파라미터 유지 권장")

        # ── 과거 대비 (DB에서 최근 5일 조회) ──
        try:
            stats = self._db.get_stats(days=7)
            if stats.get("total_trades", 0) >= 5:
                hist_win = stats.get("wins", 0)
                hist_total = stats.get("total_trades", 0)
                hist_rate = (hist_win / hist_total * 100) if hist_total else 0
                avg_pnl = stats.get("avg_pnl_pct", 0) or 0
                suggestions.append(
                    f"최근 7일 누적: {hist_total}건 승률 {hist_rate:.0f}% 평균수익 {avg_pnl:+.2f}%"
                )
        except Exception:
            pass

        if not suggestions:
            suggestions.append("특이사항 없음 — 현 파라미터 유지")

        return suggestions

    def _format_report_details(
        self, trades: list[TradeRecord], analysis: list[str]
    ) -> str:
        """텔레그램 발송용 상세 텍스트 포맷."""
        lines: list[str] = []

        # 거래 상세
        lines.append("<b>📋 거래 상세</b>")
        for i, t in enumerate(trades, 1):
            if t.exit_reason == ExitReason.NO_ENTRY:
                lines.append(f"  {i}. {t.name}({t.code}) — 눌림 미발생")
            else:
                emoji = "🟢" if t.pnl >= 0 else "🔴"
                lines.append(
                    f"  {i}. {emoji} {t.name}({t.code}) {t.market}\n"
                    f"     매수 {t.avg_buy_price:,.0f} → 청산 {t.avg_sell_price:,.0f} "
                    f"({t.exit_reason.value})\n"
                    f"     보유 {t.holding_minutes:.0f}분 | "
                    f"{t.pnl:+,.0f}원 ({t.pnl_pct:+.2f}%)"
                )

        # 청산 사유 집계
        reason_counts: dict[str, int] = {}
        for t in trades:
            if t.exit_reason != ExitReason.NO_ENTRY:
                key = t.exit_reason.value
                reason_counts[key] = reason_counts.get(key, 0) + 1
        if reason_counts:
            lines.append("\n<b>📊 청산 사유</b>")
            for reason, count in reason_counts.items():
                lines.append(f"  {reason}: {count}건")

        # 개선안
        if analysis:
            lines.append("\n<b>💡 개선안</b>")
            for s in analysis:
                lines.append(f"  • {s}")

        result = "\n".join(lines)
        # 텔레그램 메시지 길이 제한 (4096자) — 초과 시 개선안까지만 유지
        if len(result) > 3800:
            result = result[:3800] + "\n\n⚠️ 메시지 길이 초과로 일부 생략"
        return result

    # ── 실시간 가격 콜백 ──────────────────────────────────

    def _on_realtime_price(self, data: dict) -> None:
        """WebSocket 실시간 체결가 수신 콜백."""
        code = data.get("code", "")
        price = data.get("current_price", 0)
        change_pct = data.get("change_pct", 0.0)
        ts = now_kst()

        # 활성 모니터만 가격 업데이트 (멀티 트레이드 시 현재 매매 중인 종목만)
        if self._active_monitor and self._active_monitor.target.stock.code == code:
            mon = self._active_monitor
            mon.target.stock.current_price = price
            mon.target.stock.price_change_pct = change_pct
            mon.on_price(price, ts)
            logger.debug(f"[실시간] {mon.target.stock.name} {price:,}원 ({change_pct:+.2f}%)")

    def _on_futures_price(self, data: dict) -> None:
        """KOSPI200 선물 실시간 체결가 수신 콜백."""
        price = data.get("current_price", 0.0)
        if price <= 0:
            return
        self._futures_price = price

        # 활성 모니터에 선물 가격 전달
        if self._active_monitor:
            self._active_monitor.on_futures_price(price)
            logger.debug(f"[선물] {price:.2f}")

    # ── 모니터 루프 (시그널 폴링) ─────────────────────────

    async def _monitor_loop_runner(self):
        """1초 간격으로 모니터 시그널 체크."""
        while self._running:
            await asyncio.sleep(1)
            await self._process_signals()

    async def _process_signals(self):
        """활성 모니터의 시그널 처리."""
        mon = self._active_monitor
        if mon is None:
            return

        ts = now_kst()
        target = mon.target

        # DRY_RUN 체결 시뮬레이션
        if self.settings.is_dry_run and mon.state == MonitorState.HIGH_CONFIRMED:
            filled = self.trader.simulate_fills(target, target.stock.current_price, ts)
            for label in filled:
                mon.on_buy_filled(label, target.stock.current_price, 0, ts)

        # 매수 주문 배치 시그널
        if mon.signal_place_orders:
            mon.signal_place_orders = False
            await self.trader.place_buy_orders(target, self._available_cash)

        # 매수 주문 취소 시그널 (고가 갱신)
        if mon.signal_cancel_orders:
            mon.signal_cancel_orders = False
            await self.trader.cancel_buy_orders(target)
            logger.info(f"[{target.stock.name}] 고가 갱신 → 주문 취소 완료. 새 고가 추적 중")

        # 청산 시그널
        if mon.signal_exit:
            mon.signal_exit = False
            exit_reason = mon.signal_exit_reason

            await self.trader.execute_exit(
                target, exit_reason, mon.signal_exit_price
            )

            # 매매 완료 기록
            self._completed_codes.add(target.stock.code)

            # 일일 P&L 기록
            pnl = self.trader.get_pnl(target.stock.current_price)
            self.risk.record_trade_result(pnl)

            # ── TradeRecord 즉시 생성 (trader.reset() 전에 position 데이터 캡처) ──
            record = self._build_trade_record(mon, now_kst().date())
            self._trade_records.append(record)
            try:
                self._db.save_trade(record)
            except Exception as e:
                logger.error(f"거래 DB 저장 실패 ({target.stock.name}): {e}")

            # 일일 손실 한도 체크
            try:
                balance = await self.api.get_balance()
                self.risk.check_daily_loss_limit(balance.get("available_cash", 0))
            except Exception as e:
                logger.error(f"잔고 조회 실패: {e}")

            # 대시보드 상태 동기화
            await self._fire_state_update()

            # ── 멀티 트레이드: 다음 후보 진입 시도 ──
            await self._try_next_candidate(exit_reason)

    # ── 네트워크 안전 장치 ─────────────────────────────────

    def _on_ws_disconnect(self) -> None:
        """
        WebSocket 끊김 즉시 호출 (1차 방어).
        비동기 컨텍스트가 아니므로 asyncio.Task로 긴급 취소 예약.
        """
        self._network_ok = False
        if self._emergency_cancel_done:
            return

        logger.critical("🚨 WebSocket 끊김 감지 — 미체결 매수 긴급 취소 예약")
        self.notifier.notify_error(
            "WebSocket 끊김!\n미체결 매수 주문 긴급 취소 시도 중"
        )

        # 이벤트 루프에 긴급 취소 태스크 예약
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emergency_cancel_orders())
        except RuntimeError:
            logger.error("이벤트 루프 없음 — 긴급 취소 불가")

    async def _emergency_cancel_orders(self) -> None:
        """
        긴급 미체결 매수 전량 취소 (1차 방어).
        REST API로 시도 — 네트워크가 완전히 죽은 게 아니면 성공 가능.
        WebSocket만 끊기고 REST는 살아있는 경우가 많다.
        """
        if self._emergency_cancel_done:
            return
        self._emergency_cancel_done = True

        mon = self._active_monitor
        if mon is None:
            return

        target = mon.target

        # 미체결 매수 주문이 있으면 취소 시도
        if self.trader.pending_buy_orders:
            logger.critical(f"[{target.stock.name}] 미체결 매수 {len(self.trader.pending_buy_orders)}건 긴급 취소")
            try:
                await self.trader.cancel_buy_orders(target)
                logger.info(f"[{target.stock.name}] 미체결 매수 긴급 취소 완료")
                self.notifier.notify_error(
                    f"미체결 매수 취소 성공: {target.stock.name}"
                )
            except Exception as e:
                logger.critical(f"긴급 취소 실패: {e}")
                self.notifier.notify_error(
                    f"⚠️ 미체결 매수 취소 실패!\n{target.stock.name}\n수동 확인 필요"
                )

        # 이미 포지션이 잡힌 경우 → 수동 청산 안내
        if self.trader.has_position():
            pos = self.trader.position
            logger.critical(
                f"⚠️ 포지션 보유 중 네트워크 끊김! "
                f"{target.stock.name} {pos.total_qty}주 (평단 {pos.avg_price:,.0f}원)"
            )
            self.notifier.notify_error(
                f"⚠️ 포지션 보유 중 네트워크 단절!\n"
                f"{target.stock.name} {pos.total_qty}주\n"
                f"평단 {pos.avg_price:,.0f}원\n"
                f"HTS에서 수동 청산 필요!"
            )

    async def _network_health_check(self) -> None:
        """
        네트워크 헬스체크 루프 (2차 방어 보조).
        WebSocket 무응답 감지 + REST API ping 테스트.
        """
        infra = self.params.infra
        market_open = time.fromisoformat(self.params.market.open_time)
        market_close = time.fromisoformat(self.params.market.close_time)

        while self._running:
            await asyncio.sleep(infra.health_check_interval_sec)

            # 장중에만 체크
            now = now_kst().time()
            if now < market_open or now > market_close:
                continue

            # WebSocket 무응답 체크
            age = self.api.ws_last_recv_age
            if age > infra.ws_timeout_sec and self.api.ws_connected:
                logger.warning(f"WebSocket 무응답 {age:.0f}초 — 연결 상태 의심")

            if not self.api.ws_connected and self._active_monitor and not self._emergency_cancel_done:
                # WebSocket 끊김 + 활성 모니터 있음 → 긴급 조치
                logger.critical("헬스체크: WebSocket 미연결 감지")
                await self._emergency_cancel_orders()

            # 네트워크 복구 감지
            if not self._network_ok and self.api.ws_connected:
                self._network_ok = True
                self._emergency_cancel_done = False
                logger.info("네트워크 복구 감지")
                self.notifier.notify_system("네트워크 복구됨")

    # ── 대시보드 서버 ────────────────────────────────────

    def _start_dashboard_server(self) -> None:
        """대시보드(uvicorn) 서버를 데몬 스레드로 시작 + AutoTrader 연결."""
        import uvicorn
        from src.dashboard.app import app as dashboard_app, attach_autotrader

        port = self.settings.dashboard_port

        def _run():
            uvicorn.run(dashboard_app, host="0.0.0.0", port=port, log_level="warning")

        t = threading.Thread(target=_run, daemon=True, name="dashboard")
        t.start()

        # 대시보드에 AutoTrader 연결 (sync: state + callback만 설정)
        attach_autotrader(self)
        logger.info(f"대시보드 서버 시작 (port={port})")

    async def _build_stock_name_cache(self) -> None:
        """종목명 캐시 구축. stock_master.json만 사용.

        Note: 거래량순위 API 호출은 deprecated (ISSUE-010 수동 입력 전환).
        신규 종목 검증은 /api/set-targets와 /api/search-stock에서 lazy fill.
        """
        from src.dashboard.app import state as dashboard_state

        try:
            master_path = Path(__file__).resolve().parents[1] / "config" / "stock_master.json"
            if master_path.exists():
                master = json.loads(master_path.read_text(encoding="utf-8"))
                for code, name in master.items():
                    dashboard_state.cache_stock(code, name)
                logger.info(f"종목 마스터 로드: {len(master)}건")
                logger.info(f"종목명 캐시 총: {len(dashboard_state._stock_name_cache)}건")
            else:
                logger.warning(f"stock_master.json 없음: {master_path}")
        except Exception as e:
            logger.warning(f"종목명 캐시 구축 실패: {e}")

    # ── 유틸리티 ──────────────────────────────────────────

    async def _fire_state_update(self) -> None:
        """대시보드 상태 동기화 콜백 호출. 동기/비동기 콜백 모두 지원."""
        if self.on_state_update:
            result = self.on_state_update()
            if asyncio.iscoroutine(result):
                await result

    async def _wait_until(self, target_time: time) -> None:
        """지정 시각까지 대기. 이미 지났으면 즉시 리턴."""
        while self._running:
            now = now_kst().time()
            if now >= target_time:
                return

            # 남은 시간 계산
            today = now_kst().date()
            now_dt = datetime.combine(today, now)
            target_dt = datetime.combine(today, target_time)
            remaining = (target_dt - now_dt).total_seconds()

            if remaining <= 0:
                return

            wait = min(remaining, 10)  # 최대 10초씩 대기 (종료 신호 감지용)
            await asyncio.sleep(wait)


def main():
    asyncio.run(AutoTrader().run())


if __name__ == "__main__":
    main()
