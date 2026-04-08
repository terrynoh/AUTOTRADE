# W-07b — dashboard/app.py 의 옛 자료구조 의존 제거

## 미션

`src/dashboard/app.py` 의 TargetMonitor / TradeTarget / `_monitors` 의존을 *Watcher / Coordinator 호환* 으로 정정한다.

작업 영역:
- import 정정 (TargetMonitor 제거)
- self.monitors 타입힌트 정정
- _build_state (또는 동등 함수) 안의 26개 필드 접근 매핑
- at._monitors 참조 정정 (at._coordinator.watchers)

자동 진행 모드. 멈출 조건은 [멈춤 조건] 참조.

크기 예상: dashboard/app.py 약 ±20행 변경

## 화이트리스트

이 파일만 수정 가능:
- `src/dashboard/app.py`

## 블랙리스트

절대 수정 금지:
- `src/main.py` (W-06b2 결과)
- `src/core/watcher.py` (W-06b2 결과)
- `src/core/trader.py` (W-05c 결과)
- `src/core/screener.py` (W-07a 결과)
- `src/core/monitor.py` (W-07d 영역)
- `src/core/stock_master.py` (W-02 결과)
- `src/utils/notifier.py` (W-04 결과)
- `src/models/stock.py` (W-07d 영역)
- `src/models/order.py`
- `src/storage/database.py`
- `src/dashboard/*` (단, app.py 만 OK)
- `src/kis_api/*`
- `config/*`
- `tests/*` (W-07c 영역)
- 그 외 모든 파일

## 배경 — 사실 base

### M-13 + M-13b 결과 (확정됨)

**dashboard/app.py 의 정정 대상 4곳:**

| 위치 | 현재 코드 | 문제 |
|---|---|---|
| line 25 | `from src.core.monitor import TargetMonitor` | monitor.py 폐기 시 ImportError |
| line 70 | `self.monitors: list[TargetMonitor] = []` | TargetMonitor 타입힌트 |
| line 143-168 | `for mon in self.monitors: ...` (26개 필드 접근) | TargetMonitor + TradeTarget 전면 의존 |
| line 401 | `state.monitors = list(at._monitors)` | at._monitors 가 W-06a 에서 폐기됨 → AttributeError |

**at._ 참조 패턴 (M-13b):**
- line 179: `at._manual_codes` — 정상 (W-06b2 후 보존)
- line 401: `at._monitors` — **폐기됨** (W-06a) → 정정 필요
- line 404, 405: `at._available_cash` — 정상 (W-06b2 후 보존)

### 매핑 표 (R-04 정정 누적 v11)

dashboard/app.py 의 _build_state (또는 동등) 안에서 다음 매핑 적용:

| 옛 (mon / target) | 새 (watcher) |
|---|---|
| `mon.state.value` | `watcher.state.value` |
| `mon.target.stock.code` | `watcher.code` |
| `mon.target.stock.name` | `watcher.name` |
| `mon.target.stock.market` | `watcher.market` |
| `mon.target.stock.market.value` | `watcher.market.value` |
| `mon.target.stock.current_price` | `watcher.current_price` |
| `mon.target.intraday_high` | `watcher.intraday_high` |
| `mon.target.total_buy_qty` | `watcher.total_buy_qty` |
| `mon.target.total_buy_amount` | `watcher.total_buy_amount` |
| `mon.target.avg_price` | `(watcher.total_buy_amount / watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0)` |
| `mon.target.buy1_filled` | `watcher.buy1_filled` |
| `mon.target.buy2_filled` | `watcher.buy2_filled` |
| `mon.target.buy1_placed` | `watcher.buy1_placed` |
| `mon.target.buy2_placed` | `watcher.buy2_placed` |
| `mon.target.exit_reason` | `watcher.exit_reason` |
| `mon.target.target_price` | `watcher.target_price` |
| `mon.target.buy1_price(params)` | `watcher.target_buy1_price` |
| `mon.target.buy2_price(params)` | `watcher.target_buy2_price` |
| `mon.target.hard_stop_price(params)` | `watcher.hard_stop_price_value` |
| `mon.target.post_entry_low` | `watcher.post_entry_low` |
| `t = mon.target` | (제거 — watcher 직접 사용) |

### State name 매핑 (옛 MonitorState → 새 WatcherState)

