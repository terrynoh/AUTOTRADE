# W-09 — ISSUE-035 dashboard 정정 (set-targets + run-manual-screening + _monitors 잔존)

## 미션

대시보드의 *메인 UX (종목 입력 + 수동 스크리닝)* 를 정상화. ISSUE-035 (asyncio Timeout context 사고) 를 *근본 해결* + R-07 미반영 잔존 1줄 정정.

작업 영역:
- `src/dashboard/app.py`: set-targets (StockMaster 사용) + run-manual-screening (run_coroutine_threadsafe 위임) + _monitors → _coordinator.watchers
- `src/main.py`: AutoTrader 에 _loop 필드 추가 + run() 안에서 설정

자동 진행 모드. 멈춤 조건은 [멈춤 조건] 참조.

크기 예상: dashboard/app.py 약 ±50행, main.py 약 +5행

## 화이트리스트

이 두 파일만 수정 가능:
- `src/dashboard/app.py`
- `src/main.py`

## 블랙리스트

절대 수정 금지:
- `src/core/watcher.py` (R-07 종결 결과)
- `src/core/trader.py` (W-05c 결과)
- `src/core/screener.py` (W-07a 결과)
- `src/core/stock_master.py` (W-02 결과)
- `src/utils/notifier.py` (W-04 결과)
- `src/models/stock.py` (W-07d 결과)
- `src/models/order.py`
- `src/storage/database.py`
- `src/kis_api/*`
- `config/*`
- `tests/*`
- `scripts/*`
- 그 외 모든 파일

## 배경 — 사실 base

### M-16 + M-16 보완 결과 (확정됨)

**ISSUE-035 영향받는 경로 — 2건**:

| 경로 | 사고 라인 | 영향 |
|---|---|---|
| POST /api/set-targets | L262 `await api.get_current_price(code)` | 종목 입력 100% 실패 |
| POST /api/run-manual-screening | L333 `await state.autotrader._on_screening()` | 수동 스크리닝 100% 실패 |

**사고 메커니즘**:
```
uvicorn 이 FastAPI 핸들러를 *별도 Task* 에서 실행
→ 그 Task 안에서 await api.get_current_price() 또는 await _on_screening()
→ AutoTrader 의 aiohttp ClientSession 이 *AutoTrader 의 loop* 에서 생성됨
→ aiohttp TimerContext 가 asyncio.current_task() 검사
→ uvicorn task 와 AutoTrader task 가 *다른 컨텍스트* → current_task() == None
→ RuntimeError: "Timeout context manager should be used inside a task"
```

**StockMaster 메서드 (W-02 결과)**:
- `lookup_name(code)` — 코드 → 종목명 (sync, 딕셔너리 조회, KIS 호출 X)
- `lookup_code(name_or_code)` — 종목명 → 코드 (sync, 딕셔너리 조회, KIS 호출 X)

**R-07 미반영 잔존 (W-07b 빈틈)**:
- dashboard/app.py:334: `count = len(state.autotrader._monitors)` — `_monitors` 가 W-06a 에서 폐기됨

→ R-07 배포 후 이 라인 호출 시 AttributeError. W-07b 검증이 `at._monitors` 패턴만 검사해서 `state.autotrader._monitors` 패턴이 잔존.

## 흐름

```
작업 1 (main.py — _loop 필드 추가)
  → 작업 2 (main.py — run() 안에서 _loop 설정)
  → 작업 3 (dashboard/app.py — set-targets 정정 옵션 C)
  → 작업 4 (dashboard/app.py — run-manual-screening 정정 옵션 1)
  → 작업 5 (dashboard/app.py — _monitors 잔존 정정)
  → 검증 1~8
  → 보고
```

중간에 멈추지 말 것.

---

## [작업 1] main.py — AutoTrader 에 _loop 필드 추가

### 1-1. AutoTrader.__init__ 안에 필드 추가

기존 (M-12 결과 + W-06b1 후, line 80~90 근방):
```python
        self._available_cash: int = 0
        ...
        self._network_ok: bool = True
        self._emergency_cancel_done: bool = False
        self.on_state_update = None
```

