# R-17 — 시세 채널 자동 분기 (ChannelResolver) — KRX 고정 발주 단순화

> **redesign 규모**: medium (신규 클래스 1개 + 5 파일 수정)
> **수석님 5개 결정 통합**: 10초 윈도우 + 2D Dual subscribe + 3A-2 메타 4개 + **D (KRX 고정 발주)** + ChannelResolver 신규
> **작업 주체**: SONNET (이 명령서 단독으로 작업 가능, self-contained)
> **변경 이력**:
> - v1 (작성 2026-04-23) : Decision 4 = C (SOR 분기) 기준
> - v2 (수정 2026-04-23) : Decision 4 = D (KRX 고정) 로 단순화. trader.py 제외, 결함 8/10/11 자동 해소.

---

## [미션]

KIS 실시간 시세 채널을 종목별로 자동 분기:
- **NXT 활성 종목** → `H0UNCNT0` (통합 = SOR 집계, 양 거래소 체결가 push)
- **KRX-only 종목** → `H0STCNT0` (KRX 전용)

분기 결정은 종목별 push 누적 관측 (10초 윈도우, dual subscribe) 으로 자동 판정.

**주문 EXCG 는 KRX 고정** (수석님 결정 D): 시세 채널은 자동 분기하되, 발주/취소/매도는 모두 EXCG_ID_DVSN_CD = "KRX" 유지. SOR 의 잠재 호가 개선 (1틱 = 0.05~0.2%) 보다 운영 단순성 우선.

---

## [제약]
- §5.6 협업 규칙 8원칙 엄수
- 화이트리스트 6 파일만 수정 (블랙리스트 영역 절대 손대지 말 것)
- 자동 진행 모드 X — 모호한 케이스에서 멈춤 + 보고
- 코드 작성 후 검증 단계까지 완료, 그 이후 git commit + 운영 배포는 수석님이 직접

---

## [원칙]
- **코드 수정 전**: 영향/변경점 설명 → 승인받은 후 작성 (CLAUDE.md feedback rule)
- **사실 base 우선**: 추측 금지, grep / 읽기로 확인
- **원자 작업**: 각 작업 단계 완료 후 다음으로
- **검증 필수**: py_compile + import + 단위 테스트 + grep
- **단순함 우선**: 명령서 범위 외 리팩토링 / 추가 기능 / 추측 기반 설계 금지

---

## [배경 — 사실 base]

### 1. 운영 측정 (2026-04-23 KST 13:41~13:46, 별도 approval_key 세션 5분 관측)

| 그룹 | 표본 | tick 0건 | 정상 수신 | 0건율 |
|---|---|---|---|---|
| KRX_only (NXT 미거래) | 17 | 15 | 2 | **88.2%** |
| NXT 거래 가능 (대조군) | 3 | 0 | 3 | 0% |

대조군 tick 수: NAVER(035420) 85건 / 에이피알(278470) 34건 / 삼성중공업(010140) 790건.

**결론**: H0UNCNT0 은 NXT 거래 가능 종목에 한해 송신. KRX_only 종목은 구독 성공 (rt_cd=0) 응답해도 송신 0건.

**xlsx 검증** (`Obsidian/한국투자증권_오픈API_전체문서_20260416_030007.xlsx`, 시트 `국내주식 실시간체결가 (통합)` 73~ 행에 본문 추가됨):
- "송신 범위: NXT(대체거래소) 거래 가능 종목에 한함"
- "미송신: KRX 단독 상장 종목 (NXT 미거래) → 구독 성공해도 tick 0건"

### 2. 현재 코드 상태

| 위치 | 현재 | R-17 후 |
|---|---|---|
| `constants.py:59` | `WS_TR_PRICE = "H0UNCNT0"` (단일) | 3개 TR_ID 상수 (UN/ST/NX) + alias 유지 |
| `kis.py:_subscribed_codes` | `set[str]` | `dict[str, str]` (code → tr_id) |
| `kis.py:807-833 subscribe_realtime` | `WS_TR_PRICE` 하드코딩 | `tr_id: str` 파라미터 추가 |
| `kis.py:835-862 unsubscribe_realtime` | `WS_TR_PRICE` 하드코딩 | `tr_id: str` 파라미터 추가 |
| `kis.py:977-993 _ws_receiver` | `if tr_id == WS_TR_PRICE` 단일 분기 | UN/ST 모두 처리 (콜백 단일 라우팅) |
| `kis.py:1041-1057 재접속 재구독` | `WS_TR_PRICE` 하드코딩 | `_subscribed_codes` dict 의 tr_id 사용 |
| `kis.py:529 buy_order` | `EXCG_ID_DVSN_CD = "KRX"` 하드코딩 | **그대로 유지** (Decision D) |
| `kis.py:563 sell_order` | `EXCG_ID_DVSN_CD = "KRX"` 하드코딩 | **그대로 유지** (Decision D) |
| `kis.py:599 cancel_order` | EXCG 미처리 | **그대로 유지** (Decision D, KIS default = KRX 와 정합) |
| `watcher.py:629 WatcherCoordinator.__init__` | `(params, trader)` | + `set_channel_resolver(resolver)` setter |
| `Watcher dataclass` | 채널 메타 필드 0개 | 4 필드 추가 (channel_used 등) |

### 3. M-LIVE-02-FIX 선례 (kis.py:463-503)

