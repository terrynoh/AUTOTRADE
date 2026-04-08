# W-06a — main.py 사실 base 정정

## 미션

`src/main.py` 의 매매 영역을 *Watcher / WatcherCoordinator 호환* 으로 정정한다. **사실 base 정정만** — 매매 위임은 W-06b 에서 처리.

작업 영역:
- import 정정 (Watcher / WatcherCoordinator 추가, TargetMonitor / TradeTarget 제거)
- `__init__` 정정 (옛 자료구조 폐기 + Coordinator 추가)
- `_build_trade_record` 시그니처 + 본문 정정 (mon → watcher)
- `_on_realtime_price` 단순 위임 (Coordinator.on_realtime_price)
- `_on_futures_price` 단순 위임 (Coordinator.on_realtime_futures)
- `_emergency_cancel_orders` 의 `_active_monitor` 참조 → Coordinator.active
- KIS subscribe 패턴 변경 (1종목 → 3종목)

자동 진행 모드. 수석님 confirm 없이 끝까지 진행. 멈출 조건은 [멈춤 조건] 참조.

크기 예상: main.py 약 ±50 행 변경

## 화이트리스트

이 파일만 수정 가능:
- `src/main.py`

## 블랙리스트

절대 수정 금지:
- `src/core/watcher.py` (W-05 결과물 보존)
- `src/core/trader.py` (W-05c 결과물 보존)
- `src/core/monitor.py` (W-07 영역)
- `src/core/screener.py` (W-03 결과물 보존)
- `src/core/stock_master.py` (W-02 결과물 보존)
- `src/utils/notifier.py` (W-04 결과물 보존)
- `src/models/stock.py`
- `src/models/order.py`
- `src/storage/database.py`
- `src/dashboard/*`
- `src/kis_api/*`
- `config/*`
- 그 외 모든 파일

## 배경 — 사실 base

### M-11 결과 (확정됨)

**main.py 의 함수 영역 분류:**

| 라인 | 함수명 | sync/async | 영역 | W-06a 처리 |
|---|---|---|---|---|
| 52 | `__init__` | sync | [생성/종료] | **정정** |
| 106 | set_manual_codes | sync | [핸들러] | 보존 |
| 111 | clear_manual_codes | sync | [핸들러] | 보존 |
| 116 | get_manual_codes | sync | [핸들러] | 보존 |
| 120 | _get_status | sync | [핸들러] | W-06b |
| 140 | _stop_trading | sync | [핸들러] | 보존 |
| 148 | run | async | [생성/종료] | 보존 (KIS subscribe 부분만 W-06b) |
| 260 | _schedule_screening | async | [매매] | W-06b |
| 266 | _on_screening | async | [매매] | W-06b |
| 311 | _start_monitoring_candidate | async | [매매]+[시세] | **정정** (KIS subscribe 영역) |
| 357 | _try_next_candidate | async | [매매] | W-06b |
| 421 | _schedule_force_liquidate | async | [매매] | W-06b |
| 436 | _schedule_market_close | async | [DB]+[기타] | W-06b |
| 509 | `_build_trade_record` | sync | [DB] | **정정** |
| 589 | _generate_daily_analysis | sync | [기타] | 보존 |
| 703 | _format_report_details | sync | [기타] | 보존 |
| 749 | `_on_realtime_price` | sync | [시세] | **정정** |
| 764 | `_on_futures_price` | sync | [시세] | **정정** |
| 778 | _monitor_loop_runner | async | [매매] | W-06b |
| 784 | _process_signals | async | [매매] | W-06b |
| 849 | _on_ws_disconnect | sync | [시세] | 보존 |
| 870 | `_emergency_cancel_orders` | async | [매매]+[시세] | **정정** (active_monitor 참조 영역) |
| 915 | _network_health_check | async | [기타] | 보존 |
| 951 | _start_dashboard_server | sync | [기타] | 보존 |
| 979 | _build_stock_name_cache | async | [기타] | 보존 |
| 1002 | _fire_state_update | async | [기타] | 보존 |
| 1009 | _wait_until | async | [기타] | 보존 |
| 1029 | main (모듈 수준) | sync | [생성/종료] | 보존 |

**W-06a 정정 대상**: `__init__`, `_build_trade_record`, `_on_realtime_price`, `_on_futures_price`, `_emergency_cancel_orders`, `_start_monitoring_candidate` 의 KIS subscribe 영역.

**W-06a 보존 대상 (W-06b 영역)**: `_on_screening`, `_try_next_candidate`, `_process_signals`, `_schedule_force_liquidate`, `_schedule_market_close`, `_get_status`, `_monitor_loop_runner`.

### 매핑 표 (M-11 의 _build_trade_record)

| 옛 | 새 |
|---|---|
| `def _build_trade_record(self, mon: TargetMonitor, today)` | `def _build_trade_record(self, watcher: Watcher, today)` |
| `t = mon.target` | (제거) |
| `t.stock.code` | `watcher.code` |
| `t.stock.name` | `watcher.name` |
| `t.stock.market.value` | `watcher.market.value` |
| `t.intraday_high` | `watcher.intraday_high` |
| `t.total_buy_qty` | `watcher.total_buy_qty` |
| `t.avg_price` | `(watcher.total_buy_amount / watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0)` |
| `t.total_buy_amount` | `watcher.total_buy_amount` |
| `t.buy1_filled` | `watcher.buy1_filled` |
| `t.buy2_filled` | `watcher.buy2_filled` |
| `t.exit_reason` | `watcher.exit_reason` |
| `t.buy1_price(self.params)` | `watcher.target_buy1_price` |
| `t.target_price` | `watcher.target_price` |
| `t.stock.current_price` | `watcher.current_price` |

