# HANDOFF: AUTOTRADE LIVE Day 3 목표 — 내일 검증·관찰 사항 (2026-04-21)

> **문서 목적**: 신규 Claude 세션이 Day 3 세션에 **내일 해야 할 일만** 한 번에 파악하도록 정리한 단일 인계 문서.
> **작성 시점**: 2026-04-20 Day 2 세션 종료 (M-LIVE-02-FIX 배포 완료 직후)
> **최종 갱신**: 2026-04-20 저녁 — Terry 답변 반영 (LIVE-05/07 해소, 도메인 hwrim.trade 확정, R-13 강화 방향 옵션 A 확정)

---

## 0. 필독 참조 쓰레드

Day 3 세션 시작 시 다음 문서들을 **순서대로** 로드. 각 문서 범위 + 내일 필요한 이유:

| 순서 | 문서 | 다루는 범위 | 내일 필요한 이유 |
|------|------|-----------|---------------|
| 1 | `C:\Users\terryn\AUTOTRADE\CLAUDE.md` (vLive 2.1) | 전체 매매 명세, §5.6 협업 규칙 | 공통 베이스 |
| 2 | `docs/HANDOFF_2026-04-20_LIVE_Day1_analysis.md` | Day 1 오전 1차 분석 | §2-2 결론 번복됨 (Session 2 참조) |
| 3 | `docs/HANDOFF_2026-04-20_session2_api_bug_investigation.md` | ISSUE-LIVE-02 가설 A~D 전수 조사 | 내일 P0-1 검증 대상 근거 |
| 4 | **본 문서** | Day 3 목표·기대값·미검증 항목 | 내일 실행 순서 |

**Day 2 완료 작업 요약** (상세는 참조 쓰레드):
- ISSUE-LIVE-02 근본 원인 확정 (UN 시장코드 편향, NXT 미상장 종목) + 방안 2 확정 + M-LIVE-02-FIX 배포
- M-DIAG-PROG v2 + W-SAFETY-1 + 포트 8503 + run.py 변경 배포
- GCP Seoul 환경 이전 반영
- 진단 스크립트 2개 생성 (`scripts/diag_prog_realtime.py`, `scripts/diag_prog_market_codes.py`)
- 전수 소스 리뷰 완료 (CLAUDE.md vLive 2.2 업데이트 스펙 별도 세션 예정)

