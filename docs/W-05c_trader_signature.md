# W-05c — Trader 시그니처 정정 + watcher.py TODO 활성화

## 미션

`src/core/trader.py` 의 4개 함수 시그니처를 `TradeTarget` → `Watcher` 호환으로 정정하고, `src/core/watcher.py` 의 W-05b TODO 3곳을 실호출로 교체한다.

- trader.py 의 4 함수 시그니처 정정 (place_buy_orders / cancel_buy_orders / execute_exit / simulate_fills)
- 각 함수 본문의 `target.*` 패턴을 `watcher.*` 매핑 표에 따라 정정
- _current_price 헬퍼도 watcher 호환으로 정정
- import 정리 (TradeTarget 제거, Watcher 추가)
- watcher.py 의 TODO 3곳을 실호출로 교체
- 비즈니스 로직은 *절대 손대지 말 것* (KIS API 호출, DRY_RUN, Position, 시장가 재시도 등)

자동 진행 모드. 수석님 confirm 없이 끝까지 진행. 멈출 조건은 [멈춤 조건] 참조.

크기 예상: trader.py 약 ±20 행 (시그니처 변경 + 본문 매핑), watcher.py 약 +5 행 (TODO 활성화)

## 화이트리스트

이 두 파일만 수정 가능:
- `src/core/trader.py`
- `src/core/watcher.py` (TODO 활성화 영역만)

## 블랙리스트

절대 수정 금지:
- `src/main.py` (W-06 영역)
- `src/core/monitor.py` (W-07 영역)
- `src/core/screener.py` (W-03 결과물 보존)
- `src/core/stock_master.py` (W-02 결과물 보존)
- `src/utils/notifier.py` (W-04 결과물 보존)
- `src/models/stock.py`
- `src/models/order.py` (Position 살림)
- `src/storage/database.py`
- `src/dashboard/*`
- `src/kis_api/*`
- `config/*`
- 그 외 모든 파일

## 배경 — 사실 base

### M-9 결과 (확정됨)
- Trader 클래스는 `src/core/trader.py` 에 정의
- 4 메서드가 `TradeTarget` 인자를 받음:
  - `place_buy_orders(target, available_cash)` — async
  - `cancel_buy_orders(target)` — async
  - `execute_exit(target, reason, price=0)` — async
  - `simulate_fills(target, current_price, ts)` — sync
- on_buy_filled 은 `target` 안 받음 — 시그니처 정정 *불필요*

### M-10 결과 (확정됨)

**trader.py 가 TradeTarget 에 write 하는 위치 — 전수 8건:**

| 라인 | 함수 | write 필드 | 값 |
|---|---|---|---|
| 56 | place_buy_orders | target.buy1_order_id | order1.order_id |
| 57 | place_buy_orders | target.buy1_placed | True |
| 69 | place_buy_orders | target.buy2_order_id | order2.order_id |
| 70 | place_buy_orders | target.buy2_placed | True |
| 130 | cancel_buy_orders | target.buy1_placed | False |
| 131 | cancel_buy_orders | target.buy2_placed | False |
| 132 | cancel_buy_orders | target.buy1_order_id | "" |
| 133 | cancel_buy_orders | target.buy2_order_id | "" |

**trader.py 가 TradeTarget 에서 read 하는 위치 — 전수:**

| 라인 | 함수 | read 패턴 |
|---|---|---|
| 39 | place_buy_orders | target.buy1_price(self.params) ← 메서드 호출 |
| 40 | place_buy_orders | target.buy2_price(self.params) ← 메서드 호출 |
| 53 | place_buy_orders | target.stock.code |
| 60 | place_buy_orders | target.stock.name |
| 60 | place_buy_orders | target.intraday_high |
| 60 | place_buy_orders | target.stock.market.value |
| 66 | place_buy_orders | target.stock.code |
| 73 | place_buy_orders | target.stock.name |
| 73 | place_buy_orders | target.intraday_high |
| 73 | place_buy_orders | target.stock.market.value |
| 175 | execute_exit | target.stock.code |
| 232 | _current_price | target.stock.current_price |

### 매핑 표 (TradeTarget → Watcher)

| 옛 (target.*) | 새 (watcher.*) | 종류 |
|---|---|---|
| target.stock.code | code | read |
| target.stock.name | name | read |
| target.stock.market.value | market.value | read |
| target.stock.current_price | current_price | read |
| target.intraday_high | intraday_high | read |
| target.buy1_price(params) | target_buy1_price | read (메서드 → 필드) |
| target.buy2_price(params) | target_buy2_price | read (메서드 → 필드) |
| target.buy1_order_id | buy1_order_id | read+write |
| target.buy2_order_id | buy2_order_id | read+write |
| target.buy1_placed | buy1_placed | read+write |
| target.buy2_placed | buy2_placed | read+write |

### 비즈니스 로직 보존 영역 — 절대 손대지 말 것