`get_program_trade` 는 이미 UN→J fallback 패턴 적용:
- 1차: `FID_COND_MRKT_DIV_CODE = "UN"` 호출
- 응답 빈값 시 → 2차: `FID_COND_MRKT_DIV_CODE = "J"` 재시도

이 fallback 은 REST 측면. R-17 은 WebSocket 측면 분기로 별개 영역.

### 4. ORD_EXG_GB 인프라 (이미 존재, 로깅 강화만 추가)

`kis.py:80-109 _EXECUTION_FIELDS` 의 index 19 에 `ORD_EXG_GB` 정의 존재:
- 1: KRX
- 2: NXT
- 3: SOR-KRX (실주문 KRX 라우팅)
- 4: SOR-NXT (실주문 NXT 라우팅)

`kis.py:736-745 _parse_execution_body` 도 이미 추출. R-17 작업 4 에서 main.py `_process_execution_notify` 의 로깅만 추가 (관측용 — 실제 발주는 KRX 고정이므로 항상 1 또는 3 응답 예상).

### 5. WatcherCoordinator 의존성 주입 패턴

기존: `__init__(params, trader)` + setter 패턴 (`set_available_cash`, `set_exit_callback`, `set_risk_manager`).
→ R-17: 동일 setter 패턴으로 `set_channel_resolver(resolver)` 추가 (constructor 시그니처 변경 X).

### 6. main.py 의 시세 흐름 단일 진입점

- `main.py:355` 만이 `await self.api.subscribe_realtime(codes)` 호출
- `main.py:405-412 _on_realtime_price` 가 콜백 진입점 → `coordinator.on_realtime_price` 위임
- → R-17: ChannelResolver 가 dual subscribe 를 담당 → main.py:355 의 호출은 **삭제** + 그 자리에 `await self._channel_resolver.start(codes)` 호출

### 7. 5개 결정 (수석님 확정)

| # | 결정 | 확정값 |
|---|---|---|
| 1 | 분기 윈도우 시간 | **10초** |
| 2 | 재구독 절차 | **2D Dual subscribe임시** (UN+ST 동시 구독 10초 → 분기 결정 후 불필요한 쪽 unsubscribe, 시세 공백 0) |
| 3 | 메타 필드 | **3A-2** (Watcher 4 필드 + Dashboard 표시) |
| 4 | 주문 EXCG | **D — KRX 고정** (모든 발주/취소/매도 EXCG = "KRX") |
| 5 | 분기 결정 위치 | **ChannelResolver 신규 클래스** (medium redesign) |

### 8. Decision D 의 영향 (결함 자동 해소)

KRX 고정 채택으로 다음 결함 카테고리가 자동 소멸:
- **결함 10 (sell_order EXCG 2 곳 분기)**: 매도가 항상 KRX → 분기 불필요
- **결함 11 (cancel_order EXCG 부정합)**: 원주문 KRX, cancel KRX → 정합

### 9. 결함 8 (Dual Ghost High) 처리 — 작업 4 에 명시

dual subscribe 윈도우 (10초) 동안 UN+ST 가 양쪽 push 시 watcher.on_tick 이 두 번 호출되면 intraday_high 가 채널 간 호가차로 부풀어 오름.

**채택**: ChannelResolver active 중 main.py `_on_realtime_price` 가 coordinator 호출 skip (작업 4-2). 09:50 ~ 09:50:10 의 10초만 watcher 시세 무시 — 신고가 감시 시작은 09:55 이므로 4분 50초 여유, 안전.

---

## [화이트리스트]

다음 6 파일만 수정 가능:

1. **NEW** `src/core/channel_resolver.py` — ChannelResolver 신규 클래스 (~150행)
2. `src/kis_api/constants.py` — TR_ID 상수 분리
3. `src/kis_api/kis.py` — subscribe/unsubscribe tr_id 파라미터화 + _subscribed_codes dict 화 + 재접속 재구독 dict 사용
4. `src/core/watcher.py` — Watcher 4 메타 필드 추가 + WatcherCoordinator.set_channel_resolver setter
5. `src/main.py` — Coordinator 에 resolver 주입 + 시세 흐름 정합 + 체결통보 ORD_EXG_GB 로깅
6. `src/dashboard/app.py` — `/api/status` 응답에 channel_used 포함 (Watcher 표시)

---

## [블랙리스트] — 절대 수정 금지

- `src/core/trader.py` (Decision D 채택으로 EXCG 분기 불필요)
- `config/strategy_params.yaml` (R-17 은 매매 명세 변경 0건)
- `config/settings.py` (yaml 변경 없으므로)
- `src/core/screener.py` (스크리닝 로직 무관)
- `src/core/risk_manager.py` (리스크 로직 무관)
- `src/storage/database.py` (스키마 변경 0건)
- `src/utils/notifier.py`, `src/utils/price_utils.py`, `src/utils/market_calendar.py`
- `src/models/*.py` (TradeRecord/Position 변경 0건)
- 운영 환경 (134.185.115.229, /home/ubuntu/AUTOTRADE/) — 표준 금지 조항
- `Obsidian/` (vault) — 코드 작업과 무관
- 기존 테스트 / 백테스트 / 백업 파일

---

## [작업 0] 사전 백업

```bash
cd C:\Users\terryn\AUTOTRADE\.claude\worktrees\reverent-hopper-b59634
mkdir -p backups/R-17-pre/
cp src/kis_api/constants.py backups/R-17-pre/
cp src/kis_api/kis.py backups/R-17-pre/
cp src/core/watcher.py backups/R-17-pre/
cp src/main.py backups/R-17-pre/
cp src/dashboard/app.py backups/R-17-pre/
```

