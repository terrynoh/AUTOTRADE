# WAL 모드 + SIGTERM graceful shutdown 구현 보고서

작성일: 2026-04-07
브랜치: feature/dashboard-fix-v1

---

## 변경 파일

| 파일 | 변경 라인 수 | 내용 |
|------|------------|------|
| `src/storage/database.py` | +1 | WAL 모드 활성화 |
| `src/main.py` | +24 | SIGTERM graceful shutdown 7단계 |

매매 로직 변경: 0줄

---

## 1. WAL 모드 (database.py)

### 변경 위치

`src/storage/database.py:34` — `_init_tables()` 진입 직후

```python
conn.execute("PRAGMA journal_mode=WAL")
```

### 검증 결과

```
journal_mode = wal
WAL OK
```

---

## 2. SIGTERM graceful shutdown 7단계 (main.py)

### 변경 위치

`src/main.py` — `main()` 함수 전체 교체 (`asyncio.run()` → `loop.run_until_complete()`)

### 7단계 흐름

| 단계 | 위치 | 내용 |
|------|------|------|
| 1 | `main()` signal handler | SIGTERM 수신 → `_sigterm_handler()` 진입 |
| 2 | `_sigterm_handler()` | `asyncio.all_tasks(loop)` 전체 `.cancel()` |
| 3 | `run()` except 블록 | `CancelledError` → `"사용자 종료"` 로그 |
| 4 | `run()` finally | 미완료 태스크 추가 cancel + gather |
| 5 | `run()` finally | `notifier.stop_polling()` — 텔레그램 봇 중지 |
| 6 | `run()` finally | `api.disconnect()` — KIS WebSocket + REST 세션 종료 |
| 7 | `run()` finally | `tunnel.stop()` + `logger "AUTOTRADE 종료"` |

### OS 호환성

| OS | `add_signal_handler` | 동작 |
|----|---------------------|------|
| Linux (Oracle Cloud) | 지원 | SIGTERM → 핸들러 등록 완료 |
| Windows (로컬 PC) | 미지원 (`NotImplementedError`) | `except` 로 무시, `Ctrl+C` (KeyboardInterrupt) 경로 유지 |

### 검증 결과 (Windows 로컬)

```
add_signal_handler not supported (Windows)   ← 정상 (Windows 폴백)
SIGTERM handler called
CancelledError received
main CancelledError caught
SIGTERM graceful shutdown: OK
```

핸들러 직접 호출 시뮬레이션으로 `CancelledError` 전파 경로 확인.

---

## 비고

- `loop.run_until_complete(loop.shutdown_asyncgens())` — asyncio generator 정리 (Python 3.10+ 표준 패턴)
- `loop.close()` — 이벤트 루프 명시적 종료
- systemd `KillSignal=SIGTERM` (기본값) → 이 핸들러가 수신
- `TimeoutStopSec` 내 finally 블록 완료 보장