- KIS API 호출 (api.buy_order / api.sell_order / api.cancel_order)
- DRY_RUN 처리 (settings.is_dry_run 분기)
- Position 생성/관리 (Position(code, opened_at) + add_buy + add_sell)
- pending_buy_orders 리스트
- 시장가 재시도 로직 (hard_stop / futures_stop 실패 시)
- 로깅 메시지 한국어 그대로
- DRY_RUN 시 Position 직접 갱신 패턴
- on_buy_filled 함수 (시그니처 변경 없음)
- has_position / get_pnl / reset (target 안 받음)
- _send_buy_order (target 안 받음)
- _find_pending_order (target 안 받음)

### W-05b TODO 3곳

| watcher.py 라인 | 함수 | 현재 (placeholder) |
|---|---|---|
| 632 | _execute_buy | `# TODO (W-05c): self.trader.place_buy_orders(watcher, available_cash)` |
| 648 | _execute_exit | `# TODO (W-05c): self.trader.execute_exit(watcher, watcher.exit_reason, watcher.exit_price)` |
| 712 | on_buy_deadline | `# TODO (W-05c): self.trader.cancel_buy_orders(w)` |

W-05c 에서 이 3곳을 *실호출로 교체*. 단, *await* 처리 필요 (place_buy_orders / cancel_buy_orders / execute_exit 모두 async).

## ⚠ 결정적 — async/sync 처리

### 문제

- Trader 의 정정 대상 메서드는 **async** (place_buy_orders / cancel_buy_orders / execute_exit)
- WatcherCoordinator 의 메서드는 **sync** (W-05b 결정)
- sync 함수가 async 함수를 직접 호출 불가

### 해결 — 두 옵션

**옵션 (가)**: WatcherCoordinator 의 _execute_buy / _execute_exit / on_buy_deadline 을 async 로 변경
- 영향: Coordinator 의 일부 메서드가 async 가 됨
- 호출자 (main.py) 도 await 필요

**옵션 (나)**: Coordinator 가 *async 호출 의도* 를 마킹만 하고, *외부 async layer* 가 폴링해서 실제 await 처리
- 영향: Coordinator 가 sync 그대로
- main.py 가 매 틱 후 *Coordinator 의 pending async 호출* 폴링
- 추가 자료구조 필요 (pending list)

**결정 (이 명령서에서 동결)**: **옵션 (가)** 채택. 이유:
- 더 단순. 추가 자료구조 X.
- main.py 가 await 처리 (현재 main.py 도 trader 호출 시 await 사용 — line 802, 815 등)
- Coordinator 의 sync 결정은 *비즈니스 로직 단순성* 이었지 *async 회피* 가 아니었음

따라서 W-05c 에서:
- WatcherCoordinator 의 _execute_buy → async 로 변경
- WatcherCoordinator 의 _execute_exit → async 로 변경
- WatcherCoordinator 의 on_buy_deadline → async 로 변경
- WatcherCoordinator 의 _process_signals → async 로 변경 (위 메서드들 호출하므로)
- WatcherCoordinator 의 on_realtime_price → async 로 변경 (_process_signals 호출하므로)

→ Coordinator 의 *시세 라우팅 + 발주 흐름* 전체가 async 가 됨. 이는 *현재 main.py 의 _on_realtime_price 가 이미 async* 라 자연 호환.

→ on_buy_filled / on_sell_filled / on_realtime_futures / start_screening / shutdown / reset_for_next_day 는 sync 그대로 유지 (Trader 호출 X).

→ on_force_liquidate 는 *_process_signals 호출* 이 있으므로 async 변경.

## 흐름

```
작업 1 (trader.py import 정정)
  → 작업 2 (place_buy_orders 정정)
  → 작업 3 (cancel_buy_orders 정정)
  → 작업 4 (execute_exit 정정)
  → 작업 5 (simulate_fills 정정)
  → 작업 6 (_current_price 정정)
  → 작업 7 (watcher.py Coordinator 메서드 async 변경 + TODO 활성화)
  → 검증 1~7
  → 보고
```

중간에 멈추지 말 것. 각 작업 사이 검증 X.

---

## [작업 1] trader.py import 정정

### 1-1. TradeTarget import 제거 (있다면)

`src/core/trader.py` 상단의 import 영역에서:

```python
from src.models.stock import TradeTarget
```

또는 동등 패턴이 있으면 *제거*. TradeTarget 을 더 이상 사용 안 함.

### 1-2. Watcher import 추가

import 영역에 다음 줄 추가:

```python
from src.core.watcher import Watcher
```

위치: 다른 from src.* import 옆.

### 1-3. 다른 import 보존

- `from src.models.order import Position, Order, OrderSide, OrderStatus` 또는 동등 — 그대로
- `from src.models.stock import Stock` 또는 `MarketType` — 사용 여부 확인 후 그대로 보존
- KIS API import — 그대로
- logger / asyncio / typing — 그대로

---

## [작업 2] place_buy_orders 정정

### 2-1. 시그니처 변경