검증: `ls backups/R-17-pre/` 5 파일 확인.

---

## [작업 1] KIS 레이어 — 채널 분기 지원 (시세만)

### 1-1. `constants.py` (라인 ~58~62)

기존:
```python
WS_TR_PRICE = "H0UNCNT0"                # 실시간 체결가 (통합 KRX+NXT)
```

변경:
```python
# ── WebSocket 시세 TR ID (R-17: 종목별 채널 분기) ──────────────
WS_TR_PRICE_UN = "H0UNCNT0"             # 통합 (NXT 활성 종목, SOR 집계)
WS_TR_PRICE_ST = "H0STCNT0"             # KRX 전용 (KRX-only 종목)
WS_TR_PRICE_NX = "H0NXCNT0"             # NXT 전용 (현재 미사용, 예비)

# 기존 호환 (deprecated, 점진 제거 예정)
WS_TR_PRICE = WS_TR_PRICE_UN
```

> **이유**: 기존 `WS_TR_PRICE` 참조가 남아 있으므로 alias 로 유지. 작업 내에서 모두 `WS_TR_PRICE_UN/ST` 직접 사용으로 교체 후 alias 제거 가능 시 제거 (작업 1 끝까지 alias 유지).

### 1-2. `kis.py` `_subscribed_codes` 자료형 변경

`__init__` 또는 정의부에서 (grep 으로 위치 확인):

기존:
```python
self._subscribed_codes: set[str] = set()
```

변경:
```python
self._subscribed_codes: dict[str, str] = {}  # code → tr_id (R-17)
```

> **주의**: `clear_subscribed_codes` 메서드가 set / dict 호환되어야 함. `self._subscribed_codes.clear()` 라면 그대로 동작. 다른 set 전용 메서드 (`.add()`, `.remove()`) 사용처가 있다면 dict 방식으로 변경 필요. grep 으로 확인.

### 1-3. `kis.py:807-833 subscribe_realtime`

시그니처 변경:
```python
async def subscribe_realtime(self, codes: list[str], tr_id: str = WS_TR_PRICE_UN):
    """실시간 체결가 구독.

    Args:
        codes: 구독 종목 코드 리스트
        tr_id: WS_TR_PRICE_UN (통합) 또는 WS_TR_PRICE_ST (KRX 전용)
    """
    if not self._ws_key:
        await self._get_ws_key()

    if not self._ws:
        self._ws = await websockets.connect(...)
        self._ws_task = asyncio.create_task(self._ws_receiver())

    for code in codes:
        self._subscribed_codes[code] = tr_id   # set.add → dict 등록
        msg = {
            "header": {...},
            "body": {
                "input": {
                    "tr_id": tr_id,             # WS_TR_PRICE → tr_id
                    "tr_key": code,
                }
            },
        }
        await self._ws.send(json.dumps(msg))
        logger.info(f"실시간 구독: {code} (tr_id={tr_id})")
```

### 1-4. `kis.py:835-862 unsubscribe_realtime`

시그니처 변경:
```python
async def unsubscribe_realtime(self, codes: list[str] | None = None, tr_id: str | None = None):
    """실시간 구독 해제.

    Args:
        codes: 해제할 종목 코드. None 이면 전체 해제 (ws close)
        tr_id: 명시 시 해당 tr_id 만 해제 (dual subscribe 정리 용).
               None 이면 _subscribed_codes 의 tr_id 사용.
    """
    if not self._ws:
        return

    if codes:
        for code in codes:
            target_tr = tr_id or self._subscribed_codes.get(code)
            if not target_tr:
                continue
            # 등록 정보 갱신 (dual → single 정리 시 다른 tr_id 가 있으면 유지)
            if tr_id is None or self._subscribed_codes.get(code) == tr_id:
                self._subscribed_codes.pop(code, None)
            msg = {
                "header": {..., "tr_type": "2", ...},
                "body": {
                    "input": {
                        "tr_id": target_tr,
                        "tr_key": code,
                    }
                },
            }
            await self._ws.send(json.dumps(msg))
            logger.info(f"실시간 해제: {code} (tr_id={target_tr})")
    else:
        # 전체 해제 (기존과 동일)
        if self._ws_task:
            self._ws_task.cancel()
        await self._ws.close()
        self._ws = None
```

> **dual subscribe 처리 패턴**: `_subscribed_codes` dict 는 한 종목당 한 값만 보관. ChannelResolver 가 UN→ST 순서로 `subscribe_realtime` 두 번 호출 시 두 번째 호출 (ST) 이 dict 를 덮어씀. 시세 push 자체는 양쪽 모두 KIS 서버에 등록되어 정상 수신. 분기 결정 후 ChannelResolver 가 채택된 tr_id 로 dict 를 재정정 (작업 2 `_resolve` 의 마지막 단계).

### 1-5. `kis.py:977-993 _ws_receiver` UN/ST 양쪽 처리

기존:
```python
if tr_id == WS_TR_PRICE:
    fields = body.split("^")
    ...
    for cb in self._realtime_callbacks.get(WS_TR_PRICE, []):
        cb(price_data)
```

