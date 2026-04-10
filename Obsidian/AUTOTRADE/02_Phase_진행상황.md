# Phase별 진행상황 체크리스트
> 최종 업데이트: 2026-04-08

---

## Phase 0 — 백테스트 ⬜ (코드 완료, 미실행)

**목표:** pykrx 과거 데이터 → 전략 승률 정량 검증 (≥80%)

```bash
cd C:\Users\terryn\AUTOTRADE
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

## Phase 2 — 스크리닝 검증 🟡 (수동입력 방식 전환)

**목표:** 수동 입력 + 자동 검증 방식으로 변경. API 테스트 통과.

> ⚠️ KIS API에 프로그램 순매수 순위 엔드포인트 없음 (종목별 개별 조회만 가능)
> → 1단계를 완전 자동에서 **수동 입력 + 자동 검증**으로 전환 (ISSUE-010)

**변경 내용:**
- 사용자가 5~10개 종목 수동 입력 (대시보드 UI / 텔레그램 `/target`)
- 시스템이 자동 검증: 상승률 > 0% → 거래대금 ≥ 500억 → 상한가 미도달 → 프로그램순매수비중 ≥ 5% → 상승률 최고 선정

- [x] `screener.py` — `run_manual(codes)` 메서드 구현
- [x] `dashboard/app.py` — `/api/set-targets`, `/api/run-manual-screening` 엔드포인트
- [x] `dashboard/templates/index.html` — 수동 종목 입력 UI
- [x] `notifier.py` — `/target`, `/clear` 텔레그램 명령 핸들러
- [x] `main.py` — `set_manual_codes()`, 스크리닝 분기 로직
- [x] API 테스트 통과 (2026-04-06): 거래대금/상한가/프로그램 필터 정상 동작
- [ ] 장중 HTS 대조 (다음 개장일)

---

## Phase 3 — DRY_RUN 풀가동 🟡 (2026-04-06 시작)

**목표:** 완전한 가상 매매 엔진 1일 풀가동

```bash
# .env에서 TRADE_MODE=dry_run 확인 후 실행
python -m src.main
```

**2026-04-06 인프라 확인 결과:**
- [x] `TRADE_MODE=dry_run` 설정 완료
- [x] KIS API 연결 정상 (모의투자 URL)
- [x] 예수금 확인: 50,000,000원 (DRY_RUN 가상)
- [x] Cloudflare Tunnel 정상 기동
- [x] 대시보드 URL 발급: https://michel-restrictions-chapel-detailed.trycloudflare.com
- [ ] 텔레그램 알림 — 1개 채팅 정상, Chat ID 47315583 오류 (→ [[05_이슈_트래커]] ISSUE-002)

**2026-04-07(월) 본격 관찰 항목 (장 개장일):**
- [ ] 09:50 스크리닝 실행 확인
- [ ] 스크리닝 결과 HTS 수동 대조 (Phase 2 겸용)
- [ ] 09:55~ WebSocket 실시간 체결가 수신 확인
- [ ] 고가 확정 트리거(1% 하락) 시그널 로그 확인
- [ ] 가상 체결(simulate_fills) 동작 확인
- [ ] 6개 청산 조건 동시 모니터링 로그 확인
- [ ] 멀티 트레이드 (최대 3회) 전환 정상
- [ ] 대시보드 실시간 상태 표시 정상

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

---

## Phase α-0 — 매매 로직 누락 보강 ✅ (2026-04-07 완료)

**목표:** 클라우드 이전 전에 매매 로직 명세상 누락된 두 부분 구현 + 회귀 검증

- [x] **항목 1** — 10시 이전 타임아웃 가드 추가 + 주석 "25분" → "20분" 정정
  - `monitor.py`: 타임아웃 시작 시점이 10:00 이전이면 카운트 안 함
  - `strategy_params.yaml`: `timeout_start_after` 변수화
- [x] **항목 2** — 청산 조건 ④ 선물 급락 WebSocket 구독 구현
  - `kis.py`: `subscribe_futures()` + `H0IFCNT0` TR ID
  - `main.py`: `on_futures_price()` 콜백 연결
- [x] DRY_RUN 회귀 검증 통과

---

## Phase α-1a — 클라우드 lift and shift ✅ (2026-04-08 완료)

**목표:** 현재 코드 그대로 Oracle Cloud에 올리고 24/7 가동

- [x] Oracle Cloud VM.Standard.E2.1.Micro (ap-chuncheon-1, AMD x86_64, 956Mi + 4GB swap)
- [x] Python 3.11.15 + venv + 의존성 설치
- [x] SQLite WAL 모드 활성화 (`database.py`)
- [x] SIGTERM graceful shutdown 7단계 (`main.py`)
- [x] .env 배포 (USE_LIVE_API=false, TRADE_MODE=dry_run)
- [x] cloudflared v2026.3.0 설치 (PoP icn05 인천)
- [x] systemd autotrade.service (active + enabled)
- [x] Quick Tunnel 가동 + 텔레그램 URL 발송 확인
- [x] KIS 토큰 발급 + 선물 WebSocket 구독 확인

**서버:** ubuntu@134.185.115.229
**가동 시작:** 2026-04-08 01:02 KST

---

## Phase α-1b — Named Tunnel 전환 🟡 (진행 예정)

**목표:** Quick Tunnel → Cloudflare Named Tunnel 전환 → 고정 URL

**사전 필요 (수석님):**
- [ ] 도메인 결정 (기존 보유 or Cloudflare Registrar 신규 구매)
- [ ] 서브도메인 결정 (예: autotrade.example.com)
- [ ] Cloudflare 계정 + DNS 네임서버 이전 + 전파 대기

**작업 (도메인 준비 후):**
- [ ] Phase B: src/utils/tunnel.py Named 모드 지원 여부 코드 분석
- [ ] Phase C: Cloudflare Zero Trust Named Tunnel 생성
- [ ] Phase D: 서버 config 파일 + .env 환경변수 추가
- [ ] Phase E: 16:00 이후 장중 회피 무중단 전환
- [ ] 고정 URL 브라우저 접속 확인

> ⚠️ ISSUE-028 참조

---

## Phase α-1 (잔여) — 다중 사용자 UI 📅 (α-1b 후)

**목표:** A(운영자) / B(capital provider) 권한 분리 대시보드

- [ ] DB user 테이블 (id, name, role: operator | capital_provider, password_hash)
- [ ] 로그인 페이지 + HTTP-only Cookie + Session
- [ ] operator: 종목 입력 활성 / capital_provider: 조회만
- [ ] audit_log 테이블 (누가 언제 무엇을 변경)

---

## 미해결 이슈 (α-1b 이전 해결 필수)

### 🔴 ISSUE-036 — _monitors 리스트 stale + 청산 경로 단절
- 발견: 2026-04-08
- 심각도: CRITICAL (실거래 전환 시 position 영구 잠김 → 자본 무한 손실)
- 근본 원인: `_on_screening` 이 `_monitors` 초기화 없이 새 monitor append + `_active_monitor`만 교체. 청산 경로가 `_active_monitor` 전용 → evicted Mon_1 position 영구 잠김
- 영향: 실시간 손절/타임아웃/목표가 평가 안 됨, 15:20 강제청산 신호 소비 안 됨, 대시보드 카드 중복
- 트리거: dashboard "수동 스크리닝" 재클릭 (횟수 제한 없음)
- 수정 시기: α-1b (실거래 전환 전 필수)
- 수정 방향 후보: CLAUDE.md 섹션 11 참조

### 🟠 ISSUE-037 — can_open_position(0) 하드코딩 인수
- 발견: 2026-04-08 (ISSUE-036 진단 중)
- 심각도: HIGH (포지션 가드 무효, max_simultaneous_positions 설정 무력화)
- 증상: `main.py:268` 에서 실제 포지션 수 대신 0 하드코딩 → 포지션 가드 항상 통과
- 수정 시기: ISSUE-036 수정과 함께 (α-1b)