기존 (line 36):
```python
async def place_buy_orders(self, target: TradeTarget, available_cash: int) -> None:
```

변경:
```python
async def place_buy_orders(self, watcher: Watcher, available_cash: int) -> None:
```

### 2-2. 본문 매핑 — 라인별 정정

기존 본문 (line 38-75) 의 다음 패턴을 모두 정정:

| 옛 | 새 |
|---|---|
| `target.buy1_price(self.params)` | `watcher.target_buy1_price` |
| `target.buy2_price(self.params)` | `watcher.target_buy2_price` |
| `target.stock.code` | `watcher.code` |
| `target.stock.name` | `watcher.name` |
| `target.intraday_high` | `watcher.intraday_high` |
| `target.stock.market.value` | `watcher.market.value` |
| `target.buy1_order_id` | `watcher.buy1_order_id` |
| `target.buy1_placed` | `watcher.buy1_placed` |
| `target.buy2_order_id` | `watcher.buy2_order_id` |
| `target.buy2_placed` | `watcher.buy2_placed` |

### 2-3. 본문 정정 후 예시 (참고)

작업 후 본문이 대략 다음과 같아야 함:

```python
    async def place_buy_orders(self, watcher: Watcher, available_cash: int) -> None:
        """고가 확정 후 매수 지정가 2건 배치."""
        ep = self.params.entry
        buy1_price = watcher.target_buy1_price
        buy2_price = watcher.target_buy2_price

        buy1_amount = int(available_cash * ep.buy1_ratio / 100)
        buy2_amount = int(available_cash * ep.buy2_ratio / 100)

        buy1_qty = max(1, buy1_amount // buy1_price) if buy1_price > 0 else 0
        buy2_qty = max(1, buy2_amount // buy2_price) if buy2_price > 0 else 0

        now = now_kst()
        self.pending_buy_orders = []

        # 1차 매수
        order1 = await self._send_buy_order(
            watcher.code, buy1_qty, buy1_price, "buy1", now
        )
        if order1:
            watcher.buy1_order_id = order1.order_id
            watcher.buy1_placed = True
            self.pending_buy_orders.append(order1)
            logger.info(
                f"[{watcher.name}] 1차 매수 주문: {buy1_price:,}원 × {buy1_qty}주 "
                f"(고가 {watcher.intraday_high:,}원 대비 -{ep.kospi_buy1_pct if watcher.market.value == 'KOSPI' else ep.kosdaq_buy1_pct}%)"
            )

        # 2차 매수
        order2 = await self._send_buy_order(
            watcher.code, buy2_qty, buy2_price, "buy2", now
        )
        if order2:
            watcher.buy2_order_id = order2.order_id
            watcher.buy2_placed = True
            self.pending_buy_orders.append(order2)
            logger.info(
                f"[{watcher.name}] 2차 매수 주문: {buy2_price:,}원 × {buy2_qty}주 "
                f"(고가 {watcher.intraday_high:,}원 대비 -{ep.kospi_buy2_pct if watcher.market.value == 'KOSPI' else ep.kosdaq_buy2_pct}%)"
            )
```

주의:
- `await self._send_buy_order(...)` 호출은 그대로 유지 — 비즈니스 로직
- `self.pending_buy_orders` 리스트 처리 그대로
- DRY_RUN 분기 (`_send_buy_order` 내부) 그대로
- 로거 메시지 한국어 그대로

---

## [작업 3] cancel_buy_orders 정정

### 3-1. 시그니처 변경

기존 (line 113):
```python
async def cancel_buy_orders(self, target: TradeTarget) -> None:
```

변경:
```python
async def cancel_buy_orders(self, watcher: Watcher) -> None:
```

### 3-2. 본문 매핑

기존 본문 (line 115-134) 의 다음 패턴을 정정:

| 옛 | 새 |
|---|---|
| `target.buy1_placed = False` | `watcher.buy1_placed = False` |
| `target.buy2_placed = False` | `watcher.buy2_placed = False` |
| `target.buy1_order_id = ""` | `watcher.buy1_order_id = ""` |
| `target.buy2_order_id = ""` | `watcher.buy2_order_id = ""` |

KIS API 호출 (`self.api.cancel_order(...)`) 은 그대로 유지.
DRY_RUN 분기는 그대로.
`self.pending_buy_orders` 처리는 그대로.

### 3-3. 본문 정정 후 예시

```python
    async def cancel_buy_orders(self, watcher: Watcher) -> None:
        """미체결 매수 주문 전량 취소."""
        for order in self.pending_buy_orders:
            if not order.is_active:
                continue

            if self.settings.is_dry_run:
                order.status = OrderStatus.CANCELLED
                logger.debug(f"[DRY_RUN] 주문 취소: {order.label} {order.order_id}")
            else:
                try:
                    await self.api.cancel_order(order.order_id, order.code)
                    order.status = OrderStatus.CANCELLED
                    logger.info(f"주문 취소: {order.label} {order.order_id}")
                except Exception as e:
                    logger.error(f"주문 취소 실패 [{order.label}]: {e}")

        watcher.buy1_placed = False
        watcher.buy2_placed = False
        watcher.buy1_order_id = ""
        watcher.buy2_order_id = ""
        self.pending_buy_orders = []
```