변경:
```python
if tr_id in (WS_TR_PRICE_UN, WS_TR_PRICE_ST):
    fields = body.split("^")
    if len(fields) >= 12:
        price_data = {
            "code": fields[0],
            "time": fields[1],
            "current_price": int(fields[2]),
            "change_sign": fields[3],
            "change": int(fields[4]),
            "change_pct": float(fields[5]),
            "volume": int(fields[9]) if len(fields) > 9 else 0,
            "tr_id": tr_id,                     # R-17: ChannelResolver 분기 판정용
        }
        # 단일 콜백 라우팅 (UN/ST 모두 같은 콜백 키로)
        for cb in self._realtime_callbacks.get(WS_TR_PRICE_UN, []):
            try:
                cb(price_data)
            except Exception as e:
                logger.error(f"실시간 콜백 에러: {e}")
```

> **콜백 등록 키**: 기존 `WS_TR_PRICE` 단일 → 통합하여 `WS_TR_PRICE_UN` 키로만 등록 (alias 로 동작). main.py 가 `add_realtime_callback(WS_TR_PRICE_UN, cb)` 한 번 등록 → UN/ST 양쪽 push 모두 같은 cb 호출 (cb 내부에서 `tr_id` 로 분기).

### 1-6. `kis.py:1041-1057 재접속 재구독` dict 의 tr_id 사용

기존:
```python
for code in list(self._subscribed_codes):
    msg = {
        ...
        "body": {
            "input": {
                "tr_id": WS_TR_PRICE,
                "tr_key": code,
            }
        },
    }
    await self._ws.send(json.dumps(msg))
    logger.info(f"재구독 완료: {code}")
```

변경:
```python
for code, tr_id in list(self._subscribed_codes.items()):
    msg = {
        ...
        "body": {
            "input": {
                "tr_id": tr_id,
                "tr_key": code,
            }
        },
    }
    await self._ws.send(json.dumps(msg))
    logger.info(f"재구독 완료: {code} (tr_id={tr_id})")
```

> **참고**: 재접속 시점이 dual 윈도우 (10초) 안이면 한 쪽 채널만 복구될 수 있음. 분기 결정 후의 단일 채널 복구는 정합. dual 윈도우 안 재접속은 매우 드문 케이스 (10초만 발생) 이므로 별도 처리 없이 단일 복구 채택.

### 1-7. `kis.py:529 buy_order` / `kis.py:563 sell_order` / `kis.py:599 cancel_order`

**변경 없음** (Decision D — KRX 고정 유지).

세 메서드 모두 EXCG_ID_DVSN_CD 가 "KRX" 하드코딩 또는 미지정 (KIS default = KRX) 으로 정합. 시그니처 그대로.

---

## [작업 2] ChannelResolver 신규 클래스 — `src/core/channel_resolver.py` (~160행)

### 책임
- 스크리닝 후 N 종목에 대해 **dual subscribe** (UN + ST 동시 구독)
- 종목별 tick push 누적 카운트 (10초 윈도우)
- 윈도우 종료 후 분기 결정:
  - UN tick 수신 → 채널 = UN, ST 측 unsubscribe
  - UN 0건 + ST 수신 → 채널 = ST, UN 측 unsubscribe
  - 양쪽 0건 → 폴백 = UN (NXT 미상장 가정 fallthrough, 다음 push 가능성 보존)
- Watcher 의 `channel_used` 메타 필드 setter 호출 (콜백 패턴)
- **수동/정규 스크리닝 race 처리**: start() 가 active 중 호출되면 reset 후 재시작 (결함 6/7)

### 구현 골격

