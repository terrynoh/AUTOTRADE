"""Watcher 모듈 — 매매 로직의 핵심 자료구조.

이 모듈은 한 종목에 대한 *매매 결정 단위* 인 Watcher 와, 3종목 watcher
들을 *조정* 하는 WatcherCoordinator 를 포함한다.

설계 원칙:
- single source of truth: 한 종목 = 한 Watcher
- 역할 분리: Watcher = 결정, Position (별도 모듈) = 결과, Trader = 발주
- 시세 라우팅 단방향: WatcherCoordinator → Watcher (역방향 X)
- ENTERED 상태에서는 시나리오 동결 (신고가 갱신 / 목표가 재계산 X)
- terminal state (DROPPED / EXITED / SKIPPED) 는 돌아오지 않음

W-05a 범위: WatcherState enum + Watcher dataclass + 메서드 14개
W-05b 범위: WatcherCoordinator 클래스 (이 파일에 추가)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Optional

from loguru import logger

from config.settings import StrategyParams
from src.models.stock import MarketType


# ── WatcherState enum ────────────────────────────────────


class WatcherState(str, Enum):
    """Watcher 의 8개 상태.

    전이 그래프:
        WATCHING → TRIGGERED → READY ↔ PASSED → DROPPED (terminal)
                                ↓
                              ENTERED → EXITED (terminal)
        WATCHING/TRIGGERED/READY/PASSED → SKIPPED (terminal, 10:55 매수 마감)
    """

    WATCHING = "watching"          # 신고가 감시 중. 1% 하락 트리거 미발동.
    TRIGGERED = "triggered"        # 1% 하락 트리거 발동. 목표진입가 결정. 매수 발주 가능.
    READY = "ready"                # 현재가가 1차 ~ 손절 사이 (YES). active 비었으면 발주.
    PASSED = "passed"              # 현재가가 1차 위 (NO). 자리에서 지나감. 추적 계속.
    DROPPED = "dropped"            # 현재가가 손절 아래 (terminal). 그날 폐기.
    ENTERED = "entered"            # 1차 체결됨 (active position). 5조건 청산 평가.
    EXITED = "exited"              # 청산 완료 (terminal).
    SKIPPED = "skipped"            # 10:55 매수 마감 도달 (terminal). ENTERED 는 영향 없음.


TERMINAL_STATES = frozenset({
    WatcherState.DROPPED,
    WatcherState.EXITED,
    WatcherState.SKIPPED,
})


# ── Watcher dataclass ────────────────────────────────────


@dataclass
class Watcher:
    """한 종목에 대한 매매 결정 단위.

    한 종목 = 한 Watcher = 모든 진실 (single source of truth).
    상태/필드/메서드를 가지지만 *발주는 직접 안 함* (Trader 에 위임).
    """

    # === 종목 정보 (불변) ===
    code: str
    name: str
    market: MarketType
    params: StrategyParams = field(repr=False)

    # === 상태 ===
    state: WatcherState = WatcherState.WATCHING

    # === 시세 ===
    current_price: int = 0

    # === 09:50~09:55 사전 고가 추적 (Q2 보존) ===
    pre_955_high: int = 0

    # === 신고가 추적 ===
    intraday_high: int = 0
    intraday_high_time: Optional[datetime] = None
    new_high_achieved: bool = False

    # === TRIGGERED 시점 스냅샷 ===
    confirmed_high: int = 0
    confirmed_high_time: Optional[datetime] = None
    futures_at_confirmed_high: float = 0.0
    target_buy1_price: int = 0
    target_buy2_price: int = 0
    hard_stop_price_value: int = 0
    high_confirmed_at: Optional[datetime] = None

    # === 선물 가격 (외부 주입) ===
    futures_price: float = 0.0

    # === 매수 발주 / 체결 추적 ===
    buy1_pending: bool = False
    buy2_pending: bool = False
    buy1_placed: bool = False                     # 1차 매수 주문 발주 완료 (체결 무관)
    buy2_placed: bool = False                     # 2차 매수 주문 발주 완료
    buy1_filled: bool = False
    buy2_filled: bool = False
    buy1_order_id: str = ""
    buy2_order_id: str = ""
    buy1_price: int = 0
    buy2_price: int = 0
    total_buy_amount: int = 0
    total_buy_qty: int = 0

    # === ENTERED 정보 ===
    entered_at: Optional[datetime] = None
    post_entry_low: int = 0
    post_entry_low_time: Optional[datetime] = None

    # === 청산 정보 ===
    exit_reason: str = ""
    exit_price: int = 0
    exited_at: Optional[datetime] = None
    avg_sell_price: int = 0
    total_sell_amount: int = 0
    sell_orders: list = field(default_factory=list)

    # === 시그널 ===
    _exit_signal_pending: bool = False

    # === 캐시된 시각 ===
    _watch_start: Optional[time] = None
    _entry_deadline: Optional[time] = None

    def __post_init__(self):
        """파라미터 캐싱."""
        self._watch_start = time.fromisoformat(self.params.entry.new_high_watch_start)
        self._entry_deadline = time.fromisoformat(self.params.entry.entry_deadline)

    # ── Property ─────────────────────────────────────────

    @property
    def target_price(self) -> int:
        """매도 목표가. (confirmed_high + post_entry_low) / 2.

        M-7 결과: post_entry_low 가 갱신될 때마다 target_price 도 매 틱 변동.
        하락 중 매도 자리가 점점 멀어지는 의도된 비대칭 동작.
        """
        if self.post_entry_low <= 0 or self.confirmed_high <= 0:
            return 0
        return int((self.confirmed_high + self.post_entry_low) / 2)

    @property
    def is_yes(self) -> bool:
        """현재 watcher 가 매수 자리 안에 있는가 (YES 신호)."""
        return self.state == WatcherState.READY

    @property
    def is_terminal(self) -> bool:
        """terminal 상태인가 (DROPPED / EXITED / SKIPPED)."""
        return self.state in TERMINAL_STATES

    def distance_to_buy1(self, price: int) -> int:
        """현재가가 1차 매수가에서 얼마나 떨어졌는가.

        T 시점 tie-breaker 용. 절대값이 작을수록 매수 자리에 가까움.
        음수: 자리 아래 (READY 영역). 양수: 자리 위 (PASSED 영역).
        """
        return price - self.target_buy1_price

    # ── 메서드 14개 ──────────────────────────────────────

    # (5-1) update_intraday_high

    def update_intraday_high(self, price: int, ts: datetime) -> bool:
        """고가 갱신. 갱신됐으면 True 반환."""
        if price > self.intraday_high:
            self.intraday_high = price
            self.intraday_high_time = ts
            return True
        return False

    # (5-2) update_post_entry_low

    def update_post_entry_low(self, price: int, ts: datetime) -> None:
        if self.post_entry_low == 0 or price < self.post_entry_low:
            self.post_entry_low = price
            self.post_entry_low_time = ts

    # (5-3) on_tick

    def on_tick(self, price: int, ts: datetime, futures_price: float) -> None:
        """실시간 체결가 수신 시 호출. state 별 분기."""
        if self.is_terminal:
            return

        self.current_price = price
        self.futures_price = futures_price

        if self.state == WatcherState.WATCHING:
            self._handle_watching(price, ts)
        elif self.state == WatcherState.TRIGGERED:
            self._handle_triggered(price, ts)
        elif self.state == WatcherState.READY:
            self._handle_triggered(price, ts)
        elif self.state == WatcherState.PASSED:
            self._handle_triggered(price, ts)
        elif self.state == WatcherState.ENTERED:
            self._handle_entered(price, ts)

    # (5-4) _handle_watching

    def _handle_watching(self, price: int, ts: datetime) -> None:
        """WATCHING 상태 처리.

        09:50~09:55: 사전 고가 추적 (pre_955_high)
        09:55 이후: 신고가 달성 평가 + 1% 하락 트리거 평가
        """
        # === A. 09:55 이전: 사전 고가 추적만 ===
        if ts.time() < self._watch_start:
            if price > self.pre_955_high:
                self.pre_955_high = price
                self.intraday_high = price
                self.intraday_high_time = ts
            return

        # === B. 매수 마감 시각 체크 (10:55) ===
        if ts.time() >= self._entry_deadline:
            self.state = WatcherState.SKIPPED
            logger.info(
                f"[{self.name}] 매수 진입 마감({self.params.entry.entry_deadline}) → 매매 안 함"
            )
            return

        # === C. 09:55 이후: 신고가 달성 평가 ===
        if not self.new_high_achieved:
            if price > self.pre_955_high:
                self.update_intraday_high(price, ts)
                self.new_high_achieved = True
                logger.info(
                    f"[{self.name}] 09:55 이후 신고가 달성: {price:,}원"
                )
            elif price > self.intraday_high:
                self.update_intraday_high(price, ts)
            return

        # === D. 신고가 달성 후: 1% 하락 트리거 평가 ===
        if self.update_intraday_high(price, ts):
            return

        drop_pct = self.params.entry.high_confirm_drop_pct
        trigger_price = int(self.intraday_high * (1 - drop_pct / 100))

        if price <= trigger_price:
            self._fire_trigger(price, ts)

    # (5-5) _fire_trigger

    def _fire_trigger(self, price: int, ts: datetime) -> None:
        """1% 하락 트리거 발동. WATCHING → TRIGGERED 전이."""
        self.confirmed_high = self.intraday_high
        self.confirmed_high_time = self.intraday_high_time
        self.futures_at_confirmed_high = self.futures_price
        self.high_confirmed_at = ts

        if self.market == MarketType.KOSPI:
            buy1_pct = self.params.entry.kospi_buy1_pct
            buy2_pct = self.params.entry.kospi_buy2_pct
            stop_pct = self.params.exit.kospi_hard_stop_pct
        else:
            buy1_pct = self.params.entry.kosdaq_buy1_pct
            buy2_pct = self.params.entry.kosdaq_buy2_pct
            stop_pct = self.params.exit.kosdaq_hard_stop_pct

        self.target_buy1_price = int(self.confirmed_high * (1 - buy1_pct / 100))
        self.target_buy2_price = int(self.confirmed_high * (1 - buy2_pct / 100))
        self.hard_stop_price_value = int(self.confirmed_high * (1 - stop_pct / 100))

        self.state = WatcherState.TRIGGERED
        logger.info(
            f"[{self.name}] 트리거 발동: 고가 {self.confirmed_high:,}원 → "
            f"현재 {price:,}원, 1차 {self.target_buy1_price:,} / "
            f"2차 {self.target_buy2_price:,} / 손절 {self.hard_stop_price_value:,}"
        )

        self._evaluate_target(price)

    # (5-6) _handle_triggered

    def _handle_triggered(self, price: int, ts: datetime) -> None:
        """TRIGGERED / READY / PASSED 상태 처리."""
        # === A. 매수 마감 (10:55) 체크 ===
        if ts.time() >= self._entry_deadline:
            self.state = WatcherState.SKIPPED
            logger.info(
                f"[{self.name}] 매수 진입 마감({self.params.entry.entry_deadline}) → SKIPPED"
            )
            return

        # === B. 10분 미체결 timeout (M-7-1) ===
        if self._check_high_confirm_timeout(ts):
            timeout_min = self.params.entry.high_confirm_timeout_min
            self.state = WatcherState.SKIPPED
            logger.info(
                f"[{self.name}] 트리거 후 {timeout_min}분 미체결 → 모멘텀 소멸 SKIPPED"
            )
            return

        # === C. 신고가 갱신 → WATCHING 복귀 ===
        if price > self.intraday_high:
            self.update_intraday_high(price, ts)
            self.confirmed_high = 0
            self.high_confirmed_at = None
            self.target_buy1_price = 0
            self.target_buy2_price = 0
            self.hard_stop_price_value = 0
            self.state = WatcherState.WATCHING
            self.new_high_achieved = True
            logger.info(
                f"[{self.name}] 트리거 후 신고가 갱신: {price:,}원 → WATCHING 복귀"
            )
            return

        # === D. 일반 평가 (READY/PASSED/DROPPED) ===
        self._evaluate_target(price)

    # (5-7) _evaluate_target

    def _evaluate_target(self, price: int) -> None:
        """현재가 평가 → READY / PASSED / DROPPED 전이.

        손절선 아래 → DROPPED (terminal)
        1차 매수가 위 → PASSED (NO)
        1차 ~ 손절 사이 → READY (YES)
        """
        if price <= self.hard_stop_price_value:
            self.state = WatcherState.DROPPED
            logger.info(
                f"[{self.name}] 손절선 아래: {price:,} ≤ {self.hard_stop_price_value:,} → DROPPED"
            )
            return

        if price > self.target_buy1_price:
            self.state = WatcherState.PASSED
        else:
            self.state = WatcherState.READY

    # (5-8) _check_high_confirm_timeout

    def _check_high_confirm_timeout(self, ts: datetime) -> bool:
        """TRIGGERED 진입 후 N분 미체결 timeout (M-7-1)."""
        if self.high_confirmed_at is None:
            return False
        timeout_min = self.params.entry.high_confirm_timeout_min
        return (ts - self.high_confirmed_at).total_seconds() >= timeout_min * 60

    # (5-9) _handle_entered

    def _handle_entered(self, price: int, ts: datetime) -> None:
        """ENTERED 상태에서 5조건 청산 평가."""
        self.update_post_entry_low(price, ts)

        # ① 하드 손절
        if price <= self.hard_stop_price_value:
            self._emit_exit("hard_stop", 0, ts)
            logger.warning(
                f"[{self.name}] 하드 손절: {price:,}원 ≤ {self.hard_stop_price_value:,}원"
            )
            return

        # ② 20분 타임아웃 (10시 이후 최저가 기준)
        if self._check_timeout(ts):
            self._emit_exit("timeout", price, ts)
            logger.warning(f"[{self.name}] 20분 타임아웃")
            return

        # ③ 목표가
        target_price = self.target_price
        if target_price > 0 and price >= target_price:
            self._emit_exit("target", target_price, ts)
            logger.info(
                f"[{self.name}] 목표가 도달: {price:,}원 ≥ {target_price:,}원"
            )
            return

        # ④ 선물 급락
        if self._check_futures_drop():
            self._emit_exit("futures_stop", 0, ts)
            logger.warning(
                f"[{self.name}] 선물 급락: "
                f"고점시각 {self.futures_at_confirmed_high:.2f} → 현재 {self.futures_price:.2f}"
            )
            return

        # ⑤ 강제 청산은 Coordinator.on_force_liquidate 에서 처리

    # (5-10) _check_timeout

    def _check_timeout(self, ts: datetime) -> bool:
        """눌림 최저가 시점부터 N분 경과. 10시 이전 최저가는 타이머 시작 안 함."""
        if self.post_entry_low_time is None:
            return False

        guard = time.fromisoformat(self.params.exit.timeout_start_after_kst)
        if self.post_entry_low_time.time() < guard:
            return False

        timeout = timedelta(minutes=self.params.exit.timeout_from_low_min)
        return (ts - self.post_entry_low_time) >= timeout

    # (5-11) _check_futures_drop

    def _check_futures_drop(self) -> bool:
        """종목 고점 시각의 선물가 대비 N% 하락."""
        if self.futures_at_confirmed_high <= 0 or self.futures_price <= 0:
            return False
        drop_pct = (self.futures_at_confirmed_high - self.futures_price) / self.futures_at_confirmed_high * 100
        return drop_pct >= self.params.exit.futures_drop_pct

    # (5-12) _emit_exit

    def _emit_exit(self, reason: str, price: int, ts: datetime) -> None:
        """청산 신호 발화. Coordinator 가 폴링 후 발주."""
        self._exit_signal_pending = True
        self.exit_reason = reason
        self.exit_price = price
        self.exited_at = ts
        self.state = WatcherState.EXITED

    # (5-13) force_exit

    def force_exit(self, ts: datetime) -> None:
        """11:20 강제 청산. ENTERED 만 처리, 다른 state 는 no-op."""
        if self.state == WatcherState.ENTERED:
            self._emit_exit("force", 0, ts)
            logger.warning(f"[{self.name}] 11:20 강제 청산")

    # (5-14) on_buy_filled

    def on_buy_filled(self, label: str, filled_price: int, filled_qty: int, ts: datetime) -> None:
        """매수 체결 통보. 첫 체결 시 ENTERED 전이 + post_entry_low 초기화."""
        self.total_buy_amount += filled_price * filled_qty
        self.total_buy_qty += filled_qty

        if label == "buy1":
            self.buy1_filled = True
            self.buy1_pending = False
            self.buy1_price = filled_price
        elif label == "buy2":
            self.buy2_filled = True
            self.buy2_pending = False
            self.buy2_price = filled_price

        if self.state != WatcherState.ENTERED:
            self.state = WatcherState.ENTERED
            self.entered_at = ts
            self.post_entry_low = filled_price
            self.post_entry_low_time = ts

        avg = self.total_buy_amount / self.total_buy_qty if self.total_buy_qty > 0 else 0
        logger.info(
            f"[{self.name}] {label} 체결: {filled_price:,}원 × {filled_qty}주 (평단 {avg:,.0f}원)"
        )


# ──────────────────────────────────────────────────────────────────
# WatcherCoordinator — 3종목 watcher 관리 + 매매 동시성 + T 시점 평가
# ──────────────────────────────────────────────────────────────────


class WatcherCoordinator:
    """3종목 watcher 의 매매 동시성 관리.

    책임:
    - watchers: list[Watcher] 관리 (최대 3개)
    - _active_code: Optional[str] (single active rotation)
    - 시세 라우팅 (모든 watcher 에 on_tick)
    - 청산 신호 폴링 + 매수 발주 평가 (_process_signals)
    - T 시점 평가 (active 청산 후 다음 watcher 진입)
    - 시각 기반 이벤트: on_buy_deadline (10:55), on_force_liquidate (11:20)

    Trader 와의 결합:
    - __init__ 에서 trader 인스턴스를 받음 (DI)
    - Trader 가 Watcher 호환 시그니처로 실연결 (W-05c)
    """

    def __init__(self, params: StrategyParams, trader=None):
        """
        Args:
            params: StrategyParams (yaml 로드 결과)
            trader: Trader 인스턴스 (W-05c 후 호출됨, W-05b 에서는 보관만)
        """
        self.params = params
        self.trader = trader
        self.watchers: list[Watcher] = []
        self._active_code: Optional[str] = None
        self._latest_futures_price: float = 0.0
        self._available_cash: int = 0  # main.py 에서 주입
        self._exit_callback = None  # 청산 후 콜백 (main.py 에서 주입)

        # 매수 마감 / 강제 청산 시각 (캐싱)
        self._entry_deadline = time.fromisoformat(params.entry.entry_deadline)
        self._force_liquidate_time = time.fromisoformat(params.exit.force_liquidate_time)

        # 일별 상태 (start_screening 시 리셋)
        self._screening_done: bool = False

    def set_available_cash(self, amount: int) -> None:
        """매수 가능 현금 갱신. main.py 가 호출."""
        self._available_cash = amount

    def set_exit_callback(self, callback) -> None:
        """청산 후 콜백 등록. main.py 가 호출.

        콜백 시그니처: async def callback(watcher: Watcher) -> None
        호출 시점: _execute_exit 가 trader.execute_exit 끝낸 직후
        """
        self._exit_callback = callback

    @property
    def active(self) -> Optional[Watcher]:
        """현재 active position 보유 watcher (없으면 None)."""
        if self._active_code is None:
            return None
        return next((w for w in self.watchers if w.code == self._active_code), None)

    @property
    def has_active(self) -> bool:
        """active position 이 있는가."""
        return self._active_code is not None

    # ── 3-1. start_screening ─────────────────────────────

    def start_screening(self, candidates: list) -> None:
        """09:50 스크리닝 결과 받아 watcher 3개 가동.

        Args:
            candidates: list[StockCandidate] — screener.run_manual 결과
        """
        if self._screening_done:
            logger.warning("스크리닝 이미 완료됨. 중복 호출 무시.")
            return

        # watcher 생성 (top_n_gainers 만큼)
        top_n = self.params.screening.top_n_gainers
        selected = candidates[:top_n]

        self.watchers = []
        for cand in selected:
            w = Watcher(
                code=cand.code,
                name=cand.name,
                market=cand.market,
                params=self.params,
            )
            # 09:50 시점 사전 고가 / current_price 초기화
            w.intraday_high = cand.current_price
            w.pre_955_high = cand.current_price
            w.current_price = cand.current_price
            self.watchers.append(w)
            logger.info(
                f"[Coordinator] Watcher 가동: {cand.name}({cand.code}) {cand.market.value}"
            )

        self._screening_done = True
        self._active_code = None
        logger.info(f"[Coordinator] {len(self.watchers)}개 watcher 시작")

    # ── 3-2. on_realtime_price / on_realtime_futures ──────

    async def on_realtime_price(self, code: str, price: int, ts: datetime) -> None:
        """KIS WebSocket 체결가 수신. 해당 종목 watcher 에 라우팅.

        모든 watcher 에 라우팅 (terminal 제외). _active_monitor 단독 폴링 X.
        ISSUE-036 의 결함 (single watcher 폴링) 을 해소.
        """
        for w in self.watchers:
            if w.code == code and not w.is_terminal:
                w.on_tick(price, ts, self._latest_futures_price)

        # 매 틱 후 신호 폴링 + 매수 발주 평가
        await self._process_signals(ts)

    def on_realtime_futures(self, futures_price: float) -> None:
        """선물 가격 갱신. 다음 on_tick 에서 사용."""
        self._latest_futures_price = futures_price

    # ── 3-3. _process_signals ────────────────────────────

    async def _process_signals(self, ts: datetime) -> None:
        """매 틱 후 호출. 청산 신호 처리 + 매수 발주 평가.

        흐름:
        0. DRY_RUN 시뮬레이션 (active 의 미체결 매수)
        1. active watcher 의 _exit_signal_pending 처리 (청산 발주 + active 해제)
        2. active 가 없으면 yes_watchers 평가 (매수 발주)
        """
        # === 0. DRY_RUN 시뮬레이션 (active watcher 의 미체결 매수만) ===
        active = self.active
        if active is not None and self.trader is not None:
            if self.trader.settings.is_dry_run and (active.buy1_pending or active.buy2_pending):
                filled = self.trader.simulate_fills(active, active.current_price, ts)
                for label in filled:
                    active.on_buy_filled(label, active.current_price, 0, ts)

        # === 1. active 청산 신호 처리 ===
        active = self.active
        if active is not None and active._exit_signal_pending:
            await self._execute_exit(active, ts)
            self._active_code = None
            # T 시점: active 가 비었음. 다음 매수 평가는 다음 틱에서 자동 처리
            return

        # === 2. active 가 비어있으면 매수 발주 평가 ===
        if self._active_code is not None:
            return  # active 잠금 (체결 대기 또는 청산 대기)

        # 매수 마감 시각 체크
        if self._is_after_buy_deadline(ts):
            return

        # YES watcher 모두 수집
        yes_watchers = [w for w in self.watchers if w.is_yes and not w.is_terminal]
        if not yes_watchers:
            return

        # tie-breaker: 1차 매수가에 가장 가까운 종목
        chosen = min(
            yes_watchers,
            key=lambda w: abs(w.distance_to_buy1(w.current_price))
        )

        await self._execute_buy(chosen, ts)
        self._active_code = chosen.code  # 즉시 active 잠금 (체결 대기)

    # ── 3-4. _execute_buy ────────────────────────────────

    async def _execute_buy(self, watcher: Watcher, ts: datetime) -> None:
        """매수 1차 + 2차 발주. Trader 호출."""
        watcher.buy1_pending = True
        watcher.buy2_pending = True

        logger.info(
            f"[Coordinator] 매수 발주: {watcher.name} "
            f"1차 {watcher.target_buy1_price:,} / 2차 {watcher.target_buy2_price:,}"
        )

        if self.trader is not None:
            await self.trader.place_buy_orders(watcher, self._available_cash)

    # ── 3-5. _execute_exit ───────────────────────────────

    async def _execute_exit(self, watcher: Watcher, ts: datetime) -> None:
        """청산 발주. Trader 호출 후 콜백 통지."""
        logger.warning(
            f"[Coordinator] 청산 발주: {watcher.name} "
            f"reason={watcher.exit_reason} price={watcher.exit_price}"
        )

        if self.trader is not None:
            await self.trader.execute_exit(
                watcher, watcher.exit_reason, watcher.exit_price
            )

        # 청산 후 콜백 통지 (main.py 가 TradeRecord 생성 / DB 저장 / 잔고 갱신)
        if self._exit_callback is not None:
            await self._exit_callback(watcher)

    # ── 3-6. on_buy_filled / on_sell_filled ──────────────

    def on_buy_filled(self, code: str, label: str, filled_price: int,
                       filled_qty: int, ts: datetime) -> None:
        """KIS 체결 통보 콜백. main.py 또는 Trader 가 호출.

        Args:
            code: 종목코드
            label: "buy1" 또는 "buy2"
            filled_price: 체결가
            filled_qty: 체결수량
            ts: 체결 시각
        """
        for w in self.watchers:
            if w.code == code:
                w.on_buy_filled(label, filled_price, filled_qty, ts)
                logger.info(
                    f"[Coordinator] {w.name} {label} 체결 반영"
                )
                return
        logger.warning(f"[Coordinator] on_buy_filled: 종목 미일치 {code}")

    def on_sell_filled(self, code: str, filled_price: int,
                        filled_qty: int, ts: datetime) -> None:
        """매도 체결 통보. avg_sell_price / total_sell_amount 갱신.

        Args:
            code: 종목코드
            filled_price: 체결가
            filled_qty: 체결수량
            ts: 체결 시각
        """
        for w in self.watchers:
            if w.code == code:
                w.total_sell_amount += filled_price * filled_qty
                # avg_sell_price 갱신 (가중 평균)
                if w.total_buy_qty > 0:
                    w.avg_sell_price = w.total_sell_amount // w.total_buy_qty
                logger.info(
                    f"[Coordinator] {w.name} 매도 체결 반영: "
                    f"{filled_price:,} × {filled_qty}"
                )
                return
        logger.warning(f"[Coordinator] on_sell_filled: 종목 미일치 {code}")

    # ── 3-7. on_buy_deadline / on_force_liquidate ────────

    async def on_buy_deadline(self, ts: datetime) -> None:
        """10:55 매수 마감. 비-ENTERED watcher 모두 SKIPPED + 미체결 취소."""
        cancelled_count = 0
        for w in self.watchers:
            if w.state in (
                WatcherState.WATCHING,
                WatcherState.TRIGGERED,
                WatcherState.READY,
                WatcherState.PASSED,
            ):
                w.state = WatcherState.SKIPPED
                cancelled_count += 1
                if self.trader is not None:
                    await self.trader.cancel_buy_orders(w)

        logger.info(
            f"[Coordinator] 10:55 매수 마감: {cancelled_count}개 watcher SKIPPED"
        )

    async def on_force_liquidate(self, ts: datetime) -> None:
        """11:20 강제 청산. ENTERED watcher 시장가 청산.

        Watcher.force_exit 호출 → state=EXITED + _exit_signal_pending=True
        다음 _process_signals 호출에서 _execute_exit 처리.
        """
        for w in self.watchers:
            if w.state == WatcherState.ENTERED:
                w.force_exit(ts)
                logger.warning(f"[Coordinator] 11:20 강제 청산: {w.name}")

        # 즉시 한 번 더 신호 처리
        await self._process_signals(ts)

    # ── 3-8. _is_after_buy_deadline ──────────────────────

    def _is_after_buy_deadline(self, ts: datetime) -> bool:
        """현재 시각이 매수 마감 시각 (10:55) 이후인가."""
        return ts.time() >= self._entry_deadline

    # ── 3-9. shutdown / reset ────────────────────────────

    def shutdown(self) -> None:
        """서비스 종료 시 호출. watchers 비우기."""
        self.watchers = []
        self._active_code = None
        self._screening_done = False
        logger.info("[Coordinator] shutdown")

    def reset_for_next_day(self) -> None:
        """다음 거래일 준비. 익일 09:50 전에 호출."""
        self.shutdown()
