# M-LOG-1: AUTOTRADE 운영 로그 수집 시스템 구축 미션

> **신규 채팅 시작 시 이 문서를 먼저 읽고 작업 진행할 것.**
>
> **선행 조건**: CLAUDE.md (vLive 2 이후 버전) 를 먼저 읽고 프로젝트 컨텍스트 파악.

---

## 0. 미션 배경

### 왜 필요한가
2026-04-17 DRY_RUN 마지막 운영 로그에서 **11:25 에 systemd 가 재시작된 흔적** 발견:
```
2026-04-17 11:25:00 | [Coordinator] 10:55 매수 마감: 0개 watcher SKIPPED
2026-04-17 11:25:00 | 매수 마감 (10:55) — Coordinator 통지 완료
2026-04-17 11:25:00 | 강제 청산 (11:20) — Coordinator 통지 완료
```

`_wait_until()` 은 "target_time 이 이미 지났으면 즉시 return" 구조 → 11:25 재시작 시점에 이미 지난 10:55, 11:20 스케줄이 즉시 발화된 것.

**왜 11:25 에 재시작했는지는 추적 불가능** — 현재 sync_logs.bat 에 systemd journal 수집 없음.

월요일부터 LIVE 시작 → 이런 이슈 추적 불가하면 **금전 손실 위험** (재시작 시점에 포지션 보유 중이었다면 F1 이슈 직격).

### LIVE 운영 1~2주 안정 후 Phase 3 에서 처리 예정인 미해결 이슈
- **F1**: 재시작 시 미청산 포지션 복구 부재 (SA-5e 로직 필요)
- **F2**: REST timeout 시 주문 중복 오진 가능성

**이 두 이슈를 추적하려면 이번 미션의 운영 로그 수집이 선행되어야 함.**

---

## 1. 현재 배치파일 구조 (신규 작업 전 baseline)

### 파일 위치
`C:\Users\terryn\AUTOTRADE\logs\sync_logs.bat`

### 6단계 구조
| Step | 작업 | 출력 |
|------|------|------|
| 1 | trades.db 복사 | `data\trades.db` |
| 2 | App 로그 복사 (autotrade + trades) | `logs\trades\YYYY-MM-DD\*.log` |
| 3 | systemd journal 수집 (today + boot) | `logs\trades\YYYY-MM-DD\ops_HHMMSS\journal_*.log` |
| 4 | 시스템 진단 스냅샷 (status + 리소스 + dmesg) | `logs\trades\YYYY-MM-DD\ops_HHMMSS\ops_snapshot.txt` |
| 5 | 서비스 재시작 이력 (24h grep) | `logs\trades\YYYY-MM-DD\ops_HHMMSS\service_restart_history.log` |
| 6 | trades.db → JSON 추출 | `logs\trades\YYYY-MM-DD\NNN_종목_REASON.json` |

### 출력 폴더 구조
```
C:\Users\terryn\AUTOTRADE\logs\
├── sync_logs.bat                       ← 즉시 진단 배치 본체
└── trades\
    └── 2026-04-17\                     ← 날짜별 폴더 (자동 생성)
        ├── autotrade_2026-04-17.log    ← Step 2 (앱 로그)
        ├── trades_2026-04-17.log       ← Step 2 (매매 이벤트)
        ├── 001_퍼스텍_TARGET.json      ← Step 6 (DB 추출)
        ├── daily_summary.json          ← Step 6 일별 요약
        └── ops_141530\                 ← Step 3-5 (실행 시각별 하위 폴더)
            ├── journal_autotrade_today.log
            ├── journal_autotrade_boot.log
            ├── ops_snapshot.txt
            └── service_restart_history.log
```

`ops_HHMMSS` 로 실행 시각별 하위 폴더를 만드는 이유: 하루에 여러 번 실행해도 덮어쓰지 않음.

### 운영 서버 정보
- **호스트**: `ubuntu@34.47.69.63` (GCP Seoul, asia-northeast3-a)
- **SSH 키**: `~/.ssh/autotrade_gcp`
- **프로젝트 경로**: `/home/ubuntu/AUTOTRADE`
- **systemd 서비스명**: `autotrade` (확인 필요)