---

## [작업 4] execute_exit 정정

### 4-1. 시그니처 변경

기존 (line 164):
```python
async def execute_exit(
    self, target: TradeTarget, reason: str, price: int = 0
) -> Optional[Order]:
```

변경:
```python
async def execute_exit(
    self, watcher: Watcher, reason: str, price: int = 0
) -> Optional[Order]:
```

### 4-2. 본문 매핑

기존 본문 (line 166-228) 의 패턴 정정:

| 옛 | 새 |
|---|---|
| `target.stock.code` | `watcher.code` |
| `await self.cancel_buy_orders(target)` | `await self.cancel_buy_orders(watcher)` |
| `self._current_price(target)` | `self._current_price(watcher)` |

비즈니스 로직 보존:
- `if self.position is None or self.position.total_qty <= 0: return None` 그대로
- `qty = self.position.total_qty` 그대로
- `now = now_kst()` 그대로
- `use_market = reason in ("hard_stop", "futures_stop", "force")` 그대로
- Order 생성 그대로
- DRY_RUN 분기 + Position.add_sell + 로깅 그대로
- 실거래 분기 + KIS API 호출 + 시장가 재시도 로직 그대로

### 4-3. 본문 정정 후 예시 (핵심 부분만)

```python
    async def execute_exit(
        self, watcher: Watcher, reason: str, price: int = 0
    ) -> Optional[Order]:
        """전량 청산. reason에 따라 시장가/지정가 결정."""
        if self.position is None or self.position.total_qty <= 0:
            return None

        # 먼저 미체결 매수 주문 취소
        await self.cancel_buy_orders(watcher)

        qty = self.position.total_qty
        code = watcher.code
        now = now_kst()

        # 시장가: hard_stop, futures_stop, force
        use_market = reason in ("hard_stop", "futures_stop", "force")

        order = Order(
            code=code,
            side=OrderSide.SELL,
            price=0 if use_market else price,
            qty=qty,
            label=reason,
            created_at=now,
        )

        if self.settings.is_dry_run:
            sell_price = self._current_price(watcher) if use_market else price
            order.order_id = f"DRY_{reason}_{now.strftime('%H%M%S')}"
            order.filled_price = sell_price
            order.filled_qty = qty
            order.filled_at = now
            order.status = OrderStatus.FILLED
            self.position.add_sell(order)
            logger.info(
                f"[DRY_RUN] 청산: {reason} {sell_price:,}원 × {qty}주 "
                f"(P&L {self.position.pnl():+,.0f}원)"
            )
        else:
            # KIS API 실거래 호출 + 시장가 재시도 로직 (그대로 유지)
            try:
                price_type = ORDER_TYPE_MARKET if use_market else ORDER_TYPE_LIMIT
                result = await self.api.sell_order(
                    code=code, qty=qty, price=price,
                    price_type=price_type,
                )
                order.order_id = result.get("order_no", "")
                order.status = OrderStatus.PENDING
                logger.info(f"매도주문 접수: {reason} {qty}주 ({'시장가' if use_market else f'{price:,}원'})")
            except Exception as e:
                logger.error(f"매도주문 실패 [{reason}]: {e}")
                # 하드 손절 실패 시 재시도
                if reason in ("hard_stop", "futures_stop"):
                    logger.warning(f"{reason} 재시도 (시장가)")
                    try:
                        result = await self.api.sell_order(
                            code=code, qty=qty, price=0,
                            price_type=ORDER_TYPE_MARKET,
                        )
                        order.order_id = result.get("order_no", "")
                        order.status = OrderStatus.PENDING
                    except Exception as e2:
                        logger.critical(f"{reason} 재시도 실패: {e2}")
                        order.status = OrderStatus.REJECTED

        return order
```

---

## [작업 5] simulate_fills 정정

### 5-1. 시그니처 변경

기존 (line 236):
```python
def simulate_fills(self, target: TradeTarget, current_price: int, ts: datetime) -> list[str]:
```

변경:
```python
def simulate_fills(self, watcher: Watcher, current_price: int, ts: datetime) -> list[str]:
```

### 5-2. 본문 정정

이 함수는 기존 본문에서 `target` 변수를 *읽기 전용으로 사용 안 함*. order 객체와 self.position 만 다룸. 다만 *시그니처 인자명* 은 변경 필요.

본문 안에 `target.` 패턴이 있으면 모두 `watcher.` 로 변경. M-10 결과로 식별:

기존 본문 (line 238-263) 에서 `target` 사용 위치는 *없음* (확인 필요). 만약 있으면 매핑 표대로 정정.

### 5-3. 정정 후 예시