```python
"""ChannelResolver — 종목별 시세 채널 자동 분기 (R-17).

스크리닝 후 호출되어:
1. 종목들에 대해 UN + ST dual subscribe (10초 윈도우)
2. 종목별 push 누적 카운트
3. 10초 후 분기 결정 → 불필요한 채널 unsubscribe
4. Watcher 의 channel_used / channel_decided_at / push_count 메타 갱신
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from loguru import logger

from src.kis_api.constants import WS_TR_PRICE_UN, WS_TR_PRICE_ST
from src.utils.market_calendar import now_kst


# 분기 윈도우 (수석님 결정 1: 10초)
DUAL_WINDOW_SEC = 10.0


@dataclass
class _PushCount:
    """종목별 dual subscribe 윈도우 동안의 push 누적."""
    code: str
    un_count: int = 0
    st_count: int = 0
    last_un_ts: Optional[datetime] = None
    last_st_ts: Optional[datetime] = None


class ChannelResolver:
    """시세 채널 자동 분기 (UN ↔ ST).

    사용 패턴:
        resolver = ChannelResolver(kis_api)
        resolver.set_channel_decided_callback(coordinator._on_channel_decided)
        resolver.set_active_query(lambda: not resolver.is_active())  # 외부 노출
        await resolver.start([code1, code2, ...])
        # ... 10초 후 분기 자동 결정 + Watcher 메타 갱신 + 채널 정리
    """

    def __init__(self, kis_api):
        self._kis = kis_api
        self._counts: dict[str, _PushCount] = {}
        self._resolved: dict[str, str] = {}  # code → 채택된 tr_id
        self._timer_task: Optional[asyncio.Task] = None
        self._on_decided: Optional[Callable[[str, str, int, int, datetime], None]] = None
        self._active: bool = False

    def set_channel_decided_callback(self, cb: Callable[[str, str, int, int, datetime], None]) -> None:
        """채널 결정 콜백 등록.

        시그니처: cb(code, channel_used, un_count, st_count, decided_at)
        """
        self._on_decided = cb

    def is_active(self) -> bool:
        """dual 윈도우 진행 중 여부 (main.py 의 시세 라우팅 가드용)."""
        return self._active

    async def start(self, codes: list[str]) -> None:
        """N 종목에 대해 dual subscribe 시작 + 10초 타이머.

        active 중 재호출 시 → reset 후 새 codes 로 재시작 (수동/정규 스크리닝 race 처리).
        """
        if self._active:
            logger.warning(
                f"[ChannelResolver] 이미 active — reset 후 재시작 ({len(codes)} 종목)"
            )
            self.reset()

        if not codes:
            logger.info("[ChannelResolver] codes 비어있음 — skip")
            return

        self._active = True
        for code in codes:
            self._counts[code] = _PushCount(code=code)

        # Dual subscribe (UN 먼저, 그 다음 ST)
        # _subscribed_codes 는 dict — 두 번째 호출이 덮어씀.
        # ChannelResolver 결정 후 _resolve() 에서 채택된 tr_id 로 정정.
        await self._kis.subscribe_realtime(codes, tr_id=WS_TR_PRICE_UN)
        await self._kis.subscribe_realtime(codes, tr_id=WS_TR_PRICE_ST)
        logger.info(
            f"[ChannelResolver] dual subscribe 시작 ({len(codes)} 종목, {DUAL_WINDOW_SEC}초)"
        )

        # 10초 타이머
        self._timer_task = asyncio.create_task(self._wait_and_resolve())

    def on_realtime_price(self, price_data: dict) -> None:
        """실시간 시세 수신 핸들러 (main.py _on_realtime_price 가 위임).

        분기 결정 전 (active=True) 에만 카운트. 결정 후에는 무시 (Coordinator 가 처리).
        """
        if not self._active:
            return
        code = price_data.get("code")
        tr_id = price_data.get("tr_id")
        if code not in self._counts:
            return
        ts = now_kst()
        if tr_id == WS_TR_PRICE_UN:
            self._counts[code].un_count += 1
            self._counts[code].last_un_ts = ts
        elif tr_id == WS_TR_PRICE_ST:
            self._counts[code].st_count += 1
            self._counts[code].last_st_ts = ts

    async def _wait_and_resolve(self) -> None:
        """10초 대기 후 분기 결정."""
        try:
            await asyncio.sleep(DUAL_WINDOW_SEC)
            await self._resolve()
        except asyncio.CancelledError:
            logger.info("[ChannelResolver] 타이머 취소됨")
        except Exception as e:
            logger.error(f"[ChannelResolver] 타이머 오류: {e}")
        finally:
            self._active = False

    async def _resolve(self) -> None:
        """종목별 분기 결정 + 불필요한 채널 unsubscribe + 콜백 호출."""
        decided_at = now_kst()
        un_to_drop = []
        st_to_drop = []

        for code, c in self._counts.items():
            if c.un_count > 0:
                channel = WS_TR_PRICE_UN
                st_to_drop.append(code)
            elif c.st_count > 0:
                channel = WS_TR_PRICE_ST
                un_to_drop.append(code)
            else:
                # 양쪽 0건 — UN 폴백 (다음 push 가능성)
                channel = WS_TR_PRICE_UN
                st_to_drop.append(code)
                logger.warning(f"[ChannelResolver] {code}: dual 윈도우 양쪽 0건 — UN 폴백")

            self._resolved[code] = channel
            logger.info(
                f"[ChannelResolver] {code}: 채널={channel} "
                f"(UN={c.un_count} ST={c.st_count})"
            )

            if self._on_decided:
                try:
                    self._on_decided(code, channel, c.un_count, c.st_count, decided_at)
                except Exception as e:
                    logger.error(f"[ChannelResolver] 콜백 오류 ({code}): {e}")

        if un_to_drop:
            await self._kis.unsubscribe_realtime(un_to_drop, tr_id=WS_TR_PRICE_UN)
        if st_to_drop:
            await self._kis.unsubscribe_realtime(st_to_drop, tr_id=WS_TR_PRICE_ST)

        # _subscribed_codes 정합 — 채택된 tr_id 로 재등록
        for code, channel in self._resolved.items():
            self._kis._subscribed_codes[code] = channel

        logger.info(f"[ChannelResolver] 분기 결정 완료 ({len(self._resolved)} 종목)")

    def get_channel(self, code: str) -> Optional[str]:
        """결정된 채널 조회 (없으면 None)."""
        return self._resolved.get(code)

    def reset(self) -> None:
        """일별/재시작 리셋."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._counts.clear()
        self._resolved.clear()
        self._active = False
```

### 검증 (코드 작성 후)
- `python -m py_compile src/core/channel_resolver.py`
- import 검사: `python -c "from src.core.channel_resolver import ChannelResolver; print('OK')"`

---

## [작업 3] Watcher 메타 4 필드 + WatcherCoordinator setter

### 3-1. `Watcher` dataclass 에 4 필드 추가 (수석님 결정 3: 3A-2)

`src/core/watcher.py` 의 `@dataclass class Watcher:` 안에 추가:

```python
    # === R-17: 채널 분기 메타 (3A-2) ===
    channel_used: Optional[str] = None              # WS_TR_PRICE_UN / WS_TR_PRICE_ST
    channel_decided_at: Optional[datetime] = None   # 분기 결정 시각
    un_push_count_at_decision: int = 0              # 윈도우 동안 UN push 수
    st_push_count_at_decision: int = 0              # 윈도우 동안 ST push 수
```

