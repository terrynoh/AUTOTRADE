# W-05b — WatcherCoordinator + Watcher 필드 정정

## 미션

`src/core/watcher.py` 에 `WatcherCoordinator` 클래스를 추가하고, W-05a 에서 누락된 `Watcher` 필드 (`buy1_placed`, `buy2_placed`) 를 정정한다.

- WatcherCoordinator 신규 작성 (3 watcher 관리, 시세 라우팅, T 시점 평가, 시각 기반 이벤트)
- Watcher 필드 누락 정정 (buy1_placed, buy2_placed 추가)
- 단위 검증 + 통합 시나리오 1~2개 검증

자동 진행 모드. 수석님 confirm 없이 끝까지 진행. 멈출 조건은 [멈춤 조건] 참조.

크기: 예상 ~250~350 행 추가 (현재 463 → ~750)

## 화이트리스트

이 파일만 수정 가능:
- `src/core/watcher.py`

## 블랙리스트

절대 수정 금지:
- `src/main.py`
- `src/core/trader.py` (W-05c 영역)
- `src/core/monitor.py` (W-07 영역)
- `src/core/screener.py` (W-03 결과물 보존)
- `src/core/stock_master.py` (W-02 결과물 보존)
- `src/utils/notifier.py` (W-04 결과물 보존)
- `src/models/stock.py`
- `src/models/order.py`
- `src/storage/database.py`
- `src/dashboard/*` (전부)
- `src/kis_api/*` (전부)
- `config/*` (전부)
- 그 외 모든 파일

## 배경 — 사실 base

### W-05a 결과 (확정됨)
- `src/core/watcher.py` 463행
- `WatcherState` enum 8개 (WATCHING / TRIGGERED / READY / PASSED / DROPPED / ENTERED / EXITED / SKIPPED)
- `Watcher` dataclass + 메서드 14개
- 5조건 청산 (M-7 비교 연산자) 보존
- import 경로: `from config.settings import StrategyParams`

### M-9 결과 (확정됨)
- `src/core/trader.py` 의 Trader 클래스가 *TradeTarget 객체를 직접 변경*
- Trader 의 메서드: `place_buy_orders(target)`, `cancel_buy_orders(target)`, `execute_exit(target, reason, price=0)`, `simulate_fills(target, current_price, ts)`, `on_buy_filled(label, filled_price, filled_qty, ts)`
- 비동기/동기 분리: `place_buy_orders` / `cancel_buy_orders` / `execute_exit` 는 async, `simulate_fills` / `on_buy_filled` 는 sync
- Trader 시그니처 정정은 W-05c 에서 처리. **이번 W-05b 에서는 Trader 손대지 않음.**

### W-05b 의 위치
- `WatcherCoordinator` 가 `Trader` 인스턴스를 받음 (DI)
- 다만 *trader 메서드 호출은 안 함* — W-05b 시점엔 Trader 시그니처가 아직 TradeTarget 받음
- WatcherCoordinator 의 발주 호출 부분은 *placeholder* 로 작성 (W-05c 후 실연결)
- 즉 W-05b 는 Coordinator 의 비즈니스 로직 (state 라우팅, T 시점, yes_watchers 평가) 만 작성 + Trader 호출은 TODO 표시

### Watcher 필드 누락 (W-05a 검증에서 발견)
- `buy1_placed: bool = False` 누락
- `buy2_placed: bool = False` 누락
- M-9 가 식별: Trader 가 `target.buy1_placed = True` / `target.buy2_placed = True` 패턴으로 set
- 새 Watcher 도 같은 필드 보유 필요 (W-05c 의 Trader 정정 호환성 + 향후 dashboard 연동)

## 흐름

```
작업 1 (Watcher 필드 정정)
  → 작업 2 (WatcherCoordinator 클래스 신규)
  → 작업 3 (Coordinator 메서드 본문)
  → 검증 1~5
  → 보고
```

중간에 멈추지 말 것. 각 작업 사이 검증 X.

---

## [작업 1] Watcher 필드 누락 정정

### 1-1. 현재 Watcher dataclass 의 필드 영역 식별

`src/core/watcher.py` 의 `Watcher` 클래스 안에서 다음 패턴을 찾는다:

```python
buy1_filled: bool = False
buy2_filled: bool = False
```

이 두 줄 *직전* 또는 *직후* 에 추가:

```python
buy1_placed: bool = False                     # 1차 매수 주문 발주 완료 (체결 무관)
buy2_placed: bool = False                     # 2차 매수 주문 발주 완료
```

### 1-2. 추가 위치 정확성

- `buy1_pending` / `buy2_pending` 가 이미 있을 수 있음 (W-05a 에서 추가)
- `buy1_pending` 과 `buy1_placed` 는 의미가 다름:
  - `buy1_pending`: 발주 후 체결 전 (Watcher 내부 추적)
  - `buy1_placed`: 발주 자체 완료 (Trader 가 set, 외부 인터페이스)
- 두 필드 모두 보존
- 정렬 순서: `buy1_pending` → `buy2_pending` → `buy1_placed` → `bue2_placed` → `buy1_filled` → `buy2_filled`
- 위 순서가 어색하면 그냥 *기존 필드 묶음 안* 어디든 OK. dataclass field 순서는 기능적 의미 없음.

### 1-3. 검증 (이 작업 후)

- import OK (검증 1 에서 확인)
- 인스턴스 생성 시 buy1_placed=False / buy2_placed=False 기본값 (검증 3 에서 확인)

---

## [작업 2] WatcherCoordinator 클래스 신규

### 2-1. Coordinator 클래스 정의 (Watcher 클래스 *뒤* 에 추가)

`src/core/watcher.py` 의 `Watcher` 클래스 정의가 끝난 *다음 줄* 에 추가:

```python
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
    - W-05b 시점에는 Trader 호출이 TODO 표시 (Trader 시그니처가 TradeTarget 호환이라)
    - W-05c 후 Trader 가 Watcher 호환 시그니처로 정정되면 실연결
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
        
        # 매수 마감 / 강제 청산 시각 (캐싱)
        self._entry_deadline = time.fromisoformat(params.entry.entry_deadline)
        self._force_liquidate_time = time.fromisoformat(params.exit.force_liquidate_time)
        
        # 일별 상태 (start_screening 시 리셋)
        self._screening_done: bool = False
```

### 2-2. Property 정의

Coordinator 클래스 안에 추가:

```python
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
```

---

## [작업 3] Coordinator 메서드 본문

### 3-1. start_screening — 09:50 스크리닝 후 watcher 가동

```python
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
```

### 3-2. on_realtime_price — 시세 라우팅

```python
    def on_realtime_price(self, code: str, price: int, ts: datetime) -> None:
        """KIS WebSocket 체결가 수신. 해당 종목 watcher 에 라우팅.
        
        모든 watcher 에 라우팅 (terminal 제외). _active_monitor 단독 폴링 X.
        ISSUE-036 의 결함 (single watcher 폴링) 을 해소.
        """
        for w in self.watchers:
            if w.code == code and not w.is_terminal:
                w.on_tick(price, ts, self._latest_futures_price)
        
        # 매 틱 후 신호 폴링 + 매수 발주 평가
        self._process_signals(ts)
    
    def on_realtime_futures(self, futures_price: float) -> None:
        """선물 가격 갱신. 다음 on_tick 에서 사용."""
        self._latest_futures_price = futures_price
```

### 3-3. _process_signals — 청산 신호 처리 + 매수 발주 평가

```python
    def _process_signals(self, ts: datetime) -> None:
        """매 틱 후 호출. 청산 신호 처리 + 매수 발주 평가.
        
        흐름:
        1. active watcher 의 _exit_signal_pending 처리 (청산 발주 + active 해제)
        2. active 가 없으면 yes_watchers 평가 (매수 발주)
        """
        # === 1. active 청산 신호 처리 ===
        active = self.active
        if active is not None and active._exit_signal_pending:
            self._execute_exit(active, ts)
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
        
        self._execute_buy(chosen, ts)
        self._active_code = chosen.code  # 즉시 active 잠금 (체결 대기)
```

### 3-4. _execute_buy — 매수 발주 (TODO: Trader 호출)

```python
    def _execute_buy(self, watcher: Watcher, ts: datetime) -> None:
        """매수 1차 + 2차 발주. W-05c 후 Trader 호출.
        
        W-05b 시점에서는 placeholder. 로그만 남기고 watcher 의 buy1_pending /
        buy2_pending 만 set.
        """
        watcher.buy1_pending = True
        watcher.buy2_pending = True
        
        logger.info(
            f"[Coordinator] 매수 발주 (placeholder): {watcher.name} "
            f"1차 {watcher.target_buy1_price:,} / 2차 {watcher.target_buy2_price:,}"
        )
        
        # TODO (W-05c): self.trader.place_buy_orders(watcher, available_cash)
        # 위 호출은 Trader 시그니처가 Watcher 받도록 정정된 후 활성화
```