```python
    def simulate_fills(self, watcher: Watcher, current_price: int, ts: datetime) -> list[str]:
        """
        DRY_RUN 모드: 현재가가 지정가에 도달하면 가상 체결.
        체결된 label 리스트 반환.
        """
        if not self.settings.is_dry_run:
            return []

        filled_labels = []
        for order in self.pending_buy_orders:
            if not order.is_active:
                continue
            if current_price <= order.price:
                order.filled_price = order.price
                order.filled_qty = order.qty
                order.filled_at = ts
                order.status = OrderStatus.FILLED

                if self.position is None:
                    self.position = Position(code=order.code, opened_at=ts)
                self.position.add_buy(order)

                filled_labels.append(order.label)
                logger.info(
                    f"[DRY_RUN] {order.label} 가상 체결: {order.price:,}원 × {order.qty}주"
                )

        return filled_labels
```

(기존 본문과 거의 동일. 시그니처 인자명만 watcher 로.)

---

## [작업 6] _current_price 정정

### 6-1. 시그니처 변경

기존 (line 230 근방):
```python
def _current_price(self, target: TradeTarget) -> int:
    """현재가 (dry_run 시뮬레이션용)."""
    return target.stock.current_price
```

변경:
```python
def _current_price(self, watcher: Watcher) -> int:
    """현재가 (dry_run 시뮬레이션용)."""
    return watcher.current_price
```

---

## [작업 7] watcher.py 의 TODO 3곳 활성화

### 7-1. _execute_buy 정정 (watcher.py:618-633 근방)

기존 (W-05b 작성):
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

변경 (async 추가 + Trader 실호출):
```python
    async def _execute_buy(self, watcher: Watcher, ts: datetime) -> None:
        """매수 1차 + 2차 발주. Trader 호출."""
        watcher.buy1_pending = True
        watcher.buy2_pending = True
        
        logger.info(
            f"[Coordinator] 매수 발주: {watcher.name} "
            f"1차 {watcher.target_buy1_price:,} / 2차 {watcher.target_buy2_price:,}"
        )
        
        if self.trader is not None:
            # available_cash 는 main.py 에서 Coordinator 에 주입 (W-06)
            available_cash = self._available_cash
            await self.trader.place_buy_orders(watcher, available_cash)
```

⚠ `self._available_cash` 는 W-05b 에서는 없음. W-06 에서 main.py 가 Coordinator 에 *available_cash* 를 어떻게 전달할지 결정하지만, W-05c 에서는 *Coordinator 의 인스턴스 필드로 추가*. `__init__` 에 추가 작업 (작업 7-2 참조).

또는 더 단순한 패턴: `available_cash` 를 *_execute_buy 의 인자로 받음*. 이 경우 _process_signals 에서 호출 시 인자 전달 필요. _process_signals 도 마찬가지로 인자 받음 → on_realtime_price 도 → main.py 가 매번 전달.

**결정 (이 명령서에서 동결)**: **인스턴스 필드 패턴**. 이유: 시그니처 변경 폭 최소화. main.py 가 *Coordinator.set_available_cash(amount)* 같은 메서드로 갱신.

따라서 작업 7-1 의 정정은:

```python
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
```

### 7-2. WatcherCoordinator.__init__ 에 _available_cash 추가

기존 __init__ (W-05b 작성):
```python
    def __init__(self, params: StrategyParams, trader=None):
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

변경 (한 줄 추가):
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
    
    def set_available_cash(self, amount: int) -> None:
        """매수 가능 현금 갱신. main.py 가 호출."""
        self._available_cash = amount
```

### 7-3. _execute_exit 정정 (watcher.py:637-648 근방)

기존:
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

변경 (async 추가 + 실호출):
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

### 7-4. _process_signals 를 async 로 변경

기존 (W-05b 작성):
```python
    def _process_signals(self, ts: datetime) -> None:
```

변경:
```python
    async def _process_signals(self, ts: datetime) -> None:
```

본문 안의 `self._execute_exit(active, ts)` 와 `self._execute_buy(chosen, ts)` 를 *await* 처리:

```python
    async def _process_signals(self, ts: datetime) -> None:
        """매 틱 후 호출. 청산 신호 처리 + 매수 발주 평가."""
        # === 1. active 청산 신호 처리 ===
        active = self.active
        if active is not None and active._exit_signal_pending:
            await self._execute_exit(active, ts)
            self._active_code = None
            return
        
        # === 2. active 가 비어있으면 매수 발주 평가 ===
        if self._active_code is not None:
            return
        
        if self._is_after_buy_deadline(ts):
            return
        
        yes_watchers = [w for w in self.watchers if w.is_yes and not w.is_terminal]
        if not yes_watchers:
            return
        
        chosen = min(
            yes_watchers,
            key=lambda w: abs(w.distance_to_buy1(w.current_price))
        )
        
        await self._execute_buy(chosen, ts)
        self._active_code = chosen.code
```

### 7-5. on_realtime_price 를 async 로 변경

기존:
```python
    def on_realtime_price(self, code: str, price: int, ts: datetime) -> None:
```

