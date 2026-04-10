# 매매일지 — {YYYY-MM-DD} ({요일})

> R-08 영역. 현재 매매 명세 = `CLAUDE.md` §2 단일 진실.

---

## 1. 시장 환경

| 지수 | 시가 | 종가 | 등락률 | 비고 |
|---|---|---|---|---|
| KOSPI | | | % | |
| KOSDAQ | | | % | |
| KOSPI200 선물 | | | % | |

특이사항:
-

---

## 2. 운영 상태 확인

| 항목 | 값 / 상태 |
|---|---|
| TRADE_MODE | dry_run / paper / live |
| 시스템 PID | |
| 가동 시각 | KST |
| 텔레그램 알림 | 정상 / 오류 |
| 대시보드 URL | |

---

## 3. 종목 입력 (09:30~09:50)

| 시각 | 입력 종목 (코드:이름) | 채널 |
|---|---|---|
| | | 대시보드 / 텔레그램 |

---

## 4. 09:50 스크리닝 결과

| 종목 | 거래대금 (억) | 프로그램순매수 (%) | 등락률 (%) | 통과 여부 | 탈락 사유 |
|---|---|---|---|---|---|
| | | | | ✅ / ❌ | |

스크리닝 후 활성 Watcher 수:
재스크리닝 차단 동작: 정상 / 사고

---

## 5. Watcher 별 상태 추적

각 종목의 8 state 전이 (WATCHING → TRIGGERED → READY → ENTERED → EXITED / SKIPPED / DROPPED).

### 종목 1: ___
| 시각 | 상태 | 트리거 / 이벤트 | 가격 |
|---|---|---|---|
| | WATCHING | 스크리닝 통과 | |
| | TRIGGERED | 신고가 + 1% 하락 | confirmed_high = , target_buy1 = , target_buy2 = , hard_stop = |
| | READY | 진입 윈도우 + 매수 범위 | |
| | ENTERED | buy1 체결 | |
| | (T2) | buy2 체결 → _t2_callback | |
| | EXITED | 청산 사유: TARGET / HARD_STOP / TIMEOUT / FUTURES_STOP / FORCE_LIQUIDATE | |

### 종목 2: ___
| (동일 양식) |

---

## 6. R-08 두 번째 매매 체인 (T1/T2/T3)

### T1 — 첫 종목 ENTERED
- 시각:
- 종목:
- `_active_code` 설정 확인:

### T2 — 첫 종목 buy2 체결
- 시각:
- `_on_t2` 진입 확인:
- 진입 윈도우 (10:00~10:55) 안 여부:
- YES 후보 수집 (state == READY):
- tiebreaker 통과 종목:
  - KOSPI 눌림폭 ≥ 3.8% / KOSDAQ ≥ 5.6% 필터
  - 눌림폭 최대 종목:
- ReservationSnapshot 생성:
  - code:
  - confirmed_high_at_t2:
  - pullback_pct_at_t2:
  - target_buy1_price_at_t2:

### T3 — 첫 종목 EXITED
- 시각:
- `_on_exit_done` 의 6.5 단계 진입:
- `handle_t3` 진입 윈도우 체크:
- 예약 재확인 (5 항목):
  - [ ] 종목이 watchers 에 존재
  - [ ] is_yes (state == READY)
  - [ ] not is_terminal
  - [ ] target_buy1_price 가 T2 시점 값과 동일
  - [ ] tiebreaker 조건 여전히 충족
- 결과: 예약 진입 / 재판정 / 포기

### N차 매매 연쇄
- 발생 차수: 1차 / 2차 / 3차 / ... / 차
- 윈도우 종료 (10:55) 자연 종료 / daily_loss_limit / 후보 0

---

## 7. last-line defense 발화 확인

- `Trader.place_buy_orders` last-line defense (11:00) 발화: **YES / NO**
- 정상 = NO. YES 면 *코드 경로 빈틈* → 즉시 ISSUE-NNN 등록.

---

## 8. 청산 내역

| 종목 | 평균매수가 | 청산가 | 수익률 | 청산 사유 | 보유 시간 |
|---|---|---|---|---|---|
| | | | % | | |

---

## 9. 일일 요약

- 매매 횟수: 회
- 승: 건 / 패: 건
- 당일 P&L: 원
- 누적 손실 한도 (`daily_loss_limit_pct = 3%`) 도달 여부:

---

## 10. 4 alpha 지표 (누적 분석용)

| 지표 | 오늘 |
|---|---|
| Win rate | / 매매 |
| 평균 R:R | |
| 누적 자산 변동 | |
| Regime 분류 | 상승일 / 하락일 / 횡보일 / 고변동성일 |

---

## 11. 빈틈 / 사고 발견

_있다면 [[이슈_트래커]] 에 ISSUE-NNN 등록 후 본 일지에서 링크_

-

---

## 12. R-08 검증 포인트 (4/13~ 누적 평가)

- [ ] W-11d/e 두 번째 매매 체인 정상 동작
- [ ] W-14 1차 진입 빈도 ↑ 의도 발현
- [ ] W-13 timeout 20 분 효과 (이전 10분 대비)
- [ ] W-12-rev2 호가 단위 KRX 위반 0
- [ ] last-line defense 0 발화
- [ ] R-08 백로그 #9 재매수 갭 사례 발생 여부

---

## 반성 & 개선점

_전략 동작 / 사고 / 시장 해석 / 다음 거래일 준비_

-
