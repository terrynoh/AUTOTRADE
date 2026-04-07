"""
AUTOTRADE Obsidian Vault 초기 설정 스크립트
실행: python setup_obsidian_autotrade.py

기본 경로: C:/Users/terryn/Documents/Obsidian/AUTOTRADE/
변경하려면 VAULT_ROOT 수정

KIS(한국투자증권) Open API 기반 프로젝트 기준으로 작성
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from pathlib import Path
from datetime import date

# ── 설정 ──────────────────────────────────────────────
VAULT_ROOT = Path(r"C:\Users\terryn\Documents\Obsidian\AUTOTRADE")
TODAY = date.today().isoformat()
# ──────────────────────────────────────────────────────


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"  OK {path.relative_to(VAULT_ROOT)}")


def main():
    print(f"\nObsidian Vault 생성 시작: {VAULT_ROOT}\n")

    # ── 00 대시보드 ──────────────────────────────────────
    write(VAULT_ROOT / "00_대시보드.md", f"""
# AUTOTRADE 대시보드
> KIS(한국투자증권) Open API 기반 KOSPI/KOSDAQ 장중 단타 자동매매 시스템

**프로젝트 경로:** `C:\\Users\\terryn\\AUTOTRADE`
**언어/환경:** Python 3.11+ 64bit, asyncio
**최종 업데이트:** {TODAY}

---

## 📍 현재 진행 위치

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 0 | 백테스트 — 전략 승률 정량 검증 (≥80% 목표) | ⬜ 코드 완료, 미실행 |
| Phase 1 | KIS API 기반 구축 | ✅ 완료 (verify_phase1.py 전체 통과) |
| Phase 2 | 스크리닝 로직 — 장중 HTS 대조 | ⬜ 코드 완료, 미검증 |
| Phase 3 | 실시간 감시 + 분할매수 — DRY_RUN 풀가동 | ⬜ 코드 완료, 미검증 |
| Phase 4 | 주문 실행 — 모의투자 체결 확인 | ⬜ 코드 완료, 미검증 |
| Phase 5 | 1~2주 모의투자 → 실매매 전환 | ⬜ 코드 완료, 미검증 |

**다음 단계:** Phase 0 백테스트 실행 or Phase 2 스크리닝 장중 HTS 대조 검증

---

## 🔗 빠른 링크

- [[01_전략_마스터스펙]] — 전략 전체 설계 (스크리닝/매수/청산 조건)
- [[02_Phase_진행상황]] — Phase별 체크리스트
- [[03_KIS_API_레퍼런스]] — 엔드포인트, TR ID, Rate Limit
- [[04_환경설정]] — .env 구성, 실행 방법
- [[05_이슈_트래커]] — 발생 이슈 기록
- [[06_백테스트_결과]] — 백테스트 데이터
- [[개발일지/{TODAY}]] — 오늘 개발일지

---

## ⚡ 핵심 전략 파라미터 (strategy_params.yaml 기준)

### 스크리닝 (09:50)
| 조건 | 값 |
|------|-----|
| 거래대금 최소 | 500억 원 |
| 프로그램순매수비중 최소 | 5% |
| 상승률 상위 | 1종목 |
| 후보 풀 (멀티 트레이드) | 최대 5종목 |

### 매수 진입 (고가에서 1% 하락 = 고가 확정)
| 시장 | 1차 매수 | 2차 매수 |
|------|---------|---------|
| KOSPI | 고가 대비 -2.5% | 고가 대비 -3.5% |
| KOSDAQ | 고가 대비 -3.75% | 고가 대비 -4.25% |

각 50% / 50% 분할 (예수금 기준)

### 청산 조건 (우선순위 순)
| 순위 | 조건 | 기준 |
|------|------|------|
| ① | 하드 손절 | KOSPI -4.5% / KOSDAQ -6.5% (고가 대비) |
| ② | 추세 이탈 | 1분봉 higher lows 깨짐 |
| ③ | 타임아웃 | 눌림 최저가 후 20분 |
| ④ | 목표가 | (고가+저가)/2 = 고저폭 50% 회복 |
| ⑤ | 선물 급락 | 고점 시각 선물가 대비 -1% |
| ⑥ | 강제 청산 | 15:20 KST |