변경 (한 줄 추가):
```python
        self._available_cash: int = 0
        ...
        self._network_ok: bool = True
        self._emergency_cancel_done: bool = False
        self.on_state_update = None
        self._loop = None  # run() 시작 시 설정 (dashboard 가 사용)
```

위치: 자연스러운 곳. 다른 필드 옆.

---

## [작업 2] main.py — run() 안에서 _loop 설정

### 2-1. run() 의 시작 부분에 추가

기존 (M-12 결과 line 144-152):
```python
    async def run(self):
        """전체 매매 프로세스 실행."""
        logger.info("=" * 50)
        logger.info(f"AUTOTRADE 시작 (모드: {self.settings.trade_mode})")
        mt = self.params.multi_trade
        if mt.enabled:
            logger.info(f"멀티 트레이드: 최대 {mt.max_daily_trades}회, {mt.repeat_start}~{mt.repeat_end}")
        logger.info("=" * 50)
        self.notifier.notify_system(f"AUTOTRADE 시작 (모드: {self.settings.trade_mode})")
```

변경 (첫 줄에 _loop 설정 추가):
```python
    async def run(self):
        """전체 매매 프로세스 실행."""
        # 현재 loop 보관 (dashboard 가 run_coroutine_threadsafe 로 사용)
        self._loop = asyncio.get_running_loop()
        
        logger.info("=" * 50)
        logger.info(f"AUTOTRADE 시작 (모드: {self.settings.trade_mode})")
        mt = self.params.multi_trade
        if mt.enabled:
            logger.info(f"멀티 트레이드: 최대 {mt.max_daily_trades}회, {mt.repeat_start}~{mt.repeat_end}")
        logger.info("=" * 50)
        self.notifier.notify_system(f"AUTOTRADE 시작 (모드: {self.settings.trade_mode})")
```

주의:
- `asyncio.get_running_loop()` 사용 (Python 3.7+)
- run() 의 *가장 첫 줄* (logger 호출 전)
- import 영역에 `import asyncio` 가 이미 있는지 확인. 없으면 추가.

---

## [작업 3] dashboard/app.py — set-targets 정정 (옵션 C — StockMaster 사용)

### 3-1. 기존 본문 식별

```bash
sed -n '230,300p' src/dashboard/app.py
```

기존 패턴 (M-16 결과):
```python
@app.post("/api/set-targets")
async def api_set_targets(...):
    ...
    api = state.autotrader.api  # L250
    ...
    for code in codes:
        info = await api.get_current_price(code)  # L262 — 사고
        if info is None:
            ...
        ...
    state.autotrader.set_manual_codes(final_codes)  # L284
    return ...
```

### 3-2. 정정 — StockMaster 사용

새 패턴:
```python
@app.post("/api/set-targets")
async def api_set_targets(...):
    ...
    # StockMaster 로컬 캐시로 종목 검증 (KIS API 호출 X)
    sm = state.autotrader._stock_master
    final_codes = []
    invalid_inputs = []
    
    for raw in codes:
        raw = raw.strip()
        if not raw:
            continue
        
        if raw.isdigit():
            # 종목코드 입력
            name = sm.lookup_name(raw)
            if name is None:
                invalid_inputs.append(raw)
                continue
            final_codes.append(raw)
        else:
            # 종목명 입력 → 코드 변환
            resolved = sm.lookup_code(raw)
            if resolved is None:
                invalid_inputs.append(raw)
                continue
            final_codes.append(resolved)
    
    state.autotrader.set_manual_codes(final_codes)
    
    return {
        "ok": True,
        "codes": final_codes,
        "invalid": invalid_inputs,
    }
```

주의:
- `state.autotrader.api` 사용 *완전 제거*
- `await api.get_current_price(code)` 호출 *완전 제거*
- StockMaster 의 sync 메서드만 사용 (KIS API 호출 0)
- 종목 검증만 — 현재가 표시 X
- 응답에 `invalid` 필드 추가 (검증 실패 종목)
- `set_manual_codes` 는 sync 함수, 그대로 사용