변경:
```python
    async def on_realtime_price(self, code: str, price: int, ts: datetime) -> None:
```

본문 마지막의 `self._process_signals(ts)` 호출을 `await self._process_signals(ts)` 로 변경:

```python
    async def on_realtime_price(self, code: str, price: int, ts: datetime) -> None:
        """KIS WebSocket 체결가 수신. 해당 종목 watcher 에 라우팅."""
        for w in self.watchers:
            if w.code == code and not w.is_terminal:
                w.on_tick(price, ts, self._latest_futures_price)
        
        await self._process_signals(ts)
```

### 7-6. on_buy_deadline 을 async 로 변경 + Trader 호출

기존 (W-05b 작성):
```python
    def on_buy_deadline(self, ts: datetime) -> None:
        """10:55 매수 마감. 비-ENTERED watcher 모두 SKIPPED."""
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
```

변경 (async + Trader 호출 활성화):
```python
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
```

### 7-7. on_force_liquidate 를 async 로 변경

기존:
```python
    def on_force_liquidate(self, ts: datetime) -> None:
```

변경:
```python
    async def on_force_liquidate(self, ts: datetime) -> None:
```

본문 마지막의 `self._process_signals(ts)` 호출을 `await self._process_signals(ts)` 로:

```python
    async def on_force_liquidate(self, ts: datetime) -> None:
        """11:20 강제 청산. ENTERED watcher 시장가 청산."""
        for w in self.watchers:
            if w.state == WatcherState.ENTERED:
                w.force_exit(ts)
                logger.warning(f"[Coordinator] 11:20 강제 청산: {w.name}")
        
        await self._process_signals(ts)
```

### 7-8. 그 외 메서드는 sync 그대로

다음은 *변경 없음*:
- `start_screening` (sync)
- `on_realtime_futures` (sync)
- `on_buy_filled` (sync)
- `on_sell_filled` (sync)
- `_is_after_buy_deadline` (sync)
- `shutdown` (sync)
- `reset_for_next_day` (sync)
- `set_available_cash` (sync, 신규)

---

## [검증]

### 검증 1 — trader.py import OK

```bash
python -c "from src.core.trader import Trader; print('trader import OK')"
```

기대: `trader import OK`

### 검증 2 — Trader 시그니처 정정 확인

```bash
python -c "
import inspect
from src.core.trader import Trader
print('place_buy_orders:', inspect.signature(Trader.place_buy_orders))
print('cancel_buy_orders:', inspect.signature(Trader.cancel_buy_orders))
print('execute_exit:', inspect.signature(Trader.execute_exit))
print('simulate_fills:', inspect.signature(Trader.simulate_fills))
"
```

기대: 4개 시그니처 모두 *watcher* 인자 포함, *target* 미포함

### 검증 3 — watcher.py import OK + Coordinator 메서드 async 변경 확인

```bash
python -c "
import inspect
from src.core.watcher import WatcherCoordinator
print('_execute_buy is coroutine:', inspect.iscoroutinefunction(WatcherCoordinator._execute_buy))
print('_execute_exit is coroutine:', inspect.iscoroutinefunction(WatcherCoordinator._execute_exit))
print('_process_signals is coroutine:', inspect.iscoroutinefunction(WatcherCoordinator._process_signals))
print('on_realtime_price is coroutine:', inspect.iscoroutinefunction(WatcherCoordinator.on_realtime_price))
print('on_buy_deadline is coroutine:', inspect.iscoroutinefunction(WatcherCoordinator.on_buy_deadline))
print('on_force_liquidate is coroutine:', inspect.iscoroutinefunction(WatcherCoordinator.on_force_liquidate))
print('start_screening NOT coroutine:', not inspect.iscoroutinefunction(WatcherCoordinator.start_screening))
print('on_buy_filled NOT coroutine:', not inspect.iscoroutinefunction(WatcherCoordinator.on_buy_filled))
"
```

기대: 6개 메서드는 coroutine, 2개는 NOT coroutine

### 검증 4 — set_available_cash 메서드 존재

```bash
python -c "
from src.core.watcher import WatcherCoordinator
from config.settings import StrategyParams
params = StrategyParams.load()
c = WatcherCoordinator(params=params, trader=None)
print('initial available_cash:', c._available_cash)
c.set_available_cash(10000000)
print('after set:', c._available_cash)
"
```

기대:
```
initial available_cash: 0
after set: 10000000
```

### 검증 5 — Trader 가 Watcher 객체로 호출 가능 (시그니처 호환)