### 멀티 트레이드
- 수익 청산 후 다음 후보 진입 (10:00~11:00)
- 일일 최대 3회, 손절 시 당일 중단
""")

    # ── 01 전략 마스터스펙 ────────────────────────────────
    write(VAULT_ROOT / "01_전략_마스터스펙.md", f"""
# 전략 마스터 스펙
> 원본: `C:\\Users\\terryn\\AUTOTRADE\\CLAUDE.md`
> 최종 업데이트: {TODAY}

---

## 1. 핵심 가설

장 초반 프로그램 순매수 비중이 높은 상승 종목이 **9:55 이후 당일 신고가**를 달성한 뒤,
눌림(2.5~4.25%)이 발생하면 **고가-저가 50%까지 반등**할 확률이 높다.

---

## 2. 전체 데이터 흐름

```
09:00  KIS 토큰 발급, 계좌 잔고 확인
09:45  스크리닝 데이터 사전 수집 시작 (API 사전 캐시)
09:50  스크리닝 실행 → 후보 풀 최대 5종목 선정
09:55~ 1번 종목 WebSocket 구독 + KOSPI200 선물 구독
       → 당일 신고가 달성 감시
       → 신고가에서 1% 하락 → 매수 지정가 2건 진입
       → 고가 갱신 → 기존 주문 취소, 새 고가에서 1% 하락 대기 후 재주문
매수 후 → 6개 청산 조건 동시 모니터링
수익 청산 → 다음 후보 (10:00~11:00, 최대 3회)
       → 눌림 미발생 시: 매매 안 함
15:20  미체결/보유 포지션 강제 청산
15:30  장 마감 리포트
```

---

## 3. 스크리닝 (09:50 KST)

```
프로그램순매수비중(%) = (프로그램 순매수금액 / 9:50 누적 거래대금) × 100
```

| 조건 | 기준값 | yaml 키 |
|------|--------|---------|
| 거래대금 | ≥ 500억 원 | `screening.volume_min` |
| 프로그램순매수비중 | ≥ 5% | `screening.program_net_buy_ratio_min` |
| 상승률 | 최고 1종목 | `screening.top_n_gainers` |
| 상한가 제외 | 등락률 < 29.5% | `screening.max_change_pct` |
| 후보 풀 크기 | 5종목 | `screening.top_n_candidates` |

> ⚠️ 조건 미충족 = 당일 매매 안 함

---

## 4. 매수 진입 (09:55~ / 진입 마감 11:00)

### 고가 확정 트리거
신고가 달성 후 **고가에서 1% 하락** 시 → 고가 확정, 매수 주문 2건 진입

### 분할매수 (지정가)

| 시장 | 1차 (예수금 50%) | 2차 (예수금 50%) |
|------|----------------|----------------|
| KOSPI | 고가 대비 -2.5% | 고가 대비 -3.5% |
| KOSDAQ | 고가 대비 -3.75% | 고가 대비 -4.25% |

### 고가 갱신 시 재조정
1. 현재가 > 기존 고가 → 미체결 매수 전량 취소
2. 새 고가에서 1% 하락까지 대기
3. 새 고가 기준으로 2건 재주문

---

## 5. 청산 조건 (먼저 도달하는 조건 실행)

| 조건 | 트리거 | 주문유형 |
|------|--------|---------|
| ① 하드 손절 | KOSPI: 고가 대비 -4.5% / KOSDAQ: -6.5% | 시장가 |
| ② 추세 이탈 | 눌림 최저가 이후 1분봉 higher lows 깨짐 | 전량 청산 |
| ③ 타임아웃 | 눌림 최저가 후 20분 (최저가 갱신 시 리셋) | 전량 청산 |
| ④ 목표가 | (고가 + 눌림 최저가) / 2 = 50% 회복 | 전량 매도 |
| ⑤ 선물 급락 | 종목 고점 시각의 선물가 대비 -1% | 전량 청산 |
| ⑥ 강제 청산 | 15:20 KST | 시장가 |

---

## 6. 멀티 트레이드

- 수익 청산 후 → 다음 후보 종목으로 자동 전환
- 진입 가능 시간: 10:00~11:00
- 일일 최대: 3회
- 손절 청산 시 → 당일 매매 중단 (`profit_only: true`)

