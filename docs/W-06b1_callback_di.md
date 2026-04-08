# W-06b1 — Coordinator 콜백 + 의존성 주입 + StockMaster 생성

## 미션

W-06b 의 1차 작업. *Coordinator 콜백 패턴 도입* + *Screener/Notifier 의존성 주입* + *_on_exit_done 콜백 함수 신규* 작성.

작업 영역:
- `src/core/watcher.py`: Coordinator 에 `_exit_callback` 필드 + `set_exit_callback` 메서드 + `_execute_exit` 끝에 콜백 호출 추가
- `src/main.py`: StockMaster 인스턴스 생성 + Screener/Notifier 호출자 정정 + `_on_exit_done` 콜백 함수 신규 + `run()` 안에서 콜백 등록 + 초기 set_available_cash 호출

자동 진행 모드. 수석님 confirm 없이 끝까지 진행. 멈출 조건은 [멈춤 조건] 참조.

크기 예상: watcher.py 약 +15 행, main.py 약 +60 행

## 화이트리스트

이 두 파일만 수정 가능:
- `src/core/watcher.py`
- `src/main.py`

## 블랙리스트

절대 수정 금지:
- `src/core/trader.py` (W-05c 결과)
- `src/core/monitor.py` (W-07 영역)
- `src/core/screener.py` (W-03 결과)
- `src/core/stock_master.py` (W-02 결과)
- `src/utils/notifier.py` (W-04 결과)
- `src/models/stock.py`
- `src/models/order.py`
- `src/storage/database.py`
- `src/dashboard/*`
- `src/kis_api/*`
- `config/*`
- 그 외 모든 파일

## 배경 — 사실 base

### M-12 결과 (확정됨)

**main.py:801-835 의 청산 시그널 분기 (옛 _process_signals 안)**:
```
청산 발동 (signal_exit) →
  1. trader.execute_exit
  2. _completed_codes.add (옛 자료구조)
  3. trader.get_pnl + risk.record_trade_result
  4. _build_trade_record + _trade_records.append + _db.save_trade
  5. get_balance + risk.check_daily_loss_limit
  6. _fire_state_update
  7. _try_next_candidate (옛 패턴)
```

→ 이 7 단계 중 (1) 은 *Coordinator 가 이미 처리* (W-05c 결과). (2) 는 *옛 자료구조 폐기*. (3)~(6) 은 *콜백 함수 안* 으로 이동. (7) 의 잔고 갱신 영역도 *콜백 안* 으로 이동.

**main.py:353-413 의 _try_next_candidate**:
- 멀티 트레이드 가드 (profit_only / max_daily_trades / 시간 범위)
- 잔고 재조회 + _available_cash 갱신
- _start_monitoring_candidate 호출 (옛 패턴)

→ 멀티 트레이드 가드 + 잔고 갱신은 *콜백 함수* 안으로. 다음 후보 진입은 *Coordinator 가 자체 처리* (T 시점).

**Coordinator (W-05b/c 결과)**:
- W-05b: 13 메서드, 749 행
- W-05c: 6 메서드 async 화 + Trader 실호출
- 현재: _execute_exit 가 trader.execute_exit 호출 후 *추가 동작 없음*

→ W-06b1 추가: _execute_exit 끝에 콜백 호출 (있을 때만).

**W-06a 결과 (확정됨)**:
- main.py 의 옛 자료구조 6 필드 폐기
- _coordinator 인스턴스 생성 (line 66-69)
- _build_trade_record 시그니처 정정 (mon → watcher)
- _on_realtime_price / _on_futures_price 위임
- _emergency_cancel_orders 정정
- _start_monitoring_candidate 의 KIS subscribe 영역만 정정 (나머지 W-06b 영역)

**W-06a 후 발견**:
- Screener 호출자 (line 61): stock_master 인자 누락 → AutoTrader() 인스턴스 생성 시 TypeError
- _network_health_check (line 925): _active_monitor 잔존 (W-06b2 영역)

## 흐름

