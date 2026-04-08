# W-08 — R-07 통합 검증 (마지막 단계)

## 미션

R-07 redesign 의 *최종 통합 검증*. 코드 수정 0줄. 검증만.

R-07 의 14단계 코드 작업 (W-01 ~ W-07d) 이 *모두 정확히 통합* 되어 동작함을 확인. baseline day 가동 자격 검증.

## 화이트리스트

**없음 — 코드 수정 0줄**.

## 블랙리스트

**모든 파일 — 수정 금지**.

W-08 의 모든 작업은 *읽기 / 검증 / 시뮬레이션* 만.

## 배경 — R-07 종결 직전

### 완료 작업 14 단계
- W-01: yaml 8 키 변경
- W-02: StockMaster 신규 (+70행)
- W-03: screener.py 부분 수정 (-180행)
- W-04: notifier.py 부분 수정 (+44행)
- W-05a: Watcher 클래스 (+463행)
- W-05b: WatcherCoordinator + 필드 정정 (+286행)
- W-05c: Trader 시그니처 정정 + Coordinator async
- W-06a: main.py 사실 base 정정
- W-06b1: Coordinator 콜백 + DI + StockMaster
- W-06b2: main.py 매매 위임 + 함수 폐기 (-161행)
- W-07a: screener TradeTarget 의존 제거
- W-07b: dashboard TargetMonitor 의존 제거
- W-07c: tests 3 파일 폐기
- W-07d: monitor.py + TradeTarget 클래스 폐기

### 누적 W-08 검증 항목 (R-07 진행 중 등록)
- S-01: StrategyParams.load() main.py init 호출 + 전파
- S-02-a: 모든 W-시리즈 파일 UTF-8 저장
- S-02-b: 파일 읽기 encoding="utf-8" 명시
- S-02-c: Linux 환경 무영향 (참고)
- S-02-d: 검증 스크립트 grep 대신 Python 직접 읽기
- S-03: import 경로 일관성

### 추가 검증 (W-08 신규)
- 통합 import 검증 (모든 모듈)
- AutoTrader 인스턴스 생성 + 의존성 주입 검증
- WatcherCoordinator 생성 + 콜백 등록
- Watcher 시나리오 시뮬 (5조건 청산 + 매수 트리거)
- DRY_RUN 시뮬 (가능하면)

## 흐름

```
S-01 (StrategyParams 전파)
  → S-02 (인코딩 일관성)
  → S-03 (import 경로 일관성)
  → 통합 import 검증
  → AutoTrader 인스턴스 + 의존성 주입
  → Watcher 시나리오 시뮬
  → Coordinator 시나리오 시뮬
  → DRY_RUN 시뮬 (가능하면)
  → 보고
```

검증만. 멈춤 조건 = 검증 실패 시.

---

## [S-01] StrategyParams 전파 검증

### S-01-1. StrategyParams.load() 호출 위치

main.py 의 __init__ 에서 호출:
```bash
grep -n "StrategyParams\.load\|self\.params" src/main.py | head -10
```

기대:
- main.py:54 또는 동등 위치에서 `self.params = StrategyParams.load()`
- 다른 영역에서 `self.params` 참조

