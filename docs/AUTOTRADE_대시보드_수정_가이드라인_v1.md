# AUTOTRADE 대시보드 수정 가이드라인 v1.0

> **목적**: 코드 리뷰 + 스크린샷 분석에서 발견된 모든 문제를 수정하기 위한 실행 사양.
> **대상 작업자**: K 수석님 (Claude Code 또는 IDE 직접 작업)
> **수정 대상 파일**: `src/main.py`, `src/dashboard/app.py`, `src/dashboard/templates/index.html`, `config/settings.py`, `config/strategy_params.yaml`, `src/core/monitor.py`, `src/utils/notifier.py`
> **선행 조건**: 현재 DRY_RUN 정상 작동, verify_phase1.py 통과, 코드 백업/git branch 생성
> **완료 정의**: 8장의 모든 검증 시나리오 통과 + 1일 DRY_RUN 풀가동 무사고

---

## 0. 작업 전 준비

### 0.1 환경 백업
```bash
# git branch 분리 (rollback 안전망)
cd C:\Users\terryn\AUTOTRADE
git status                    # 변경사항 없는지 확인
git checkout -b feature/dashboard-shadow-state-fix
git push -u origin feature/dashboard-shadow-state-fix

# .env 백업
copy .env .env.backup-pre-fix

# DB 백업 (작업 중 사고 대비)
copy data\trades.db data\trades.db.backup-pre-fix
```

### 0.2 현재 상태 스냅샷
- [ ] `python -m src.main` 정상 기동 확인
- [ ] `python verify_phase1.py` 전체 통과 확인
- [ ] 현재 대시보드 스크린샷 1장 저장 (작업 후 비교용)
- [ ] 이슈 트래커에 `ISSUE-019` 신규 항목 생성: "대시보드 그림자 상태 + 운영 UX 개선"

### 0.3 작업 원칙 (개발지침과 동일)
- 모든 수정은 **개발지침 9개 규칙 준수**
- 새 파라미터는 반드시 `strategy_params.yaml` + `settings.py` 양쪽에 추가
- 한 카테고리 끝날 때마다 git commit
- DRY_RUN으로 회귀 테스트, 그 다음 카테고리

---

## 1. 작업 순서 (의존성 그래프)

순서가 중요합니다. 의존성을 무시하면 중간에 깨집니다.

```
[Phase F0] 카테고리 A — 그림자 상태 해소  ⚠ 필수 선행
              │
              ├─→ [Phase F1] 카테고리 B — 데드 코드/UI 제거
              │
              ├─→ [Phase F2] 카테고리 C — 보안 수정
              │
              ├─→ [Phase F3] 카테고리 D — 운영 로직/UX 개선
              │       │
              │       └─→ D1 (모니터 폐기 조건) ⚠ 전략 변경
              │
              └─→ [Phase F4] 카테고리 E — 코드 위생
                          │
                          └─→ [Phase F5] end-to-end 검증
```

**왜 A가 무조건 먼저인가**:
A를 먼저 끝내지 않고 B/C/D를 손대면, 두 개의 상태 객체에 똑같은 수정을 두 번 해야 합니다. 그리고 A를 한 후에 다시 B/C/D를 했을 때 또 깨집니다 (ISSUE-003 재발 패턴 그대로). **A 하나만 끝나도 이후 작업이 절반 이하의 비용으로 끝납니다.**

---

## 2. 카테고리 A — 그림자 상태 해소 (CRITICAL)

### 2.1 문제 요약

`AutoTrader`(main.py)와 `DashboardState`(dashboard/app.py)가 같은 프로세스 안에 살면서도 **각자 자기 KIS API 인스턴스 / Trader / RiskManager / monitors를 따로 들고 있습니다.**

증거:
- `main.py:89` — `self.on_state_update = None` (콜백 미연결)
- `app.py:504~563` — `@app.on_event("startup")`이 자체 KISAPI/Trader/RiskManager 생성
- `app.py:482~499` — `_price_updater()` 별도 폴링 (AutoTrader가 이미 WebSocket으로 받는데도)
- 스크린샷: 설정된 종목과 모니터 종목 mismatch (000250 모니터에는 있는데 설정 목록엔 없음)

### 2.2 목표 아키텍처

```
┌─────────────────────────────────────────┐
│    AutoTrader (main.py) — 단일 진실의 원천 │
│    - api, trader, risk, monitors          │
│    - manual_codes                         │
│    - settings, params                     │
│                                           │
│    on_state_change ──┐                    │
└──────────────────────┼────────────────────┘
                       │ (in-process callback)
                       ▼
┌─────────────────────────────────────────┐
│    DashboardBridge (dashboard/app.py)    │
│    - read-only snapshot of AutoTrader    │
│    - sync on every state change          │
│    - WS broadcast                        │
│                                           │
│    Routes ──┐                             │
│   ┌─────────┘                             │
│   │  GET  /api/status      (read)         │
│   │  POST /api/set-targets (→ autotrader.set_manual_codes)│
│   │  POST /api/screening   (→ autotrader._on_screening)   │
│   │  POST /api/stop        (→ autotrader._stop_trading)   │
│   │  WS   /ws              (broadcast)    │
│   └───────────────────────────────────────┘
└─────────────────────────────────────────┘
```

### 2.3 변경 사양

#### A1. AutoTrader → DashboardBridge 콜백 채널 구축

**파일**: `src/main.py`, `src/dashboard/app.py`

**변경**:

1. `dashboard/app.py`에 신규 함수 추가:
   ```python
   def attach_autotrader(autotrader) -> None:
       """AutoTrader 인스턴스를 대시보드에 연결.
       이 함수가 호출되기 전에는 대시보드는 read-only 빈 화면을 표시함.
       """
       state.autotrader = autotrader
       autotrader.on_state_update = _sync_from_autotrader
   ```

2. `dashboard/app.py`에 신규 함수 `_sync_from_autotrader()`:
   - `autotrader._monitors` → `state.monitors`로 복사
   - `autotrader._available_cash` → `state.available_cash`
   - `autotrader.risk.daily_pnl` → `state.daily_pnl`
   - `autotrader.risk.daily_trades` → `state.daily_trades`
   - `autotrader._manual_codes` → `state.manual_codes`
   - `autotrader.settings.trade_mode` → `state.trade_mode`
   - 스냅샷 후 WebSocket broadcast 트리거

3. `main.py`의 `_start_dashboard_server()` 수정:
   ```python
   def _start_dashboard_server(self) -> None:
       import uvicorn
       from src.dashboard.app import app as dashboard_app, attach_autotrader
       attach_autotrader(self)              # ← 신규 줄
       port = self.settings.dashboard_port
       def _run():
           uvicorn.run(dashboard_app, host="0.0.0.0", port=port, log_level="warning")
       t = threading.Thread(target=_run, daemon=True, name="dashboard")
       t.start()
   ```

4. `main.py`의 `_fire_state_update()`는 그대로 두되, 호출 지점 5곳 모두 살아 있는지 확인:
   - `run()` 초기 (`_running = True` 직후)
   - `_start_monitoring_candidate()` 끝
   - `_process_signals()` 안 매수/매도/청산 직후
   - 폐기/스킵 처리 후
   - `_schedule_market_close()` 일일 리포트 생성 후

**검증**:
- [ ] `python -m src.main` 실행 후 대시보드 접속 → 모니터/예수금/P&L이 main.py 로그와 1초 이내 동기화
- [ ] 텔레그램 `/target 005930` → 대시보드 "설정된 종목"에 1초 이내 반영
- [ ] 텔레그램 `/clear` → 대시보드 "설정된 종목" 비워짐
- [ ] 대시보드의 "수동 스크리닝" 버튼 → 화면의 모니터 카드가 main.py 로그와 동일

