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
from typing import Callable, Optional

from loguru import logger

from config.settings import StrategyParams
from src.utils.price_utils import floor_to_tick, ceil_to_tick
from src.models.stock import MarketType
from src.models.order import Position, Order   # Phase 2 B: dual-write


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


# ── ReservationSnapshot dataclass ────────────────────────


@dataclass
class ReservationSnapshot:
    """T2 시점 (첫 종목 buy2 체결) 에 두 번째 매매 후보로 예약된 종목의 스냅샷.

    T3 시점 (첫 종목 EXITED) 에 이 스냅샷을 기준으로 예약 재확인을 수행:
      - 종목이 여전히 watchers 목록에 존재
      - state 가 여전히 READY (is_yes = True)
      - target_buy1_price 가 변경되지 않음 (신고가 갱신 없음)
      - tiebreaker 조건 (3.8%/5.6% 이하 낙폭) 여전히 유지

    Fields:
        code: 종목 코드
        name: 종목명 (로그용)
        market: KOSPI / KOSDAQ (MarketType)
        reserved_at: T2 시점
        confirmed_high_at_t2: T2 시점의 confirmed_high (신고가 갱신 검증용)
        current_price_at_t2: T2 시점의 현재가
        pullback_pct_at_t2: T2 시점의 눌림폭 (get_pullback_pct 기준)
        target_buy1_price_at_t2: T2 시점의 1차 매수가 (재계산 검증용)
        target_buy2_price_at_t2: T2 시점의 2차 매수가
    """

    code: str
    name: str
    market: MarketType
    reserved_at: datetime
    confirmed_high_at_t2: int
    current_price_at_t2: int
    pullback_pct_at_t2: float
    target_buy1_price_at_t2: int
    target_buy2_price_at_t2: int


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
    is_double_digit: bool = False  # R-11: 프로그램순매수비중 ≥10% 여부
    program_ratio: float = 0.0     # R-13: 프로그램순매수비중 실수값 (0.0~100.0)

    # === 상태 ===
    state: WatcherState = WatcherState.WATCHING

    # === 시세 ===
    current_price: int = 0

    # === 09:00~09:49 사전 고가 추적 ===
    pre_950_high: int = 0

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
    futures_price_ts: Optional[datetime] = None  # STALENESS-01: 선물 WS 수신 ts (주가 ts 아님)

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
    buy1_qty: int = 0                              # R10-009
    buy1_time: Optional[datetime] = None          # R10-009
    buy2_price: int = 0
    buy2_qty: int = 0                              # R10-009
    buy2_time: Optional[datetime] = None          # R10-009
    total_buy_amount: int = 0
    total_buy_qty: int = 0

    # === Phase 2 B: dual-write Position (trader.position 와 병렬 유지) ===
    # 체결통보 시 trader.position 갱신과 동시에 여기도 갱신됨.
    # Phase 3 에서 trader.position 정리 시 이 필드가 단일 소스가 됨.
    position: Optional[Position] = None

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
    _t2_callback: Optional[Callable[[str, datetime], None]] = field(default=None, repr=False)

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
        # 호가 단위 보정 (W-12-rev2): 목표가 floor — 더 일찍 익절 (수익 확정)
        raw = int((self.confirmed_high + self.post_entry_low) / 2)
        return floor_to_tick(raw)

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

    # (5-2.5) _recalc_prices — R-13 가격 재계산

    def _recalc_prices(self) -> None:
        """is_double_digit 기준으로 매수가/손절가 재계산.

        R-13: 비중 변경 시 호출하여 가격 갱신.
        confirmed_high가 설정된 상태(TRIGGERED 이후)에서만 유효.
        """
        if self.confirmed_high <= 0:
            return  # TRIGGERED 이전이면 무시

        if self.market == MarketType.KOSPI:
            if self.is_double_digit:
                # R-11: KOSPI Double (프로그램순매수비중 ≥10%)
                buy1_pct = self.params.entry.kospi_double_buy1_pct
                buy2_pct = self.params.entry.kospi_double_buy2_pct
                stop_pct = self.params.exit.kospi_double_hard_stop_pct
            else:
                # R-11: KOSPI Single (프로그램순매수비중 <10%)
                buy1_pct = self.params.entry.kospi_single_buy1_pct
                buy2_pct = self.params.entry.kospi_single_buy2_pct
                stop_pct = self.params.exit.kospi_single_hard_stop_pct
        else:
            # R-11: KOSDAQ Double — KOSDAQ Single은 Screener에서 이미 제외됨
            buy1_pct = self.params.entry.kosdaq_double_buy1_pct
            buy2_pct = self.params.entry.kosdaq_double_buy2_pct
            stop_pct = self.params.exit.kosdaq_double_hard_stop_pct

        # 호가 단위 보정 (W-12-rev2)
        # 매수가: floor — 더 낮은 가격에 매수 (안전 마진)
        # 손절가: ceil  — 더 일찍 손절 (손실 최소화)
        self.target_buy1_price = floor_to_tick(int(self.confirmed_high * (1 - buy1_pct / 100)))
        self.target_buy2_price = floor_to_tick(int(self.confirmed_high * (1 - buy2_pct / 100)))
        self.hard_stop_price_value = ceil_to_tick(int(self.confirmed_high * (1 - stop_pct / 100)))

    # (5-3) on_tick

    def on_tick(
        self,
        price: int,
        ts: datetime,
        futures_price: float,
        futures_ts: Optional[datetime] = None,
    ) -> None:
        """실시간 체결가 수신 시 호출. state 별 분기.

        Args:
            price: 주가 체결가
            ts: 주가 수신 ts (stock_ts) — _handle_* 로 전달
            futures_price: 최근 선물가 (Coordinator._latest_futures_price)
            futures_ts: 선물 WS 수신 ts (Coordinator._latest_futures_ts) — staleness 감지용
        """
        if self.is_terminal:
            return

        self.current_price = price
        self.futures_price = futures_price
        self.futures_price_ts = futures_ts  # STALENESS-01: 선물 ts 저장 (주가 ts 아님!)

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

        09:50 이전: 사전 고가 추적 (pre_950_high)
        09:50 이후: 신고가 달성 평가 + 1% 하락 트리거 평가
        """
        # === A. 09:50 이전: 사전 고가 추적만 ===
        if ts.time() < self._watch_start:
            if price > self.pre_950_high:
                self.pre_950_high = price
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

        # === C. 09:50 이후: 신고가 달성 평가 ===
        if not self.new_high_achieved:
            if price > self.pre_950_high:
                self.update_intraday_high(price, ts)
                self.new_high_achieved = True
                logger.info(
                    f"[{self.name}] 09:50 이후 신고가 달성: {price:,}원"
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

        # R-13: 가격 계산 메서드로 위임
        self._recalc_prices()

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
        if self._check_futures_drop(ts):
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

    def _check_futures_drop(self, ts: datetime) -> bool:
        """종목 고점 시각의 선물가 대비 N% 하락.

        Args:
            ts: 주가 수신 ts (stock_ts) — 매 tick 평가 기준 시각.

        STALENESS-01: 선물 ts 기준 30초 이상 stale 시 skip (fail-open).
            비교식: age = stock_ts − self.futures_price_ts
            매매원칙: 매 tick 평가 → 평가 기준 시각 = stock_ts.
            경고 알림은 main._futures_staleness_monitor 가 담당.
        """
        if self.futures_at_confirmed_high <= 0 or self.futures_price <= 0:
            return False
        # staleness guard (STALENESS-01)
        if self.futures_price_ts is None:
            return False
        age_sec = (ts - self.futures_price_ts).total_seconds()
        if age_sec > 30:
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

    def on_buy_filled(self, label: str, filled_price: int, filled_qty: int,
                       ts: datetime, *, order: Optional[Order] = None) -> None:
        """매수 체결 통보. 첫 체결 시 ENTERED 전이 + post_entry_low 초기화.
        
        R-14 (W-39): 부분체결 지원 — 매 체결 통보마다 누적 처리.
        Phase 2 B: dual-write position. Order 객체는 keyword-only 인자로
        받음 (하위 호환). Trader 와 동일 패턴으로 "이번 체결분만
        직접 누적" — Position.add_buy() 미사용 (부분체결 중복 예방).
        """
        # === 기존 로직 (Watcher 내부 필드) ===
        self.total_buy_amount += filled_price * filled_qty
        self.total_buy_qty += filled_qty

        if label == "buy1":
            self.buy1_filled = True
            self.buy1_pending = False
            self.buy1_price = filled_price         # 마지막 체결가
            self.buy1_qty += filled_qty            # R-14 W-39: 누적
            self.buy1_time = ts
        elif label == "buy2":
            was_buy2_filled = self.buy2_filled
            self.buy2_filled = True
            self.buy2_pending = False
            self.buy2_price = filled_price         # 마지막 체결가
            self.buy2_qty += filled_qty            # R-14 W-39: 누적
            self.buy2_time = ts

            # T2 이벤트: buy2 최초 체결 시 Coordinator 에 알림 (중복 호출 방지)
            if not was_buy2_filled and self._t2_callback:
                try:
                    self._t2_callback(self.code, ts)
                except Exception as e:
                    logger.error(f"[Watcher {self.code}] _t2_callback 호출 실패: {e}")

        if self.state != WatcherState.ENTERED:
            self.state = WatcherState.ENTERED
            self.entered_at = ts
            self.post_entry_low = filled_price
            self.post_entry_low_time = ts

        # === Phase 2 B: dual-write position 갱신 ===
        # Trader.on_live_buy_filled 과 동일 패턴 — "이번 체결분만 직접 누적".
        # Position.add_buy() 는 order.filled_qty (누적값) 전체를 더하므로
        # 부분체결 시 중복 발생 → 사용 금지.
        if self.position is None:
            self.position = Position(code=self.code, name=self.name, opened_at=ts)
        self.position.total_buy_amount += filled_price * filled_qty
        self.position.total_qty += filled_qty
        if self.position.opened_at is None:
            self.position.opened_at = ts
        # buy_orders list: 같은 order 객체는 첫 체결 시에만 append.
        # Python reference 의존 — Trader 가 order.filled_qty / status 갱신하면
        # watcher.position.buy_orders 안의 같은 객체도 자동 반영.
        if order is not None and order not in self.position.buy_orders:
            self.position.buy_orders.append(order)

        avg = self.total_buy_amount / self.total_buy_qty if self.total_buy_qty > 0 else 0
        logger.info(
            f"[{self.name}] {label} 체결: {filled_price:,}원 × {filled_qty}주 (1차 {self.buy1_qty}주, 2차 {self.buy2_qty}주, 평단 {avg:,.0f}원)"
        )

    # (5-15) get_pullback_pct

    def get_pullback_pct(self) -> float:
        """현재가의 고가 대비 눌림폭 (0.0 ~ 1.0).

        TRIGGERED 이후: confirmed_high 기준.
        TRIGGERED 이전: intraday_high 기준.

        W-11d tiebreaker 에서 사용:
          KOSPI  kospi_next_entry_max_pct (3.8%) 이하 필터
          KOSDAQ kosdaq_next_entry_max_pct (5.6%) 이하 필터
          동일 조건 내 pullback 최대값 종목 선정.

        반환값 예시:
          0.0   → 현재가 = 고가 (눌림 없음)
          0.038 → 고가 대비 3.8% 하락 (KOSPI tiebreaker 경계)
          0.041 → 고가 대비 4.1% 하락 (KOSPI 하드 손절선)
        """
        high = self.confirmed_high if self.confirmed_high > 0 else self.intraday_high
        if high <= 0 or self.current_price <= 0:
            return 0.0
        return 1.0 - (self.current_price / high)


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
        self._latest_futures_ts: Optional[datetime] = None  # STALENESS-01: 선물 WS 수신 ts
        self._available_cash: int = 0  # main.py 에서 주입
        self._exit_callback = None  # 청산 후 콜백 (main.py 에서 주입)
        self._risk_manager = None  # R-14 버그픽스: 리스크 매니저 (main.py에서 주입)

        # 매수 마감 / 강제 청산 시각 (캐싱)
        self._entry_deadline = time.fromisoformat(params.entry.entry_deadline)
        self._force_liquidate_time = time.fromisoformat(params.exit.force_liquidate_time)

        # 일별 상태 (start_screening 시 리셋)
        self._screening_done: bool = False

        # === W-11d: 두 번째 매매 예약 ===
        self._reserved_snapshot: Optional[ReservationSnapshot] = None

        # === W-11d: 진입 윈도우 캐시 (multi_trade.repeat_start ~ entry_deadline) ===
        self._repeat_start = time.fromisoformat(params.multi_trade.repeat_start)
        self._repeat_end = time.fromisoformat(params.multi_trade.repeat_end)

    def set_available_cash(self, amount: int) -> None:
        """매수 가능 현금 갱신. main.py 가 호출."""
        self._available_cash = amount

    def set_exit_callback(self, callback) -> None:
        """청산 후 콜백 등록. main.py 가 호출.

        콜백 시그니처: async def callback(watcher: Watcher) -> None
        호출 시점: _execute_exit 가 trader.execute_exit 끝낸 직후
        """
        self._exit_callback = callback

    def set_risk_manager(self, risk_manager) -> None:
        """R-14 버그픽스: 리스크 매니저 주입. main.py 가 호출.
        
        _process_signals에서 trading_halted 상태를 체크하여
        지수 급락(-1.5%) 또는 손절 2회 후 신규 매수 차단.
        """
        self._risk_manager = risk_manager

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

    def start_screening(self, candidates: list, *, is_final: bool = False) -> None:
        """스크리닝 결과로 Watcher 생성.

        Args:
            candidates: list[StockCandidate] — screener.run_manual 결과
            is_final: True 이면 09:50 정규 스크리닝 (이후 추가 호출 차단).
                      False 이면 수동 스크리닝 (여러 번 덮어쓰기 허용).

        W-11d 변경:
        - is_final 파라미터 추가 (keyword-only)
        - active 상태에서는 재스크리닝 차단 (포지션 방치 방지)
        - Watcher 생성 시 _t2_callback 주입 (T2 이벤트 → _on_t2 핸들러)
        - _screening_done 설정 조건: is_final=True 일 때만
        - _reserved_snapshot 초기화 (재스크리닝 시 기존 예약 폐기)
        """
        # === A. 가드: 09:50 정규 스크리닝 이후 추가 호출 차단 ===
        if self._screening_done:
            logger.warning("스크리닝 이미 확정됨 (09:50 정규). 추가 호출 무시.")
            return

        # === B. 가드: active 상태에서 재스크리닝 차단 (포지션 방치 방지) ===
        if self._active_code is not None:
            logger.warning(
                f"active 상태에서 재스크리닝 차단 (active={self._active_code}). "
                f"기존 포지션 보호."
            )
            return

        # === C. Watcher 생성 (기존 watchers 폐기 후 새로 생성) ===
        self.watchers = []
        double_threshold = self.params.screening.program_net_buy_ratio_double
        for cand in candidates:
            # R-11: 프로그램순매수비중 기준 Double/Single 판정
            ratio = getattr(cand, 'program_net_buy_ratio', 0.0) or 0.0
            is_double = ratio >= double_threshold

            w = Watcher(
                code=cand.code,
                name=cand.name,
                market=cand.market,
                params=self.params,
                is_double_digit=is_double,  # R-11
                program_ratio=ratio,        # R-13
            )
            # R-09b: API 조회한 당일 고가 사용 (pre_950_high 정확도 향상)
            actual_high = getattr(cand, 'intraday_high', cand.current_price) or cand.current_price
            w.intraday_high = actual_high
            w.pre_950_high = actual_high  # 09:00~09:49 실제 고가 반영
            w.current_price = cand.current_price

            # W-11d: T2 이벤트 콜백 주입
            w._t2_callback = self._on_t2

            self.watchers.append(w)
            digit_label = "Double" if is_double else "Single"
            logger.info(
                f"[Coordinator] Watcher 가동: {cand.name}({cand.code}) {cand.market.value} "
                f"비중={ratio:.1f}% ({digit_label})"
            )

        # === D. 상태 초기화 ===
        self._active_code = None
        self._reserved_snapshot = None  # W-11d: 재스크리닝 시 기존 예약 폐기

        # === E. 09:50 정규 스크리닝일 경우 가드 활성화 ===
        if is_final:
            self._screening_done = True
            logger.info(
                f"[Coordinator] {len(self.watchers)}개 watcher 시작 "
                f"(is_final=True, 추가 스크리닝 차단)"
            )
        else:
            logger.info(
                f"[Coordinator] {len(self.watchers)}개 watcher 시작 "
                f"(is_final=False, 09:50 까지 재호출 가능)"
            )

    # ── 3-2. on_realtime_price / on_realtime_futures ──────

    async def on_realtime_price(self, code: str, price: int, ts: datetime) -> None:
        """KIS WebSocket 체결가 수신. 해당 종목 watcher 에 라우팅.

        모든 watcher 에 라우팅 (terminal 제외). _active_monitor 단독 폴링 X.
        ISSUE-036 의 결함 (single watcher 폴링) 을 해소.
        """
        for w in self.watchers:
            if w.code == code and not w.is_terminal:
                w.on_tick(price, ts, self._latest_futures_price, self._latest_futures_ts)

        # 매 틱 후 신호 폴링 + 매수 발주 평가
        await self._process_signals(ts)

    def on_realtime_futures(self, futures_price: float, ts: datetime) -> None:
        """선물 가격 갱신. 다음 on_tick 에서 사용.

        Args:
            futures_price: 선물 체결가
            ts: 선물 WS 수신 ts (main._on_futures_price 에서 now_kst()) — STALENESS-01

        staleness 감지는 Watcher._check_futures_drop + main._futures_staleness_monitor.
        """
        self._latest_futures_price = futures_price
        self._latest_futures_ts = ts

    # ── 3-3. _process_signals ────────────────────────────

    async def _process_signals(self, ts: datetime) -> None:
        """매 틱 후 호출. 청산 신호 처리 + 매수 발주 평가.

        흐름:
        0. R-14 버그픽스: DROPPED 상태 미체결 취소
        1. active watcher 의 _exit_signal_pending 처리 (청산 발주 + active 해제)
        2. active 가 없으면 yes_watchers 평가 (매수 발주)

        R16: DRY_RUN 시뮬레이션 블록 제거 (LIVE 전용).
        """
        # === R-14 버그픽스: DROPPED 상태 미체결 취소 ===
        # _evaluate_target에서 DROPPED로 전이되면 _exit_signal_pending이 false이므로
        # 청산 로직을 안 탐 → 미체결 주문이 그대로 잔존
        active = self.active
        if active is not None and active.state == WatcherState.DROPPED:
            logger.warning(
                f"[Coordinator] DROPPED 상태 감지: {active.name} 미체결 취소"
            )
            if self.trader is not None:
                await self.trader.cancel_buy_orders(active)
            self._active_code = None
            return

        # === 1. active 청산 신호 처리 ===
        active = self.active
        if active is not None and active._exit_signal_pending:
            await self._execute_exit(active, ts)
            self._active_code = None
            # T 시점: active 가 비었음. 다음 매수 평가는 다음 틱에서 자동 처리
            return

        # === 3. active 가 비어있으면 매수 발주 평가 ===
        if self._active_code is not None:
            return  # active 잠금 (체결 대기 또는 청산 대기)

        # R-14 버그픽스: 리스크 체크 (지수 급락 -1.5% 또는 손절 2회)
        if self._risk_manager is not None and self._risk_manager.trading_halted:
            return

        # W-11e: 진입 윈도우 체크 (10:00 ~ 10:55)
        # 첫 매매와 두 번째 매매 모두 동일 윈도우 적용 (수석님 확정).
        # _is_after_buy_deadline 대체 (결정 3=A, 결정 4=A).
        if not self._is_in_entry_window(ts):
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

        # ISSUE-LIVE-09: lock-first (await 전 잠금 → 다음 tick 재진입 차단)
        self._active_code = chosen.code
        try:
            await self._execute_buy(chosen, ts)
        except Exception as e:
            logger.error(
                f"[Coordinator] 매수 발주 실패, active 해제: {chosen.code} {e}"
            )
            self._active_code = None
            raise

    # ── 3-4. _execute_buy ────────────────────────────────

    async def _execute_buy(self, watcher: Watcher, ts: datetime) -> None:
        """매수 1차 + 2차 발주. Trader 호출."""
        # ISSUE-LIVE-09: 멱등성 가드 (lock-first 실패 시 defense in depth)
        if watcher.buy1_pending or watcher.buy1_placed:
            logger.warning(
                f"[Coordinator] 중복 _execute_buy 차단: {watcher.name} "
                f"buy1_pending={watcher.buy1_pending} buy1_placed={watcher.buy1_placed}"
            )
            return

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
        """청산 발주. Trader 호출.

        R15-005 변경: _exit_callback 호출을 여기서 제거함.
        trader.execute_exit 가 REST 발주만 하고 리턴함 (SUBMITTED 상태).
        실제 체결 확정은 체결통보(H0STCNI0) 시 비동기 처리.
        _exit_callback (main._on_exit_done) 은 매도 전량 체결 확정 시점에
        on_sell_complete 에서 발화됨 (position.is_open == False 일 때).
        """
        # R10-004 버그 수정: 청산 신호 즉시 리셋 (중복 청산 방지)
        if not watcher._exit_signal_pending:
            logger.warning(f"[Coordinator] 청산 중복 호출 방어: {watcher.name}")
            return
        watcher._exit_signal_pending = False

        logger.warning(
            f"[Coordinator] 청산 발주: {watcher.name} "
            f"reason={watcher.exit_reason} price={watcher.exit_price}"
        )

        if self.trader is not None:
            await self.trader.execute_exit(
                watcher, watcher.exit_reason, watcher.exit_price
            )

        # R15-005: _exit_callback 은 이제 여기서 호출되지 않음.
        # 매도 전량 체결 확정 시점에 on_sell_complete 에서 발화됨.
        # (main._on_execution_notify 가 position.is_open == False 인지 확인 후 호출)

    # ── 3-5b. on_sell_complete ── R15-005 ─────────────────

    async def on_sell_complete(self, watcher: Watcher, ts: datetime) -> None:
        """R15-005: 매도 전량 체결 확정 시 호출.

        호출 경로:
            main._on_execution_notify 에서 on_live_sell_filled 처리 후
            trader.position.is_open == False 로 전이되면 이 메서드 호출.

        이 시점에 보장:
            - trader.position 실제 체결가/수량 완전 반영
            - trader.pending_sell_orders 해당 주문 FILLED 상태
            - P&L 계산 정확 가능

        후속: _exit_callback (main._on_exit_done) 발화
            - P&L 기록 / TradeRecord 저장 / 잔고 재조회 / trader.reset / handle_t3
        """
        if self._exit_callback is not None:
            await self._exit_callback(watcher)

    # ── 3-6. on_buy_filled / on_sell_filled ──────────────

    def on_buy_filled(self, code: str, label: str, filled_price: int,
                       filled_qty: int, ts: datetime, *,
                       order: Optional[Order] = None) -> None:
        """KIS 체결 통보 콜백. main.py 또는 Trader 가 호출.

        Args:
            code: 종목코드
            label: "buy1" 또는 "buy2"
            filled_price: 체결가
            filled_qty: 체결수량
            ts: 체결 시각
            order: Phase 2 B — Order 객체 (dual-write 시 buy_orders list 관리용)
        """
        for w in self.watchers:
            if w.code == code:
                w.on_buy_filled(label, filled_price, filled_qty, ts, order=order)
                logger.info(
                    f"[Coordinator] {w.name} {label} 체결 반영"
                )
                return
        logger.warning(f"[Coordinator] on_buy_filled: 종목 미일치 {code}")

    def on_sell_filled(self, code: str, filled_price: int,
                        filled_qty: int, ts: datetime, *,
                        order: Optional[Order] = None) -> None:
        """매도 체결 통보. avg_sell_price / total_sell_amount 갱신.

        Args:
            code: 종목코드
            filled_price: 체결가
            filled_qty: 체결수량
            ts: 체결 시각
            order: Phase 2 B — Order 객체 (dual-write 시 sell_orders list 관리용)
        """
        for w in self.watchers:
            if w.code == code:
                # === 기존 로직 (Watcher 내부 필드) ===
                w.total_sell_amount += filled_price * filled_qty
                # avg_sell_price 갱신 (가중 평균)
                if w.total_buy_qty > 0:
                    w.avg_sell_price = w.total_sell_amount // w.total_buy_qty
                logger.info(
                    f"[Coordinator] {w.name} 매도 체결 반영: "
                    f"{filled_price:,} × {filled_qty}"
                )

                # === Phase 2 B: dual-write position 갱신 ===
                if w.position is None:
                    logger.error(
                        f"[Phase 2 B] 매도 체결 수신했으나 watcher.position 없음: code={code}"
                    )
                    return
                # total_qty 차감 (부분체결 지원 — 매번 차감)
                w.position.total_qty -= filled_qty
                if w.position.total_qty <= 0:
                    w.position.is_open = False
                    w.position.closed_at = ts
                # sell_orders list: 같은 order 객체는 첫 체결 시에만 append
                if order is not None and order not in w.position.sell_orders:
                    w.position.sell_orders.append(order)
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

        # ── ISSUE-LIVE-10: 10:55 일일 safety net ──
        # 중복발주 루프 등으로 tracked 밖에 고아 미체결이 남을 경우 최후 차단.
        # tracked-only cancel 은 실시간 타이밍상 유지 (수석님 결정) — 일 1회 inquire 로 커버.
        if self.trader is not None:
            try:
                _unfilled = await self.trader.api.inquire_unfilled_orders()
                _buy_unfilled = [
                    u for u in _unfilled
                    if u.get("sll_buy_dvsn_cd") == "02"
                    and int(u.get("psbl_qty", "0") or 0) > 0
                ]
                if _buy_unfilled:
                    logger.critical(
                        f"[Coordinator] 10:55 safety net: 고아 미체결 매수 "
                        f"{len(_buy_unfilled)}건 감지 → 전량 취소"
                    )
                    _recovered = 0
                    for u in _buy_unfilled:
                        try:
                            await self.trader.api.cancel_order(u["odno"], u["pdno"])
                            _recovered += 1
                            logger.warning(
                                f"[Coordinator] safety net 취소: "
                                f"odno={u['odno']} pdno={u['pdno']} "
                                f"psbl_qty={u.get('psbl_qty')}"
                            )
                        except Exception as e:
                            logger.error(
                                f"[Coordinator] safety net 취소 실패 "
                                f"odno={u.get('odno')}: {e}"
                            )
                    if _recovered > 0:
                        logger.critical(
                            f"[Coordinator] 10:55 safety net: {_recovered}건 고아 회수 완료"
                        )
            except Exception as e:
                logger.error(f"[Coordinator] 10:55 safety net 실패 (fail-open): {e}")

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

    # ── 3-9. W-11d: T2/T3 두 번째 매매 예약 로직 ────────

    def _is_in_entry_window(self, ts: datetime) -> bool:
        """현재 시각이 신규 진입 발주 가능 윈도우 안인지 확인.

        매매 명세 (R-08):
        - 시작: 10:00 (multi_trade.repeat_start)
        - 종료: 10:55 (entry.entry_deadline, 포함)
        - 첫 매매와 두 번째 매매에 동일 적용.

        Args:
            ts: 검증할 시각

        Returns:
            True: 윈도우 안 (진입 발주 가능)
            False: 윈도우 밖 (진입 차단)
        """
        current = ts.time()
        return self._repeat_start <= current <= self._entry_deadline

    def _on_t2(self, code: str, ts: datetime) -> None:
        """T2 이벤트 핸들러: 첫 종목의 buy2 가 체결된 시점.

        Watcher.on_buy_filled 의 buy2 분기에서 _t2_callback 으로 호출됨.
        동기 함수 (async 아님, Watcher 가 동기 콜백을 호출하기 때문).

        역할:
        - active 가 호출 종목과 일치하는지 확인 (잘못된 콜백 방어)
        - 진입 윈도우 (10:00~10:55) 안인지 확인
        - 두 번째 매매 후보 (YES 종목) 중 tiebreaker 통과 종목을 _reserved_snapshot 에 저장
        - 후보 0개면 예약 없이 종료 (T3 시점에 재판정)

        Args:
            code: T2 가 발생한 종목 코드
            ts: T2 시점
        """
        # 1. active 일치 확인 (방어)
        if self._active_code != code:
            logger.warning(
                f"[Coordinator] T2 콜백 방어: code={code} 가 active={self._active_code} 와 불일치. 무시."
            )
            return

        # 2. 진입 윈도우 체크
        if not self._is_in_entry_window(ts):
            logger.info(
                f"[Coordinator] T2 시점이 진입 윈도우 밖 ({ts.time()}), 예약 시도 안 함"
            )
            return

        # 3. 중복 호출 방어 (T2 가 두 번 호출되는 일 없어야 하지만)
        if self._reserved_snapshot is not None:
            logger.warning(
                f"[Coordinator] T2 중복 호출 감지: 기존 예약 {self._reserved_snapshot.code} 유지"
            )
            return

        # 4. 예약 시도
        snapshot = self._try_reserve_at_t2(ts)
        if snapshot is None:
            logger.info(
                f"[Coordinator] T2 ({code}) 시점 YES 후보 0개 또는 tiebreaker 미통과 → 예약 없음"
            )
            return

        self._reserved_snapshot = snapshot
        logger.info(
            f"[Coordinator] T2 예약 완료: {snapshot.name} ({snapshot.code}) "
            f"눌림 {snapshot.pullback_pct_at_t2:.2%}, "
            f"1차 매수가 {snapshot.target_buy1_price_at_t2:,}"
        )

    def _try_reserve_at_t2(self, ts: datetime) -> Optional[ReservationSnapshot]:
        """T2 시점에 두 번째 매매 후보 종목을 선정하고 ReservationSnapshot 으로 반환.

        흐름:
        1. active 가 아닌 watchers 중 is_yes (state == READY) 종목 수집
        2. tiebreaker 적용: KOSPI 3.8% 이상 / KOSDAQ 5.6% 이상 눌림폭 + 최대 선정
        3. 선정 종목의 스냅샷 생성

        Returns:
            ReservationSnapshot: 선정된 종목의 스냅샷
            None: YES 후보 0개 또는 tiebreaker 통과 0개
        """
        yes_watchers = [
            w for w in self.watchers
            if w.is_yes and not w.is_terminal and w.code != self._active_code
        ]
        if not yes_watchers:
            return None

        chosen = self._tiebreaker_for_next(yes_watchers)
        if chosen is None:
            return None

        snapshot = ReservationSnapshot(
            code=chosen.code,
            name=chosen.name,
            market=chosen.market,
            reserved_at=ts,
            confirmed_high_at_t2=chosen.confirmed_high,
            current_price_at_t2=chosen.current_price,
            pullback_pct_at_t2=chosen.get_pullback_pct(),
            target_buy1_price_at_t2=chosen.target_buy1_price,
            target_buy2_price_at_t2=chosen.target_buy2_price,
        )
        return snapshot

    def _tiebreaker_for_next(self, candidates: list) -> Optional[Watcher]:
        """두 번째 매매 후보 중 tiebreaker 통과 + 눌림폭 최대 종목 선정.

        매매 명세 (R-08, 2026-04-09 수석님 확정):
        - KOSPI: 눌림폭 (1 - current/high) >= 3.8%
        - KOSDAQ: 눌림폭 >= 5.6%
        - 필터 통과 종목 중 눌림폭이 가장 큰 종목 선정

        매매 의도: 더 깊은 진입 = R:R 비대칭 확보 (손절선 근처 진입)

        Args:
            candidates: YES 종목 리스트 (is_yes + not is_terminal 필터 완료)

        Returns:
            tiebreaker 통과 + 눌림폭 최대 Watcher
            None: 필터 통과 종목 0개
        """
        kospi_threshold = self.params.multi_trade.kospi_next_entry_max_pct / 100.0
        kosdaq_threshold = self.params.multi_trade.kosdaq_next_entry_max_pct / 100.0

        passed = []
        for w in candidates:
            pullback = w.get_pullback_pct()

            if w.market == MarketType.KOSPI:
                threshold = kospi_threshold
            elif w.market == MarketType.KOSDAQ:
                threshold = kosdaq_threshold
            else:
                logger.warning(
                    f"[Coordinator] tiebreaker: 알 수 없는 시장 {w.market} ({w.code}), 제외"
                )
                continue

            if pullback >= threshold:
                passed.append((pullback, w))

        if not passed:
            return None

        # 눌림폭 최대 선정
        passed.sort(key=lambda x: x[0], reverse=True)
        return passed[0][1]

    def _verify_reservation_at_t3(self, ts: datetime) -> bool:
        """T3 시점에 _reserved_snapshot 의 예약 종목이 여전히 진입 가능한지 엄격 재확인.

        재확인 항목 (수석님 결정):
        1. watchers 에 존재
        2. is_yes (state == READY)
        3. not is_terminal
        4. target_buy1_price 가 T2 시점과 동일 (재계산되지 않음)
        5. tiebreaker 조건 여전히 충족

        Args:
            ts: T3 시점 (로그용)

        Returns:
            True: 모든 재확인 통과
            False: 하나라도 실패 → 호출자가 예약 폐기 + 재판정
        """
        snapshot = self._reserved_snapshot
        if snapshot is None:
            logger.warning("[Coordinator] _verify_reservation_at_t3: 예약 없음")
            return False

        # 1. watchers 에서 종목 찾기
        reserved_watcher = next(
            (w for w in self.watchers if w.code == snapshot.code),
            None,
        )
        if reserved_watcher is None:
            logger.warning(
                f"[Coordinator] T3 재확인 실패: 예약 종목 {snapshot.code} 가 watchers 에 없음"
            )
            return False

        # 2. is_yes 재확인
        if not reserved_watcher.is_yes:
            logger.info(
                f"[Coordinator] T3 재확인 실패: {snapshot.name} state={reserved_watcher.state} (READY 아님)"
            )
            return False

        # 3. is_terminal 재확인
        if reserved_watcher.is_terminal:
            logger.info(
                f"[Coordinator] T3 재확인 실패: {snapshot.name} terminal 상태"
            )
            return False

        # 4. target_buy1_price 변경 검증
        if reserved_watcher.target_buy1_price != snapshot.target_buy1_price_at_t2:
            logger.info(
                f"[Coordinator] T3 재확인 실패: {snapshot.name} 매수가 변경됨 "
                f"(T2: {snapshot.target_buy1_price_at_t2:,}, T3: {reserved_watcher.target_buy1_price:,})"
            )
            return False

        # 5. tiebreaker 조건 재확인
        kospi_threshold = self.params.multi_trade.kospi_next_entry_max_pct / 100.0
        kosdaq_threshold = self.params.multi_trade.kosdaq_next_entry_max_pct / 100.0

        if reserved_watcher.market == MarketType.KOSPI:
            threshold = kospi_threshold
        elif reserved_watcher.market == MarketType.KOSDAQ:
            threshold = kosdaq_threshold
        else:
            logger.warning(
                f"[Coordinator] T3 재확인: 알 수 없는 시장 {reserved_watcher.market}"
            )
            return False

        current_pullback = reserved_watcher.get_pullback_pct()
        if current_pullback < threshold:
            logger.info(
                f"[Coordinator] T3 재확인 실패: {snapshot.name} 눌림폭 부족 "
                f"({current_pullback:.2%} < {threshold:.2%})"
            )
            return False

        logger.info(
            f"[Coordinator] T3 재확인 통과: {snapshot.name} "
            f"눌림 {current_pullback:.2%}, 1차 매수가 {reserved_watcher.target_buy1_price:,}"
        )
        return True

    async def handle_t3(self, ts: datetime) -> None:
        """T3 (첫 종목 EXITED) 시점 호출. main.py._on_exit_done 에서 위임받음.

        흐름:
        1. 진입 윈도우 (10:00~10:55) 체크 — 벗어나면 예약 폐기 후 return
        2. 예약 존재 시 → _verify_reservation_at_t3 → 통과 시 진입, 실패 시 fallthrough
        3. 재판정 (예약 없거나 fallthrough): YES 종목 → tiebreaker → 진입 또는 포기

        Args:
            ts: T3 시점 (호출 시각)
        """
        # === 1. 진입 윈도우 체크 ===
        if not self._is_in_entry_window(ts):
            if self._reserved_snapshot is not None:
                logger.info(
                    f"[Coordinator] T3: 진입 윈도우 밖 ({ts.time()}), "
                    f"예약 {self._reserved_snapshot.code} 폐기"
                )
                self._reserved_snapshot = None
            else:
                logger.info(
                    f"[Coordinator] T3: 진입 윈도우 밖 ({ts.time()}), 다음 매매 없음"
                )
            return

        # === 2. 예약 존재 시 재확인 ===
        if self._reserved_snapshot is not None:
            if self._verify_reservation_at_t3(ts):
                # 재확인 통과 → 예약된 종목으로 진입
                snapshot = self._reserved_snapshot
                reserved_watcher = next(
                    (w for w in self.watchers if w.code == snapshot.code),
                    None,
                )
                if reserved_watcher is not None:
                    logger.info(
                        f"[Coordinator] T3: 예약 진입 → {reserved_watcher.name}"
                    )
                    self._reserved_snapshot = None
                    # ISSUE-LIVE-09: lock-first (await 전 잠금)
                    self._active_code = reserved_watcher.code
                    try:
                        await self._execute_buy(reserved_watcher, ts)
                    except Exception as e:
                        logger.error(
                            f"[Coordinator] T3 예약 진입 실패, active 해제: "
                            f"{reserved_watcher.code} {e}"
                        )
                        self._active_code = None
                        raise
                    return
                else:
                    # 이론상 도달 불가능 (_verify 가 잡았어야 함). 방어.
                    logger.error(
                        f"[Coordinator] T3 예약 진입 방어: {snapshot.code} watchers 에 없음 (이론상 불가능)"
                    )
                    self._reserved_snapshot = None
                    # fallthrough 재판정
            else:
                # 재확인 실패 → 예약 폐기 후 재판정
                logger.info("[Coordinator] T3: 예약 재확인 실패, 예약 폐기 후 재판정")
                self._reserved_snapshot = None
                # fallthrough 재판정

        # === 3. 재판정 (예약 없거나 재확인 실패) ===
        yes_watchers = [
            w for w in self.watchers
            if w.is_yes and not w.is_terminal
        ]
        if not yes_watchers:
            logger.info("[Coordinator] T3: 재판정 시 YES 종목 0개, 다음 매매 포기")
            return

        chosen = self._tiebreaker_for_next(yes_watchers)
        if chosen is None:
            logger.info("[Coordinator] T3: tiebreaker 통과 종목 0개, 다음 매매 포기")
            return

        logger.info(f"[Coordinator] T3: 재판정 진입 → {chosen.name}")
        # ISSUE-LIVE-09: lock-first (await 전 잠금)
        self._active_code = chosen.code
        try:
            await self._execute_buy(chosen, ts)
        except Exception as e:
            logger.error(
                f"[Coordinator] T3 재판정 진입 실패, active 해제: {chosen.code} {e}"
            )
            self._active_code = None
            raise

    # ── 3-10. shutdown / reset ────────────────────────────

    def shutdown(self) -> None:
        """서비스 종료 시 호출. watchers 비우기."""
        self.watchers = []
        self._active_code = None
        self._screening_done = False
        self._reserved_snapshot = None  # W-11d
        logger.info("[Coordinator] shutdown")

    def reset_for_next_day(self) -> None:
        """다음 거래일 준비. 익일 09:50 전에 호출."""
        self.shutdown()