### S-01-2. self.params 가 모든 의존 클래스에 전파

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('AutoTrader.params:', type(at.params).__name__)
print('Screener.params:', type(at.screener.params).__name__ if hasattr(at.screener, 'params') else 'N/A')
print('Trader.params:', type(at.trader.params).__name__)
print('Coordinator.params:', type(at._coordinator.params).__name__)
print('RiskManager.params:', type(at.risk.params).__name__ if hasattr(at.risk, 'params') else 'N/A')
"
```

기대: 모든 클래스가 *동일 StrategyParams 인스턴스* 보유 (또는 동등)

### S-01-3. yaml 8 키 변경 (W-01) 이 정확히 적용됨

```bash
python -c "
from config.settings import StrategyParams
params = StrategyParams.load()
print('screening.top_n_gainers:', params.screening.top_n_gainers)
print('entry.kospi_buy1_pct:', params.entry.kospi_buy1_pct)
print('entry.kospi_buy2_pct:', params.entry.kospi_buy2_pct)
print('entry.kosdaq_buy1_pct:', params.entry.kosdaq_buy1_pct)
print('entry.kosdaq_buy2_pct:', params.entry.kosdaq_buy2_pct)
print('entry.entry_deadline:', params.entry.entry_deadline)
print('exit.force_liquidate_time:', params.exit.force_liquidate_time)
print('exit.kosdaq_hard_stop_pct:', params.exit.kosdaq_hard_stop_pct)
"
```

기대:
```
screening.top_n_gainers: 3
entry.kospi_buy1_pct: 2.7
entry.kospi_buy2_pct: 3.3
entry.kosdaq_buy1_pct: 4.0
entry.kosdaq_buy2_pct: 5.0
entry.entry_deadline: 10:55
exit.force_liquidate_time: 11:20
exit.kosdaq_hard_stop_pct: 6.15
```

### S-01-4. high_confirm_timeout_min 보존

```bash
python -c "
from config.settings import StrategyParams
params = StrategyParams.load()
print('entry.high_confirm_timeout_min:', params.entry.high_confirm_timeout_min)
print('exit.timeout_from_low_min:', params.exit.timeout_from_low_min)
print('exit.timeout_start_after_kst:', params.exit.timeout_start_after_kst)
"
```

기대:
- high_confirm_timeout_min: 10
- timeout_from_low_min: 20
- timeout_start_after_kst: 10:00

---

## [S-02] 인코딩 일관성

### S-02-a. 모든 src/ 파일이 UTF-8

```bash
python -c "
import os
errors = []
for root, dirs, files in os.walk('src'):
    if '__pycache__' in root:
        continue
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8') as fp:
                    fp.read()
            except UnicodeDecodeError as e:
                errors.append((path, str(e)))
print('UTF-8 에러:', len(errors))
for p, e in errors:
    print(f'  {p}: {e}')
print('통과' if not errors else '실패')
"
```

기대: `통과`

### S-02-b. yaml 파일 읽기 encoding 명시 확인

```bash
grep -n "open.*\.yaml\|open.*\.yml" src/ config/ -r 2>/dev/null
```

각 라인에서 `encoding="utf-8"` 명시 여부 확인. 없으면 *Linux 무영향, Windows 잠재 사고*.

### S-02-c. Linux 환경 무영향 메모

(검증 아님 — 메모. Linux 의 기본 encoding 이 UTF-8 이라 *현재 Oracle Cloud Seoul 환경* 에서는 무영향.)

### S-02-d. 검증 스크립트가 grep 대신 Python 직접 읽기

본 명령서의 모든 검증이 Python 직접 읽기 패턴 사용 확인. (이미 적용됨.)

---

## [S-03] import 경로 일관성

### S-03-1. 신규 파일의 import 경로 검증

```bash
python -c "
import os
# 검증 대상 import 패턴
expected_patterns = {
    'from config.settings': 'StrategyParams 경로',
    'from src.models.stock': 'Stock / StockCandidate / MarketType',
    'from src.core.watcher': 'Watcher / WatcherCoordinator',
    'from src.core.stock_master': 'StockMaster',
    'from src.core.trader': 'Trader',
    'from src.core.screener': 'Screener',
}

# 폐기되었어야 할 import
forbidden_patterns = [
    'from src.core.monitor',
    'from src.core import monitor',
    'TradeTarget',
    'TargetMonitor',
    'MonitorState',
]

# 모든 src/ 파일 검사
issues = []
for root, dirs, files in os.walk('src'):
    if '__pycache__' in root:
        continue
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            with open(path, 'r', encoding='utf-8') as fp:
                content = fp.read()
            for forbidden in forbidden_patterns:
                if forbidden in content:
                    issues.append((path, forbidden))

if issues:
    print('금지 패턴 잔존:')
    for p, f in issues:
        print(f'  {p}: {f}')
else:
    print('잔존 0건')