## ⚠ 결정적 — 옛 자료구조 폐기 vs 임시 보존

### 폐기 대상 (옛 single active 패턴)

W-06a 시점에 *완전 폐기*:
- `self._monitors: list[TargetMonitor] = []`
- `self._active_monitor: Optional[TargetMonitor] = None`
- `self._subscribed_code: Optional[str] = None` (3종목 패턴 변경)
- `self._candidate_pool: list[TradeTarget] = []`
- `self._candidate_index: int = 0`
- `self._completed_codes: set[str] = set()`

### 신규 추가

`__init__` 안에:
- `self._coordinator: WatcherCoordinator = WatcherCoordinator(params=self.params, trader=self.trader)`
- `self._subscribed_codes: list[str] = []` (3종목 구독 코드 추적)

### ⚠ W-06b 영역의 함수가 *옛 자료구조에 의존*

W-06a 후에 다음 함수들이 *옛 자료구조 참조* 를 *그대로 가지고 있음*:
- `_on_screening` (line 309: `_start_monitoring_candidate(0)`)
- `_start_monitoring_candidate` (W-06a 에서 KIS subscribe 영역만 정정, 나머지는 W-06b)
- `_try_next_candidate` (line 397, 401: `_available_cash` 갱신)
- `_process_signals` (line 786: `_active_monitor`)
- `_schedule_force_liquidate` (line 425: `_monitors`)
- `_schedule_market_close` (line 448: `_monitors`)
- `_monitor_loop_runner` (line 778: monitor 호출)
- `_get_status` (line 122-137: `_monitors`)

→ **W-06a 후에 main.py 는 *import 에러* 또는 *AttributeError* 가 날 가능성 높음**.

→ **W-06a 의 검증은 *import 만* 통과하는 것을 목표**. 실행은 W-06b 후에 검증.

→ 즉 W-06a 후 main.py 는 *반쯤 깨진 상태*. W-06b 가 *나머지를 정리*. 이건 *의도된 중간 상태*.

→ 두 작업 W-06a + W-06b 가 *한 흐름* 으로 묶임. 사이에 main.py 가동 시도 X.

### 대안 — 작업 분할 방식 변경

이론상 *W-06 한 번에* 가 더 안전. 다만 명령서가 *너무 큼* (1500~2500 행). 분할이 안전.

**결정 (이 명령서에서 동결)**: **W-06a 는 사실 base 정정 + import 통과만 보장**. main.py 의 *논리 일관성* 은 W-06b 후에 보장. W-06a 와 W-06b 사이에 *Code 가 main.py 를 가동하지 말 것*.

## 흐름

```
작업 1 (import 정정)
  → 작업 2 (__init__ 정정)
  → 작업 3 (_build_trade_record 정정)
  → 작업 4 (_on_realtime_price 정정)
  → 작업 5 (_on_futures_price 정정)
  → 작업 6 (_emergency_cancel_orders 정정)
  → 작업 7 (_start_monitoring_candidate 의 KIS subscribe 영역 정정)
  → 검증 1~5
  → 보고
```

중간에 멈추지 말 것. 각 작업 사이 검증 X.

---

## [작업 1] import 정정

### 1-1. 폐기할 import (line 36, 39)

```python
from src.core.monitor import TargetMonitor, MonitorState     # 폐기
from src.models.stock import TradeTarget                     # 폐기
```

→ *완전 제거*. (단, 다음 작업의 *다른 import 가 같은 모듈에서 와야* 하면 그 부분만 보존.)

확인: src.core.monitor 는 *완전 폐기* (W-07). src.models.stock 은 *Stock / MarketType 등 다른 클래스 보존*. TradeTarget 만 폐기.

따라서 정정:
- `from src.core.monitor import TargetMonitor, MonitorState` → *완전 제거*
- `from src.models.stock import TradeTarget` → *완전 제거*

### 1-2. 신규 import 추가

import 영역에 다음 추가 (다른 from src.* import 옆):

```python
from src.core.watcher import Watcher, WatcherCoordinator
```

### 1-3. 다른 import 보존

- `from src.core.screener import Screener` 그대로
- `from src.core.trader import Trader` 그대로
- `from src.core.risk_manager import RiskManager` 그대로
- `from src.models.trade import TradeRecord, DailySummary, ExitReason` 그대로
- `from src.storage.database import Database` 그대로
- `from src.utils.notifier import Notifier` 그대로
- `from src.utils.tunnel import CloudflareTunnel` 그대로
- `from src.utils.market_calendar import now_kst` 그대로
- `from src.kis_api.kis import KISAPI` 그대로
- `from src.kis_api.constants import WS_TR_PRICE, WS_TR_FUTURES` 그대로
- `from config.settings import Settings, StrategyParams` 그대로
- 표준 라이브러리 (asyncio, json, os, sys, datetime, pathlib, typing, loguru) 그대로

---

## [작업 2] __init__ 정정