---

#### A2. dashboard/app.py에서 KIS API 직접 소유 제거

**파일**: `src/dashboard/app.py`

**삭제 대상**:

1. `DashboardState` 클래스에서 다음 필드 **삭제**:
   - `self.settings`
   - `self.params`
   - `self.api`
   - `self.trader`
   - `self.risk`
   
   대신 `self.autotrader: Optional["AutoTrader"] = None` 추가.
   
   이 객체는 read-only proxy 역할만 함. `self.autotrader.params`, `self.autotrader.settings` 식으로 참조.

2. `@app.on_event("startup")` 함수 (`_auto_connect`) **전체 삭제**.
   - 이유: AutoTrader가 시작하면서 attach_autotrader()로 연결해주므로 불필요.
   - 대신 startup 훅은 종목명 캐시 로드만 남김 (autotrader가 attach될 때까지 대기 → 별도 task로 분리).

3. `_price_updater()` **전체 삭제**.
   - 이유: AutoTrader가 이미 WebSocket으로 실시간 가격을 받음. 중복.

4. 엔드포인트 **삭제**:
   - `POST /api/connect` ← AutoTrader 자동 연결로 불필요
   - `POST /api/disconnect` ← 위험한 버튼. 운영 중 끊지 않음

**유지 + 수정 대상**:

5. `POST /api/set-targets` → `state.autotrader.set_manual_codes(codes)` 호출하도록 변경
6. `POST /api/run-manual-screening` → `await state.autotrader._on_screening()` 호출
7. `POST /api/screening` → 카테고리 B에서 삭제 (자동 스크리닝 제거)

**검증**:
- [ ] `app.py` 안에서 `KISAPI(...)` 생성 코드가 0건인지 grep으로 확인
- [ ] `app.py` 안에서 `Trader(...)`, `RiskManager(...)` 생성 코드가 0건인지 확인
- [ ] `python -m src.dashboard.app` 단독 실행 시 "AutoTrader 미연결" 안내 화면이 표시되는지
- [ ] `python -m src.main` 실행 후 대시보드 접속 시 정상 작동

---

#### A3. DashboardState.to_dict() 수정

**파일**: `src/dashboard/app.py`

**변경**:

`to_dict()` 메서드가 `self.trader`, `self.risk`, `self.params`를 직접 참조하던 부분을 모두 `self.autotrader.*`로 교체.

```python
def to_dict(self) -> dict:
    if not self.autotrader:
        # AutoTrader 미연결 상태 — 빈 데이터 반환
        return {
            "connected": False,
            "trade_mode": "",
            "available_cash": 0,
            "total_eval": 0,
            "daily_pnl": 0,
            "daily_trades": 0,
            "monitors": [],
            "manual_codes": [],
            "logs": list(self.log_messages),
            "server_time": now_kst().strftime("%H:%M:%S"),
            "autotrader_attached": False,
        }
    
    at = self.autotrader
    monitors_data = [self._monitor_to_dict(mon) for mon in at._monitors]
    
    return {
        "connected": at.api.is_connected if at.api else False,
        "trade_mode": at.settings.trade_mode,
        "available_cash": at._available_cash,
        "total_eval": at._available_cash,  # 또는 별도 잔고 필드
        "daily_pnl": round(at.risk.daily_pnl) if at.risk else 0,
        "daily_trades": at.risk.daily_trades if at.risk else 0,
        "monitors": monitors_data,
        "manual_codes": at._manual_codes,
        "logs": list(self.log_messages)[-self._log_return_size():],
        "server_time": now_kst().strftime("%H:%M:%S"),
        "autotrader_attached": True,
    }
```

`_monitor_to_dict()`는 기존 `to_dict()` 안의 monitor 변환 로직을 분리한 헬퍼.

**검증**:
- [ ] `to_dict()` 안에 `self.api`, `self.trader`, `self.risk` 참조가 0건
- [ ] `/api/status` 엔드포인트 응답이 이전과 동일한 키 구조 유지 (프론트 변경 최소화)

---

### 2.4 카테고리 A 완료 후 git commit

```bash
git add .
git commit -m "feat(dashboard): 그림자 상태 해소 — AutoTrader 단일 owner

- DashboardState에서 KIS API 직접 소유 제거
- attach_autotrader() 콜백 채널 신설
- _price_updater() 삭제 (AutoTrader WebSocket으로 통합)
- /api/connect, /api/disconnect 엔드포인트 삭제
- ISSUE-019 카테고리 A 완료"
```

---

## 3. 카테고리 B — 데드 코드 / 데드 UI 제거

### B1. "자동 스크리닝" 버튼 제거

**파일**: `src/dashboard/templates/index.html`

**변경**:
- `<button id="btn-screening" ...>자동 스크리닝</button>` 줄 삭제
- JS의 `doScreening()` 함수 삭제
- `setBtn` defaults에서 `btn-screening` 항목 삭제
- 페이지 로드 시 `setBtn('btn-screening', ...)` 호출 모두 삭제

**파일**: `src/dashboard/app.py`
- `POST /api/screening` 엔드포인트 삭제

**이유**: ISSUE-010에서 수동 입력 방식으로 전환했으므로 자동 스크리닝은 데드 기능.

### B2. "API 연결" / "연결 해제" 버튼 제거

**파일**: `src/dashboard/templates/index.html`
- `btn-connect`, `btn-disconnect` 버튼 삭제
- `doConnect()`, `doDisconnect()` JS 함수 삭제
- 페이지 로드 시 setBtn 호출 정리

**이유**: A 카테고리에서 AutoTrader가 자동 연결을 책임짐. 수동 연결 버튼은 위험.

### B3. 컨트롤 row 자체 정리

위 B1, B2 후에는 `.btn-group`을 감싸던 `<div class="card">`가 비어있게 됨. 이 카드 자체를 삭제하거나, 대신 **EMERGENCY STOP 버튼만 두도록 재구성** (D6에서 다룸).

### B4. 데드 CSS는 보존

`.price-levels`, `.price-marker`, `.price-label` CSS는 **삭제하지 말 것**. D5에서 가격 게이지로 살릴 예정.

### B5. 검증

- [ ] grep으로 `btn-connect`, `btn-disconnect`, `btn-screening`, `doConnect`, `doDisconnect`, `doScreening`, `/api/connect`, `/api/disconnect`, `/api/screening` 모두 0건
- [ ] 대시보드 접속 시 화면에 사라진 버튼들이 안 보임
- [ ] 브라우저 콘솔 에러 0건

### B6. git commit

```bash
git commit -m "refactor(dashboard): 데드 코드/UI 제거

- 자동 스크리닝 버튼/엔드포인트 (ISSUE-010 잔재)
- API 연결/해제 버튼 (위험)
- ISSUE-019 카테고리 B 완료"
```

---

## 4. 카테고리 C — 보안 수정

### C1. XSS — `renderSelectedStocks` data-attribute 패턴 적용

**파일**: `src/dashboard/templates/index.html`

**현재 (취약)**:
```javascript
function renderSelectedStocks() {
    const el = document.getElementById('selected-stocks');
    el.innerHTML = selectedStocks.map(s =>
        `<span ...>
            ${escHtml(s.name)}(${s.code})
            <span onclick="removeSelectedStock('${s.code}')" ...>&times;</span>
        </span>`
    ).join('');
    ...
}
```

문제: `onclick="...('${s.code}')"`는 JS 문자열 컨텍스트라 HTML escape로는 부족.