"
```

기대: `잔존 0건`

### S-03-2. import 경로의 from src.config 사용 금지

W-05a 케이스 W-1 에서 발견된 패턴: `from src.config` 가 *틀림*. 정확한 경로는 `from config.settings`.

```bash
python -c "
import os
issues = []
for root, dirs, files in os.walk('src'):
    if '__pycache__' in root:
        continue
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            with open(path, 'r', encoding='utf-8') as fp:
                content = fp.read()
            if 'from src.config' in content:
                issues.append(path)

print('from src.config 잔존:', issues)
print('통과' if not issues else '실패')
"
```

기대: `통과`

---

## [통합 import 검증]

### IMP-1. 모든 핵심 모듈 import OK

```bash
python -c "
from src.main import AutoTrader
from src.core.watcher import Watcher, WatcherCoordinator, WatcherState
from src.core.trader import Trader
from src.core.screener import Screener
from src.core.stock_master import StockMaster
from src.utils.notifier import Notifier
from src.models.stock import Stock, StockCandidate, MarketType
from src.models.order import Position, Order
from src.dashboard import app
from config.settings import Settings, StrategyParams
print('all imports OK')
"
```

기대: `all imports OK`

### IMP-2. monitor.py / TradeTarget import 불가 확인

```bash
python -c "
try:
    from src.core import monitor
    print('FAIL — monitor.py 잔존')
except (ImportError, ModuleNotFoundError):
    print('OK — monitor.py 폐기')

try:
    from src.models.stock import TradeTarget
    print('FAIL — TradeTarget 잔존')
except ImportError:
    print('OK — TradeTarget 폐기')
"
```

기대:
```
OK — monitor.py 폐기
OK — TradeTarget 폐기
```

---

## [INST] AutoTrader 인스턴스 생성 + 의존성 주입 검증

### INST-1. AutoTrader 인스턴스 생성

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('AutoTrader 인스턴스 생성 OK')
print('  settings:', type(at.settings).__name__)
print('  params:', type(at.params).__name__)
print('  api:', type(at.api).__name__)
print('  stock_master:', type(at._stock_master).__name__)
print('  screener:', type(at.screener).__name__)
print('  trader:', type(at.trader).__name__)
print('  risk:', type(at.risk).__name__)
print('  coordinator:', type(at._coordinator).__name__)
print('  notifier:', type(at.notifier).__name__)
print('  db:', type(at._db).__name__)
"
```

기대: 모든 인스턴스 정상 생성

### INST-2. Coordinator 의 trader 의존성 주입 확인

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('coordinator.trader is at.trader:', at._coordinator.trader is at.trader)
print('coordinator.params is at.params:', at._coordinator.params is at.params)
"
```

기대: 두 항목 모두 `True`

### INST-3. Screener 의 stock_master 의존성 주입 확인

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('screener.stock_master is at._stock_master:', at.screener.stock_master is at._stock_master)
"
```

기대: `True`

### INST-4. Notifier 의 stock_master 의존성 주입 확인

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('notifier.stock_master is at._stock_master:', at.notifier.stock_master is at._stock_master)
"
```

기대: `True`

### INST-5. WatcherCoordinator 의 초기 상태

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
c = at._coordinator
print('watchers:', c.watchers)
print('_active_code:', c._active_code)
print('_screening_done:', c._screening_done)
print('_available_cash:', c._available_cash)
print('_exit_callback:', c._exit_callback)
print('_subscribed_codes:', at._subscribed_codes)
"
```

기대:
```
watchers: []
_active_code: None
_screening_done: False
_available_cash: 0
_exit_callback: None
_subscribed_codes: []
```

(`_exit_callback` 은 run() 실행 후에야 등록됨. __init__ 시점에는 None 정상.)

---

## [SCEN] Watcher 시나리오 시뮬

### SCEN-1. Watcher 5조건 청산 시뮬 — 하드 손절

