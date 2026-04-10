# Gate G0 — Reference Spec (for verification only)

> 이 문서는 Gate G0 선행 확인을 위한 **참조용 명세**다.
> 기존 코드를 이 명세에 맞춰 수정하는 용도가 아니다.
> 코드가 명세와 다르면 **코드가 진실**이며, 이 문서를 갱신한다.

## §2. 인터페이스 명세 (참조)

### core/types.py
```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(str, Enum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"

@dataclass
class Quote:
    ticker: str
    price: int
    ts: datetime

@dataclass
class Fill:
    client_order_id: str
    ticker: str
    side: Side
    qty: int
    price: int
    fee: int = 0
    tax: int = 0
    ts: datetime = field(default_factory=datetime.now)

@dataclass
class Position:
    ticker: str
    qty: int
    avg_price: int
```

### data/kis_client.py
```python
class KISClient:
    def __init__(self, app_key: str, app_secret: str, account_no: str, base_url: str): ...
    def get_token(self) -> str: ...
    def get_price(self, ticker: str) -> Quote: ...
```
- 주문 메서드 없음 (read-only)
- 토큰 만료시각 캐싱 + 자동 갱신

### execution/virtual_ledger.py
```python
class VirtualLedger:
    def __init__(self, path: str, initial_cash: int): ...
    def cash(self) -> int: ...
    def positions(self) -> dict[str, Position]: ...
    def fills(self) -> list[Fill]: ...
    def apply_buy(self, ticker: str, qty: int, price: int, client_order_id: str) -> Fill: ...
    def apply_sell(self, ticker: str, qty: int, price: int, client_order_id: str) -> Fill: ...
```
규칙:
- `client_order_id` 중복 시 → 기존 Fill 반환, 잔고/포지션 변경 없음 (멱등성)
- 매수 시 cash 부족 → `ValueError("INSUFFICIENT_CASH")`
- 매도 시 보유수량 부족 → `ValueError("INSUFFICIENT_POSITION")`
- 평단 갱신: `(old_qty*old_avg + new_qty*new_price) // (old_qty+new_qty)`
- 전량 매도 시 positions dict에서 제거
- 영속화: JSON 파일, atomic write (`tmp + os.replace`)

### execution/order_manager.py
```python
class OrderManager:
    def __init__(self, kis: KISClient, ledger: VirtualLedger, dry_run: bool):
        if not dry_run:
            raise RuntimeError("G0: live mode is forbidden")
    def market_buy(self, ticker: str, qty: int, client_order_id: str) -> Fill: ...
    def market_sell(self, ticker: str, qty: int, client_order_id: str) -> Fill: ...
```
- `kis.get_price()`로 실시세 조회 → `ledger.apply_*()`로 가상 체결
- `dry_run=False`면 생성자에서 즉시 RuntimeError

## §3. Smoke E2E 11단계 시나리오 (참조)

`scripts/smoke_e2e.py`가 다음 순서로 무에러 통과해야 G0 합격:

1. Settings 로딩 → `DRY_RUN=True` 확인 (아니면 SystemExit)
2. KISClient 토큰 발급 → 만료시각 로깅
3. `kis.get_price("005930")` → `price > 0` 검증
4. VirtualLedger 초기화 (`initial_cash=10_000_000`)
5. 최초 실행 시 `ledger.cash() == 10_000_000`
6. `coid1 = uuid4().hex` → `market_buy("005930", 1, coid1)`
   - `"005930" in ledger.positions()`
   - `positions["005930"].qty == 1`
7. **멱등성 테스트:** 동일 `coid1`로 `market_buy` 재호출
   - 반환 Fill이 6단계와 동일
   - `positions["005930"].qty == 1` (증가 없음)
8. `coid2 = uuid4().hex` → `market_sell("005930", 1, coid2)`
   - `"005930" not in ledger.positions()` (전량매도 → 제거)
9. **잔고 부족 거부:** `market_buy("005930", 999_999, uuid4().hex)`
   - `ValueError`, 메시지에 `"INSUFFICIENT_CASH"` 포함
10. **텔레그램 알림 (옵션):** `TG_BOT_TOKEN` 있을 때만
    - `"[AUTOTRADE G0] smoke OK / trace_id={trace_id}"`
11. JSON 요약 stdout 출력: `{trace_id, fills, final_cash, pnl_proxy}`

## 선행 확인 절차

코드는 위 §2·§3와 실제 코드베이스를 대조하여 다음을 보고한다:

(a) 각 클래스/메서드 시그니처가 일치하는가? (인자명·타입·반환타입·예외)
(b) 불일치 항목이 있다면 항목별로 나열하라. **수정하지 말 것.**
(c) §3의 11단계 중 현재 코드로 *그대로* 실행 가능한 단계와 불가능한 단계를 구분하라.

수석님이 (a)(b)(c) 보고를 받은 후 §3 재작성 여부를 결정한다.