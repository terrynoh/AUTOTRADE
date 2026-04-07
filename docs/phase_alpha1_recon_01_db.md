# 사전 조사 1/5 — DB 코드 vs 실제 DB 파일

작성일: 2026-04-07
브랜치: feature/dashboard-fix-v1
git 상태: 미추적 파일만(??), 추적 파일 수정 없음 — 조사 진행

---

## §0. 사전 학습

- CLAUDE.md: 읽음 (라인 수: 약 480라인)

---

## §1-1. DB 파일 존재 여부

**발견됨 (실행 확인):**

| 경로 | 크기 | 최종 수정 | 행 수 |
|------|------|----------|------|
| `./data/trades.db` | 28,672 bytes (28K) | 2026-04-06 19:01 | 전 테이블 0행 |

`.gitignore` 에 `data/` 디렉토리 전체가 등록되어 있음 → git 추적 안 됨 (정상).

**실제 테이블 목록 (sqlite_master 확인):**
- `trades` (0행)
- `daily_summary` (0행)
- `sqlite_sequence` (내부 시스템 테이블, 0행)

**실제 인덱스:**
- `idx_trades_code` → trades
- `idx_trades_date` → trades
- `sqlite_autoindex_daily_summary_1` → daily_summary (UNIQUE 제약 자동 생성)

---

## §1-2. database.py 스키마 정의

파일: `src/storage/database.py`
SQL 방식: raw `sqlite3` 직접 (ORM 없음)

### 테이블 1 — `trades`

```sql
-- src/storage/database.py:36-59
CREATE TABLE IF NOT EXISTS trades (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date           TEXT NOT NULL,
    code                 TEXT NOT NULL,
    name                 TEXT NOT NULL,
    market               TEXT NOT NULL,
    avg_buy_price        REAL DEFAULT 0,
    total_buy_qty        INTEGER DEFAULT 0,
    total_buy_amount     INTEGER DEFAULT 0,
    buy_count            INTEGER DEFAULT 0,
    first_buy_time       TEXT,
    avg_sell_price       REAL DEFAULT 0,
    total_sell_amount    INTEGER DEFAULT 0,
    sell_time            TEXT,
    exit_reason          TEXT NOT NULL,
    pnl                  REAL DEFAULT 0,
    pnl_pct              REAL DEFAULT 0,
    holding_minutes      REAL DEFAULT 0,
    rolling_high         INTEGER DEFAULT 0,
    entry_trigger_price  INTEGER DEFAULT 0,
    target_price         REAL DEFAULT 0,
    trade_mode           TEXT DEFAULT 'dry_run',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
```

컬럼 수: 22

### 테이블 2 — `daily_summary`

```sql
-- src/storage/database.py:61-75
CREATE TABLE IF NOT EXISTS daily_summary (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date      TEXT UNIQUE NOT NULL,
    trade_mode        TEXT DEFAULT 'dry_run',
    candidates_count  INTEGER DEFAULT 0,
    targets_count     INTEGER DEFAULT 0,
    total_trades      INTEGER DEFAULT 0,
    winning_trades    INTEGER DEFAULT 0,
    losing_trades     INTEGER DEFAULT 0,
    no_entry_count    INTEGER DEFAULT 0,
    total_pnl         REAL DEFAULT 0,
    max_single_loss   REAL DEFAULT 0,
    max_single_gain   REAL DEFAULT 0,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
```

컬럼 수: 13

### 인덱스

```sql
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
```

### 초기화 함수

- 함수명: `_init_tables()` (`src/storage/database.py:33`)
- 별도 `init_db()`, `create_tables()` 함수 없음
- `Database.__init__()` 생성자에서 직접 `self._init_tables()` 호출 (`database.py:26`) → 인스턴스 생성 즉시 실행

---

## §1-3. 마이그레이션 호출 추적

`_init_tables()`는 `Database.__init__` 내부에서만 호출. 외부 직접 호출 없음.

`Database()` 인스턴스화 지점:

| 파일 | 라인 | 맥락 |
|------|------|------|
| `src/main.py` | 100 | `self._db = Database()` — `AutoTrader.__init__` 내부 |

→ `AutoTrader` 생성 시 자동으로 DB 초기화(`_init_tables`) 실행.
→ `Database` 직접 import 지점: `src/main.py:43`.

---

## §1-4. DRY_RUN 모드의 DB 사용 여부

**분기 구조:**

DRY_RUN 분기(`settings.is_dry_run`)가 trader.py 및 main.py에 여러 곳에 존재하지만, DB 저장 경로는 모드에 관계없이 공통이다.

**DRY_RUN 가상 체결 흐름:**

1. `main.py:791-794` — DRY_RUN 분기에서 `trader.simulate_fills()` 호출 → 가상 체결 발생
2. 체결 이후 `mon.signal_exit` 시그널 → `main.py:827` — `self._db.save_trade(record)` 호출
3. 장 마감 시 `main.py:452` — `self._db.save_trade(record)` (미진입 포함)
4. 장 마감 시 `main.py:469` — `self._db.save_daily_summary(summary)` 호출

**저장 위치:** `self._trade_records` (메모리 리스트)에 추가 **+ 동시에 DB에 저장**

`trades.trade_mode`, `daily_summary.trade_mode` 컬럼에 `'dry_run'` 값이 기록되어 실거래 기록과 구분 가능.

**결론:** DRY_RUN은 DB를 **사용한다** (trades + daily_summary 양쪽 모두 기록).

---

## §1-5. multi-user 흔적 확인

`src/storage/database.py` 스키마에서 다음 키워드 grep 결과:

| 키워드 | 결과 |
|--------|------|
| user / users | 없음 |
| account / accounts | 없음 |
| tenant | 없음 |
| role | 없음 |
| audit_log | 없음 |
| password | 없음 |
| session | 없음 |

**결론:** 현재 스키마에 multi-user 관련 테이블/컬럼 없음.

---

## §6. 발견 사항

1. **DB 파일 존재하나 전 테이블 0행**: 파일(28K)은 있지만 데이터는 없음. 스키마만 생성된 상태. `.gitignore`로 올바르게 제외됨.

2. **DRY_RUN이 real DB에 쓴다**: Phase α-1 클라우드 이전 후 DRY_RUN 테스트 시 운영 DB와 같은 파일에 기록됨. `trade_mode` 컬럼으로 구분은 가능하나, Phase α-1 다중 사용자 설계 시 DB 파일 경로 분리 또는 테스트 전용 경로 옵션을 고려해야 하는지 확인 필요.

3. **Phase α-1 신규 테이블 작업 확인**: CLAUDE.md §5.5 Phase α-1에서 `user`, `audit_log` 테이블 신규 추가 예정. 현재 스키마에 없음 → 마이그레이션 스크립트 또는 `_init_tables()` 확장 필요.