---

## 7. 리스크 관리

| 항목 | 값 |
|------|-----|
| 일일 손실 한도 | 예수금 대비 3% |
| 최대 포지션 크기 | 예수금 100% |
| 동시 포지션 | 1종목 |

---

## 8. 매매 모드

| 모드 | 설명 |
|------|------|
| `dry_run` | 주문 없이 시그널 로깅 + 완전한 가상 체결/P&L 추적 |
| `paper` | KIS 모의투자 계좌 실제 주문 |
| `live` | 실매매 (충분한 검증 후) |

---

## 9. 기술 스택

| 항목 | 선택 |
|------|------|
| 언어 | Python 3.11+ **64bit** |
| 이벤트 루프 | asyncio |
| HTTP | aiohttp |
| WebSocket | websockets |
| 보조 데이터 | pykrx (백테스트/거래일 확인 전용, 장중 금지) |
| 데이터 처리 | pandas |
| DB | SQLite |
| 로깅 | loguru |
| 설정 | pydantic-settings + .env |
| 알림 | python-telegram-bot |
| 원격 모니터링 | Cloudflare Tunnel |

---

## 10. 주요 제약사항

- **pykrx 장중 호출 금지** — 느리고 타임아웃/IP차단 위험. 장중은 전부 KIS API
- **KIS Rate Limit** — 실거래 초당 20건 / 모의투자 초당 2건 (asyncio.Semaphore)
- **전략 수치 하드코딩 금지** — 전부 `config/strategy_params.yaml`에서 로드
- **프로그램매매 ≠ 기관 순매수** — 차익/비차익 프로그램 주문만 포함
""")

    # ── 02 Phase 진행상황 ─────────────────────────────────
    write(VAULT_ROOT / "02_Phase_진행상황.md", f"""
# Phase별 진행상황 체크리스트
> 최종 업데이트: {TODAY}

---

## Phase 0 — 백테스트 ⬜ (코드 완료, 미실행)

**목표:** pykrx 과거 데이터 → 전략 승률 정량 검증 (≥80%)

```bash
cd C:\\Users\\terryn\\AUTOTRADE
python -m src.backtest.data_collector   # 과거 데이터 수집
python -m src.backtest.simulator        # 전략 시뮬레이션
python -m src.backtest.report           # 결과 리포트
```

- [ ] KOSPI/KOSDAQ 6개월~1년 분봉 데이터 수집
- [ ] 스크리닝 조건 백테스트 적용
- [ ] 거래비용(0.4~0.7%) 반영 순수익률 양수 확인
- [ ] 승률 ≥ 80% 달성
- [ ] 최적 파라미터 도출

> ⚠️ pykrx 버그 우회 방법 검토 중 → [[05_이슈_트래커]] 참조

---

## Phase 1 — KIS API 기반 구축 ✅

**상태:** verify_phase1.py 전체 통과

- [x] KIS OAuth2 토큰 발급/갱신 (24시간, 만료 1시간 전 자동 갱신)
- [x] REST API 조회 (현재가, 거래량순위, 프로그램매매, 분봉차트)
- [x] WebSocket 실시간 체결가 구독 (H0STCNT0)
- [x] 모의투자/실거래 URL 전환 자동화
- [x] asyncio.Semaphore Rate Limit 적용
- [x] pydantic-settings .env 로드
- [x] SQLite 기반 거래 기록

---

## Phase 2 — 스크리닝 검증 ⬜

**목표:** 장중 스크리닝 결과를 HTS(MTS/네이버증권)와 수동 대조

- [ ] 장중 스크리너 실행 (09:50 타이밍 확인)
- [ ] 거래대금 500억 필터 — 네이버증권 대조
- [ ] 프로그램순매수비중 계산 — KIS API 지연 5분 이내 확인
- [ ] 상승률 1위 종목 일치 확인
- [ ] API 지연이 5분 초과 시 → 전략 전제 재검토 필요

---

## Phase 3 — DRY_RUN 풀가동 ⬜

**목표:** 완전한 가상 매매 엔진 1일 풀가동

