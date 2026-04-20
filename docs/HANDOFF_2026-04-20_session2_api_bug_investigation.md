# HANDOFF: AUTOTRADE LIVE Day 1 Session 2 — API 버그 + TZ 전환 분석 (2026-04-20)

> **목적**: 신규 Claude 세션이 Session 2 의 컨텍스트를 이어가기 위한 self-contained 인계 문서
> **작성**: Session 2 종료 시점 (2026-04-20)
> **선행 필독**:
> 1. `C:\Users\terryn\AUTOTRADE\CLAUDE.md` (vLive 2.1)
> 2. `C:\Users\terryn\AUTOTRADE\docs\HANDOFF_2026-04-20_LIVE_Day1_analysis.md` (Session 1)
> 3. 본 문서

---

## 0. Session 2 개요

### 시작점
Session 1 HANDOFF 의 "주성엔지니어링 탈락 사유 확정: 프로그램 비중 0%, 필터 정상 작동" 이라는 결론에 대해 Terry 가 HTS 실측 스크린샷으로 이의 제기 → 재검증 시작.

### 주요 발견 (한 줄 요약)
1. **Session 1 결론 번복**: 주성엔지니어링 실제 비중 10.63% (KOSDAQ Double 대상), AUTOTRADE만 0 반환 — **시스템 버그**
2. **서버 TZ UTC → KST 전환** (오늘 아침 Terry 수정): 매매 로직 무영향 확정, 단 cron/rotation/journalctl은 확인 필요
3. **KIS API 문서 검증 결과**: output 배열 순서 가설 기각, 종목별 집계 누락 (가설 D) 이 가장 유력
4. **우리기술 + 에코프로머티 동일 증상 발견**: 주성엔지 혼자 문제가 아님. 체계적 버그

---

## 1. Session 1 결론 번복 — 주성엔지니어링 건

### 번복 근거 (HTS 스크린샷 2컷)

**스크린샷 1 (09:49:41 KST 기준)**:
- 거래대금: 92,903백만원 (929.03억)
- 프로그램 매수: 37,989백만원
- 프로그램 매도: 28,115백만원
- 프로그램 순매수: **9,874백만원 (98.74억)**
- 실제 비중: **10.63%** (KOSDAQ Double 진입 대상)

**스크린샷 2 (09:50:30~56 KST 기준)**:
- 09:50 이후 값이 12~13%대로 상승 유지

### AUTOTRADE 로그 (동일 시점)
```
00:49:05.962 (= 09:49:05 KST)
  주성엔지니어링(036930) KOSDAQ 현재가=79,000 등락=+12.22% 거래대금=929억  ← 거래대금 일치
00:49:08.105
  주성엔지니어링(036930) 프로그램순매수=0 비중=0.00% 미달                     ← HTS와 불일치
```

**거래대금은 정확히 일치**하는데 **프로그램 순매수만 0**. 두 값은 다른 엔드포인트에서 옴:
- 거래대금 → `inquire-price` (TR `FHKST01010100`) ✅ 정상
- 프로그램 순매수 → `program-trade-by-stock` (TR `FHPPG04650101`) ❌ 0 반환

### Session 1 HANDOFF 수정 필요 항목
- 섹션 2-2 "주성엔지니어링 탈락 사유 — 확정" 전체 재작성
- 기존 "필터 정상 작동, 재고 불필요" → 새 "시스템 버그 (ISSUE-LIVE-02), 매매 대상 놓침"

---

## 2. 서버 TZ UTC → KST 전환 영향 분석

### 배경
Terry 가 오늘 아침 서버 TZ를 UTC → KST로 수정 (이유: Day 1 운영 중 타임존 오설정 발견).

### 코드 검증 결과 (§5.6 사실 base)

