# W-31 — WebSocket H0UNCNT0 런타임 로깅 검증 (소규모 검증 프로젝트)

> **문서 작성일**: 2026-04-22 (§4 로깅 디자인 완성)
> **구현 예정일**: 2026-04-22 야간 ~ 04-23 운영 배포
> **관측 시작일**: 2026-04-23 (거래일)
> **검증 유형**: 소규모 검증 프로젝트 — 원인 고립 전용 (매매 로직 변경 없음, 관측만)
> **선행**: W-30 / W-30-ext / W-30-ext2 (2026-04-22, 완료 · 임시 자원 삭제됨)
> **상위 관계**: R-08 매매 명세 구조 점검. 독립 수명 (R-N redesign 아님)
> **임시성**: 검증 종료 후 로깅 코드 철거 + 임시 파일 삭제 + 대표 샘플만 본 문서 §A 에 첨부 보존

---

## 1. 목적 (한 줄)

KIS WebSocket `H0UNCNT0` (UN 통합 실시간 tick) 이 **KRX-only 종목에서 KRX+NXT 종목과 동등한 tick 을 제공하는지** 런타임 관측으로 확정.

---

## 2. 배경 — W-30 이 답하지 못한 영역

### 2026-04-22 운영 실적

- 매매 1건 (278470 에이피알) — 성공
- 미매매 5종목 — 의심 대상

### W-30 검증 결과 (핵심 숫자 보존, 원본 데이터는 삭제됨)

| 종목 | pre_955_high | post_955_max | first_cross | 판정 |
|---|---|---|---|---|
| 278470 에이피알 (대조) | 436,000 @ 09:47 | 441,000 @ 10:28 | 10:00:00 | ★ CROSSED |
| 001250 GS글로벌 | 4,230 @ 09:54 | 4,230 @ 09:55 | - | not-crossed |
| 011930 신성이엔지 | 4,380 @ 09:46 | **4,390 @ 09:56** | **09:55:00** | **★ CROSSED (+10원)** |
| 092190 KEC | 4,720 @ 09:00 | 4,615 @ 09:57 | - | not-crossed |
| 097230 HJ중공업 | 30,400 @ 09:44 | 30,400 @ 10:07 | - | not-crossed |
| 010140 삼성중공업 (대조) | 31,500 @ 09:34 | 31,350 @ 10:19 | - | not-crossed |

**데이터 출처**: KIS REST `FHKST03010200` (분봉 차트, J 채널 전수 수집 — UN 채널은 4종목 highs=0 반환하여 불가).

### 확정된 것

- ✅ **H1 (pre_955_high 오염) 기각**: `get_current_price` UN=J, 스크리너 초기 고가 정상
- ✅ **H7-refined (REST 분봉 UN/J 비대칭) 확인**: `get_minute_chart` UN 호출 시 KRX-only 4종목 highs=0 반환, J 호출은 정상 — 단 Watcher 는 분봉 미사용이라 런타임 영향 없음 (백로그 편입)
- ✅ **3종목 시장 미충족 확인** (GS글로벌/KEC/HJ중공업) = 삼성중공업과 동일 패턴 (정상)

### 남은 불확정 영역

- ❓ WebSocket `H0UNCNT0` 이 KRX-only 종목의 09:55:00 경계 순간 tick 을 정상 전달했는가
- ❓ 신성이엔지 극미 돌파 (+10원, 1틱, 09:55:00) 가 실제 Watcher 에 도달했는가
- ❓ REST 분봉에서 발견된 UN/J 비대칭이 WebSocket 에도 존재하는가

---

## 3. 가설 (검증 대상)

| ID | 가설 | 판정 기준 |
|---|---|---|
| **H-WS-1** | `H0UNCNT0` UN tick 이 KRX-only 종목에서 누락/지연 | KRX-only 종목 tick 간격 분포가 KRX+NXT 대비 유의 차이 (중앙값 2× 이상 또는 연속 gap ≥ 5초 발견) |
| **H-WS-2** | tick 은 수신되나 `prpr` 의 intraday max 가 REST high 와 불일치 | WebSocket 누적 high < 동시점 REST `get_current_price` high |
| **H-WS-3** | WebSocket 정상, 원인은 다른 곳 | 모든 metric 이 KRX+NXT 종목과 동등 → H-WS 기각 |

