# 사전 조사 3/5 — dashboard 현황

작성일: 2026-04-07
브랜치: feature/dashboard-fix-v1
git 상태: 미추적 파일만(??), 추적 파일 수정 없음 — 조사 진행

---

## §0. 사전 학습

이전 보고서(recon_01, recon_02) 재독 생략. 사전 확정 사실 적용.

---

## §4-1. dashboard 디렉토리 위치와 파일 구성

실제 위치: `src/dashboard/` (CLAUDE.md §3 파일 트리에 미기재)

| 파일 | 라인 수 |
|------|--------|
| `src/dashboard/__init__.py` | 0 |
| `src/dashboard/__main__.py` | 5 |
| `src/dashboard/app.py` | 423 |
| `src/dashboard/templates/index.html` | 636 |

CSS/JS 별도 파일 없음. 스타일·스크립트 모두 `index.html` 인라인.

별도 경로: `dist/AUTOTRADE/_internal/src/dashboard` — PyInstaller 빌드 산출물. 운영 코드 아님.

---

## §4-2. 웹 프레임워크 식별

`src/dashboard/app.py:17`:
```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
```

**결론: FastAPI**

`requirements.txt` 버전:
```
fastapi>=0.110
uvicorn>=0.27
```

---

## §4-3. 서빙 방식 & 포트

### 포트
- 설정값: `config/settings.py:51` — `dashboard_port: int = 8503` (기본값)
- `.env`의 `DASHBOARD_PORT` 또는 `settings.dashboard_port`로 오버라이드 가능

### 서빙 방식: 두 가지 경로

**경로 A — `src/main.py` (AutoTrader와 동일 프로세스, 데몬 스레드)**

`src/main.py:948-963`:
```python
def _start_dashboard_server(self) -> None:
    import uvicorn
    from src.dashboard.app import app as dashboard_app, attach_autotrader
    port = self.settings.dashboard_port
    def _run():
        uvicorn.run(dashboard_app, host="0.0.0.0", port=port, log_level="warning")
    t = threading.Thread(target=_run, daemon=True, name="dashboard")
    t.start()
    attach_autotrader(self)
```
→ AutoTrader와 **같은 프로세스**, 별도 데몬 스레드에서 uvicorn 실행.
→ `attach_autotrader(self)` 호출로 `DashboardState.autotrader` 연결.

**경로 B — `launcher.py` (독립 서브프로세스)**

`launcher.py:92-98`:
```python
proc = subprocess.Popen(
    [sys.executable, "-m", "src.dashboard.app"],
    ...
)
```
→ 대시보드만 먼저 띄울 때 사용. 이 경우 AutoTrader 미연결 상태로 시작.

---

## §4-4. 라우트 / 페이지 목록

| 메서드 | 경로 | 라인 | 인증 |
|--------|------|------|------|
| GET | `/` | 202 | 관리자 토큰(쿼리파라미터 `?token=`) 또는 localhost |
| GET | `/api/status` | 227 | 없음 (읽기 전용) |
| POST | `/api/set-targets` | 234 | `X-Admin-Token` 헤더 필수 |
| GET | `/api/search-stock` | 300 | 없음 |
| POST | `/api/run-manual-screening` | 323 | `X-Admin-Token` 헤더 필수 |
| WebSocket | `/ws` | 349 | `?token=` (없으면 읽기 전용) |

라우트 총 6개. HTML 페이지는 `/` 단 1개 (SPA 구조).

---

## §4-5. 현재 인증 상태

### 구현된 인증 메커니즘

**토큰 방식: `DASHBOARD_ADMIN_TOKEN` (환경변수)**

```
src/dashboard/app.py:27
ADMIN_TOKEN = os.getenv("DASHBOARD_ADMIN_TOKEN", "")
```

토큰 미설정 시 모든 요청이 관리자 권한으로 처리됨 (`app.py:30-31` 경고 로그).

**관리자 판별 로직 (3곳)**

1. `GET /` — IP 기반 + 쿼리파라미터 토큰:
   `app.py:216`: `is_admin = is_local or (ADMIN_TOKEN and hmac.compare_digest(token_param, ADMIN_TOKEN))`
   → localhost면 토큰 없이 관리자, 외부면 `?token=xxx` 필요

2. `POST /api/set-targets`, `POST /api/run-manual-screening` — HTTP 헤더:
   `app.py:53-56`: `X-Admin-Token` 헤더 검증 (`hmac.compare_digest`)

3. `/ws` WebSocket — 쿼리파라미터:
   `app.py:354-365`: `?token=` 있으면 관리자, 없으면 읽기 전용 (logs·available_cash 제외 브로드캐스트)

**HTML 측 IS_ADMIN 전달:**
`app.py:218-223`: 서버가 HTML을 내려줄 때 `/*__ADMIN__*/`, `/*__TOKEN__*/` 플레이스홀더를 문자열 치환.
`index.html:294-298`: `IS_ADMIN === false` 이면 `#manual-input-section` DOM 요소를 `display:none` 처리.