```bash
# .env에서 TRADE_MODE=dry_run 확인 후 실행
python -m src.main
```

- [ ] 스크리닝 → 신고가 감시 → 매수 시그널 로깅 정상
- [ ] 가상 체결 시뮬레이션 (simulate_fills) 정상
- [ ] 6개 청산 조건 동시 모니터링 정상
- [ ] 멀티 트레이드 (최대 3회) 전환 정상
- [ ] 텔레그램 알림 수신 확인
- [ ] 대시보드 (port 8503) 상태 표시 정상
- [ ] Cloudflare Tunnel URL 텔레그램 발송 확인

---

## Phase 4 — 모의투자 주문 검증 ⬜

```env
TRADE_MODE=paper
KIS_ACCOUNT_NO_PAPER=모의투자계좌번호
```

- [ ] KIS 모의투자 계좌 매수 주문 전송 확인
- [ ] 체결 확인 (VTTC0012U → 체결)
- [ ] 지정가 주문 정상 (고가 대비 -2.5%, -3.5%)
- [ ] 매도/취소 주문 확인
- [ ] 슬리피지 측정 데이터 축적

---

## Phase 5 — 모의투자 → 실매매 전환 ⬜

- [ ] 1~2주 모의투자 실행
- [ ] 승률/수익률 통계 = 백테스트의 80% 이상
- [ ] 에러 복구 시나리오 테스트 (WebSocket 끊김, 긴급 취소 동작)
- [ ] 일일 손실 한도 정상 작동
- [ ] **`TRADE_MODE=live` 전환 후 소액 실매매 시작**
""")

    # ── 03 KIS API 레퍼런스 ───────────────────────────────
    write(VAULT_ROOT / "03_KIS_API_레퍼런스.md", f"""
# KIS Open API 레퍼런스
> 파일: `C:\\Users\\terryn\\AUTOTRADE\\src\\kis_api\\constants.py`
> 최종 업데이트: {TODAY}

---

## 베이스 URL

| 환경 | URL |
|------|-----|
| 실거래 | `https://openapi.koreainvestment.com:9443` |
| 모의투자 | `https://openapivts.koreainvestment.com:29443` |

## WebSocket URL

| 환경 | URL |
|------|-----|
| 실거래 | `ws://ops.koreainvestment.com:21000` |
| 모의투자 | `ws://ops.koreainvestment.com:31000` |

---

## 엔드포인트 & TR ID

### 시세 조회

| 용도 | 엔드포인트 | TR ID |
|------|-----------|-------|
| 주식현재가 | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` |
| 주식현재가 일자별 | `/uapi/domestic-stock/v1/quotations/inquire-daily-price` | `FHKST01010400` |
| 분봉차트 | `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` | `FHKST03010200` |
| 거래량순위 | `/uapi/domestic-stock/v1/quotations/volume-rank` | `FHPST01710000` |
| 프로그램매매 당일 | `/uapi/domestic-stock/v1/quotations/comp-program-trade-today` | `FHPPG04600101` |
| 프로그램매매 일별 | `/uapi/domestic-stock/v1/quotations/comp-program-trade-daily` | `FHPPG04600001` |

### 주문

| 용도 | 엔드포인트 | TR ID (실거래) | TR ID (모의) |
|------|-----------|-------------|------------|
| 현금매수 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0012U` | `VTTC0012U` |
| 현금매도 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0011U` | `VTTC0011U` |
| 주문취소 | `/uapi/domestic-stock/v1/trading/order-rvsecncl` | `TTTC0803U` | `VTTC0803U` |
| 잔고조회 | `/uapi/domestic-stock/v1/trading/inquire-balance` | `TTTC8434R` | `VTTC8434R` |
| 미체결조회 | `/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl` | — | — |

### 인증

| 용도 | 엔드포인트 |
|------|-----------|
| 토큰 발급 | `/oauth2/tokenP` |
| 토큰 취소 | `/oauth2/revokeP` |
| WebSocket Key | `/oauth2/Approval` |

---

## WebSocket TR ID

| TR ID | 용도 |
|-------|------|
| `H0STCNT0` | 실시간 체결가 (KRX) |
| `H0STASP0` | 실시간 호가 (KRX) |
| `H0STPGM0` | 실시간 프로그램매매 |
| `H0IFCNT0` | 실시간 선물 체결 (KOSPI200) |

---

## Rate Limit

| 환경 | 초당 최대 요청 | 간격 |
|------|-------------|------|
| 실거래 | 20건 | 0.05초 |
| 모의투자 | 2건 | 0.5초 |

→ `asyncio.Semaphore`로 제어 (`src/kis_api/kis.py`)

---

## 인증 패턴

```python
await self.api.connect()       # 토큰 자동 발급/갱신 (24시간 유효)
# 만료 1시간 전 자동 갱신
```

---

## 선물 종목코드

```
KOSPI200 선물 근월물: 101S3000
# 분기마다 코드 변경됨 → constants.py 업데이트 필요
```

---

## 주문유형 코드

| 코드 | 유형 |
|------|------|
| `00` | 지정가 |
| `01` | 시장가 |
| `02` | 조건부지정가 |
| `03` | 최유리지정가 |

---

## 에러코드

| 코드 | 설명 |
|------|------|
| `EGW00000` | 정상 |
| `EGW00121` | 장 운영시간 외 요청 |
| `EGW00123` | 호가 접수 불가 시간 |
| `IGW00006` | 토큰 만료 |
| `IGW00007` | 인가되지 않은 IP |

---

## 코딩 패턴

```python
# REST 조회
info = await self.api.get_current_price("005930")
rank = await self.api.get_volume_rank(market="J")

