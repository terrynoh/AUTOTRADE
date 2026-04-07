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
| Phase 1 | KIS API 기반 구축 | ✅ 완료 |
| Phase 2 | 스크리닝 로직 — 수동입력+자동검증, HTS 대조 | ✅ 코드 완료, API 테스트 통과 |
| Phase 3 | 실시간 감시 + 분할매수 — DRY_RUN 풀가동 | 코드 완료, 미검증 |
| Phase 4 | 주문 실행 — 모의투자 체결 확인 | 코드 완료, 미검증 |
| Phase 5 | 운영 안정화 — 1~2주 모의투자 후 실매매 전환 | 코드 완료, 미검증 |
| **Phase α-0** | **매매 로직 누락 부분 보강 (10시 가드 + 선물 급락)** | **🟡 진행 예정 (다음 작업)** |
| Phase α-1 | 클라우드 lift and shift + 다중 사용자 UI | 📅 α-0 후 |
| Phase α-2 | Strategy plug-in 구조 + Account Executor 구조 | 📅 α-1 안정 후 |
| Phase β | KIS 계좌 분리 + 매매 로직 B/C/D 추가 | 📅 α-2 후 |

### 카테고리 A/B/E 코드 정리 작업 (2026-04-07 완료)

Phase α 진입 전 코드 base 정리. feature/dashboard-fix-v1 브랜치.

| Commit | 내용 |
|---|---|
| 0a40261 | Cat A: 그림자 상태 해소 — DashboardState가 자체 KIS API 보유하던 구조 제거, AutoTrader를 단일 owner로 |
| 9501a69 | Cat A 후속: ISSUE-010 deprecated 경로 차단 (`_build_stock_name_cache`에서 get_volume_rank 호출 제거, `_on_screening` 자동 폴백 차단) |
| 44e6c55 | Cat B: 데드 코드/UI 제거 (-52 lines) — "자동 스크리닝" / "API 연결" 버튼 등 |
| be89ccc | Cat E: 코드 위생 (add_log deque hot path 최적화) |

**브랜치 상태**: feature/dashboard-fix-v1 — main 머지 안 됨 (Phase α-1 작업 끝난 후 머지 결정)

**카테고리 C(보안), D(UX, 모니터 폐기 등)**: 폐기. Phase α/β에서 다중 사용자 관점으로 재설계.

---

## 5.5 Phase α 온라인화 — 작업 합의 (2026-04-07)

### 미션
**현재 PC에서 돌아가는 AUTOTRADE를 클라우드로 옮기되, 동시에 다중 사용자 UI를 도입한다. 단, 매매 의사결정은 운영자 1명(A)이 단독으로 수행하고, 다른 사용자들은 capital provider 역할이다.**

### 핵심 모델 — capital provider 모델

```
                  ┌──────────────────────────┐
                  │  AutoTrader (Singleton)   │
                  │  - 종목 선정: A 운영자만   │
                  │  - 매매 의사결정: 글로벌    │
                  │  - 시그널 발행: 1번        │
                  └────────────┬─────────────┘
                               │
                               ▼ (단순 broadcast)
                  ┌──────────────────────────┐
                  │  Signal Hub               │
                  │  같은 시그널을 모든          │
                  │  Account Executor에 전달  │
                  └────┬──────────────┬──────┘
                       │              │
                       ▼              ▼
                ┌─────────┐     ┌─────────┐
                │ Account │     │ Account │
                │ Executor│     │ Executor│
                │   A     │     │   B     │
                │         │     │         │
                │ KIS API │     │ KIS API │
                │ A       │     │ B       │
                │         │     │         │
                │ A 계좌  │     │ B 계좌  │
                │ A 예수금│     │ B 예수금│
                └─────────┘     └─────────┘
                       │              │
                       ▼              ▼
                  ┌──────────────────────────┐
                  │  SQLite (단일 파일)       │
                  │  - 매매 결정: 1행          │
                  │  - 체결 결과: 계좌별 2행   │
                  │  - P&L: 계좌별            │
                  └──────────────────────────┘
```

### 모델의 핵심 원칙 (8가지)