### 3-5. _execute_exit — 청산 발주 (TODO: Trader 호출)

```python
    def _execute_exit(self, watcher: Watcher, ts: datetime) -> None:
        """청산 발주. W-05c 후 Trader 호출.
        
        W-05b 시점에서는 placeholder. 로그만 남김.
        Watcher.state 는 이미 _emit_exit 에서 EXITED 로 전이됨.
        """
        logger.warning(
            f"[Coordinator] 청산 발주 (placeholder): {watcher.name} "
            f"reason={watcher.exit_reason} price={watcher.exit_price}"
        )
        
        # TODO (W-05c): self.trader.execute_exit(watcher, watcher.exit_reason, watcher.exit_price)
```

### 3-6. on_buy_filled / on_sell_filled — 외부 콜백

```python
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
```

### 3-7. on_buy_deadline / on_force_liquidate — 시각 기반 이벤트

```python
    def on_buy_deadline(self, ts: datetime) -> None:
        """10:55 매수 마감. 비-ENTERED watcher 모두 SKIPPED.
        
        ENTERED 는 영향 없음. 11:20 까지 청산 평가 계속.
        """
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
                # TODO (W-05c): self.trader.cancel_buy_orders(w)
        
        logger.info(
            f"[Coordinator] 10:55 매수 마감: {cancelled_count}개 watcher SKIPPED"
        )
    
    def on_force_liquidate(self, ts: datetime) -> None:
        """11:20 강제 청산. ENTERED watcher 시장가 청산.
        
        Watcher.force_exit 호출 → state=EXITED + _exit_signal_pending=True
        다음 _process_signals 호출에서 _execute_exit 처리.
        """
        for w in self.watchers:
            if w.state == WatcherState.ENTERED:
                w.force_exit(ts)
                logger.warning(f"[Coordinator] 11:20 강제 청산: {w.name}")
        
        # 즉시 한 번 더 신호 처리
        self._process_signals(ts)
```

### 3-8. _is_after_buy_deadline — 매수 마감 시각 체크

```python
    def _is_after_buy_deadline(self, ts: datetime) -> bool:
        """현재 시각이 매수 마감 시각 (10:55) 이후인가."""
        return ts.time() >= self._entry_deadline
```

### 3-9. shutdown / reset — 정리

```python
    def shutdown(self) -> None:
        """서비스 종료 시 호출. watchers 비우기."""
        self.watchers = []
        self._active_code = None
        self._screening_done = False
        logger.info("[Coordinator] shutdown")
    
    def reset_for_next_day(self) -> None:
        """다음 거래일 준비. 익일 09:50 전에 호출."""
        self.shutdown()
```

---

## [검증]

### 검증 1 — import OK

```bash
python -c "from src.core.watcher import Watcher, WatcherState, WatcherCoordinator; print('import OK')"
```

기대: `import OK`

### 검증 2 — Watcher 필드 정정 확인

```bash
python -c "
from src.core.watcher import Watcher
from config.settings import StrategyParams
from src.models.stock import MarketType
params = StrategyParams.load()
w = Watcher(code='005930', name='삼성전자', market=MarketType.KOSPI, params=params)
print('buy1_placed:', w.buy1_placed)
print('buy2_placed:', w.buy2_placed)
print('buy1_pending:', w.buy1_pending)
print('buy2_pending:', w.buy2_pending)
print('buy1_filled:', w.buy1_filled)
print('buy2_filled:', w.buy2_filled)
"
```

기대: 6개 필드 모두 `False`

### 검증 3 — Coordinator 인스턴스 생성

```bash
python -c "
from src.core.watcher import WatcherCoordinator
from config.settings import StrategyParams
params = StrategyParams.load()
c = WatcherCoordinator(params=params, trader=None)
print('watchers:', c.watchers)
print('active_code:', c._active_code)
print('has_active:', c.has_active)
print('active:', c.active)
print('screening_done:', c._screening_done)
"
```

기대:
```
watchers: []
active_code: None
has_active: False
active: None
screening_done: False
```

### 검증 4 — start_screening + on_realtime_price 통합 시나리오

