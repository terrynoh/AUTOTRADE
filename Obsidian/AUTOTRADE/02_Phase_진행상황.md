# Phase별 진행상황 체크리스트
> 최종 업데이트: 2026-04-06

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