```bash
python -c "
from src.core.watcher import Watcher, WatcherState
from config.settings import StrategyParams
from src.models.stock import MarketType
from datetime import datetime

params = StrategyParams.load()
w = Watcher(code='005930', name='삼성전자', market=MarketType.KOSPI, params=params)

# ENTERED 상태 셋업
w.state = WatcherState.ENTERED
w.confirmed_high = 100000
w.hard_stop_price_value = 95900  # KOSPI -4.1%
w.post_entry_low = 99000
w.post_entry_low_time = datetime(2026, 4, 8, 10, 30, 0)

# 하드 손절 트리거
ts = datetime(2026, 4, 8, 10, 35, 0)
w._handle_entered(95800, ts)

print('state:', w.state.value)
print('exit_reason:', w.exit_reason)
print('exit_price:', w.exit_price)
print('exit_signal_pending:', w._exit_signal_pending)
assert w.state == WatcherState.EXITED, 'EXITED 전이 실패'
assert w.exit_reason == 'hard_stop', 'hard_stop 미발동'
assert w.exit_price == 0, '시장가 가격 0 아님'
print('하드 손절 시뮬 통과')
"
```

기대: `하드 손절 시뮬 통과`

### SCEN-2. Watcher 신고가 트리거 시뮬

```bash
python -c "
from src.core.watcher import Watcher, WatcherState
from config.settings import StrategyParams
from src.models.stock import MarketType
from datetime import datetime, time

params = StrategyParams.load()
w = Watcher(code='005930', name='삼성전자', market=MarketType.KOSPI, params=params)

# 09:50 시점 — pre_955_high 추적
ts = datetime(2026, 4, 8, 9, 50, 0)
w.pre_955_high = 100000
w.intraday_high = 100000
w.current_price = 100000
w._handle_watching(101000, ts)
print('09:50 pre_955_high 갱신:', w.pre_955_high)
assert w.pre_955_high == 101000, 'pre_955_high 갱신 실패'

# 09:55 직후 — 신고가 달성
ts = datetime(2026, 4, 8, 9, 55, 30)
w._handle_watching(102000, ts)
print('09:55 후 new_high_achieved:', w.new_high_achieved)
print('intraday_high:', w.intraday_high)
assert w.new_high_achieved, 'new_high_achieved 실패'

# 1% 하락 트리거
ts = datetime(2026, 4, 8, 10, 0, 0)
w._handle_watching(100980, ts)  # 102000 * (1 - 0.01) = 100980
print('1%% 하락 후 state:', w.state.value)
print('confirmed_high:', w.confirmed_high)
print('target_buy1_price:', w.target_buy1_price)
print('hard_stop_price_value:', w.hard_stop_price_value)
assert w.state in (WatcherState.READY, WatcherState.PASSED, WatcherState.TRIGGERED), '트리거 발동 실패'
print('신고가 트리거 시뮬 통과')
"
```

기대: `신고가 트리거 시뮬 통과`

### SCEN-3. WatcherCoordinator 통합 시뮬

```bash
python -c "
from src.core.watcher import WatcherCoordinator, Watcher, WatcherState
from config.settings import StrategyParams
from src.models.stock import StockCandidate, MarketType
from datetime import datetime
import asyncio

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
print('watchers:', len(c.watchers))
print('watcher 0:', c.watchers[0].code, c.watchers[0].state.value)
print('watcher 1:', c.watchers[1].code, c.watchers[1].state.value)
print('watcher 2:', c.watchers[2].code, c.watchers[2].state.value)
assert len(c.watchers) == 3, 'watchers 수 불일치'

# 시세 라우팅 시뮬 (async)
async def test_routing():
    ts = datetime(2026, 4, 8, 10, 0, 0)
    await c.on_realtime_price('005930', 71000, ts)
    return c.watchers[0].intraday_high

result = asyncio.run(test_routing())
print('after price tick — watcher 0 high:', result)
assert result == 71000, 'intraday_high 갱신 실패'
print('Coordinator 통합 시뮬 통과')
"
```

기대: `Coordinator 통합 시뮬 통과`

### SCEN-4. on_buy_deadline 시뮬

