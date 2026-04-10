# ISSUE-035 종결 리포트

**종결일:** 2026-04-08  
**commit:** `62e115e`  
**영향 파일:** `src/main.py` 1개, +26/-12줄

---

## 결론

`main.py:955-958` 의 `threading.Thread + uvicorn.run(loop=미지정)` cross-loop 패턴이 근본 원인.  
옵션 (B) — 동일 event loop 의 `asyncio.create_task(server.serve())` 패턴 — 으로 완전 해결.

---

## 진단부터 종결까지 타임라인

| 단계 | 결과 |
|---|---|
| 발견 | dashboard `/api/set-targets` 100% 실패 (RuntimeError) |
| 1차 가설 (Code) | aiohttp cross-loop 코드 구조 버그 — 환경 무관 |
| 모순 발견 | "로컬 OK, 클라우드 NG" → 환경 무관 가설과 충돌 |
| 진입점 분석 | 로컬/클라우드 모두 `python -m src.main` 동일 확정 |
| 라이브러리 비교 | aiohttp/fastapi 양쪽 동일 (3.13.5 / 0.135.3) |
| OS 차이 식별 | 로컬 win32 (Proactor), 클라우드 Linux (Selector) |
| 격리 재현 | `/tmp/repro_issue035.py` 30줄 → cross-loop 에러 정확히 재현 |
| (B) 패턴 사전 검증 | `/tmp/repro_fix_b.py` → 같은 loop 통과 PASS |
| 분석 리포트 | 변경 4개 위치 + 위험 요소 식별 + α-1a 원칙 부합 확인 |
| 본 코드 적용 | `main.py` 1파일, +26/-12줄, commit `62e115e` |
| 클라우드 배포 | scp + diff 검증 + 백업본 확보 (`main.py.bak.issue035`) |
| 정적 검증 | syntax + import 둘 다 통과 |
| 가동 확인 | `대시보드 서버 시작 (port=8503, in-loop task)` 로그 확인 |
| 직접 재현 검증 | 삼성/SK하이닉스/카카오 3종목 정상 등록 + 자동 스크리닝 가동 |

---

## 근본 원인 상세

```
[AutoTrader loop - main.py:1018]
  aiohttp.ClientSession 생성 (kis.py:172)
  ↓
  _start_dashboard_server() (main.py:948)
    threading.Thread(target=uvicorn.run(...))  ← 별도 스레드, 별도 loop 생성
    attach_autotrader(self)                    ← state.autotrader 주입
  ↓
[uvicorn loop - 별도 스레드]
  api_set_targets() (app.py:265)
    await api.get_current_price()
      async with self._session.get(...)        ← loop-A session을 loop-B에서 호출
        TimerContext.__enter__()
          asyncio.current_task() == None?      ← Linux Selector에서 RuntimeError
```

**OS 의존성:**
- Linux `SelectorEventLoop`: cross-loop 즉시 거부 → RuntimeError
- Windows `ProactorEventLoop`: 동일 패턴 통과 (잠재 버그 숨겨짐)

---

## 수정 내용 (commit 62e115e)

### 변경 1: `_start_dashboard_server()` 전체 교체

```python
# 변경 전 (cross-loop 버그)
def _start_dashboard_server(self) -> None:
    def _run():
        uvicorn.run(dashboard_app, host="0.0.0.0", port=port, log_level="warning")
    t = threading.Thread(target=_run, daemon=True, name="dashboard")
    t.start()
    attach_autotrader(self)

# 변경 후 (동일 loop task)
def _start_dashboard_server(self) -> "asyncio.Task":
    attach_autotrader(self)  # race window 최소화
    config = uvicorn.Config(dashboard_app, host="0.0.0.0", port=port,
                            log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    self._dashboard_server = server
    task = asyncio.create_task(server.serve(), name="dashboard_server")
    return task
```

### 변경 2: 호출부 반환값 수신 (main.py:200)
```python
dashboard_task = self._start_dashboard_server()
```

### 변경 3: tasks 리스트에 dashboard_task 추가 (main.py:216)
```python
tasks = [
    dashboard_task,   # ← 추가: asyncio.wait 감시 + finally cancel 대상
    asyncio.create_task(self._schedule_screening(), ...),
    ...
]
```

### 변경 4: finally 블록 graceful shutdown (main.py:242)
```python
if hasattr(self, "_dashboard_server") and self._dashboard_server is not None:
    self._dashboard_server.should_exit = True
```

### 부수: `import threading` 제거 (사용처 0)

---

## 핵심 교훈

1. **Windows ProactorEventLoop 가 Linux SelectorEventLoop 보다 cross-loop 검사가 느슨하다.**  
   Windows 에서 도는 코드를 Linux 로 옮길 때 항상 의심해야 할 첫 번째 차이.

2. **`threading.Thread + uvicorn.run` 패턴은 cross-loop 잠재 버그.**  
   같은 프로세스에서 AutoTrader loop 와 dashboard handler 가 통신해야 할 때는 절대 쓰지 말 것.  
   `asyncio.create_task(server.serve())` 가 정석.

3. **`requirements.txt` 핀 부재가 분산된 환경 차이를 만든다.**  
   α-1b 또는 α-2 에서 핀 작업 필요.

4. **격리 재현 스크립트는 가설 검증의 결정타.**  
   본 코드 수정 전에 30줄로 재현 + 30줄로 수정안 검증. 다음 ISSUE 에서도 재사용.

5. **"로컬에서 됐다" 는 절대 증거가 아니다.**  
   OS / 라이브러리 / 진입점 / 환경 변수 중 어느 하나라도 다르면 다른 시스템.

6. **dashboard handler 는 자기 loop 를 알 필요가 없어야 한다.**  
   (A)/(C) 옵션이 dashboard 를 손대는 우회였던 이유.  
   결합 버그는 결합 지점 (main.py) 에서 고치는 게 정석.  
   α-1a 원칙의 글자와 정신이 일치한 사례.