```
작업 1 (watcher.py — _exit_callback 필드 + set_exit_callback 메서드)
  → 작업 2 (watcher.py — _execute_exit 끝에 콜백 호출)
  → 작업 3 (main.py — StockMaster 인스턴스 생성)
  → 작업 4 (main.py — Screener 호출자 정정)
  → 작업 5 (main.py — Notifier 호출자 정정)
  → 작업 6 (main.py — _on_exit_done 콜백 함수 신규)
  → 작업 7 (main.py — run() 안에서 Coordinator.set_exit_callback 등록)
  → 작업 8 (main.py — run() 안에서 초기 set_available_cash 호출)
  → 검증 1~6
  → 보고
```

중간에 멈추지 말 것. 각 작업 사이 검증 X.

---

## [작업 1] watcher.py — _exit_callback 필드 + set_exit_callback 메서드

### 1-1. WatcherCoordinator.__init__ 안에 필드 추가

기존 (W-05c 결과 — line 469 근방):
```python
    def __init__(self, params: StrategyParams, trader=None):
        self.params = params
        self.trader = trader
        self.watchers: list[Watcher] = []
        self._active_code: Optional[str] = None
        self._latest_futures_price: float = 0.0
        self._available_cash: int = 0  # main.py 에서 주입
        
        # 매수 마감 / 강제 청산 시각 (캐싱)
        self._entry_deadline = time.fromisoformat(params.entry.entry_deadline)
        self._force_liquidate_time = time.fromisoformat(params.exit.force_liquidate_time)
        
        # 일별 상태 (start_screening 시 리셋)
        self._screening_done: bool = False
```

변경 (한 줄 추가):
```python
    def __init__(self, params: StrategyParams, trader=None):
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
```

### 1-2. set_exit_callback 메서드 추가

`set_available_cash` 메서드 *직후* 에 추가:

기존 (W-05c 결과):
```python
    def set_available_cash(self, amount: int) -> None:
        """매수 가능 현금 갱신. main.py 가 호출."""
        self._available_cash = amount
```

변경 (메서드 1개 추가):
```python
    def set_available_cash(self, amount: int) -> None:
        """매수 가능 현금 갱신. main.py 가 호출."""
        self._available_cash = amount
    
    def set_exit_callback(self, callback) -> None:
        """청산 후 콜백 등록. main.py 가 호출.
        
        콜백 시그니처: async def callback(watcher: Watcher) -> None
        호출 시점: _execute_exit 가 trader.execute_exit 끝낸 직후
        """
        self._exit_callback = callback
```

타입 힌트는 *간단하게 생략*. 콜백 시그니처는 docstring 에만 명시.

---

## [작업 2] watcher.py — _execute_exit 끝에 콜백 호출

### 2-1. 기존 _execute_exit (W-05c 결과 — line 637 근방)

```python
    async def _execute_exit(self, watcher: Watcher, ts: datetime) -> None:
        """청산 발주. Trader 호출."""
        logger.warning(
            f"[Coordinator] 청산 발주: {watcher.name} "
            f"reason={watcher.exit_reason} price={watcher.exit_price}"
        )
        
        if self.trader is not None:
            await self.trader.execute_exit(
                watcher, watcher.exit_reason, watcher.exit_price
            )
```

### 2-2. 변경 — 콜백 호출 추가

```python
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
```

주의:
- 콜백은 `await` 로 호출 (async 함수 가정)
- 콜백이 없으면 *조용히 skip*
- Trader 호출 *후* 에 콜백 호출 (순서 중요)

---

## [작업 3] main.py — StockMaster 인스턴스 생성

### 3-1. import 추가

main.py 의 import 영역에 추가:
```python
from src.core.stock_master import StockMaster
```

위치: 다른 `from src.core.*` import 옆.

### 3-2. __init__ 안에 인스턴스 생성

기존 (W-06a 결과 — line 55-69 근방):
```python
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

        # ── Coordinator (W-05b/c 결과물) ──
        self._coordinator: WatcherCoordinator = WatcherCoordinator(
            params=self.params,
            trader=self.trader,
        )
```