옛 MonitorState 와 새 WatcherState 가 *다른 이름* 가능:

| 옛 MonitorState (추정) | 새 WatcherState |
|---|---|
| WATCHING_NEW_HIGH | WATCHING (또는 TRIGGERED) |
| TRACKING_HIGH | WATCHING |
| HIGH_CONFIRMED | TRIGGERED |
| ENTERED | ENTERED |
| EXITED | EXITED |
| SKIPPED | SKIPPED |
| (없음) | READY |
| (없음) | PASSED |
| (없음) | DROPPED |

대시보드 응답에서 state.value 가 *프론트엔드와 결합* 되어 있을 가능성. 이름 변경 시 *프론트엔드 깨짐* 가능.

→ 결정: state.value 는 *그대로 watcher.state.value* 사용. 프론트엔드 호환성은 *별도 작업* (R-08 영역). W-07b 에서는 *코드 정정만*.

## 흐름

```
작업 1 (import 정정 — line 25)
  → 작업 2 (타입힌트 정정 — line 70)
  → 작업 3 (_build_state 본문 정정 — line 143-168)
  → 작업 4 (at._monitors 정정 — line 401)
  → 검증 1~6
  → 보고
```

중간에 멈추지 말 것.

---

## [작업 1] import 정정 (line 25)

### 1-1. TargetMonitor import 제거

기존:
```python
from src.core.monitor import TargetMonitor
```

→ *완전 제거*. 또는 *Watcher import 로 대체*:

```python
from src.core.watcher import Watcher
```

권장: Watcher import 추가 (타입힌트용).

### 1-2. 다른 import 보존

dashboard/app.py 의 다른 import (FastAPI / typing / 등) 그대로 보존.

만약 *MonitorState* import 도 있으면 → 함께 제거.

---

## [작업 2] 타입힌트 정정 (line 70)

### 2-1. self.monitors 타입힌트

기존:
```python
self.monitors: list[TargetMonitor] = []
```

변경:
```python
self.monitors: list[Watcher] = []
```

주의:
- 변수명 `monitors` 그대로 유지 (다른 영역과의 호환성)
- 타입만 Watcher 로

---

## [작업 3] _build_state 본문 정정 (line 143-168)

### 3-1. 본문 식별

```bash
sed -n '140,175p' src/dashboard/app.py
```

### 3-2. 매핑 적용

배경의 *매핑 표* 를 참조하여 모든 `mon.*` / `t.*` 패턴 정정:

기존 (예상 패턴):
```python
for mon in self.monitors:
    t = mon.target
    state_dict["monitors"].append({
        "code": t.stock.code,
        "name": t.stock.name,
        "market": t.stock.market.value,
        "state": mon.state.value,
        "intraday_high": t.intraday_high,
        "current_price": t.stock.current_price,
        "total_buy_qty": t.total_buy_qty,
        "avg_price": t.avg_price,
        "buy1_filled": t.buy1_filled,
        "buy2_filled": t.buy2_filled,
        "buy1_price": t.buy1_price(self.params),
        "buy2_price": t.buy2_price(self.params),
        "hard_stop_price": t.hard_stop_price(self.params),
        "target_price": t.target_price,
        "exit_reason": t.exit_reason,
        ...
    })
```

변경 (매핑 표 적용):
```python
for watcher in self.monitors:
    state_dict["monitors"].append({
        "code": watcher.code,
        "name": watcher.name,
        "market": watcher.market.value,
        "state": watcher.state.value,
        "intraday_high": watcher.intraday_high,
        "current_price": watcher.current_price,
        "total_buy_qty": watcher.total_buy_qty,
        "avg_price": (watcher.total_buy_amount / watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0),
        "buy1_filled": watcher.buy1_filled,
        "buy2_filled": watcher.buy2_filled,
        "buy1_price": watcher.target_buy1_price,
        "buy2_price": watcher.target_buy2_price,
        "hard_stop_price": watcher.hard_stop_price_value,
        "target_price": watcher.target_price,
        "exit_reason": watcher.exit_reason,
        ...
    })
```

주의:
- *변수명* `mon` → `watcher` 변경
- `t = mon.target` 라인 *완전 제거*
- 응답 dict 의 *키 이름* 그대로 보존 (프론트엔드 호환성)
- *값 매핑* 만 변경
- buy1_price / buy2_price / hard_stop_price 의 *메서드 호출* (`(self.params)`) 패턴 → *필드 접근* 으로
- avg_price 는 *계산식* 으로 변경 (Watcher 는 avg_price property 없음)