**수정**:
```javascript
function renderSelectedStocks() {
    const el = document.getElementById('selected-stocks');
    el.innerHTML = selectedStocks.map(s =>
        `<span class="stock-tag" ...>
            ${escHtml(s.name)}(${escHtml(s.code)})
            <span class="remove-tag" data-code="${escHtml(s.code)}" ...>&times;</span>
        </span>`
    ).join('');
    
    // 이벤트 위임
    el.querySelectorAll('.remove-tag').forEach(node => {
        node.addEventListener('click', () => {
            removeSelectedStock(node.dataset.code);
        });
    });
    
    document.getElementById('manual-codes-input').value = 
        selectedStocks.map(s => s.code).join(', ');
}
```

**검증**:
- [ ] grep으로 `onclick="` 검색 → 인라인 onclick이 0건
- [ ] 종목 태그 X 버튼 정상 작동
- [ ] 종목 코드에 특수문자(예: `'`)가 들어와도 깨지지 않음

### C2. `/api/search-stock` 비관리자 KIS API fallback 차단

**파일**: `src/dashboard/app.py`

**현재 (취약)**:
```python
@app.get("/api/search-stock")
async def api_search_stock(q: str = ""):
    ...
    # 캐시 미스 시 KIS API 직접 조회 ← 무인증 접근 가능
    if not results and ... and state.connected and state.api:
        info = await state.api.get_current_price(q_stripped)
        ...
```

**수정**: `_check_admin` 호출 추가 + 비관리자는 캐시 hit만 반환.

```python
@app.get("/api/search-stock")
async def api_search_stock(
    q: str = "",
    request: Request = None,
):
    if not q or len(q.strip()) < 1:
        return {"results": []}
    
    # 관리자 여부 판별 (헤더 또는 쿠키)
    is_admin = _is_admin_request(request)
    
    results = state.search_stock(q)
    
    # 캐시 미스 + 관리자만 KIS API fallback 허용
    q_stripped = q.strip()
    if (not results 
        and is_admin 
        and len(q_stripped) == 6 
        and q_stripped.isdigit() 
        and state.autotrader 
        and state.autotrader.api):
        try:
            info = await state.autotrader.api.get_current_price(q_stripped)
            if info.get("current_price", 0) > 0:
                name = info.get("name", "") or q_stripped
                state.cache_stock(q_stripped, name)
                results = [{"code": q_stripped, "name": name}]
        except Exception:
            pass
    
    return {"results": results}
```

`_is_admin_request()` 헬퍼 함수 신설 (request에서 X-Admin-Token 헤더 또는 쿠키 검증).

**검증**:
- [ ] 관리자 토큰 없이 `/api/search-stock?q=000001` 호출 시 빈 결과 (캐시 hit 없으면)
- [ ] 관리자 토큰으로 호출 시 KIS fallback 정상 작동

### C3. CORS 화이트리스트 좁히기

**파일**: `src/dashboard/app.py`

**현재 (헐거움)**:
```python
allow_origin_regex=r"https://.*\.trycloudflare\.com|..."
```

**임시 수정** (Named Tunnel 도입 전):
- 환경변수 `DASHBOARD_ALLOWED_ORIGINS`에 명시적 origin 리스트를 두고 거기서만 허용
- `.env` 신규 항목: `DASHBOARD_ALLOWED_ORIGINS=https://your-current-tunnel.trycloudflare.com,http://localhost:8503`

```python
allowed = os.getenv("DASHBOARD_ALLOWED_ORIGINS", "").split(",")
allowed = [o.strip() for o in allowed if o.strip()]
if not allowed:
    allowed = ["http://localhost:8503", "http://127.0.0.1:8503"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Admin-Token", "Content-Type"],
)
```

> ⚠ 이 수정은 Cloudflare Quick Tunnel URL이 바뀔 때마다 `.env`를 갱신해야 함을 의미합니다. 셋업 가이드의 Named Tunnel 전환과 함께 자연 해소됩니다.

**검증**:
- [ ] `.env`에 명시한 origin에서만 대시보드 접근 가능
- [ ] 다른 origin (예: 다른 trycloudflare URL)에서는 CORS 차단

### C4. ADMIN_TOKEN HTML 인라인 제거 (중간단계)

**파일**: `src/dashboard/app.py`, `src/dashboard/templates/index.html`

**현재 (취약)**:
```python
html = html.replace("/*__TOKEN__*/", f'const ADMIN_TOKEN = "{ADMIN_TOKEN}";')
```

브라우저 view-source로 토큰 노출.

**중간단계 수정** (멀티테넌트 쿠키 도입 전 임시):
- `IS_ADMIN`만 HTML로 주입, `ADMIN_TOKEN`은 주입하지 않음
- API 호출 시 `?token=` URL 파라미터 1회만 사용 (페이지 로드 시점), 그 이후는 쿠키로 전환

**임시 패치 절차**:
1. 페이지 진입 시 `?token=xxx`가 URL에 있으면 → 서버가 `Set-Cookie: dashboard_admin=xxx; HttpOnly; Secure; SameSite=Strict` 발급 후 token 파라미터 없는 URL로 리다이렉트
2. 이후 API 요청은 쿠키 자동 동봉
3. JS의 `adminHeaders()` 함수는 빈 객체 반환 (헤더 불필요)

**파일 수정**:

`app.py`:
```python
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # 1. URL에 token이 있으면 쿠키 발급 후 리다이렉트
    token_param = request.query_params.get("token", "")
    if token_param and ADMIN_TOKEN and hmac.compare_digest(token_param, ADMIN_TOKEN):
        from fastapi.responses import RedirectResponse
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(
            key="dashboard_admin",
            value=token_param,
            httponly=True,
            secure=True,    # HTTPS만
            samesite="strict",
            max_age=8 * 3600,  # 8시간
        )
        return resp
    
    # 2. 쿠키로 관리자 판별
    cookie_token = request.cookies.get("dashboard_admin", "")
    is_admin = (
        ADMIN_TOKEN 
        and cookie_token 
        and hmac.compare_digest(cookie_token, ADMIN_TOKEN)
    )
    
    # 3. HTML 렌더 (TOKEN 주입 ❌)
    html = _get_html_template()
    html = html.replace("/*__ADMIN__*/", f"const IS_ADMIN = {str(is_admin).lower()};")
    # /*__TOKEN__*/ 라인은 삭제
    
    return HTMLResponse(html)
```

`index.html`:
- `/*__TOKEN__*/` 자리 삭제
- `adminHeaders()` 함수는 빈 객체 반환 (`return {};`)
- `fetch(...)`는 자동으로 쿠키 동봉 (same-origin이므로 기본 동작)

**검증**:
- [ ] 브라우저 view-source에서 토큰 문자열 검색 → 0건
- [ ] `?token=...` URL 접근 후 자동 리다이렉트 + URL에 token 사라짐
- [ ] 쿠키 8시간 후 만료, 재인증 요구

> ⚠ 이건 중간단계입니다. 멀티테넌트 전환 시 사용자별 세션 + CSRF 토큰으로 재설계됩니다.

### C5. git commit

```bash
git commit -m "security(dashboard): XSS/인증/CORS 강화

- renderSelectedStocks XSS 패치 (data-attribute 패턴)
- /api/search-stock 비관리자 KIS fallback 차단
- CORS 화이트리스트 명시화
- ADMIN_TOKEN HTML 인라인 제거 → HTTP-only 쿠키
- ISSUE-019 카테고리 C 완료"
```

---

## 5. 카테고리 D — 운영 로직 / UX 개선

### D1. 모니터 폐기 조건 (CRITICAL — 전략 변경)