```bash
python -c "
from src.core.watcher import WatcherCoordinator, Watcher, WatcherState
from config.settings import StrategyParams
from src.models.stock import StockCandidate, MarketType
from datetime import datetime
import asyncio

params = StrategyParams.load()
c = WatcherCoordinator(params=params, trader=None)

candidates = [
    StockCandidate(code='005930', name='삼성전자', market=MarketType.KOSPI,
                   trading_volume_krw=100000000000, program_net_buy=10000000000,
                   price_change_pct=2.5, current_price=70000),
]
c.start_screening(candidates)

async def test_deadline():
    ts = datetime(2026, 4, 8, 10, 55, 0)
    await c.on_buy_deadline(ts)
    return c.watchers[0].state.value

result = asyncio.run(test_deadline())
print('after deadline — state:', result)
assert result == 'skipped', 'SKIPPED 전이 실패'
print('on_buy_deadline 시뮬 통과')
"
```

기대: `on_buy_deadline 시뮬 통과`

### SCEN-5. on_force_liquidate 시뮬

```bash
python -c "
from src.core.watcher import WatcherCoordinator, Watcher, WatcherState
from config.settings import StrategyParams
from src.models.stock import StockCandidate, MarketType
from datetime import datetime
import asyncio

params = StrategyParams.load()
c = WatcherCoordinator(params=params, trader=None)

candidates = [
    StockCandidate(code='005930', name='삼성전자', market=MarketType.KOSPI,
                   trading_volume_krw=100000000000, program_net_buy=10000000000,
                   price_change_pct=2.5, current_price=70000),
]
c.start_screening(candidates)

# ENTERED 상태로 강제 셋업
c.watchers[0].state = WatcherState.ENTERED
c.watchers[0].confirmed_high = 100000
c.watchers[0].hard_stop_price_value = 95900
c._active_code = '005930'

async def test_force():
    ts = datetime(2026, 4, 8, 11, 20, 0)
    await c.on_force_liquidate(ts)
    return c.watchers[0].state.value, c.watchers[0].exit_reason

state, reason = asyncio.run(test_force())
print('after force — state:', state, 'reason:', reason)
assert state == 'exited', '강제 청산 EXITED 실패'
assert reason == 'force', '강제 청산 reason 실패'
print('on_force_liquidate 시뮬 통과')
"
```

기대: `on_force_liquidate 시뮬 통과`

---

## [TRADE] Trader 호환성 검증

### TRADE-1. Trader 의 4 시그니처 Watcher 호환

```bash
python -c "
import inspect
from src.core.trader import Trader

# 4 메서드 시그니처 확인
methods = ['place_buy_orders', 'cancel_buy_orders', 'execute_exit', 'simulate_fills']
for m in methods:
    sig = inspect.signature(getattr(Trader, m))
    params_list = list(sig.parameters.keys())
    has_watcher = 'watcher' in params_list
    has_target = 'target' in params_list
    print(f'{m}: params={params_list}, watcher_OK={has_watcher}, target_FREE={not has_target}')
    assert has_watcher and not has_target, f'{m} 시그니처 불일치'
print('Trader 4 시그니처 모두 Watcher 호환')
"
```

기대: `Trader 4 시그니처 모두 Watcher 호환`

### TRADE-2. Trader async/sync 분리 보존

```bash
python -c "
import inspect
from src.core.trader import Trader

async_methods = ['place_buy_orders', 'cancel_buy_orders', 'execute_exit']
sync_methods = ['simulate_fills', 'on_buy_filled', 'has_position', 'get_pnl', 'reset']

for m in async_methods:
    is_coro = inspect.iscoroutinefunction(getattr(Trader, m))
    print(f'{m}: async={is_coro}')
    assert is_coro, f'{m} async 아님'

for m in sync_methods:
    is_coro = inspect.iscoroutinefunction(getattr(Trader, m))
    print(f'{m}: sync={not is_coro}')
    assert not is_coro, f'{m} async 됨'

print('Trader async/sync 분리 보존')
"
```

기대: `Trader async/sync 분리 보존`

---

## [DRY_RUN] DRY_RUN 시뮬 (가능하면)