### 3-3. 정확한 패턴 식별

명령서가 *예상 패턴* 만 명시함. 실제 line 143-168 의 *정확한 코드* 는 Code 가 식별:

```bash
sed -n '140,175p' src/dashboard/app.py
```

식별 후 매핑 표대로 정정. 만약 *예상 외 필드* 가 있으면:
- 매핑 표에 있는 패턴 → 그대로 적용
- 매핑 표에 없는 패턴 → 멈춤 + 보고 (예: TradeTarget 의 다른 메서드 / 필드)

---

## [작업 4] at._monitors 정정 (line 401)

### 4-1. 본문 확인

```bash
sed -n '395,410p' src/dashboard/app.py
```

### 4-2. 정정

기존:
```python
state.monitors = list(at._monitors)
```

변경:
```python
state.monitors = list(at._coordinator.watchers)
```

주의:
- `list(...)` 패턴 그대로
- `at._coordinator.watchers` 사용 (W-05b 에서 정의된 필드)
- 만약 line 401 주변에 *다른 at._monitors 참조* 가 있으면 함께 정정
- 만약 *at._active_monitor* 참조가 있으면 → `at._coordinator.active` 또는 멈춤 + 보고

---

## [검증]

### 검증 1 — dashboard import OK

```bash
python -c "from src.dashboard import app; print('dashboard import OK')"
```

기대: `dashboard import OK`

### 검증 2 — TargetMonitor 잔존 0건

```bash
python -c "
with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()
count = content.count('TargetMonitor')
print('TargetMonitor count:', count)
assert count == 0, 'TargetMonitor 잔존'
"
```

기대: `TargetMonitor count: 0`

### 검증 3 — TradeTarget 잔존 0건

```bash
python -c "
with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()
count = content.count('TradeTarget')
print('TradeTarget count:', count)
assert count == 0, 'TradeTarget 잔존'
"
```

기대: `TradeTarget count: 0`

### 검증 4 — at._monitors 잔존 0건

```bash
python -c "
with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()
count = content.count('at._monitors')
print('at._monitors count:', count)
assert count == 0, 'at._monitors 잔존'
"
```

기대: `at._monitors count: 0`

### 검증 5 — mon.target 잔존 0건

```bash
python -c "
with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()
count = content.count('mon.target')
print('mon.target count:', count)
assert count == 0, 'mon.target 잔존'
"
```

기대: `mon.target count: 0`

### 검증 6 — main + watcher + dashboard 회귀 import

```bash
python -c "
from src.main import AutoTrader
from src.core.watcher import Watcher, WatcherCoordinator
from src.dashboard import app
print('all imports OK')
"
```

기대: `all imports OK`

---

## [검증 실패 시 — 멈춤 조건]

- 검증 1 ~ 6 어느 하나 실패 → 멈춤 + 보고

멈춤 시 변경 *되돌리지 말 것*. 그대로 두고 보고.

---

## [모호한 케이스 — 사전 결정]

### 케이스 J-1: _build_state 함수의 정확한 이름
M-13b 결과는 *line 143-168 영역* 만 식별. 함수명은 모름. Code 가 grep 으로 식별:
```bash
grep -n "^    def \|^    async def " src/dashboard/app.py | head -30
```

### 케이스 J-2: 응답 dict 의 키 이름 보존
프론트엔드가 어떤 키를 사용하는지 모름. *전부 보존*. 키 이름 변경 X. 값 매핑만.

### 케이스 J-3: target_price 가 property 인지 메서드인지
Watcher 의 target_price 는 *property* (W-05a 결과). dashboard 가 `t.target_price` 로 *property 호출 패턴* 또는 `t.target_price()` 로 *메서드 호출 패턴* 일 수 있음. property 패턴 그대로 사용.

### 케이스 J-4: avg_price 의 계산
Watcher 에 avg_price 필드 *없음*. 직접 계산:
```python
(watcher.total_buy_amount / watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0)
```

또는 *별도 헬퍼 변수*:
```python
avg = watcher.total_buy_amount / watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0
state_dict["monitors"].append({
    ...
    "avg_price": avg,
    ...
})
```