> ⚠ 이 항목은 전략 명세 변경입니다. 펀드매니저 관점에서 가장 중요한 수정.

**문제**: 스크린샷에서 발견된 삼천당제약 -11.49% 케이스. 시스템이 추적할 가치가 없는 종목임을 인지하지 못하고 계속 모니터링.

**파일**:
- `config/strategy_params.yaml` — 신규 파라미터
- `config/settings.py` — Pydantic 모델 추가
- `src/core/monitor.py` — 폐기 로직 추가
- `src/models/stock.py` — `MonitorState`에 신규 상태 추가 (있으면 재사용)

**신규 파라미터** (`strategy_params.yaml`):
```yaml
screening:
  # ... 기존 ...
  
  # 모니터링 중 폐기 조건
  giveup_drop_pct: 3.0      # 시가 대비 -3% 이하로 빠지면 폐기
  giveup_check_after: "10:00"  # 이 시각 이후부터만 폐기 체크 (장 초반 변동성 보호)
  giveup_no_high_by: "11:00"   # 이 시각까지 신고가 미달성 시 폐기
```

**신규 Pydantic 모델 추가** (`settings.py`의 `ScreeningParams`에):
```python
class ScreeningParams(BaseModel):
    # ... 기존 ...
    giveup_drop_pct: float = Field(default=3.0, ge=0.5, le=20.0)
    giveup_check_after: str = "10:00"
    giveup_no_high_by: str = "11:00"
```

**`monitor.py`에 폐기 체크 로직 추가**:

```python
# TargetMonitor 클래스의 update() 또는 메인 loop 안에서
def _check_giveup(self) -> bool:
    """폐기 조건 체크. True 반환 시 모니터링 중단."""
    now = now_kst().time()
    s = self.params.screening
    
    check_after = time.fromisoformat(s.giveup_check_after)
    no_high_by = time.fromisoformat(s.giveup_no_high_by)
    
    if now < check_after:
        return False
    
    open_price = self.target.stock.open_price  # 시가 (스크리닝 시점에 저장 필요)
    current = self.target.stock.current_price
    
    # 조건 1: 시가 대비 -giveup_drop_pct% 이하
    if current and open_price:
        drop_pct = (current - open_price) / open_price * 100
        if drop_pct <= -s.giveup_drop_pct:
            logger.warning(
                f"[폐기] {self.target.stock.code} {self.target.stock.name} "
                f"시가 {open_price:,} → 현재 {current:,} ({drop_pct:+.2f}%)"
            )
            return True
    
    # 조건 2: 11:00까지 신고가 미달성
    if now >= no_high_by and not self.target.new_high_achieved:
        logger.warning(
            f"[폐기] {self.target.stock.code} {self.target.stock.name} "
            f"{s.giveup_no_high_by}까지 신고가 미달성"
        )
        return True
    
    return False
```

폐기 시 처리:
- `state = MonitorState.SKIPPED` (또는 `GIVEN_UP` 신규 추가)
- WebSocket 구독 해제
- 텔레그램 알림 (선택)
- 멀티 트레이드 활성화 시 다음 후보로 전환

**검증**:
- [ ] strategy_params.yaml에 신규 키 3개 추가
- [ ] `verify_phase1.py` 통과 (Pydantic 검증)
- [ ] DRY_RUN에서 시가 대비 큰 폭 하락 시뮬레이션 → 폐기 로그 출력
- [ ] 폐기된 종목은 모니터에서 제외되거나 별도 표시
- [ ] 백테스트 재실행 — 기존 결과(020150, 006400)에 영향 없는지 확인 (회귀)
- [ ] 이슈 트래커에 별도 ISSUE 등록 추천 (전략 변경이라 audit 가치 있음)

> ⚠ 이 변경은 백테스트 결과를 바꿀 수 있습니다. 반드시 회귀 테스트 후 적용.

---

### D2. 헤더 sticky + 모드 배지 항상 보이게

**파일**: `src/dashboard/templates/index.html`

**변경 (CSS)**:
```css
.header {
    background: #111827;
    border-bottom: 1px solid #1e293b;
    padding: 12px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    
    /* 추가 */
    position: sticky;
    top: 0;
    z-index: 1000;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}

/* LIVE 모드 시 헤더 강조 */
body.mode-live-active .header {
    border-bottom: 3px solid #ef4444;
    background: #1a0f0f;
}
body.mode-live-active .header::before {
    content: "⚠ LIVE TRADING ⚠";
    position: absolute;
    top: 0; left: 50%;
    transform: translateX(-50%);
    background: #ef4444;
    color: white;
    padding: 2px 12px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
```

**JS 변경 (updateUI)**:
```javascript
// trade_mode가 live면 body에 클래스 추가
if (d.trade_mode === 'live') {
    document.body.classList.add('mode-live-active');
} else {
    document.body.classList.remove('mode-live-active');
}
```

**검증**:
- [ ] 스크롤 시 헤더가 화면 상단에 고정
- [ ] DRY_RUN 모드에서 모드 배지 표시
- [ ] (가능하면 .env에서 임시로 trade_mode=live로 바꿔서) LIVE 표시 시각 강조 확인 → 다시 dry_run으로 복원

---

### D3. 일일 손실 한도 progress bar

**파일**: `src/dashboard/templates/index.html`

**변경 (HTML)**:

`당일 P&L` 카드 안에 progress bar 추가.

```html
<div class="card">
    <h3>당일 P&L</h3>
    <div class="value" id="daily-pnl">-</div>
    <div class="sub" id="daily-trades">거래 0건</div>
    
    <!-- 신규: 손실 한도 게이지 -->
    <div class="loss-limit-bar">
        <div class="loss-limit-fill" id="loss-limit-fill"></div>
        <div class="loss-limit-label" id="loss-limit-label">손실 한도 0% 사용</div>
    </div>
</div>
```

**CSS**:
```css
.loss-limit-bar {
    margin-top: 8px;
    height: 6px;
    background: #1e293b;
    border-radius: 3px;
    overflow: hidden;
    position: relative;
}
.loss-limit-fill {
    height: 100%;
    width: 0%;
    background: #22c55e;
    transition: width 0.3s, background 0.3s;
}
.loss-limit-fill.warning { background: #f59e0b; }
.loss-limit-fill.danger  { background: #ef4444; }
.loss-limit-label {
    font-size: 10px;
    color: #64748b;
    margin-top: 2px;
}
```

**JS** (`updateUI` 안):
```javascript
// 손실 한도 사용률
const lossLimitPct = d.daily_loss_limit_pct || 3.0;  // 백엔드에서 내려와야 함
const initialCash = d.initial_cash || d.available_cash || 1;
const lossUsedPct = d.daily_pnl < 0 
    ? Math.abs(d.daily_pnl) / initialCash * 100 
    : 0;
const usagePct = Math.min((lossUsedPct / lossLimitPct) * 100, 100);

const fill = document.getElementById('loss-limit-fill');
fill.style.width = usagePct + '%';
fill.className = 'loss-limit-fill';
if (usagePct >= 75) fill.classList.add('danger');
else if (usagePct >= 50) fill.classList.add('warning');

document.getElementById('loss-limit-label').textContent = 
    `손실 한도 ${usagePct.toFixed(0)}% 사용 (-${lossUsedPct.toFixed(2)}% / -${lossLimitPct}%)`;
```

**백엔드 변경** (`app.py to_dict()`):
- `daily_loss_limit_pct`: `at.params.risk.daily_loss_limit_pct` 추가
- `initial_cash`: `at._initial_cash` 추가