1. **A는 단독 의사결정자**: 종목 선정, 매매 트리거, 매매 운영 모두 A.
2. **B는 capital provider**: A의 매매를 자기 자본 규모로 그대로 복제. 의사결정 권한 없음.
3. **같은 시간, 같은 종목, 같은 조건으로 매매**: A와 B는 동시에 같은 종목을 같은 매수가/매도가 룰로 거래.
4. **다른 점은 자본 규모와 P&L뿐**: A의 예수금이 1000만원, B의 예수금이 5000만원이면 — 같은 매매여도 수량과 P&L이 다를 뿐.
5. **B는 A의 매매를 거부할 수 없다**: manual approval 없음, follower 거부 권한 없음.
6. **각자 본인 계좌의 EMERGENCY STOP만 가능**: 본인 자본 자율성. 다른 사람 매매 중단 불가.
7. **StrategyParams는 글로벌**: user별 다른 전략 X. A의 전략이 곧 모두의 전략.
8. **권한**: 운영자 admin 같은 추가 역할 없음. A는 운영자, 나머지는 capital provider. 그뿐.

### 시스템 확장 한계 (의식적 결정)

- **사용자 수**: A, B 2명 고정. 영원히 2명 이상으로 늘리지 않는다.
- **매매 로직**: 현재 매매 로직 A가 base. 향후 B, C, D 등 추가 가능. 단, 사용자 수와 시스템 구조는 변경 안 함.

이 두 가지 상한 때문에 다음 기술 결정이 정당화됨:

| 결정 | 이유 |
|---|---|
| **DB는 SQLite** | 사용자 2명 고정 → 동시 쓰기 부하 무시 가능 → PostgreSQL 도입 부담 불필요 |
| **매매 로직은 plug-in 구조** | B, C, D 추가 가능성 → Strategy 추상 클래스 도입 (Phase α-2) |
| **권한 모델은 단순 user_id** | 사용자 2명 고정 → tenant_id, role 매트릭스 등 multi-tenant 추상화 불필요 |

### Phase α의 세 단계

---

**Phase α-0 (다음 작업)** — 매매 로직 누락 부분 보강

목표: **클라우드 이전 전에 매매 로직 명세상 누락된 두 부분을 구현하고 회귀 검증한다.**

근거: 매매 시스템에서 청산 조건과 시간 가드는 critical이다. 깨진 로직을 24/7 클라우드 환경으로 옮기면 위험이 커진다. 운영 환경 이전 전에 시스템이 명세대로 작동하는지 먼저 확인.

작업 범위 (1~2일):

**1. 항목 1 — 10시 이후 가드 + 주석 정정**

- 명세: "10시 이후 최저가에서 20분 내 목표가 달성 못하면 매도"
- 현재 상태:
  - 코드 동작: 20분 (yaml 설정 그대로) ✅
  - 주석: "25분" — 잘못 ⚠
  - **10시 이전 최저가는 무시하는 가드 — 미구현 ❌**
- 작업:
  1. monitor.py 코드 검증: 현재 타임아웃 시작 시점에 10시 가드가 있는가
  2. 가드 없으면 추가: "타임아웃 시작 시점이 10시 이전이면 시작 안 함, 10시 이후 최저가만 카운트"
  3. 주석 "25분" → "20분" 정정
  4. yaml에 가드 임계값 변수화 검토 (`monitor.timeout_start_after_kst = "10:00"`)
  5. DRY_RUN 회귀 검증
- 예상 시간: 1~2시간

**2. 항목 2 — 선물 급락 데이터 수신 구현 (청산 조건 ④)**

- 명세: "종목의 최고점 시각에서 현재까지 선물이 1% 하락 시 전량 청산"
- 현재 상태:
  - 청산 조건 ④ 로직: 구현됨 ✅ (monitor.py:228-234, 248-253)
  - KOSPI200 선물 코드/TR ID: 정의됨 ✅ (constants.py:66, 69)
  - **WebSocket 구독: 미구현 ❌**
  - **선물 가격 업데이트 콜백: 미구현 (TODO 주석만 — main.py:754-755) ❌**
  - **monitor.on_futures_price() 호출: 함수만 존재, 호출 없음 ❌**
- 작업:
  1. KIS WebSocket 선물 구독 코드 추가 (kis_api/kis.py 또는 main.py)
  2. 선물 가격 수신 콜백 등록
  3. monitor.on_futures_price() 호출 연결 (main.py:754-755 TODO 부분)
  4. 종목 고점 시각의 선물 가격 저장 로직 검증 (monitor.py)
  5. DRY_RUN 회귀 검증
- 예상 시간: 3~5시간

**3. Phase α-0 완료 게이트**

- DRY_RUN 모드에서 시뮬레이션으로 두 로직 작동 확인
- 가능하면 모의투자 환경에서 실제 데이터 흐름 확인
- 백테스트 재실행 (회귀 없는지)
- 모두 통과 → Phase α-1 진입

---

**Phase α-1 (Phase α-0 후)** — 클라우드 lift and shift + 다중 사용자 UI

목표: **현재 코드 그대로 클라우드에 올리고, A/B 두 사용자가 같은 대시보드를 권한 분리해서 본다.**