변경 (StockMaster 인스턴스를 self.api 후 / Screener 전 에 생성):
```python
        self.api = KISAPI(
            app_key=self.settings.kis_app_key,
            app_secret=self.settings.kis_app_secret,
            account_no=self.settings.account_no,
            is_paper=self.settings.is_paper_mode,
            infra_params=self.params.infra,
        )
        
        # ── StockMaster (W-02 결과물, Screener/Notifier 공유) ──
        self._stock_master = StockMaster()
        
        self.screener = Screener(self.api, self.params, stock_master=self._stock_master, is_live=self.settings.is_live, use_live_data=self.settings.use_live_data)
        self.trader = Trader(self.api, self.settings, self.params)
        self.risk = RiskManager(self.params)

        # ── Coordinator (W-05b/c 결과물) ──
        self._coordinator: WatcherCoordinator = WatcherCoordinator(
            params=self.params,
            trader=self.trader,
        )
```

주의:
- StockMaster 생성 위치: self.api 직후, self.screener 직전
- self._stock_master 필드로 보관 (Notifier 도 사용)
- Screener 호출에 stock_master 인자 추가 (작업 4)

---

## [작업 4] main.py — Screener 호출자 정정

작업 3-2 의 변경에 *이미 포함됨*. 즉:

기존:
```python
self.screener = Screener(self.api, self.params, is_live=self.settings.is_live, use_live_data=self.settings.use_live_data)
```

변경:
```python
self.screener = Screener(self.api, self.params, stock_master=self._stock_master, is_live=self.settings.is_live, use_live_data=self.settings.use_live_data)
```

W-03 의 Screener.__init__ 가 stock_master 키워드 인자를 받음. 위치는 *params 직후*.

만약 W-03 의 시그니처가 *키워드 인자 위치* 가 다르면 — *그 위치에 맞게* 정정.

---

## [작업 5] main.py — Notifier 호출자 정정

기존 (M-12 결과 — line 89-92):
```python
        # ── 텔레그램 알림 ──
        self.notifier = Notifier(
            bot_token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
        )
```

변경:
```python
        # ── 텔레그램 알림 ──
        self.notifier = Notifier(
            bot_token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
            stock_master=self._stock_master,
        )
```

W-04 의 Notifier.__init__ 가 stock_master 키워드 인자를 받음.

만약 W-04 의 시그니처가 다르면 *그에 맞게* 정정.

---

## [작업 6] main.py — _on_exit_done 콜백 함수 신규 작성

### 6-1. 추가 위치

`_build_trade_record` 함수 *직후* 또는 `_emergency_cancel_orders` 함수 *직후*. 즉 *기존 매매 영역 함수 옆*. 자연스러운 위치 선택.

권장: `_emergency_cancel_orders` 직후 (line 913 근방).

### 6-2. 함수 본문

```python
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
        
        profit_reasons = {"target"}
        is_profit_exit = watcher.exit_reason in profit_reasons
        
        if mt.profit_only and not is_profit_exit:
            logger.info(f"손실/비수익 청산({watcher.exit_reason}) → 멀티 트레이드 중단 (당일 매매 종료)")
            self._coordinator.set_available_cash(0)
            await self._fire_state_update()
            return
        
        if self.risk.daily_trades >= mt.max_daily_trades:
            logger.info(f"일일 최대 매매 횟수 도달 ({self.risk.daily_trades}/{mt.max_daily_trades}) → 중단")
            self._coordinator.set_available_cash(0)
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
        
        # 7. 대시보드 동기화
        await self._fire_state_update()
```

주의:
- 시그니처: `async def _on_exit_done(self, watcher: Watcher) -> None`
- Watcher 타입 힌트 사용 (W-06a 에서 import 됨)
- 본문은 옛 _process_signals (line 813-829) + 옛 _try_next_candidate (line 361-403) 의 *비즈니스 로직* 그대로
- 옛 _completed_codes / _candidate_pool / _candidate_index 참조 *제거*
- 옛 _start_monitoring_candidate 호출 *제거* (Coordinator 가 자동 처리)
- 로깅 메시지 한국어 그대로
- try-except 패턴 옛 코드 그대로

---

## [작업 7] main.py — run() 안에서 Coordinator.set_exit_callback 등록

### 7-1. 등록 위치

기존 (M-12 결과 — line 169-173):
```python
            # 실시간 콜백 1회 등록 (중복 방지)
            if not self._realtime_callback_registered:
                self.api.add_realtime_callback(WS_TR_PRICE, self._on_realtime_price)
                self.api.add_realtime_callback(WS_TR_FUTURES, self._on_futures_price)
                self._realtime_callback_registered = True
```