**검증**:
- [ ] DRY_RUN에서 P&L이 음수일 때 게이지가 차오름
- [ ] 50%/75% 임계치에서 색 변화

---

### D4. 모니터 카드 health 색상 띠

**파일**: `src/dashboard/templates/index.html`

**목적**: 운영자가 한눈에 "이 종목이 살아있는 후보인지 죽은 종목인지" 구별.

**변경 (CSS)**:
```css
.monitor-card {
    margin-bottom: 16px;
    padding: 12px 12px 12px 16px;  /* 좌측 패딩 키움 */
    background: #0d1117;
    border-radius: 6px;
    border: 1px solid #1e293b;
    border-left: 4px solid #475569;  /* 기본: 회색 */
    position: relative;
}
.monitor-card.health-watching { border-left-color: #3b82f6; }  /* 청색 - 정상 추적 */
.monitor-card.health-weak     { border-left-color: #eab308; }  /* 노랑 - 약한 신호 */
.monitor-card.health-dead     { 
    border-left-color: #475569;     /* 회색 */
    opacity: 0.6;
}
.monitor-card.health-holding  { border-left-color: #22c55e; }  /* 녹색 - 보유 중 */
.monitor-card.health-danger   {
    border-left-color: #ef4444;     /* 빨강 */
    animation: pulse-danger 2s infinite;
}
@keyframes pulse-danger {
    0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
    50%      { box-shadow: 0 0 0 8px rgba(239, 68, 68, 0); }
}
.dead-badge {
    position: absolute;
    top: 8px; right: 8px;
    background: #1e293b;
    color: #94a3b8;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 700;
}
```

**JS (`renderMonitor`)**:
```javascript
function renderMonitor(m) {
    // health 판정
    let healthClass = 'health-watching';
    let deadBadge = '';
    
    if (m.state === 'entered') {
        healthClass = 'health-holding';
        // 손절가까지 거리 < 1% 이면 danger
        if (m.current_price && m.hard_stop_price) {
            const distPct = (m.current_price - m.hard_stop_price) / m.current_price * 100;
            if (distPct < 1.0) healthClass = 'health-danger';
        }
    } else if (m.state === 'skipped' || m.state === 'given_up') {
        healthClass = 'health-dead';
        deadBadge = '<span class="dead-badge">폐기</span>';
    } else if (m.change_pct < 0) {
        healthClass = 'health-weak';
    } else if (m.change_pct < 2) {
        healthClass = 'health-weak';
    }
    
    return `
    <div class="monitor-card ${healthClass}">
        ${deadBadge}
        ... 기존 카드 내용 ...
    </div>`;
}
```

기존 monitor card의 인라인 style을 .monitor-card 클래스로 추출.

**검증**:
- [ ] 등락률 양수 종목 → 청색 띠
- [ ] 등락률 음수 종목 → 노랑 띠
- [ ] (D1 적용 후) 폐기된 종목 → 회색 띠 + dimmed + 폐기 배지
- [ ] 보유 중 → 녹색
- [ ] 손절가 근처 → 빨강 + pulse

---

### D5. 죽은 CSS `.price-levels` 살려서 가격 게이지

**파일**: `src/dashboard/templates/index.html`

**목적**: 손절가, 1차 매수가, 2차 매수가, 현재가, 고가, 목표가의 상대 위치를 한눈에.

**변경 (HTML, monitor card 안에 추가)**:
```html
<!-- 가격 레벨 게이지 -->
<div class="price-gauge">
    <div class="gauge-track"></div>
    <div class="gauge-marker hard-stop" style="left: ${hardStopPct}%" title="손절가"></div>
    <div class="gauge-marker buy2" style="left: ${buy2Pct}%" title="2차 매수"></div>
    <div class="gauge-marker buy1" style="left: ${buy1Pct}%" title="1차 매수"></div>
    <div class="gauge-marker target" style="left: ${targetPct}%" title="목표가"></div>
    <div class="gauge-marker high" style="left: 100%" title="고가"></div>
    <div class="gauge-current" style="left: ${currentPct}%"></div>
</div>
```

**CSS** (기존 `.price-levels`를 재활용):
```css
.price-gauge {
    position: relative;
    height: 32px;
    margin: 12px 0 8px;
}
.gauge-track {
    position: absolute;
    top: 14px;
    left: 0; right: 0;
    height: 4px;
    background: #1e293b;
    border-radius: 2px;
}
.gauge-marker {
    position: absolute;
    top: 10px;
    width: 2px;
    height: 12px;
    transform: translateX(-50%);
}
.gauge-marker.hard-stop { background: #ef4444; }
.gauge-marker.buy1      { background: #3b82f6; }
.gauge-marker.buy2      { background: #1e40af; }
.gauge-marker.target    { background: #22c55e; }
.gauge-marker.high      { background: #94a3b8; }
.gauge-current {
    position: absolute;
    top: 6px;
    width: 4px;
    height: 20px;
    background: #fbbf24;
    transform: translateX(-50%);
    border-radius: 2px;
    box-shadow: 0 0 8px rgba(251, 191, 36, 0.6);
}
```

**JS (`renderMonitor` 안 percent 계산)**:
```javascript
// 손절가(0%) ~ 고가(100%) 사이에서 각 가격 위치 계산
const min = m.hard_stop_price;
const max = m.intraday_high;
const range = max - min;
const pct = (price) => range > 0 ? Math.max(0, Math.min(100, (price - min) / range * 100)) : 50;

const hardStopPct = 0;
const buy2Pct     = pct(m.buy2_price);
const buy1Pct     = pct(m.buy1_price);
const targetPct   = pct(m.target_price);
const currentPct  = pct(m.current_price);
```

**검증**:
- [ ] 모니터 카드에 게이지 표시
- [ ] 현재가 변동에 따라 노란 마커 이동
- [ ] 손절가/매수가/목표가가 적절한 비율로 배치

---

### D6. EMERGENCY STOP 버튼

**파일**: `src/dashboard/templates/index.html`, `src/dashboard/app.py`, `src/main.py`

**HTML** (상단 컨트롤 영역, B2/B3 정리 후 빈 자리):
```html
<div class="emergency-controls">
    <button class="btn-emergency" id="btn-emergency-stop" onclick="confirmEmergencyStop()">
        🛑 EMERGENCY STOP
    </button>
    <span class="emergency-hint">미체결 취소 + 보유 청산 + 당일 매매 중단</span>
</div>
```

**CSS**:
```css
.emergency-controls {
    background: #1a0f0f;
    border: 1px solid #3b1e1e;
    border-radius: 8px;
    padding: 12px 16px;
    display: flex;
    align-items: center;
    gap: 16px;
}
.btn-emergency {
    background: #ef4444;
    color: white;
    border: none;
    padding: 12px 24px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 2px 8px rgba(239, 68, 68, 0.4);
    transition: all 0.2s;
}
.btn-emergency:hover {
    background: #dc2626;
    transform: translateY(-1px);
}
.emergency-hint {
    color: #94a3b8;
    font-size: 12px;
}
```

**JS**:
```javascript
function confirmEmergencyStop() {
    const confirmed = confirm(
        "EMERGENCY STOP을 실행합니다.\n\n" +
        "1. 모든 미체결 주문 취소\n" +
        "2. 보유 종목 시장가 청산\n" +
        "3. 당일 매매 중단\n\n" +
        "정말 실행하시겠습니까?"
    );
    if (!confirmed) return;
    
    fetch('/api/emergency-stop', { method: 'POST' })
        .then(r => r.json())
        .then(res => {
            alert(res.ok ? '✅ Emergency Stop 실행됨' : '❌ 실패: ' + res.msg);
        });
}
```