### DRY-1. DRY_RUN 모드 설정 가능

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
os.environ.setdefault('DRY_RUN', 'true')
os.environ.setdefault('DRY_RUN_CASH', '10000000')
from src.main import AutoTrader
at = AutoTrader()
print('is_dry_run:', at.settings.is_dry_run)
print('dry_run_cash:', at.settings.dry_run_cash)
"
```

기대: `is_dry_run: True`, `dry_run_cash: 10000000`

만약 환경변수 패턴이 다르면 → 보고에 명시 + 검증 SKIP.

### DRY-2. Coordinator + Trader DRY_RUN 통합 (가능하면)

DRY_RUN 환경이 *제대로 동작* 하면 다음 시뮬:

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
os.environ.setdefault('DRY_RUN', 'true')
os.environ.setdefault('DRY_RUN_CASH', '10000000')

from src.main import AutoTrader
at = AutoTrader()

print('AutoTrader DRY_RUN 모드 인스턴스 OK')
print('  trader.settings.is_dry_run:', at.trader.settings.is_dry_run)
print('  coordinator.trader.settings.is_dry_run:', at._coordinator.trader.settings.is_dry_run)
"
```

기대: 모든 항목 True

---

## [DASH] dashboard 검증

### DASH-1. dashboard import + 기본 기동 가능 여부

```bash
python -c "
from src.dashboard import app
print('dashboard module:', type(app).__name__)
print('module attributes:', [x for x in dir(app) if not x.startswith('_')][:20])
"
```

기대: dashboard 모듈 정상 import

### DASH-2. DashboardState 의 to_dict 호환성 (가능하면)

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.dashboard import app
from src.main import AutoTrader

# DashboardState 클래스 식별
import inspect
classes = [name for name, obj in inspect.getmembers(app, inspect.isclass)]
print('dashboard classes:', classes)
"
```

기대: DashboardState 또는 동등 클래스 존재

---

## [LINE] 라인 수 누적 변화

```bash
python -c "
import os

files = {
    'src/main.py': None,
    'src/core/watcher.py': None,
    'src/core/trader.py': None,
    'src/core/screener.py': None,
    'src/core/stock_master.py': None,
    'src/utils/notifier.py': None,
    'src/dashboard/app.py': None,
    'src/models/stock.py': None,
    'src/models/__init__.py': None,
}

for path in files:
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            files[path] = len(f.readlines())

# 폐기된 파일
removed = ['src/core/monitor.py']
print('=== R-07 종결 시점 라인 수 ===')
for path, lines in files.items():
    print(f'{path}: {lines}')
print()
print('=== 폐기된 파일 ===')
for path in removed:
    exists = os.path.exists(path)
    print(f'{path}: {\"잔존\" if exists else \"폐기\"}')