작업 범위 (1.5~2일):

1. **클라우드 인프라**
   - 클라우드 호스트 결정 + 셋업 (Oracle Cloud Always Free 또는 K 수석님 결정)
   - Python 3.11 + 의존성 설치
   - SQLite 데이터 디렉토리 셋업
   - Cloudflare Named Tunnel (Quick Tunnel 아님 — 고정 도메인)
   - systemd unit (launcher.py를 service로)
   - 시크릿 관리: Master Key + Fernet (KIS 키 .env 평문 → 암호화)
   - 백업: sqlite3 dump cron (일 1회)
   - SSH 보안 (key-based auth, fail2ban)

2. **다중 사용자 UI**
   - DB에 user 테이블 신규 (id, name, role: 'operator' | 'capital_provider', password_hash)
   - 대시보드 인증: HTTP-only Cookie + Session
   - 로그인 페이지 신규
   - 사용자 2명 (A=operator, B=capital_provider) 등록
   - 대시보드 view 권한 분리:
     - operator: 종목 입력 영역 활성, 매매 트리거 활성
     - capital_provider: 종목 입력 영역 비활성, 조회만
   - audit_log 테이블 신규 (누가 언제 무엇을 변경했는지)

3. **코드 변경 최소화**
   - AutoTrader, Trader, Monitor 등 매매 로직 코드는 **건드리지 않음** (Phase α-0에서 확정된 로직 그대로)
   - SQLite 그대로
   - 텔레그램 봇 그대로 (옵션 Y 같은 변경 없음)
   - 변경되는 파일: dashboard/app.py (인증 + 권한), config/settings.py (시크릿 복호화), launcher.py (systemd 호환)

**Phase α-1에 포함되지 않는 것 (의식적 제외)**:
- ❌ Strategy plug-in 구조 (Phase α-2)
- ❌ Account Executor 구조 (Phase α-2)
- ❌ KIS 계좌 분리 (Phase β)
- ❌ Signal Hub 활성화 (Phase β)
- ❌ PostgreSQL 마이그레이션 (사용자 2명 고정 → 영영 X)
- ❌ 텔레그램 옵션 Y (별도 결정)
- ❌ 동적 파라미터 변경 UI (별도 결정)

---

**Phase α-2 (α-1 안정 후)** — Strategy plug-in 구조 + Account Executor 구조

목표: **매매 로직을 hardcoded class에서 plug-in 구조로 분리, KIS 계좌 분리를 위한 Account Executor 구조 도입. 단, 인스턴스는 1개로 시작.**

작업 범위 (1~2일):

1. **Strategy plug-in 구조**
   - `src/strategies/base.py` 신규: `Strategy` 추상 클래스
     - `on_screening(codes)`, `on_price_update(code, price)`, `evaluate_entry(monitor)`, `evaluate_exit(position)` 등
   - `src/strategies/strategy_a.py` 신규: 현재 매매 로직 A를 `StrategyA` 클래스로 분리
     - 9:55 신고가 → 1% 하락 트리거 → 분할매수 → 5조건 청산 (Phase α-0에서 확정된 그대로)
   - `AutoTrader.__init__(strategy: Strategy)` 변경: strategy를 inject 받음
   - 회귀 검증 필수: 변경 전후 매매 결과 동일해야 함
   - 향후 `StrategyB`, `StrategyC` 추가는 단순 파일 추가만

2. **Account Executor 구조 (Phase β 준비)**
   - KIS API factory 패턴: user_id별 KIS API 인스턴스 생성
   - 단, Phase α-2에서는 인스턴스 1개만 (DRY_RUN 단계)
   - DB에 user별 KIS 키 저장 가능한 구조 (지금은 빈 컬럼)
   - Trader/Monitor가 user 컨텍스트로 KIS API 받게

**Phase α-2가 끝나면**:
- 매매 로직 추가가 단순 파일 추가 작업
- KIS 계좌 추가가 단순 설정 + 키 입력 작업

---

**Phase β (α-2 후, K 수석님 결정 시)** — 두 번째 KIS 계좌 활성화

작업 범위:
- B의 KIS 키 입력
- B의 Account Executor 활성화
- Signal Hub broadcast 활성화 (현재 1개 → 2개 fan-out)
- DRY_RUN 검증 → 모의투자 검증 → 실매매 전환

매매 로직 B, C, D 추가도 이 시점에 같이 결정.

---

## 5.6 Claude ↔ K 수석님 협업 규칙

오늘(2026-04-07) 12시간 협업에서 학습된 규칙. Claude(여기 채팅의 어시스턴트, 그리고 Claude Code)는 다음을 따른다.