### 2-1. 폐기할 필드 (line 67-75)

기존:
```python
        # ── 모니터 관리 ──
        self._monitors: list[TargetMonitor] = []
        self._active_monitor: Optional[TargetMonitor] = None  # 현재 매매 중인 모니터
        self._subscribed_code: Optional[str] = None  # 현재 WebSocket 구독 중인 종목코드
        self._realtime_callback_registered: bool = False  # 실시간 콜백 등록 여부

        # ── 멀티 트레이드 ──
        self._candidate_pool: list[TradeTarget] = []   # 스크리닝 후보 풀
        self._candidate_index: int = 0                  # 현재 후보 인덱스
        self._completed_codes: set[str] = set()         # 이미 매매 완료된 종목코드
```

→ *완전 제거*. 단 `_realtime_callback_registered` 는 *KIS WebSocket 콜백 등록 추적용* 으로 *보존*. 즉:

변경 후:
```python
        # ── Coordinator (W-05b/c 결과물) ──
        self._coordinator: WatcherCoordinator = WatcherCoordinator(
            params=self.params,
            trader=self.trader,
        )
        self._subscribed_codes: list[str] = []  # 현재 WebSocket 구독 중인 종목코드들
        self._realtime_callback_registered: bool = False  # 실시간 콜백 등록 여부
```

주의:
- `WatcherCoordinator` 인스턴스 생성 시점이 *self.trader 생성 직후* 여야 함 (line 63 후)
- 즉 코드 순서: line 63 self.trader = ... → 그 다음 줄에 _coordinator
- `_realtime_callback_registered` 는 폐기 안 함 (KIS 콜백 등록 추적용)
- `_subscribed_code: Optional[str]` → `_subscribed_codes: list[str]` (3종목 패턴)

### 2-2. 다른 __init__ 영역 보존

- `self.settings` 보존
- `self.params` 보존
- `self.api` 보존
- `self.screener` 보존
- `self.trader` 보존
- `self.risk` 보존
- `self._manual_codes` 보존
- `self._trade_records` 보존
- `self._available_cash` / `self._initial_cash` 보존
- `self._futures_price` 보존
- `self._running` 보존
- `self._network_ok` 보존
- `self._emergency_cancel_done` 보존
- `self.on_state_update` 보존
- `self.notifier` 보존
- `self._db` 보존
- `self._tunnel` 보존

### 2-3. 정정 후 __init__ 예시 (해당 영역만)

```python
    def __init__(self):
        self.settings = Settings()
        self.params = StrategyParams.load()
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
        self._subscribed_codes: list[str] = []  # 현재 WebSocket 구독 중인 종목코드들
        self._realtime_callback_registered: bool = False  # 실시간 콜백 등록 여부

        # ── 수동 종목 입력 ──
        self._manual_codes: list[str] = []

        # ── 거래 기록 (멀티 트레이드 시 즉시 저장) ──
        self._trade_records: list[TradeRecord] = []

        # ── 기타 ──
        self._available_cash: int = 0
        self._initial_cash: int = 0
        self._futures_price: float = 0.0
        self._running: bool = False
        self._network_ok: bool = True
        self._emergency_cancel_done: bool = False
        self.on_state_update = None

        # ── 텔레그램 알림 ──
        self.notifier = Notifier(
            bot_token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
        )

        # ── DB ──
        self._db = Database()

        # ── Cloudflare Tunnel ──
        self._tunnel = CloudflareTunnel(port=self.settings.dashboard_port)
```

---

## [작업 3] _build_trade_record 정정 (line 509-587)

### 3-1. 시그니처 변경

기존:
```python
def _build_trade_record(self, mon: TargetMonitor, today) -> TradeRecord:
```

변경:
```python
def _build_trade_record(self, watcher: Watcher, today) -> TradeRecord:
```

### 3-2. 본문 정정

기존 본문 (line 510-587) 을 다음과 같이 변경:

```python
    def _build_trade_record(self, watcher: Watcher, today) -> TradeRecord:
        """Watcher에서 TradeRecord 생성."""
        pos = self.trader.position

        # 미진입
        if watcher.total_buy_qty <= 0:
            return TradeRecord(
                trade_date=today,
                code=watcher.code,
                name=watcher.name,
                market=watcher.market.value,
                exit_reason=ExitReason.NO_ENTRY,
                rolling_high=watcher.intraday_high,
                trade_mode=self.settings.trade_mode,
            )

        # 매수/매도 데이터
        avg_buy = (
            watcher.total_buy_amount / watcher.total_buy_qty
            if watcher.total_buy_qty > 0 else 0
        )
        pnl = 0.0
        pnl_pct = 0.0
        avg_sell = 0.0
        sell_amount = 0
        sell_time = None
        holding_min = 0.0

        # 포지션에서 매도 데이터 추출
        if pos and pos.sell_orders:
            sell_amount = pos.total_sell_amount
            avg_sell = sell_amount / watcher.total_buy_qty if watcher.total_buy_qty > 0 else 0
            last_sell = pos.sell_orders[-1]
            sell_time = last_sell.filled_at
            pnl = sell_amount - watcher.total_buy_amount
            pnl_pct = (pnl / watcher.total_buy_amount * 100) if watcher.total_buy_amount > 0 else 0
        elif pos:
            # 미청산 (현재가 기준)
            pnl = pos.pnl(watcher.current_price)
            pnl_pct = pos.pnl_pct(watcher.current_price)
            avg_sell = watcher.current_price

        # 보유 시간
        if pos and pos.opened_at and sell_time:
            holding_min = (sell_time - pos.opened_at).total_seconds() / 60
        elif pos and pos.opened_at:
            holding_min = (now_kst() - pos.opened_at).total_seconds() / 60

        # ExitReason 매핑
        reason_map = {
            "hard_stop": ExitReason.HARD_STOP,
            "timeout": ExitReason.TIMEOUT,
            "target": ExitReason.TARGET,
            "futures_stop": ExitReason.FUTURES_STOP,
            "force": ExitReason.FORCE_LIQUIDATE,
            "manual": ExitReason.MANUAL,
        }
        exit_reason = reason_map.get(watcher.exit_reason, ExitReason.NO_ENTRY)

        return TradeRecord(
            trade_date=today,
            code=watcher.code,
            name=watcher.name,
            market=watcher.market.value,
            avg_buy_price=avg_buy,
            total_buy_qty=watcher.total_buy_qty,
            total_buy_amount=watcher.total_buy_amount,
            buy_count=int(watcher.buy1_filled) + int(watcher.buy2_filled),
            first_buy_time=pos.opened_at if pos else None,
            avg_sell_price=avg_sell,
            total_sell_amount=sell_amount,
            sell_time=sell_time,
            exit_reason=exit_reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_minutes=holding_min,
            rolling_high=watcher.intraday_high,
            entry_trigger_price=watcher.target_buy1_price,
            target_price=watcher.target_price,
            trade_mode=self.settings.trade_mode,
        )
```

주의:
- `t = mon.target` 라인 *완전 제거* (변수 t 자체가 사라짐)
- `t.*` → `watcher.*` 매핑 표대로
- `t.buy1_price(self.params)` → `watcher.target_buy1_price` (메서드 → 필드)
- `t.target_price` → `watcher.target_price` (양쪽 다 property/필드)
- `t.avg_price` → 직접 계산식 (`watcher.total_buy_amount / watcher.total_buy_qty`)
- 다른 비즈니스 로직 (pos / pnl / reason_map) *그대로 보존*

---

## [작업 4] _on_realtime_price 정정 (line 749-762)

### 4-1. 시그니처 변경

기존:
```python
def _on_realtime_price(self, data: dict) -> None:
```

변경 (sync → async, Coordinator 호출이 async 라):
```python
async def _on_realtime_price(self, data: dict) -> None:
```

### 4-2. 본문 정정

기존:
```python
    def _on_realtime_price(self, data: dict) -> None:
        """WebSocket 실시간 체결가 수신 콜백."""
        code = data.get("code", "")
        price = data.get("current_price", 0)
        change_pct = data.get("change_pct", 0.0)
        ts = now_kst()

        # 활성 모니터만 가격 업데이트 (멀티 트레이드 시 현재 매매 중인 종목만)
        if self._active_monitor and self._active_monitor.target.stock.code == code:
            mon = self._active_monitor
            mon.target.stock.current_price = price
            mon.target.stock.price_change_pct = change_pct
            mon.on_price(price, ts)
            logger.debug(f"[실시간] {mon.target.stock.name} {price:,}원 ({change_pct:+.2f}%)")
```

변경 후:
```python
    async def _on_realtime_price(self, data: dict) -> None:
        """WebSocket 실시간 체결가 수신 콜백. Coordinator 로 위임."""
        code = data.get("code", "")
        price = data.get("current_price", 0)
        ts = now_kst()

        # Coordinator 가 모든 watcher 라우팅 (ISSUE-036 해소)
        await self._coordinator.on_realtime_price(code, price, ts)
```

주의:
- `change_pct` 변수는 *Coordinator 가 사용 안 함* — 제거
- `self._active_monitor` 참조 *완전 제거*
- `mon.target.stock.current_price = price` 같은 필드 직접 갱신은 *Coordinator 안에서 watcher.current_price 가 처리* — 제거
- `logger.debug` 는 *Coordinator 안에서 처리* — 제거 (또는 보존, 케이스 E-1 참조)
- 함수 자체가 *async 로 변경* — KIS API 콜백이 *sync 함수 또는 async 함수 둘 다 지원하는지* 확인 필요. M-11 결과 line 175 에서 `add_realtime_callback(WS_TR_PRICE, self._on_realtime_price)` 패턴 — 콜백 등록만. 만약 KIS API 가 *sync 콜백만 지원* 하면 이 변경이 *불가능*. 케이스 E-2 참조.

---

## [작업 5] _on_futures_price 정정 (line 764-774)

### 5-1. 시그니처 변경

기존:
```python
def _on_futures_price(self, data: dict) -> None:
```

변경 (sync 그대로):
```python
def _on_futures_price(self, data: dict) -> None:
```

이유: `Coordinator.on_realtime_futures` 는 *sync* 메서드 (W-05c 결정).

### 5-2. 본문 정정

기존:
```python
    def _on_futures_price(self, data: dict) -> None:
        """KOSPI200 선물 실시간 체결가 수신 콜백."""
        price = data.get("current_price", 0.0)
        if price <= 0:
            return
        self._futures_price = price

        # 활성 모니터에 선물 가격 전달
        if self._active_monitor:
            self._active_monitor.on_futures_price(price)
            logger.debug(f"[선물] {price:.2f}")
```