**백엔드 (`app.py`)**:
```python
@app.post("/api/emergency-stop")
async def api_emergency_stop(request: Request):
    if not _is_admin_request(request):
        raise HTTPException(403)
    
    if not state.autotrader:
        return {"ok": False, "msg": "AutoTrader 미연결"}
    
    try:
        await state.autotrader.emergency_stop()
        return {"ok": True}
    except Exception as e:
        logger.error(f"Emergency stop 실패: {e}")
        return {"ok": False, "msg": str(e)}
```

**main.py에 신규 메서드**:
```python
async def emergency_stop(self) -> None:
    """긴급 정지 — 미체결 취소 + 보유 청산 + 매매 중단."""
    logger.critical("EMERGENCY STOP 실행")
    self.notifier.notify_error("🛑 EMERGENCY STOP 실행됨")
    
    # 1. 미체결 주문 전량 취소
    await self._emergency_cancel_orders()
    
    # 2. 보유 종목 시장가 청산
    for mon in self._monitors:
        if mon.state == MonitorState.ENTERED:
            await self.trader.exit_position(mon.target, "EMERGENCY", mon.target.stock.current_price)
    
    # 3. 매매 중단
    self._running = False
    
    await self._fire_state_update()
```

**검증**:
- [ ] EMERGENCY STOP 클릭 → confirm 모달
- [ ] 확인 → 텔레그램 알림 + 미체결 취소 + 보유 청산 + `_running = False`
- [ ] 비관리자는 버튼 hidden 또는 disabled

---

### D7. 로그 단위 가독성 개선

**파일**: 로그를 생성하는 모듈 (`screener.py`, `monitor.py` 등)

**문제 (스크린샷)**:
```
삼성전자(005930) 프로그램순매수=-305,305,028,800 비중=-14.33% X
```

**개선 형식**:
```
005930 삼성전자  순매수 -3,053억  비중 -14.33%  ✗
066970 엘앤에프  순매수   +320억  비중 +21.17%  ✓
```

**유틸리티 함수 신설** (`src/utils/format.py` 신규 또는 기존 utils):
```python
def fmt_money_kor(amount: int) -> str:
    """원 단위 금액을 한국식 단위(억/조)로 포맷.
    
    Examples:
        305_305_028_800 → '+3,053억'
        -52_371_569_800 → '-523억'
        1_500_000_000_000 → '+1.5조'
    """
    if amount == 0:
        return "0원"
    
    sign = "+" if amount > 0 else "-"
    abs_amt = abs(amount)
    
    if abs_amt >= 1_000_000_000_000:  # 1조 이상
        return f"{sign}{abs_amt / 1_000_000_000_000:.1f}조"
    elif abs_amt >= 100_000_000:  # 1억 이상
        return f"{sign}{abs_amt // 100_000_000:,}억"
    elif abs_amt >= 10_000:  # 1만 이상
        return f"{sign}{abs_amt // 10_000:,}만"
    else:
        return f"{sign}{abs_amt:,}원"


def fmt_stock_log(code: str, name: str, net_buy: int, ratio: float, passed: bool) -> str:
    """스크리닝 로그 1줄 표준 포맷."""
    mark = "✓" if passed else "✗"
    name_padded = name.ljust(8)  # 한글 정렬은 부정확하지만 시각적 도움
    money = fmt_money_kor(net_buy).rjust(10)
    return f"{code} {name_padded}  순매수 {money}  비중 {ratio:+6.2f}%  {mark}"
```

**적용**: `screener.py` 등의 로그 생성 부분을 위 함수 호출로 교체.

**검증**:
- [ ] DRY_RUN 로그에 새 형식으로 출력
- [ ] 단위(억/조) 정확
- [ ] 부호(+/-) 명시

---

### D8. ETN/ETF 6자리 영숫자 코드 처리

**파일**: `src/dashboard/app.py`

**현재 (`resolve_input`)**:
```python
if text.upper().startswith("Q") and len(text) >= 6:
    return text
```

**문제**: 스크린샷의 `0007C0`(아크밀) 같은 코드는 Q-prefix가 아니지만 영숫자 6자리. 인식 실패.

**수정**:
```python
import re

def resolve_input(self, text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    
    # 6자리 숫자 코드
    if len(text) == 6 and text.isdigit():
        return text
    
    # 6자리 영숫자 코드 (ETN/ETF, 신주인수권 등)
    if re.fullmatch(r"[0-9A-Z]{6}", text.upper()):
        return text.upper()
    
    # 종목명으로 검색
    upper = text.upper()
    if upper in self._stock_name_reverse:
        return self._stock_name_reverse[upper]
    
    matches = self.search_stock(text)
    if len(matches) == 1:
        return matches[0]["code"]
    
    return None
```

**검증**:
- [ ] `0007C0` 입력 시 인식
- [ ] `Q12345` 입력 시 인식
- [ ] `005930` 입력 시 인식
- [ ] `삼성전자` 입력 시 변환

---

### D9. 손절가까지 거리 표시

**파일**: `src/dashboard/templates/index.html`

**변경 (`renderMonitor`)**:

기존:
```javascript
<div><span style="color: #64748b;">손절가:</span> <span style="color: #ef4444;">${fmtPrice(m.hard_stop_price)}</span></div>
```

신규:
```javascript
${(() => {
    if (!m.hard_stop_price || !m.current_price) {
        return `<div><span style="color: #64748b;">손절가:</span> <span style="color: #ef4444;">${fmtPrice(m.hard_stop_price)}</span></div>`;
    }
    const distPct = (m.current_price - m.hard_stop_price) / m.current_price * 100;
    const cls = distPct < 1 ? 'pnl-minus' : distPct < 2 ? 'price-flat' : '';
    return `<div>
        <span style="color: #64748b;">손절가:</span>
        <span style="color: #ef4444;">${fmtPrice(m.hard_stop_price)}</span>
        <span class="${cls}" style="font-size: 11px; margin-left: 4px;">(${distPct >= 0 ? '+' : ''}${distPct.toFixed(1)}%)</span>
    </div>`;
})()}
```

**검증**:
- [ ] 모니터 카드에 손절가 옆에 거리 표시
- [ ] 거리 < 1% 시 빨간색

---

### D10. git commit

```bash
git commit -m "feat(dashboard): 운영 UX + 폐기 로직

- 모니터 폐기 조건 (giveup_drop_pct, giveup_no_high_by)
- 헤더 sticky + LIVE 모드 강조
- 일일 손실 한도 progress bar
- 모니터 카드 health 색상 띠
- 가격 게이지 (.price-levels CSS 부활)
- EMERGENCY STOP 버튼
- 로그 단위 가독성 (억/조 포맷)
- ETN/ETF 6자리 영숫자 코드 처리
- 손절가까지 거리 표시
- ISSUE-019 카테고리 D 완료"
```

---

## 6. 카테고리 E — 코드 위생

### E1. `add_log` deque 재생성 버그 수정

**파일**: `src/dashboard/app.py`

**현재 (버그)**:
```python
def add_log(self, level: str, msg: str) -> None:
    entry = {...}
    if self.params and self.log_messages.maxlen != self.params.infra.dashboard_log_buffer_size:
        from collections import deque
        self.log_messages = deque(self.log_messages, maxlen=...)
    self.log_messages.append(entry)
```

**문제**: 매 로그 추가마다 maxlen 비교 + import + 가끔 deque 재생성. 핫 패스 비효율.