---

## 4. 로깅 디자인 (상세 확정, 2026-04-22 작성)

> **프로젝트 루트**: `C:\Users\terryn\AUTOTRADE`
> **운영 서버 루트**: `/home/ubuntu/AUTOTRADE`
> 모든 경로는 프로젝트 루트 기준 상대경로로 표기.

### 4.1 수집 이벤트 스키마 (JSONL, 1행 / event)

모든 레코드 공통 필수 필드:
- `ts` (str, ISO8601 ms + `+09:00`) — 로깅 순간 `now_kst()`
- `layer` (str) — `"ws_handler"` / `"coord_route"` / `"watcher_tick"` / `"rest_snapshot"` 4종
- `code` (str) — 종목코드
- `seq_local` (int) — 모듈 전역 atomic counter (gap 식별용)

레이어별 추가 필드:

| 레이어 | 추가 필드 | 소스 |
|---|---|---|
| `ws_handler` | `tick_time` (HHMMSS), `prpr`, `cntg_vol`, `vi_stnd_prc`, `hour_cls_code` | `kis.py` L1062-1077 `price_data` dict |
| `coord_route` | `prpr`, `routed_watcher_count` | `watcher.py` L797 진입부 |
| `watcher_tick` | `prpr`, `watcher_state`, `intraday_high`, `pre_955_high`, `confirmed_high`, `name`, `market_type`, `listing_scope` | Watcher self 필드 |
| `rest_snapshot` | `rest_prpr`, `rest_high`, `rest_low`, `rest_lag_ms` | `api.get_current_price()` 응답 |

예시:
```json
{"ts":"2026-04-23T09:55:00.123+09:00","layer":"watcher_tick","code":"011930","seq_local":5678,"prpr":4390,"watcher_state":"WATCHING","intraday_high":4380,"pre_955_high":4380,"confirmed_high":null,"name":"신성이엔지","market_type":"KOSDAQ","listing_scope":"KRX_only"}
```

### 4.2 삽입 지점 (파일:라인 확정)

**원칙**: 모든 삽입 블록은 `try/except: pass` 격리. 로깅 실패가 매매 로직에 0영향.

| # | 레이어 | 파일 경로 (프로젝트 루트 기준) | 삽입 라인 | 컨텍스트 |
|---|---|---|---|---|
| 1 | `ws_handler` | `src/kis_api/kis.py` | **L1077 직후** (price_data dict 생성 완료, L1078 callback for-loop 직전) | 네트워크 수신 직후 — 파싱은 됐으나 분기 전 |
| 2 | `coord_route` | `src/core/watcher.py` | **L797 `on_realtime_price` 진입부 (for-loop L803 직전)** | 라우팅 직전 — terminal 필터 전 |
| 3 | `watcher_tick` | `src/core/watcher.py` | **L302 직후** (`self.futures_price_ts = futures_ts` 다음, state 분기 L304 직전) | Watcher 수신 직후 — state 분기 직전 |

라인 번호 검증: W-31 구현 시점에 `git diff` 로 현행 라인 재확인 필요 (이 문서의 라인은 2026-04-22 작성 시점 기준).

### 4.3 신규 로거 모듈 (격리 sink)

**파일**: `src/utils/ws_runtime_logger.py` (W-31 신규 작성, 검증 종료 후 삭제)

핵심 요구사항:
- 주 logger (loguru) 와 **완전 분리** — main log 오염 방지
- 쓰기 실패 격리 (try/except pass, 매매 지연 금지)
- atomic counter 제공 (seq_local)
- thread-safe (WS handler + event loop + REST polling task 동시 접근)

설계 골격:

```python
# src/utils/ws_runtime_logger.py (설계안)
import json, threading, itertools
from pathlib import Path
from datetime import datetime

_LOCK = threading.Lock()
_SEQ = itertools.count(1)
_SINK_DIR = Path(__file__).parent.parent.parent / "logs" / "ws_runtime"

def log_event(record: dict) -> None:
    """fire-and-forget. 어떤 예외도 삼켜 매매 로직에 영향 0."""
    try:
        _SINK_DIR.mkdir(parents=True, exist_ok=True)
        record["seq_local"] = next(_SEQ)
        record.setdefault("ts", datetime.now().astimezone().isoformat(timespec="milliseconds"))
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = _SINK_DIR / f"ws_tick_{date_str}.jsonl"
        with _LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
```