### 3-3. 정확한 라인 식별

명령서가 *예상 패턴* 만 명시. 실제 본문이 다를 수 있음. Code 가 식별:

```bash
sed -n '230,300p' src/dashboard/app.py
```

식별 후:
- `api = state.autotrader.api` 라인 → 제거
- `await api.get_current_price` 라인 → 제거 + StockMaster 패턴으로 대체
- 그 외 검증 / 응답 로직은 *그대로 보존*

만약 *예상 외 패턴* 발견 → 멈춤 + 보고.

---

## [작업 4] dashboard/app.py — run-manual-screening 정정 (run_coroutine_threadsafe)

### 4-1. 기존 본문 식별

```bash
sed -n '320,345p' src/dashboard/app.py
```

기존 패턴 (M-16 결과):
```python
@app.post("/api/run-manual-screening")
async def api_run_manual_screening(...):
    ...
    await state.autotrader._on_screening()  # L333 — 사고
    count = len(state.autotrader._monitors)  # L334 — R-07 미반영
    return {"count": count, ...}
```

### 4-2. 정정 — run_coroutine_threadsafe 위임

새 패턴:
```python
@app.post("/api/run-manual-screening")
async def api_run_manual_screening(...):
    ...
    autotrader = state.autotrader
    
    # AutoTrader 의 loop 가 살아있는지 확인
    if autotrader._loop is None or not autotrader._loop.is_running():
        return JSONResponse(
            {"ok": False, "error": "AutoTrader loop 가 가동 중이 아님"},
            status_code=503
        )
    
    # AutoTrader loop 에 위임 (KIS API 의 aiohttp 컨텍스트 정상)
    future = asyncio.run_coroutine_threadsafe(
        autotrader._on_screening(),
        autotrader._loop
    )
    
    # 결과 대기 (max 60초)
    try:
        await asyncio.wrap_future(future)
    except asyncio.TimeoutError:
        return JSONResponse(
            {"ok": False, "error": "수동 스크리닝 timeout (60초)"},
            status_code=504
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"수동 스크리닝 실패: {e}"},
            status_code=500
        )
    
    # R-07: _monitors → _coordinator.watchers
    count = len(autotrader._coordinator.watchers)
    return {"ok": True, "count": count}
```

주의:
- `await state.autotrader._on_screening()` *직접 호출 제거*
- `asyncio.run_coroutine_threadsafe` 로 *AutoTrader loop 에 위임*
- `asyncio.wrap_future` 로 *uvicorn task 에서 결과 대기*
- `_monitors` → `_coordinator.watchers` *작업 5 에서 함께 처리되지만 여기서도 적용*
- 에러 처리 추가 (timeout, exception)
- 응답 형식 보존 (ok / count / error)

### 4-3. import 추가

dashboard/app.py 의 import 영역에 *없으면* 추가:
```python
import asyncio
```

또는 이미 있으면 보존.

`JSONResponse` 도 import 필요할 수 있음:
```python
from fastapi.responses import JSONResponse
```

이미 있으면 보존.

---

## [작업 5] dashboard/app.py — _monitors 잔존 정정

### 5-1. _monitors 사용 위치 전수 식별

```bash
grep -n "_monitors\|state\.autotrader\._" src/dashboard/app.py
```

기대: 작업 4 의 line 334 외에 *다른 _monitors 사용* 도 식별.

### 5-2. 정정

각 사용 위치에서:
- `state.autotrader._monitors` → `state.autotrader._coordinator.watchers`
- `autotrader._monitors` → `autotrader._coordinator.watchers`
- `at._monitors` → `at._coordinator.watchers`

작업 4 의 정정에 *이미 포함됨*. 추가로 다른 위치에 있으면 함께 정정.

### 5-3. 다른 R-07 미반영 잔존 검색

```bash
grep -n "_active_monitor\|_candidate_pool\|_candidate_index\|_completed_codes" src/dashboard/app.py
```

기대: 0건 (W-06a/b 에서 모두 폐기)

만약 잔존 발견 → 정정 또는 멈춤 + 보고

---

## [검증]

