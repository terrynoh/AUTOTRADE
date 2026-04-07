# 사전 조사 5/5 — 시크릿 로딩 현재 상태

작성일: 2026-04-07
브랜치: feature/dashboard-fix-v1
git 상태: 미추적 파일만(??), 추적 파일 수정 없음 — 조사 진행

---

## §0. 사전 학습

이전 보고서(recon_01~04) 재독 생략. 사전 확정 사실 적용.

---

## §3-1. .env 파일 키 목록

### .env.example (키 이름만)

```
KIS_APP_KEY
KIS_APP_SECRET
KIS_ACCOUNT_NO
KIS_ACCOUNT_NO_PAPER
TRADE_MODE
DASHBOARD_PORT
DASHBOARD_ADMIN_TOKEN
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
LOG_LEVEL
```

### 실제 .env (키 이름만, 값 인용 금지)

```
KIS_APP_KEY
KIS_APP_SECRET
KIS_ACCOUNT_NO
KIS_ACCOUNT_NO_PAPER
USE_LIVE_API
TRADE_MODE
DRY_RUN_CASH
DASHBOARD_ADMIN_TOKEN
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
DASHBOARD_PORT
LOG_LEVEL
```

.env.example 대비 실제 .env에 추가된 키: `USE_LIVE_API`, `DRY_RUN_CASH`
.env.example에만 있고 실제 .env에 없는 키: 없음

---

## §3-2. settings.py 시크릿 로딩 코드

`config/settings.py:26-59` — `class Settings(BaseSettings)`:

```python
# config/settings.py:30-33  KIS
kis_app_key: str = ""
kis_app_secret: str = ""
kis_account_no: str = ""
kis_account_no_paper: str = ""

# config/settings.py:46-47  텔레그램
telegram_bot_token: str = ""
telegram_chat_id: str = ""

# config/settings.py:50-51  대시보드
dashboard_admin_token: str = ""
dashboard_port: int = 8503
```

로딩 방식: `pydantic-settings BaseSettings` — `model_config` 에 `.env` 파일 경로 지정:

```python
# config/settings.py:56-59
model_config = {
    "env_file": str(PROJECT_ROOT / ".env"),
    "env_file_encoding": "utf-8",
}
```

모든 시크릿 필드 타입: `str = ""` (기본값 빈 문자열). `SecretStr` 미사용. Pydantic validator 없음.

---

## §3-3. 시크릿 사용 지점 grep

### kis_app_key / kis_app_secret / account_no

파일별 hit 수:

| 파일 | hit 수 |
|------|--------|
| `src/main.py` | 3 |
| `src/kis_api/kis.py` | 24 |

대표 3개:

```
src/main.py:57      app_key=self.settings.kis_app_key,
src/main.py:58      app_secret=self.settings.kis_app_secret,
src/main.py:59      account_no=self.settings.account_no,
```

→ `AutoTrader.__init__` 에서 `KISAPI(...)` 생성 시 1회 전달. 이후 `KISAPI` 인스턴스 내부에서만 사용.

### telegram_bot_token / telegram_chat_id

```
src/main.py:95      bot_token=self.settings.telegram_bot_token,
src/main.py:96      chat_id=self.settings.telegram_chat_id,
```

→ `Notifier(...)` 생성 시 1회 전달.

### dashboard_admin_token / DASHBOARD_ADMIN_TOKEN

```
src/dashboard/app.py:27    ADMIN_TOKEN = os.getenv("DASHBOARD_ADMIN_TOKEN", "")
src/main.py:206            admin_token = os.getenv("DASHBOARD_ADMIN_TOKEN", "")
```

→ `dashboard/app.py`는 `pydantic-settings`를 사용하지 않고 `os.getenv()` 직접 호출.
→ `src/main.py:206`도 `Settings()` 경유 없이 `os.getenv()` 직접 호출 (Cloudflare Tunnel URL 생성 용도).
→ `Settings.dashboard_admin_token` 필드(`settings.py:50`)는 현재 코드에서 사용되지 않음.

---

## §3-4. 암호화 인프라 존재 여부

`cryptography`, `Fernet`, `fernet`, `keyring`, `nacl`, `base64.*decode.*KEY` grep 결과:

**hit 없음 — 암호화 인프라 없음. 평문 .env 사용.**

---

## §3-5. .gitignore 시크릿 보호

`.gitignore` 내 시크릿 관련 패턴:

```
.env
token_paper.json
token_live.json
token_*.tmp
```

`.env` git history 확인:

```
(출력 없음)
```

→ `.env`가 git history에 커밋된 이력 없음.

---

## §3-6. 토큰/키 길이·형식

`config/settings.py`에 Pydantic validator, type hint 제약, SecretStr 미사용. 형식 추론 불가.

| 항목 | 결론 |
|------|------|
| `kis_app_key` 형식 | 추론 불가 (타입: `str`, 제약 없음) |
| `kis_app_secret` 형식 | 추론 불가 (타입: `str`, 제약 없음) |
| `telegram_bot_token` 형식 | 추론 불가 (타입: `str`, 제약 없음) |
| `dashboard_admin_token` 형식 | 추론 불가 (타입: `str`, 제약 없음) |

---

## §6. 발견 사항

1. **`Settings.dashboard_admin_token` 필드 사용 안 됨**: `settings.py:50`에 `dashboard_admin_token: str = ""` 정의되어 있으나, 실제 코드(`dashboard/app.py:27`, `src/main.py:206`)는 모두 `os.getenv("DASHBOARD_ADMIN_TOKEN", "")`으로 직접 읽음. Settings 경유 없음.

2. **토큰 캐시 파일(`token_paper.json`, `token_live.json`)에 KIS access token 평문 저장**: `src/kis_api/kis.py:37-56` `_save_token_cache()` — JSON 파일에 `"access_token": token` 평문 기록. `os.chmod(..., 0o600)` 적용 시도하나 Windows에서는 무시됨(`except OSError: pass`). `.gitignore`로 커밋 제외는 됨.

3. **`SecretStr` 미사용**: 모든 시크릿 필드가 `str` 타입. `repr()`, 로그 출력 시 평문 노출 가능성 있음.

4. **KIS access token 24시간 유효**: 캐시 파일 탈취 시 24시간 동안 유효한 토큰 노출. 파일 권한은 Linux 클라우드에서 `chmod 600` 적용됨 (Windows `except` 분기는 통과 안 함).