### 미션 관리

1. **미션 외 작업 만들지 않기**. K 수석님이 명시 안 한 작업은 안 함. 가이드 문서, 카테고리 작업, 부가 결정 사항 등을 임의로 만들지 않는다.
2. **K 수석님이 정정하시면 그 자리에서 멈추기**. "그게 아니라 X야"라고 하시면 새 결정 사항 만들지 말고, 미션을 다시 본다. 정정은 새 작업 추가가 아니라 기존 작업 폐기 신호.

### 추측 금지

3. **추측으로 가이드/명세 작성 안 하기**. 코드/명세 base 없으면 안 작성. 불확실하면 K 수석님께 물어보거나 Claude Code에 분석 시킨다.
4. **결정 항목을 임의로 늘리지 않기**. K 수석님이 답해주시는 만큼만 결정. "이것도 결정 필요" 라고 작업을 늘리지 않는다.

### 도구 활용

5. **Claude Code가 할 일을 K 수석님께 떠넘기지 않기**. grep, 명령 실행, 파일 분석은 Claude Code 영역. 어시스턴트(Claude)는 K 수석님과의 의사결정 + 명세 작성 + 게이트 검토만.
6. **분석은 코드/파일 base로 하기**. 어시스턴트가 K 수석님 PC 파일에 직접 접근 못 하므로, 진단/grep/분석이 필요하면 Claude Code에 분석 메시지를 만들어서 K 수석님이 그것만 던지면 되도록 한다.

### 페르소나

7. **펀드매니저/시스템 엔지니어 모자는 K 수석님이 부르실 때만**. 자기 의견을 자주 끼워 넣지 않는다.

### 게이트

8. **Claude Code의 멈춤 종류를 명시 요구**: (a) 명세 외 변경 발견 (b) 명세 모호 결정 요청 (c) 카테고리 단위 작업 끝 확인 (d) 에러 디버깅. 어떤 종류인지 명시 없이는 K 수석님이 추측해야 함.

---

## 5.7 텔레그램 명령어

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

### 운영 사고 학습 (2026-04-07)

다음 패턴은 클라우드 운영에서 반복되면 안 된다:

1. **백그라운드 실행 좀비 누적**: `python -m src.main &` 패턴이 좀비 5개 누적시킴 (152MB×2). 검증 단계에서는 포그라운드 실행 + 새 터미널에서 명령. 본격 운영은 systemd가 표준화. 백그라운드 `&` 금지.

2. **장중 코드 작업 위험**: 매매 시간 중 코드 변경이 사고 야기. 운영자가 데드 UI 버튼 클릭 → INVALID FID_COND_MRKT_DIV_CODE 에러. 운영용/개발용 환경 분리 필요. 본격 운영은 클라우드 상시.

3. **포트 점유 좀비**: launcher.py가 포트 정리 코드 가지고 있지만, 백그라운드 실행 시 우회됨. systemd가 표준화하면 해결.

4. **K 수석님 PC가 24/7 안 켜져있음**: 클라우드 이전의 본질적 가치. 매매 시간(09:00~15:30 KST)에 PC가 켜져있어야 한다는 제약 자체가 v.1의 존재 이유.

5. **텔레그램 polling Conflict**: 좀비 main.py 두 인스턴스가 같은 봇 토큰으로 polling 시도 → telegram.error.Conflict 무한 반복. 단일 인스턴스 보장 필수.

---

## 11. 미해결 이슈 (Phase α 작업 외)

다음은 Phase α 작업과 무관하게 별도 추적되는 이슈들. Obsidian 이슈 트래커와 동기화 필요.

### ISSUE-020 — screener.run() 자동 스크리닝 메서드 정리 (다음 sprint)
- Cat A 후속 보정에서 임시 차단됨 (deprecated 마킹 + warning)
- 본격 정리는 별도 sprint
- 백테스트 코드 의존성 분리 필요

### ISSUE-024 — 모니터에 미등록 종목 잔존 (삼천당제약 사고)
- 2026-04-07 오전 발견
- 5원칙 3번 위반 (시스템이 종목을 자동 추가)
- 가능 원인: (a) 카테고리 A 후속 보정 전의 자동 폴백, (b) 세션 간 상태 잔존
- 조사 + 해결 필요

**Phase α-0으로 흡수된 이슈** (별도 추적 종료):
- ~~ISSUE-025 청산 조건 ④ 선물 급락 미구현~~ → Phase α-0 항목 2
- ~~monitor.py "25분 타임아웃" 주석 오류~~ → Phase α-0 항목 1