### 3-2. WatcherCoordinator 에 setter + 콜백 추가

```python
    # __init__ 끝부분에 추가
    self._channel_resolver = None  # R-17: ChannelResolver (main.py 에서 주입)

    def set_channel_resolver(self, resolver) -> None:
        """R-17: 채널 분기 resolver 주입. main.py 가 호출.

        스크리닝 후 main.py 가 resolver.start(codes) 호출 +
        resolver 의 결정 콜백 → Coordinator._on_channel_decided 자동 등록.
        """
        self._channel_resolver = resolver
        resolver.set_channel_decided_callback(self._on_channel_decided)

    def _on_channel_decided(
        self, code: str, channel: str, un_count: int, st_count: int, decided_at: datetime
    ) -> None:
        """ChannelResolver 콜백 — Watcher 메타 갱신.

        주의: 동기 함수 (resolver 가 동기 호출). watcher 가 watchers 목록에 없으면 무시.
        """
        watcher = next((w for w in self.watchers if w.code == code), None)
        if watcher is None:
            logger.warning(f"[Coordinator] _on_channel_decided: {code} watcher 없음 — skip")
            return
        watcher.channel_used = channel
        watcher.channel_decided_at = decided_at
        watcher.un_push_count_at_decision = un_count
        watcher.st_push_count_at_decision = st_count
        logger.info(
            f"[Coordinator] {code} 채널 메타 갱신: {channel} "
            f"(UN={un_count} ST={st_count})"
        )
```

> **start_screening 변경 없음**: ChannelResolver.start() 호출은 main.py:355 위치에서 `subscribe_realtime` 을 대체 (작업 4-3). Coordinator 의 start_screening 자체는 시그니처/로직 그대로 유지.

---

## [작업 4] main.py 통합

### 4-1. `__init__` 에 ChannelResolver 생성 + Coordinator 주입

```python
from src.core.channel_resolver import ChannelResolver

# self._coordinator = WatcherCoordinator(...) 다음 행에 추가
self._channel_resolver = ChannelResolver(self.api)
self._coordinator.set_channel_resolver(self._channel_resolver)
```

### 4-2. `_on_realtime_price` 변경 — Resolver 위임 + ghost high 차단

기존 (예시):
```python
def _on_realtime_price(self, price_data: dict):
    code = price_data["code"]
    price = price_data["current_price"]
    ts = now_kst()
    asyncio.create_task(self._coordinator.on_realtime_price(code, price, ts))
```

변경:
```python
def _on_realtime_price(self, price_data: dict):
    # R-17 (1): ChannelResolver 가 active 면 카운트 (분기 판정용)
    if self._channel_resolver:
        self._channel_resolver.on_realtime_price(price_data)

    # R-17 (2): dual 윈도우 (10초) 동안은 watcher 라우팅 skip (ghost high 방지)
    if self._channel_resolver and self._channel_resolver.is_active():
        return

    code = price_data["code"]
    price = price_data["current_price"]
    ts = now_kst()
    asyncio.create_task(self._coordinator.on_realtime_price(code, price, ts))
```

> **ghost high 차단 근거**: dual 시점 (09:50 ~ 09:50:10) 은 신고가 감시 시작 (09:55) 이전 — watcher 의 신고가 추적 누락 0초. 시세 무시 안전.

### 4-3. main.py:355 의 `subscribe_realtime` 호출 교체

기존 (정확한 라인은 grep 으로 재확인):
```python
await self.api.subscribe_realtime(codes)
```

변경:
```python
# R-17: ChannelResolver 가 dual subscribe 를 대체
await self._channel_resolver.start(codes)
```

> **확인 필요**: `subscribe_realtime` 호출 위치가 main.py:355 한 군데만 인지 grep 으로 재확인. 만약 다른 위치에서도 호출되면 그 위치 처리도 명시 보고.

### 4-4. 체결통보 ORD_EXG_GB 로깅 추가

`_process_execution_notify` 에서 ORD_EXG_GB 추출 + 로깅:
```python
ord_exg_gb = exec_data.get("ORD_EXG_GB", "")
exg_label = {"1": "KRX", "2": "NXT", "3": "SOR-KRX", "4": "SOR-NXT"}.get(ord_exg_gb, f"UNK({ord_exg_gb})")
logger.info(f"[체결통보] {code} {exec_data.get('SLL_BUY_DVSN_CD')} 체결 EXG={exg_label}")
```

> **목적**: Decision D 채택으로 발주는 항상 KRX (응답 1 또는 3 예상). 응답 2/4 가 관측되면 운영 이상 — 로그로 즉시 식별.

---

## [작업 5] Dashboard `/api/status` 응답에 channel_used 포함

`src/dashboard/app.py` 의 watchers 직렬화 부분 (`/api/status` 엔드포인트, 약 라인 230) 에 추가:
```python
{
    "code": w.code,
    "name": w.name,
    "state": w.state.value,
    ...
    "channel_used": w.channel_used,  # R-17
    "channel_decided_at": w.channel_decided_at.isoformat() if w.channel_decided_at else None,
    "un_push_count": w.un_push_count_at_decision,
    "st_push_count": w.st_push_count_at_decision,
}
```

> **HTML 표시는 백로그**: API 응답에 필드만 추가, HTML 측 표시는 다음 W-N 에서.

---

## [검증]