| 파일 | 시간 사용 방식 | OS TZ 영향 |
|------|---------------|-----------|
| `src/utils/market_calendar.py` | `now_kst() = datetime.now(KST)` (명시 TZ) | **없음** |
| `src/main.py` 전역 (스케줄/루프/콜백) | 모두 `now_kst()` 기반 주입 | **없음** |
| `src/core/watcher.py` | 외부 주입 `ts` 파라미터, 내부 `datetime.now()` 호출 0건 | **없음** |
| `src/storage/trade_logger.py` | `trade_date = now_kst().date()`, 체결 시각 ts 주입 | **없음** |
| `src/kis_api/kis.py` | `time.time()`, `time.monotonic()` (TZ 무관) | **없음** |

**매매 로직 영향 확정: 없음**. CLAUDE.md 주석의 "시스템 시간대와 무관" 설계 의도가 유효함.

### 영향 있는 영역 (확인/조치 필요)

**🚨 영향 있음 — 내일 매매 보장 위해 필수 확인**
- **cron 스케줄 해석**: 서버 UTC 시절 crontab `35 21 * * 1-5` = 21:35 UTC = 06:35 KST
  - 서버 TZ 전환 후: 동일 표현식이 **KST 21:35 (저녁 9시 35분)** 로 해석
  - **내일 아침 06:35 KST에 자동 기동 안 될 수 있음**
  - 조치: `crontab -l` 확인 → `CRON_TZ=Asia/Seoul` 추가 또는 시간 재작성

**⚠️ 영향 있음 — 로그 분석 혼선 주의**
- **loguru 로그 타임스탬프**: OS 로컬 TZ 기준 → 전환 이전 UTC, 이후 KST (같은 파일 내 혼재 가능)
- **로그 파일 rotation 경계**: `autotrade_{time:YYYY-MM-DD}.log` OS TZ 기준 자정 → 전환일 경계 로그 위치 혼선
- **journalctl 타임스탬프**: OS TZ 기준 → SIGTERM 추적 시 UTC/KST 혼동 주의

**✓ 영향 없음 (확인 완료)**
- Telegram Notifier (`ts.strftime(...)` 에서 ts = now_kst())
- KIS WebSocket 시각 필드 (KIS 서버 제공)
- SQLite DB `trade_date`, `entered_at`, `exited_at` (now_kst 기반)

### 🔍 확인 못 한 영역 (grep 필요)
- `src/dashboard/app.py` (FastAPI)
- `src/utils/notifier.py` (Telegram)
- `scripts/control_api.py`, `scripts/send_dashboard_url.py`

→ 여기서 `datetime.now()` (naive) 호출 없는지 검증 필요. Session 3 추가 과제.

---

## 3. KIS API 공식 문서 기반 가설 검증

한국투자증권 공식 API 문서 (Excel, 2026-04-16 기준) 확인 완료.

### `program-trade-by-stock` (FHPPG04650101) 스키마

- URL: `/uapi/domestic-stock/v1/quotations/program-trade-by-stock`
- Request: `FID_COND_MRKT_DIV_CODE` = "J"/"NX"/"UN" (우리는 UN), `FID_INPUT_ISCD` = 종목코드
- Response `output` = **object array** (시간별 스냅샷 배열)
- 각 원소 주요 필드:
  - `bsop_hour` (영업 시간, HHMMSS 6자리)
  - `stck_prpr`, `prdy_ctrt`, `acml_vol`
  - `whol_smtn_seln_tr_pbmn`, `whol_smtn_shnu_tr_pbmn`, **`whol_smtn_ntby_tr_pbmn`** (우리가 읽는 필드)
- **배열 정렬 순서 문서상 미명시**

### 가설 A (output 배열 순서 이슈) — 기각

**근거**: 오늘 로그 09:20:38 DEBUG 덤프에 한화비전(489790) output[0] 전체 구조가 찍힘:
```
output[0] = {
  'bsop_hour': '092038',         ← 조회 시점과 정확히 일치
  'stck_prpr': '86200',
  'whol_smtn_ntby_tr_pbmn': '9033916250',
  ...
}
```
→ `bsop_hour` 가 조회 시점 = **output[0] 는 최신 레코드 확정**. 순서 가정은 정확.

