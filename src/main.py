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
import sys
from datetime import datetime, time, timedelta
from typing import Optional

from loguru import logger

from config.settings import Settings, StrategyParams
from src.kis_api.kis import KISAPI
from src.kis_api.constants import WS_TR_PRICE
from src.core.screener import Screener
from src.core.monitor import TargetMonitor, MonitorState
from src.core.trader import Trader
from src.core.risk_manager import RiskManager
from src.models.stock import TradeTarget
from src.utils.notifier import Notifier

# 네트워크 헬스체크 상수
WS_TIMEOUT_SEC = 60        # WebSocket 무응답 판정 기준 (초)
HEALTH_CHECK_INTERVAL = 5  # 헬스체크 주기 (초)


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
        )
        self.screener = Screener(self.api, self.params, is_live=self.settings.is_live)
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

        # ── 기타 ──
        self._available_cash: int = 0
        self._initial_cash: int = 0
        self._futures_price: int = 0
        self._running: bool = False
        self._network_ok: bool = True       # 네트워크 정상 여부
        self._emergency_cancel_done: bool = False  # 긴급 취소 실행 여부 (중복 방지)
        self.on_state_update = None  # 대시보드 동기화 콜백 (run.py에서 설정)

        # ── 텔레그램 알림 ──
        self.notifier = Notifier(
            bot_token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
        )

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

        tasks: list[asyncio.Task] = []
        try:
            await self.api.connect()

            # 실시간 콜백 1회 등록 (중복 방지)
            if not self._realtime_callback_registered:
                self.api.add_realtime_callback(WS_TR_PRICE, self._on_realtime_price)
                self._realtime_callback_registered = True

            # WebSocket 끊김 콜백 등록 (1차 방어: 즉시 미체결 매수 취소)
            self.api.set_ws_disconnect_callback(self._on_ws_disconnect)

            # 계좌 잔고 확인
            balance = await self.api.get_balance()
            self._initial_cash = balance.get("available_cash", 0)
            self._available_cash = self.risk.calculate_available_cash(self._initial_cash)
            logger.info(f"예수금: {self._initial_cash:,}원 → 매매가용: {self._available_cash:,}원")

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
            await self.api.disconnect()
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

        targets = await self.screener.run()

        if not targets:
            logger.info("스크리닝 결과 없음 — 당일 매매 안 함")
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
        now = datetime.now().time()
        repeat_start = time.fromisoformat(mt.repeat_start)
        repeat_end = time.fromisoformat(mt.repeat_end)

        if now < repeat_start or now > repeat_end:
            logger.info(f"멀티 트레이드 시간 범위 밖 ({mt.repeat_start}~{mt.repeat_end}) → 중단")
            return

        # Trader 초기화
        self.trader.reset()

        # 잔고 재확인
        try:
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
                mon.force_exit(datetime.now())
            elif mon.state in (MonitorState.HIGH_CONFIRMED, MonitorState.TRACKING_HIGH):
                # 미매수 상태 → 주문 취소
                await self.trader.cancel_buy_orders(mon.target)
                mon.state = MonitorState.SKIPPED
                logger.info(f"[{mon.target.stock.name}] 15:20 미매수 → 주문 취소")

    # ── 스케줄: 15:30 장 마감 ─────────────────────────────

    async def _schedule_market_close(self):
        await self._wait_until(time(15, 30))
        logger.info("=" * 50)
        logger.info("장 마감 — 일일 리포트")
        logger.info("=" * 50)
        self._running = False

        # 일일 결과 로그
        logger.info(f"당일 매매 횟수: {self.risk.daily_trades}회")
        logger.info(f"당일 누적 P&L: {self.risk.daily_pnl:+,.0f}원")

        for mon in self._monitors:
            t = mon.target
            if t.total_buy_qty > 0:
                pnl = self.trader.get_pnl(t.stock.current_price)
                logger.info(
                    f"  [{t.stock.name}] 평단 {t.avg_price:,.0f}원, "
                    f"청산사유={t.exit_reason or 'N/A'}, P&L={pnl:+,.0f}원"
                )
            else:
                logger.info(f"  [{t.stock.name}] 매수 미발생 (상태: {mon.state.value})")

    # ── 실시간 가격 콜백 ──────────────────────────────────

    def _on_realtime_price(self, data: dict) -> None:
        """WebSocket 실시간 체결가 수신 콜백."""
        code = data.get("code", "")
        price = data.get("current_price", 0)
        change_pct = data.get("change_pct", 0.0)
        ts = datetime.now()

        # 활성 모니터만 가격 업데이트 (멀티 트레이드 시 현재 매매 중인 종목만)
        if self._active_monitor and self._active_monitor.target.stock.code == code:
            mon = self._active_monitor
            mon.target.stock.current_price = price
            mon.target.stock.price_change_pct = change_pct
            mon.on_price(price, ts)
            logger.debug(f"[실시간] {mon.target.stock.name} {price:,}원 ({change_pct:+.2f}%)")

        # 선물 가격 업데이트 (별도 TR ID로 올 경우)
        # TODO: WS_TR_FUTURES 콜백 분리

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

        ts = datetime.now()
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
        while self._running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            # 장중에만 체크 (09:00~15:30)
            now = datetime.now().time()
            if now < time(9, 0) or now > time(15, 30):
                continue

            # WebSocket 무응답 체크
            age = self.api.ws_last_recv_age
            if age > WS_TIMEOUT_SEC and self.api.ws_connected:
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
            now = datetime.now().time()
            if now >= target_time:
                return

            # 남은 시간 계산
            now_dt = datetime.combine(datetime.today(), now)
            target_dt = datetime.combine(datetime.today(), target_time)
            remaining = (target_dt - now_dt).total_seconds()

            if remaining <= 0:
                return

            wait = min(remaining, 10)  # 최대 10초씩 대기 (종료 신호 감지용)
            await asyncio.sleep(wait)


def main():
    asyncio.run(AutoTrader().run())


if __name__ == "__main__":
    main()