**sink 경로**: `C:\Users\terryn\AUTOTRADE\logs\ws_runtime\ws_tick_YYYY-MM-DD.jsonl` (로컬), `/home/ubuntu/AUTOTRADE/logs/ws_runtime/ws_tick_YYYY-MM-DD.jsonl` (운영).

### 4.4 병행 REST 교차 검증

**목적**: WS tick 과 동시점 REST 응답 차이 측정 → H-WS-2 (prpr max < REST high) 검증.

**삽입 지점**: `src/main.py` 가동 시점에 신규 async task 추가 (기존 매매 로직과 독립):

```python
# src/main.py (W-31 한정 삽입, 검증 종료 후 삭제)
async def _ws_runtime_rest_polling(self):
    from src.utils.ws_runtime_logger import log_event
    observe_codes = self._ws_runtime_observe_codes  # 4.5 참조
    while not self._shutdown:
        for code in observe_codes:
            t0 = time.time()
            try:
                snap = await self.api.get_current_price(code)
                log_event({
                    "layer": "rest_snapshot", "code": code,
                    "rest_prpr": int(snap.get("stck_prpr", 0)),
                    "rest_high": int(snap.get("stck_hgpr", 0)),
                    "rest_low":  int(snap.get("stck_lwpr", 0)),
                    "rest_lag_ms": int((time.time() - t0) * 1000),
                })
            except Exception:
                pass
        await asyncio.sleep(1.0)
```

가동 시점: `main.py.run()` 의 기존 startup 블록 끝. shutdown 시점: 기존 shutdown path 에서 자연 종료 (asyncio.CancelledError).

### 4.5 관측 대상 + 격리 구독 리스트

**yaml 신규 섹션** (`config/strategy_params.yaml`, W-31 한정 추가):

```yaml
# W-31 한정 — 검증 종료 후 이 섹션 전체 삭제
ws_runtime:
  enabled: true
  observe_codes:
    # KRX-only 의심군 (W-30 분봉 UN highs=0)
    - "001250"   # GS글로벌
    - "011930"   # 신성이엔지 (극미 돌파 주목)
    - "092190"   # KEC
    - "097230"   # HJ중공업
    # KRX+NXT 대조군
    - "278470"   # 에이피알 (04-22 매매 성공)
    - "010140"   # 삼성중공업
  rest_polling_interval_sec: 1.0
```

**격리 구독 로직**: 매매 스크리닝 (09:50) 과 독립. `main.py.run()` 가동 시점에 `observe_codes` 전수 `api.subscribe_realtime_price()` 호출. Watcher 인스턴스화 안 함 (매매 대상 아님). → WS tick 은 `ws_handler` + `coord_route` 레이어만 기록 (watcher 레이어는 당일 스크리닝 종목만).

### 4.6 listing_scope 판정 방법 (확정)

**Primary**: 수기 JSON 캐시 `config/krx_nxt_listing.json` (W-31 신규 작성, 임시)

```json
{
  "version": "2026-04-22",
  "source": "수석님 수기 작성 (W-31 관측 6종목 한정)",
  "scope": {
    "001250": "KRX_only",
    "011930": "KRX_only",
    "092190": "KRX_only",
    "097230": "KRX_only",
    "278470": "KRX_NXT",
    "010140": "KRX_NXT"
  }
}
```

로더: `ws_runtime_logger.py` 내부 1회 로드 + in-memory dict 캐시.

**Fallback**: 파일 없음 or 코드 미등재 시 `listing_scope: "unknown"` 기록 (크래시 금지, 매매 로직 무관).

**영구 캐시 아님**: W-31 범위는 6종목 관측용. KRX 공식 NXT 리스트 전수 (200+종목) 는 R-N 영역.

### 4.7 파일 생성/수정 요약 (구현 작업 목록)

