# 사전 조사 4/5 — launcher.py systemd 호환성

작성일: 2026-04-07
브랜치: feature/dashboard-fix-v1
git 상태: 미추적 파일만(??), 추적 파일 수정 없음 — 조사 진행

---

## §0. 사전 학습

이전 보고서(recon_01~03) 재독 생략. 사전 확정 사실 적용.

---

## §5-1. launcher.py 진입 구조

**전체 라인 수: 186**

### `if __name__ == "__main__"` 블록

```python
# launcher.py:180-186
if __name__ == "__main__":
    if "--schedule" in sys.argv:
        logger.info("AUTOTRADE 런처 (스케줄 모드) — 매일 09:00 KST 자동 시작")
        asyncio.run(schedule_loop())
    else:
        logger.info("AUTOTRADE 런처 — 즉시 시작")
        asyncio.run(launch_dashboard())
```

argparse 미사용. `"--schedule" in sys.argv` 단순 체크.

### 즉시 시작 모드 (`python launcher.py`)

`asyncio.run(launch_dashboard())` 실행:
1. 포트 점유 프로세스 정리 (`_kill_port`)
2. `subprocess.Popen([sys.executable, "-m", "src.dashboard.app"], ...)` — **대시보드만** 서브프로세스로 기동
3. Cloudflare Tunnel 시작
4. 텔레그램으로 URL 발송
5. `while True: await asyncio.sleep(30)` 루프로 대시보드 프로세스 생존 체크

→ **`src/main.py` (AutoTrader) 를 실행하지 않음.** 대시보드 단독 기동.

### `--schedule` 모드 (`python launcher.py --schedule`)

`asyncio.run(schedule_loop())` 실행:

```python
# launcher.py:160-177
async def schedule_loop() -> None:
    while True:
        now = now_kst()
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_sec = (target - now).total_seconds()
        await asyncio.sleep(wait_sec)
        await launch_dashboard()
```

매일 09:00 KST까지 `asyncio.sleep` 후 `launch_dashboard()` 호출 — 이후 동일.

---

## §5-2. main.py 진입 구조

### `if __name__ == "__main__"` 블록

```python
# src/main.py:1015-1020
def main():
    asyncio.run(AutoTrader().run())

if __name__ == "__main__":
    main()
```

argparse 미사용. `python -m src.main` 또는 `python src/main.py` → `AutoTrader().run()` 직행.

### AutoTrader.run() 내 dashboard 시작 위치

```python
# src/main.py:199-200
# 대시보드 서버 시작 (API 연결 + 잔고 확인 후 → attach_autotrader 가능)
self._start_dashboard_server()
```

`run()` 메서드 내 초기화 시퀀스에서 KIS API 연결 + 잔고 확인 후 호출.
`_start_dashboard_server()`는 `threading.Thread(target=_run, daemon=True)` 로 uvicorn 기동 후 `attach_autotrader(self)` 호출.

### systemd 진입점 후보

`python -m src.main` 하나로 AutoTrader + 대시보드(데몬 스레드) 모두 기동됨.

---

## §5-3. 시그널 핸들러

`grep signal\. SIGTERM SIGINT signal_handler asyncio\.signal` 결과:

```
launcher.py:50    os.kill(int(pid), signal.SIGTERM)
```

`launcher.py:50` 은 포트 정리 목적으로 다른 프로세스에 SIGTERM을 **보내는** 코드. 자신이 시그널을 **받는** 핸들러가 아님.

`src/main.py`, `src/` 전체: SIGTERM/SIGINT 핸들러 등록 코드 없음.

**결론:** SIGTERM을 받아서 처리하는 핸들러 없음.

단, `asyncio.run(AutoTrader().run())` 의 `finally` 블록(`src/main.py:242-253`)에서 다음을 정리:
- 미완료 태스크 취소 (`task.cancel()`)
- `self.notifier.stop_polling()` — 텔레그램 폴링 중지
- `self.api.disconnect()` — KIS WebSocket 종료
- `self._tunnel.stop()` — Cloudflare Tunnel 종료

이 `finally`는 `KeyboardInterrupt`와 `asyncio.CancelledError`를 `except`로 잡을 때 실행됨 (`src/main.py:237`).

**SIGTERM 동작:** systemd가 SIGTERM을 보내면 Python 기본 동작은 `KeyboardInterrupt`가 아니라 즉시 프로세스 종료. `finally`가 실행된다는 보장 없음. graceful shutdown 미보장.

---

## §5-4. 포트 정리 코드

함수: `_kill_port(port: int)` — `launcher.py:37-56`

```python
# launcher.py:37-56
def _kill_port(port: int) -> None:
    """지정 포트를 점유 중인 프로세스 종료 (Windows)."""
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True, text=True, timeout=5,
    )
    for line in result.stdout.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            parts = line.split()
            pid = parts[-1]
            if pid.isdigit() and int(pid) > 0:
                os.kill(int(pid), signal.SIGTERM)
```