### CLAUDE.md §1 "관리자 토큰 필요" 코드 구현 여부

**구현됨.** `DASHBOARD_ADMIN_TOKEN` 환경변수 기반 토큰 인증. timing-safe (`hmac.compare_digest`) 적용.

**결론:** 현재 dashboard는 **부분 인증** — 토큰 기반 관리자/비관리자 2단계 구분 존재. 단, 세션/쿠키 없음 (stateless). 로그인 페이지 없음. 토큰을 URL에 평문 전달(`?token=`).

---

## §4-6. dashboard ↔ AutoTrader 통신

**방식: 직접 객체 참조 (in-process 공유 상태)**

`src/dashboard/app.py`의 import:
```
src/dashboard/app.py:15  from src.utils.market_calendar import now_kst
src/dashboard/app.py:25  from src.core.monitor import TargetMonitor
```

`DashboardState.autotrader` 필드(`app.py:64`)에 AutoTrader 인스턴스 직접 저장.
`attach_autotrader(autotrader)` 함수(`app.py:382-390`)로 연결.

각 API 핸들러는 `state.autotrader`를 통해 AutoTrader 메서드 직접 호출:
- `state.autotrader.set_manual_codes(final_codes)` — `app.py:285`
- `state.autotrader._on_screening()` — `app.py:334`
- `state.autotrader.api.get_current_price(code)` — `app.py:312`

AutoTrader → Dashboard 역방향: `autotrader.on_state_update = _sync_from_autotrader` 콜백(`app.py:386`).

**결론:** DB 폴링이나 HTTP REST 없음. 동일 프로세스 내 객체 공유.

---

## §4-7. 종목 입력·매매 트리거 경로

### 종목 입력

라우트: `POST /api/set-targets` (`app.py:234-297`)
인증: `_check_admin(token)` — `X-Admin-Token` 헤더 필수
처리 흐름:
1. 요청 body `{"codes": [...]}` 수신
2. `state.resolve_input(raw)` — 종목명→코드 변환
3. `api.get_current_price(code)` — KIS API 유효성 검증
4. `state.autotrader.set_manual_codes(final_codes)` 호출 — `app.py:285`

### 수동 스크리닝 트리거

라우트: `POST /api/run-manual-screening` (`app.py:323-342`)
인증: `_check_admin(token)` — `X-Admin-Token` 헤더 필수
처리 흐름:
1. `state.autotrader._manual_codes` 확인 (비어있으면 거부)
2. `await state.autotrader._on_screening()` 직접 호출 — `app.py:334`

---

## §4-8. 정적 자원 / 템플릿

| 항목 | 내용 |
|------|------|
| 템플릿 디렉토리 | `src/dashboard/templates/` |
| 템플릿 파일 | `index.html` 1개 |
| 템플릿 엔진 | **없음** — FastAPI Jinja2 미사용. 서버에서 Python 문자열 `.replace()`로 플레이스홀더 치환 |
| CSS | `index.html` 내 `<style>` 인라인 (약 270라인) |
| JS | `index.html` 내 `<script>` 인라인 (약 350라인) |
| 별도 static 디렉토리 | 없음 |
| 로그인 페이지 | 없음 |
| 기존 패턴 | `/*__ADMIN__*/`, `/*__TOKEN__*/` 플레이스홀더를 `app.py`가 서빙 시점에 치환. 새 페이지 추가 시 이 패턴을 따르거나 Jinja2 도입 필요 |

---

## §6. 발견 사항

1. **토큰을 URL 쿼리파라미터로 전달 (`?token=xxx`)**: `GET /`와 `/ws` 에서 `token_param = request.query_params.get("token", "")` 방식. URL에 토큰 평문 노출 → 서버 로그, Cloudflare 로그, 브라우저 히스토리에 기록됨.

2. **localhost는 토큰 없이 무조건 관리자**: `app.py:216` `is_local = client_ip in ("127.0.0.1", "::1", "localhost")`. 클라우드 이전 후 동일 서버에서 로컬 접속 시 무조건 관리자 권한.

3. **`DASHBOARD_ADMIN_TOKEN` 미설정 시 인증 무력화**: `app.py:55` `if ADMIN_TOKEN and ...` — 토큰이 빈 문자열이면 `_check_admin` 통과. 클라우드 배포 시 필수 설정.

4. **세션/쿠키 없음**: 매 요청마다 토큰 전달 필요. 브라우저 새로고침 시 토큰 재입력 또는 URL 재사용 필요.

5. **`launcher.py` 경로 B**: 대시보드를 독립 서브프로세스로 기동 시 `attach_autotrader` 미호출 → `state.autotrader = None` 상태. 이 경우 `/api/set-targets` 등 모든 쓰기 API가 `"AutoTrader 미연결"` 반환.

6. **템플릿 엔진 없음**: 서버사이드 렌더링이 Python 문자열 `.replace()` 2회. 로그인 페이지 추가 시 Jinja2 도입 또는 동일 방식 확장 중 선택 필요.