### 7-2. 변경 — Coordinator 콜백 등록 추가

```python
            # 실시간 콜백 1회 등록 (중복 방지)
            if not self._realtime_callback_registered:
                self.api.add_realtime_callback(WS_TR_PRICE, self._on_realtime_price)
                self.api.add_realtime_callback(WS_TR_FUTURES, self._on_futures_price)
                self._realtime_callback_registered = True
            
            # Coordinator 청산 콜백 등록 (W-06b1)
            self._coordinator.set_exit_callback(self._on_exit_done)
```

주의:
- 위치: realtime callback 등록 *직후*
- 매번 등록 (중복 방지 가드 X — set_exit_callback 은 단순 set 이라 중복 OK)
- 또는 가드 추가도 OK (편한 패턴)

---

## [작업 8] main.py — run() 안에서 초기 set_available_cash 호출

### 8-1. 호출 위치

기존 (M-12 결과 — line 184-192):
```python
            # 계좌 잔고 확인
            if self.settings.is_dry_run and self.settings.dry_run_cash > 0:
                self._initial_cash = self.settings.dry_run_cash
                logger.info(f"[DRY_RUN] 가상 예수금 사용: {self._initial_cash:,}원")
            else:
                balance = await self.api.get_balance()
                self._initial_cash = balance.get("available_cash", 0)
            self._available_cash = self.risk.calculate_available_cash(self._initial_cash)
            logger.info(f"예수금: {self._initial_cash:,}원 → 매매가용: {self._available_cash:,}원")
```

### 8-2. 변경 — Coordinator 에 초기 잔고 주입

```python
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
```

주의:
- 한 줄 추가
- 위치: self._available_cash 갱신 *직후*, logger 호출 *직전*

---

## [검증]

### 검증 1 — watcher.py import OK

```bash
python -c "from src.core.watcher import Watcher, WatcherCoordinator; print('watcher import OK')"
```

기대: `watcher import OK`

### 검증 2 — Coordinator.set_exit_callback 메서드 존재 + _exit_callback 필드

```bash
python -c "
from src.core.watcher import WatcherCoordinator
from config.settings import StrategyParams
params = StrategyParams.load()
c = WatcherCoordinator(params=params, trader=None)
print('initial _exit_callback:', c._exit_callback)
print('has set_exit_callback:', hasattr(c, 'set_exit_callback'))

async def fake_callback(watcher):
    print('callback called')

c.set_exit_callback(fake_callback)
print('after set:', c._exit_callback is not None)
"
```

기대:
```
initial _exit_callback: None
has set_exit_callback: True
after set: True
```

### 검증 3 — main.py import OK

```bash
python -c "from src.main import AutoTrader; print('main import OK')"
```

기대: `main import OK`

### 검증 4 — AutoTrader 인스턴스 생성 (Screener 호환성)

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('stock_master:', type(at._stock_master).__name__)
print('screener:', type(at.screener).__name__)
print('notifier:', type(at.notifier).__name__)
print('coordinator:', type(at._coordinator).__name__)
print('has _on_exit_done:', hasattr(at, '_on_exit_done'))
"
```

기대:
```
stock_master: StockMaster
screener: Screener
notifier: Notifier
coordinator: WatcherCoordinator
has _on_exit_done: True
```

### 검증 5 — _on_exit_done 시그니처

```bash
python -c "
import inspect
from src.main import AutoTrader
sig = inspect.signature(AutoTrader._on_exit_done)
params_list = list(sig.parameters.keys())
print('params:', params_list)
print('is coroutine:', inspect.iscoroutinefunction(AutoTrader._on_exit_done))
assert 'watcher' in params_list, 'watcher 인자 누락'
print('_on_exit_done 시그니처 OK')
"
```

기대:
```
params: ['self', 'watcher']
is coroutine: True
_on_exit_done 시그니처 OK
```

### 검증 6 — Coordinator 의 _execute_exit 가 콜백 호출 패턴 가짐

Python 직접 읽기 (S-02-d):

```bash
python -c "
with open('src/core/watcher.py', 'r', encoding='utf-8') as f:
    content = f.read()