```bash
python -c "
import asyncio
import inspect
from src.core.trader import Trader
from src.core.watcher import Watcher
from config.settings import StrategyParams
from src.models.stock import MarketType

# 시그니처 호환만 확인 (실행 X)
sig_place = inspect.signature(Trader.place_buy_orders)
params_list = list(sig_place.parameters.keys())
print('place_buy_orders params:', params_list)
assert 'watcher' in params_list, 'watcher 인자 누락'
assert 'target' not in params_list, 'target 인자 잔존'
print('place_buy_orders 시그니처 호환 OK')

sig_cancel = inspect.signature(Trader.cancel_buy_orders)
params_list = list(sig_cancel.parameters.keys())
assert 'watcher' in params_list, 'cancel watcher 인자 누락'
assert 'target' not in params_list, 'cancel target 인자 잔존'
print('cancel_buy_orders 시그니처 호환 OK')

sig_exit = inspect.signature(Trader.execute_exit)
params_list = list(sig_exit.parameters.keys())
assert 'watcher' in params_list, 'exit watcher 인자 누락'
assert 'target' not in params_list, 'exit target 인자 잔존'
print('execute_exit 시그니처 호환 OK')

sig_sim = inspect.signature(Trader.simulate_fills)
params_list = list(sig_sim.parameters.keys())
assert 'watcher' in params_list, 'sim watcher 인자 누락'
assert 'target' not in params_list, 'sim target 인자 잔존'
print('simulate_fills 시그니처 호환 OK')
"
```

기대: 4개 시그니처 모두 호환 OK

### 검증 6 — TODO 3곳이 모두 사라짐

Python 직접 읽기 (S-02-d):

```bash
python -c "
with open('src/core/watcher.py', 'r', encoding='utf-8') as f:
    content = f.read()
todo_count = content.count('TODO (W-05c)')
print('TODO (W-05c) count:', todo_count)
assert todo_count == 0, f'TODO 잔존: {todo_count}건'
print('TODO 모두 활성화됨')
"
```

기대: `TODO (W-05c) count: 0`

### 검증 7 — trader.py 에 TradeTarget 잔존 없음

```bash
python -c "
with open('src/core/trader.py', 'r', encoding='utf-8') as f:
    content = f.read()
target_count = content.count('TradeTarget')
print('TradeTarget count in trader.py:', target_count)
"
```

기대: `TradeTarget count in trader.py: 0` (import 와 시그니처 모두 제거)

---

## [검증 실패 시 — 멈춤 조건]

- 검증 1 실패 (trader.py import 에러) → 멈춤 + 보고
- 검증 2 실패 (시그니처 정정 안 됨) → 멈춤 + 보고
- 검증 3 실패 (Coordinator async 변경 안 됨) → 멈춤 + 보고
- 검증 4 실패 (set_available_cash 미존재) → 멈춤 + 보고
- 검증 5 실패 (시그니처 assert 실패) → 멈춤 + 보고
- 검증 6 실패 (TODO 잔존) → 멈춤 + 보고
- 검증 7 실패 (TradeTarget 잔존) → 멈춤 + 보고

멈춤 시 변경 *되돌리지 말 것*. 그대로 두고 보고.

---

## [모호한 케이스 — 사전 결정]

### 케이스 D-1: place_buy_orders 의 ep.kospi_buy1_pct 분기
기존 코드:
```python
ep.kospi_buy1_pct if target.stock.market.value == 'KOSPI' else ep.kosdaq_buy1_pct
```

변경:
```python
ep.kospi_buy1_pct if watcher.market.value == 'KOSPI' else ep.kosdaq_buy1_pct
```

`market.value` 비교 패턴 그대로. 'KOSPI' 문자열 그대로.

### 케이스 D-2: M-10 결과의 라인 번호와 실제 라인 차이
M-10 의 라인 번호는 *근방 표시* 일 수 있음. grep 으로 정확한 위치 식별 후 정정.

### 케이스 D-3: target 변수가 다른 의미로 사용된 경우
trader.py 에서 `target` 이 *TradeTarget 외 다른 의미* (예: 임시 변수) 로 사용되면 *그 부분은 건드리지 말 것*. 매핑 표는 *TradeTarget 인스턴스* 에만 적용.

식별 방법: `target` 변수의 *타입* 이 함수 시그니처의 `target: TradeTarget` 인 경우만 대상.

### 케이스 D-4: simulate_fills 본문에 target 사용 0건
M-10 결과: simulate_fills 본문은 `target` 을 사용 안 함. 시그니처만 정정. 본문 변경 0.

### 케이스 D-5: _send_buy_order 와 _find_pending_order 는 손대지 말 것
이 두 헬퍼는 *target 인자를 받지 않음*. 시그니처 정정 불필요. 본문 변경 X.

### 케이스 D-6: _build_pending_order 또는 동등 헬퍼 발견
M-10 에서 못 본 헬퍼가 있을 수 있음. 만약 *target 인자를 받는 헬퍼* 가 발견되면 *멈춤 + 보고*. 추가 정정 영역인지 수석님 결정 필요.

### 케이스 D-7: watcher.py 의 _execute_buy 가 이미 async 인 경우
W-05b 작성 시 sync 였으나 다른 경로로 이미 async 가 됐을 수 있음. 그 경우 작업 7-1 의 *async 추가* 부분 skip.