권장: 인라인 계산식.

### 케이스 J-5: hard_stop_price 의 필드명
Watcher 의 필드명: `hard_stop_price_value` (W-05a 결과). 메서드 `hard_stop_price` 가 *없음*. dashboard 의 dict 키는 `hard_stop_price` 그대로 보존, 값만 `watcher.hard_stop_price_value`.

### 케이스 J-6: state.value 의 매핑
옛 MonitorState 와 새 WatcherState 의 *이름이 다를 수* 있음:
- 옛 HIGH_CONFIRMED → 새 TRIGGERED
- 옛 TRACKING_HIGH → 새 WATCHING
- 새 READY / PASSED / DROPPED 는 *옛에 없는 state*

→ 프론트엔드 호환성은 *R-08 영역*. W-07b 에서는 *그대로 watcher.state.value* 사용. 프론트엔드가 새 state name 으로 *알 수 없는 값* 받을 수 있음 — 그건 *별도 정정*.

### 케이스 J-7: line 143-168 외 다른 mon / target 사용
함수 본문 외에 *다른 영역* 에서 mon / target 사용 가능. 검증 5 (mon.target 잔존 0건) 가 *전체 파일* 검사. 0건 통과해야 함.

### 케이스 J-8: __init__ 의 self.monitors 초기값
기존: `self.monitors: list[TargetMonitor] = []`
변경: `self.monitors: list[Watcher] = []`
초기값 `[]` 그대로.

### 케이스 J-9: TradeTarget 의 buy1_price(params) 메서드 호출 패턴
- 옛: `t.buy1_price(self.params)` — 메서드 호출, params 인자 전달
- 새: `watcher.target_buy1_price` — 필드 접근, params 인자 X
- self.params 참조 *제거*

### 케이스 J-10: dashboard 의 self.params 참조
dashboard 가 self.params 를 *다른 영역에서 사용* 가능. 그건 *그대로 보존*. 작업 3 의 매핑에서만 *제거*.

---

## [자체 발견 처리 규칙]

Code 가 작업 중 다음을 발견하면 → 수정하지 말 것 → 보고의 [발견] 섹션에 1~3줄 기록:
- dashboard/app.py 의 잠재적 버그
- 매핑 표에 없는 TradeTarget 필드 접근 패턴
- _active_monitor 참조 (예상 외)
- 다른 at._ 참조 (예상 외)
- WatcherState 와 MonitorState 의 이름 충돌 (프론트엔드 호환성 우려)
- 더 깔끔한 패턴

---

## [보고]

### A. 변경 라인
- 작업 1 (import): 라인 번호 + 변경 내용
- 작업 2 (타입힌트): 라인 번호
- 작업 3 (_build_state 본문): 라인 범위 + 매핑 적용 개수
- 작업 4 (at._monitors): 라인 번호

### B. 라인 수
- dashboard/app.py: before / after / 차이

### C. 검증 결과
- 검증 1 ~ 6: 각각 성공/실패 + 출력

### D. 다른 파일 수정 여부
*반드시 dashboard/app.py 만*

### E. 모호한 케이스 처리 (J-1 ~ J-10 중 발생한 것)

### F. [발견] 섹션
특히 다음을 명시:
- _build_state 함수의 실제 이름
- 매핑 표에 없는 필드 (있으면)
- WatcherState 와 MonitorState 이름 충돌 영역

### G. W-07c 진입 준비
- dashboard/app.py 정정 완료
- W-07c 작업 영역: tests/ 3파일 폐기 (git rm)

---

## [추가 금지 — 자동 모드 강화]

- dashboard/app.py 외 어떤 파일도 수정 금지
- main.py / watcher.py / trader.py / screener.py 변경 금지
- 새 함수 / 클래스 추가 금지
- 응답 dict 의 키 이름 변경 금지 (프론트엔드 호환성)
- 새 import 금지 (단, Watcher import 추가는 명령서 명시)
- 로깅 메시지 변경 금지
- 영어 메시지 추가 금지
- TradeTarget 참조 추가 금지
- TargetMonitor 참조 추가 금지
- monitor.py 폐기 금지 (W-07d 영역)
- TradeTarget 클래스 폐기 금지 (W-07d 영역)
- commit 금지
- 임시 파일 / scratch 파일 생성 금지
- W-07c / W-07d 영역 손대지 말 것