변경 후:
```python
    def _on_futures_price(self, data: dict) -> None:
        """KOSPI200 선물 실시간 체결가 수신 콜백. Coordinator 로 위임."""
        price = data.get("current_price", 0.0)
        if price <= 0:
            return
        self._futures_price = price

        # Coordinator 에 선물 가격 전달
        self._coordinator.on_realtime_futures(price)
```

주의:
- `self._futures_price = price` 는 *main.py 자체 필드 갱신* — 보존 (다른 함수가 사용 가능)
- `self._active_monitor.on_futures_price(price)` → `self._coordinator.on_realtime_futures(price)`
- `logger.debug` 는 *제거 또는 보존* (케이스 E-1)

---

## [작업 6] _emergency_cancel_orders 정정 (line 870-913 근방)

### 6-1. _active_monitor 참조 식별

M-11 결과 line 880-892 의 컨텍스트:
```python
        mon = self._active_monitor
        ...
        target = mon.target
        ...
        if self.trader.pending_buy_orders:
            ...
            try:
                await self.trader.cancel_buy_orders(target)
```

### 6-2. Coordinator.active 패턴으로 변경

`mon = self._active_monitor` → `watcher = self._coordinator.active`

`target = mon.target` → *제거* (watcher 가 target 역할)

`await self.trader.cancel_buy_orders(target)` → `await self.trader.cancel_buy_orders(watcher)`

`mon.target.stock.name` 패턴이 있으면 → `watcher.name`

### 6-3. 정정 패턴

기존 (M-11 의 line 880-892 영역):
```python
        mon = self._active_monitor
        if not mon:
            return
        target = mon.target

        # 미체결 매수 주문이 있으면 취소 시도
        if self.trader.pending_buy_orders:
            logger.critical(f"[{target.stock.name}] 미체결 매수 {len(self.trader.pending_buy_orders)}건 긴급 취소")
            try:
                await self.trader.cancel_buy_orders(target)
                logger.info(f"[{target.stock.name}] 미체결 매수 긴급 취소 완료")
                self.notifier.notify_error(...)
```

변경 후:
```python
        watcher = self._coordinator.active
        if not watcher:
            return

        # 미체결 매수 주문이 있으면 취소 시도
        if self.trader.pending_buy_orders:
            logger.critical(f"[{watcher.name}] 미체결 매수 {len(self.trader.pending_buy_orders)}건 긴급 취소")
            try:
                await self.trader.cancel_buy_orders(watcher)
                logger.info(f"[{watcher.name}] 미체결 매수 긴급 취소 완료")
                self.notifier.notify_error(...)
```

주의:
- `self._active_monitor` → `self._coordinator.active`
- `target = mon.target` 라인 *제거*
- `target.stock.name` → `watcher.name`
- `target` 인자 → `watcher` 인자
- 함수 *전체* 의 다른 부분 (try/except / 로깅 / notifier 호출) *그대로 보존*

### 6-4. 함수 전체에 _active_monitor / target 참조 식별

`_emergency_cancel_orders` 함수 안에 *위 영역 외 다른 _active_monitor / target 참조* 가 있을 수 있음. 함수 전체를 *grep* 해서 모두 정정:
```bash
sed -n '870,913p' src/main.py | grep -n "_active_monitor\|target\."
```

각 라인 매핑 표대로 정정.

---

## [작업 7] _start_monitoring_candidate 의 KIS subscribe 영역 정정

### ⚠ 결정적 — 이 함수 *전체* 가 W-06b 영역

`_start_monitoring_candidate` (line 311 근방) 는 *옛 single active 패턴* 의 핵심. W-06b 에서 *대부분 폐기* 또는 *Coordinator.start_screening 호출 위임* 으로 변경.

W-06a 에서는 *KIS subscribe 영역만* 정정. 즉:

### 7-1. KIS subscribe 패턴 변경 (line 326-345 영역)

기존:
```python
        # 이전 종목 WebSocket 구독 해제
        if self._subscribed_code:
            try:
                await self.api.unsubscribe_realtime([self._subscribed_code])
                logger.debug(f"이전 종목 구독 해제: {self._subscribed_code}")
            except Exception as e:
                logger.warning(f"이전 종목 구독 해제 실패: {e}")
            self._subscribed_code = None

        # 새 모니터 생성
        monitor = TargetMonitor(target, self.params)
        self._active_monitor = monitor
        self._monitors.append(monitor)

        code = target.stock.code

        # WebSocket 실시간 구독
        try:
            await self.api.subscribe_realtime([code])
            self._subscribed_code = code
            logger.info(f"[{target.stock.name}({code})] 실시간 감시 시작 (후보 #{index+1})")
        except Exception as e:
            logger.error(f"실시간 구독 실패: {e}")
```

### 7-2. W-06a 에서는 *최소 정정만*

`_subscribed_code` 가 `_subscribed_codes: list[str]` 로 변경됨. 위 영역의 `_subscribed_code` 참조를 *임시로 수정* — 단, 함수 자체는 *논리 일관성 미보장*.