**오늘 배포 커밋**:
- `2bbf961` W-SAFETY-1 (F1 SA-5e / F2' / H2 / VI-Observer Stage 1)
- `06dd183` run.py 포트 8501 → 8503
- `0d932c5` M-DIAG-PROG v2 kis.py 진단 로깅
- **(신규, Day 2 종료 시점 push 전)** M-LIVE-02-FIX kis.py UN→J fallback

**Terry 답변 확정 (Day 2 저녁)**:
- Q1 R-13 Single→Double 강화 방향 = **옵션 A (의도된 손절 타이트화)**
- Q2 도메인 = **`hwrim.trade` 단일** (`hwrimlab.trade` 계열은 잔재)
- Q3 simulation yaml + DEPRECATED 파라미터 = 즉시 삭제 대상 (cleanup 별도 진행)
- ISSUE-LIVE-05 해소 (10:53/12:27/14:12 SIGTERM 모두 Terry 의도 재시작)
- send_dashboard_url.py = 과거 Quick Tunnel 잔재, 고정 URL 전환 후 무의미 (처분 결정 보류)

---

## 1. 내일 LIVE 에서 **반드시 검증되어야 할** 이슈

### 🔴 [P0-1] M-LIVE-02-FIX 실전 효과 검증

**배경**:
- Day 2 배포 완료. Day 2 운영 중 체결 0건으로 전체 경로 실전 미검증
- 수정 범위: `src/kis_api/kis.py::get_program_trade` UN 빈 배열 시 J fallback
- 참조: Session 2 HANDOFF 전체 + Day 2 diag 실측 (UN=J+NX 산술 증명 완료)

**검증 포인트** (내일 장중 로그 `logs/autotrade_2026-04-21.log` grep):

| # | grep 키워드 | 검증 목적 | 기대 결과 (정상) | 이상 신호 |
|---|-----------|---------|----------------|---------|
| 1 | `[PROG-FALLBACK-J]` | NXT 미상장 종목 fallback 작동 | **INFO 로그 1건 이상** (종목별 × 호출 수) | 0건 = 모든 입력이 NXT 이중상장 (가능) 또는 fallback 미작동 |
| 2 | `[PROG-J-FALLBACK]` | fallback 후 정상 파싱 | DEBUG 로그 (#1과 동일 카운트) | FALLBACK-J 발동됐는데 없음 = 파싱 경로 이상 |
| 3 | `[PROG-UN]` | NXT 이중상장 종목 기존 경로 유지 | DEBUG 로그 다수 | 0건 = 모든 종목 NXT 미상장 (드뭄) |
| 4 | `PROG-ANOMALY-EMPTY` | UN+J 모두 실패 (진짜 이상) | **0건 권장** | 1건 이상 = KIS 전체 장애 가능성, 즉시 조사 |
| 5 | `프로그램순매수=0 비중=0.00% 미달` | 0 반환 탈락 케이스 감소 | Day 1 대비 감소 (특히 NXT 미상장 KOSDAQ) | 패턴 동일 = fallback 미작동 |

**기대되는 최종 결과**:
- ✅ 방안 2 정상 작동: `[PROG-FALLBACK-J]` INFO + `[PROG-J-FALLBACK]` DEBUG 동일 카운트 + `PROG-ANOMALY-EMPTY` 거의 없음
- ✅ Day 1 유형 종목 복귀: 주성엔지·에코프로머티 같은 NXT 미상장 KOSDAQ 종목이 프로그램매매 필터에서 "통과" 또는 "미달" 로 정상 판정
- ✅ 리그레션 0: NXT 이중상장 종목 (한화비전·로보티즈·SK하이닉스 등) 기존 UN 경로로 정상 비중 판정
- ✅ Rate limit 여유: `_ratio_updater` 1초 폴링 지연 누적 없음

**비정상 판정 기준 (즉시 롤백 고려)**:
- `PROG-ANOMALY-EMPTY` 장중 5건 이상 (J fallback 후에도 실패 = KIS 측 이슈 또는 수정 부작용)
- 매수 발주 0건인데도 매매 파라미터 비중 재계산 에러 로그
- `[PROG-FALLBACK-J]` 전체 호출의 30% 이상 (NXT 이중상장 종목까지 fallback 도는 이상)

**롤백 절차**: `git revert <M-LIVE-02-FIX 커밋>` → `scp src/kis_api/kis.py` → `sudo systemctl restart autotrade`

---

### 🔴 [P0-2] W-SAFETY-1 실전 자연 검증

**배경**:
- Day 2 배포 완료 (커밋 `2bbf961`). 기동 시 F1 SA-5e 한 번만 실측 통과, 나머지 미검증
- 참조: Session 2 HANDOFF + CLAUDE.md W-SAFETY-1 항목

**검증 포인트** (자연 발생 시 확인):

| # | 검증 대상 | 발동 조건 | 기대 결과 |
|---|---------|---------|---------|
| 1 | F1 SA-5e (홀딩 잔존) | 기동 시 `get_balance().holdings` 존재 | 매매 거부 + 텔레그램 경고 |
| 2 | F2' unmatched (매수) | `on_live_buy_filled` None 반환 | `[F2-Timeout]` 또는 `[F2-Unmatched]` CRITICAL + 텔레그램 |
| 3 | F2' unmatched (매도) | `on_live_sell_filled` None 반환 | 동일 패턴 |
| 4 | H2 11:20 강제청산 실패 | 11:20:30 기준 `trader.has_position()==True` | `[H2]` CRITICAL + 텔레그램 |
| 5 | VI-Observer Stage 1 | H0UNCNT0 인덱스 34/35/43/44/45 필드값 변화 | `[VI-OBSERVER]` CRITICAL + 종목별 첫 변화 1회 텔레그램 |

**기대 결과**:
- ✅ F1: 잔고 0 이면 발동 안 함 (정상)
- ✅ F2'/H2: 자연 조건 미성립 시 발동 안 함 (이상 상황 없는 게 정상)
- 🟡 VI-Observer: 장중 VI 1건이라도 발동 시 필드값 실측 확보 → Stage 2 값 의미 확정용 데이터 수집 (해석 안 함, 로그만 축적)

---

## 2. 내일 **관찰만** 하고 수정 안 할 이슈

### 🟡 [P1-1] ISSUE-LIVE-03 HTTP 500 광범위 발생

**배경**:
- Day 1 오전 실측: 01:44:52 ~ 01:53:29 약 9분간 대부분 HTTP 500 WARNING
- 대략 초당 1.5~2건 발생
- TR_ID: `FHKST01010100` (~60%) + `FHPPG04650101` (~40%)
- 주문 TR (`TTTC0012U` 등) 0건 → 매매 주문 경로 영향 없음
- 재시도 로직 (`max_retries=2`, `http_retry_delay_sec=1.0`) 작동 중, 1차 재시도로 대부분 복구
- 참조: Session 3 로그 분석 결과

**내일 확인 포인트**:

| # | 확인 대상 | 목적 |
|---|---------|-----|
| 1 | HTTP 500 시간대별 분포 | Day 1 과 동일 (09:45~10:53 집중) 인지 종일 분포인지 |
| 2 | TR_ID 비율 | FHKST01010100 vs FHPPG04650101 |
| 3 | 재시도 소진 (2/2) 빈도 | 0 이어야 정상 |
| 4 | `KIS API 요청 실패 [...]: 최대 재시도 횟수 초과` ERROR | 0 이어야 정상 |

**grep 명령**:
```bash
grep "HTTP 500" logs/autotrade_2026-04-21.log | wc -l
grep "HTTP 500" logs/autotrade_2026-04-21.log | awk '{print substr($1,12,5)}' | sort | uniq -c   # 분당 빈도
grep "HTTP 500" logs/autotrade_2026-04-21.log | grep -oP 'FH[A-Z0-9]+' | sort | uniq -c   # TR_ID 별
grep "최대 재시도 횟수 초과" logs/autotrade_2026-04-21.log
```

**기대값**:
- Day 1 과 유사한 패턴 → KIS 인프라 상시 이슈 확정 → 수정 방향 결정 (옵션 A retry 증가 / B 로그 레벨 조정 / C 집계 로깅)
- Day 1 대비 현저히 감소 → 일시적 부하였음 → 수정 불필요
- **판단 기준은 내일 데이터 수집 후 Day 3 세션에서 결정**, 오늘 추측 기반 수정 배제

**수정 트리거 조건**:
- 재시도 2/2 발생 또는 "최대 재시도 횟수 초과" ERROR 1건이라도 발생 → 즉시 옵션 A (retry 3회) 검토
- 그 외에는 관찰만

---

## 3. 해소된 이슈 (참고용)

### ✅ ISSUE-LIVE-05 (10:53/12:27/14:12 SIGTERM) — **해소**

Terry 확인 결과 세 건 모두 의도된 재시작:
- 10:53 수동 — Day 1 오전 매수 실패 + 타임오버 후 오늘 라이브 포기 결정
- 12:27 수동 — 설정 조정
- 14:12 systemctl restart — M-LIVE-02-FIX 배포

추가 조사 불필요.

### ✅ ISSUE-LIVE-07 (R-13 Single→Double 강화 시 손절선 상승) — **의도 확정**

CLAUDE.md § 2 R-13 재확인 + Terry Q1 답변 = **옵션 A (의도된 손절 타이트화)**.

매매 철학: 비중 상승 = 프로그램 유입 가속 신호 → 손절도 타이트하게 관리. ENTERED 상태에서 현재가가 새 손절선 아래면 즉시 DROPPED 발동도 의도된 동작.

관찰 불필요. 단 CLAUDE.md vLive 2.2 에서 명세 대칭 기술 추가 예정 (별도 세션).

---

## 4. 미검증 항목 (자연 발생 대기)

이전 세션부터 누적된 미검증 목록. 내일 자연 발생 시 확인, 발생 안 해도 추가 조치 없음:

| # | 항목 | 발동 조건 | 기대 검증 |
|---|------|---------|---------|
| 8 | R15-007 청산 후 예수금 갱신 | 첫 매매 청산 완료 | P&L 반영된 `buyable_cash` 조회 성공, 로그상 `직전 _available_cash` → `new_cash` 정합 |
| 9 | Phase 2 B Position invariant | 매수/매도 체결 발생 | `_check_position_invariant` 불일치 CRITICAL 0건 |
| 10 | W-SAFETY-1 F2' | 체결 매칭 실패 | 발동 안 하는 게 정상. 발동 시 로그 확보 |
| 11 | W-SAFETY-1 H2 force_liquidate 정상 도달 | 11:20 시각 도달 + ENTERED 유지 | `on_force_liquidate` + 30초 대기 블록 실행 확인 |
| 12 | 네트워크 장애 방어 3종 | WS 끊김 / REST timeout / ws_key 재발급 | 자연 발생 시 로그로 확인 |
| 14 | F1 SA-5e holdings 검증 | 첫 체결 발생 후 다음 기동 | 재시작 시 holdings 감지 → 매매 거부 |
| 15 |#15 daily_archive.sh truncate 정상 동작 (autotrade.log/err 초기화 확인)

#15 daily_archive.sh truncate 정상 동작 (autotrade.log/err 초기화 확인)

---

## 5. 내일 시작 시 1번째 작업 — 로그 수집

장 종료 후 (15:30 KST 이후) Terry 수동 실행:

```bat
cd C:\Users\terryn\AUTOTRADE
logs\sync_logs.bat
```

**수집 파일**:
- `logs/trades/2026-04-21/autotrade_2026-04-21.log`
- `logs/trades/2026-04-21/trades_2026-04-21.log`
- `logs/trades/2026-04-21/ops_<HHMMSS>/journal_autotrade_today.log`
- `logs/trades/2026-04-21/ops_<HHMMSS>/journal_autotrade_boot.log`
- `logs/trades/2026-04-21/ops_<HHMMSS>/ops_snapshot.txt`
- `logs/trades/2026-04-21/ops_<HHMMSS>/service_restart_history.log`
- `data/trades.db`

**수집 완료 후 우선 grep 순서**:
1. `PROG-FALLBACK-J` 카운트 (P0-1 검증 핵심)
2. `HTTP 500` 시간대별 분포 (P1-1 관찰)
3. `PROG-ANOMALY-EMPTY` 발생 여부 (즉시 롤백 트리거)
4. `[F1 SA-5e]` / `[F2-` / `[H2]` / `[VI-OBSERVER]` (P0-2 W-SAFETY-1)
5. `[R15-007] 청산 후 예수금 갱신` (미검증 #8)
6. `[Phase 2 B] Position 불일치` (미검증 #9)

---

## 6. 내일 세션 흐름 권고
Step 1. 본 HANDOFF + 참조 쓰레드 3개 로드
Step 2. sync_logs.bat 실행 결과 공유
Step 3. P0-1 (M-LIVE-02-FIX) 검증 결과 확인 — 최우선
Step 4. P0-2 (W-SAFETY-1) 자연 발생 여부 점검
Step 5. P1-1 (LIVE-03 HTTP 500) 관찰 데이터 수집
Step 6. 이슈별 판단:
- M-LIVE-02-FIX 정상 → 장 마감 후 CLAUDE.md vLive 2.2 업데이트 세션 진입
- 이상 발생 시 → 롤백 또는 긴급 수정
- HTTP 500 수정 트리거 발동 시 → 옵션 A retry 3회 검토
Step 7. Day 3 HANDOFF 작성 (Day 4 인계용)

---

## 7. 긴급 연락 포인트 (§5.6 #3 멈춤 조건)

**Day 3 세션 중 즉시 중단하고 Terry 보고해야 할 상황**:
- `PROG-ANOMALY-EMPTY` 5건 이상 (KIS 전체 장애 가능성)
- `최대 재시도 횟수 초과` ERROR 1건이라도 발생
- 매수 체결 후 `Phase 2 B Position 불일치` CRITICAL 발생
- 예상 외 SIGTERM/재시작 발생 (Terry 가 지시하지 않은 재시작)
- 매도 체결 후 `[F2-Timeout]` / `[F2-Unmatched]` 발생

---

## 8. 변경 없이 유지할 항목

- `CLAUDE.md` vLive 2.1 매매 명세 (vLive 2.2 업데이트는 별도 세션)
- `config/strategy_params.yaml` 전략 파라미터 (cleanup 별도 세션)
- `config/stock_master.json` (2,773 종목)
- `scripts/diag_prog_realtime.py`, `scripts/diag_prog_market_codes.py` (재활용 도구, 유지)

---

## 9. 보류 항목 (내일 범위 밖, 기록만)

- **CLAUDE.md vLive 2.2 업데이트** — Day 2 저녁 리뷰에서 구성 방향 확정. P1~P12 시간축 경로 재편 + 도메인 통일 + 시각 정정 + R-13 강화 방향 대칭 기술. 내일 장 마감 후 세션에서 착수 예정
- **Code cleanup** — DEPRECATED 파라미터 제거 + trader.py 로그 버그 수정 + simulation yaml 제거 + 도메인 통일 + 백업 파일 삭제. CLAUDE.md 업데이트와 함께 진행
- **개발일지 누락분 재구성** — `Obsidian/개발일지/` 는 2026-04-13 이후 누락. R-10 / R-14 / R-16 / Phase 2 B / R15-005 / R15-007 / W-SAFETY-1 / M-LIVE-02-FIX 일지 재구성 필요 (우선순위 낮음, Phase 3 전 권고)
- **send_dashboard_url.py 처분** — 고정 URL 전환 후 무의미. 삭제 / 보류 / 용도 변경 Terry 결정 필요

---

*작성자: Claude (Day 2 세션, 2026-04-20)*
*다음 세션 시작 권장 프롬프트*:
> *"Day 3 세션 시작. CLAUDE.md vLive 2.1 + docs/HANDOFF_2026-04-21_LIVE_Day3_objectives.md + Day 1/Session 2 HANDOFF 로드 완료. sync_logs.bat 실행 결과 공유하니 P0-1 (M-LIVE-02-FIX 검증) 부터 진행. LIVE-05/07 은 해소됨, P1-1 HTTP 500 관찰만 진행."*