### 사전 조건
1. SSH key 인증 완료 (비밀번호 없이 ssh/scp 가능)
2. ubuntu 사용자 sudo 권한 (journalctl, dmesg)
3. systemd 서비스명 = `autotrade` (다르면 배치 일괄 교체 필요)

---

## 2. 문제 유형별 로그 명세 (트러블슈팅 매트릭스)

| 증상 | 첫 번째로 볼 파일 | 확인 grep 키워드 |
|------|------------------|-----------------|
| **서비스 갑자기 죽음** | `service_restart_history.log` | `SIGTERM` / `SIGKILL` / `Main process exited` |
| **재시작 이유 불명** | `journal_autotrade_boot.log` | 직전 부팅 이후 로그 전체 |
| **WebSocket 자꾸 끊김** | `autotrade_YYYY-MM-DD.log` | `WebSocket 연결 끊김` |
| **매매 안 일어남** | `trades_YYYY-MM-DD.log` + `autotrade*.log` | `screener` / `Watcher` / `state` |
| **포지션 불일치 (Phase 2 B)** | `autotrade*.log` + `trades.db` | `[Phase 2 B] Position 불일치` |
| **OOM 킬당함** | `ops_snapshot.txt` | `dmesg` 섹션 → `Out of memory: Killed process` |
| **토큰 만료** | `ops_snapshot.txt` | `token cache metadata` 섹션 → 파일 mtime |
| **API 호출 실패** | `autotrade*.log` | `KIS API 에러` |
| **체결통보 안 옴** | `journal_autotrade_today.log` | `[R15-005]` |
| **R15-007 예수금 이상** | `autotrade*.log` | `[R15-007]` (buyable_cash 갱신 추적) |
| **외부 개입 (HTS 정정/취소)** | `autotrade*.log` | `외부 개입 감지` / `RCTF_CLS=` |
| **주문 거부** | `autotrade*.log` | `RFUS_YN=1` / `on_live_rejected` |
| **이상 재시작 (오늘 11:25 케이스)** | `service_restart_history.log` | `Stopped` 이벤트 시각 확인 |

---

## 3. 옵션 B 설계 결정 사항 (이번 미션의 핵심)

### 옵션 B 정의
**운영 서버 cronjob 으로 매일 자동 로그 압축 → 별도 디렉토리 보관 → 로컬에서 필요시 당겨받기**

### 결정 1: 기존 sync_logs.bat 와의 관계

**선택지 A**: 통합 (sync_logs.bat 가 압축 아카이브와 실시간 둘 다 처리)
**선택지 B**: 분리 (sync_logs.bat = 즉시 진단용, 새 스크립트 = 아카이브 다운로드용)

**채택: 선택지 B (분리)** — 이유:
- **용도 분리 명확**: 즉시 진단 vs 사후 분석
- **sync_logs.bat 는 SSH 라이브 호출** → 서버 부하 + 시간 소요. 매번 journalctl/dmesg 다 호출하면 30초+ 걸림
- **압축 아카이브는 미리 준비된 파일** → scp 한 번에 끝남 (수 초)
- **rollback 용이**: 한쪽 깨져도 다른 쪽 정상 동작
- **장 마감 후 자동 백업** + **문제 즉시 진단** 두 시나리오 모두 커버

### 결정 2: 새 스크립트 이름 + 역할 분리

