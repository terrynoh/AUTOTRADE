# AUTOTRADE — 한국 주식 자동매매 시스템

KOSPI/KOSDAQ 장중 90분 윈도우 (09:50~11:20) 단타 자동매매.
외인/기관 지지 구조 확인 후 눌림 구간에서 분할 매수 → 목표가/손절/타임아웃 기반 청산.

> **현재 버전**: vLive 2.1 (R16 Phase 1 + Phase 2 B + W-SAFETY-1)
> **운영 모드**: LIVE 전용 (DRY_RUN/paper 모드 전면 폐기, 2026-04-17 전환)
> **운영 환경**: GCP Seoul (asia-northeast3-a)

---

## 요구사항

- Python 3.11+ (venv 권장)
- 한국투자증권 Open API 앱키 (https://apiportal.koreainvestment.com)
- 실거래 계좌 (모의투자 미지원 — R16 이후)
- HTS ID (체결통보 구독용)

## 설치

```bash
# 저장소 클론 후
cd /path/to/AUTOTRADE

# 가상환경 생성 + 패키지 설치
./setup.sh          # Linux/WSL
setup.bat           # Windows

# .env 설정
cp .env.example .env
# .env 편집: KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, KIS_HTS_ID,
#            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DASHBOARD_ADMIN_TOKEN 입력
chmod 600 .env      # 운영 서버 권장 권한
```

## 실행

```bash
# 통합 실행 (매매 엔진 + 대시보드)
python run.py

# 대시보드만 단독 실행 (뷰어 모드)
python run.py --dashboard-only

# 매매 엔진만 (systemd 서비스로 운영하는 경우)
python -m src.main
```

**운영 서버 (GCP Seoul)**: systemd 의 `autotrade.service` 로 자동 기동.
cron 06:35 에 `trading_day_start.sh` 가 거래일 판정 후 서비스 시작.

## 환경변수 (.env 8개)

| 변수 | 설명 |
|------|------|
| `KIS_APP_KEY` / `KIS_APP_SECRET` | KIS Open API 인증 |
| `KIS_ACCOUNT_NO` | 실거래 계좌번호 (10자리) |
| `KIS_HTS_ID` | 체결통보 구독용 HTS ID (필수) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 텔레그램 알림 |
| `DASHBOARD_PORT` | 대시보드 포트 (기본 8503) |
| `DASHBOARD_ADMIN_TOKEN` | 대시보드 제어 버튼 사용 토큰 |
| `LOG_LEVEL` | 로그 레벨 (기본 INFO, 개발 DEBUG) |

## 매매 명세 (요약)

**시간 구조** (KST):

| 시각 | 이벤트 |
|------|--------|
| 06:35~06:40 | cron → 거래일 판정 후 서비스 시작 + 텔레그램 URL 발송 |
| 09:00~09:50 | 지지 형성 구간 (매매 대상 아님) |
| 09:30~09:50 | 대시보드에서 수동 종목 입력 |
| 09:50 | 정규 스크리닝 (`is_final=True`) |
| 09:55 | 신고가 감시 시작 |
| 10:00~10:55 | 신규 진입 발주 윈도우 |
| 10:55 | 매수 마감 (entry_deadline) |
| 11:00 | 매매 철학 시한 (last-line defense) |
| 11:20 | 강제 청산 |
| 15:30 | 장 마감 + 일일 리포트 텔레그램 |

**매수가** (프로그램 순매수 비중 기반 Double/Single 분기, R-11/R-13):

| 시장 | 비중 | 1차 | 2차 | 손절 |
|------|------|-----|-----|------|
| KOSPI | Double (≥10%) | 고가 -1.9% | 고가 -2.4% | -3.0% |
| KOSPI | Single (<10%) | 고가 -2.5% | 고가 -3.5% | -4.0% |
| KOSDAQ | Double (≥10%) | 고가 -2.9% | 고가 -3.9% | -4.4% |
| KOSDAQ | Single (<10%) | — | — | **매매 제외** |

**5 청산 조건**:

| 조건 | 임계값 |
|------|--------|
| 하드 손절 | Double/Single별 차등 |
| 타임아웃 | 매수 후 저점 갱신 후 20분 |
| 목표가 | (confirmed_high + post_entry_low) / 2 |
| 선물 급락 | KOSPI200 선물 -1% |
| 강제 청산 | 11:20 |

**리스크 관리** (R-12):

- 지수 급락 (당일 선물 고점 -1.5%) → 신규 매수 차단
- 일일 손절 2회 도달 → 신규 매수 차단
- 일일 손실 한도 -3.0%

파라미터 전체: `config/strategy_params.yaml` 참조.

## 디렉토리 구조

```
AUTOTRADE/
├── src/
│   ├── main.py                 # AutoTrader 엔진 (전체 루프)
│   ├── core/                   # Watcher, Coordinator, Trader, Screener, RiskManager
│   ├── kis_api/                # KIS REST + WebSocket + 체결통보
│   ├── models/                 # Order, Position, StockCandidate, TradeRecord
│   ├── storage/                # SQLite trades.db + TradeLogger
│   ├── dashboard/              # FastAPI 실시간 대시보드
│   └── utils/                  # notifier (Telegram), logger (loguru), market_calendar
├── config/
│   ├── settings.py             # Pydantic Settings
│   ├── strategy_params.yaml    # 매매 명세 (동결)
│   └── stock_master.json       # 종목명 캐시
├── scripts/                    # cron 스크립트 (trading_day_start.sh 등) + daily_archive.sh
├── logs/                       # 런타임 로그 + sync_logs.bat / fetch_archive.bat
├── data/                       # SQLite trades.db
├── docs/                       # 작업 명령서 아카이브
└── CLAUDE.md                   # 신규 Claude 세션용 프로젝트 명세 (상세)
```

## 안전장치 (W-SAFETY-1, 2026-04-17)

- **F1 SA-5e**: 시작 시 미청산 포지션 감지 → 매매 거부 + 텔레그램 알림
- **F2′**: 체결통보 매칭 실패 (REST timeout 오진) → critical 알림
- **H2**: 11:20 강제 청산 실패 감지 → critical 알림
- **VI-Observer**: 장 상태 필드 (`TRHT_YN`, `MRKT_TRTM_CLS_CODE`, `HOUR_CLS_CODE`, `VI_STND_PRC` 등) 변화 감지 → 로그 + 당일 종목별 첫 변화 텔레그램 (Stage 1 관찰자 모드)

## 운영 로그 관리

```bash
# 즉시 진단 (문제 발생 시 수동 실행)
logs\sync_logs.bat          # 운영 서버에서 실시간 로그/시스템 상태 수집

# 장 마감 후 아카이브 다운로드 (사후 분석)
logs\fetch_archive.bat                # 어제 아카이브
logs\fetch_archive.bat 2026-04-21     # 특정 날짜
logs\fetch_archive.bat --last 7       # 최근 7일
```

운영 서버 cronjob `daily_archive.sh` 가 매일 15:35 에 당일 로그 전체 압축 (`archive/YYYY-MM-DD.tar.gz`).

## 대시보드

- **운영**: https://app.hwrimlab.trade (Cloudflare Named Tunnel, port 8503)
- **로컬**: http://localhost:8503

주요 기능:
- 실시간 watcher 상태 (WATCHING / TRIGGERED / READY / ENTERED / EXITED 등)
- 수동 종목 입력 (09:30~09:50)
- 거래 내역 (trades.db 기반)
- 실시간 로그 스트림

## 협업 규칙 (§5.6, 8 원칙)

신규 Claude 세션은 `CLAUDE.md` 먼저 필독. 핵심:

1. 사실 base 우선 (추측 금지, grep/cat 으로 실제 확인)
2. 진단 후 작업 (검증 → 연관성 → 확정 → 작업 → 검증 → 보고)
3. 멈춤 조건 명시 (예상 외 결과 즉시 멈춤)
4. 화이트리스트 원칙 (명령서에 명시된 파일만 수정)
5. 운영 서버 직접 접근 0건 (로컬 작업 후 수동 배포)
6. git commit 자동 실행 금지

## 주의사항

- 매매 명세는 `strategy_params.yaml` 에 **동결**. 임의 변경 금지 — 변경 시 새 R-N redesign 필요.
- 장중 배포 금지 (09:00~15:30 사이 코드 변경 X).
- 토큰 캐시 (`token_live.json`) 권한 600 유지.

---

**상세 문서**: `CLAUDE.md` (프로젝트 명세 self-contained)
**Obsidian vault**: `C:\Users\terryn\Documents\Obsidian\AUTOTRADE` (R-N 작업 이력, 결정 근거)