### 검증 1 — main.py + dashboard import OK

```bash
ssh ubuntu@134.185.115.229 "cd /home/ubuntu/AUTOTRADE && source venv/bin/activate && python -c '
from src.main import AutoTrader
from src.dashboard import app
print(\"all imports OK\")
'"
```

또는 로컬 (Windows) 검증:
```bash
python -c "
from src.main import AutoTrader
from src.dashboard import app
print('all imports OK')
"
```

기대: `all imports OK`

### 검증 2 — AutoTrader._loop 필드 존재

```bash
python -c "
from src.main import AutoTrader
at = AutoTrader()
print('_loop initial:', at._loop)
assert at._loop is None, '_loop 초기값 None 아님'
print('_loop 필드 OK')
"
```

기대:
```
_loop initial: None
_loop 필드 OK
```

### 검증 3 — set-targets 의 KIS API 호출 제거 확인

```bash
python -c "
with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# api_set_targets 함수 본문 식별
import re
match = re.search(r'async def api_set_targets.*?(?=\n@app|\nasync def |\ndef |\Z)', content, re.DOTALL)
if match:
    body = match.group(0)
    has_get_current_price = 'get_current_price' in body
    has_state_api = 'state.autotrader.api' in body
    has_stock_master = 'stock_master' in body or '_stock_master' in body
    print('set-targets get_current_price:', has_get_current_price)
    print('set-targets state.autotrader.api:', has_state_api)
    print('set-targets StockMaster 사용:', has_stock_master)
    assert not has_get_current_price, 'get_current_price 잔존'
    assert not has_state_api, 'state.autotrader.api 잔존'
    assert has_stock_master, 'StockMaster 미사용'
print('검증 3 OK')
"
```

기대:
```
set-targets get_current_price: False
set-targets state.autotrader.api: False
set-targets StockMaster 사용: True
검증 3 OK
```

### 검증 4 — run-manual-screening 의 run_coroutine_threadsafe 적용

```bash
python -c "
with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re
match = re.search(r'async def api_run_manual_screening.*?(?=\n@app|\nasync def |\ndef |\Z)', content, re.DOTALL)
if match:
    body = match.group(0)
    has_threadsafe = 'run_coroutine_threadsafe' in body
    has_direct_await = 'await state.autotrader._on_screening' in body
    has_coordinator_watchers = '_coordinator.watchers' in body
    print('run-manual-screening run_coroutine_threadsafe:', has_threadsafe)
    print('run-manual-screening 직접 await (사고 패턴):', has_direct_await)
    print('run-manual-screening _coordinator.watchers:', has_coordinator_watchers)
    assert has_threadsafe, 'run_coroutine_threadsafe 미적용'
    assert not has_direct_await, '직접 await 잔존 (사고 패턴)'
    assert has_coordinator_watchers, '_coordinator.watchers 미적용'
print('검증 4 OK')
"
```

기대:
```
run-manual-screening run_coroutine_threadsafe: True
run-manual-screening 직접 await (사고 패턴): False
run-manual-screening _coordinator.watchers: True
검증 4 OK
```

### 검증 5 — _monitors 잔존 0건 (전체 dashboard/app.py)

```bash
python -c "
with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# state.autotrader._monitors 또는 autotrader._monitors 또는 at._monitors
patterns = ['state.autotrader._monitors', 'autotrader._monitors', '._monitors']
for p in patterns:
    count = content.count(p)
    print(f'{p}: {count}건')

# _monitors 단어 자체 (단, _monitors_ 같은 것은 제외)
import re
matches = re.findall(r'\\._monitors(?!_)', content)
print('_monitors (정확한 패턴):', len(matches))
assert len(matches) == 0, '_monitors 잔존'
print('검증 5 OK')
"
```

기대:
```
state.autotrader._monitors: 0건
autotrader._monitors: 0건
._monitors: 0건
_monitors (정확한 패턴): 0
검증 5 OK
```

### 검증 6 — 다른 R-07 미반영 잔존 검색

