# HANDOFF: AUTOTRADE LIVE Day 1 로그 분석 인계 (2026-04-20)

> **목적**: 신규 Claude 세션이 LIVE 첫 실행 로그 분석 이어가기 위한 self-contained 인폼 문서
> **작성**: 이전 세션 종료 시점 (2026-04-20)
> **선행 필독**: `C:\Users\terryn\AUTOTRADE\CLAUDE.md` (vLive 2.1)

---

## 1. 이전 세션 완료 작업

### 1-1. Oracle → GCP 인프라 잔재 완전 제거 (4개 파일)

| 파일 | 변경 |
|------|------|
| `CLAUDE.md` | §3 외부 인프라: Oracle Cloud Seoul → GCP Seoul (34.47.69.63, asia-northeast3-a). vLive 2.1 버전 이력 추가 |
| `docs/M-LOG-1_mission_brief.md` | 운영 서버 정보: Oracle IP → GCP IP + SSH 키 경로 추가 |
| `DEPLOY_R14_BUGFIX.md` | Step 2 배포 섹션: Oracle Cloud Seoul → GCP Seoul + 구체적 ssh 명령 |
| `.env.example` | `DASHBOARD_PORT` 8501 → 8503, `DASHBOARD_URL` 추가 (로컬 sync_trades.py 전용) |

확인: `logs/sync_logs.bat`, `logs/fetch_archive.bat`, `scripts/*.sh`, `scripts/*.py` 이미 GCP 정보 반영됨.

### 1-2. 코드 정리 3건