### 가설 B (응답 구조 이중 분기) — 판단 보류

- 문서상 단일 스키마, 분기 없음
- 다만 `_prog_trade_logged` 가 최초 1회만 DEBUG 덤프 → 0 반환 종목의 output 구조는 로그에 없음
- 빈 array `[]` 인지 `[{...'0'...}]` 인지 현재 로그로는 구분 불가 → M-DIAG-PROG v2 로 확인 예정

### 가설 C (KIS 서버 배치 지연) — 부분 설명

- 주성엔지 (09:47:04 첫 발생 → 09:47:58 AUTOTRADE 조회, 54초 gap) 만 설명 가능
- 우리기술/에코프로머티는 **09:20:39 부터 쭉 0** → 배치 지연으로 설명 불가

### 가설 D (종목별 집계 누락) — 가장 유력, 신규 정립

**교차 비교 증거** (autotrade_2026-04-20.log 1100줄 분석):

| 종목 | 시장 | 거래대금 | 비중 로그 추이 |
|------|------|---------|--------------|
| 우리기술(032820) | KOSDAQ | 693→1250억 | 09:20 / 09:34 / 09:47 / 09:49 **4회 모두 0** |
| 에코프로머티(450080) | **KOSPI** | 929→1389억 | 09:34 / 09:48 **2회 모두 0** |
| 주성엔지(036930) | KOSDAQ | 929억 | 09:47 / 09:49 **2회 모두 0** |

**핵심**:
- KOSPI/KOSDAQ 무관 (에코프로머티 KOSPI 도 0)
- 거래대금 충분 (500억 이상)
- "배치 지연"으로 설명 불가 (우리기술은 장 시작 20분 후부터 계속 0)

**가설 D 내용**: KIS REST API `program-trade-by-stock` 내부 집계 시스템이 **특정 종목을 집계 대상에서 누락**. HTS는 WebSocket (`H0UNPGM0`) 피드로 별개 경로로 데이터 수집 → HTS만 값이 찍힘.

### 교차 검증 소스 발견 — `H0UNPGM0` WebSocket

- 공식 TR: `H0UNPGM0` (국내주식 실시간프로그램매매 통합)
- constants.py 에 `WS_TR_PROGRAM = "H0STPGM0"` (KRX 전용)만 정의됨, **H0UNPGM0 미정의**
- Response Body: `MKSC_SHRN_ISCD`, `STCK_CNTG_HOUR`, `SELN_TR_PBMN`, `SHNU_TR_PBMN`, **`NTBY_TR_PBMN`** (순매수거래대금)
- REST 배치 지연 우회 + HTS와 동일 피드 가능성 높음
- **R-14 (가칭) 재설계 후보**

---

## 4. 로그 교차 비교 — 추가 확정 사실

### 팩트 A: `_ratio_updater` 자체는 정상 작동 (LIG넥스원 케이스)
```
00:21:18  [LIG넥스원] 비중 변경: 10.2% → 9.8% (Single)
00:46:32  [LIG넥스원] 비중 변경: 9.6% → 10.7% (Double)
01:02:07  [LIG넥스원] 비중 변경: 10.1% → 9.8% (Single)
```
Watcher 가 생성된 종목은 Double/Single 전이 정상 추적. 문제는 "Watcher 생성 진입 단계".

### 팩트 B: HTTP 500 광범위 발생 — 조회성 TR 한정
- 2061줄 중 거의 초당 1건 HTTP 500 재시도
- `FHKST01010100` (현재가), `FHPPG04650101` (프로그램매매) 양쪽
- **주문 TR (`TTTC0012U`) 은 500 없음** → 매매 주문 경로는 영향 없음
- rate limit 준수 중 (초당 16회, 0.05초 간격) → KIS 인프라 측 이슈 추정
- 재시도 로직으로 최종 성공, 매매 로직 기능상 영향 없음
- 관찰 대기 → E3 우선순위 재평가

