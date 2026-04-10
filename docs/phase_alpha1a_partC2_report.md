# AUTOTRADE α-1a Part C-2: 코드 배포 + .env 업로드 + 첫 startup 검증 보고서

작성일: 2026-04-08 (KST)
서버: ubuntu@134.185.115.229

---

## 작업 항목별 결과

| 항목 | 상태 | 비고 |
|------|------|------|
| C-2-1 사전 확인 | ✅ | git clean, .env 존재, 원격 디렉토리 확인 |
| C-2-2 코드 배포 | ✅ | tar+scp (rsync 미설치 폴백), 160KB / 64파일 |
| C-2-3 .env 업로드 + 검증 | ✅ | USE_LIVE_API=false 확인 (수동 수정), 권한 600 |
| C-2-4 pip install | ✅ | 전 패키지 설치 완료 |
| C-2-5 startup 검증 | ✅ | 15초 실행 → SIGTERM → AUTOTRADE 종료 확인 |

---

## C-2-2 코드 배포 상세

```
방법: tar + scp (rsync Windows 미설치로 폴백)
파일 수: 64
압축 크기: 160KB
제외: dist/ node_modules/ __pycache__ .git *.pyc
```

원격 코드 검증:
```
database.py:34 → PRAGMA journal_mode=WAL ✅
main.py       → def main(): SIGTERM handler 포함 ✅
```

---

## C-2-3 .env 검증 결과

게이트 발동: `USE_LIVE_API=true` 발견 → 즉시 보고 → `false` 수정 후 진행.

| 키 | 최종 값 | 판정 |
|----|---------|------|
| USE_LIVE_API | false | ✅ |
| TRADE_MODE | dry_run | ✅ |
| 권한 | -rw------- (600) | ✅ |
| 백업 | .env.bak 생성 | ✅ |

**로컬 PC `.env` 동기화 필요**: K 수석님 직접 처리 (`USE_LIVE_API=false` 설정).

---

## C-2-4 주요 패키지 버전

| 패키지 | 버전 |
|--------|------|
| aiohttp | 3.13.5 |
| fastapi | 0.135.3 |
| pykrx | 1.2.4 |
| pandas | 2.3.3 |
| python-telegram-bot | 22.7 |
| websockets | 16.0 |
| uvicorn | 0.44.0 |
| pydantic-settings | 2.13.1 |

---

## C-2-5 startup 검증 로그 (핵심)

```
AUTOTRADE 시작 (모드: dry_run)
KIS 토큰 발급 완료 (만료: 2026-04-08 23:45)       ← 모의투자 토큰
WebSocket 키 발급 완료
선물 실시간 구독: 101S3000 (TR: H0IFCNT0)          ← 청산 조건 ④ 연결
DB 초기화 완료: /home/ubuntu/AUTOTRADE/data/trades.db
종목 마스터 로드: 2773건
대시보드 서버 시작 (port=8503)
cloudflared 미설치 — 원격 모니터링 비활성          ← C-3 예정, 정상
SIGTERM 수신 → graceful shutdown 시작              ← timeout 15s 신호
사용자 종료 (Ctrl+C)
텔레그램 명령 수신 중지
KIS API 연결 해제
AUTOTRADE 종료
```

텔레그램 CancelledError traceback: `stop()` 내부 정상 예외, 종료 플로우 영향 없음.

---

## 비고

- cloudflared 미설치: C-3에서 Cloudflare Named Tunnel 설정 예정. 현재 warning 정상.
- iptables 포트 8503 미개방: Cloudflare Tunnel이 로컬 포트로 연결하므로 inbound 불필요. C-3에서 확인.
- KIS 토큰 캐시 파일 (`token_paper.json`): 서버에 생성됨. .gitignore에 등록되어 있으므로 배포에 포함되지 않음.
