# CLAUDE.md — AUTOTRADE 설계 + 코딩 규칙

KOSPI/KOSDAQ 장중 단타 자동매매. 한국투자증권(KIS) Open API 기반.
프로그램이 직접 KIS 서버에 REST/WebSocket으로 주문을 전송하는 자동매매 시스템.

---

## 1. 전략 로직

> **모든 수치는 `config/strategy_params.yaml`에서 로드.** 코드에 매직넘버 직접 사용 금지.

### 핵심 가설

장 초반 프로그램 순매수 비중이 높은 상승 종목이 9:55 이후 당일 신고가를 달성한 뒤,
눌림(2.5~4.25%)이 발생하면 고가-저가 50%까지 반등할 확률이 높다.

### 1단계: 종목 입력 + 자동 검증 (09:30~09:50)

> **KIS API에 프로그램 순매수 순위 엔드포인트가 없음** (종목별 조회만 가능).
> 따라서 1단계는 **수동 입력 + 자동 검증** 방식.

#### 입력 채널 (택 1)
- **대시보드 UI**: 텍스트 입력 → "종목 설정" → "수동 스크리닝" (관리자 토큰 필요)
- **텔레그램 봇**: `/target 006400,247540,020150` 또는 `/target 삼성SDI,에코프로비엠` (인가된 chat_id만)

#### 자동 검증 필터 (순서대로)

| 순서 | 조건 | yaml 키 |
|------|------|---------|
| 1 | 상승률 > 0% (하락/보합 제외) | — |
| 2 | 거래대금 ≥ 500억 원 | `screening.volume_min` |
| 3 | 상한가 미도달 (등락률 < 20%) | `infra.upper_limit_check_pct` |
| 4 | 프로그램순매수비중 ≥ 5% | `screening.program_net_buy_ratio_min` |
| 5 | 상승률 최고 1종목 | `screening.top_n_gainers` |

```
프로그램순매수비중(%) = (프로그램 순매수금액 / 누적 거래대금) × 100
```

#### 자동 스크리닝 (폴백)
수동 종목 미설정 시 기존 자동 스크리닝(거래량순위 API 기반) 실행.

### 2단계: 신고가 감시 (09:55~)

- 스크리닝 통과 종목의 실시간 체결가를 WebSocket으로 구독
- **9:55 이후** 당일 신고가 달성 여부 감시
- 신고가 달성 → 고가(intraday high) 실시간 추적 시작

### 3단계: 매수 — 고가 확정 트리거 + 지정가 2건

**고가 확정 트리거**: 고가에서 **1% 하락** 시 고가가 확정된 것으로 간주하고 매수 주문 진입.

| 시장 | 1차 매수 (예수금 50%) | 2차 매수 (예수금 50%) | yaml 키 |
|------|----------------------|----------------------|---------|
| KOSPI | 고가 대비 -2.5% | 고가 대비 -3.5% | `entry.kospi_buy1_pct`, `entry.kospi_buy2_pct` |
| KOSDAQ | 고가 대비 -3.75% | 고가 대비 -5.25% | `entry.kosdaq_buy1_pct`, `entry.kosdaq_buy2_pct` |

**고가 갱신 시 주문 재조정:**
1. 현재가가 기존 고가를 갱신하면 → 미체결 매수 주문 전량 취소
2. 새 고가에서 1% 하락할 때까지 대기
3. 1% 하락 확인 → 새 고가 기준으로 매수 지정가 2건 재주문

### 4단계: 청산 — 먼저 도달하는 조건이 실행

```
① 하드 손절   : 고가 대비 KOSPI -4.1% / KOSDAQ -6.2% → 즉시 시장가
② 타임아웃    : 눌림 최저가 시점부터 20분, 최저가 갱신 시 리셋
③ 목표가      : (고가 + 눌림 최저가) / 2 → 전량 매도
④ 선물 급락   : 종목 고점 시각의 선물가 대비 1% 하락 → 전량 청산
⑤ 강제 청산   : 15:20 KST
```