변경 후 (최소 정정):
```python
        # 이전 종목 WebSocket 구독 해제
        if self._subscribed_codes:
            try:
                await self.api.unsubscribe_realtime(self._subscribed_codes)
                logger.debug(f"이전 종목 구독 해제: {self._subscribed_codes}")
            except Exception as e:
                logger.warning(f"이전 종목 구독 해제 실패: {e}")
            self._subscribed_codes = []

        # 새 모니터 생성 (W-06b 에서 폐기 예정)
        monitor = TargetMonitor(target, self.params)  # ⚠ W-06b 에서 폐기
        self._active_monitor = monitor                  # ⚠ W-06b 에서 폐기
        self._monitors.append(monitor)                  # ⚠ W-06b 에서 폐기

        code = target.stock.code

        # WebSocket 실시간 구독
        try:
            await self.api.subscribe_realtime([code])
            self._subscribed_codes = [code]  # 임시 — W-06b 에서 3종목 패턴
            logger.info(f"[{target.stock.name}({code})] 실시간 감시 시작 (후보 #{index+1})")
        except Exception as e:
            logger.error(f"실시간 구독 실패: {e}")
```

### 7-3. ⚠ 함수 본문에 _monitors / _active_monitor / TargetMonitor / TradeTarget 참조

이 함수는 *옛 자료구조 의존이 가장 큼*. W-06a 시점에 *완전 정정 불가능*. **그대로 둘 것**. *AttributeError / NameError 가 import 시점이 아닌 호출 시점에만 발생* — import 만 통과하면 W-06a 검증 통과.

만약 함수 안에 `TargetMonitor` 클래스 *직접 호출* 이 있어서 *import 자체* 가 깨지면 → 그 라인을 *임시 주석* 처리. W-06b 에서 *Coordinator.start_screening 호출로 대체*.

W-06a 의 작업 7 의 *유일한 책임*: `_subscribed_code` → `_subscribed_codes` 패턴 변경 + 그 외 모든 영역은 *그대로 둠 또는 주석 처리*.

---

## [검증]

### 검증 1 — main.py import OK

```bash
python -c "from src.main import AutoTrader; print('main import OK')"
```

기대: `main import OK`

만약 import 자체가 실패하면 (예: NameError, ImportError) → 작업 7 의 영역에서 *옛 자료구조 직접 사용* 라인을 *임시 주석* 처리. 그 후 다시 import 시도.

### 검증 2 — AutoTrader 인스턴스 생성 (단순)

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('coordinator:', type(at._coordinator).__name__)
print('subscribed_codes:', at._subscribed_codes)
print('manual_codes:', at._manual_codes)
print('available_cash:', at._available_cash)
"
```

기대:
```
coordinator: WatcherCoordinator
subscribed_codes: []
manual_codes: []
available_cash: 0
```

만약 *환경변수 부재로 KIS API 초기화 실패* 면 → 해당 검증을 *skip* 하고 보고에 명시. import 만 통과해도 W-06a 검증 통과.

### 검증 3 — 옛 필드 폐기 확인

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
removed_fields = ['_monitors', '_active_monitor', '_subscribed_code', '_candidate_pool', '_candidate_index', '_completed_codes']
for f in removed_fields:
    has = hasattr(at, f)
    print(f'{f}: {\"잔존\" if has else \"폐기\"}')
"
```

기대: 모든 필드 *폐기*

### 검증 4 — _build_trade_record 시그니처 정정 확인

```bash
python -c "
import inspect
from src.main import AutoTrader
sig = inspect.signature(AutoTrader._build_trade_record)
params_list = list(sig.parameters.keys())
print('params:', params_list)
assert 'watcher' in params_list, 'watcher 인자 누락'
assert 'mon' not in params_list, 'mon 인자 잔존'
print('_build_trade_record 시그니처 정정 OK')
"
```

기대: `params: ['self', 'watcher', 'today']` + assert 통과

### 검증 5 — TargetMonitor / TradeTarget import 잔존 없음

Python 직접 읽기 (S-02-d):

```bash
python -c "
with open('src/main.py', 'r', encoding='utf-8') as f:
    content = f.read()
target_monitor_count = content.count('TargetMonitor')
trade_target_count = content.count('TradeTarget')
print('TargetMonitor count in main.py:', target_monitor_count)
print('TradeTarget count in main.py:', trade_target_count)
"
```

기대:
- W-06a 후 main.py 의 *함수 본문* 에 일부 잔존 가능 (W-06b 영역)
- 다만 *import 영역* 에는 *0건* 이어야 함
- count 가 *전체 0* 이면 더 좋음. 0 이 아니면 *어디 잔존* 인지 보고에 기록

---

## [검증 실패 시 — 멈춤 조건]

- 검증 1 실패 (import 에러) → 멈춤 + 보고. 단, *작업 7 의 옛 자료구조 직접 사용 라인* 이 import 시점에 깨지는 경우 *그 라인을 임시 주석 처리* 후 재시도. 그래도 안 되면 멈춤.
- 검증 2 실패 (인스턴스 생성 에러) → 환경변수 부재로 인한 KIS API 초기화 실패라면 *skip* 가능. 그 외의 에러면 멈춤.
- 검증 3 실패 (옛 필드 잔존) → 멈춤 + 보고
- 검증 4 실패 (시그니처 정정 안 됨) → 멈춤 + 보고
- 검증 5 의 import 영역에 TargetMonitor 또는 TradeTarget 잔존 → 멈춤 + 보고