# 검증 — monitor.py 폐기
assert not os.path.exists('src/core/monitor.py'), 'monitor.py 잔존'
print()
print('R-07 라인 검증 통과')
"
```

기대: `R-07 라인 검증 통과`

---

## [검증 실패 시 — 멈춤 조건]

- 어떤 검증이라도 실패 → 멈춤 + 보고
- AssertionError 발생 → 멈춤 + 보고
- ImportError 발생 → 멈춤 + 보고

W-08 은 *검증만* 작업이라 *코드 정정* 자체가 화이트리스트 외. 멈춤 시 *수석님 결정 필요*.

---

## [모호한 케이스 — 사전 결정]

### 케이스 M-1: 환경변수 KIS_APP_KEY 등이 실제 값인 경우
검증 시 *test 값* 으로 임시 대체. 실제 KIS API 호출 X (검증은 인스턴스 생성만).

### 케이스 M-2: AutoTrader.__init__ 안의 Database / CloudflareTunnel 초기화 실패
DB 파일 권한 / Tunnel 설정 부재로 실패 가능. 그 경우 *해당 검증 SKIP* + 보고에 명시.

### 케이스 M-3: dashboard 의 DashboardState 클래스가 다른 이름
to_dict 메서드를 찾기 위해 *grep 으로 클래스명 식별*. 발견 안 되면 보고에 명시.

### 케이스 M-4: DRY_RUN 환경변수 이름
- DRY_RUN / IS_DRY_RUN / TRADE_MODE 등 다양 가능
- settings.is_dry_run 패턴은 동일
- 검증 DRY-1 에서 *환경변수 이름 식별* 후 적용

### 케이스 M-5: 시뮬레이션의 datetime 이 *2026-04-08* 사용
현재 날짜 무관. 단순 시뮬 시간. 그대로 사용.

### 케이스 M-6: SCEN-2 의 1% 하락 트리거 가격 계산
102000 * (1 - 0.01) = 100980. 정확한 가격으로 트리거 발동.
만약 high_confirm_drop_pct 가 다른 값이면 (예: 1.5%) → 그에 맞춰 계산
yaml 의 entry.high_confirm_drop_pct 확인:
  python -c "from config.settings import StrategyParams; p = StrategyParams.load(); print(p.entry.high_confirm_drop_pct)"

### 케이스 M-7: SCEN-3 의 candidates 에 price_change_pct 필드
W-07b 의 발견: Watcher 에 price_change_pct 없음. 다만 StockCandidate 에는 있음.
시뮬레이션의 candidates 에 *그대로 전달*. Coordinator.start_screening 안에서 *사용 안 함* (W-07b 발견 12 — R-08 영역).

### 케이스 M-8: yaml 키 일부가 *예상 값과 다름*
S-01-3 검증에서 발견. 예상:
- top_n_gainers: 3
- entry.entry_deadline: 10:55
- exit.force_liquidate_time: 11:20
- 등등

만약 *다른 값* 이면 → W-01 결과 누락 가능. 멈춤 + 보고.

---

## [자체 발견 처리 규칙]

Code 가 작업 중 다음을 발견하면 → 보고의 [발견] 섹션에 기록:
- 검증 통과 못 한 영역
- 예상 외 값
- W-01 ~ W-07d 의 결과가 *예상과 다름*
- dashboard 의 to_dict 호환성 우려
- DRY_RUN 환경변수 패턴 식별
- baseline day 가동 전 추가 우려

---

## [보고]

### A. S-01 결과
- StrategyParams 전파 검증
- yaml 8 키 변경 적용 확인

### B. S-02 결과
- UTF-8 인코딩 일관성

### C. S-03 결과
- import 경로 일관성

### D. IMP 결과
- 모든 핵심 모듈 import OK
- monitor.py / TradeTarget 폐기 확인

### E. INST 결과
- AutoTrader 인스턴스 + 의존성 주입

### F. SCEN 결과
- Watcher 5조건 청산 시뮬 (하드 손절)
- Watcher 신고가 트리거 시뮬
- Coordinator 통합 시뮬
- on_buy_deadline 시뮬
- on_force_liquidate 시뮬

### G. TRADE 결과
- Trader 4 시그니처 Watcher 호환
- async/sync 분리 보존

### H. DRY_RUN 결과
- DRY_RUN 모드 설정 가능 여부
- 통합 인스턴스 생성

### I. DASH 결과
- dashboard import OK
- DashboardState to_dict 호환성

### J. LINE 라인 수 누적
- 모든 핵심 파일의 R-07 종결 시점 라인 수
- monitor.py 폐기 확인

### K. 모호한 케이스 처리 (M-1 ~ M-8 중 발생한 것)

### L. [발견] 섹션
- baseline day 가동 전 추가 우려 항목
- R-08 영역으로 미루어진 항목

### M. R-07 종결 선언
- 모든 검증 통과 → R-07 종결 자격
- 실패 검증 있음 → 추가 작업 필요

---

## [추가 금지 — 자동 모드 강화]

- 어떤 파일도 수정 금지 (W-08 = 검증만)
- commit 금지
- 임시 파일 / scratch 파일 생성 금지
- 새 import 추가 금지 (단, 검증 스크립트 안의 import 는 OK)
- 환경변수 영구 설정 금지 (단, 검증 시점의 임시 환경변수는 OK)
- KIS API 실제 호출 금지 (test 값 사용)
- DB 영구 변경 금지
- R-08 영역 손대지 말 것