### 팩트 C: VI-Observer 구독 정상 (Session 1 의심 #14 해소)
- main.py L405 `_on_realtime_price()` → `_check_vi_observer()` 호출 확인
- 5필드 (`new_mkop_cls_code`, `trht_yn`, `hour_cls_code`, `mrkt_trtm_cls_code`, `vi_stnd_prc`) 파싱 정상
- Stage 1 구현 완료 상태. 오늘 로그에 VI 알림 없음 = 당일 VI 미발동 (가능성 A 확정)

---

## 5. 통합 이슈 정리

### 섹션 A — 해결 완료 (지난주 대기 → 오늘 검증)

| # | 항목 | 검증 근거 |
|---|------|----------|
| 1 | R15-007 초기 예수금 조회 | `buyable_cash=1,004,915원` 로그 |
| 2 | R15-005 체결통보 WS + AES 키 | `iv_len=16 key_len=32 SUBSCRIBE SUCCESS` |
| 3 | 선물 WS 구독 (H0IFCNT0) | `선물 실시간 구독: 101S3000` |
| 4 | 종목 실시간 시세 WS | `[LG에너지솔루션] 09:50 이후 신고가 432,000원` |
| 5 | R-13 `_ratio_updater` 1초 폴링 | LIG넥스원 Double/Single 전이 3회 |
| 6 | Coordinator 8-state 전이 | 두산 WATCHING→TRIGGERED→SKIPPED |
| 7 | VI-Observer Stage 1 (Session 1 의심 #14) | 코드+main.py L405 검증 완료, 가능성 A 확정 |

### 섹션 B — PENDING (관찰/수동 대기)

| # | 항목 | 조건 |
|---|------|------|
| 8 | R15-007 청산 후 예수금 갱신 | 다음 LIVE 첫 체결 |
| 9 | Phase 2 B Position invariant | 다음 체결 |
| 10 | W-SAFETY-1 F2′ | 다음 체결 |
| 11 | W-SAFETY-1 H2 (11:20 force_liquidate) | 정상 도달일 |
| 12 | 네트워크 장애 방어 3종 | 장애 자연 발생 |
| 13 | R15-007 MTS 일치 검증 | Terry 수동 (MTS 주문가능원화 스샷) |
| 14 | W-SAFETY-1 F1 SA-5e holdings | Terry 수동 (MTS 보유종목 스샷) |

### 섹션 C — 신규 이슈 (Session 2 발견)

#### 🔴 P0 — 내일 아침 전 필수

**ISSUE-LIVE-01: 서버 TZ 전환 후속 조치 (cron)**
- 원인: Terry 오늘 아침 서버 TZ UTC → KST 전환
- 매매 로직 영향: 없음 확정
- 필수 조치: Terry 수동 `crontab -l` 확인 → `CRON_TZ=Asia/Seoul` 또는 시간 재작성
- 검증: 내일 06:35 KST 정각 cron 트리거 여부
- 연관: 로그 파일 rotation, journalctl 타임스탬프 변화 주의

#### 🟠 P1 — 월요일 장 시작 전 권고

**ISSUE-LIVE-02: KIS `program-trade-by-stock` 종목별 0 반환 버그**
- 증상: 우리기술(KOSDAQ), 에코프로머티(KOSPI), 주성엔지(KOSDAQ) 3종목 0 반환
- 원인: 가설 D (종목별 집계 누락) 가장 유력
- 영향: 스크리닝 탈락 → Watcher 미생성 → 매매 기회 체계적 누락 편향
- 권고: M-DIAG-PROG v2 (§6 초안 참고)
- 장기: WebSocket H0UNPGM0 전환 검토 (R-14 가칭)

**ISSUE-LIVE-03: KIS 조회성 TR HTTP 500 광범위**
- 증상: 초당 1건 500 재시도 (FHKST01010100, FHPPG04650101)
- 구분: **주문 TR은 500 없음** → 매매 영향 없음
- 원인: KIS 인프라 측 추정
- 관찰: LIVE 1주 이상 누적 후 E3 우선순위 재평가

#### 🟡 P2 — 중장기 재검토

**ISSUE-LIVE-04: 스크리닝 타이밍 단일 스냅샷 의존성 (설계 맹점)**
- 증상: 로보티즈 09:49 비중 8.98% → 60~90초 후 12.44% 급등. `is_final=True` 잠금으로 복원 불가
- LIVE-02 와 차이: LIVE-02 는 API 외부 원인, LIVE-04 는 경계값 (10%) 설계 원인
- 선택지: (a) 현 설계 유지 (b) 준-Double 승격 (c) 지속 모니터링 전환
- 판단 시점: LIVE 1~2주 운영 후

**ISSUE-LIVE-05: 10:53 KST SIGTERM 조기 종료 원인 불명**
- 환경 백로그 E4 동일
- 추적: Terry sync_logs.bat 재실행 → journal_autotrade_today.log / service_restart_history.log / ops_snapshot.txt (dmesg)
- 주의: TZ 변경으로 journal 타임스탬프 해석 혼선 (LIVE-01 연관)

**ISSUE-LIVE-06: run.py 이중 대시보드 포트 충돌 가능성**
- 영향: systemd 경로 (운영) 무영향, `python run.py` 로컬 실행 시 충돌
- 옵션: A (run.py 스레드 제거, 권장) / B (`--skip-dashboard` 플래그) / C (현행)

#### ℹ️ 문서 정정

**ISSUE-DOC-01: Session 1 HANDOFF 주성엔지 결론 오류**
- 파일: `docs/HANDOFF_2026-04-20_LIVE_Day1_analysis.md`
- 수정 필요 섹션: 2-2 주성엔지니어링 탈락 사유
- 기존 → 새 버전 연결: 본 Session 2 HANDOFF §1 참조

#### ⚪ 시스템 이슈 아님

**ISSUE-ACC-01: 예수금 부족 (계좌 관리)**
- 예수금 1,004,915원 / 두산 1차 1,429,000원
- R15-007 정상, Terry 계좌 입금 판단

---

## 6. M-DIAG-PROG v2 미션 초안

> **주의**: 초안이며 수석님 승인 대기. 화이트리스트 엄수.

### 목적
ISSUE-LIVE-02 가설 D (종목별 집계 누락) 를 로그로 확정 검증.

### 범위 (화이트리스트)
**파일**: `src/kis_api/kis.py` 의 `get_program_trade()` 단일 함수만

### 변경 내용 (매매 로직 수정 0, 로깅만)
1. `self._prog_trade_logged` 단일 플래그 제거 — 매 호출 덤프
2. 로그 추가:
   - output 배열 길이
   - output[0] 의 `bsop_hour` 와 `whol_smtn_ntby_tr_pbmn`
   - output 빈 배열 시 **별도 WARNING** (현재 조용히 0 반환)
   - 배열 길이 > 1 일 경우 output[-1] 의 `bsop_hour` 도 기록 (순서 재확인 안전망)
3. 로그 레벨: DEBUG (기존 일관성 유지)

### 검증
- pytest: 응답 파싱 모킹 테스트 1개 추가
  - 빈 output → WARNING 로그 발생 확인
  - 정상 output → DEBUG 덤프 확인
- import/grep 검증 후 로컬 py_compile
- 운영 배포는 Terry 수동 (§7 표준 금지 조항)

### 월요일 장중 실측
- Terry 수동 타겟에 **우리기술(032820) 포함** (재현성 높은 "지속 0" 케이스)
- 장중 로그 수집 후 Session 3 분석

### 롤백
- git revert 1 commit

---

## 7. 다음 세션 첫 액션 권장 순서

1. **이 문서 + CLAUDE.md + Session 1 HANDOFF 읽기**
2. **Terry 상태 확인**:
   - crontab 확인했는지 (ISSUE-LIVE-01)
   - sync_logs.bat 재실행했는지 (ISSUE-LIVE-05)
   - 계좌 입금했는지 (ISSUE-ACC-01)
   - MTS 스크린샷 2컷 (항목 #13, #14)
3. **작업 범위 제안 후보** (Terry 선택):
   - (a) M-DIAG-PROG v2 명령서 상세 작성
   - (b) Session 1 HANDOFF §2-2 수정 (ISSUE-DOC-01)
   - (c) ops 로그 분석 (SIGTERM 원인, LIVE-05)
   - (d) dashboard/app.py / notifier.py TZ 영향 grep
   - (e) R-14 (H0UNPGM0 전환) 사전 설계 논의
4. **선택 후 진행**

---

## 8. 참조 — 결정적 로그 발췌 (재사용 가능)

### KIS API output[0] 구조 (Session 2 결정적 증거)
```
2026-04-19 22:13:58 (= 2026-04-20 07:13:58 KST) 서비스 시작
2026-04-20 00:20:38.946 (= 09:20:38 KST) 첫 스크리닝
  DEBUG 프로그램매매 output[0]: {
    'bsop_hour': '092038',
    'stck_prpr': '86200',
    'whol_smtn_ntby_tr_pbmn': '9033916250',
    ...
  }
  → 한화비전 비중 12.63% 일치 확인
```

### 0 반환 종목 교차 비교
```
우리기술(032820):
  09:20:39 프로그램순매수=0 비중=0.00% 미달
  09:34:28 프로그램순매수=0 비중=0.00% 미달
  09:47:59 프로그램순매수=0 비중=0.00% 미달
  09:49:05 프로그램순매수=0 비중=0.00% 미달

에코프로머티(450080):
  09:34:30 프로그램순매수=0 비중=0.00% 미달
  09:48:01 프로그램순매수=0 비중=0.00% 미달

주성엔지니어링(036930):
  09:47:58 거래대금=929억  (inquire-price 정상)
  09:48:02 프로그램순매수=0 비중=0.00% 미달  (HTS 실측: 9,874백만 = 10.63%)
  09:49:04 거래대금=929억
  09:49:08 프로그램순매수=0 비중=0.00% 미달  (HTS 실측: 동일)
```

### LIG넥스원 `_ratio_updater` 정상 작동
```
00:21:18 (= 09:21 KST) [LIG넥스원] 비중 변경: 10.2% → 9.8% (Single)
00:46:32 (= 09:46 KST) [LIG넥스원] 비중 변경: 9.6% → 10.7% (Double)
01:02:07 (= 10:02 KST) [LIG넥스원] 비중 변경: 10.1% → 9.8% (Single)
```

### SIGTERM
```
2026-04-20 01:53:30.011 (= 10:53 KST)
  SIGTERM 수신 → graceful shutdown 시작
  → 2분 뒤 AUTOTRADE 종료
```

---

## 9. 세션 간 TZ 주의사항 (Session 3+ 참고)

- **오늘 (2026-04-20) 로그 파일** `autotrade_2026-04-20.log` 내부 타임스탬프:
  - 시작 부분 (22:13 ~ 01:53): **UTC** (서버 UTC 시절)
  - 이후 부분 (Terry TZ 수정 이후): **KST** 일 가능성 (미확인)
- 변환: **UTC + 9h = KST**
- 내일 이후 로그: KST 기준 (crontab 정정 가정)

---

**문서 끝 — Session 2, 2026-04-20**

**다음 세션 시작 지시 예시**:
> "docs/HANDOFF_2026-04-20_session2_api_bug_investigation.md 읽고 컨텍스트 이어가줘. Terry가 crontab 확인 완료/미완료, MTS 스크린샷 [있음/없음]. 다음 작업으로 [M-DIAG-PROG v2 명령서 작성 / Session 1 HANDOFF 수정 / ops 로그 분석 / 기타] 진행하자."