**수정**:
```python
# 파일 상단으로 import 이동
from collections import deque

# DashboardState.__init__에서 한 번만 설정
def __init__(self):
    ...
    self._log_buffer_size = 200  # 기본값
    self.log_messages: deque[dict] = deque(maxlen=self._log_buffer_size)

def configure_from_params(self, params) -> None:
    """params 로드 시 1회만 호출. deque 사이즈 갱신."""
    new_size = params.infra.dashboard_log_buffer_size
    if new_size != self._log_buffer_size:
        self._log_buffer_size = new_size
        self.log_messages = deque(self.log_messages, maxlen=new_size)

def add_log(self, level: str, msg: str) -> None:
    self.log_messages.append({
        "time": now_kst().strftime("%H:%M:%S"),
        "level": level,
        "msg": msg,
    })
```

`configure_from_params()`는 `attach_autotrader()` 안에서 1회 호출.

**검증**:
- [ ] `add_log()` 안에 import 문 0건
- [ ] 동일 동작 (deque 사이즈 정상)

---

### E2. 매직 넘버 → infra.dashboard_*

**파일**: `config/strategy_params.yaml`, `config/settings.py`, `src/dashboard/app.py`, `src/dashboard/templates/index.html`

**신규 파라미터** (`InfraParams`):
```python
class InfraParams(BaseModel):
    # ... 기존 ...
    
    # 대시보드 폴링/브로드캐스트 간격
    dashboard_ws_broadcast_interval_sec: float = Field(default=1.0, ge=0.1, le=10.0)
    dashboard_http_polling_interval_sec: float = Field(default=2.0, ge=0.5, le=30.0)
    dashboard_search_debounce_ms: int = Field(default=200, ge=50, le=2000)
    dashboard_ws_fallback_delay_ms: int = Field(default=3000, ge=500, le=10000)
```

**`strategy_params.yaml`에 추가**:
```yaml
infra:
  # ... 기존 ...
  dashboard_ws_broadcast_interval_sec: 1.0
  dashboard_http_polling_interval_sec: 2.0
  dashboard_search_debounce_ms: 200
  dashboard_ws_fallback_delay_ms: 3000
```

**`app.py` WebSocket 루프**:
```python
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    ...
    interval = (
        state.autotrader.params.infra.dashboard_ws_broadcast_interval_sec
        if state.autotrader 
        else 1.0
    )
    while True:
        await ws.send_json(state.to_dict())
        await asyncio.sleep(interval)
```

**`index.html`** — JS는 백엔드에서 이 값을 받아서 사용:
- `/api/status`나 페이지 로드 시 `infra` 섹션을 응답에 포함
- JS가 그 값을 `setInterval`/`setTimeout`에 사용

**검증**:
- [ ] grep으로 `asyncio.sleep(1)`, `asyncio.sleep(3)`, `setInterval(2000)`, `setTimeout(200)`, `setTimeout(3000)` 등 매직 넘버 0건
- [ ] yaml 값 변경 후 재기동 → 동작 변화 확인

---

### E3. `_check_admin` 일관성

**파일**: `src/dashboard/app.py`

`_check_admin()`은 X-Admin-Token 헤더만 보지만, C4 적용 후 쿠키 기반으로 통일됨. 헤더 vs 쿠키 vs URL 파라미터 3가지가 혼재하지 않도록 정리.

```python
def _is_admin_request(request: Request) -> bool:
    """관리자 권한 검증 통합. 쿠키 기반."""
    if not ADMIN_TOKEN:
        return True  # 토큰 미설정 = 모두 허용 (로컬 개발)
    
    cookie_token = request.cookies.get("dashboard_admin", "")
    return bool(cookie_token and hmac.compare_digest(cookie_token, ADMIN_TOKEN))


def _check_admin(request: Request) -> None:
    if not _is_admin_request(request):
        raise HTTPException(status_code=403, detail="관리자 권한 필요")
```

모든 보호 엔드포인트는 `Request` 객체를 받고 `_check_admin(request)` 호출.

**검증**:
- [ ] grep으로 `Header(None, alias="X-Admin-Token")` 0건
- [ ] 모든 mutating 엔드포인트가 _check_admin 호출

---

### E4. git commit

```bash
git commit -m "chore(dashboard): 코드 위생

- add_log deque 재생성 버그
- 매직 넘버 → infra.dashboard_* 파라미터화
- 인증 헤더/쿠키/URL 혼재 정리 → 쿠키 통일
- ISSUE-019 카테고리 E 완료"
```

---

## 7. 카테고리 F — 부수 정리 (선택)

### F1. PWA 준비 (선택, 셋업 가이드 후로 미뤄도 OK)

- `manifest.json` 추가
- `<link rel="manifest">`
- 아이콘 (192x192, 512x512)
- 모바일 터치 타겟 최소 44x44px 검토

> 지금은 우선순위 낮음. 셋업 가이드 끝나고 진행 권장.

### F2. CSP / 보안 헤더 (선택)

```python
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; ..."
    return response
```

> 우선순위 낮음. 멀티테넌트 전환 시 같이 도입.

---

## 8. 검증 시나리오 (end-to-end)

각 카테고리 완료 후 아래 시나리오를 모두 통과해야 "완료" 상태.

### 시나리오 1 — 콜드 스타트
1. 프로세스 모두 정지
2. `python -m src.main` 실행
3. 텔레그램에 시작 알림 + 대시보드 URL 도착
4. 대시보드 접속 → 로딩 화면 후 데이터 표시
5. **검증**:
   - [ ] 모드 배지 (DRY_RUN/PAPER/LIVE) 상단에 표시
   - [ ] 예수금이 "-"가 아닌 실제 값으로 표시
   - [ ] 시스템 로그에 INFO 메시지들 흐름
   - [ ] 브라우저 콘솔 에러 0건

### 시나리오 2 — 종목 변경 후 동기화
1. 텔레그램으로 `/target 005930,066970` 전송
2. **검증**:
   - [ ] 1초 이내 대시보드 "설정된 종목"에 두 종목 표시
   - [ ] main.py 로그와 대시보드 화면 일치
3. 대시보드에서 "수동 스크리닝" 클릭
4. **검증**:
   - [ ] 모니터 카드가 설정된 종목 기준으로만 생성됨 (mismatch 없음)
5. 텔레그램으로 `/clear`
6. **검증**:
   - [ ] 1초 이내 대시보드에서 "설정된 종목" 비워짐
   - [ ] 모니터 카드도 함께 정리됨 (동기화)

### 시나리오 3 — 비관리자 접속
1. Cloudflare Tunnel URL을 `?token=` 없이 접속
2. **검증**:
   - [ ] 종목 입력 영역, EMERGENCY STOP 버튼 hidden
   - [ ] 모니터 카드, 모드 배지는 보임 (read-only)
   - [ ] `/api/set-targets`, `/api/run-manual-screening`, `/api/emergency-stop` 직접 호출 시 403
   - [ ] `/api/search-stock?q=000001` 호출 시 캐시 hit 없으면 빈 결과 (KIS fallback 차단)

### 시나리오 4 — 모니터 폐기 동작 (D1)
1. DRY_RUN 모드, 시가 대비 큰 폭 하락한 종목을 수동 입력
2. 스크리닝 실행
3. 10:00 이후 (또는 임시로 시각 기준 변경)
4. **검증**:
   - [ ] 폐기 로그 출력
   - [ ] 모니터 카드 health-dead 색상 띠 + 폐기 배지
   - [ ] WebSocket 구독 해제 (또는 재구독 안 함)
   - [ ] 멀티 트레이드 활성 시 다음 후보로 자동 전환