| 스크립트 | 위치 | 용도 | 실행 시점 |
|---------|------|------|----------|
| **sync_logs.bat** (기존, 보존) | `C:\Users\terryn\AUTOTRADE\logs\` | 즉시 진단 — 라이브 SSH 로 systemd journal + 리소스 + DB 수집 | 문제 발생 시 수동 실행 |
| **fetch_archive.bat** (신규) | `C:\Users\terryn\AUTOTRADE\logs\` | 사후 분석 — 운영 서버 cronjob 이 매일 만든 압축 아카이브 다운로드 | 매일 또는 필요시 수동 |

### 결정 3: 운영 서버 cronjob 스펙

**위치**: `/home/ubuntu/AUTOTRADE/scripts/daily_archive.sh` (신규 작성)

**실행 시각**: 매일 15:35 (장 마감 15:30 + 5분 여유)

**아카이브 내용**:
```
/home/ubuntu/AUTOTRADE/archive/YYYY-MM-DD.tar.gz
├── autotrade_YYYY-MM-DD.log         ← loguru 앱 로그
├── trades_YYYY-MM-DD.log            ← 매매 이벤트
├── trades.db                        ← SQLite DB (해당일 시점)
├── journal_today.log                ← journalctl -u autotrade --since today
├── journal_boot.log                 ← journalctl -u autotrade -b
├── ops_snapshot.txt                 ← systemctl + uptime + free + df + ps + dmesg
├── service_restart_history.log      ← 24h 재시작 이벤트
└── manifest.json                    ← {date, file_sizes, sha256, autotrade_version, git_commit}
```

**보존 정책**:
- 운영 서버: 30일 (디스크 공간 고려, `find ... -mtime +30 -delete`)
- 로컬: 무제한 (Terry 가 직접 정리)

**cronjob 설정**:
```cron
35 15 * * 1-5 /home/ubuntu/AUTOTRADE/scripts/daily_archive.sh >> /home/ubuntu/AUTOTRADE/logs/cron_archive.log 2>&1
```
- `1-5` = 월~금 (한국 거래일 기준)
- 토/일/공휴일 미실행 (트래픽 절약)
- 한국 공휴일은 별도 처리 안 함 (실행되어도 빈 로그라 무해)

### 결정 4: 로컬 fetch_archive.bat 동작 명세

**기본 동작**: 어제 날짜의 아카이브 다운로드 (장 마감 후 다음날 출근 시 사용 가정)

**사용 패턴**:
```powershell
# 어제 아카이브 다운로드 (기본)
fetch_archive.bat

# 특정 날짜 지정
fetch_archive.bat 2026-04-21