# WebSocket 실시간
await self.api.subscribe_realtime(codes=["005930"])
self.api.add_realtime_callback("H0STCNT0", self._on_price)

# 주문
order = await self.api.buy_order(
    code="005930", qty=100, price=70000,
    price_type="00",   # 지정가
)
```
""")

    # ── 04 환경설정 ──────────────────────────────────────
    write(VAULT_ROOT / "04_환경설정.md", f"""
# 환경설정 가이드
> 최종 업데이트: {TODAY}

---

## 1. Python 환경

```bash
# Python 3.11+ 64bit 필수 (KIS API는 64bit)
python --version        # 3.11.x
python -c "import struct; print(struct.calcsize('P')*8)"  # 64
```

---

## 2. 패키지 설치

```bash
cd C:\\Users\\terryn\\AUTOTRADE
pip install -r requirements.txt
```

주요 패키지:
- `aiohttp` — KIS REST API
- `websockets` — KIS WebSocket 실시간 체결
- `pydantic-settings` — .env 설정 로드
- `pandas` — 데이터 처리
- `pykrx` — 거래일 확인 / 백테스트 전용 (장중 호출 금지)
- `loguru` — 로깅
- `python-telegram-bot` — 알림
- `cloudflared` (별도 설치) — Cloudflare Tunnel

---

## 3. .env 설정

```bash
copy .env.example .env
# .env 파일 편집
```

```env
# 한국투자증권 KIS
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_시크릿
KIS_ACCOUNT_NO=실매매계좌번호
KIS_ACCOUNT_NO_PAPER=모의투자계좌번호

# 매매 모드 (dry_run → paper → live 순서로)
TRADE_MODE=dry_run

# 텔레그램 알림
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=채팅ID

# 대시보드
DASHBOARD_ADMIN_TOKEN=임의토큰
DASHBOARD_PORT=8503

# 로깅
LOG_LEVEL=DEBUG
```

---

## 4. KIS Open API 신청

1. `securities.koreainvestment.com` 로그인
2. 트레이딩 → KIS Open API 신청
3. 앱키/시크릿 발급 → `.env`에 입력
4. 모의투자 계좌 신청 → 계좌번호 `.env`에 입력

---

## 5. 실행

```bash
# 일반 실행
python -m src.main

# 대시보드 별도 실행 (port 8503)
python -m src.dashboard

# 백테스트
python -m src.backtest.data_collector
python -m src.backtest.simulator
```

---

## 6. 대시보드 & 원격 모니터링

- 로컬: `http://localhost:8503`
- 원격: 실행 시 Cloudflare Tunnel URL이 텔레그램으로 발송됨
- `admin_token` 쿼리파라미터로 인증

---

## 7. 매매 모드 전환 순서