**Windows 의존성:**
- `netstat -ano` 명령은 Windows와 Linux 모두 존재하나, 출력 포맷이 다름
- Windows `netstat -ano` 출력: `TCP 0.0.0.0:8503 0.0.0.0:0 LISTENING 12345`
- Linux `netstat -ano` 출력: 마지막 컬럼이 PID/Program (`12345/python`) 형식 — `parts[-1].isdigit()` 체크가 실패할 수 있음
- 함수 docstring에 명시적으로 `(Windows)` 표기

**Linux 동작:** 명령 자체는 실행되지만 PID 파싱이 실패할 가능성 있음. 포트 정리 silent 실패.

---

## §5-5. OS 의존성 grep

### Windows-only 패키지 import

```
hit 없음
```

`import win32`, `import wmi`, `import winreg`, `import pywin32`, `os.startfile`, `tasklist`, `taskkill`, `wmic` — 전부 없음.

### 경로 하드코딩 (`C:\`, `C:/`, `\\Users\\`)

hit 있음 — 전부 `setup_obsidian_autotrade.py` 내 문서 문자열:

```
setup_obsidian_autotrade.py:5    기본 경로: C:/Users/terryn/Documents/Obsidian/AUTOTRADE/
setup_obsidian_autotrade.py:38   **프로젝트 경로:** `C:\\Users\\terryn\\AUTOTRADE`
setup_obsidian_autotrade.py:107  > 원본: `C:\\Users\\terryn\\AUTOTRADE\\CLAUDE.md`
setup_obsidian_autotrade.py:255  cd C:\\Users\\terryn\\AUTOTRADE
setup_obsidian_autotrade.py:343  > 파일: `C:\\Users\\terryn\\AUTOTRADE\\...`
setup_obsidian_autotrade.py:499  cd C:\\Users\\terryn\\AUTOTRADE
setup_obsidian_autotrade.py:593  C:\\Users\\terryn\\AUTOTRADE\\
setup_obsidian_autotrade.py:702  cd C:\\Users\\terryn\\AUTOTRADE
```

`setup_obsidian_autotrade.py`는 Obsidian 초기화 도구로 운영 코드 아님.
`src/`, `launcher.py`, `config/` 내 Windows 경로 하드코딩 없음.

### `launcher.py:97` — `CREATE_NO_WINDOW`

```python
creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
```

`sys.platform` 조건부로 적용. Linux에서는 `creationflags=0` — 영향 없음.

**결론:** `src/` + `config/` 내 OS 의존성 없음. `_kill_port()` 의 `netstat -ano` 파싱이 Linux에서 실패할 수 있으나, 클라우드에서 launcher.py Path B를 사용하지 않으므로 영향 없음.

---

## §5-6. 텔레그램 봇 polling — 단일 인스턴스 보장

### 폴링 시작 코드

`src/utils/notifier.py:184-200`:
```python
self._app = Application.builder().token(self.bot_token).build()
# ... 핸들러 등록 ...
await self._app.initialize()
await self._app.start()
await self._app.updater.start_polling(drop_pending_updates=True)
```

`AutoTrader.__init__` 에서 `Notifier` 생성 → `AutoTrader.run()` 내 어느 시점에서 `start_polling` 호출.

### 단일 인스턴스 보장 메커니즘

PID 락 파일, 포트 락, 파일 락 등 없음.

**단일 인스턴스 보장 메커니즘 없음.**

systemd `[Service]` 단위로 기동 시 systemd가 단일 인스턴스를 보장.

---

## §5-7. requirements.txt

**전체 의존성 개수: 13개**

```
aiohttp>=3.9
websockets>=12.0
pandas>=2.0
pykrx>=1.0
pydantic>=2.0
pydantic-settings>=2.0
pyyaml>=6.0
loguru>=0.7
fastapi>=0.110
uvicorn>=0.27
python-telegram-bot>=20.0
pytest>=7.0
pytest-asyncio>=0.23
```

**Windows-only 패키지:** 없음.

**인증 관련 패키지 (bcrypt, passlib, itsdangerous, python-jose 등):** 없음.

---

## §6. 발견 사항

1. **launcher.py는 AutoTrader를 기동하지 않음**: `launch_dashboard()`는 `src.dashboard.app`만 서브프로세스로 기동. `src/main.py`는 호출하지 않음. 클라우드 systemd는 `python -m src.main`을 직접 진입점으로 써야 함.

2. **SIGTERM graceful shutdown 미보장**: `src/main.py`에 SIGTERM 핸들러 없음. systemd가 SIGTERM → `TimeoutStopSec` 후 SIGKILL 순서로 처리할 때 `finally` 블록 실행이 보장되지 않음 (KIS WebSocket 미정리, DB flush 미실행 가능성).

3. **`_kill_port()` Linux 파싱 실패 가능**: `netstat -ano` 출력 포맷 차이로 PID 추출 실패 가능. 클라우드에서 launcher.py를 사용하지 않으면 영향 없음.

4. **인증 패키지 미포함**: `bcrypt`, `passlib`, `python-jose` 등 α-1 세션/쿠키 인증 구현에 필요한 패키지가 `requirements.txt`에 없음. α-1 구현 시 추가 필요.