```bash
python -c "
from src.core.watcher import WatcherCoordinator, Watcher, WatcherState
from config.settings import StrategyParams
from src.models.stock import StockCandidate, MarketType
from datetime import datetime

params = StrategyParams.load()
c = WatcherCoordinator(params=params, trader=None)

# 가짜 candidates 3개
candidates = [
    StockCandidate(code='005930', name='삼성전자', market=MarketType.KOSPI,
                   trading_volume_krw=100000000000, program_net_buy=10000000000,
                   price_change_pct=2.5, current_price=70000),
    StockCandidate(code='000660', name='SK하이닉스', market=MarketType.KOSPI,
                   trading_volume_krw=80000000000, program_net_buy=5000000000,
                   price_change_pct=3.0, current_price=120000),
    StockCandidate(code='035720', name='카카오', market=MarketType.KOSPI,
                   trading_volume_krw=60000000000, program_net_buy=4000000000,
                   price_change_pct=1.8, current_price=50000),
]

c.start_screening(candidates)
print('after screening watchers:', len(c.watchers))
print('watcher 0:', c.watchers[0].code, c.watchers[0].state.value)
print('watcher 1:', c.watchers[1].code, c.watchers[1].state.value)
print('watcher 2:', c.watchers[2].code, c.watchers[2].state.value)

# 시세 라우팅 시뮬레이션 (10:00 시점)
ts = datetime(2026, 4, 8, 10, 0, 0)
c.on_realtime_price('005930', 71000, ts)
print('after price tick — watcher 0 high:', c.watchers[0].intraday_high)
print('active_code:', c._active_code)
"
```

기대:
```
after screening watchers: 3
watcher 0: 005930 watching
watcher 1: 000660 watching
watcher 2: 035720 watching
after price tick — watcher 0 high: 71000
active_code: None
```

### 검증 5 — on_buy_deadline 시나리오

```bash
python -c "
from src.core.watcher import WatcherCoordinator, Watcher, WatcherState
from config.settings import StrategyParams
from src.models.stock import StockCandidate, MarketType
from datetime import datetime

params = StrategyParams.load()
c = WatcherCoordinator(params=params, trader=None)

candidates = [
    StockCandidate(code='005930', name='삼성전자', market=MarketType.KOSPI,
                   trading_volume_krw=100000000000, program_net_buy=10000000000,
                   price_change_pct=2.5, current_price=70000),
]
c.start_screening(candidates)

# 10:55 매수 마감 발화
ts = datetime(2026, 4, 8, 10, 55, 0)
c.on_buy_deadline(ts)
print('after deadline — watcher 0 state:', c.watchers[0].state.value)
"
```

기대: `after deadline — watcher 0 state: skipped`

---

## [검증 실패 시 — 멈춤 조건]

- 검증 1 실패 (import 에러) → 멈춤 + 보고
- 검증 2 실패 (필드 누락) → 멈춤 + 보고
- 검증 3 실패 (Coordinator 인스턴스 생성 에러) → 멈춤 + 보고
- 검증 4 실패 (시세 라우팅 에러) → 멈춤 + 보고
- 검증 5 실패 (10:55 SKIPPED 안 됨) → 멈춤 + 보고

멈춤 시 변경 *되돌리지 말 것*. 그대로 두고 보고.

---

## [모호한 케이스 — 사전 결정]

### 케이스 C-1: WatcherCoordinator 정의 위치
Watcher 클래스 정의 *직후* 에 추가. 같은 파일.

### 케이스 C-2: Optional / List import
이미 W-05a 에서 import 되어 있음. 추가 import 불필요.
만약 안 되어 있으면: `from typing import Optional` 추가.

### 케이스 C-3: trader 인자 타입 힌트
`trader=None` 기본값. 타입 힌트 X (W-05c 후 정정 가능).
또는 `trader: Optional["Trader"] = None` (forward reference, 문자열).
권장: 타입 힌트 X (단순)

### 케이스 C-4: candidates 인자 타입 힌트
`candidates: list` 그대로. List[StockCandidate] 같은 정밀 타입 힌트는 X.
이유: import 추가 회피

### 케이스 C-5: TODO 주석 처리
`# TODO (W-05c): ...` 패턴 그대로 유지. 이건 *정상 작업 흔적*. 제거 X.