### 눌림 미발생 시

눌림이 오지 않으면 매매 안 함. 15:20까지 미매수 시 당일 종료.

### 포지션 관리

- 최대 동시 포지션: 1종목 (안정화 후 확대 가능)
- 투자금: 예수금 기준 (`config/strategy_params.yaml`)

---

## 2. 기술 스택 & 아키텍처

### KIS Open API

| 용도 | 엔드포인트 | TR ID |
|------|-----------|-------|
| 주식현재가 | /uapi/domestic-stock/v1/quotations/inquire-price | FHKST01010100 |
| 거래량순위 | /uapi/domestic-stock/v1/quotations/volume-rank | FHPST01710000 |
| 프로그램매매(당일) | /uapi/domestic-stock/v1/quotations/comp-program-trade-today | FHPPG04600101 |
| 분봉차트 | /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice | FHKST03010200 |
| 현금매수 | /uapi/domestic-stock/v1/trading/order-cash | TTTC0012U (모의: VTTC0012U) |
| 현금매도 | /uapi/domestic-stock/v1/trading/order-cash | TTTC0011U (모의: VTTC0011U) |
| 잔고조회 | /uapi/domestic-stock/v1/trading/inquire-balance | TTTC8434R (모의: VTTC8434R) |
| 실시간체결 | WebSocket H0STCNT0 | — |

- **Rate Limit**: 조회 초당 20건 (모의투자 2건) — asyncio.Semaphore로 제한
- **OAuth2**: app_key + app_secret → 24시간 토큰, 만료 1시간 전 자동 갱신
- **모의투자 URL**: `https://openapivts.koreainvestment.com:29443`
- **실거래 URL**: `https://openapi.koreainvestment.com:9443`

### 기술 스택

| 항목 | 선택 |
|------|------|
| 언어 | Python 3.11+, 64bit |
| 이벤트 루프 | asyncio (`asyncio.run()`이 메인 루프) |
| HTTP | aiohttp |
| WebSocket | websockets |
| 보조 데이터 | pykrx (거래일 확인 + 백테스트 과거 데이터만. 장중 호출 금지) |
| 데이터 처리 | pandas |
| DB | SQLite |
| 로깅 | loguru |
| 설정 | pydantic-settings + .env |
| 알림 | python-telegram-bot |
| 테스트 | pytest + pytest-asyncio |

### asyncio 이벤트 루프 구조

```
asyncio.run() → AutoTrader.run()
  → asyncio.gather(스케줄 태스크들)
  → KIS REST API (시세/주문)
  → KIS WebSocket (실시간 체결가)
```

### 데이터 소스 분리

| 시점 | 소스 | 용도 |
|------|------|------|
| 09:00 시작 시 | pykrx (1회) | 거래일 확인 |
| 09:30 종목 입력 | 대시보드 UI / 텔레그램 | 사용자 수동 입력 (5~10종목) |
| 09:50 스크리닝 | KIS REST API | 현재가, 거래대금, 프로그램매매 검증 |
| 09:55~ 실시간 | KIS WebSocket | 신고가 감시, 체결가, 선물 |
| 주문 | KIS REST API | 매수/매도/취소 |

### 3단계 매매 모드

- `dry_run`: 주문 없이 시그널 로깅 + 가상 체결/P&L 추적 (완전한 페이퍼 트레이딩 엔진)
- `paper`: KIS 모의투자 계좌로 실제 주문
- `live`: 실매매 계좌 (충분한 검증 후)

---

## 3. 프로젝트 구조