| 경로 | 유형 | 내용 | 라이프사이클 |
|---|---|---|---|
| `src/utils/ws_runtime_logger.py` | **신규** | 로거 모듈 (§4.3) | 검증 종료 후 삭제 |
| `config/krx_nxt_listing.json` | **신규** | 수기 listing_scope 매핑 (§4.6) | 검증 종료 후 삭제 (영구는 R-N) |
| `config/strategy_params.yaml` | 수정 | `ws_runtime:` 섹션 추가 (§4.5) | 검증 종료 후 섹션 삭제 |
| `config/settings.py` | 수정 | `WSRuntimeParams` Pydantic 클래스 신규 (yaml 파싱) | 검증 종료 후 삭제 |
| `src/kis_api/kis.py` | 수정 | L1077 직후 try/except 로깅 블록 | 검증 종료 후 제거 |
| `src/core/watcher.py` | 수정 | L797 진입부 + L302 직후 try/except 로깅 블록 | 검증 종료 후 제거 |
| `src/main.py` | 수정 | `_ws_runtime_rest_polling` task + startup 등록 | 검증 종료 후 제거 |
| `logs/ws_runtime/` | **신규** 디렉터리 | 로거가 자동 생성 | 검증 종료 후 삭제 (대표 샘플만 §A 보존) |
| `scripts/sim/analyze_ws_runtime.py` | **신규** | 분석 스크립트 (관측 후 작성) | 검증 종료 후 삭제 |
| `scripts/sim/` | **재생성** 필요 | W-30 정리 시 디렉터리 삭제됨 | 검증 종료 후 삭제 |

### 4.8 분석 스크립트 (관측 후 작성)

**경로**: `C:\Users\terryn\AUTOTRADE\scripts\sim\analyze_ws_runtime.py` (W-31 신규, 관측 D-1 이후 작성)

**입력**: `logs/ws_runtime/ws_tick_YYYY-MM-DD.jsonl`

**산출**:
- code × layer groupby → tick 간격 중앙값 / p95 / max gap
- KRX-only 그룹 vs KRX+NXT 그룹 통계 비교 표
- WS tick prpr max vs REST snapshot rest_high 시점별 괴리
- 판정 매트릭스 (§5) 의 입력값 산출

---

## 5. 판정 매트릭스 (관측 후 의사결정)

| 관측 결과 | 판정 | 후속 조치 |
|---|---|---|
| KRX-only tick 간격 KRX+NXT 와 동등 + REST 괴리 0 | **H-WS 기각** | 2026-04-22 미매매 = 시장 미충족 확정. 프로젝트 종료, 로깅 코드 철거 |
| KRX-only tick 일부 누락 or 지연 ≥ 5초 gap 발견 | **H-WS-1 확인** | (가) J 채널 WebSocket fallback 검토 / (나) REST 교차 검증 레이어 도입 / (다) 신규 R-N redesign 발의 |
| tick 정상 수신이나 prpr max < REST high | **H-WS-2 확인** | Watcher 의 WebSocket 단독 의존 재평가 → REST 보조 폴링 설계 |
| 관측 기간 내 KRX-only 종목 의미 있는 가격 변동 0 | **불확정 연장** | 다음 거래일 재실행. 누적 최대 5일 합산 판정 |
| 로깅 자체에 데이터 수집 누락 | **재설계** | 로깅 삽입 지점 재검토, 1일 내 재시도 |

**판정 실패 시 멈춤 조건** (§5.6 원칙 3):
- 관측 데이터 <50% 수집률 → 즉시 멈춤 + 보고
- 예외 발생으로 로깅 자체가 매매 로직에 지연 야기 → 즉시 철거 + 보고

---

## 6. 범위 + 제약

### 범위 (In-scope)

- 로컬 작업 폴더에서 로깅 코드 추가 (import 검증까지)
- 1~5 거래일 관측
- 분석 스크립트 `scripts/sim/analyze_ws_runtime.py` 임시 작성 가능 (종료 후 삭제)
- 판정 매트릭스 적용 후 수석님 보고

### 금지 (Out-of-scope, §5.6 + 표준 5 금지 적용)

- 운영 서버 직접 접근 0건 (Code 측)
- 매매 로직 / Watcher 상태 전이 변경 0건 (관측 전용)
- systemd 재시작 0건 (수석님이 직접 판단)
- CLAUDE.md 상세 내용 편입 금지 (본 문서만 독립 존재)
- git commit 자동 실행 금지

---

## 7. 임시 자원 방침 (수석님 클린 정책 준수)