### 케이스 C-6: import 추가
새로 필요한 import 가 있는가?
- `time` (timedelta 와 함께 datetime 모듈) — 이미 import 됨
- `Optional` — typing 에 이미 있을 가능성. 없으면 추가
- 다른 import 추가 금지

### 케이스 C-7: WatcherCoordinator 가 dataclass 인가
**아니다.** 일반 클래스. `@dataclass` 데코레이터 사용 X.
이유: 메서드가 많고 *상태 변경 로직* 이 핵심. dataclass 부적절.

### 케이스 C-8: Coordinator 의 비동기 인터페이스
W-05b 에서는 *모두 sync*. async 메서드 0개.
이유: Trader 호출이 placeholder 라 async 필요 없음. W-05c / W-06 에서 main.py 의 async layer 가 sync 호출.

### 케이스 C-9: logger 사용
`from loguru import logger` 가 W-05a 에서 이미 import 됨. 그대로 사용.

### 케이스 C-10: TYPE_CHECKING import
`if TYPE_CHECKING: from src.core.trader import Trader` 같은 패턴 사용 X.
이유: type hint 자체를 안 쓰므로 불필요.

---

## [자체 발견 처리 규칙]

Code 가 작업 중 다음을 발견하면 → 수정하지 말 것 → 보고의 [발견] 섹션에 1~3줄 기록:
- W-05a 의 Watcher 코드의 잠재적 버그
- M-7 / M-7-1 / M-8 / M-9 결과와 다른 코드 발견
- 새 자료구조의 일관성 문제
- main.py / trader.py 와의 결합 문제 (현재는 placeholder 라 정상)
- 명령서가 다루지 않은 영역의 사고
- 더 깔끔한 패턴

---

## [보고]

### A. 변경 라인
- 작업 1 (Watcher 필드 정정): 추가 라인 번호
- 작업 2 (Coordinator 클래스 정의): 라인 범위
- 작업 3 (Coordinator 메서드들): 각 메서드별 라인 범위

### B. watcher.py 라인 수
- W-05a 후: 463 행
- W-05b 후: ? 행
- 차이: +? 행

### C. 검증 결과
- 검증 1: 성공/실패 + 출력
- 검증 2: 성공/실패 + 출력 (6개 필드 값)
- 검증 3: 성공/실패 + 출력 (Coordinator 초기 상태)
- 검증 4: 성공/실패 + 출력 (시세 라우팅 시나리오)
- 검증 5: 성공/실패 + 출력 (10:55 SKIPPED)

### D. 다른 파일 수정 여부
*반드시 "수정 없음"*

### E. TODO 위치 (W-05c 작업 입력)
- `_execute_buy` 의 TODO 라인 번호
- `_execute_exit` 의 TODO 라인 번호
- `on_buy_deadline` 의 TODO 라인 번호 (cancel_buy_orders)

이 3곳이 W-05c 에서 *Trader 호출로 대체* 됩니다.

### F. 모호한 케이스 처리 결과 (C-1 ~ C-10 중 발생한 것)

### G. [발견] 섹션 — 자체 발견 사항 (있으면)

### H. W-05c 진입 준비 — 다음 작업이 의존할 항목
- WatcherCoordinator 가 *완성된 상태* (Trader 호출 placeholder)
- W-05c 에서 Trader 시그니처 정정 후 placeholder 를 실호출로 교체

---

## [추가 금지 — 자동 모드 강화]

- src/core/watcher.py 외 어떤 파일도 수정 금지
- Trader 클래스 import 금지 (W-05c 영역)
- TradeTarget 클래스 import 금지 (W-07 폐기 예정)
- 새 클래스 추가 금지 (WatcherCoordinator 외)
- 새 메서드 추가 금지 (위 명시된 것만)
- async/await 추가 금지 (Coordinator 는 sync)
- try-except 추가 금지
- 타입 힌트 추가 금지 (위 명시된 것만)
- 새 dataclass 정의 금지
- 새 import 금지 (단, Optional 누락 시 추가 OK)
- 로깅 메시지 변경 금지 (위 명시된 것 그대로)
- 영어 메시지 추가 금지 (한국어 그대로)
- TODO 주석 제거 금지
- placeholder 를 실호출로 변경 금지 (W-05c 영역)
- monitor.py 수정 금지 (W-07 영역)
- W-05c / W-06 / W-07 영역 손대지 말 것
- commit 금지
- 임시 파일 / scratch 파일 생성 금지