```
C:\Users\terryn\AUTOTRADE\
├── .env                          # API 키, 계좌번호 (커밋 금지)
├── .env.example
├── requirements.txt
├── verify_phase1.py              # Phase 1 검증 스크립트
├── launcher.py                   # 09:00 KST 자동 시작 + 텔레그램 URL 발송
├── run_backtest.py               # KIS API 분봉 백테스트 실행
├── config/
│   ├── settings.py               # Pydantic Settings
│   ├── strategy_params.yaml      # 전략 파라미터 (튜닝용)
│   └── stock_master.json         # 전체 종목 마스터 (KOSPI+KOSDAQ ~2800종목)
├── src/
│   ├── main.py                   # 진입점 (asyncio + AutoTrader)
│   ├── kis_api/
│   │   ├── kis.py                # KIS REST + WebSocket 클라이언트
│   │   ├── api_handlers.py       # API 응답 파싱
│   │   └── constants.py          # 엔드포인트, TR ID
│   ├── core/
│   │   ├── screener.py           # 스크리닝 (수동입력 검증 + 자동 폴백)
│   │   ├── monitor.py            # 실시간 조정/반등 감시
│   │   ├── trader.py             # 주문 실행
│   │   └── risk_manager.py       # 손절, 포지션 관리, 강제 청산
│   ├── models/
│   │   ├── stock.py              # StockCandidate, TradeTarget
│   │   ├── order.py              # Order, Position
│   │   └── trade.py              # TradeRecord, DailySummary
│   ├── storage/
│   │   └── database.py           # SQLite CRUD
│   ├── backtest/
│   │   ├── data_collector.py     # pykrx 과거 데이터 수집
│   │   ├── simulator.py          # 전략 시뮬레이션 엔진
│   │   └── report.py             # 백테스트 결과 리포트
│   └── utils/
│       ├── notifier.py           # 텔레그램 알림 + 명령 수신 (/target, /clear)
│       ├── market_calendar.py    # 거래일 확인
│       └── logger.py             # loguru 설정
└── tests/
```

---

## 4. 핵심 데이터 흐름

```
09:00  장 시작 → KIS 토큰 발급, 계좌 확인
09:30  사용자가 후보 종목 5~10개 입력 (대시보드 UI 또는 텔레그램 /target)
09:50  수동 스크리닝 실행 → 자동 검증(상승률/거래대금/상한가/프로그램순매수비중) → 타겟 선정
       (수동 종목 미설정 시 자동 스크리닝 폴백)
09:55~ 타겟 종목 WebSocket 구독 + KOSPI200 선물 구독
       → 당일 신고가 달성 감시
       → 신고가 달성 → 고가 실시간 추적
       → 고가에서 1% 하락 → 매수 지정가 2건 진입
       → 고가 갱신 → 기존 주문 취소, 새 고가에서 1% 하락 대기 후 재주문
매수 후 → 5개 청산 조건 동시 모니터링 (하드손절/20분타임아웃/목표가/선물급락/강제)
       → 눌림 미발생 시: 매매 안 함
15:20  미체결 포지션 강제 청산
15:30  장 마감 → 일일 리포트, 텔레그램 발송
```

---

## 5. 구현 단계 & 현재 상태

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 0 | 백테스트 — 전략 승률 정량 검증 (≥80% 목표) | 코드 완료, 미실행 |
| **Phase 1** | KIS API 기반 구축 | ✅ 완료 (verify_phase1.py 전체 통과) |
| Phase 2 | 스크리닝 로직 — 수동입력+자동검증, HTS 대조 | ✅ 코드 완료, API 테스트 통과 |
| Phase 3 | 실시간 감시 + 분할매수 — DRY_RUN 풀가동 | 코드 완료, 미검증 |
| Phase 4 | 주문 실행 — 모의투자 체결 확인 | 코드 완료, 미검증 |
| Phase 5 | 운영 안정화 — 1~2주 모의투자 후 실매매 전환 | 코드 완료, 미검증 |

**다음 단계**: Phase 3 DRY_RUN 장중 풀가동 검증 (수동 종목 입력 → 자동 매매 흐름)

---

## 5.5 텔레그램 명령어