W-31 종료 후 삭제 의무:

| 자원 | 조치 |
|---|---|
| `logs/ws_runtime/*.jsonl` | 분석 완료 후 대표 샘플 1~2건만 본 문서 §A (부록) 에 첨부 보존, 나머지 삭제 |
| 로깅 삽입 코드 | 원인 고립 후 제거 또는 DEBUG 강등 (실가동 INFO 노이즈 방지) |
| `scripts/sim/analyze_ws_runtime.py` | 검증 종료 후 삭제 |
| `config/krx_nxt_listing.json` (작성 시) | W-31 범위에서는 관측 전용, 영구 필요 시 R-N 재검토 |

---

## 8. 신규 채팅 진입 가이드 (CLAUDE.md + 본 문서 단독 이해용)

신규 Claude 세션이 CLAUDE.md 읽은 뒤 **§8 백로그 + 향후 방향 > "진행 중 검증 프로젝트"** 서브섹션 포인터로 본 문서 진입. 본 문서만 있으면 다음 확인 가능:

1. **왜 하는가**: §1 목적 + §2 배경
2. **무엇을 가정하는가**: §3 가설 표
3. **어떻게 관측하는가**: §4 로깅 디자인
4. **문제 발견 시 어떻게 판단하는가**: §5 판정 매트릭스
5. **건드려도 되는가**: §6 범위 + 제약

W-30 원본 데이터 (`logs/diag/w30*.json` 등) 는 검증 종료 시 삭제되었으므로, 재검증 필요 시 §2 테이블의 핵심 숫자 + 본 문서에서 재구성. 원본이 반드시 필요하면 당일 재수집 스크립트 (`verify_new_high_j.py` 패턴) 재작성 가능 — 단 임시 자원 의무 계속 적용.

---

## 9. 상태 추적

| 항목 | 상태 | 일자 |
|---|---|---|
| W-30 선행 검증 완료 | ✅ | 2026-04-22 |
| W-31 설계 문서 (본 문서) | ✅ | 2026-04-22 |
| 로깅 디자인 상세 확정 | ✅ | 2026-04-22 (§4 완성) |
| 로컬 구현 (§4.7 10 항목) | ⏳ | 2026-04-22~23 |
| 로컬 검증 (py_compile + import + grep 매매 로직 diff 0) | - | - |
| 운영 배포 (수석님 수행) | - | - |
| 관측 D-1 (2026-04-23) | - | - |
| 관측 D-2~D-5 (필요 시) | - | - |
| 분석 스크립트 작성 + 실행 | - | - |
| 최종 판정 + 수석님 보고 | - | - |
| 로깅 코드 철거 + 임시 자원 삭제 | - | - |

---

## 10. 관련 파일 (라이프사이클)

> 상세 경로/라인/역할은 §4.7 파일 생성/수정 요약 표 참조.

**검증 종료 후 삭제 대상**:
- `src/utils/ws_runtime_logger.py` (§4.3 신규 로거)
- `config/krx_nxt_listing.json` (§4.6 수기 매핑)
- `scripts/sim/analyze_ws_runtime.py` + `scripts/sim/` 디렉터리 (§4.8)
- `logs/ws_runtime/` 디렉터리 전체 (대표 샘플 1~2건만 본 문서 §A 보존)
- `src/kis_api/kis.py` L1077 직후 try/except 블록
- `src/core/watcher.py` L797 + L302 try/except 블록
- `src/main.py` `_ws_runtime_rest_polling` 메서드 + startup 등록
- `config/strategy_params.yaml` `ws_runtime:` 섹션
- `config/settings.py` `WSRuntimeParams` Pydantic 클래스

**보존**:
- 본 문서 `C:\Users\terryn\AUTOTRADE\Obsidian\W-31_WebSocket_런타임로깅_검증.md`
- CLAUDE.md §8 "진행 중 검증 프로젝트" 의 1줄 인덱싱

---

## 부록 A — 관측 데이터 대표 샘플 (검증 완료 후 기록)

_(검증 진행 후 대표 tick JSONL 1~2건 첨부 예정)_

---

**문서 끝 — 2026-04-22 §4 로깅 디자인 완성 (W-30 후속) · 2026-04-22 야간 구현 → 2026-04-23 관측 시작 예정**