| 파일 | 변경 |
|------|------|
| `run.py` | `--port` 기본값 8501 → 8503 + help 메시지 구체화 (Cloudflare Named Tunnel 연결) |
| `README.md` | 전면 재작성 (vLive 2.1, LIVE 전용, DRY_RUN/paper 제거, W-SAFETY-1 안전장치 + 운영 로그 + 대시보드 섹션) |
| `.env.example` | `DASHBOARD_URL=` 주석 추가 (로컬 sync_trades.py 용 원격 API 엔드포인트, 예: https://app.hwrimlab.trade) |

---

## 2. 2026-04-20 LIVE Day 1 로그 분석 — 확정 사실

### 2-1. 로그 파일 위치 (로컬)

| 파일 | 규모 | 내용 |
|------|------|------|
| `logs/trades/2026-04-20/autotrade_2026-04-20.log` | 2061줄 | 전체 앱 로그 (INFO 이상) |
| `logs/trades/2026-04-20/screening_watcher_2026-04-20.log` | 130줄 | 스크리닝 + Watcher 관련만 필터링 |

**중요**: 로그 타임스탬프는 **UTC** 기준 (GCP 서버). **KST = UTC + 9h**.
- 00:20 UTC = 09:20 KST
- 00:47 UTC = 09:47 KST
- 00:49 UTC = 09:49 KST (정규 스크리닝)

### 2-2. 주성엔지니어링 (036930) 탈락 사유 — **확정**

**사유**: **프로그램순매수비중 0.00%** 로 5% 필터에서 탈락 (거래대금 아님)

**로그 근거** (09:47/09:49 KST 양쪽 동일):
```
주성엔지니어링(036930) KOSDAQ 현재가=79,000 등락=+12.22% 거래대금=929억  ← 기본 필터 통과
주성엔지니어링(036930) 프로그램순매수=0 비중=0.00% 미달                   ← 프로그램 필터 탈락
```

**해석**: +12.22% 급등했지만 프로그램 순매수 비중 0% → 외인/기관 지지 없는 급등. AUTOTRADE 매매 철학("외인/기관 지지 + 개인 매수 유입")에 부합 안 함. **필터 정상 작동**, 재고 불필요.

### 2-3. 로보티즈 (108490) 탈락 사유 — **확정**

**사유**: **R-11 KOSDAQ Single 제외 규칙** (비중 <10%)

**로그 근거**:

| 시각 (KST) | 거래대금 | 프로그램 비중 | 결과 |
|-----------|---------|-------------|------|
| 09:34 | 569억 | 7.72% | 프로그램 통과, **KOSDAQ Single 제외** |
| 09:48 | 631억 | 7.70% | 동일 |
| **09:49 (정규)** | **642억** | **8.98%** | 프로그램 통과, **KOSDAQ Single 제외** (<10%) |

**해석**: 거래대금 필터 통과, 프로그램 5% 필터도 통과. 하지만 R-11 규칙상 KOSDAQ 은 비중 ≥10% (Double)만 매매 대상.

### 2-4. Terry 의 초기 가정 수정

**Terry 가정**: "거래대금 500억 기준으로 두 종목 못 잡는거야?"
**실제**: 거래대금은 둘 다 통과. 진짜 탈락 사유는 전혀 다른 필터 (프로그램 비중 / KOSDAQ Single).

---

## 3. 추가 개발 판단 사항 (진단)

### 3-1. 확정된 진단 (즉시 반영 필요 없음, 관찰 대기)

**A. 예수금 부족 — Terry 계좌 잔액 문제 (시스템 이슈 아님)**
- 시작 시 로그: `buyable_cash (nrcvb_buy_amt) = 1,004,915원`
- 두산 매수가 1,429,000원 → 1주도 못 사는 상황
- 10:29 KST 로그: `KIS API 에러 [TTTC0012U]: 주문가능금액을 초과 했습니다`
- R15-007 정상 작동 (KIS 가 반환한 값 정확히 사용). **계좌 입금 필요**

**B. KIS API HTTP 500 오류 대량 발생**
- 2061줄 중 대부분이 `HTTP 500 [FHKST01010100]` 또는 `[FHPPG04650101]` 재시도
- KIS 서버 불안정성 (Terry 코드 문제 아님)
- 매매에 영향 없었음 (재시도 로직 작동)
- **관찰 포인트**: LIVE 1주 이상 관찰 후 E3 (KIS API 재시도 로직 강화) 우선순위 재평가

**C. SIGTERM 10:53:30 KST 수신 → 장 마감 전 조기 종료**
- 원인 미확인 (journal 로그 추가 조사 필요)
- E4 (systemd SIGTERM 원인 식별) 환경 백로그와 연관
- 2026-04-17 DRY_RUN 에도 유사 이슈 있었음 (11:25 에 autotrade.service 재시작)

### 3-2. 논의 가치 있는 설계 포인트

**A. R-11 KOSDAQ Single 제외 룰 재검토**
- 로보티즈 비중 8.98% → 10% 미달로 제외됨
- "비중 7~10% 구간" 을 준 Double 로 승격할지 논의 가치
- 매매 명세 동결 상태 (R-N redesign 필요)
- **판단**: LIVE 1~2주 관찰 후 재검토 (W-SAFETY-1 Stage 2 일정과 동일)

**B. run.py 구조적 문제 — 해결 옵션 3가지 (Terry 판단 필요)**

이전 세션에서 run.py 의 `--port` 기본값을 8501 → 8503 으로 맞췄지만 근본 문제 발견:

```
python run.py 실행 시:
├── Thread 1: run.py 의 run_dashboard (port=args.port, 이제 8503)
└── main.py: _start_dashboard_server (port=settings.dashboard_port=8503)
    → 같은 포트 2개 시도 → "Address already in use" 충돌 가능
```

**현재 운영 영향 없음**: systemd 가 `python -m src.main` 직접 실행 → run.py 우회
**로컬 실행 시 문제**: `python run.py` 시 충돌

옵션:
- **A**: run.py 별도 스레드 dashboard 제거 (main.py 의 in-loop 만 사용) — **권장**
- **B**: `--skip-dashboard` 플래그 추가 (기본 제거, 명시적 활성화 시만)
- **C**: 현재 유지 (충돌 에러로 감지)

---

## 4. 로그 분석 이어서 진행할 내용

### 4-1. 아직 확인 못 한 타임라인 이벤트

이전 세션에서 **tail 900줄** + **head 1300줄** 만 확인. 1300~1160줄 구간 미확인.

확인 필요한 이벤트:

| 시각 (KST) | 이벤트 | 추가 확인 필요 |
|-----------|--------|--------------|
| 10:19:47 | 두산 TRIGGER 발동 (고가 1,457,000 → 현재 1,441,500) | 트리거 후 추이 |
| 10:29:45 | 두산 매수 발주 실패 (예수금 초과) | 에러 반복 횟수 |
| 10:31:31 | LG에너지솔루션 09:50 이후 신고가 432,000원 | 트리거 여부 |
| 10:39:48 | 두산 20분 미체결 → SKIPPED | 정상 작동 확인 |
| 10:53:30 | SIGTERM 수신 → graceful shutdown | **원인 불명** |

### 4-2. 추가로 조사할 사항

1. **SIGTERM 10:53 원인 식별**
   - `logs/trades/2026-04-20/ops_*/journal_autotrade_today.log` 에서 sudo 로그 확인
   - `service_restart_history.log` 에서 `SIGTERM` / `Stopped` 이벤트 시각 확인
   - 메모리 OOM 여부 (`ops_snapshot.txt` 의 dmesg 섹션)

2. **HTTP 500 빈도 정량화**
   - 2061줄 중 HTTP 500 비중 (%)
   - 특정 엔드포인트 집중 여부 (FHKST01010100 = 가격조회, FHPPG04650101 = 프로그램매매)
   - 시간대별 분포 (개장 직후 vs 중반 vs 종료 직전)

3. **두산 매수 실패 후 시스템 대응**
   - 예수금 부족 에러 발생 → Watcher 상태 전이 추적
   - 다른 active 후보 전환 여부
   - T1/T2/T3 연쇄 작동 여부

4. **R-12 리스크 관리 작동 여부**
   - 일일 손실 한도 도달 여부 (체결 자체 없었으므로 N/A 예상)
   - 지수 급락 감지 로그
   - 손절 횟수 카운트

5. **W-SAFETY-1 안전장치 작동 검증**
   - F1 SA-5e: 시작 시 holdings 체크 로그 (`[R15-007]` 로그 이후)
   - F2′: 체결통보 unmatched 없었음 (체결 자체 없음)
   - H2: 11:20 force_liquidate 발화 여부 (10:53 SIGTERM 으로 도달 안 함)
   - VI-Observer: H0UNCNT0 필드 변화 로그 유무

### 4-3. 사용할 로그 파일

```
로컬 경로:
C:\Users\terryn\AUTOTRADE\logs\trades\2026-04-20\
├── autotrade_2026-04-20.log        (2061줄, 전체)
├── screening_watcher_2026-04-20.log (130줄, 스크리닝+Watcher)
└── ops_HHMMSS\                     (sync_logs.bat 재실행 시 생성)
    ├── journal_autotrade_today.log
    ├── journal_autotrade_boot.log
    ├── ops_snapshot.txt
    └── service_restart_history.log
```

**권장**: 신규 채팅 시작 시 Terry 가 sync_logs.bat 재실행 → ops_*/ 폴더의 systemd journal 로그 확보. 이게 있어야 SIGTERM 원인 추적 가능.

---

## 5. 프로젝트 컨텍스트 핵심 요약

### 5-1. AUTOTRADE 현재 상태

- **버전**: vLive 2.1 (R16 Phase 1 + Phase 2 B + W-SAFETY-1)
- **운영 모드**: LIVE 전용 (2026-04-17 전환)
- **운영 환경**: GCP Seoul (34.47.69.63, asia-northeast3-a, Ubuntu 22.04 LTS)
- **진입점**: systemd `autotrade.service` → `python -m src.main`
- **대시보드**: app.hwrimlab.trade (Cloudflare Named Tunnel, port 8503)

### 5-2. 매매 명세 (동결, R-N redesign 없이 변경 불가)

- 시간: 09:50 스크리닝 / 10:00~10:55 진입 윈도우 / 11:20 강제 청산
- KOSPI Double(≥10%): -1.9/-2.4/-3.0, Single: -2.5/-3.5/-4.0
- KOSDAQ Double(≥10%): -2.9/-3.9/-4.4, Single: **매매 제외**
- R-12: 지수 -1.5% or 손절 2회 → 매매 중단
- 일일 손실 한도 -3.0%

### 5-3. 협업 규칙 (§5.6 8원칙)

1. 사실 base 우선 (추측 금지, grep/cat 으로 실제 확인)
2. 진단 후 작업 (검증 → 연관성 → 확정 → 작업 → 검증 → 보고)
3. 멈춤 조건 명시 (예상 외 결과 즉시 멈춤)
4. 화이트리스트 원칙 (명령서 명시 파일만 수정)
5. 원자 작업
6. 검증 필수 (import/grep/단위 테스트)
7. 추가 발견 기록 ([발견] 섹션)
8. 통합 관점

### 5-4. Claude 도구 제약 (신규 세션 주의)

- **Filesystem 도구만 사용 가능** (bash/shell 실행 권한 없음)
- **sync_logs.bat 실행 불가** (Terry 가 로컬에서 실행해야 함)
- **읽기/쓰기/편집만 가능**: `filesystem:read_text_file`, `filesystem:edit_file`, `filesystem:write_file`

---

## 6. 이전 세션 추적 참조

### 6-1. 이전 transcript
- `/mnt/transcripts/2026-04-17-12-24-14-autotrade-r16-phase1-refactor.txt`
- `/mnt/transcripts/2026-04-17-13-47-51-autotrade-r16-phase1-2-implementation.txt`
- `/mnt/transcripts/2026-04-20-01-24-39-autotrade-r16-live-prep.txt` (W-SAFETY-1 세션)
- `/mnt/transcripts/2026-04-20-01-59-55-autotrade-w-safety-1-live.txt` (이번 세션)

### 6-2. 주요 이슈 추적

| ID | 내용 | 상태 |
|----|------|------|
| F1 | 재시작 시 포지션 복구 | ✅ W-SAFETY-1 Stage 1 완료 |
| F2′ | timeout 오진 체결 누락 | ✅ critical 알림 구현 |
| H2 | force_liquidate 실패 | ✅ 알림 구현 |
| VI | VI/서킷브레이커 | ✅ Stage 1 관찰자 모드 |
| **10:53 SIGTERM** | 원인 불명 조기 종료 | 🔍 **신규 세션 우선순위** |
| **R15-007 검증** | MTS '주문가능원화' 일치 확인 | ⏸ Terry 수동 확인 필요 |
| **E4 systemd** | SIGTERM 원인 식별 | 🔍 10:53 이슈와 연관 |

---

## 7. 신규 세션 시작 시 권장 첫 액션

1. **이 문서 (HANDOFF_2026-04-20_LIVE_Day1_analysis.md) 읽기**
2. **CLAUDE.md 확인** (vLive 2.1 정보)
3. **Terry 에게 확인**:
   - sync_logs.bat 재실행 완료 여부 (ops_*/ 폴더 생성)
   - 계좌 예수금 충전 여부
   - 어느 이슈부터 진행할지 우선순위
4. **작업 범위 제안** → Terry 승인 → 진행

---

## 8. 이번 세션에서 확정된 구체적 로그 발췌 (재사용 가능)

### 주성엔지니어링 탈락 (09:47 KST, 00:47 UTC)
```
2026-04-20 00:47:58.062 | INFO  | src.core.screener:run_manual:109 |   주성엔지니어링(036930) KOSDAQ 현재가=79,000 등락=+12.22% 거래대금=929억
2026-04-20 00:48:02.123 | INFO  | src.core.screener:run_manual:174 |   주성엔지니어링(036930) 프로그램순매수=0 비중=0.00% 미달
```

### 로보티즈 탈락 (09:49 KST, 00:49 UTC)
```
2026-04-20 00:49:03.506 | INFO  | src.core.screener:run_manual:109 |   로보티즈(108490) KOSDAQ 현재가=285,000 등락=+4.40% 거래대금=642억
2026-04-20 00:49:07.989 | INFO  | src.core.screener:run_manual:174 |   로보티즈(108490) 프로그램순매수=5,762,916,500 비중=8.98% 통과
2026-04-20 00:49:08.106 | INFO  | src.core.screener:run_manual:197 |   → 제외(KOSDAQ Single): 로보티즈(108490) 비중=8.98% < 10.0%
```

### 초기 예수금 (07:13 KST 서비스 시작 시, 21:13 UTC 전일)
```
2026-04-19 22:13:58.291 | INFO  | __main__:run:219 | [R15-007] 초기 주문가능금액 조회:
  buyable_cash (nrcvb_buy_amt) = 1,004,915원  ← 주 사용 필드
  ord_psbl_cash              = 1,009,951원
  ruse_psbl_amt              = 0원
```

### 두산 매수 실패 (10:29 KST, 01:29 UTC)
```
2026-04-20 01:29:45.288 | INFO  | src.core.watcher:_execute_buy:852 | [Coordinator] 매수 발주: 두산 1차 1,429,000 / 2차 1,422,000
2026-04-20 01:29:45.373 | ERROR | src.kis_api.kis:_request:354 | KIS API 에러 [TTTC0012U]: 주문가능금액을 초과 했습니다
2026-04-20 01:29:45.774 | ERROR | src.kis_api.kis:_request:354 | KIS API 에러 [TTTC0012U]: 초당 거래건수를 초과하였습니다.
```

### 두산 SKIPPED (10:39 KST, 01:39 UTC)
```
2026-04-20 01:39:48.758 | INFO  | src.core.watcher:_handle_triggered:383 | [두산] 트리거 후 20분 미체결 → 모멘텀 소멸 SKIPPED
```

### SIGTERM 조기 종료 (10:53 KST, 01:53 UTC)
```
2026-04-20 01:53:30.011 | INFO  | __main__:_sigterm_handler:1006 | SIGTERM 수신 → graceful shutdown 시작
2026-04-20 01:53:30.831 | INFO  | src.kis_api.kis:disconnect:221 | KIS API 연결 해제
2026-04-20 01:53:30.831 | INFO  | __main__:run:292 | AUTOTRADE 종료
```

---

**문서 끝. 신규 채팅 시작 시 이 문서 경로만 알려주면 바로 이어갈 수 있음.**

**문서 경로**: `C:\Users\terryn\AUTOTRADE\docs\HANDOFF_2026-04-20_LIVE_Day1_analysis.md`
