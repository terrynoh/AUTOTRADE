# Phase 진행상황

> 🟡 **동기화 영역 stub** — 현재 Phase / 매매 명세 / 운영 상태는 [`CLAUDE.md`](file:///C:/Users/terryn/AUTOTRADE/CLAUDE.md) §2/§7 단일 진실.
> 본 파일은 *Phase history 보존* 영역. 신규 Phase 시작/종결 시 history 만 추가.
>
> **W-16 정합**: 2026-04-10

---

## 현재 단계 (요약)

- **R-08 매매 명세 구현 완료** (2026-04-10)
- **운영 배포 완료** — Oracle Cloud, DRY_RUN 가동 중 (PID 60305)
- **4/13(월) 첫 거래일 DRY_RUN 검증 대기**

상세는 `CLAUDE.md` §2 (매매 명세) + §7 (운영 환경) + 부록 A (R-08 작업 요약) 참조.

---

## Phase history (W-16 이전 보존)

### Phase 0 — 백테스트 ⬜
- 코드 완료, 미실행
- 표본 1 거래일 / 2 매매 / 100% (2026-04-06) — 통계적 무의미
- 상세: [[백테스트_결과]]

### Phase 1 — KIS API 기반 구축 ✅
- 완료 (verify_phase1.py 전체 통과)
- KIS OAuth2 / REST / WebSocket / Rate Limit 구축

### Phase 2 — 스크리닝 로직 🟡 → ✅ (R-08 흡수)
- 수동 입력 + 자동 검증 방식 (ISSUE-010)
- R-08 에서 `top_n_gainers` 삭제, 개수 제한 없음으로 변경

### Phase 3 — DRY_RUN 풀가동 ✅
- 2026-04-06 시작, 이후 R-07/R-08 거치며 안정화

### Phase α-0 — 매매 로직 누락 보강 ✅ (2026-04-07)
- 항목 1: 10:00 이전 타임아웃 가드 + 주석 정정
- 항목 2: 청산 조건 ④ 선물 급락 WebSocket 구독
- DRY_RUN 회귀 검증 통과

### Phase α-1a — Oracle Cloud lift-and-shift ✅ (2026-04-08)
- VM.Standard.E2.1.Micro (ap-chuncheon-1, AMD x86_64, 956Mi + 4GB swap)
- Python 3.11.15 / SQLite WAL / SIGTERM graceful / cloudflared / systemd
- 가동: ubuntu@134.185.115.229, 2026-04-08 01:02 KST

### Phase α-1b — Named Tunnel 전환 📅 (보류)
- ISSUE-028 (vault 이전 트래커, R-08 백로그로 흡수)
- 사전: 도메인 결정 + Cloudflare 계정

### R-07 종결 — Watcher 아키텍처 ✅ (2026-04-09)
- 상세: [[R/R-07_종결]]

### R-08 종결 — 매매 명세 구현 + 운영 배포 ✅ (2026-04-10)
- 상세: [[R/R-08_종결]]

---

## 다음 단계

- 2026-04-13 (월) 첫 거래일 DRY_RUN 검증 (R-08 W-11d/e 두 번째 매매 체인 + W-12-rev2 호가 단위 + W-13 timeout 20분 + W-14 매수 비율 의도 + last-line defense 발화 여부)
- 검증 결과 → [[매매일지/2026-04-13]] 작성
- R-09 검토 영역 진입 (R-08 백로그 #3, #8, #9, #11 + ISSUE-030/035 + Watcher 유닛 테스트)