```bash
python -c "
with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 옛 자료구조 패턴
patterns = [
    '_active_monitor',
    '_candidate_pool',
    '_candidate_index',
    '_completed_codes',
    'TargetMonitor',
    'TradeTarget',
    'MonitorState',
]
issues = []
for p in patterns:
    if p in content:
        issues.append(p)

if issues:
    print('잔존 패턴:', issues)
    assert False, '옛 자료구조 잔존'
else:
    print('옛 자료구조 잔존 0건')
"
```

기대: `옛 자료구조 잔존 0건`

### 검증 7 — main.py + dashboard import (회귀)

```bash
python -c "
from src.main import AutoTrader
from src.core.watcher import Watcher, WatcherCoordinator
from src.dashboard import app
import inspect

# main.py 의 run() 안에서 _loop 설정 확인
src = inspect.getsource(AutoTrader.run)
has_loop_set = 'self._loop = asyncio.get_running_loop()' in src
print('run() 안의 _loop 설정:', has_loop_set)
assert has_loop_set, 'run() 안의 _loop 설정 누락'
print('검증 7 OK')
"
```

기대: `검증 7 OK`

### 검증 8 — AutoTrader 인스턴스 + 기본 동작

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('KIS_ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('AutoTrader 인스턴스 OK')
print('_loop (init):', at._loop)
print('_stock_master:', type(at._stock_master).__name__)
print('_coordinator:', type(at._coordinator).__name__)
print('_coordinator.watchers:', at._coordinator.watchers)
print('검증 8 OK')
"
```

기대: 모든 항목 정상

---

## [검증 실패 시 — 멈춤 조건]

- 검증 1~8 어느 하나 실패 → 멈춤 + 보고
- AssertionError 발생 → 멈춤 + 보고
- ImportError 발생 → 멈춤 + 보고

멈춤 시 변경 *되돌리지 말 것*. 그대로 두고 보고.

---

## [모호한 케이스 — 사전 결정]

### 케이스 Q-1: api_set_targets 의 정확한 시그니처
M-16 결과: 약 65행 (L230-295). 본문 안에 *codes 인자 받는 패턴* 가정.

만약 Pydantic model 사용:
```python
class TargetsRequest(BaseModel):
    codes: list[str]

@app.post("/api/set-targets")
async def api_set_targets(req: TargetsRequest):
    codes = req.codes
    ...
```

→ 그 패턴 그대로 보존. codes 추출 영역만 정정.

### 케이스 Q-2: api_run_manual_screening 의 정확한 시그니처
M-16 결과: 약 22행 (L320-342). 인자 없거나 단순.

기존 그대로 보존. 본문만 정정.

### 케이스 Q-3: StockMaster 의 lookup 메서드 동작
M-16 결과:
- `lookup_name(code)` → 종목명 또는 None
- `lookup_code(name_or_code)` → 종목코드 또는 None

→ 작업 3 의 정정 패턴이 정확.

만약 *다른 시그니처* (예: 예외 발생) 면 멈춤 + 보고.

### 케이스 Q-4: dashboard 의 인증 / 토큰 검증
api_set_targets 에 *admin token 검증* 영역이 있을 수 있음. 그건 *그대로 보존*. 작업 3 은 *KIS API 호출 영역만* 정정.

### 케이스 Q-5: WebSocket 핸들러
dashboard/app.py 에 *WebSocket 핸들러* 가 있을 수 있음. 그건 *손대지 말 것*. 작업 영역은 *2 endpoint 만*.

### 케이스 Q-6: asyncio import 위치
dashboard/app.py 의 상단 import 영역에 `import asyncio` 가 있는지 확인. 없으면 추가.

### 케이스 Q-7: JSONResponse import
`from fastapi.responses import JSONResponse` 가 있는지 확인. 없으면 추가.

### 케이스 Q-8: AutoTrader._stock_master 필드
W-06b1 에서 `self._stock_master = StockMaster(...)` 추가됨. dashboard 는 `state.autotrader._stock_master` 로 접근.

### 케이스 Q-9: run() 의 _loop 설정 위치
명령서: 첫 줄. 다만 *logger 호출 후* 도 OK. 핵심은 *await 호출 전*. 권장: 첫 줄.

### 케이스 Q-10: dashboard 의 다른 핸들러
api_set_targets / api_run_manual_screening 외 다른 핸들러는 *손대지 말 것*. 단 *_monitors 잔존* 만 정정.

---

## [자체 발견 처리 규칙]

Code 가 작업 중 다음을 발견하면 → 보고의 [발견] 섹션에 기록:
- dashboard/app.py 의 다른 잠재 사고
- 매핑 표에 없는 영역
- 새 R-07 미반영 잔존
- 더 깔끔한 패턴

---

## [보고]

### A. 변경 라인 (main.py)
- 작업 1 (_loop 필드): 라인 번호 + 추가 라인
- 작업 2 (_loop 설정): 라인 번호 + 추가 라인

### B. 변경 라인 (dashboard/app.py)
- 작업 3 (set-targets): 라인 범위 + 변경 패턴
- 작업 4 (run-manual-screening): 라인 범위 + 변경 패턴
- 작업 5 (_monitors 잔존): 라인 번호

### C. 라인 수
- main.py: before / after / 차이
- dashboard/app.py: before / after / 차이

### D. 검증 결과
- 검증 1 ~ 8: 각각 성공/실패 + 출력

### E. 다른 파일 수정 여부
*반드시 main.py + dashboard/app.py 두 파일만*

### F. 모호한 케이스 처리 (Q-1 ~ Q-10 중 발생한 것)

### G. [발견] 섹션

### H. 다음 단계 — 운영 환경 재배포 준비
- W-09 정정 완료
- 운영 환경 재배포 (scp + 추출)
- 운영 환경 재검증
- service 수동 시작
- 대시보드 종목 입력 시도

---

## [추가 금지 — 자동 모드 강화]

- 화이트리스트 외 어떤 파일도 수정 금지
- src/core/* 변경 금지
- src/utils/* 변경 금지
- src/models/* 변경 금지
- src/kis_api/* 변경 금지
- config/ 변경 금지
- tests/ 변경 금지
- scripts/ 변경 금지
- 새 파일 생성 금지
- 새 함수 / 클래스 추가 금지 (단, 작업 3/4 의 본문 정정은 OK)
- WebSocket 핸들러 변경 금지
- 다른 endpoint 변경 금지
- commit 금지
- 임시 파일 / scratch 파일 생성 금지
- 운영 환경 직접 변경 금지 (W-09 는 로컬 코드만)

---

## [다음 단계 — W-09 통과 후 운영 환경 재배포]

W-09 검증 통과 후 *수동 작업* (수석님 또는 Code):

```bash
# 1. 운영 환경 service 종료 확인 (이미 inactive)
ssh ubuntu@134.185.115.229 "sudo systemctl is-active autotrade.service"
# 기대: inactive

# 2. dashboard/app.py + main.py 운영 환경에 복사
cd <AUTOTRADE 로컬 경로>
scp src/dashboard/app.py ubuntu@134.185.115.229:/home/ubuntu/AUTOTRADE/src/dashboard/
scp src/main.py ubuntu@134.185.115.229:/home/ubuntu/AUTOTRADE/src/

# 3. 운영 환경 검증
ssh ubuntu@134.185.115.229 "
cd /home/ubuntu/AUTOTRADE
source venv/bin/activate
python -c '
from src.main import AutoTrader
from src.dashboard import app
at = AutoTrader()
print(\"_loop:\", at._loop)
print(\"_stock_master:\", type(at._stock_master).__name__)
print(\"_coordinator.watchers:\", at._coordinator.watchers)
print(\"운영 환경 검증 OK\")
'
"

# 4. service 수동 시작
ssh ubuntu@134.185.115.229 "sudo systemctl start autotrade.service"

# 5. service 상태 확인
ssh ubuntu@134.185.115.229 "sudo systemctl is-active autotrade.service"
# 기대: active

# 6. 텔레그램 URL 받기 (자동 발송)

# 7. 대시보드 접속 → 종목 입력 시도

# 8. 09:50 매매 시작 대기
```
