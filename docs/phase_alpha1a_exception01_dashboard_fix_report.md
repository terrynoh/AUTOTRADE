# Phase α-1a Exception-01: Dashboard ISSUE-030 수정 보고서

**작성일시**: 2026-04-08 01:55 KST
**작업자**: Claude Sonnet 4.6 (K 수석 지시)
**결과**: ✅ both-ok (수석님 시각 확인 완료)

---

## 1. 예외 결정 인용

**DECISION-α1a-EXCEPTION-01** (2026-04-08 01:35 KST)

α-1a 절대 원칙 "dashboard 코드 0줄 변경"에 대한 일시 예외.
예외 범위: `src/dashboard/app.py` + `src/dashboard/templates/index.html` 합쳐서 5줄 이내.
예외 사유: ISSUE-030 두 버그가 09:00 가동 검증의 시각적 도구를 무력화. 수정 범위가 작고 영향 격리 가능하다고 판단하여 즉시 수정 선택.
이 예외는 이후 dashboard 코드 변경에 대한 일반적 허용이 아님.

---

## 2. §EXCEPTION-1 코드 base 분석 결과

### 버그 1 (예수금 "-") 분석
- WebSocket 핸들러 `app.py:355`: `is_admin = not ADMIN_TOKEN or hmac.compare_digest(token_param, ADMIN_TOKEN)`
- HTML `index.html:467`: `ws = new WebSocket(…/ws)` — `?token=` 미부착
- 결과: `token_param = ""` → `is_admin = False` → `available_cash` strip → JS `undefined` → `fmtWon(undefined)` = `"-"`
- 기존 주입 패턴 확인: `app.py:220`에 `/*__TOKEN__*/` → `const ADMIN_TOKEN = "…"` 이미 완비. HTTP 헤더 전송에는 사용 중(line 287-289). WebSocket에만 미연결.

### 버그 2 (평가금액 ← 투자원금) 분석
- `app.py:405`: `state.total_eval = at._initial_cash` (초기 자본금 고정)
- `holdings_market_value` 또는 보유 종목 시가 합계 변수: **코드 base에 없음**
- 보수적 결정 적용: `total_eval = at._available_cash` (보유 0 가정)

---

## 3. 변경 내용

### 변경된 라인 (git diff 기준)

**src/dashboard/app.py** (1줄)
```python
# BEFORE (line 405)
state.total_eval = at._initial_cash

# AFTER
state.total_eval = at._available_cash
```

**src/dashboard/templates/index.html** (1줄)
```javascript
// BEFORE (line 467)
ws = new WebSocket(`${proto}//${location.host}/ws`);

// AFTER
ws = new WebSocket(`${proto}//${location.host}/ws${(typeof ADMIN_TOKEN !== 'undefined' && ADMIN_TOKEN) ? '?token=' + ADMIN_TOKEN : ''}`);
```

**합계: 2 files, 2 lines** (≤ 5줄 제약 준수 ✅)

### 인증 안전성 확인
- admin 접속 시: `/*__TOKEN__*/` → `const ADMIN_TOKEN = "실제토큰"` 주입 → WebSocket에 `?token=실제토큰` 부착 → `is_admin = True` → `available_cash` 전송
- 비-admin 접속 시: `const ADMIN_TOKEN = ""` → WebSocket에 토큰 없음 → `is_admin = False` → `available_cash` strip 유지
- 기존 stripping 로직 제거 없음 ✅

---

## 4. Commit 정보

| 항목 | 값 |
|------|-----|
| Commit hash | `fcc5a6a9580c6eaaaf9b769810846c9d075d3eb4` |
| 메시지 | `alpha1a-exception: dashboard ISSUE-030 fix` |
| 브랜치 | `feature/dashboard-fix-v1` |
| 롤백 명령 | `git revert --no-edit fcc5a6a` |

---

## 5. 재배포 후 30초 시점 로그 (발췌)

```
2026-04-08 01:45:51.895 | INFO  | 텔레그램 알림 활성화 (2명)
2026-04-08 01:45:51.896 | INFO  | DB 초기화 완료
2026-04-08 01:45:52 ~ 53 | INFO  | AUTOTRADE 시작 (모드: dry_run)
2026-04-08 01:45:53.140 | INFO  | KIS 토큰 캐시 로드 (만료: 2026-04-08 23:45)
2026-04-08 01:45:53.141 | INFO  | KIS API 연결 완료 (모의투자)
2026-04-08 01:45:53.174 | INFO  | 선물 실시간 구독: 101S3000 (TR: H0IFCNT0)
2026-04-08 01:45:53.174 | INFO  | [DRY_RUN] 가상 예수금: 50,000,000원 → 매매가용: 50,000,000원
2026-04-08 01:45:53.702 | INFO  | 대시보드 서버 시작 (port=8503)
2026-04-08 01:45:53.707 | INFO  | 종목 마스터 로드: 2773건
2026-04-08 01:45:57.098 | INFO  | Cloudflare Tunnel 시작: https://sender-pts-recreation-bunch.trycloudflare.com
```

---

## 6. 메모리 변화

| 시점 | Used | Swap |
|------|------|------|
| 재시작 전 | 340Mi / 956Mi (36%) | 1Mi / 4Gi |
| 30초 후 | 340Mi / 956Mi (36%) | 1Mi / 4Gi |

메모리 게이트 (< 800Mi, swap < 1500Mi) ✅

---

## 7. 새 Tunnel URL

`https://sender-pts-recreation-bunch.trycloudflare.com`

---

## 8. 수석님 확인 결과

**both-ok** ✅

- 예수금 카드: `50,000,000원` 표시 확인 (버그 1 수정 성공)
- 평가금액 카드: `50,000,000원` 표시 확인 (버그 2 수정 성공)
- 롤백 없음

---

## 9. 잔여 사항

- **보유 종목 발생 후 `total_eval` 정확도**: 현재 `_available_cash`만 반영. 보유 시 `available_cash + 보유종목시가합계`가 정확하나 `holdings_market_value` 미구현. → ISSUE-030 잔여로 α-1b 이후 추적.
- **ISSUE-030 상태**: 두 버그 수정 완료. 보유 발생 후 `total_eval` 정확도 개선은 잔여 항목으로 유지.