### 시나리오 5 — 손절선 도달
1. DRY_RUN 모드, 보유 포지션 시뮬레이션
2. 가격을 손절가 근처로 조작 (또는 백테스트로 재현)
3. **검증**:
   - [ ] 카드 health-danger 클래스 (빨간 띠 + pulse)
   - [ ] 손절가 옆 거리 표시 빨간색
   - [ ] 가격 게이지의 노란 마커가 손절가 마커 근처로 이동

### 시나리오 6 — EMERGENCY STOP
1. DRY_RUN, 보유 포지션 1개 + 미체결 주문 1개
2. EMERGENCY STOP 클릭 → confirm
3. **검증**:
   - [ ] 텔레그램 긴급 알림
   - [ ] 미체결 취소 로그
   - [ ] 보유 청산 로그
   - [ ] `_running = False`
   - [ ] 대시보드에 매매 중단 상태 표시

### 시나리오 7 — 회귀 검증
1. 백테스트 재실행 (`python -m src.backtest.simulator`)
2. **검증**:
   - [ ] 020150, 006400 결과가 이전과 동일 (D1 폐기 조건이 회귀를 일으키지 않았는지)
3. `verify_phase1.py` 실행
4. **검증**:
   - [ ] 전체 통과

### 시나리오 8 — 24시간 안정성
1. DRY_RUN 모드로 24시간 가동
2. **검증**:
   - [ ] 메모리 leak 없음 (`tasklist /fi "imagename eq python.exe"`)
   - [ ] 로그 deque 200개 초과 안 함
   - [ ] WebSocket 연결 끊김/재연결 정상
   - [ ] KIS 토큰 자동 갱신 정상
   - [ ] 대시보드 응답성 유지

---

## 9. Definition of Done

모든 항목 체크 후 작업 완료 처리:

### 코드
- [ ] 카테고리 A~E 모든 변경 적용
- [ ] git commit 5건 이상 (카테고리별)
- [ ] grep 체크: `KISAPI(`, `Trader(`, `RiskManager(`가 `dashboard/app.py`에 0건
- [ ] grep 체크: 인라인 `onclick="...(...)"` 0건
- [ ] grep 체크: 매직 넘버 (sleep(1), sleep(3), setInterval(2000), 등) 0건

### 테스트
- [ ] 시나리오 1~8 모두 통과
- [ ] verify_phase1.py 통과
- [ ] 백테스트 회귀 통과
- [ ] DRY_RUN 24시간 무사고

### 문서
- [ ] `05_이슈_트래커.md`에 ISSUE-019 해결완료 등록
- [ ] `02_Phase_진행상황.md` 업데이트 (Phase 3 안정화 +α)
- [ ] `06_백테스트_결과.md`에 D1 적용 후 백테스트 결과 추가 기록
- [ ] `01_전략_마스터스펙.md`에 폐기 조건 추가 (3장 또는 5장)
- [ ] `07_개발지침.md`에 그림자 상태 안티패턴 사례 추가 (규칙 10 신설 권장)

### 배포 준비
- [ ] feature 브랜치 → main merge
- [ ] 태그 부여: `v0.2-pre-multitenant` 정도

### 그 다음 단계로 진행
- [ ] K 수석님이 셋업 가이드 요청 → Oracle Cloud + Claude Code 워크스페이스 셋업

---

## 10. 작업 중 자주 묻는 질문 / 함정

### Q1. AutoTrader가 대시보드를 import하면 순환 참조 안 나나요?
A. dashboard/app.py가 main.py를 import하지 않고, main.py가 dashboard.app.attach_autotrader만 import합니다. 단방향입니다. type hint는 `"AutoTrader"` 문자열로 forward reference 사용.

### Q2. 데몬 스레드 안에서 uvicorn 돌리는 게 안정적인가요?
A. 현재 구조 유지합니다. 다만 멀티테넌트 전환 시 별도 프로세스로 분리하는 게 운영상 더 안전합니다 (장애 격리). 그건 셋업 가이드 단계에서 다룹니다.

### Q3. 폐기 조건(D1)을 백테스트 데이터로 검증하려면?
A. `src/backtest/simulator.py`에도 동일한 폐기 로직이 들어가야 회귀가 의미가 있습니다. monitor.py와 simulator.py 양쪽에 같은 조건 적용 필수.

### Q4. 카테고리 D가 너무 크면 D1만 먼저 하고 나머지는 나중에 해도 되나요?
A. 됩니다. D1(폐기 조건)이 가장 critical, 나머지는 UX 개선이라 점진적 적용 가능. 다만 D2(헤더 sticky)와 D6(EMERGENCY STOP)은 LIVE 전환 전에 반드시 들어가야 합니다.

### Q5. 카테고리 작업 중간에 운영자(A)가 사용해야 하면?
A. git stash 후 main branch에서 운영, 작업 후 stash pop. 또는 작업은 별도 PC/VM에서.

### Q6. 회귀가 발생하면?
A. 카테고리 단위 commit이 있으므로 `git revert <commit>`으로 카테고리 단위 롤백 가능. 그래서 카테고리별 commit이 중요합니다.

---

## 11. 작업 완료 후 K 수석님이 Claude에게 보고할 것

작업 완료 시 다음을 알려주시면 셋업 가이드로 자연스럽게 이어갑니다:

1. **완료된 카테고리**: A/B/C/D/E (또는 부분 완료)
2. **검증 시나리오 통과 결과**: 1~8 중 통과/실패
3. **이슈 트래커**: ISSUE-019 상태
4. **수정 중 발견된 추가 이슈**: 있다면 (자주 있습니다)
5. **다음 진행 의향**: 셋업 가이드(원래 본론)로 진행 OK?

이 보고를 받으면 바로 **Oracle Cloud + Claude Code 워크스페이스 셋업 가이드 v1.0** 을 작성하겠습니다.

---

## 부록 A — git workflow 권장

```bash
# 작업 시작
git checkout -b feature/dashboard-shadow-state-fix

# 카테고리별 작업 → commit
# A 끝나면
git add -A && git commit -m "feat(dashboard): 그림자 상태 해소 (cat A)"

# B 끝나면
git add -A && git commit -m "refactor(dashboard): 데드 코드/UI 제거 (cat B)"

# ... 카테고리 E까지

# 검증 통과 후 main으로 merge
git checkout main
git merge --no-ff feature/dashboard-shadow-state-fix
git tag v0.2-pre-multitenant
git push origin main --tags
```

## 부록 B — 작업 중 디버깅 팁

**그림자 상태가 의심될 때**:
```python
# main.py 임시 디버그 코드
import id
print(f"AutoTrader.api id: {id(self.api)}")

# dashboard/app.py 임시 디버그 코드
print(f"Dashboard.api id: {id(state.api)}")
```
두 id가 같으면 단일 인스턴스, 다르면 그림자 상태.

**대시보드 데이터 흐름 추적**:
- 브라우저 dev tools → Network → WS → 메시지 탭에서 매초 받는 JSON 확인
- 그 JSON과 main.py 로그를 1초 간격으로 비교

**WebSocket vs HTTP 폴링 구분**:
- 브라우저 dev tools → Network → 'ws' 필터 → 연결되어 있으면 WS 사용 중
- 'fetch/XHR' 필터 → `/api/status` 호출 빈도 보면 HTTP 폴링 여부 확인

---

**문서 끝.**

**작업 시작 전 마지막 한 마디** (펀드매니저):
> 이 문서의 모든 카테고리는 인프라/UX 개선입니다. 알파를 늘리지는 않습니다.
> Phase 0 백테스트는 이 작업과 **반드시 병행**해 주세요. 작업 끝났을 때
> 백테스트 결과가 없으면, 잘 닦인 시스템을 들고 어디로 갈지 모르는 상태가 됩니다.
