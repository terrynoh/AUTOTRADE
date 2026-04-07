# 사전 조사 2/5 — 사용자 컨텍스트 grep

작성일: 2026-04-07
브랜치: feature/dashboard-fix-v1
git 상태: 미추적 파일만(??), 추적 파일 수정 없음 — 조사 진행

---

## §0. 사전 학습

CLAUDE.md 및 직전 보고서(recon_01_db.md) 재독 생략.
사전 확정 사실 적용: DB 스키마에 user/account/tenant 컬럼 없음.

---

## §2-1. 키워드 grep

검색 대상: `src/**/*.py`, `dashboard/**/*.py`, `launcher.py`
키워드: `user_id`, `account_id`, `tenant_id`, `owner_id`, `operator`, `capital_provider`

**hit 없음.**

---

## §2-2. AutoTrader 생성자 시그니처

```
src/main.py:53
def __init__(self):
```

인자 없음. `Settings()`, `StrategyParams.load()` 등을 내부에서 직접 생성.

---

## §2-3. Trader / TargetMonitor / KISAPI 생성자 시그니처

### Trader

```
src/core/trader.py:27
def __init__(self, api: KISAPI, settings: Settings, params: StrategyParams):
```

### TargetMonitor

클래스명: `TargetMonitor` (`Monitor`가 아님)

```
src/core/monitor.py:34
def __init__(self, target: TradeTarget, params: StrategyParams):
```

### KISAPI

클래스명: `KISAPI` (`KIS`, `KISClient`가 아님)

```
src/kis_api/kis.py:120
def __init__(
    self,
    app_key: str,
    app_secret: str,
    account_no: str,
    is_paper: bool = True,
    infra_params: object | None = None,
):
```

---

## §2-4. KIS 인스턴스 생성 지점

`= KISAPI(` 검색 결과:

| 파일 | 라인 | 맥락 |
|------|------|------|
| `src/main.py` | 56 | `AutoTrader.__init__` 내부 — 운영 인스턴스 |
| `run_backtest.py` | 95 | 백테스트 스크립트 — 독립 실행용 |
| `simulate_today.py` | 410 | 시뮬레이션 스크립트 — 독립 실행용 |
| `verify_phase1.py` | 43 | Phase 1 검증 스크립트 — 독립 실행용 |

**결론:** 운영 코드(`src/`) 내 KISAPI 인스턴스 생성 지점은 `src/main.py:56` 단 1개.
백테스트·검증 스크립트는 각각 독립적으로 자체 인스턴스를 생성하나, 이는 운영 루프 밖.
CLAUDE.md §5 "AutoTrader를 단일 owner로" 사실 확인 — `src/` 내 KISAPI 인스턴스는 AutoTrader 1개가 소유.

---

## §2-5. 매매 함수 시그니처

### buy_order

```
src/kis_api/kis.py:496
async def buy_order(
    self,
    code: str,
    qty: int,
    price: int = 0,
    price_type: str = ORDER_TYPE_LIMIT,
) -> dict:
```

### sell_order

```
src/kis_api/kis.py:530
async def sell_order(
    self,
    code: str,
    qty: int,
    price: int = 0,
    price_type: str = ORDER_TYPE_LIMIT,
) -> dict:
```

### get_balance

```
src/kis_api/kis.py:592
async def get_balance(self) -> dict:
```

**결론:** 세 메서드 모두 사용자 식별자(user_id, account_id 등) 인자 없음.
계좌번호(`CANO`, `ACNT_PRDT_CD`)는 `KISAPI.__init__`에서 `account_no`를 받아 인스턴스 변수로 고정.
메서드 호출 시 계좌 선택 불가 — 단일 계좌 고정 구조.

---

## §2-6. 결론

**(A) Single-user 가정.**

`src/` 전체에 `user_id`, `account_id`, `tenant_id`, `operator`, `capital_provider` 키워드 0건.
`AutoTrader.__init__(self)`는 인자 없이 Settings에서 KIS 키·계좌번호를 단일 로드.
`KISAPI`, `Trader`, `TargetMonitor` 모두 사용자 식별자를 인자로 받지 않음.
KISAPI는 `src/` 내에서 AutoTrader 1개만 생성. 계좌번호는 생성 시 고정.

---

## §6. 발견 사항

1. **`account_no`가 KISAPI 생성자에 고정**: `src/main.py:59`에서 `self.settings.account_no` 단일값 주입. Phase α-2 Account Executor 구조 도입 시 이 지점이 변경 대상.

2. **`get_balance(self)` 인자 없음**: 잔고 조회가 고정 계좌 기준. 계좌 추가 시 메서드 시그니처 변경 필요.

3. **`run_backtest.py`, `simulate_today.py`, `verify_phase1.py`**: 각각 독립적으로 KISAPI 인스턴스를 생성. 이 스크립트들은 `src/`와 무관하게 별도 KIS 키·계좌를 사용할 수 있으나, 현재는 동일한 `.env`에서 읽으므로 동일 계좌 사용.