각 작업 완료 후 즉시 다음 검증을 수행:

### A. 컴파일
```bash
python -m py_compile \
    src/kis_api/constants.py \
    src/kis_api/kis.py \
    src/core/channel_resolver.py \
    src/core/watcher.py \
    src/main.py \
    src/dashboard/app.py
```
모두 통과 필수.

### B. import 체인
```bash
python -c "
from src.core.channel_resolver import ChannelResolver
from src.core.watcher import Watcher, WatcherCoordinator, ReservationSnapshot
from src.kis_api.constants import WS_TR_PRICE_UN, WS_TR_PRICE_ST, WS_TR_PRICE
from src.kis_api.kis import KISAPI
from src.main import AutoTrader
print('IMPORT OK')
"
```

### C. ChannelResolver 단위 테스트 (mock)

`scripts/sim/test_channel_resolver.py` 신규 작성 (블랙리스트 외, scripts/sim 은 자유):
```python
"""R-17 ChannelResolver 단위 테스트."""
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.core.channel_resolver import ChannelResolver, DUAL_WINDOW_SEC
from src.kis_api.constants import WS_TR_PRICE_UN, WS_TR_PRICE_ST


async def test_resolver():
    kis = MagicMock()
    kis.subscribe_realtime = AsyncMock()
    kis.unsubscribe_realtime = AsyncMock()
    kis._subscribed_codes = {}

    resolver = ChannelResolver(kis)
    decided = []
    resolver.set_channel_decided_callback(
        lambda c, ch, u, s, t: decided.append((c, ch, u, s))
    )

    await resolver.start(["AAA", "BBB", "CCC"])
    assert resolver.is_active() is True

    # AAA: UN tick 5건 → UN
    for _ in range(5):
        resolver.on_realtime_price({"code": "AAA", "tr_id": WS_TR_PRICE_UN, "current_price": 10000})
    # BBB: ST tick 3건 → ST
    for _ in range(3):
        resolver.on_realtime_price({"code": "BBB", "tr_id": WS_TR_PRICE_ST, "current_price": 20000})
    # CCC: 양쪽 0건 → UN 폴백

    await asyncio.sleep(DUAL_WINDOW_SEC + 0.5)
    assert resolver.is_active() is False

    assert ("AAA", WS_TR_PRICE_UN, 5, 0) in decided
    assert ("BBB", WS_TR_PRICE_ST, 0, 3) in decided
    assert ("CCC", WS_TR_PRICE_UN, 0, 0) in decided

    # active 가드 — 두 번째 start 호출 (race 처리)
    await resolver.start(["DDD"])
    assert resolver.is_active() is True
    resolver.reset()

    print("PASS: ChannelResolver 분기 정확 + race 가드 정상")


if __name__ == "__main__":
    asyncio.run(test_resolver())
```

실행: `python scripts/sim/test_channel_resolver.py` → "PASS" 출력 확인.

### D. grep 검증 — 기존 `WS_TR_PRICE` 단일 참조 잔존 확인
```bash
grep -rn "WS_TR_PRICE\b" src/ | grep -v "WS_TR_PRICE_"
```
결과를 보고서에 첨부 (alias 로 동작하므로 잔존 자체는 OK, 향후 정리 백로그).

### E. grep 검증 — `_subscribed_codes` 사용처 set / dict 호환성
```bash
grep -rn "_subscribed_codes" src/
```
`.add()` / `.remove()` / `set()` 호출 잔존 확인 (모두 dict 호환으로 변경 필요).

---

## [검증 실패 시 — 멈춤 조건]

다음 케이스 중 하나라도 발생하면 **즉시 작업 중단 + 보고**:

1. py_compile 실패 1건 이상
2. import 체인 실패
3. ChannelResolver 단위 테스트 PASS 못 함
4. main.py 내 `subscribe_realtime` 호출 위치를 명확히 식별 못 함 (여러 곳에서 호출되거나 호출 패턴이 grep 으로 단정 불가)
5. `_subscribed_codes` 사용처에서 dict 호환되지 않는 set 전용 메서드 호출 발견 (변경 영향이 화이트리스트 외 파일까지 확장)
6. 작업 중 화이트리스트 외 파일 수정 필요성 발견
7. 기존 WatcherCoordinator.start_screening 시그니처 또는 호출 패턴과 충돌

---

## [모호한 케이스] — 사전 결정

| 케이스 | 결정 |
|---|---|
| ChannelResolver 의 dual subscribe 후 `_subscribed_codes` 가 ST 로 덮어쓰기 됨 | `_resolve()` 끝에서 채택된 tr_id 로 dict 재정정 (작업 2 _resolve 코드에 포함) |
| 양쪽 0건 분기 결정 시 폴백 | UN 유지 (NXT 미상장이라도 다음 push 가능, 결정 보수적) |
| Watcher 가 watchers 에서 사라진 후 콜백 호출 | _on_channel_decided 가 None 체크 + skip (작업 3-2 에 포함) |
| 기존 `WS_TR_PRICE` 참조가 콜백 등록 키로 사용된 곳 | `WS_TR_PRICE_UN` 으로 점진 교체 (alias 가 같은 값이므로 동작 동일) |
| ChannelResolver 가 active 인 동안 watcher 가 받는 시세 | main.py `_on_realtime_price` 에서 active 체크 후 coordinator 호출 skip (작업 4-2) |
| ChannelResolver 결정 전 매수 시도 | 09:50 스크리닝 → 09:50:10 분기 결정 → 09:55 신고가 감시 시작 → 10:00 매수 윈도우. 매수 시점에는 항상 결정 완료. fallback: watcher.channel_used None 시에도 발주는 KRX 고정이므로 동작 영향 없음 |
| 수동 스크리닝 + 09:50 정규 스크리닝 race | ChannelResolver.start() 가 active 중 호출 시 reset 후 새 codes 로 재시작 (작업 2 start 코드에 포함) |
| 수동 스크리닝 → 다른 종목으로 추가 수동 스크리닝 (덮어쓰기) | 동일 — start() 의 active reset 처리로 새 종목 채널 재판정 |
| 재접속이 dual 윈도우 (10초) 안에 발생 | _subscribed_codes dict 의 단일 tr_id 로 복구 (한 쪽 채널만 복구). dual 윈도우 종료 후 정상 동작. 매우 드문 케이스이므로 별도 처리 안 함 |