has_callback_call = 'self._exit_callback(watcher)' in content
print('_execute_exit 안에 콜백 호출:', has_callback_call)
assert has_callback_call, '_execute_exit 안에 콜백 호출 누락'
"
```

기대: `_execute_exit 안에 콜백 호출: True`

---

## [검증 실패 시 — 멈춤 조건]

- 검증 1 실패 (watcher import) → 멈춤 + 보고
- 검증 2 실패 (set_exit_callback 미존재) → 멈춤 + 보고
- 검증 3 실패 (main import) → 멈춤 + 보고
- 검증 4 실패 (AutoTrader 생성) → 환경변수 이슈 외에 *Screener 시그니처 불일치* 면 멈춤 + 보고
- 검증 5 실패 (_on_exit_done 시그니처) → 멈춤 + 보고
- 검증 6 실패 (콜백 호출 누락) → 멈춤 + 보고

멈춤 시 변경 *되돌리지 말 것*. 그대로 두고 보고.

---

## [모호한 케이스 — 사전 결정]

### 케이스 F-1: Screener 시그니처의 stock_master 인자 위치
W-03 결과로 *키워드 인자* 로 추가됨. 정확한 위치는 키워드 인자라 *순서 무관*. 작업 4 처럼 명시적으로 `stock_master=...` 키워드로 전달.

### 케이스 F-2: Notifier 시그니처의 stock_master 인자 위치
W-04 결과. 키워드 인자로 추가됨. `stock_master=self._stock_master` 키워드로 전달.

### 케이스 F-3: StockMaster() 생성 시 인자
W-02 결과의 StockMaster 클래스가 *기본값* 으로 생성 가능한지 확인. 기본 경로 (config/stock_master.json) 사용.

만약 *경로 인자 필수* 면 → 명시적 전달:
```python
from pathlib import Path
master_path = Path(__file__).parent.parent / "config" / "stock_master.json"
self._stock_master = StockMaster(master_path)
```

### 케이스 F-4: _on_exit_done 의 위치
권장: `_emergency_cancel_orders` 직후 (line 913 근방). 다른 위치도 OK 다만 *매매 영역* 함수들 옆이 자연스러움.

### 케이스 F-5: Coordinator.set_exit_callback 의 매번 호출
run() 안에서 *매번 등록* 가능. 같은 콜백을 다시 set 해도 *동일* (단순 set). 가드 불필요.

### 케이스 F-6: 콜백 함수 안의 _fire_state_update 호출 위치
명령서의 _on_exit_done 본문에 _fire_state_update 가 *4 곳* 에서 호출:
1. multi_trade 비활성 시 return 직전
2. profit_only 가드 시 return 직전
3. max_daily_trades 가드 시 return 직전
4. 정상 흐름 끝

각 return 직전에 호출. 정상 흐름 끝의 호출도 *유지*.

### 케이스 F-7: Watcher 타입 힌트 import 가 main.py 에 있는가
W-06a 에서 `from src.core.watcher import Watcher, WatcherCoordinator` 추가됨. 이미 있음. 추가 import 불필요.

### 케이스 F-8: _on_exit_done 의 logger 메시지
옛 _process_signals + _try_next_candidate 의 logger 메시지 *그대로 보존*. 한국어 메시지.

### 케이스 F-9: trader.reset 호출 위치
콜백 함수 안. 옛 _try_next_candidate 의 line 388 위치. 즉 *잔고 갱신 후*. 다만 *Coordinator 의 다음 매수 평가* 가 *trader.reset 후* 에 일어나야 함.

→ 콜백 함수 안에서 *trader.reset 후* `_fire_state_update` 호출. 그 다음에 *return*. Coordinator 가 *다음 _process_signals 호출 시* 자동으로 다음 매수 평가.

### 케이스 F-10: _exit_callback 타입 힌트
간단하게 생략. 또는 `Optional[Callable]` 사용. 권장: 생략 (코드 단순).

---

## [자체 발견 처리 규칙]

Code 가 작업 중 다음을 발견하면 → 수정하지 말 것 → 보고의 [발견] 섹션에 1~3줄 기록:
- watcher.py 의 잠재적 버그
- main.py 의 W-06a 결과의 잠재적 문제
- Screener / Notifier 의 stock_master 인자 시그니처 불일치
- StockMaster() 생성 실패 (파일 경로 등)
- W-06b2 에서 처리해야 할 추가 영역

---

## [보고]

### A. 변경 라인 (watcher.py)
- 작업 1 (필드 + 메서드): 라인 번호 + 추가 라인 수
- 작업 2 (_execute_exit 콜백 호출): 라인 번호 + 추가 라인 수

### B. 변경 라인 (main.py)
- 작업 3 (StockMaster import + 인스턴스): 라인 번호
- 작업 4 (Screener 호출자): 라인 번호
- 작업 5 (Notifier 호출자): 라인 번호
- 작업 6 (_on_exit_done 신규): 라인 범위
- 작업 7 (set_exit_callback 등록): 라인 번호
- 작업 8 (초기 set_available_cash): 라인 번호

### C. 라인 수
- watcher.py: before / after / 차이
- main.py: before / after / 차이

### D. 검증 결과
- 검증 1 ~ 6: 각각 성공/실패 + 출력

### E. 다른 파일 수정 여부
*반드시 watcher.py + main.py 두 파일만*

### F. 모호한 케이스 처리 결과 (F-1 ~ F-10 중 발생한 것)

### G. [발견] 섹션 — 자체 발견 사항 (있으면)

### H. W-06b2 진입 준비
- Coordinator 콜백 패턴 완성
- StockMaster 의존성 주입 완성
- _on_exit_done 콜백 함수 신규 작성 완료
- W-06b2 에서 처리할 함수 목록:
  - _on_screening 정정 (Coordinator.start_screening 호출)
  - _start_monitoring_candidate 폐기
  - _try_next_candidate 폐기
  - _process_signals 폐기 또는 축소
  - _monitor_loop_runner 폐기
  - _schedule_force_liquidate 단순화
  - _schedule_market_close 정정 (_monitors → coordinator.watchers)
  - _get_status 정정
  - _network_health_check 정정 (_active_monitor → coordinator.has_active)
  - _schedule_buy_deadline 신규 (10:55 트리거)
  - run() 의 tasks 리스트 정정 (monitor_loop 제거, buy_deadline 추가)

---

## [추가 금지 — 자동 모드 강화]

- watcher.py + main.py 외 어떤 파일도 수정 금지
- Trader 클래스 변경 금지 (W-05c 결과)
- monitor.py 수정 금지 (W-07 영역)
- Screener / Notifier / StockMaster 클래스 변경 금지 (W-02/03/04 결과)
- _on_screening 본문 변경 금지 (W-06b2 영역)
- _start_monitoring_candidate 본문 변경 금지 (W-06b2 영역)
- _try_next_candidate 본문 변경 금지 (W-06b2 영역)
- _process_signals 본문 변경 금지 (W-06b2 영역)
- _monitor_loop_runner 본문 변경 금지 (W-06b2 영역)
- _schedule_force_liquidate 본문 변경 금지 (W-06b2 영역)
- _schedule_market_close 본문 변경 금지 (W-06b2 영역)
- _get_status 본문 변경 금지 (W-06b2 영역)
- _network_health_check 본문 변경 금지 (W-06b2 영역)
- _build_trade_record 본문 변경 금지 (W-06a 결과)
- run() 의 tasks 리스트 변경 금지 (W-06b2 영역, 단 작업 7+8 의 한 줄 추가는 OK)
- 새 함수 / 새 클래스 추가 금지 (단, _on_exit_done + set_exit_callback 만 명령서 명시)
- try-except 추가/제거 금지 (단, _on_exit_done 본문은 명령서 명시 패턴 그대로)
- 타입 힌트 추가 금지 (단, 시그니처 변경 영역은 OK)
- 새 import 금지 (단, StockMaster 추가는 명령서 명시)
- 로깅 메시지 변경 금지 (한국어 그대로)
- 영어 메시지 추가 금지
- TradeTarget 참조 추가 금지 (W-07 폐기 예정)
- TargetMonitor 참조 추가 금지 (W-07 폐기 예정)
- commit 금지
- 임시 파일 / scratch 파일 생성 금지
- W-06b2 / W-07 / W-08 영역 손대지 말 것