멈춤 시 변경 *되돌리지 말 것*. 그대로 두고 보고.

---

## [모호한 케이스 — 사전 결정]

### 케이스 E-1: _on_realtime_price / _on_futures_price 의 logger 메시지
기존: `logger.debug(f"[실시간] ...")` 형태로 main.py 에서 로깅.
새 자료구조: Coordinator 안에서 로깅 (W-05b 작성).
중복 가능성 — main.py 의 logger 는 *제거* 권장. 다만 *디버깅 편의* 위해 보존도 OK.

**결정**: main.py 의 logger.debug 는 *제거*. 중복 회피.

### 케이스 E-2: _on_realtime_price 의 sync → async 변경 가능 여부
KIS API 의 add_realtime_callback 이 *sync 콜백만 지원* 가능성. 그 경우 _on_realtime_price 를 *async 로 변경 불가*.

**식별 방법**: src/kis_api/kis.py 의 add_realtime_callback 본문에서 콜백 호출 패턴 확인:
```bash
grep -A 5 "def add_realtime_callback" src/kis_api/kis.py
grep -B 2 -A 10 "callback.*data\|callback(.*)" src/kis_api/kis.py
```

콜백 호출 패턴이 *await callback(...)* 이면 async 지원. 그냥 *callback(...)* 이면 sync 만.

**결정 (이 명령서에서 동결)**: KIS API 가 sync 콜백만 지원한다고 *가정*. _on_realtime_price 는 *sync 그대로*. 단 본문에서 Coordinator.on_realtime_price 를 호출하려면 *async 함수를 sync 에서 호출* 해야 함.

**해결 패턴**: `asyncio.create_task(self._coordinator.on_realtime_price(code, price, ts))` 로 *fire-and-forget*. 즉:

```python
def _on_realtime_price(self, data: dict) -> None:
    """WebSocket 실시간 체결가 수신 콜백. Coordinator 로 위임."""
    code = data.get("code", "")
    price = data.get("current_price", 0)
    ts = now_kst()

    # Coordinator 가 모든 watcher 라우팅 (async fire-and-forget)
    asyncio.create_task(self._coordinator.on_realtime_price(code, price, ts))
```

이 패턴이 *현재 main.py 의 다른 sync→async 호출 패턴과 일관* 하면 사용. 다른 패턴이면 *해당 패턴* 따라감.

만약 KIS API 가 *async 콜백 지원* 이면 _on_realtime_price 를 *async 변경* — fire-and-forget 불필요.

**최종 결정**: KIS API 패턴 식별 후 결정. *식별 결과를 보고에 기록*. 

만약 식별 시간이 너무 오래 걸리면 → *fire-and-forget 패턴* 사용 (sync 함수 + asyncio.create_task).

### 케이스 E-3: __init__ 의 WatcherCoordinator 생성 시점
self.trader 가 *line 63* 에서 생성됨. WatcherCoordinator 는 *그 후* 에 생성해야 함 (trader 인자 전달). 즉 line 64 또는 그 다음 줄.

순서: settings → params → api → screener → trader → **coordinator** → 나머지

### 케이스 E-4: _start_monitoring_candidate 의 함수 자체 보존
W-06b 영역. W-06a 는 *손대지 않음* 원칙. 단, KIS subscribe 영역 (line 326-345) 만 *_subscribed_code → _subscribed_codes* 패턴 변경.

함수 본문의 *나머지* (TargetMonitor 생성 등) 는 *그대로 둠*. 단, 그 라인이 *import 시점에 NameError* 를 일으키면 *임시 주석* 처리.

### 케이스 E-5: _process_signals / _try_next_candidate / _schedule_force_liquidate 등의 _active_monitor / _monitors 참조
W-06a 영역 *아님*. W-06b 가 정정. W-06a 시점에는 *그대로 둠*.

다만 *import 시점* 에는 깨지지 않음 (옛 필드는 폐기됐지만, 옛 필드 *접근* 은 *함수 호출 시점* 에만 발생). import 자체는 OK.

→ 검증 1 통과 가능.

### 케이스 E-6: _get_status 의 _monitors 참조
W-06b 영역. W-06a 시점에 *그대로 둠*. 호출 시 AttributeError 발생 가능 — W-06b 후 정상화.

### 케이스 E-7: _coordinator 필드의 위치
__init__ 안에서 *self.trader 생성 직후*. 작업 2 의 정정 후 코드 그대로.

### 케이스 E-8: typing.Optional 사용
이미 line 28 에 `from typing import Optional` 있음. 추가 import 불필요.

### 케이스 E-9: _subscribed_codes 의 순서/중복
list[str] 그대로. 중복 처리 X. 주의: unsubscribe 시 *모두 한 번에* 처리 가능 → `await self.api.unsubscribe_realtime(self._subscribed_codes)`.

### 케이스 E-10: TradeTarget / TargetMonitor 가 *함수 본문* 에 잔존
W-06b 영역. W-06a 는 *import 영역에서만 폐기*. 함수 본문은 W-06b.

### 케이스 E-11: _build_trade_record 의 호출자
M-11 결과 line 827: `record = self._build_trade_record(mon, now_kst().date())` — _process_signals 안에서 호출.