---

## [보고]

작업 완료 후 다음 형식으로 보고:

```
## R-17 작업 결과 보고

### 작업 완료 요약
| # | 작업 | 결과 |
|---|---|---|
| 0 | 사전 백업 | OK / FAIL |
| 1 | KIS 레이어 (시세) | OK / 부분 / FAIL |
| 2 | ChannelResolver 신규 | OK / FAIL |
| 3 | Watcher 메타 + setter | OK / FAIL |
| 4 | main.py 통합 | OK / FAIL |
| 5 | Dashboard | OK / FAIL |

### 검증 결과
- py_compile: OK / FAIL (실패 시 파일 + 에러)
- import: OK / FAIL
- ChannelResolver 단위 테스트: PASS / FAIL
- grep WS_TR_PRICE 잔존: N건 (위치 첨부)
- grep _subscribed_codes 사용처 호환성: OK / FAIL

### 변경 파일 + 라인 수
- src/core/channel_resolver.py (NEW, +N행)
- src/kis_api/constants.py (+M / -K)
- src/kis_api/kis.py (+M / -K)
- src/core/watcher.py (+M / -K)
- src/main.py (+M / -K)
- src/dashboard/app.py (+M / -K)

### 모호 케이스 처리
- (있으면 어떻게 결정했는지)

### 발견 (작업 중)
- (예상 외 발견 / 추가 빈틈 / 향후 백로그 후보)

### 다음 단계
- git commit + 운영 배포 (수석님 결정)
- 또는 모호 케이스 추가 결정 필요
```

---

## [표준 금지 조항]

CLAUDE.md §협업 규칙 → 모든 명령서 표준 포함:

1. **운영 서버 접근 0건** — ssh / scp / rsync 금지
2. **운영 서버 배포 금지** — 134.185.115.229 또는 /home/ubuntu/AUTOTRADE/ 어떤 변경도 금지
3. **git commit 자동 실행 금지**
4. **로컬 AutoTrader 실제 실행 금지** (검증은 import / py_compile / 단위 테스트만)
5. **systemd / 데몬 재시작 금지**

이 5개 위반 시 즉시 작업 중단 + 보고.

---

## [부록 A] — 4-23 운영 측정 첨부 (참조용)

xlsx 시트 "국내주식 실시간체결가 (통합)" 73~ 행에 동일 본문 등재됨.
관측 raw: `logs/ws_runtime/ws_krx_only_test_20260423_134101.jsonl`
NXT 코드 리스트 (PDF 25.03.31): `logs/ws_runtime/nxt_codes.txt` (796건)
검증 스크립트: `scripts/sim/ws_krx_only_test.py`

---

## [부록 B] — 5개 결정 한 줄 요약

| # | 결정 | 한 줄 |
|---|---|---|
| 1 | 윈도우 10초 | NXT 활성 push 99% 가 5초 이내 도달, 10초면 false positive 1.8% 이하 |
| 2 | 2D Dual subscribe | 시세 공백 0초 (UN+ST 동시 push, 결정 후 한 쪽 정리) |
| 3 | 3A-2 메타 4 필드 | Watcher 표시 + Dashboard 노출, DB 스키마 변경 0 |
| 4 | **D — KRX 고정 발주** | 모든 발주/취소/매도 EXCG = "KRX". SOR 호가 개선 (~0.05~0.15%/일) 포기 대신 운영 단순성 + 결함 10/11 자동 해소 |
| 5 | ChannelResolver 신규 | 책임 분리 (KIS 레이어는 통신만, 분기 결정은 도메인 클래스) |

---

## [부록 C] — 백로그 (R-17 범위 외, 별도 W-N)

1. **TR_ID 마이그레이션**: TTTC0803U (cancel 구) → TTTC0013U (신, KIS 권고). xlsx 시트 "주식주문(정정취소)" 행 22 권고. 운영 영향 없음 (구 TR_ID 도 동작) 이지만 추후 정리.
2. **HTML 대시보드 채널 표시**: `/api/status` 의 channel_used 필드를 frontend 에 노출.
3. **`WS_TR_PRICE` alias 제거**: 모든 호출처를 `WS_TR_PRICE_UN` 으로 명시 변경 후 alias 삭제.
4. **SOR 효과 측정 (선택)**: LIVE 안정화 후 일정 기간 SOR vs KRX A/B 측정 → Decision 4 재검토 evidence.

**문서 끝 (R-17 종합명령서 v2, KRX 고정 단순화, 2026-04-23)**
