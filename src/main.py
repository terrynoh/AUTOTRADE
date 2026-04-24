"""
AUTOTRADE 메인 진입점.

asyncio 기반으로 KIS REST/WebSocket 위에서
전체 매매 프로세스를 오케스트레이션한다.

R16: LIVE 전용 (paper/dry_run/FillManager/CashManager 폐기).
R15-005: LIVE 체결통보 (H0STCNI0) 본구현.
R15-007: 매수가능조회 (TTTC8908R, nrcvb_buy_amt) 기반 예수금 관리.

멀티 트레이드:
  - 09:49 스크리닝 → 후보 풀 최대 5종목 선정
  - 1번 종목부터 신고가 감시 → 매수 → 청산
  - 수익 청산 시 10:00~11:00 이내면 다음 후보로 전환
  - 일일 최대 3회, 손실 시 당일 중단

타임라인:
  09:00  장 시작 → 토큰 발급, 계좌 확인
  09:49  스크리닝 → 후보 풀 선정
  09:50~ 신고가 감시 + 매수 대기
  수익청산 → 다음 후보 (10:00~11:00, 최대 3회)
  11:20  강제 청산
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
from src.kis_api.constants import WS_TR_PRICE, WS_TR_PRICE_UN, WS_TR_FUTURES
from src.core.screener import Screener
from src.core.stock_master import StockMaster
from src.core.trader import Trader
from src.core.risk_manager import RiskManager
from src.core.watcher import Watcher, WatcherCoordinator, WatcherState
from src.core.channel_resolver import ChannelResolver
from src.storage.trade_logger import TradeLogger
from src.utils.notifier import Notifier
from src.utils.logger import setup_logger
from src.utils.market_calendar import now_kst



class AutoTrader:
    """AUTOTRADE 메인 오케스트레이터 (R16 LIVE 전용)."""

    def __init__(self):
        self.settings = Settings()
        self.params = StrategyParams.load()
        self.api = KISAPI(
            app_key=self.settings.kis_app_key,
            app_secret=self.settings.kis_app_secret,
            account_no=self.settings.account_no,
            infra_params=self.params.infra,
        )
        # ── StockMaster (W-02 결과물, Screener/Notifier 공유) ──
        self._stock_master = StockMaster(Path(__file__).parent.parent / "config" / "stock_master.json")

        self.screener = Screener(self.api, self.params, stock_master=self._stock_master)
        self.trader = Trader(self.api, self.settings, self.params)
        self.risk = RiskManager(self.params)

        # ── Coordinator (W-05b/c 결과물) ──
        self._coordinator: WatcherCoordinator = WatcherCoordinator(
            params=self.params,
            trader=self.trader,
        )

        # ── R-17: ChannelResolver (시세 채널 자동 분기) ──
        self._channel_resolver: ChannelResolver = ChannelResolver(self.api)
        self._coordinator.set_channel_resolver(self._channel_resolver)

        self._realtime_callback_registered: bool = False  # 실시간 콜백 등록 여부

        # ── 수동 종목 입력 ──
        self._manual_codes: list[str] = []

        # ── 예수금 상태 (R16: CashManager 제거, 단순 int 관리) ──
        # _initial_cash: 시작 시 조회한 buyable_cash (하루 고정, 수익률 분모)
        # _available_cash: 매 청산 후 갱신되는 매수 가용 금액 (변동)
        self._initial_cash: int = 0
        self._available_cash: int = 0

        # ── 기타 ──
        self._futures_price: float = 0.0
        self._running: bool = False
        # STALENESS-01: 선물가 staleness 추적
        self._futures_last_update_ts: Optional[datetime] = None
        self._futures_stale_alerted: bool = False
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

        # ── 로그 시스템 (R-10) ──
        # capital 은 run() 초기 잔고 확인 후 self._initial_cash 로 갱신됨.
        self._trade_logger = TradeLogger()

        # ── VI-Observer (W-SAFETY-1) ──
        # 각 종목 이전 VI 필드 상태 (변화 감지용). 해석 안 함 — Stage 2 실측 후 확정.
        self._prev_vi_state: dict[str, dict] = {}
        # 당일 종목별 첫 변화 알림 발송 여부 (중복 방지)
        self._vi_notified_codes: set[str] = set()

        # ── H2 중복 알림 방지 (W-SAFETY-1) ──
        self._h2_notified: bool = False

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
            "initial_cash": self._initial_cash,
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
            # R-17: WS_TR_PRICE_UN 키로 등록 (_ws_receiver 가 UN/ST 모두 이 키로 발화)
            if not self._realtime_callback_registered:
                self.api.add_realtime_callback(WS_TR_PRICE_UN, self._on_realtime_price)
                self.api.add_realtime_callback(WS_TR_FUTURES, self._on_futures_price)
                self._realtime_callback_registered = True

            # R15-005 SA-5c: 체결통보 구독 (LIVE 전용 — R16 이후 is_live 가드 불필요).
            # 콜백 등록 -> 구독 순서. JSON 구독응답에서 IV/KEY 수신 후
            # 실제 체결통보 메시지가 언제든 올 수 있으므로 callback 이 먼저 준비되어야 함.
            # KIS_HTS_ID 미설정 시 즉시 ValueError -> 거래 시작 거부 (안전).
            if not self.settings.kis_hts_id:
                raise ValueError(
                    "KIS_HTS_ID 미설정 — LIVE 모드 시작 거부. "
                    ".env 에 KIS_HTS_ID=<HTS ID> 추가 필요"
                )
            self.api.add_execution_callback(self._on_execution_notify)
            await self.api.subscribe_execution(self.settings.kis_hts_id)
            logger.info(
                f"[R15-005] 체결통보 구독 시작 (HTS ID: {self.settings.kis_hts_id[:4]}***)"
            )

            # Coordinator 청산 콜백 등록 (W-06b1)
            self._coordinator.set_exit_callback(self._on_exit_done)

            # R-12 재설계: 리스크 매니저 주입 (하드손절 1회=strict / 2회=halt)
            self._coordinator.set_risk_manager(self.risk)

            # WebSocket 끊김 콜백 등록 (1차 방어: 즉시 미체결 매수 취소)
            self.api.set_ws_disconnect_callback(self._on_ws_disconnect)

            # KOSPI200 선물 실시간 구독 (청산 조건 ④ 선물 급락용)
            try:
                await self.api.subscribe_futures()
            except Exception as e:
                logger.error(f"선물 구독 실패: {e}")

            # ── F1 SA-5e: 시작 시 미청산 포지션 감지 (W-SAFETY-1) ──
            # 팩트: get_balance() 공식 API. holdings 존재 시 매매 거부 (자동 복구 X).
            # 의도: 재시작 시 KIS 계좌 잔고와 AUTOTRADE 메모리 불일치 방지.
            try:
                _initial_balance = await self.api.get_balance()
                _holdings = _initial_balance.get("holdings", []) or []
            except Exception as e:
                logger.error(f"[F1 SA-5e] 잔고 조회 실패 — 방어적으로 매매 거부: {e}")
                self.notifier.notify_error(
                    f"🚨 [F1 SA-5e] 시작 시 잔고 조회 실패\n"
                    f"AUTOTRADE 매매 거부됨.\n"
                    f"에러: {e}\n"
                    f"수동 확인 후 재시작 필요"
                )
                return

            # ── F1 확장 (ISSUE-LIVE-10): 미체결 매수 고아 주문 감지/회수 ──
            # 시나리오: 포지션 0 + 미체결 있음 → F1 SA-5e (holdings 기반) 는 못 잡음.
            # inquire_unfilled_orders (TTTC0084R) 로 감지 → 전량 취소 → critical 알림.
            try:
                _unfilled = await self.api.inquire_unfilled_orders()
                _buy_unfilled = [
                    u for u in _unfilled
                    if u.get("sll_buy_dvsn_cd") == "02"
                    and int(u.get("psbl_qty", "0") or 0) > 0
                ]
                if _buy_unfilled:
                    logger.critical(
                        f"[F1 확장] 시작 시 미체결 매수 {len(_buy_unfilled)}건 감지 → 전량 취소"
                    )
                    _recovered = 0
                    for u in _buy_unfilled:
                        try:
                            await self.api.cancel_order(u["odno"], u["pdno"])
                            _recovered += 1
                            logger.warning(
                                f"[F1 확장] 미체결 고아 취소: "
                                f"odno={u['odno']} pdno={u['pdno']} psbl_qty={u.get('psbl_qty')}"
                            )
                        except Exception as e:
                            logger.error(f"[F1 확장] 고아 취소 실패 odno={u.get('odno')}: {e}")
                    if _recovered > 0:
                        self.notifier.notify_error(
                            f"🚨 [F1 확장] 시작 시 미체결 매수 {_recovered}건 고아 주문 회수"
                        )
            except Exception as e:
                logger.error(f"[F1 확장] 미체결 조회 실패 (안전망 비활성): {e}")

            if _holdings:
                _holdings_msg = "\n".join(
                    f"  {h.get('code','')} {h.get('name','')} "
                    f"{h.get('qty',0)}주 @ {h.get('buy_price',0):,}원"
                    for h in _holdings
                )
                logger.critical(
                    f"[F1 SA-5e] 시작 시 미청산 포지션 감지 → 매매 거부\n{_holdings_msg}"
                )
                self.notifier.notify_error(
                    f"⚠️ 재시작 시 미청산 포지션 감지!\n"
                    f"AUTOTRADE 매매 거부됨.\n"
                    f"HTS/MTS 에서 수동 청산 후 재시작 필요.\n\n"
                    f"{_holdings_msg}"
                )
                return

            # ── 초기 예수금 확인 (R15-007) ──
            # get_buy_available() 반환 buyable_cash (nrcvb_buy_amt) = MTS 주문가능원화
            buy_info = await self.api.get_buy_available()
            self._initial_cash = buy_info["buyable_cash"]
            logger.info(
                f"[R15-007] 초기 주문가능금액 조회:"
                f"\n  buyable_cash (nrcvb_buy_amt) = {buy_info['buyable_cash']:,}원  ← 주 사용 필드"
                f"\n  ord_psbl_cash              = {buy_info['ord_psbl_cash']:,}원"
                f"\n  ruse_psbl_amt              = {buy_info['ruse_psbl_amt']:,}원"
            )
            logger.info(
                f"[R15-007] 첫 실측 검증 포인트: MTS 앱 '주문가능원화' 와 "
                f"buyable_cash ({self._initial_cash:,}원) 일치 여부 확인"
            )

            # 매매 가용 자금 = buyable_cash × max_position_size_pct (기본 100%)
            self._available_cash = self.risk.calculate_available_cash(self._initial_cash)
            self._coordinator.set_available_cash(self._available_cash)

            # TradeLogger capital 갱신 (수익률 % 분모 = 시작 시 투자 기준금액)
            self._trade_logger.capital = self._initial_cash

            logger.info(
                f"초기 예수금: {self._initial_cash:,}원 → 매매가용: {self._available_cash:,}원 "
                f"(max_position_size_pct 반영)"
            )

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
                asyncio.create_task(self._ratio_updater(), name="ratio_updater"),  # R-13
                asyncio.create_task(self._futures_staleness_monitor(), name="futures_staleness"),  # STALENESS-01
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

    # ── 스케줄: 09:49 스크리닝 ────────────────────────────

    async def _schedule_screening(self):
        """09:49에 정규 스크리닝 실행 (is_final=True).

        R-09b: 프로그램이 여러 날 연속 가동될 경우를 대비해 while 루프로 반복.
        """
        screening_time = time.fromisoformat(self.params.screening.screening_time)
        while self._running:
            await self._wait_until(screening_time)
            if not self._running:
                break
            await self._on_screening(is_final=True)
            # 다음 날 09:50까지 최소 12시간 대기 후 다시 체크
            await asyncio.sleep(43200)  # 12시간

    async def _on_screening(self, *, is_final: bool = False):
        """스크리닝 실행 → Coordinator 에 watchers 주입 → 3종목 KIS 구독.

        Args:
            is_final: True=09:50 정규 스크리닝 (이후 추가 호출 차단),
                      False=수동 스크리닝 (덧어쓰기 가능).
        """
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

        # Coordinator 에 watchers 주입 (R-09: is_final 전달)
        self._coordinator.start_screening(targets, is_final=is_final)

        # R-17: ChannelResolver 가 dual subscribe (UN+ST) 를 대체
        # 10초 윈도우 후 자동 분기 결정 + 불필요한 채널 unsubscribe
        codes = [w.code for w in self._coordinator.watchers]
        if codes:
            try:
                await self._channel_resolver.start(codes)
                logger.info(f"실시간 감시 시작 (dual subscribe): {len(codes)}종목 {codes}")
            except Exception as e:
                logger.error(f"실시간 구독 실패: {e}")

        await self._fire_state_update()

    # ── 스케줄: 11:20 강제 청산 ───────────────────────────

    async def _schedule_buy_deadline(self):
        """매수 마감 시각 도달 시 Coordinator 에 통지 (10:55)."""
        deadline = time.fromisoformat(self.params.entry.entry_deadline)
        await self._wait_until(deadline)
        await self._coordinator.on_buy_deadline(now_kst())
        logger.info(f"매수 마감 ({self.params.entry.entry_deadline}) — Coordinator 통지 완료")

    async def _schedule_force_liquidate(self):
        """강제 청산 시각 도달 시 Coordinator 에 통지.

        W-SAFETY-1 (H2): 통지 후 30초 대기 → 포지션 잔존 시 critical 알림.
        VI/서킷브레이커로 시장가 매도가 지연되는 케이스 대응.
        """
        force_time = time.fromisoformat(self.params.exit.force_liquidate_time)
        await self._wait_until(force_time)
        await self._coordinator.on_force_liquidate(now_kst())
        logger.info(f"강제 청산 ({self.params.exit.force_liquidate_time}) — Coordinator 통지 완료")

        # H2: 30초 대기 후 포지션 잔존 체크
        # 정상 시장가 체결: 1~5초. 체결통보 전달 포함 10초 이내. 30초 = 안전 마진.
        await asyncio.sleep(30)
        if self.trader.has_position() and not self._h2_notified:
            self._h2_notified = True
            _pos = self.trader.position
            _code = _pos.code if _pos else "(unknown)"
            _qty = _pos.total_qty if _pos else 0
            logger.critical(
                f"[H2] 11:20 강제 청산 실패 — 포지션 잔존\n"
                f"code={_code} total_qty={_qty}"
            )
            self.notifier.notify_error(
                f"🚨 11:20 강제 청산 실패!\n"
                f"종목: {_code}\n"
                f"수량: {_qty}주\n"
                f"원인 가능성: VI 발동 / 서킷브레이커 / 유동성 부족\n"
                f"HTS 수동 청산 즉시 필요!"
            )

    # ── 스케줄: 15:30 장 마감 + 일일 리포트 ─────────────────

    async def _schedule_market_close(self):
        close_time = time.fromisoformat(self.params.market.close_time)
        await self._wait_until(close_time)
        logger.info("=" * 50)
        logger.info("장 마감 — 일일 리포트 생성")
        logger.info("=" * 50)
        self._running = False

        today = now_kst().date()

        # ── 일별 요약 출력 + 텔레그램 전송 ──
        try:
            new_summary = self._trade_logger.update_daily_summary(today, self.settings.trade_mode)
            if new_summary:
                # 일일 요약 텔레그램 전송
                self.notifier.notify_daily_summary(new_summary)
        except Exception as e:
            logger.error(f"일별 요약 로그 실패: {e}")

        logger.info("일일 리포트 완료")
        await self._fire_state_update()

    # ── 실시간 가격 콜백 ──────────────────────────────────

    def _on_realtime_price(self, data: dict) -> None:
        """WebSocket 실시간 체결가 수신 콜백. Coordinator 로 위임.

        W-SAFETY-1: VI-Observer 추가. 가격 라우팅 전 VI 필드 변화 감지.

        R-17:
          (1) ChannelResolver 가 active 면 카운트 전달 (분기 판정용)
          (2) dual 윈도우 (10초) 동안은 watcher 라우팅 skip (ghost high 방지)
              09:50~09:50:10 의 10초만 — 신고가 감시 시작 09:55 이전이므로 안전.
        """
        # R-17 (1): ChannelResolver 에 카운트 전달
        if self._channel_resolver:
            self._channel_resolver.on_realtime_price(data)

        # R-17 (2): dual 윈도우 active 중 watcher 라우팅 skip
        if self._channel_resolver and self._channel_resolver.is_active():
            return

        code = data.get("code", "")
        price = data.get("current_price", 0)
        ts = now_kst()

        # VI-Observer: 필드 변화 감지 (팩트 영역만, 해석 안 함)
        self._check_vi_observer(code, data, ts)

        # Coordinator 가 모든 watcher 라우팅 (async fire-and-forget)
        asyncio.create_task(self._coordinator.on_realtime_price(code, price, ts))

    def _check_vi_observer(self, code: str, data: dict, ts: datetime) -> None:
        """VI-Observer (W-SAFETY-1 Stage 1): VI 관련 필드 변화 감지.

        팩트: H0UNCNT0 공식 문서 인덱스 34/35/43/44/45 파싱.
        동작: 값 변화 시 critical 로그 + 당일 종목별 첫 변화 텔레그램 1회.
        Stage 2: LIVE 실측 + HTS 대조로 필드값 의미 확정 후 매매 반영 예정.
        """
        if not code:
            return
        current = {
            "trht_yn":            data.get("trht_yn", ""),
            "mrkt_trtm_cls_code": data.get("mrkt_trtm_cls_code", ""),
            "hour_cls_code":      data.get("hour_cls_code", ""),
            "new_mkop_cls_code":  data.get("new_mkop_cls_code", ""),
            "vi_stnd_prc":        data.get("vi_stnd_prc", ""),
        }
        prev = self._prev_vi_state.get(code)
        if prev is None:
            self._prev_vi_state[code] = current
            return
        if current == prev:
            return

        # 실제 변경된 필드만 추출 (before != after 가드)
        field_defs = [
            ("trht_yn",            "TRHT_YN",            "TRHT_YN"),
            ("mrkt_trtm_cls_code", "MRKT_TRTM_CLS_CODE", "MRKT_TRTM_CLS_CODE"),
            ("hour_cls_code",      "HOUR_CLS_CODE",      "HOUR_CLS_CODE"),
            ("new_mkop_cls_code",  "NEW_MKOP_CLS_CODE",  "NEW_MKOP_CLS_CODE"),
            ("vi_stnd_prc",        "VI_STND_PRC",        "VI_STND_PRC"),
        ]
        changed_log_lines = []
        changed_tg_lines = []
        for key, log_label, tg_label in field_defs:
            if prev[key] == current[key]:
                continue
            changed_log_lines.append(f"  {log_label:<18} {prev[key]!r} → {current[key]!r}")
            changed_tg_lines.append(f" {tg_label}: {prev[key]!r} → {current[key]!r}")

        if not changed_log_lines:
            # 방어: current != prev 통과했으나 5필드 기준으로는 동일 (이론상 도달 불가)
            self._prev_vi_state[code] = current
            return

        # 변화 감지 → critical 로그 (변경된 필드만)
        logger.critical(
            f"[VI-OBSERVER] {code} 장 상태 필드 변화:\n"
            + "\n".join(changed_log_lines) + "\n"
            f"  price={data.get('current_price', 0):,}"
        )

        # 텔레그램: 당일 종목별 첫 변화 1회만 (변경된 필드만)
        if code not in self._vi_notified_codes:
            self._vi_notified_codes.add(code)
            self.notifier.notify_system(
                f"🔍 [VI-OBSERVER] {code} {ts.strftime('%H:%M:%S')}\n"
                f"필드값 변화 감지 (당일 최초):\n"
                + "\n".join(changed_tg_lines) + "\n"
                f"가격: {data.get('current_price', 0):,}\n\n"
                f"HTS 에서 실제 상태 확인 (Stage 2 분석용)"
            )

        self._prev_vi_state[code] = current

    def _on_futures_price(self, data: dict) -> None:
        """KOSPI200 선물 실시간 체결가 수신 콜백. Coordinator + RiskManager 로 위임."""
        price = data.get("current_price", 0.0)
        if price <= 0:
            return
        self._futures_price = price

        # STALENESS-01: 선물 WS 수신 ts 기록 (staleness 감지 기준)
        f_ts = now_kst()
        self._futures_last_update_ts = f_ts

        # Coordinator 에 선물 가격 + ts 전달 (STALENESS-01)
        self._coordinator.on_realtime_futures(price, f_ts)

    # ── R15-005: 체결통보 (H0STCNI0) 콜백 ────────────────

    def _on_execution_notify(self, parsed: dict) -> None:
        """KIS 체결통보 (H0STCNI0) 동기 콜백.

        KISAPI._ws_receiver 가 동기적으로 호출함. 비동기 처리는
        _process_execution_notify 에 위임.
        """
        asyncio.create_task(self._process_execution_notify(parsed))

    def _check_position_invariant(self, order, label: str) -> None:
        """Phase 2 B: dual-write 정합성 검증.

        체결통보 처리 직후 호출. trader.position 와 watcher.position 은
        동일 async task 내 sequential 갱신되므로 반드시 일치해야 함.
        불일치 시 critical 로그 + 텔레그램 알림 (매매는 계속 — 차후 확인).

        Args:
            order: 방금 체결 처리된 Order 객체 (code 조회용)
            label: "buy" 또는 "sell" (로그 식별용)
        """
        if self.trader.position is None:
            return
        watcher = next(
            (w for w in self._coordinator.watchers if w.code == order.code),
            None,
        )
        if watcher is None or watcher.position is None:
            return
        t_qty = self.trader.position.total_qty
        w_qty = watcher.position.total_qty
        t_amt = self.trader.position.total_buy_amount
        w_amt = watcher.position.total_buy_amount
        if t_qty != w_qty or t_amt != w_amt:
            logger.critical(
                f"[Phase 2 B] Position 불일치 ({label} 체결 후) "
                f"code={order.code}: "
                f"trader(qty={t_qty}, amt={t_amt}) vs "
                f"watcher(qty={w_qty}, amt={w_amt})"
            )
            self.notifier.notify_error(
                f"⚠️ Position dual-write 불일치\n"
                f"code={order.code} {label}\n"
                f"trader(qty={t_qty}, amt={t_amt:,})\n"
                f"watcher(qty={w_qty}, amt={w_amt:,})"
            )

    async def _process_execution_notify(self, parsed: dict) -> None:
        """체결통보 본체 처리 — 5 케이스 분기.

        CNTG_YN=1 → 접수/정정/취소/거부
            RCTF_CLS=0 → 정상 접수 → trader.on_live_acknowledged
            RCTF_CLS∈(1,2) → 정정/취소 통보 → 외부 개입 확정 → 텔레그램 critical
        CNTG_YN=2 → 체결
            SELN_BYOV_CLS=02 → 매수 체결 → trader.on_live_buy_filled + coordinator.on_buy_filled
            SELN_BYOV_CLS=01 → 매도 체결 → trader.on_live_sell_filled + coordinator.on_sell_filled
                                                        → 전량 체결 시 coordinator.on_sell_complete
        RFUS_YN=1 → 거부 → trader.on_live_rejected + 텔레그램 error

        예외 시: 로그 저장 후 return (상위는 _ws_receiver 의 try 블록이 혹시모를
        잡아줬으나 이로 인해 WS 수신 중단되면 안 됨).
        """
        try:
            cntg_yn = parsed.get("CNTG_YN", "")
            side = parsed.get("SELN_BYOV_CLS", "")     # 01=매도, 02=매수
            rctf = parsed.get("RCTF_CLS", "")
            rfus = parsed.get("RFUS_YN", "")
            order_id = parsed.get("ODER_NO", "")
            oorder_id = parsed.get("OODER_NO", "")
            code = parsed.get("STCK_SHRN_ISCD", "")
            ts = now_kst()

            # ── 케이스 0: 거부 (최우선 체크) ──
            if rfus == "1":
                self.trader.on_live_rejected(order_id, ts)
                self.notifier.notify_error(
                    f"🚨 LIVE 주문 거부\n"
                    f"종목: {code}\n"
                    f"주문번호: {order_id}\n"
                    f"side: {'매수' if side == '02' else ('매도' if side == '01' else side)}"
                )
                return

            # ── 케이스 1: 접수 통보 (CNTG_YN=1) ──
            if cntg_yn == "1":
                if rctf == "0":
                    # 정상 접수 → SUBMITTED → ACKNOWLEDGED
                    # Fix 4 (R-18): ACK 수신 시 order_id 매칭 실패 시 1회 retry.
                    # place_buy_orders REST 응답 → pending_buy_orders append 사이
                    # WS ACK 가 도달하는 race 방어. 200ms 대기 후 재매칭.
                    result = self.trader.on_live_acknowledged(order_id, ts)
                    if result is None:
                        await asyncio.sleep(0.2)
                        result = self.trader.on_live_acknowledged(order_id, ts)
                        if result is None:
                            logger.warning(
                                f"[Fix 4] ACK retry 후에도 매칭 실패: "
                                f"order_id={order_id} code={code} — 외부 개입 의심"
                            )
                    return
                elif rctf in ("1", "2"):
                    # Fix 3e (R-18): RCTF=2 (취소) ACK 의 자체 cancel false positive 차단.
                    # cancel_buy_orders / cancel_and_reorder_buy2 가 cancel_order REST 발주
                    # 직전 등록한 oorder_id 를 set 에서 확인. 일치 시 자체 cancel ACK 정상.
                    if rctf == "2" and oorder_id in self.trader._self_cancelled_order_ids:
                        logger.info(
                            f"[Fix 3e] 자체 cancel ACK 수신: "
                            f"OODER={oorder_id} code={code} → 정상 처리"
                        )
                        self.trader._self_cancelled_order_ids.discard(oorder_id)
                        return

                    # 정정(1)/취소(2) — AUTOTRADE 는 정정/취소 발주 안 함 → 외부 개입 확정
                    action = "정정" if rctf == "1" else "취소"
                    logger.critical(
                        f"[R15-005] 외부 개입 감지: {action} 접수 통보 "
                        f"ODER={order_id} OODER={oorder_id} code={code}"
                    )
                    self.notifier.notify_error(
                        f"🚨 외부 개입 감지 ({action})\n"
                        f"종목: {code}\n"
                        f"주문번호: {order_id}\n"
                        f"원주문: {oorder_id}\n"
                        f"AUTOTRADE 는 {action} 발주 안 함 → HTS/MTS 확인 필요"
                    )
                    return
                else:
                    logger.warning(
                        f"[R15-005] 알 수 없는 RCTF_CLS={rctf!r}, ODER={order_id}, parsed={parsed}"
                    )
                    return

            # ── 케이스 2: 체결 통보 (CNTG_YN=2) ──
            if cntg_yn == "2":
                # R-17: ORD_EXG_GB 로깅 (Decision D = KRX 고정, 응답 1 또는 3 예상)
                ord_exg_gb = parsed.get("ORD_EXG_GB", "")
                exg_label = {
                    "1": "KRX", "2": "NXT", "3": "SOR-KRX", "4": "SOR-NXT"
                }.get(ord_exg_gb, f"UNK({ord_exg_gb})")
                logger.info(
                    f"[체결통보] {code} "
                    f"{'매수' if side == '02' else ('매도' if side == '01' else side)} "
                    f"체결 EXG={exg_label}"
                )

                try:
                    filled_price = int(parsed.get("CNTG_UNPR", "0"))
                    filled_qty = int(parsed.get("CNTG_QTY", "0"))
                except (ValueError, TypeError) as e:
                    logger.error(
                        f"[R15-005] 체결 수량/가격 파싱 실패: {e}, parsed={parsed}"
                    )
                    return

                if filled_price <= 0 or filled_qty <= 0:
                    logger.error(
                        f"[R15-005] 체결 수량/가격 이상: "
                        f"price={filled_price} qty={filled_qty}, parsed={parsed}"
                    )
                    return

                if side == "02":
                    # 매수 체결
                    order = self.trader.on_live_buy_filled(order_id, filled_price, filled_qty, ts)
                    if order is None:
                        # F2' (W-SAFETY-1): 정책 확정 — 외부 거래 금지.
                        # unmatched = Timeout 오진 또는 동기 안 맞은 체결 의심.
                        _active = self._coordinator.active
                        if _active and _active.code == code:
                            logger.critical(
                                f"[F2-Timeout] 자기 종목 매수 체결 매칭 실패 — "
                                f"Timeout 오진 의심\n"
                                f"code={code} active={_active.name} "
                                f"order_id={order_id} "
                                f"price={filled_price:,} qty={filled_qty}"
                            )
                            self.notifier.notify_error(
                                f"🚨 자기 종목 체결 매칭 실패 (F2-Timeout)\n"
                                f"종목: {code} ({_active.name})\n"
                                f"체결: {filled_qty}주 @ {filled_price:,}원\n"
                                f"order_id={order_id}\n"
                                f"AUTOTRADE 는 체결 인지 못함 → HTS 즉시 확인 필요"
                            )
                        else:
                            logger.critical(
                                f"[F2-Unmatched] 매수 체결통보 unmatched, active 불일치\n"
                                f"code={code} active_code={self._coordinator._active_code} "
                                f"order_id={order_id} price={filled_price:,} qty={filled_qty}"
                            )
                            self.notifier.notify_error(
                                f"🚨 매수 체결 unmatched (active 불일치)\n"
                                f"code={code} order_id={order_id}\n"
                                f"HTS 확인 필요"
                            )
                        return
                    # Coordinator 에 매수 체결 라우팅 (Watcher.on_buy_filled → ENTERED 전이 + T2 콜백)
                    # Phase 2 B: order 도 전달 (dual-write position.buy_orders list 관리용)
                    self._coordinator.on_buy_filled(
                        order.code, order.label, filled_price, filled_qty, ts, order=order
                    )
                    # Phase 2 B: dual-write 정합성 검증
                    self._check_position_invariant(order, label="buy")
                    return

                elif side == "01":
                    # 매도 체결
                    order = self.trader.on_live_sell_filled(order_id, filled_price, filled_qty, ts)
                    if order is None:
                        # F2' 매도 (W-SAFETY-1): 매도 체결통보 매칭 실패.
                        # 특히 위험: 청산 실패로 인식 → T3 연쇄 중단.
                        _active = self._coordinator.active
                        if _active and _active.code == code:
                            logger.critical(
                                f"[F2-Timeout] 자기 종목 매도 체결 매칭 실패 — "
                                f"청산 처리 누락 의심\n"
                                f"code={code} active={_active.name} "
                                f"order_id={order_id} "
                                f"price={filled_price:,} qty={filled_qty}"
                            )
                            self.notifier.notify_error(
                                f"🚨 자기 종목 매도 매칭 실패 (F2-Timeout)\n"
                                f"종목: {code} ({_active.name})\n"
                                f"체결: {filled_qty}주 @ {filled_price:,}원\n"
                                f"order_id={order_id}\n"
                                f"청산 처리 꼬임 가능 → T3 연쇄 중단 위험\n"
                                f"HTS 즉시 확인 필요"
                            )
                        else:
                            logger.critical(
                                f"[F2-Unmatched] 매도 체결통보 unmatched, active 불일치\n"
                                f"code={code} active_code={self._coordinator._active_code} "
                                f"order_id={order_id} price={filled_price:,} qty={filled_qty}"
                            )
                            self.notifier.notify_error(
                                f"🚨 매도 체결 unmatched (active 불일치)\n"
                                f"code={code} order_id={order_id}\n"
                                f"HTS 확인 필요"
                            )
                        return
                    # Coordinator 에 매도 체결 라우팅 (avg_sell_price 갱신)
                    # Phase 2 B: order 도 전달 (dual-write position.sell_orders list 관리용)
                    self._coordinator.on_sell_filled(
                        order.code, filled_price, filled_qty, ts, order=order
                    )
                    # Phase 2 B: dual-write 정합성 검증 (매도는 total_qty 차감 후 검증)
                    self._check_position_invariant(order, label="sell")
                    # 전량 체결 확정 시 on_sell_complete 체인 발화 → _on_exit_done
                    pos = self.trader.position
                    if pos is not None and not pos.is_open:
                        watcher = next(
                            (w for w in self._coordinator.watchers if w.code == order.code),
                            None,
                        )
                        if watcher is not None:
                            logger.info(
                                f"[R15-005] 매도 전량 체결 확정 → on_sell_complete 발화: "
                                f"{watcher.name} code={order.code}"
                            )
                            await self._coordinator.on_sell_complete(watcher, ts)
                        else:
                            logger.error(
                                f"[R15-005] 매도 전량 체결했으나 watcher 미일치: "
                                f"code={order.code}"
                            )
                    return

                else:
                    logger.warning(
                        f"[R15-005] 알 수 없는 SELN_BYOV_CLS={side!r}, ODER={order_id}, parsed={parsed}"
                    )
                    return

            # ── 케이스 3: 알 수 없는 CNTG_YN ──
            logger.warning(f"[R15-005] 알 수 없는 CNTG_YN={cntg_yn!r}, parsed={parsed}")

        except Exception as e:
            logger.exception(f"[R15-005] _process_execution_notify 예외: {e}, parsed={parsed}")

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
        """청산 완료 콜백. Coordinator.on_sell_complete 가 호출 (R15-005).

        책임:
        1. P&L 기록
        2. R-12: 손절 횟수 기록 (hard_stop인 경우)
        3. TradeLogger로 거래 기록 (DB 저장만, 텔레그램은 10 단계에서)
        4. 손실 한도 체크 (get_balance 사용 — 기존 동작 유지)
        5. 멀티 트레이드 가드
        6. 다음 매매 가용 예수금 갱신 (get_buy_available 사용 — R15-007)
        7. trader.reset
        8. T3 위임 (W-11e 두 번째 매매 트리거)
        9. 대시보드 동기화
        10. What-if 시나리오 시뮬 + 통합 텔레그램 전송 (분봉 API + scenario_sim)
        """
        # 1. P&L 기록
        pnl = self.trader.get_pnl(watcher.current_price)
        self.risk.record_trade_result(pnl)

        # 2. R-12 재설계: 하드손절 처리 (1회=strict, 2회=halt)
        if watcher.exit_reason == "hard_stop":
            result = self.risk.record_hard_stop()
            if result["halted"]:
                self.notifier.notify_error(
                    f"🚨 하드손절 {self.risk._hard_stop_count}회 → 당일 매매 중단\n"
                    f"{self.risk.halt_reason}"
                )
            elif result["entered_strict"]:
                self.notifier.notify_error(
                    f"⚠️ 하드손절 청산 → 당일 strict 모드 진입\n"
                    f"KOSPI 비중 ≥{self.params.risk.strict_mode_program_ratio_threshold}% 종목만 매수 허용"
                )

        # 3. TradeLogger로 거래 기록 (DB 저장 + 로그 출력)
        #    텔레그램 전송은 10 단계(시나리오 시뮬 포함)로 이동
        record = None
        try:
            record = self._trade_logger.record_trade(
                watcher, self.trader, trade_mode=self.settings.trade_mode
            )
        except Exception as e:
            logger.error(f"거래 로그 저장 실패 ({watcher.name}): {e}")

        # 4. 손실 한도 체크 (기존 동작 유지 — get_balance().available_cash 사용)
        # R15-007 범위 밖 (손실률 분모는 별도 이슈로 분리).
        try:
            balance = await self.api.get_balance()
            self.risk.check_daily_loss_limit(balance.get("available_cash", 0))
        except Exception as e:
            logger.error(f"잔고 조회 실패 (손실 한도 체크): {e}")

        # 5. 멀티 트레이드 가드
        mt = self.params.multi_trade
        if not mt.enabled:
            await self._fire_state_update()
            return

        # 6. 다음 매매 가용 예수금 갱신 (R15-007: get_buy_available 사용)
        # 매도 체결 직후 KIS 서버 nrcvb_buy_amt 에 P&L 반영 기대.
        # 첫 매매 청산 후 두 번째 매수 시점에 P&L 반영된 합산 금액 조회.
        # 타이밍 이슈 발견 시 안 B (내부 계산 + 교차 검증) 로 전환 가능.
        try:
            prev_cash = self._available_cash
            buy_info = await self.api.get_buy_available()
            new_cash = self.risk.calculate_available_cash(buy_info["buyable_cash"])
            self._available_cash = new_cash
            self._coordinator.set_available_cash(new_cash)
            logger.info(
                f"[R15-007] 청산 후 예수금 갱신:"
                f"\n  직전 _available_cash:      {prev_cash:,}원"
                f"\n  buyable_cash (API):        {buy_info['buyable_cash']:,}원  (P&L {int(pnl):+,} 반영 기대)"
                f"\n  ord_psbl_cash:             {buy_info['ord_psbl_cash']:,}원"
                f"\n  ruse_psbl_amt:             {buy_info['ruse_psbl_amt']:,}원"
                f"\n  다음 매매 가용 (new_cash): {new_cash:,}원"
            )
        except Exception as e:
            logger.error(f"잔고 조회 실패 (다음 매매 준비): {e}")

        # 7. trader.reset (다음 종목 매수 준비)
        self.trader.reset()

        # === 8. T3 위임 (W-11e: 두 번째 매매 트리거) ===
        # _on_exit_done 진입 시점에 _active_code 는 여전히 청산된 종목 코드.
        # handle_t3 가 _execute_buy 를 호출하기 전에 _active_code 를 None 으로 명시 처리.
        # 이렇게 하면 handle_t3 안에서 새 chosen.code 로 _active_code 가 atomic 하게 교체됨.
        self._coordinator._active_code = None
        await self._coordinator.handle_t3(now_kst())

        # 9. 대시보드 동기화
        await self._fire_state_update()

        # === 10. What-if 시나리오 시뮬 + 통합 텔레그램 전송 ===
        # handle_t3 후에 실행 — 매매 경로에 0 줄 영향 (결과는 이미 확정된 상태).
        # 실패 시 fallback: 시나리오 없이 기본 notify_trade_complete 로 전송.
        if record is None:
            return
        try:
            await self._send_trade_complete_with_scenarios(record)
        except Exception as e:
            logger.error(f"시나리오 시뮬 실패 ({watcher.name}): {e}")
            # 최소한 기본 리포트는 전송
            try:
                self.notifier.notify_trade_complete(record)
            except Exception as e2:
                logger.error(f"기본 리포트 전송도 실패: {e2}")

    async def _send_trade_complete_with_scenarios(self, record) -> None:
        """분봉 차트 조회 + 60/70% 시뮬 실행 + 통합 리포트 전송 + 분봉 스냅샷 저장.

        매매 루프에 영향 0: _on_exit_done 9 단계 완료 후 호출됨.
        실패해도 기본 리포트는 Step 10 의 except 블록에서 fallback 실행.
        """
        from src.utils.scenario_sim import run_scenarios

        if record.buy1_time is None or record.total_buy_qty <= 0:
            # 시뮬 조건 미충족 — 기본 리포트만
            self.notifier.notify_trade_complete(record)
            return

        # 분봉 차트 조회 (KIS REST, UN 채널)
        candles = []
        try:
            candles = await self.api.get_minute_chart(record.code)
        except Exception as e:
            logger.warning(f"분봉 차트 조회 실패 ({record.name}): {e}")

        # 분봉 스냅샷 파일 저장 (batch 재생성/검증용)
        try:
            self._save_minute_chart_snapshot(record, candles)
        except Exception as e:
            logger.debug(f"분봉 스냅샷 저장 실패 ({record.name}): {e}")

        if not candles:
            self.notifier.notify_trade_complete(record)
            return

        # 시뮬 초기 low = buy1_price (체결 시점 저점 = 매수가)
        initial_low = record.buy1_price if record.buy1_price > 0 else int(record.avg_buy_price)
        scenarios = run_scenarios(
            minute_chart=candles,
            confirmed_high=record.new_high_price,
            initial_low=initial_low,
            buy_time=record.buy1_time,
            recovery_pcts=[60.0, 70.0],
            force_time=self.params.exit.force_liquidate_time,
        )

        self.notifier.notify_trade_complete_with_scenarios(record, scenarios)

    def _save_minute_chart_snapshot(self, record, candles: list[dict]) -> None:
        """분봉 스냅샷을 파일로 저장. batch 재실행 시 재사용.

        경로: logs/minute_charts/YYYY-MM-DD/{code}_{name}.json
        """
        if not candles:
            return
        date_str = record.trade_date.isoformat() if hasattr(record.trade_date, "isoformat") else str(record.trade_date)
        out_dir = Path("logs") / "minute_charts" / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c for c in record.name if c.isalnum() or c in ("_", "-"))
        out_path = out_dir / f"{record.code}_{safe_name}.json"
        payload = {
            "code": record.code,
            "name": record.name,
            "trade_date": date_str,
            "candles": candles,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug(f"분봉 스냅샷 저장: {out_path}")

    async def _ratio_updater(self) -> None:
        """R-13: 프로그램 순매수 비중 실시간 업데이트.

        1초 간격으로 WATCHING/TRIGGERED/BUY1_FILLED 상태 Watcher의 비중 조회.
        비중 변경 시 is_double_digit 갱신 및 가격 재계산.

        R16: is_paper_mode 분기 제거 (LIVE 전용, 1.0초 간격 고정).
        """
        interval = 1.0  # LIVE: 1초 간격 (rate limit 20건/초 충분)

        # 비중 업데이트 대상 상태
        target_states = {
            WatcherState.WATCHING,
            WatcherState.TRIGGERED,
            WatcherState.READY,
            WatcherState.ENTERED,
        }

        while self._running:
            try:
                await asyncio.sleep(interval)

                for w in self._coordinator.watchers:
                    if w.state not in target_states:
                        continue

                    try:
                        # 1. REST 호출
                        prog = await self.api.get_program_trade(w.code)
                        price_info = await self.api.get_current_price(w.code)
                        trading_value = price_info.get("trading_value", 0)

                        # 2. 비중 계산
                        if trading_value > 0:
                            net_buy = prog.get("program_net_buy", 0)
                            new_ratio = (net_buy / trading_value) * 100
                        else:
                            new_ratio = 0.0

                        # 3. 변경 감지
                        old_ratio = w.program_ratio
                        old_is_double = w.is_double_digit
                        new_is_double = new_ratio >= self.params.screening.program_net_buy_ratio_double

                        # 비중 갱신
                        w.program_ratio = new_ratio

                        # 4. Double/Single 상태 변경 시 처리
                        if old_is_double != new_is_double:
                            w.is_double_digit = new_is_double

                            logger.info(
                                f"[{w.name}] 비중 변경: {old_ratio:.1f}% → {new_ratio:.1f}% "
                                f"({'Double' if new_is_double else 'Single'})"
                            )

                            # 5. 상태별 처리
                            if w.state == WatcherState.TRIGGERED:
                                # 1차 미체결 상태: 가격만 재계산
                                old_buy1 = w.target_buy1_price
                                old_stop = w.hard_stop_price_value

                                w._recalc_prices()

                                logger.info(
                                    f"[{w.name}] 가격 재계산: "
                                    f"buy1 {old_buy1:,} → {w.target_buy1_price:,}, "
                                    f"stop {old_stop:,} → {w.hard_stop_price_value:,}"
                                )

                            elif w.state == WatcherState.ENTERED:
                                # 1차 체결 상태: 2차 재발주 (A안 확정)
                                old_buy2 = w.target_buy2_price
                                old_stop = w.hard_stop_price_value

                                w._recalc_prices()

                                logger.info(
                                    f"[{w.name}] 가격 재계산: "
                                    f"buy2 {old_buy2:,} → {w.target_buy2_price:,}, "
                                    f"stop {old_stop:,} → {w.hard_stop_price_value:,}"
                                )

                                # 2차 미체결 시 재발주 (R15-007: get_buy_available 사용)
                                if not w.buy2_filled and w.buy2_order_id:
                                    buy_info = await self.api.get_buy_available()
                                    cash = buy_info["buyable_cash"]

                                    success = await self.trader.cancel_and_reorder_buy2(
                                        w, w.target_buy2_price, cash
                                    )
                                    if success:
                                        logger.info(f"[{w.name}] buy2 재발주 완료")

                    except Exception as e:
                        logger.warning(f"[{w.name}] 비중 업데이트 실패: {e}")
                        continue

            except asyncio.CancelledError:
                logger.info("_ratio_updater 종료")
                break
            except Exception as e:
                logger.error(f"_ratio_updater 에러: {e}")
                await asyncio.sleep(5)  # 에러 시 5초 대기 후 재시도

    async def _futures_staleness_monitor(self) -> None:
        """STALENESS-01: 선물가 30초 이상 무수신 시 Telegram 경고 (최초 1회) + 회복 시 1회.

        - 10초 간격 폴링
        - 30초 초과 = stale → 최초 감지 1회 알림
        - 회복 시 1회 알림

        Watcher._check_futures_drop 의 staleness guard 와 독립 작동:
        - Watcher 쪽은 청산 평가 시점에 skip (fail-open) 결정
        - 이 모니터는 운영자 알림 채널 (수석님이 수동 판단 개입 가능하게)
        """
        interval = 10.0
        threshold = 30.0
        while self._running:
            try:
                await asyncio.sleep(interval)
                if self._futures_last_update_ts is None:
                    continue
                age_sec = (now_kst() - self._futures_last_update_ts).total_seconds()
                if age_sec > threshold:
                    if not self._futures_stale_alerted:
                        self._futures_stale_alerted = True
                        logger.warning(
                            f"선물가 staleness 감지: {age_sec:.0f}초 무수신 → 청산 조건 ④ skip"
                        )
                        try:
                            self.notifier.notify_error(
                                f"⚠️ 선물가 {age_sec:.0f}초 무수신\n"
                                f"청산 조건 ④ 일시 skip (fail-open)"
                            )
                        except Exception as e:
                            logger.error(f"staleness 알림 실패: {e}")
                else:
                    if self._futures_stale_alerted:
                        self._futures_stale_alerted = False
                        logger.info(f"선물가 staleness 회복 (age={age_sec:.0f}초)")
                        try:
                            self.notifier.notify_system("✅ 선물가 수신 회복")
                        except Exception as e:
                            logger.error(f"staleness 회복 알림 실패: {e}")
            except asyncio.CancelledError:
                logger.info("_futures_staleness_monitor 종료")
                break
            except Exception as e:
                logger.error(f"_futures_staleness_monitor 에러: {e}")
                await asyncio.sleep(5)


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
