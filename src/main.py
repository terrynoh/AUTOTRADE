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
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import Settings, StrategyParams
from src.kis_api.kis import KISAPI
from src.kis_api.constants import WS_TR_PRICE, WS_TR_FUTURES
from src.core.screener import Screener
from src.core.stock_master import StockMaster
from src.core.trader import Trader
from src.core.risk_manager import RiskManager
from src.core.watcher import Watcher, WatcherCoordinator
from src.models.trade import TradeRecord, DailySummary, ExitReason
from src.storage.database import Database
from src.utils.notifier import Notifier
from src.utils.logger import setup_logger
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
        # ── StockMaster (W-02 결과물, Screener/Notifier 공유) ──
        self._stock_master = StockMaster(Path(__file__).parent.parent / "config" / "stock_master.json")

        self.screener = Screener(self.api, self.params, stock_master=self._stock_master, is_live=self.settings.is_live, use_live_data=self.settings.use_live_data)
        self.trader = Trader(self.api, self.settings, self.params)
        self.risk = RiskManager(self.params)

        # ── Coordinator (W-05b/c 결과물) ──
        self._coordinator: WatcherCoordinator = WatcherCoordinator(
            params=self.params,
            trader=self.trader,
        )
        self._subscribed_codes: list[str] = []  # 현재 WebSocket 구독 중인 종목코드들
        self._realtime_callback_registered: bool = False  # 실시간 콜백 등록 여부

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
        self._loop = None  # run() 시작 시 설정 (dashboard 가 사용)

        # ── 텔레그램 알림 ──
        self.notifier = Notifier(
            bot_token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
            stock_master=self._stock_master,
        )

        # ── DB ──
        self._db = Database()

        # ── Cloudflare Tunnel ──

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
        for watcher in self._coordinator.watchers:
            monitors.append({
                "code": watcher.code,
                "name": watcher.name,
                "state": watcher.state.value,
                "intraday_high": watcher.intraday_high,
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
        # 현재 loop 보관 (dashboard 가 run_coroutine_threadsafe 로 사용)
        self._loop = asyncio.get_running_loop()

        logger.info("=" * 50)
        logger.info(f"AUTOTRADE 시작 (모드: {self.settings.trade_mode})")
        mt = self.params.multi_trade
        if mt.enabled:
            logger.info(f"멀티 트레이드: {mt.repeat_start}~{mt.repeat_end}")
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

            # Coordinator 청산 콜백 등록 (W-06b1)
            self._coordinator.set_exit_callback(self._on_exit_done)

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
            self._coordinator.set_available_cash(self._available_cash)  # W-06b1
            logger.info(f"예수금: {self._initial_cash:,}원 → 매매가용: {self._available_cash:,}원")

            # 대시보드 서버 시작 (API 연결 + 잔고 확인 후 → attach_autotrader 가능)
            dashboard_task = self._start_dashboard_server()
            await self._build_stock_name_cache()


            self._running = True
            await self._fire_state_update()

            # 스케줄 태스크 실행 — 하나라도 실패하면 나머지 취소
            tasks = [
                dashboard_task,
                asyncio.create_task(self._schedule_screening(), name="screening"),
                asyncio.create_task(self._schedule_buy_deadline(), name="buy_deadline"),
                asyncio.create_task(self._schedule_force_liquidate(), name="force_liquidate"),
                asyncio.create_task(self._schedule_market_close(), name="market_close"),
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
            # dashboard server 는 should_exit 로 graceful shutdown 시도 후 cancel
            if hasattr(self, "_dashboard_server") and self._dashboard_server is not None:
                self._dashboard_server.should_exit = True
            # 아직 남아있는 태스크 정리
            for t in tasks:
                if not t.done():
                    t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.notifier.stop_polling()
            await self.api.disconnect()
            logger.info("AUTOTRADE 종료")

    # ── 스케줄: 09:50 스크리닝 ────────────────────────────

    async def _schedule_screening(self):
        """09:50까지 대기 후 스크리닝 실행."""
        screening_time = time.fromisoformat(self.params.screening.screening_time)
        await self._wait_until(screening_time)
        await self._on_screening()

    async def _on_screening(self):
        """스크리닝 실행 → Coordinator 에 watchers 주입 → 3종목 KIS 구독."""
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

        logger.info(f"후보 풀 {len(targets)}종목 선정")
        for i, t in enumerate(targets):
            logger.info(f"  #{i+1} {t.name}({t.code}) {t.market.value} "
                        f"등락={t.price_change_pct:+.2f}% 거래대금={t.trading_volume_krw/1e8:.0f}억")

        # Coordinator 에 watchers 주입 (W-06b2)
        self._coordinator.start_screening(targets)

        # 3종목 KIS WebSocket 구독
        codes = [w.code for w in self._coordinator.watchers]
        if codes:
            try:
                await self.api.subscribe_realtime(codes)
                self._subscribed_codes = codes
                logger.info(f"실시간 감시 시작: {len(codes)}종목 {codes}")
            except Exception as e:
                logger.error(f"실시간 구독 실패: {e}")

        await self._fire_state_update()

    # ── 스케줄: 15:20 강제 청산 ───────────────────────────

    async def _schedule_buy_deadline(self):
        """매수 마감 시각 도달 시 Coordinator 에 통지 (10:55)."""
        deadline = time.fromisoformat(self.params.entry.entry_deadline)
        await self._wait_until(deadline)
        await self._coordinator.on_buy_deadline(now_kst())
        logger.info(f"매수 마감 ({self.params.entry.entry_deadline}) — Coordinator 통지 완료")

    async def _schedule_force_liquidate(self):
        """강제 청산 시각 도달 시 Coordinator 에 통지."""
        force_time = time.fromisoformat(self.params.exit.force_liquidate_time)
        await self._wait_until(force_time)
        await self._coordinator.on_force_liquidate(now_kst())
        logger.info(f"강제 청산 ({self.params.exit.force_liquidate_time}) — Coordinator 통지 완료")

    # ── 스케줄: 15:30 장 마감 + 일일 리포트 ─────────────────

    async def _schedule_market_close(self):
        close_time = time.fromisoformat(self.params.market.close_time)
        await self._wait_until(close_time)
        logger.info("=" * 50)
        logger.info("장 마감 — 일일 리포트 생성")
        logger.info("=" * 50)
        self._running = False

        today = now_kst().date()

        # ── 1. 미진입 watcher 의 NO_ENTRY 레코드 추가 + 이미 저장된 레코드 합산 ──
        recorded_codes = {r.code for r in self._trade_records}
        for watcher in self._coordinator.watchers:
            if watcher.code not in recorded_codes:
                # 미진입 또는 아직 기록되지 않은 watcher
                record = self._build_trade_record(watcher, today)
                self._trade_records.append(record)
                try:
                    self._db.save_trade(record)
                except Exception as e:
                    logger.error(f"거래 DB 저장 실패 ({watcher.name}): {e}")

        trade_records = self._trade_records

        # ── 2. DailySummary 생성 + DB 저장 ──
        summary = DailySummary(
            summary_date=today,
            trade_mode=self.settings.trade_mode,
            candidates_count=len(self._coordinator.watchers),
            targets_count=len(self._coordinator.watchers),
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

    def _build_trade_record(self, watcher: Watcher, today) -> TradeRecord:
        """Watcher에서 TradeRecord 생성."""
        pos = self.trader.position

        # 미진입
        if watcher.total_buy_qty <= 0:
            return TradeRecord(
                trade_date=today,
                code=watcher.code,
                name=watcher.name,
                market=watcher.market.value,
                exit_reason=ExitReason.NO_ENTRY,
                rolling_high=watcher.intraday_high,
                trade_mode=self.settings.trade_mode,
            )

        # 매수/매도 데이터
        avg_buy = (
            watcher.total_buy_amount / watcher.total_buy_qty
            if watcher.total_buy_qty > 0 else 0
        )
        pnl = 0.0
        pnl_pct = 0.0
        avg_sell = 0.0
        sell_amount = 0
        sell_time = None
        holding_min = 0.0

        # 포지션에서 매도 데이터 추출
        if pos and pos.sell_orders:
            sell_amount = pos.total_sell_amount
            avg_sell = sell_amount / watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0
            last_sell = pos.sell_orders[-1]
            sell_time = last_sell.filled_at
            pnl = sell_amount - watcher.total_buy_amount
            pnl_pct = (pnl / watcher.total_buy_amount * 100) if watcher.total_buy_amount > 0 else 0
        elif pos:
            # 미청산 (현재가 기준)
            pnl = pos.pnl(watcher.current_price)
            pnl_pct = pos.pnl_pct(watcher.current_price)
            avg_sell = watcher.current_price

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
        exit_reason = reason_map.get(watcher.exit_reason, ExitReason.NO_ENTRY)

        return TradeRecord(
            trade_date=today,
            code=watcher.code,
            name=watcher.name,
            market=watcher.market.value,
            avg_buy_price=avg_buy,
            total_buy_qty=watcher.total_buy_qty,
            total_buy_amount=watcher.total_buy_amount,
            buy_count=int(watcher.buy1_filled) + int(watcher.buy2_filled),
            first_buy_time=pos.opened_at if pos else None,
            avg_sell_price=avg_sell,
            total_sell_amount=sell_amount,
            sell_time=sell_time,
            exit_reason=exit_reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_minutes=holding_min,
            rolling_high=watcher.intraday_high,
            entry_trigger_price=watcher.target_buy1_price,
            target_price=watcher.target_price,
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
        """WebSocket 실시간 체결가 수신 콜백. Coordinator 로 위임."""
        code = data.get("code", "")
        price = data.get("current_price", 0)
        ts = now_kst()

        # Coordinator 가 모든 watcher 라우팅 (async fire-and-forget)
        asyncio.create_task(self._coordinator.on_realtime_price(code, price, ts))

    def _on_futures_price(self, data: dict) -> None:
        """KOSPI200 선물 실시간 체결가 수신 콜백. Coordinator 로 위임."""
        price = data.get("current_price", 0.0)
        if price <= 0:
            return
        self._futures_price = price

        # Coordinator 에 선물 가격 전달
        self._coordinator.on_realtime_futures(price)

    # ── 모니터 루프 (시그널 폴링) ─────────────────────────

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

        watcher = self._coordinator.active
        if not watcher:
            return

        # 미체결 매수 주문이 있으면 취소 시도
        if self.trader.pending_buy_orders:
            logger.critical(f"[{watcher.name}] 미체결 매수 {len(self.trader.pending_buy_orders)}건 긴급 취소")
            try:
                await self.trader.cancel_buy_orders(watcher)
                logger.info(f"[{watcher.name}] 미체결 매수 긴급 취소 완료")
                self.notifier.notify_error(
                    f"미체결 매수 취소 성공: {watcher.name}"
                )
            except Exception as e:
                logger.critical(f"긴급 취소 실패: {e}")
                self.notifier.notify_error(
                    f"⚠️ 미체결 매수 취소 실패!\n{watcher.name}\n수동 확인 필요"
                )

        # 이미 포지션이 잡힌 경우 → 수동 청산 안내
        if self.trader.has_position():
            pos = self.trader.position
            logger.critical(
                f"⚠️ 포지션 보유 중 네트워크 끊김! "
                f"{watcher.name} {pos.total_qty}주 (평단 {pos.avg_price:,.0f}원)"
            )
            self.notifier.notify_error(
                f"⚠️ 포지션 보유 중 네트워크 단절!\n"
                f"{watcher.name} {pos.total_qty}주\n"
                f"평단 {pos.avg_price:,.0f}원\n"
                f"HTS에서 수동 청산 필요!"
            )

    async def _on_exit_done(self, watcher: Watcher) -> None:
        """청산 완료 콜백. Coordinator._execute_exit 가 호출.

        책임:
        1. P&L 기록
        2. TradeRecord 생성 + DB 저장
        3. 손실 한도 체크
        4. 멀티 트레이드 가드
        5. 잔고 재조회 + Coordinator 갱신
        6. trader.reset
        7. 대시보드 동기화
        """
        # 1. P&L 기록
        pnl = self.trader.get_pnl(watcher.current_price)
        self.risk.record_trade_result(pnl)

        # 2. TradeRecord 생성 + DB 저장
        record = self._build_trade_record(watcher, now_kst().date())
        self._trade_records.append(record)
        try:
            self._db.save_trade(record)
        except Exception as e:
            logger.error(f"거래 DB 저장 실패 ({watcher.name}): {e}")

        # 3. 손실 한도 체크
        try:
            balance = await self.api.get_balance()
            self.risk.check_daily_loss_limit(balance.get("available_cash", 0))
        except Exception as e:
            logger.error(f"잔고 조회 실패 (손실 한도 체크): {e}")

        # 4. 멀티 트레이드 가드
        mt = self.params.multi_trade
        if not mt.enabled:
            await self._fire_state_update()
            return

        # 5. 잔고 재조회 + Coordinator 갱신
        try:
            if self.settings.is_dry_run and self.settings.dry_run_cash > 0:
                new_cash = self.risk.calculate_available_cash(self.settings.dry_run_cash)
                logger.info(f"[DRY_RUN] 가상 예수금 재설정: {new_cash:,}원")
            else:
                balance = await self.api.get_balance()
                new_cash = self.risk.calculate_available_cash(
                    balance.get("available_cash", 0)
                )
            self._available_cash = new_cash
            self._coordinator.set_available_cash(new_cash)
            logger.info(f"다음 매매 가용 예수금: {new_cash:,}원")
        except Exception as e:
            logger.error(f"잔고 조회 실패 (다음 매매 준비): {e}")

        # 6. trader.reset (다음 종목 매수 준비)
        self.trader.reset()

        # === 6.5. T3 위임 (W-11e: 두 번째 매매 트리거) ===
        # _on_exit_done 진입 시점에 _active_code 는 여전히 청산된 종목 코드.
        # handle_t3 가 _execute_buy 를 호출하기 전에 _active_code 를 None 으로 명시 처리.
        # 이렇게 하면 handle_t3 안에서 새 chosen.code 로 _active_code 가 atomic 하게 교체됨.
        self._coordinator._active_code = None
        await self._coordinator.handle_t3(now_kst())

        # 7. 대시보드 동기화
        await self._fire_state_update()

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
                # 2배 timeout 초과 시 좀비 연결로 판단, 강제 끊기
                if age > infra.ws_timeout_sec * 2:
                    logger.critical(f"WebSocket 좀비 감지 {age:.0f}초 — 연결 강제 종료")
                    self.api._ws_connected = False  # 재접속 루프 트리거

            if not self.api.ws_connected and self._coordinator.has_active and not self._emergency_cancel_done:
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

    def _start_dashboard_server(self) -> "asyncio.Task":
        """대시보드(uvicorn) 서버를 같은 event loop 의 task 로 시작 + AutoTrader 연결.

        ISSUE-035: 별도 스레드의 uvicorn loop 와 AutoTrader loop 의 cross-loop
        호출 문제 해결. 같은 loop 에서 task 로 띄워 dashboard handler 가
        AutoTrader 와 동일한 Task 컨텍스트에서 실행되도록 한다.
        """
        import uvicorn
        from src.dashboard.app import app as dashboard_app, attach_autotrader

        port = self.settings.dashboard_port

        # AutoTrader 먼저 attach (race window 최소화)
        attach_autotrader(self)

        config = uvicorn.Config(
            dashboard_app,
            host="0.0.0.0",
            port=port,
            log_level="warning",
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        self._dashboard_server = server  # shutdown 시 should_exit 접근용
        task = asyncio.create_task(server.serve(), name="dashboard_server")
        logger.info(f"대시보드 서버 시작 (port={port}, in-loop task)")
        return task

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
    import signal as _signal

    # 로거 초기화 — loguru 파일 sink 활성화 (M-08-L-1 에서 누락 확정)
    _settings = Settings()
    _params = StrategyParams.load()
    setup_logger(log_level=_settings.log_level, infra_params=_params.infra)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # ── SIGTERM graceful shutdown (7단계) ──────────────────
    # 1단계: SIGTERM 수신 → 핸들러 진입
    def _sigterm_handler():
        logger.info("SIGTERM 수신 → graceful shutdown 시작")
        # 2단계: 실행 중인 모든 asyncio 태스크 cancel()
        for task in asyncio.all_tasks(loop):
            task.cancel()
        # 3~7단계: CancelledError → run() finally 블록 실행
        #   3. tasks 정리 (cancel + gather)
        #   4. notifier.stop_polling()  — 텔레그램 봇 중지
        #   5. api.disconnect()         — KIS WebSocket + REST 세션 종료
        #   6. tunnel.stop()            — Cloudflare Tunnel 종료
        #   7. logger "AUTOTRADE 종료"  — 종료 로그

    if hasattr(_signal, "SIGTERM"):
        try:
            loop.add_signal_handler(_signal.SIGTERM, _sigterm_handler)
        except (NotImplementedError, RuntimeError):
            pass  # Windows는 add_signal_handler 미지원 — 무시

    try:
        loop.run_until_complete(AutoTrader().run())
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


if __name__ == "__main__":
    main()