# 최근 N일 일괄 (선택 구현)
fetch_archive.bat --last 7
```

**다운로드 후 처리**:
1. `logs\archive\YYYY-MM-DD.tar.gz` 저장
2. 자동 압축 해제 → `logs\archive\YYYY-MM-DD\`
3. `manifest.json` SHA256 검증
4. 검증 통과 시 OK 메시지

### 결정 5: 폴더 구조 분리

| 폴더 | 용도 | 생성 주체 |
|------|------|----------|
| `logs\trades\YYYY-MM-DD\` | 즉시 진단 결과 (sync_logs.bat 출력) | 기존 유지 |
| `logs\archive\YYYY-MM-DD\` | 일별 아카이브 (fetch_archive.bat 출력) | **신규** |

**중복 우려 없음**: 두 폴더 분리되어 있어 충돌 없음. 사후 분석 시 archive 우선, 실시간 진단 시 trades 우선.

---

## 4. 작업 범위 (신규 채팅에서 할 일)

### 4-1. 운영 서버 daily_archive.sh 작성
**파일**: `/home/ubuntu/AUTOTRADE/scripts/daily_archive.sh` (Terry 가 운영 서버에 수동 업로드)

**역할**: 매일 15:35 실행되어 당일 모든 로그를 한 파일로 압축

**필수 요건**:
- bash 스크립트, `set -euo pipefail` 안전 모드
- 기존 archive 가 있으면 overwrite (재실행 시 최신 상태 유지)
- 30일 이전 아카이브 자동 삭제
- 실패 시 stderr 로그 → cron_archive.log 에 기록되어 사후 추적 가능
- AUTOTRADE 매매 프로세스에 영향 0 (별도 프로세스로 동작)
- manifest.json 생성: `date`, `file_sizes` (각 파일 byte), `sha256` (각 파일), `git_commit` (현재 HEAD), `autotrade_version` (CLAUDE.md vLive 버전)

### 4-2. cronjob 등록 안내
운영 서버에서 Terry 가 직접 실행할 명령어 정리:
```bash
crontab -e
# 또는 한 번에:
(crontab -l 2>/dev/null; echo "35 15 * * 1-5 /home/ubuntu/AUTOTRADE/scripts/daily_archive.sh >> /home/ubuntu/AUTOTRADE/logs/cron_archive.log 2>&1") | crontab -
```

### 4-3. 로컬 fetch_archive.bat 작성
**파일**: `C:\Users\terryn\AUTOTRADE\logs\fetch_archive.bat`

**기능**:
- 인자 파싱 (날짜 지정 / 어제 기본값 / `--last N`)
- scp 다운로드 (`/home/ubuntu/AUTOTRADE/archive/YYYY-MM-DD.tar.gz`)
- tar 자동 해제 (Windows 10+ 기본 tar 명령 활용)
- manifest.json SHA256 검증 (Windows certutil 활용)
- 검증 결과 콘솔 출력

### 4-4. 운영 서버 디렉토리 사전 생성 안내
```bash
mkdir -p /home/ubuntu/AUTOTRADE/archive
mkdir -p /home/ubuntu/AUTOTRADE/scripts
```

### 4-5. CLAUDE.md 업데이트 (선택)
- 8 백로그 섹션에 "로그 수집 시스템 구축 완료" 항목 추가
- 6 운영 환경 섹션에 archive 디렉토리 + cronjob 추가

---

## 5. 검증 체크리스트 (작업 완료 시)

| # | 검증 항목 | 기대 결과 |
|---|-----------|----------|
| 1 | `daily_archive.sh` 수동 실행 | `archive/YYYY-MM-DD.tar.gz` 생성, 압축 해제 시 8개 파일 모두 존재 |
| 2 | `manifest.json` 형식 | JSON parse 가능, sha256 모두 매칭 |
| 3 | 30일 보존 정책 | 31일 전 더미 파일 생성 후 스크립트 실행 → 삭제 확인 |
| 4 | cronjob 등록 | `crontab -l` 에 표시 |
| 5 | 로컬 `fetch_archive.bat` 인자 없이 실행 | 어제 날짜 아카이브 다운로드 + 압축 해제 + 검증 통과 |
| 6 | 로컬 `fetch_archive.bat 2026-04-21` | 지정 날짜 아카이브 다운로드 |
| 7 | 기존 `sync_logs.bat` 영향 없음 | 동작 동일 (즉시 진단용 그대로) |
| 8 | AUTOTRADE 본체 영향 없음 | 매매 프로세스 정상 동작 |

---

## 6. 참고 파일 위치

### 로컬 (Windows)
| 항목 | 경로 |
|------|------|
| 프로젝트 루트 | `C:\Users\terryn\AUTOTRADE\` |
| **로그 폴더** | `C:\Users\terryn\AUTOTRADE\logs\` |
| **즉시 진단 배치 (보존)** | `C:\Users\terryn\AUTOTRADE\logs\sync_logs.bat` |
| **신규 아카이브 배치** | `C:\Users\terryn\AUTOTRADE\logs\fetch_archive.bat` (작성 예정) |
| 즉시 진단 출력 | `C:\Users\terryn\AUTOTRADE\logs\trades\YYYY-MM-DD\` |
| **아카이브 다운로드 폴더 (신규)** | `C:\Users\terryn\AUTOTRADE\logs\archive\YYYY-MM-DD\` |
| CLAUDE.md | `C:\Users\terryn\AUTOTRADE\CLAUDE.md` |
| trades.db | `C:\Users\terryn\AUTOTRADE\data\trades.db` |
| JSON 추출 스크립트 | `C:\Users\terryn\AUTOTRADE\scripts\extract_trades_local.py` |
| 미션 브리프 | `C:\Users\terryn\AUTOTRADE\docs\M-LOG-1_mission_brief.md` |

### 운영 서버 (Ubuntu)
| 항목 | 경로 |
|------|------|
| 프로젝트 루트 | `/home/ubuntu/AUTOTRADE/` |
| 앱 로그 | `/home/ubuntu/AUTOTRADE/logs/` |
| **아카이브 출력 (신규)** | `/home/ubuntu/AUTOTRADE/archive/` |
| **daily_archive.sh (신규)** | `/home/ubuntu/AUTOTRADE/scripts/daily_archive.sh` |
| **cron_archive.log (신규)** | `/home/ubuntu/AUTOTRADE/logs/cron_archive.log` |
| trades.db | `/home/ubuntu/AUTOTRADE/data/trades.db` |
| systemd 서비스 | `autotrade.service` (확인 필요) |

---

## 7. 협업 규칙 (CLAUDE.md §5.6 준수)

신규 채팅에서도 다음 원칙 엄수:

1. **사실 base 우선** — 추측 금지, grep/cat 으로 실제 파일 확인
2. **진단 후 작업** — 검증 → 관련 메서드/연관성 검토 → 확정 → 작업 → 검증 → 보고
3. **멈춤 조건 명시** — 예상 외 결과 시 즉시 멈춤
4. **화이트리스트** — 이 미션의 작업 대상은 다음 4개:
   - `/home/ubuntu/AUTOTRADE/scripts/daily_archive.sh` (신규, Terry 가 운영 서버에 업로드)
   - `C:\Users\terryn\AUTOTRADE\logs\fetch_archive.bat` (신규)
   - `C:\Users\terryn\AUTOTRADE\CLAUDE.md` (선택, 백로그 + 운영 환경 섹션 업데이트)
   - **sync_logs.bat 는 변경 금지** (즉시 진단 용도 보존)
5. **운영 서버 직접 접근 금지** — 모든 작업은 로컬에서 작성 후 Terry 가 수동 업로드/실행
6. **AUTOTRADE 본체 매매 로직 변경 금지** — 로그 수집은 별도 프로세스, 본체와 격리

---

## 8. 시작 시 첫 액션 (신규 채팅)

신규 채팅에서 첫 번째로 할 일:

1. CLAUDE.md 읽기 (vLive 2 이후 버전)
2. 이 미션 브리프 (M-LOG-1) 다시 읽기
3. 현재 `sync_logs.bat` 내용 확인 (`Filesystem:read_text_file`)
4. 현재 `logs\trades\` 디렉토리 구조 확인
5. Terry 에게 다음 사전 확인:
   - 운영 서버에 `archive/`, `scripts/` 디렉토리가 이미 있는가?
   - systemd 서비스명이 `autotrade` 가 맞는가? (`systemctl list-units --type=service | grep -i auto`)
   - `git rev-parse HEAD` 명령으로 git 커밋 해시 가져오기 가능한가?
6. 작업 순서 제시 + Terry 승인 받고 진행

---

## 9. 참고 — 오늘 (2026-04-17) 발견된 이상 패턴

`trades_2026-04-17.log` 마지막 6줄:
```
2026-04-17 10:55:00 | [Coordinator] 10:55 매수 마감: 3개 watcher SKIPPED
2026-04-17 10:55:00 | 매수 마감 (10:55) — Coordinator 통지 완료
2026-04-17 11:20:00 | 강제 청산 (11:20) — Coordinator 통지 완료
2026-04-17 11:25:00 | [Coordinator] 10:55 매수 마감: 0개 watcher SKIPPED
2026-04-17 11:25:00 | 매수 마감 (10:55) — Coordinator 통지 완료
2026-04-17 11:25:00 | 강제 청산 (11:20) — Coordinator 통지 완료
```

**해석**:
- 10:55, 11:20 정상 발화 (첫 번째 사이클)
- 11:20 ~ 11:25 사이에 systemd 가 autotrade.service 를 재시작
- 재시작 직후 `_wait_until` 이 이미 지난 시각 즉시 발화 → 11:25 에 두 이벤트 동시 발생

**원인 추적 가능 도구 (이번 미션 결과물)**:
- 어제 날짜의 archive → `service_restart_history.log` 에서 11:20 ~ 11:25 사이 `Stopped` / `Started` 이벤트 확인
- `journal_today.log` 에서 직전 종료 사유 확인

---

**작성일**: 2026-04-17 (DRY_RUN 마지막 운영일 / LIVE 시작 직전 주말)
**작성자**: Claude (Phase 1 + Phase 2 B 작업 세션)
**수신**: 신규 Claude 세션
**미션 ID**: M-LOG-1