| 명령 | 기능 | 예시 |
|------|------|------|
| `/target 코드,종목명` | 타겟 종목 설정 (코드+종목명 혼합 가능) | `/target 006400,에코프로비엠` |
| `/target` | 현재 설정된 종목 조회 | |
| `/clear` | 종목 설정 초기화 | |
| `/screen` | 설정된 종목으로 스크리닝 실행 | |
| `/status` | 현재 매매 상태 조회 (모드/예수금/P&L/감시종목) | |
| `/stop` | 당일 매매 중단 + 미체결 취소 | |
| `/help` | 명령어 도움말 | |

### 런처 (launcher.py)
- `python launcher.py` — 즉시 대시보드 시작 + Tunnel URL 텔레그램 발송
- `python launcher.py --schedule` — 매일 09:00 KST 자동 시작 (데몬)
- 기존 포트 점유 프로세스 자동 정리 후 시작 (포트 충돌 방지)

---

## 6. 절대 금지 규칙

### pykrx 장중 실시간 호출 금지
- 장중 호출 시 느리고(2~5초), 타임아웃/IP차단 위험
- **허용**: 09:00 거래일 확인 1회, 시총 1회 캐시, 백테스트 과거 데이터
- **금지**: 11시 스크리닝, 장중 모니터링에서 pykrx 호출
- 장중 실시간 데이터는 전부 KIS REST API 또는 WebSocket

### KIS API Rate Limit 준수
- 조회 API: 초당 20건 (모의투자 2건)
- asyncio.Semaphore로 동시 요청 수 제한

### 전략 파라미터 하드코딩 금지
- 모든 전략 수치는 `config/strategy_params.yaml`에서 로드
- `2.0`, `3.0`, `4.1`, `5.1`, `25`, `50` 같은 매직넘버 코드에 직접 사용 금지
- `self.params.entry.kospi_drop_pct` 형태로 참조

---

## 7. KIS API 코딩 패턴

### REST 조회
```python
info = await self.api.get_current_price("005930")
rank = await self.api.get_volume_rank(market="J")
```

### 실시간 (WebSocket)
```python
await self.api.subscribe_realtime(codes=["005930"])
self.api.add_realtime_callback("H0STCNT0", self._on_price)
```

### 주문
```python
order = await self.api.buy_order(
    code="005930", qty=100, price=70000,
    price_type="00",  # 지정가
)
```

### 인증
```python
await self.api.connect()  # 토큰 자동 발급/갱신 (24시간 유효)
```

---

## 8. 코딩 스타일

- 한국어 주석 OK, 변수/함수명은 영어
- 로깅: `loguru`의 `logger` 사용. 매수/매도/손절은 INFO 이상
- KIS API 호출은 반드시 try-except로 감싸고 에러 로깅
- `from __future__ import annotations` + 타입힌트 사용
- 모든 API 호출은 `async/await`
- 코드는 설명 없이 전체를 작성 (Terry 선호)
- 환경: `C:\Users\terryn\AUTOTRADE`, Git Bash에서는 `export` 사용 (`set` 아님)

---

## 9. 주요 유의사항

- **프로그램매매 순위 API 없음**: KIS API에 프로그램 순매수 *순위* 조회 엔드포인트 없음. 종목별 개별 조회만 가능 → 1단계 수동 입력 방식 채택 배경
- **프로그램매매 데이터 지연**: KIS API 지연 5분 이상이면 전략 전제 재검토
- **프로그램매매 ≠ 기관 순매수**: 차익/비차익 프로그램 주문만 포함, 기관 수동 주문 미포함
- **슬리피지**: 손절은 시장가, 그 외 지정가
- **거래비용**: 총 추정 ~0.4~0.7% (수수료 + 거래세 + 슬리피지). 백테스트 시 반드시 반영
- **DRY_RUN**: 단순 주문 미전송이 아니라 완전한 가상 체결/P&L 추적 엔진
- **선물 데이터**: KOSPI200 선물(101S) WebSocket 구독 필요 (청산 조건 ④)