W-06a 후 호출자가 *여전히 mon 인자 전달* — *AttributeError 발생*. 다만 *호출 시점* 에만 발생. import 통과.

→ W-06b 에서 *호출자도 정정* (mon → watcher).

### 케이스 E-12: KIS subscribe 패턴의 *3종목 동시 구독*
W-06a 시점에는 *_subscribed_codes 필드만* 추가. *3종목 동시 구독 호출* 자체는 W-06b. 즉 *_start_monitoring_candidate 의 line 344* 의 `await self.api.subscribe_realtime([code])` 는 *그대로 둠* (한 종목씩).

W-06b 가 *Coordinator.start_screening + 3종목 subscribe* 패턴으로 변경.

---

## [자체 발견 처리 규칙]

Code 가 작업 중 다음을 발견하면 → 수정하지 말 것 → 보고의 [발견] 섹션에 1~3줄 기록:
- main.py 의 잠재적 버그
- M-11 결과와 다른 코드 발견
- watcher.py / trader.py 와의 결합 문제
- KIS API 패턴 발견 (특히 sync/async 콜백)
- W-06b 에서 처리해야 할 추가 영역

---

## [보고]

### A. 변경 라인
- 작업 1 (import): 추가 / 제거 라인 번호
- 작업 2 (__init__): 추가 / 제거 라인 번호 (Coordinator 추가, 옛 필드 6개 제거)
- 작업 3 (_build_trade_record): 시그니처 + 본문 정정 라인
- 작업 4 (_on_realtime_price): 본문 정정 라인 (sync/async 결정 결과 명시)
- 작업 5 (_on_futures_price): 본문 정정 라인
- 작업 6 (_emergency_cancel_orders): _active_monitor → coordinator.active 정정 라인
- 작업 7 (_start_monitoring_candidate): _subscribed_code → _subscribed_codes 정정 라인 (그 외 보존)

### B. 라인 수
- main.py: before / after / 차이

### C. 검증 결과
- 검증 1: 성공/실패 + 출력
- 검증 2: 성공/실패 + 출력 (또는 skip 사유)
- 검증 3: 성공/실패 + 출력 (옛 필드 폐기)
- 검증 4: 성공/실패 + 출력 (_build_trade_record 시그니처)
- 검증 5: 성공/실패 + 출력 (TargetMonitor / TradeTarget count)

### D. 다른 파일 수정 여부
*반드시 main.py 만*

### E. KIS API 콜백 패턴 식별 결과 (케이스 E-2)
- KIS API 가 sync 콜백만 지원? async 도 지원?
- _on_realtime_price 를 sync 그대로 + asyncio.create_task 패턴?
- 또는 async 로 변경?

### F. 옛 자료구조 잔존 영역 (W-06b 입력)
다음 함수들의 *옛 자료구조 참조* 가 *그대로 잔존* 함을 보고:
- `_on_screening` (line 309: _start_monitoring_candidate 호출)
- `_start_monitoring_candidate` (TargetMonitor 생성 등)
- `_try_next_candidate` (잔고 갱신)
- `_process_signals` (_active_monitor 의존)
- `_schedule_force_liquidate` (_monitors 순회)
- `_schedule_market_close` (_monitors 순회)
- `_monitor_loop_runner` (?)
- `_get_status` (_monitors 순회)

각 함수마다 *임시 주석 처리* 한 라인 / *그대로 보존* 한 라인 명시.

### G. 모호한 케이스 처리 결과 (E-1 ~ E-12 중 발생한 것)

### H. [발견] 섹션 — 자체 발견 사항 (있으면)

### I. W-06b 진입 준비
- main.py 가 *반쯤 깨진 상태* (의도된 중간 상태)
- import 통과 + AutoTrader 인스턴스 생성 가능
- W-06b 에서 처리할 함수 목록

---

## [추가 금지 — 자동 모드 강화]

- main.py 외 어떤 파일도 수정 금지
- watcher.py / trader.py 변경 금지 (W-05 결과물)
- _on_screening 본문 변경 금지 (W-06b 영역)
- _start_monitoring_candidate 본문 변경 금지 (단, KIS subscribe 영역 + _subscribed_code 패턴만 정정)
- _try_next_candidate 본문 변경 금지 (W-06b)
- _process_signals 본문 변경 금지 (W-06b)
- _schedule_force_liquidate 본문 변경 금지 (W-06b)
- _schedule_market_close 본문 변경 금지 (W-06b)
- _monitor_loop_runner 본문 변경 금지 (W-06b)
- _get_status 본문 변경 금지 (W-06b)
- run() 본문 변경 금지 (단, 검증 1~5 가 깨지면 *최소* 정정 OK)
- 새 함수 / 새 클래스 추가 금지 (단, 작업 2 의 _coordinator 필드 추가 OK)
- try-except 추가 금지
- 타입 힌트 추가 금지 (단, 시그니처 변경 영역은 OK)
- 새 import 금지 (단, watcher import 추가는 명령서 명시)
- 로깅 메시지 변경 금지 (단, 케이스 E-1 의 logger 제거 OK)
- 영어 메시지 추가 금지
- monitor.py 수정 금지 (W-07 영역)
- TradeTarget 클래스 자체 수정 금지 (W-07 영역)
- commit 금지
- 임시 파일 / scratch 파일 생성 금지
- W-06b / W-07 / W-08 영역 손대지 말 것