```
dry_run → paper → live
```

각 단계 충분히 검증 후 다음 단계로 전환.
`live` 전환 전 반드시 [[02_Phase_진행상황]] Phase 5 체크리스트 완료.

---

## 8. 프로젝트 구조

```
C:\\Users\\terryn\\AUTOTRADE\\
├── .env                        # API 키, 계좌번호 (커밋 금지)
├── config/
│   ├── settings.py             # Pydantic Settings
│   └── strategy_params.yaml   # 전략 파라미터 (튜닝용)
├── src/
│   ├── main.py                 # 진입점 (asyncio + AutoTrader)
│   ├── kis_api/
│   │   ├── kis.py              # KIS REST + WebSocket 클라이언트
│   │   ├── api_handlers.py     # API 응답 파싱
│   │   └── constants.py        # 엔드포인트, TR ID
│   ├── core/
│   │   ├── screener.py         # 09:50 스크리닝
│   │   ├── monitor.py          # 실시간 신고가 감시/반등 감시
│   │   ├── trader.py           # 주문 실행
│   │   └── risk_manager.py     # 손절, 포지션 관리
│   ├── models/
│   │   ├── stock.py            # StockCandidate, TradeTarget
│   │   ├── order.py            # Order, Position
│   │   └── trade.py            # TradeRecord, DailySummary
│   ├── dashboard/
│   │   └── app.py              # 웹 대시보드 (port 8503)
│   ├── storage/
│   │   └── database.py         # SQLite CRUD
│   ├── backtest/
│   │   ├── data_collector.py   # pykrx 과거 데이터 수집
│   │   ├── simulator.py        # 전략 시뮬레이션 엔진
│   │   └── report.py           # 백테스트 결과 리포트
│   └── utils/
│       ├── notifier.py         # 텔레그램 알림
│       ├── market_calendar.py  # 거래일 확인
│       ├── logger.py           # loguru 설정
│       └── tunnel.py           # Cloudflare Tunnel
└── tests/
    └── test_models.py
```
""")

    # ── 05 이슈 트래커 ────────────────────────────────────
    write(VAULT_ROOT / "05_이슈_트래커.md", f"""
# 이슈 트래커
> 최종 업데이트: {TODAY}

---

## 이슈 템플릿

```
## [ISSUE-NNN] 제목
- **발생일:** YYYY-MM-DD
- **Phase:** Phase N
- **심각도:** 🔴 Critical / 🟡 Warning / 🟢 Minor
- **상태:** 미해결 / 해결완료

### 현상

### 원인

### 해결
```

---

## 미해결 이슈

### [ISSUE-001] pykrx 백테스트 데이터 수집 버그
- **발생일:** 미확인 (Phase 0 진입 전)
- **Phase:** Phase 0
- **심각도:** 🟡 Warning
- **상태:** 미해결

#### 현상
pykrx로 과거 분봉 데이터 수집 시 버그 발생 가능성

#### 원인
미확인

#### 해결
우회 방법 검토 중 → 해결 후 Phase 0 백테스트 실행

---

## 알려진 제약사항

| 항목 | 내용 |
|------|------|
| KIS Rate Limit | 실거래 초당 20건, 모의투자 초당 2건 |
| pykrx 장중 호출 | 금지 (타임아웃/IP차단 위험) |
| 프로그램매매 데이터 지연 | 5분 초과 시 전략 전제 재검토 필요 |
| 선물 종목코드 | `101S3000` — 분기마다 변경 (갱신 필요) |
| WebSocket 끊김 | 긴급 취소 로직 구현됨 (`_on_ws_disconnect`) |

---

## 해결된 이슈

_없음_
""")

    # ── 06 백테스트 결과 ──────────────────────────────────
    write(VAULT_ROOT / "06_백테스트_결과.md", f"""
# 백테스트 결과
> 최종 업데이트: {TODAY}

---

## 실행 방법

```bash
cd C:\\Users\\terryn\\AUTOTRADE

# 1. 과거 데이터 수집 (pykrx — 장 마감 후 or 주말에 실행)
python -m src.backtest.data_collector

# 2. 시뮬레이션 실행
python -m src.backtest.simulator

# 3. 결과 리포트
python -m src.backtest.report
```