### 케이스 D-8: _available_cash 필드가 이미 존재
드물지만 W-05b 결과물에 *우연히* 같은 이름 필드가 있을 수 있음. 그 경우 작업 7-2 의 *추가* 는 skip + 보고에 기록.

### 케이스 D-9: Coordinator 의 다른 메서드가 _process_signals 를 호출
M-9 / M-10 에서 식별 안 된 호출자가 있을 수 있음. grep 으로 확인:
```bash
grep -n "_process_signals" src/core/watcher.py
```
모든 호출자가 *await* 사용하는지 확인. async 변경 후 호출자도 *async + await* 필요.

### 케이스 D-10: from typing import Optional 누락
이미 있을 가능성. 없으면 추가 OK. 다른 import 추가는 금지.

---

## [자체 발견 처리 규칙]

Code 가 작업 중 다음을 발견하면 → 수정하지 말 것 → 보고의 [발견] 섹션에 1~3줄 기록:
- trader.py 의 잠재적 버그 (현재 코드 보존이 원칙)
- M-9 / M-10 결과와 다른 코드 발견
- watcher.py 의 W-05a/b 결과물의 잠재적 문제
- main.py 와의 결합 문제 (W-06 영역, 정정 안 함)
- 더 깔끔한 패턴

---

## [보고]

### A. 변경 라인 (trader.py)
- 작업 1 (import): 추가/제거 라인
- 작업 2 (place_buy_orders): 시그니처 + 본문 정정 라인
- 작업 3 (cancel_buy_orders): 시그니처 + 본문 정정 라인
- 작업 4 (execute_exit): 시그니처 + 본문 정정 라인
- 작업 5 (simulate_fills): 시그니처 정정 라인 (본문 변경 0 가능)
- 작업 6 (_current_price): 시그니처 + 본문 정정 라인

### B. 변경 라인 (watcher.py)
- 작업 7-1 (_execute_buy): async 추가 + 실호출 교체 라인
- 작업 7-2 (__init__ + set_available_cash): 추가 라인
- 작업 7-3 (_execute_exit): async 추가 + 실호출 교체 라인
- 작업 7-4 (_process_signals): async 변경 라인
- 작업 7-5 (on_realtime_price): async 변경 라인
- 작업 7-6 (on_buy_deadline): async 변경 + 실호출 교체 라인
- 작업 7-7 (on_force_liquidate): async 변경 라인

### C. 라인 수
- trader.py: before / after / 차이
- watcher.py: before (749) / after / 차이

### D. 검증 결과
- 검증 1: 성공/실패 + 출력
- 검증 2: 성공/실패 + 출력 (4 시그니처)
- 검증 3: 성공/실패 + 출력 (8 메서드 async/sync)
- 검증 4: 성공/실패 + 출력 (set_available_cash)
- 검증 5: 성공/실패 + 출력 (assert 4개)
- 검증 6: 성공/실패 + 출력 (TODO count = 0)
- 검증 7: 성공/실패 + 출력 (TradeTarget count in trader.py)

### E. 다른 파일 수정 여부
*반드시 trader.py + watcher.py 두 파일만*

### F. 모호한 케이스 처리 결과 (D-1 ~ D-10 중 발생한 것)

### G. [발견] 섹션 — 자체 발견 사항 (있으면)

### H. W-06 진입 준비
- Trader 가 Watcher 호환 시그니처
- WatcherCoordinator 의 매수/청산 메서드가 async + Trader 실호출
- W-06 에서 main.py 의 매매 로직 영역을 Coordinator 호출 위임으로 변경
- main.py 의 trader 호출 (line 430, 795, 802, 807, 815, 890) 도 watcher 인자로 전달

---

## [추가 금지 — 자동 모드 강화]

- trader.py + watcher.py 외 어떤 파일도 수정 금지
- KIS API 호출 코드 변경 금지 (api.buy_order / api.sell_order / api.cancel_order)
- DRY_RUN 분기 변경 금지
- Position 클래스 사용 패턴 변경 금지 (Position 생성 / add_buy / add_sell / pnl)
- 시장가 재시도 로직 변경 금지
- 로깅 메시지 변경 금지 (한국어 그대로)
- 영어 메시지 추가 금지
- on_buy_filled (Trader 의) 시그니처 변경 금지 (target 인자 안 받음)
- _send_buy_order 시그니처 변경 금지
- _find_pending_order 시그니처 변경 금지
- has_position / get_pnl / reset 시그니처 변경 금지
- 새 함수 / 새 클래스 추가 금지 (단, set_available_cash 1개는 명령서 명시)
- try-except 추가/제거 금지
- 타입 힌트 추가 금지 (단, 시그니처 변경 영역은 watcher 타입 힌트 OK)
- main.py 수정 어떤 형태로도 금지 (W-06 영역)
- monitor.py 수정 금지 (W-07 영역)
- TradeTarget 클래스 자체 수정 금지 (W-07 영역)
- commit 금지
- 임시 파일 / scratch 파일 생성 금지
- W-06 / W-07 / W-08 영역 손대지 말 것