> ⚠️ pykrx는 장중 호출 금지. 백테스트는 장 마감 후 실행.

---

## 결과 기록

| 실행일 | 검증 기간 | 종목수 | 승률 | 평균수익 | 평균손실 | 거래비용 반영 순수익 | 비고 |
|--------|---------|--------|------|---------|---------|-------------------|------|
| (미실행) | | | | | | | |

---

## Phase 0 통과 기준

- [ ] 승률 ≥ 80%
- [ ] 거래비용(0.4~0.7%) 반영 순수익률 양수
- [ ] 최적 파라미터 도출 완료

---

## 파라미터 튜닝 기록

| 날짜 | 변경 항목 | 변경 전 | 변경 후 | 결과 |
|------|---------|--------|--------|------|
| | | | | |

---

## 거래비용 구조

| 항목 | 비율 |
|------|------|
| 증권거래세 (매도) | 0.20% |
| KIS 위탁수수료 | ~0.015% |
| 슬리피지 추정 | 0.1~0.3% |
| **왕복 합계** | **~0.4~0.7%** |

> 목표 수익 1~2%대 → 거래비용이 수익성에 직접 영향. 백테스트 시 반드시 반영.
""")

    # ── 개발일지 ──────────────────────────────────────────
    write(VAULT_ROOT / f"개발일지/{TODAY}.md", f"""
# 개발일지 — {TODAY}

## 오늘 한 일

- Obsidian AUTOTRADE vault 초기 설정 완료 (KIS API 기준)

## 다음 할 일

- [ ] Phase 0 백테스트 실행 (pykrx 버그 우회 방법 확인)
- [ ] Phase 2 장중 스크리닝 HTS 대조 검증 준비
- [ ] 선물 종목코드 `101S3000` 분기 갱신 여부 확인

## 메모

_개발하면서 생긴 생각, 의문, 발견 기록_

## 이슈

_없음_
""")

    write(VAULT_ROOT / "개발일지/_템플릿.md", """
# 개발일지 — {날짜}

## 오늘 한 일

-

## 다음 할 일

- [ ]

## 메모

_개발하면서 생긴 생각, 의문, 발견_

## 이슈

_발생 이슈 → [[05_이슈_트래커]]에도 기록_
""")

    # ── 매매일지 ──────────────────────────────────────────
    write(VAULT_ROOT / "매매일지/_템플릿.md", """
# 매매일지 — {날짜}

## 당일 시장 환경

- KOSPI: % 등락
- KOSDAQ: % 등락
- 특이사항:

## 스크리닝 결과 (09:50)

| 종목 | 거래대금 | 프로그램순매수비중 | 상승률 | 통과여부 |
|------|---------|-----------------|--------|---------|
| | | | | |

## 신고가 달성 / 매수 진입

| 종목 | 신고가 시각 | 신고가 | 1차 매수가 | 2차 매수가 | 체결 여부 |
|------|-----------|--------|---------|---------|---------|
| | | | | | |

## 청산 내역

| 종목 | 평균매수가 | 청산가 | 수익률 | 청산사유 | 보유시간 |
|------|---------|--------|--------|--------|---------|
| | | | | | |

## 일일 요약

- 매매 횟수: 회 (최대 3회)
- 승: 건 / 패: 건
- 당일 P&L: 원

## 반성 & 개선점

_전략 작동 여부, 개선 필요 사항_
""")

    print(f"""
──────────────────────────────────────
Obsidian Vault 생성 완료!

생성된 파일:
  00_대시보드.md            ← 메인 진입점
  01_전략_마스터스펙.md      ← 전략 전체 설계
  02_Phase_진행상황.md      ← Phase별 체크리스트
  03_KIS_API_레퍼런스.md    ← 엔드포인트, TR ID
  04_환경설정.md             ← .env, 실행 방법
  05_이슈_트래커.md
  06_백테스트_결과.md
  개발일지/{TODAY}.md
  개발일지/_템플릿.md
  매매일지/_템플릿.md

Obsidian에서 vault를 열어 확인하세요:
  {VAULT_ROOT}
──────────────────────────────────────
""")


if __name__ == "__main__":
    main()
