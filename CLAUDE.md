# CLAUDE.md — AUTOTRADE (v3.1, R-08 구현 완료 + 소스 관리 보강)

> **목적**: 이 문서는 신규 Claude 세션이 AUTOTRADE 프로젝트의 *context 연결 없이* 프로그램 정체성, 매매 철학, 아키텍처, 협업 규칙, 운영 환경을 즉시 파악할 수 있도록 작성된 self-contained 명세서.
>
> **버전**: v3.1 (2026-04-10, R-08 구현 완료 + §7.4 소스 관리 + 부록 A commit 이력 보강)
> **이전 버전**: v3 (2026-04-10, R-08 매매 명세 구현 완료 + 운영 배포 후), v2 (2026-04-09, R-07 종결), v1 (프로젝트 시작)
> **현재 단계**: R-08 구현 완료, DRY_RUN 운영 중. 4/13 첫 거래일 검증 대기

---

## 목차

1. 프로그램 정체성
2. 매매 명세 (R-08, 동결본)
3. 아키텍처
4. 파일 구조 + 책임
5. 자료구조
6. 매매 흐름 (시각별)
7. 개발 / 운영 환경
8. 협업 규칙 (수석님 ↔ Claude)
9. 4 페르소나
10. W / M / R 워크플로우
11. 이력 + 사고 이력
12. R-08 빈틈 카탈로그 + 백로그
13. 향후 확장 방향
14. vault 참조 원칙
15. 금지 사항

---

## 1. 프로그램 정체성

### 이름
**AUTOTRADE** — 한국 주식 자동 매매 시스템 (단타)

### 운영자
수석님 (K수석). Bangkok 거주, 한국 시니어 소프트웨어 엔지니어. ~18~20년 대규모 온라인 서비스 인프라/API 통합/스케줄링/DB/배포 경험. 강점: 시스템 엔지니어링. 학습 중: 매매/금융 도메인. Korean capital markets 에 대한 깊은 이해 + 독립 분석력.

### 목적
KIS (한국투자증권) Open API 를 통해 KOSPI/KOSDAQ 종목을 *매일 장중 90분 윈도우* (09:50 ~ 11:20) 안에서 *자동 단타 매매*.

### 매매 철학 (2026-04-09 수석님 확정)

**상승 메커니즘**:
- 외인/기관 지지 (바닥 방어) → 개인 주목 → 개인 매수 유입 → 상승
- 외인/기관은 바닥만 만들고 *상승 주도 아님*
- 개인 매수 유입이 진짜 상승의 동력

**시각별 의미**:
- **09:00 ~ 09:50** = 지지 형성 구간 (노이즈, 매매 대상 아님)
- **09:50** = 지지 구조 확인 시점 (스크리닝)
- **09:55** = 신고가 감시 시작 ("신고가" 정의 = 09:50 스크리닝 통과 이후 당일 최고가, 09:50 이전 고가는 신호 아님)
- **09:55 pre_955_high 5분 버퍼** = 급등 추세성 검증 윈도우 (의도된 설계)
- **10:00 ~ 10:55** = 신규 진입 발주 윈도우 (반등 메커니즘 작동)
- **10:55** = 매수 발주 마감 (entry_deadline, 11:00 - 5분 실행 버퍼)
- **11:00** = 반등 메커니즘 소멸 (last-line defense)
- **11:20** = 강제 청산 (당일 진입 + 당일 청산 원칙)

**보수적 방어**:
- 신고가 + 1% 하락 + 매수가 도달 모두 충족되어야 매수
- 조건 미충족 시 *매매 0건도 수용*
- 자본 보호 우선 (손절가는 고가 대비 절대값)

### 운영 모드
- **DRY_RUN (현재 기본)** — 실거래 KIS API + 가상 체결 (simulate_fills). 자본 위험 0
- **LIVE (수석님 결정 시점)** — 실거래 KIS API + 실제 주문. 단계적 자본 증가 계획

---

## 2. 매매 명세 (R-08 동결본)

> 이 명세는 `config/strategy_params.yaml` 에 인코딩되어 있으며, R-08 의 W-11~W-15 단계에서 큰 변경이 적용됨. 임의 변경 금지 — 변경 시 새 R-N redesign 필요.

### 시각 트리거

| 시각 | 이벤트 |
|---|---|
| 06:40 | cron + send_dashboard_url.py → autotrade.service 가동 |
| 09:30 ~ 09:50 | 수석님 종목 입력 (대시보드, 개수 제한 없음) |
| 09:50 | `_schedule_screening` 정규 트리거 → 종목 스크리닝 + Watcher N개 가동 (`is_final=True`) |
| 09:55 | 신고가 감시 시작 (Watcher 의 `intraday_high` 추적) |
| **10:00 ~ 10:55** | **신규 진입 발주 윈도우** (`_is_in_entry_window`) |
| 10:55 | `entry_deadline` — 신규 진입 발주 마감 (`on_buy_deadline`, 미매수 Watcher → SKIPPED) |
| **11:00** | **매매 철학 시한** (`repeat_end`, Trader last-line defense, 어떤 경로로도 신규 발주 거부) |
| 11:20 | `force_liquidate_time` — 잔여 ENTERED 강제 청산 |
| 장 마감 | `_schedule_market_close` → 일일 리포트 텔레그램 발송 |

### 유니버스 + 스크리닝 (R-08 변경)

- **수동 입력 개수 제한 없음** (R-07 의 `top_n_gainers=3`, `top_n_candidates` 모두 삭제)
- **09:50 단일 정규 스크리닝** (`is_final=True` 가드 활성)
- **09:50 이전 수동 스크리닝 여러 번 허용** (`is_final=False`, 덮어쓰기 가능)
- **active 상태에서 재스크리닝 차단** (포지션 보호)
- **Watcher 동적 편입 없음** (임시, R-09+ 검토 영역)

### 매수 조건 (단계별)

```
[Watcher 상태 전이]

WATCHING
  ↓ 신고가 달성 + 고가 대비 1% 하락 확인
TRIGGERED
  ↓ 목표 매수가 계산 완료 (W-12-rev2: KRX 호가 단위 보정)
  ↓ 현재가가 매수 범위 내 + 진입 윈도우 안 (10:00~10:55)
READY
  ↓ active 슬롯 확보 + 매수 발주 + 체결 (Trader)
ENTERED
  ↓ 5조건 중 하나 충족
EXITED

또는 TRIGGERED 후 20분 (high_confirm_timeout_min, W-13) 미체결 → SKIPPED
또는 10:55 도달 → SKIPPED (entry_deadline)
또는 현재가가 손절선 이하 → DROPPED (terminal)
또는 신고가 갱신 시 → WATCHING 복귀 (재트리거 가능)
```

### 매수가 (W-14 변경 후 yaml 동결값)

| 시장 | 1차 매수가 | 2차 매수가 | 분할 간격 |
|---|---|---|---|
| KOSPI | 고가 × (1 - 2.5/100) | 고가 × (1 - 3.5/100) | 1.0% |
| KOSDAQ | 고가 × (1 - 3.5/100) | 고가 × (1 - 5.5/100) | 2.0% |

**W-14 의도** (수석님 확정 2026-04-10): 1차 진입 빈도 ↑. 1차를 얕게 (덜 떨어진 시점에 매수), 2차를 깊게 (분할 간격 확대). 평균가는 동일 (KOSPI 3.0%, KOSDAQ 4.5%).

**호가 단위 보정** (W-12-rev2): KRX 2023-01-25 개편 후 기준 (대신증권 공고 출처). KOSPI/KOSDAQ 동일.
- < 2,000 = 1
- < 5,000 = 5
- < 20,000 = 10
- < 50,000 = 50
- < 200,000 = 100
- < 500,000 = 500
- ≥ 500,000 = 1,000

매수가는 floor (안전 방향 = 더 깊은 진입), 손절가는 ceil (안전 방향 = 더 빨리 손절).

### 5 청산 조건 (Watcher._handle_entered)

| 조건 | 임계값 |
|---|---|
| 하드 손절 | 고가 대비 KOSPI -4.1% / KOSDAQ -6.15% (호가 단위 ceil) |
| 타임아웃 | 매수 후 저점 갱신 후 20분 경과 (`exit.timeout_from_low_min = 20`) |
| 목표가 도달 | 목표가 = (confirmed_high + post_entry_low) / 2 (호가 단위 floor) |
| 선물 급락 | KOSPI200 선물 -1% (`exit.futures_drop_pct = 1.0`) |
| 강제 청산 | 11:20 도달 (`exit.force_liquidate_time`) |

### 다음 종목 매매 (R-08 신규, W-11d/e)

R-08 의 핵심 변경 — 첫 종목 매매가 종료된 후 두 번째 종목으로 자동 이행:

**T1 (첫 종목 ENTERED)**: `_active_code` 가 첫 종목 코드로 설정됨.

**T2 (첫 종목 buy2 체결)**:
- Watcher.on_buy_filled 의 buy2 분기에서 `_t2_callback` 호출
- Coordinator._on_t2 진입
- 진입 윈도우 (10:00~10:55) 안인지 확인
- YES 후보 (state == READY) 수집
- **두 번째 매매 tiebreaker**: KOSPI 눌림폭 ≥ 3.8% / KOSDAQ ≥ 5.6% 필터 + 눌림폭 최대
- 통과 종목을 `_reserved_snapshot` 에 저장 (ReservationSnapshot dataclass)
- 매매 의도: "손절 직전까지 떨어진 종목 = 더 깊은 진입 = R:R 비대칭 확보"

**T3 (첫 종목 EXITED)**:
- main.py._on_exit_done 에서 `await coordinator.handle_t3(now_kst())` 위임
- 진입 윈도우 (10:00~10:55) 체크 — 벗어나면 예약 폐기
- **예약 재확인 (엄격)**:
  1. 종목이 watchers 에 존재
  2. is_yes (state == READY)
  3. not is_terminal
  4. target_buy1_price 가 T2 시점 값과 동일 (재계산되지 않음)
  5. tiebreaker 조건 여전히 충족
- 재확인 통과 → 예약 종목 진입
- 재확인 실패 → 예약 폐기 + 재판정 (yes_watchers 재수집 + tiebreaker)
- 재판정 0개 → 즉시 포기

**N차 매매 연쇄**: 두 번째 매매에서도 T2/T3 가 발생하면 세 번째, 네 번째 매매가 이어짐. 진입 윈도우 (10:00~10:55) 가 끝나면 자연스럽게 멈춤. `daily_loss_limit_pct = 3.0%` 가 누적 손실 안전망.

**관련 yaml 키** (multi_trade 섹션):
- `enabled: true` (R-08 활성화)
- `repeat_start: "10:00"` (진입 윈도우 시작)
- `repeat_end: "11:00"` (매매 철학 시한, last-line defense)
- `profit_only: false` (손익 무관 다음 진입)
- `kospi_next_entry_max_pct: 3.8` (두 번째 매매 tiebreaker 임계)
- `kosdaq_next_entry_max_pct: 5.6`

### 운영 파라미터 (W-11~W-15 후)

- **high_confirm_drop_pct**: 1.0 (신고가 후 1% 하락 시 TRIGGERED)
- **high_confirm_timeout_min**: 20 (W-13: 10→20, 매수 기회 확보)
- **entry_deadline**: "10:55"
- **repeat_start**: "10:00" (multi_trade)
- **repeat_end**: "11:00" (multi_trade, last-line defense)
- **force_liquidate_time**: "11:20"
- **kospi_hard_stop_pct**: 4.1
- **kosdaq_hard_stop_pct**: 6.15
- **futures_drop_pct**: 1.0
- **exit.timeout_start_after_kst**: "10:00"
- **kospi_next_entry_max_pct**: 3.8 (두 번째 매매 tiebreaker)
- **kosdaq_next_entry_max_pct**: 5.6
- **daily_loss_limit_pct**: 3.0 (안전망)
- **max_position_size_pct**: (yaml 참조)

### yaml 전체 구조 (6개 섹션)

| 섹션 | 주요 키 |
|---|---|
| `screening` | screening_time, volume_min, program_net_buy_ratio_min, max_change_pct |
| `entry` | new_high_watch_start, entry_deadline, high_confirm_drop_pct, high_confirm_timeout_min, kospi/kosdaq_buy1/2_pct, buy1/2_ratio |
| `exit` | profit_target_recovery_pct, timeout_from_low_min, kospi/kosdaq_hard_stop_pct, futures_drop_pct, timeout_start_after_kst, force_liquidate_time |
| `order` | slippage_ticks, unfilled_timeout_sec, max_simultaneous_positions |
| `risk` | daily_loss_limit_pct, max_position_size_pct |
| `multi_trade` | enabled, repeat_start, repeat_end, profit_only, kospi/kosdaq_next_entry_max_pct |
| `api` / `market` / `infra` | rate_limit, 시장 시간, HTTP/WS 타임아웃, 대시보드 버퍼, 로그 rotation 등 (별도 Pydantic 클래스 없음) |

> R-07 의 `top_n_gainers`, `top_n_candidates`, `max_daily_trades` 는 R-08 에서 삭제됨.

---

## 3. 아키텍처

### 컴포넌트 다이어그램

```
┌─────────────────────────────────────────────────────────────┐
│                      AutoTrader (main.py)                    │
│  - run() : 전체 매매 루프                                    │
│  - _schedule_screening : 09:50 트리거                        │
│  - _schedule_buy_deadline : 10:55 트리거                     │
│  - _schedule_force_liquidate : 11:20 트리거                  │
│  - _schedule_market_close : 장 마감 트리거                   │
│  - _on_screening() : 스크리닝 → Coordinator 위임             │
│  - _on_exit_done() : 청산 체결 → P&L → DB → Telegram         │
│                      → handle_t3 위임 (W-11e)                │
│  - _loop : asyncio loop (필드, Dashboard 가 참조)            │
└─────────────────────────────────────────────────────────────┘
         │              │              │              │
         ▼              ▼              ▼              ▼
    ┌────────┐    ┌──────────┐   ┌────────┐    ┌──────────┐
    │Screener│    │Coordinator│   │ Trader │    │ Notifier │
    └────────┘    └──────────┘   └────────┘    └──────────┘
                        │
                        ├── Watcher (N instances, 09:50 가동)
                        │     - state: WatcherState (8 상태)
                        │     - intraday_high, target_buy1/2_price, hard_stop_price_value
                        │     - on_tick, _handle_watching, _handle_triggered
                        │     - _handle_entered, _check_timeout, _check_futures_drop
                        │     - _t2_callback (W-11c, T2 콜백)
                        │     - is_yes @property (W-11c-hotfix, READY only)
                        │     - get_pullback_pct (W-11c)
                        │
                        ├── single active rotation
                        │     on_buy_deadline, on_force_liquidate
                        │
                        └── 두 번째 매매 예약 (W-11d 신규)
                              _reserved_snapshot: ReservationSnapshot
                              _on_t2 → _try_reserve_at_t2 → _tiebreaker_for_next
                              handle_t3 → _verify_reservation_at_t3
                              _is_in_entry_window

┌─────────────────────────────────────────────────────────────┐
│                    Dashboard (app.py, FastAPI)               │
│  - GET  /                     : HTML 렌더링                  │
│  - GET  /api/status           : 현재 상태 JSON               │
│  - GET  /api/search-stock     : 종목 검색 (StockMaster)      │
│  - POST /api/set-targets      : 종목 입력 (StockMaster 검증) │
│  - POST /api/run-manual-screening : 수동 스크리닝            │
│    (run_coroutine_threadsafe 로 AutoTrader loop 위임)        │
│  - WebSocket /ws              : 실시간 상태 push             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     KIS API (kis_api/)                       │
│  - aiohttp ClientSession (async)                             │
│  - REST + WebSocket (실시세 / 체결)                          │
│  - 토큰 자동 갱신 + 캐시                                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   외부 인프라                                 │
│  - Oracle Cloud Seoul (Ubuntu 운영)                          │
│  - Cloudflare Quick Tunnel (대시보드 외부 접속)              │
│  - Telegram Bot (알림 + 봇 명령)                             │
│  - cron (06:40 자동 가동)                                    │
│  - systemd (autotrade.service)                               │
└─────────────────────────────────────────────────────────────┘
```

### 핵심 컴포넌트

#### AutoTrader (src/main.py)
- **책임**: 전체 매매 루프 + 외부 I/O 오케스트레이션
- **주요 필드**: `_loop`, `_available_cash`, `_initial_cash`, `_coordinator`, `_stock_master`, `_manual_codes`, `api`, `trader`, `screener`, `notifier`, `_tunnel`
- **주요 메서드**: `run`, `_schedule_screening`, `_schedule_buy_deadline`, `_schedule_force_liquidate`, `_schedule_market_close`, `_on_screening`, `_on_exit_done`, `_build_trade_record`, `_get_status`, `set_manual_codes`, `clear_manual_codes`, `get_manual_codes`, `_stop_trading`
- **W-11e 변경**: `_on_exit_done` 의 6단계 (trader.reset) 와 7단계 (대시보드 동기화) 사이에 `self._coordinator._active_code = None` + `await self._coordinator.handle_t3(now_kst())` 추가
- **라인 수**: 약 980행 (W-11e 후 +약 5)

#### WatcherCoordinator (src/core/watcher.py)
- **책임**: N Watcher 병렬 관리 + 시세 라우팅 + single active rotation + 두 번째 매매 예약
- **R-07 메서드**: `start_screening`, `on_realtime_price`, `on_realtime_futures`, `_process_signals`, `_execute_buy`, `_execute_exit`, `on_buy_filled`, `on_sell_filled`, `set_available_cash`, `set_exit_callback`, `on_buy_deadline`, `on_force_liquidate`
- **R-08 신규 메서드 (W-11d, +387 라인)**:
  - `_on_t2(code, ts)` — Watcher 의 T2 (buy2 체결) 콜백, 동기 함수
  - `_try_reserve_at_t2(ts)` — YES 후보 수집 + tiebreaker → ReservationSnapshot 반환
  - `_tiebreaker_for_next(candidates)` — KOSPI 3.8% / KOSDAQ 5.6% 필터 + 눌림폭 최대
  - `_verify_reservation_at_t3(ts)` — 5항목 엄격 재확인
  - `_is_in_entry_window(ts)` — 진입 윈도우 (10:00~10:55) 체크
  - `handle_t3(ts)` — async, T3 진입 게이트 (예약 진입 또는 재판정)
- **W-11d 변경**: `start_screening` 시그니처 추가 (`is_final: bool = False`, keyword-only), active 차단, `_t2_callback = self._on_t2` 주입
- **W-11e 변경**: `_process_signals` 의 `_is_after_buy_deadline` → `not _is_in_entry_window` 로 통합 (`_is_after_buy_deadline` 메서드 제거)
- **상태**: `watchers: list[Watcher]`, `_active_code`, `_screening_done`, `_reserved_snapshot`, `_repeat_start`, `_repeat_end`, `_entry_deadline`

#### Watcher (src/core/watcher.py)
- **책임**: 단일 종목의 매매 수명주기 관리 (WATCHING → ... → EXITED / SKIPPED / DROPPED)
- **8 상태**: WATCHING, TRIGGERED, READY, PASSED, DROPPED, ENTERED, EXITED, SKIPPED
- **주요 메서드**: `on_tick`, `_handle_watching`, `_fire_trigger`, `_handle_triggered`, `_evaluate_target`, `_check_high_confirm_timeout`, `_handle_entered`, `_check_timeout`, `_check_futures_drop`, `_emit_exit`, `force_exit`, `on_buy_filled`, `update_intraday_high`, `update_post_entry_low`
- **R-08 신규 (W-11c)**:
  - `_t2_callback: Optional[Callable[[str, datetime], None]]` 필드
  - `is_yes` @property — `state == READY` 만 True (W-11c-hotfix)
  - `get_pullback_pct()` — confirmed_high 우선, 없으면 intraday_high 기준 눌림폭 계산
  - `on_buy_filled` 의 buy2 분기에서 `_t2_callback` 호출 (try/except 격리)
- **W-12-rev2**: `_fire_trigger` 의 매수가 계산에 `floor_to_tick`/`ceil_to_tick` 적용
- **라인 수**: 1190행 (R-07 시점 768행 → +422 행)

#### Trader (src/core/trader.py)
- **책임**: 주문 발주 + 체결 콜백 (Watcher 호환 시그니처)
- **주요 메서드**: `place_buy_orders`, `_send_buy_order`, `cancel_buy_orders`, `execute_exit`, `on_buy_filled`, `simulate_fills`, `has_position`, `get_pnl`, `reset`
- **W-11e 신규**: `place_buy_orders` 진입부에 last-line defense 추가:
  ```python
  _now = now_kst()
  _repeat_end_time = dtime.fromisoformat(self.params.multi_trade.repeat_end)
  if _now.time() >= _repeat_end_time:
      logger.error(f"CRITICAL: 매매 철학 시한 위반 시도 ... 발주 거부")
      return
  ```
- **DRY_RUN 모드**: `simulate_fills` 패턴으로 가상 체결

#### Screener (src/core/screener.py)
- **책임**: 수동 스크리닝 → `list[StockCandidate]` 반환
- **주요 메서드**: `run_manual(codes)`
- **R-08 변경**: `top_n_gainers` 슬라이싱 삭제, 통과 종목 전부 반환
- **W-12-rev2**: `_tick_size` 헬퍼 제거 → `price_utils.py` 로 분리 + import

#### StockMaster (src/core/stock_master.py)
- **책임**: 종목 코드 ↔ 종목명 양방향 검증 (로컬 캐시, KIS API 호출 X)
- **주요 메서드**: `lookup_name(code)`, `lookup_code(name_or_code)`, `__len__`
- **데이터 source**: `config/stock_master.json` (약 2,773 종목, 수동 갱신)

#### RiskManager (src/core/risk_manager.py)
- **책임**: 손절가 계산, 일일 손실 한도, 포지션 가용 확인
- **주요 메서드**: `calculate_available_cash`, `check_daily_loss_limit`, `can_open_position`, `record_trade_result`, `reset_daily`

#### price_utils (src/utils/price_utils.py, W-12-rev2 신규)
- **책임**: KRX 호가 단위 보정 헬퍼 (2023-01-25 개편 후 기준)
- **주요 함수**: `tick_size(price)`, `floor_to_tick(price)`, `ceil_to_tick(price)`
- **사용처**: screener.py (스크리닝 시 가격 표준화), watcher.py (`_fire_trigger` 의 매수가/손절가/목표가 계산)
- **검증**: 삼성전기 583,000 → 1차 567,000 / 2차 563,000 / 손절 560,000 (1,000원 단위)

#### Notifier (src/utils/notifier.py)
- **책임**: Telegram 봇 + 시스템 알림
- **주요 메서드**: `notify_system`, `notify_entry`, `notify_exit`, `notify_skip`, `notify_error`, `notify_screening_result`, `notify_daily_report`, `setup_commands`, `start_polling`, `stop_polling`
- **봇 명령**: `/target`, `/clear`, `/screen`, `/status`, `/stop`, `/help`

#### Dashboard (src/dashboard/app.py)
- **책임**: FastAPI 기반 실시간 대시보드
- **주요 endpoint**:
  - `GET /` (HTML 렌더링)
  - `GET /api/status` (현재 상태 JSON)
  - `POST /api/set-targets` (W-09 정정 — StockMaster 로컬 검증)
  - `GET /api/search-stock` (종목 검색, StockMaster)
  - `POST /api/run-manual-screening` (W-09 정정 — run_coroutine_threadsafe 위임)
  - `WebSocket /ws` (실시간 상태 push)
- **R-08 백로그 #25**: screening broadcast 경로 `.stock` 잔존 참조 (StockCandidate 객체 처리 누락)

### 모듈 의존성 그래프

```
main.py
  ├── config.settings (Settings, StrategyParams)
  ├── kis_api.kis (KISAPI)
  ├── kis_api.constants (WS_TR_PRICE, WS_TR_FUTURES)
  ├── core.screener (Screener)
  ├── core.stock_master (StockMaster)
  ├── core.trader (Trader)
  ├── core.risk_manager (RiskManager)
  ├── core.watcher (Watcher, WatcherCoordinator, ReservationSnapshot)
  ├── models.trade (TradeRecord, DailySummary, ExitReason)
  ├── storage.database (Database)
  ├── utils.notifier (Notifier)
  ├── utils.tunnel (CloudflareTunnel)
  └── utils.market_calendar (now_kst)

core/watcher.py
  ├── config.settings (StrategyParams)
  ├── models.stock (MarketType)
  └── utils.price_utils (floor_to_tick, ceil_to_tick)  # W-12-rev2

core/trader.py
  ├── config.settings (Settings, StrategyParams)
  ├── kis_api.kis (KISAPI)
  ├── kis_api.constants (ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET)
  ├── core.watcher (Watcher)
  ├── models.order (Order, OrderSide, OrderStatus, Position)
  └── utils.market_calendar (now_kst)

core/screener.py
  ├── config.settings (StrategyParams)
  ├── core.stock_master (StockMaster)
  ├── kis_api.kis (KISAPI)
  ├── models.stock (StockCandidate, MarketType)
  └── utils.price_utils (floor_to_tick, ceil_to_tick)  # W-12-rev2

dashboard/app.py
  ├── utils.market_calendar (now_kst)
  └── core.watcher (Watcher)

utils/price_utils.py → 외부 의존성 없음 (self-contained)
core/stock_master.py → 외부 의존성 없음 (self-contained)
utils/notifier.py → 외부 의존성 없음 (self-contained)
```

---

## 4. 파일 구조 + 책임

```
AUTOTRADE/
├── src/
│   ├── main.py                    # AutoTrader 엔진 (약 980행)
│   ├── core/
│   │   ├── watcher.py             # Watcher + WatcherCoordinator + ReservationSnapshot (1190행)
│   │   ├── trader.py              # Trader (약 290행, +last-line defense)
│   │   ├── screener.py            # Screener (약 230행)
│   │   ├── stock_master.py        # StockMaster (70행)
│   │   └── risk_manager.py        # RiskManager
│   │
│   ├── models/
│   │   ├── stock.py               # StockCandidate, MarketType (42행)
│   │   ├── order.py               # Order, OrderSide, OrderStatus, Position (108행)
│   │   └── trade.py               # TradeRecord, DailySummary, ExitReason (105행)
│   │
│   ├── kis_api/
│   │   ├── kis.py                 # KIS REST + WebSocket 통합 (889행)
│   │   ├── api_handlers.py        # API 응답 파싱 핸들러 (114행)
│   │   └── constants.py           # 엔드포인트, TR ID, 상수 (92행)
│   │
│   ├── storage/
│   │   └── database.py            # SQLite CRUD (201행)
│   │
│   ├── dashboard/
│   │   └── app.py                 # FastAPI 대시보드 (444행)
│   │
│   ├── backtest/
│   │   ├── simulator.py
│   │   ├── data_collector.py
│   │   └── report.py
│   │
│   └── utils/
│       ├── price_utils.py         # KRX 호가 단위 보정 (W-12-rev2 신규)
│       ├── notifier.py            # Telegram 봇 + 알림 (408행)
│       ├── tunnel.py              # Cloudflare Quick Tunnel
│       ├── market_calendar.py     # 거래일 확인 (now_kst, is_trading_day 등)
│       └── logger.py              # loguru 설정
│
├── config/
│   ├── settings.py                # Pydantic Settings — .env + StrategyParams (W-15: default 동기화)
│   ├── strategy_params.yaml       # 매매 명세 (R-08 동결, W-11~W-15 변경 누적)
│   └── stock_master.json          # 종목 캐시 (2,773 종목)
│
├── scripts/
│   ├── send_dashboard_url.py      # cron 06:40 트리거
│   └── dashboard_sim.py           # 로컬 시뮬
│
├── logs/                           # 런타임 로그 (loguru, 3-tier file sink)
├── data/                           # SQLite DB
├── docs/                           # W-N 명령서 아카이브
├── backups/                        # 운영 백업
├── .env                            # 환경 변수 (12개 키)
├── venv/                           # Python 가상환경
└── CLAUDE.md                       # 이 문서
```

### 폐기된 파일 (R-07 결과)
- `src/core/monitor.py` — W-07d 폐기, Watcher 로 대체
- `src/models/stock.py:TradeTarget` — W-07d 폐기, StockCandidate 직접 사용
- `tests/test_*.py` 3 파일 — W-07c 폐기

---

## 5. 자료구조

### WatcherState (Enum)

```python
class WatcherState(str, Enum):
    WATCHING = "watching"          # 신고가 대기
    TRIGGERED = "triggered"        # 신고가 확정 (1% 하락) → 목표가 계산 완료
    READY = "ready"                # 매수 범위 내 (YES — 현재가가 1차~손절선 사이)
    PASSED = "passed"              # 자리 지나감 (NO — 현재가가 1차 매수가 위로 복귀)
    DROPPED = "dropped"            # 손절선 이하 (terminal)
    ENTERED = "entered"            # 매수 체결 (보유 중 — active position)
    EXITED = "exited"              # 청산 완료 (terminal)
    SKIPPED = "skipped"            # 포기 (terminal — 타임아웃 / 10:55 / 미매수)

TERMINAL_STATES = frozenset({
    WatcherState.DROPPED,
    WatcherState.EXITED,
    WatcherState.SKIPPED,
})
```

### 상태 전이 다이어그램

```
WATCHING → TRIGGERED → READY ↔ PASSED
   ↑          ↓          ↓        ↓
   │ (신고가  ↓        ENTERED  DROPPED (terminal)
   │  갱신)  ↓          ↓
   └─────────┘       EXITED (terminal)

또는 임의 상태 → SKIPPED (terminal, 10:55 / timeout 20분)
```

### Watcher 주요 필드

```python
@dataclass
class Watcher:
    # === 종목 정보 (불변) ===
    code: str
    name: str
    market: MarketType
    params: StrategyParams

    # === 상태 ===
    state: WatcherState

    # === 시세 ===
    current_price: int
    pre_955_high: int             # 09:50~09:55 사전 고가
    intraday_high: int            # 장중 최고가
    intraday_high_time: datetime

    # === TRIGGERED 시점 스냅샷 ===
    confirmed_high: int
    confirmed_high_time: datetime
    futures_at_confirmed_high: float
    target_buy1_price: int        # 1차 매수가 (W-12-rev2: floor_to_tick)
    target_buy2_price: int        # 2차 매수가 (W-12-rev2: floor_to_tick)
    hard_stop_price_value: int    # 하드 손절가 (W-12-rev2: ceil_to_tick)
    high_confirmed_at: datetime   # timeout 카운트 시작 시각

    # === 선물 가격 (외부 주입) ===
    futures_price: float

    # === 매수 발주 / 체결 추적 ===
    buy1_placed: bool
    buy2_placed: bool
    buy1_filled: bool
    buy2_filled: bool
    buy1_price: int
    buy2_price: int
    total_buy_amount: int
    total_buy_qty: int

    # === ENTERED 정보 ===
    entered_at: datetime
    post_entry_low: int           # 매수 후 최저가
    post_entry_low_time: datetime

    # === 청산 정보 ===
    exit_reason: str
    exit_price: int
    exited_at: datetime
    avg_sell_price: int
    total_sell_amount: int
    sell_orders: list

    # === 시그널 ===
    _exit_signal_pending: bool

    # === W-11c 신규 ===
    _t2_callback: Optional[Callable[[str, datetime], None]] = None  # T2 콜백 (Coordinator 가 주입)

    # === Properties (W-11c-hotfix, W-11d) ===
    @property
    def is_yes(self) -> bool:
        """READY 상태만 YES (수석님 확정)."""
        return self.state == WatcherState.READY

    @property
    def is_terminal(self) -> bool:
        """terminal 상태 (DROPPED / EXITED / SKIPPED)."""
        return self.state in TERMINAL_STATES

    def get_pullback_pct(self) -> float:
        """confirmed_high 우선, 없으면 intraday_high 기준 눌림폭."""
        ...
```

### ReservationSnapshot (W-11d 신규)

```python
@dataclass
class ReservationSnapshot:
    """
    T2 시점 (첫 종목 buy2 체결) 에 두 번째 매매 후보로 예약된 종목의 스냅샷.
    T3 시점 (첫 종목 EXITED) 에 이 스냅샷을 기준으로 엄격 재확인.
    """
    code: str
    name: str
    market: MarketType
    reserved_at: datetime
    confirmed_high_at_t2: int
    current_price_at_t2: int
    pullback_pct_at_t2: float
    target_buy1_price_at_t2: int    # T3 재확인 시 변경 검증용
    target_buy2_price_at_t2: int
```

### StockCandidate (src/models/stock.py)

```python
@dataclass
class StockCandidate:
    code: str
    name: str
    market: MarketType
    current_price: int
    price_change_pct: float
    trading_volume_krw: int

    @property
    def program_net_buy_ratio(self) -> float: ...
```

### TradeRecord (src/models/trade.py)

```python
class ExitReason(str, Enum):
    TARGET, HARD_STOP, TREND_BREAK, TIMEOUT,
    FUTURES_STOP, FORCE_LIQUIDATE, MANUAL, NO_ENTRY

@dataclass
class TradeRecord:
    # code, name, market
    # entry_time, exit_time
    # buy1_price, buy2_price, avg_buy_price
    # exit_price, quantity
    # pnl_krw, pnl_pct
    # exit_reason

@dataclass
class DailySummary:
    @property
    def win_rate(self) -> float: ...
    def add_trade(self, trade: TradeRecord) -> None: ...
```

---

## 6. 매매 흐름 (시각별)

### 06:40 — cron 자동 가동

```
cron → send_dashboard_url.py
     → autotrade.service 시작
     → AutoTrader.__init__ + run()
     → self._loop = asyncio.get_running_loop()
     → api.connect() (KIS API REST + WebSocket)
     → api.subscribe_futures() (KOSPI200 선물)
     → DRY_RUN 모드: dry_run_cash (5천만) 주입
     → Coordinator.set_available_cash
     → _start_dashboard_server (FastAPI)
     → _tunnel.start() (Cloudflare Quick Tunnel)
     → Telegram: 대시보드 URL 발송
     → 매매 시각 대기
```

### 09:30 ~ 09:50 — 수석님 액션

```
1. 텔레그램으로 대시보드 URL 받음
2. 대시보드 접속 (admin token)
3. 직접 입력 박스에 종목 입력 (개수 제한 없음)
4. [종목 설정] 클릭 → api_set_targets (StockMaster 검증) → _manual_codes 저장
5. (선택) [수동 스크리닝] 클릭 → start_screening(is_final=False)
   - 09:50 정규 스크리닝 전에 여러 번 호출 가능 (덮어쓰기)
```

### 09:50 — 자동 정규 스크리닝

```
_schedule_screening 트리거
  → _on_screening() (async)
  → screener.run_manual(_manual_codes) (async, KIS API 호출)
  → list[StockCandidate] 반환 (필터 통과 종목 전부, top_n 슬라이싱 없음)
  → Coordinator.start_screening(targets, is_final=True)
  → N Watcher 생성 + _t2_callback 주입 + 가동
  → 각 Watcher: KIS WebSocket subscribe
  → state = WATCHING
  → _screening_done = True (이후 추가 호출 차단)
```

### 09:55 ~ 11:00 — 매매 윈도우

```
실시세 수신 (KIS WebSocket)
  → Coordinator.on_realtime_price(code, price, ts)
  → not w.is_terminal 체크 → Watcher.on_tick(price, ts, futures_price)

[Watcher.on_tick] (terminal 종목은 두 겹 차단)
  → _handle_watching → 신고가 추적, 1% 하락 시 _fire_trigger → TRIGGERED
    (W-12-rev2: target_buy1/2_price 와 hard_stop_price_value 가 호가 단위 보정)
  → _handle_triggered
    → A: ts >= entry_deadline (10:55) → SKIPPED
    → B: timeout 20분 (W-13) → SKIPPED
    → C: 신고가 갱신 → WATCHING 복귀 (재트리거 가능)
    → D: _evaluate_target → READY / PASSED / DROPPED

[Coordinator._process_signals] (이벤트 루프 polling)
  → active 청산 신호 처리
  → 진입 윈도우 체크 (W-11e: _is_in_entry_window, 10:00~10:55)
  → yes_watchers 수집 (is_yes + not is_terminal)
  → 첫 매매 tiebreaker: distance_to_buy1 가장 가까운 (현행 유지)
  → _execute_buy → Trader.place_buy_orders
    (W-11e: last-line defense, _now >= 11:00 이면 발주 거부)
  → 체결 콜백: Watcher.on_buy_filled
    → buy1 체결 → ENTERED
    → buy2 체결 → _t2_callback(self.code, ts) (try/except 격리)

[Coordinator._on_t2] (T2 콜백, 동기)
  → active 일치 확인 (방어)
  → _is_in_entry_window 체크
  → _try_reserve_at_t2:
    - YES 후보 수집 (is_yes + not is_terminal + active 제외)
    - _tiebreaker_for_next: KOSPI 3.8% / KOSDAQ 5.6% + 눌림폭 최대
    - ReservationSnapshot 생성
  → _reserved_snapshot 에 저장

[Watcher._handle_entered] (매 tick, ENTERED 상태)
  → _check_timeout, _check_futures_drop, 하드 손절, 목표가
  → 충족 시 _emit_exit → _exit_signal_pending = True

[Coordinator._process_signals → _execute_exit]
  → trader.execute_exit (실제 청산 발주)
  → await _exit_callback(watcher) = main.py._on_exit_done
  → _on_exit_done 완료 후 _process_signals 가 self._active_code = None

[main.py._on_exit_done] (W-11e)
  1. P&L 기록
  2. TradeRecord + DB 저장
  3. 손실 한도 체크
  4. multi_trade.enabled 가드
  5. 잔고 재조회 + Coordinator.set_available_cash
  6. trader.reset
  6.5. (W-11e 신규)
       self._coordinator._active_code = None
       await self._coordinator.handle_t3(now_kst())
  7. 대시보드 동기화

[Coordinator.handle_t3] (W-11d, async)
  → _is_in_entry_window 체크 (벗어나면 예약 폐기 + return)
  → 예약 존재 시:
    → _verify_reservation_at_t3 (5항목 엄격 재확인)
    → 통과 → _execute_buy (예약 진입)
    → 실패 → 예약 폐기 + fallthrough 재판정
  → 재판정 (예약 없음 또는 fallthrough):
    → yes_watchers 수집
    → _tiebreaker_for_next
    → 통과 → _execute_buy
    → 통과 0 → 포기

→ 두 번째 매매가 ENTERED 되면 또 T1/T2/T3 가능 → N차 매매 연쇄
→ 진입 윈도우 (10:00~10:55) 끝나면 자연스럽게 멈춤
→ daily_loss_limit_pct 3% 도달 시 신규 발주 차단

TRIGGERED 후 20분 미체결 → _check_high_confirm_timeout → SKIPPED
```

### 10:55 — on_buy_deadline

```
_schedule_buy_deadline 트리거
  → Coordinator.on_buy_deadline
  → 모든 Watcher: ENTERED 아닌 상태 → SKIPPED
  → Telegram: 매수 마감 알림
```

### 11:00 — Trader last-line defense (W-11e)

```
이 시각 이후 어떤 경로로도 _execute_buy 가 호출되면
  → Trader.place_buy_orders 진입부에서:
    _now >= dtime.fromisoformat(repeat_end='11:00')
  → logger.error("CRITICAL: 매매 철학 시한 위반 시도 ... 발주 거부")
  → return (발주 차단)
```

### 11:20 — on_force_liquidate

```
_schedule_force_liquidate 트리거
  → Coordinator.on_force_liquidate
  → 모든 Watcher: ENTERED → 강제 매도 (시장가)
  → Trader.execute_exit (강제)
  → 체결 후 EXITED
```

### 장 마감 — 일일 리포트

```
_schedule_market_close 트리거
  → _build_trade_record (각 Watcher 별)
  → Database.save_trade + save_daily_summary
  → Notifier.notify_daily_report
  → Telegram: 일일 리포트
```

---

## 7. 개발 / 운영 환경

### 개발 환경 (수석님 PC)
- **OS**: Windows 11
- **Shell**: Git Bash (PowerShell 아님)
- **Python**: 3.11 (venv)
- **IDE**: VSCode + Claude Code / Cowork
- **AUTOTRADE 경로**: `C:\Users\terryn\AUTOTRADE`
- **Obsidian vault**: `C:\Users\terryn\Documents\Obsidian\AUTOTRADE`

### 운영 환경 (Oracle Cloud)
- **IP**: 134.185.115.229
- **스펙**: Oracle Cloud Seoul E2.1.Micro (1 vCPU, 956Mi RAM + 4GB swap)
- **OS**: Ubuntu 22.04 LTS
- **경로**: `/home/ubuntu/AUTOTRADE`
- **Python**: 3.11 venv (`~/AUTOTRADE/venv`)
- **사용자**: `ubuntu`
- **git**: 없음 (수동 scp 배포)

### 배포 패턴
```bash
# 로컬 → 운영
ssh ubuntu@134.185.115.229
cd /home/ubuntu/AUTOTRADE
mkdir -p backups/YYYY-MM-DD-pre-RXX
cp config/strategy_params.yaml backups/YYYY-MM-DD-pre-RXX/
# ... (배포 대상 파일 모두)
exit

# 로컬에서 (Git Bash)
cd /c/Users/terryn/AUTOTRADE
scp src/utils/price_utils.py ubuntu@134.185.115.229:/home/ubuntu/AUTOTRADE/src/utils/
scp config/strategy_params.yaml ubuntu@134.185.115.229:/home/ubuntu/AUTOTRADE/config/
# ... (모든 변경 파일)

# 운영 검증
ssh ubuntu@134.185.115.229
cd /home/ubuntu/AUTOTRADE
python3 -m py_compile src/core/watcher.py src/core/trader.py src/main.py src/utils/price_utils.py
python3 -c "from src.main import AutoTrader; ..."  # import 체인
python3 -c "from config.settings import StrategyParams; ..."  # 파라미터 확인

# 재시작
sudo systemctl restart autotrade
sudo systemctl status autotrade --no-pager
tail -30 logs/autotrade_$(date +%Y-%m-%d).log
```

### 자동화
- **cron 06:40 KST**: `send_dashboard_url.py` 실행 → service 시작 → URL 텔레그램 발송
- **systemd**: `autotrade.service` (enabled, 재시작 시 자동)

### .env 변수 (12개)

| 변수 | 설명 | 기본값 |
|---|---|---|
| KIS_APP_KEY | KIS API 앱 키 | — |
| KIS_APP_SECRET | KIS API 앱 시크릿 | — |
| KIS_ACCOUNT_NO | KIS 실거래 계좌번호 | — |
| KIS_ACCOUNT_NO_PAPER | KIS 모의투자 계좌번호 | — |
| USE_LIVE_API | 실거래 API 사용 여부 | true |
| DRY_RUN_CASH | DRY_RUN 가상 예수금 | 50000000 |
| TRADE_MODE | dry_run / paper / live | dry_run |
| TELEGRAM_BOT_TOKEN | 텔레그램 봇 토큰 | — |
| TELEGRAM_CHAT_ID | 알림 수신 채팅 ID | — |
| DASHBOARD_PORT | 대시보드 포트 | 8503 |
| DASHBOARD_ADMIN_TOKEN | 대시보드 관리자 토큰 | — |
| LOG_LEVEL | 로그 레벨 | INFO |

### 소스 관리 (git workflow)

**원격 저장소**: 정확한 호스트는 vault 참조 (보안상 이 문서에 노출 안 함)

**브랜치 구조**:
- `main` — 안정 브랜치, 운영 sync 기준
- `feature/dashboard-fix-v1` — 작업 브랜치 (R-08 작업 누적, 향후 정리/rename 가능)
- 단일 개발자 워크플로 → fast-forward merge 가능

**브랜치 전략**:
- 작업은 feature 브랜치 (`feature/*`) 에서 수행 + commit
- main 으로 fast-forward merge (diverge 거의 없음)
- 양쪽 origin push (main + feature 둘 다)
- merge conflict 거의 없음 (단일 개발자)

**commit 메시지 패턴**:
- `feat: <영역> <내용>` — 기능 추가 (예: R-08 매매 명세 구현)
- `fix: <영역> <내용>` — 버그 수정
- `chore: <내용>` — 위생 / 인프라 / .gitignore 정리
- `docs: <내용>` — 문서 (CLAUDE.md vault 등)

**운영 ↔ git 워크플로우**:

```
1. 로컬 작업 (Claude Code 명령서 기반)
2. 로컬 검증 (py_compile, import 체인, grep)
3. 로컬 → 운영 scp 배포 (수동, Git Bash)
4. 운영 검증 (import + StrategyParams 로드)
5. systemctl restart autotrade
6. git commit (수석님 직접, 자동 X)
7. main fast-forward merge
8. origin push (main + feature 둘 다)
```

**중요**: 운영 서버에는 git 이 없음 (수동 scp 배포). 따라서:
- git 은 *소스 보존 + 추적용*
- 운영 동기화는 *scp 직접 배포*
- **운영 ↔ git 비대칭 위험 영역**: 운영 서버에만 있는 파일이 git 에 없으면 다른 환경 재구축 시 누락. R-08 정리 시 발견된 경우:
  - `scripts/send_dashboard_url.py` (운영 cron 핵심) — 추가 commit 으로 해소
  - `docs/autotrade.service.*` (systemd 정의) — 추가 commit 으로 해소

**`.gitignore` 주요 패턴** (R-08 정리 후):

```gitignore
# 보안
.env
token_paper.json
token_live.json
token_*.tmp

# 런타임
data/
logs/

# Python
__pycache__/
*.pyc
*.pyo
venv/
.venv/
dist/
build/

# IDE
.vscode/
.idea/
*.swp

# Claude / Agents
.claude/
.agents/

# Work backups (R-08 추가)
*.pre-W*
*.pre-R*
*.bak
*.bak.*

# Temporary files (R-08 추가)
*.patch
_test_names.txt
setup.zip
skills-lock.json

# Vault backups (R-08 추가, 대용량)
Obsidian/*.tar.gz
Obsidian/*.zip
```

**git 추적 vs 비추적 원칙**:
- ✅ 추적: 소스 코드 (`src/`), 설정 (`config/`), 문서 (`CLAUDE.md`, `docs/W-N_*.md`), 운영 인프라 (`scripts/`, `docs/autotrade.service.*`), `.gitignore`
- ❌ 비추적: 보안 (.env, token), 런타임 (data, logs, __pycache__), 작업 백업 (`.pre-*`, `.bak`), vault 백업 (대용량)

**3-way 동기화 원칙**: 모든 변경 후 다음 3 영역이 일치해야 함:
1. 운영 서버 (`/home/ubuntu/AUTOTRADE/`)
2. 로컬 작업폴더 (`C:\Users\terryn\AUTOTRADE\`)
3. git origin (main + feature 양쪽)

→ 한 영역만 변경하면 다른 영역과 비대칭 발생. 비대칭은 향후 사고의 시드.

---

## 8. 협업 규칙 (수석님 ↔ Claude)

### §5.6 협업 규칙 — 8 원칙

> 이 섹션은 vault 의 *원본 §5.6* 기반. 수석님이 직접 관리.

1. **사실 base 우선** — 추측 금지. 코드 / 로그 / 문서 직접 확인. 메모리/추측을 사실로 단정하지 말 것.
2. **진단 후 작업** — M-N (미션) → 명령서 (W-N) → 검증 → 보고 순서
3. **멈춤 조건 명시** — 예상 외 결과 시 즉시 멈춤 + 보고. 자동 수정 금지
4. **화이트리스트 원칙** — 명령서는 *수정 가능한 파일만* 명시. 블랙리스트는 손대지 말 것
5. **원자 작업** — 한 작업 = 한 영역. 여러 영역 동시 수정 금지
6. **검증 필수** — 모든 작업 후 자동 검증 (import / grep / 단위 테스트)
7. **추가 발견 기록** — 작업 중 발견한 빈틈 / 사고 가능성 → [발견] 섹션에 기록
8. **통합 관점** — W-N 의 좁은 영역도 *전체 시스템 맥락에서* 영향 검토

### 표준 금지 조항 5개 (모든 명령서에 자동 포함)

R-08 진행 중 학습된 안전 조항. 모든 W-N / M-N 명령서 끝에 표준 포함:

1. **운영 서버 접근 0건** — 명령서 안에 ssh / scp / rsync 명령 0건
2. **운영 서버 배포 금지** — `134.185.115.229` 또는 `/home/ubuntu/AUTOTRADE/` 어떤 변경도 금지
3. **git commit 자동 실행 금지**
4. **로컬 AutoTrader 실제 실행 금지** (검증은 import / grep / 단위 테스트만)
5. **systemd / 데몬 재시작 금지**

이 5개는 명령서 권한 (Code) 과 운영 권한 (수석님) 의 분리를 보장. 운영 환경 변경은 항상 수석님 직접 판단 + 직접 실행.

### 명령서 작성 패턴

```
[미션] <목적>
[제약] 화이트리스트 / 블랙리스트
[원칙] §5.6 + 자동 진행 모드 여부

[배경 — 사실 base]
<현재 상태, 진단 결과, 사용 가능한 메서드>

[화이트리스트]
<수정 가능 파일 명시>

[블랙리스트]
<손대지 말 영역>

[작업 0] 사전 백업
[작업 1] 사실 base 확인
[작업 2] ...
...

[검증 실패 시 — 멈춤 조건]
<어떤 경우 멈춤 + 보고>

[모호한 케이스]
<사전 결정 또는 멈춤 기준>

[보고]
<Code 가 보고할 형식>

[표준 금지 조항]
- 5개 항목
```

### 진단 패턴 — M-N

```
[미션] M-N — <진단 대상>
[제약] 코드 수정 0줄, 읽기만, 로컬만

[작업 1] <식별>
[작업 2] <본문 확인>
...

[검증]
<판정 기준>

[보고]
<형식>

[표준 금지 조항]
- 5개
```

### Claude 의 §5.6 원칙 1 위반 패턴 (R-08 학습)

R-08 진행 중 Claude 가 자주 위반한 패턴 (수석님이 매번 짚어줌):

1. **메모리/이전 대화의 값을 사실 base 로 단정** — 코드 grep 으로 재확인 안 함
2. **명세 사실 base 무시** — 코드는 grep 으로 확인하면서 명세는 머릿속 가정으로 처리
3. **무의식적 가정** — "재매수 = 사고" 같은 도메인 가정을 명세에 근거 없이 적용
4. **컨텍스트가 길어질수록 사실 base 확인 게을리짐** — 특히 documents 블록 내용 누락
5. **답변 모호 시 좁게 해석** — 양쪽 답이 가능한 경우에도 한쪽으로 단정

대응:
- 모든 결정 전에 "이 결정의 사실 base 가 무엇인가" 자문
- 모호한 답변 시 즉시 명시 재요청
- 명령서 작성 시 "이 명령서의 명세 근거는 무엇인가" 자문

---

## 9. 4 페르소나

### 페르소나 1 — 펀드매니저
- **역할**: 매매 명세 / 매매 철학 결정권자
- **관심사**: 매매 로직, 리스크 파라미터, 자본 배분
- **호출 시점**: R-N redesign 시 매매 명세 변경 논의

### 페르소나 2 — 시스템 엔지니어 (SE)
- **역할**: 아키텍처 / 코드 품질 결정권자
- **관심사**: 모듈 분리, 의존성, 테스트, 리팩토링
- **호출 시점**: 코드 작성 / redesign 시 구조 결정

### 페르소나 3 — DBA 센터장
- **역할**: 데이터 / 스키마 / 저장소 결정권자
- **관심사**: SQLite 스키마, TradeRecord, 마이그레이션
- **호출 시점**: 저장소 / 데이터 이슈 발생 시

### 페르소나 4 — 보안팀장
- **역할**: 보안 / 권한 / 접근 제어 결정권자
- **관심사**: KIS API 키, Telegram 봇 토큰, 대시보드 admin token, 운영 환경 접근
- **호출 시점**: 인증 / 권한 / 민감 데이터 이슈 발생 시

### 호출 패턴
수석님이 명시적으로 *"<페르소나> 관점에서 이 영역을 평가해줘"* 라고 할 때, 또는 영역이 명확할 때 Claude 가 자동 적용.

추가로 **베테랑 주식 시장 리서치/분석가/펀드매니저** 역할 — AUTOTRADE 매매 전략 논의 시 Claude 의 기본 역할. 수석님 인프라 지식과 피어 레벨, 매매/금융 도메인은 멘토 레벨에서 대화하며 *"이 전략에 진짜 알파가 있는가"* 를 능동적으로 도전.

---

## 10. W / M / R 워크플로우

### 접두사 체계

- **R-N** (Redesign): 큰 영역 재설계. 여러 W-N 단계 포함
- **W-N** (Work): 단일 작업 단위. 명령서 기반
- **M-N** (Mini): 진단 미션. 코드 수정 없이 읽기만
- **ISSUE-N**: 사고 / 버그 번호 (vault 에서 관리)

### R-08 진행 (이번 세션, 2026-04-09 ~ 04-10)

```
R-08 (R-07 빈틈 정리 + 두 번째 매매 + 호가 단위 + 위생)
├── M-14, M-15, M-16, M-17 (Coordinator 인벤토리, 다양한 진단)
├── W-10b setup_logger 운영 배포
├── W-11a yaml R-08 파라미터 (top_n 삭제, profit_only false 등)
├── W-11b Screener/Coordinator/main.py 정리
├── W-11c Watcher _t2_callback + get_pullback_pct
├── W-11c-hotfix is_yes @property READY only 원복
├── M-18 호가 단위 빈틈 진단
├── W-12 (1차 → rev2 → rev3 검토 → rev2 재확정) 호가 단위 보정
│   └── price_utils.py 신규 + screener.py + watcher.py
├── M-19-pre timeout 사실 base 확정
├── W-13 high_confirm_timeout_min 10→20
├── W-11d Coordinator 두 번째 매매 핵심 (+387 라인)
│   ├── ReservationSnapshot dataclass
│   ├── _on_t2, _try_reserve_at_t2, _tiebreaker_for_next
│   ├── _verify_reservation_at_t3, _is_in_entry_window
│   ├── handle_t3 (async)
│   └── start_screening is_final 파라미터
├── M-20 _execute_exit + _on_exit_done 실행 순서 + EXITED tick 처리 진단
├── W-11e main.py + watcher.py + trader.py 통합
│   ├── _on_exit_done 에 handle_t3 위임 (6.5 단계)
│   ├── _process_signals 의 _is_after_buy_deadline → _is_in_entry_window
│   ├── _is_after_buy_deadline 메서드 제거
│   └── trader.py place_buy_orders last-line defense
├── W-14 매수 비율 변경 (KOSPI 2.5/3.5, KOSDAQ 3.5/5.5)
├── M-21 settings.py default ↔ yaml 전수 비교
└── W-15 settings.py default 6건 yaml 동기화
```

운영 배포: 7 파일 + 1 신규 (price_utils.py) → 4/10 운영 서버 정상 가동 (DRY_RUN).

### 명령서 전달 패턴

```
(가) 짧은 작업: 채팅 직접 전달
(나) 긴 작업: `docs/W-N_*.md` 파일 → Code 에게 경로 전달
(다) 진단: M-N 명령어 채팅 직접 전달
```

---

## 11. 이력 + 사고 이력

### R-07 종결 (2026-04-09, v2 시점)
- Watcher + WatcherCoordinator 중심 (8 state)
- 3종목 동시 + single active rotation
- 매매 명세 (09:50 / 10:55 / 11:20 / -2.7%/-3.3% / -4.0%/-5.0%)
- W-09 로 dashboard ISSUE-035 정정
- 매매 0건 baseline day 통과 (시장 조건 미충족 = 정상)

### R-08 진행 (2026-04-09 ~ 04-10, 이번 세션)

**R-07 재평가**: R-07 은 "종결" 이 아니라 "운 좋게 테스트되지 않았던 미완성 상태". 5건 빈틈 발견:
1. 두 번째 매매 예약 구조 부재
2. setup_logger() 미호출
3. dashboard `.stock` 잔존 참조
4. ISSUE-E1 rapid restart transient
5. multi_trade.repeat_start/end 미구현

**오늘 추가 발견** (R-08 진행 중):
6. 호가 단위 보정 누락 (수석님 화면 발견)
7. (종결) 삼성전기 SKIPPED 사고 → timeout 10분이 너무 짧음 (코드 버그 아님)
8. slippage_ticks 미사용 (yaml 정의, 코드 0건)
9. R-08 재매수 명세-구현 갭 (M-20 발견)
10. settings.py default ↔ yaml 6건 불일치 (M-21 전수 발견)
11. Pydantic validator 부재 (시각 필드, W-15 작업 중 발견)

**R-08 매매 명세 변경**:
- 유니버스: 수동 입력 개수 제한 없음
- 09:50 단일 정규 스크리닝 + 09:50 이전 수동 여러 번 허용
- 두 번째 매매 예약 구조 (T1/T2/T3)
- 진입 윈도우 10:00~10:55 (모든 신규 진입)
- 매수 비율 변경 (W-14)
- timeout 20분 (W-13)
- 동일 종목 당일 재매수 허용 (코드 갭은 백로그)

### 주요 사고 이력

| ISSUE | 영역 | 발견 시점 | 상태 |
|---|---|---|---|
| ISSUE-030 | 대시보드 평가금액 = 예수금 표시 | W-07b | R-08 대기 |
| ISSUE-035 | dashboard set-targets + run-manual-screening KIS API 컨텍스트 충돌 | 2026-04-09 오전 | W-09 해소 |
| W-07b 검증 빈틈 | `at._monitors` 패턴만 검사, 잔존 | W-09 작업 중 | 해소 |
| t.stock.* 잔존 | W-07a 후 main.py 정정 누락 | 2026-04-09 09:22 | 해소 |
| `.stock` 잔존 참조 | screening broadcast 경로 (StockCandidate) | 2026-04-09 09:22 | R-08 백로그 #25 |
| 호가 단위 미보정 | 매수가/손절가가 KRX 호가 단위 위반 | 2026-04-10 (수석님 화면) | W-12-rev2 해소 |
| 삼성전기 SKIPPED | 트리거 후 10분 SKIPPED, 시장은 19분에 도달 | 2026-04-10 운영 | W-13 timeout 20분 (빈틈 아님) |

### KIS API 크로스루프 위험 영역

| 위치 | 호출 | 위험도 |
|---|---|---|
| dashboard/app.py:309 | `api.get_current_price()` in `api_search_stock` | 🔴 ISSUE-035 패턴 (수석님 미사용 합의) |

> 나머지 20+ KIS API 호출은 모두 AutoTrader event loop 내부 — 안전.

### 환경 사고 이력

| # | 사고 | 해소 |
|---|---|---|
| E1 | 토큰 캐시 read-only filesystem | 자연 해소 (디스크 19%, 쓰기 정상) |
| E2 | ISSUE-035 | W-09 |
| E3 | KIS API FHKST01010100 일시 에러 | 재시도 성공 |
| E4 | systemd SIGTERM (원인 미지) | R-08 백로그 |

---

## 12. R-08 빈틈 카탈로그 + 백로그

### R-08 빈틈 (전체 11건)

| # | 빈틈 | 위험도 | 상태 | 해결 작업 |
|---|---|---|---|---|
| 1 | 두 번째 매매 예약 구조 부재 | 🔴 높음 | ✅ 해결 | W-11d (Coordinator 핵심) |
| 2 | setup_logger() 미호출 | 🟡 중간 | ✅ 해결 | W-10b |
| 3 | dashboard `.stock` 잔존 참조 (app.py:356) | 🟡 중간 | 백로그 | R-08 #25 |
| 4 | ISSUE-E1 rapid restart transient | 🟢 낮음 | ✅ 흡수 | 방어 설계 |
| 5 | multi_trade.repeat_start/end 미구현 | 🔴 높음 | ✅ 해결 | W-11d/e |
| 6 | 호가 단위 보정 누락 | 🔴 높음 | ✅ 해결 | W-12-rev2 |
| 7 | (종결) 삼성전기 SKIPPED — 빈틈 아님 | — | ✅ 종결 | W-13 (timeout 20분) |
| 8 | slippage_ticks 미사용 | 🟢 낮음 | 백로그 | 매매 의도 확인 필요 |
| 9 | R-08 재매수 명세-구현 갭 | 🟡 중간 | 백로그 | EXITED Watcher tick 차단 |
| 10 | settings.py default ↔ yaml 6건 불일치 | 🟡 중간 | ✅ 해결 | W-15 |
| 11 | Pydantic validator 부재 (시각 필드) | 🟢 낮음 | 백로그 | str 타입 직접 할당 |

**해결**: 6건 (#1, #2, #5, #6, #7, #10) + 흡수 1건 (#4)
**백로그**: 4건 (#3, #8, #9, #11)

### R-08 백로그 (작업 영역)

| # | 항목 | 우선순위 |
|---|---|---|
| #3 (R-08 #25) | dashboard `.stock` 잔존 참조 — 원인 지점 외부 (main._on_screening / Coordinator.start_screening / DashboardState 후보) | 중 |
| #8 | slippage_ticks 의도 확인 (구현 / 삭제 / 잔존 결정) | 낮 |
| #9 | R-08 재매수 명세-구현 갭 — EXITED Watcher 리셋 또는 새 인스턴스 | 중 |
| #11 | Pydantic validator 추가 (entry_deadline, force_liquidate_time) | 낮 |
| #14 | ISSUE-030 대시보드 평가금액 (잔여) | 중 |
| #19 | api_search_stock ISSUE-035 정정 | 중 |
| #20 | stock_master.json 자동 갱신 | 중 |
| 신규 | Watcher 기반 유닛 테스트 신규 작성 | 중 |

### 환경 영역

| # | 항목 | 우선순위 |
|---|---|---|
| E1 | 토큰 캐시 read-only 재발 감시 | 중 (자연 해소) |
| E3 | KIS API 재시도 로직 강화 | 중상 |
| E4 | systemd SIGTERM 원인 식별 (OOM?) | 중 |

### 8 카테고리 실거래 위험 (R-04 원본)

> vault 의 8 카테고리 원본 확인 후 갱신 필요. 현재까지 알려진 영역:

1. 슬리피지 / 호가 단위 → ✅ W-12-rev2 (호가 단위), 🟢 슬리피지 백로그 #8
2. 주문 거부 처리 → R-08 영역
3. 잔고 정확성 → R-08 영역
4. 부분 체결 → R-08 영역
5. 네트워크 끊김 / 재시작 → R-08 영역 (E4)
6. 수수료 / 세금 → R-08 영역
7. VI / 거래정지 시나리오 → R-08 영역
8. 모니터링 / 알람 강화 → R-08 영역

---

## 13. 향후 확장 방향

### 단기 (R-08 백로그 정리, 실거래 전)
- 빈틈 #3, #8, #9, #11 정리
- ISSUE-030 대시보드 평가금액
- api_search_stock ISSUE-035 정정
- Watcher 기반 유닛 테스트 신규
- 8 카테고리 위험 해소 (우선순위 순)
- DRY_RUN 검증 → LIVE 전환 결정

### 중기 (R-09 이후, 도메인 확장)
- 매매 명세 개선 (백테스트 기반)
- 복수 전략 지원 (현재 단타 1개 → 다중 전략, Strategy plug-in 패턴)
- Account Executor 구조 (계좌별 분리)
- 실시간 리스크 관리 (VaR, drawdown 제한)
- 성과 분석 대시보드 (일별 / 주별 / 월별)
- 알림 고도화 (텔레그램 인터랙티브)

### 장기
- Phase α-1: 클라우드 lift-and-shift + 멀티 사용자 UI
- Phase α-2: Strategy plug-in 패턴 + Account Executor
- Phase β: 두 번째 KIS 계좌 활성화, Signal Hub fan-out
- 백테스트 엔진 신규 구축
- 다른 거래소 지원 (NASDAQ, HKEX)
- AI 기반 종목 선정 보조
- 포트폴리오 관리 (다중 계좌)

### 단계적 자본 증가 계획

> vault 의 자본 증가 계획 원본 확인 후 갱신 필요.

---

## 14. vault 참조 원칙

### vault 위치
`C:\Users\terryn\Documents\Obsidian\AUTOTRADE`

### vault 와 CLAUDE.md v3 의 관계
- **CLAUDE.md v3 = 앵커** — 신규 채팅 시 한 번에 읽는 self-contained 명세
- **vault = 상세 자료** — 각 영역의 심화 내용, 결정 근거, ISSUE 상세
- **우선순위**: CLAUDE.md v3 가 *현재 유효한 기준*. vault 는 *참조용*

### vault 이식 예정 영역
- 매매 철학 / R-04 의사결정 원본
- 4 페르소나 정의 원본
- §5.6 협업 규칙 원본 (8 원칙)
- 8 카테고리 실거래 위험 분석
- 단계적 자본 증가 계획
- ISSUE 번호 체계 전수 목록

---

## 15. 금지 사항

### 절대 금지
1. **DRY_RUN 없이 실거래 가동** — 수석님 명시 결정 전까지 LIVE 모드 자동 전환 금지
2. **매매 명세 임의 변경** — yaml 동결값. 변경 시 새 R-N redesign 필수
3. **Coordinator / Watcher / Trader 의 상태 전이 로직 임의 변경** — 검증 없이 금지
4. **운영 환경 직접 코드 수정** — 로컬 → 검증 → 배포 순서 엄수

### 작업 시 금지 (§5.6)
5. **추측 기반 작업** — 사실 base 우선
6. **광범위 검증 없이 단일 파일 정정** — 사용처 grep 필수
7. **화이트리스트 외 파일 수정** — W-N 명령서의 화이트리스트 엄수
8. **commit 자동 실행** — 수석님 결정
9. **테스트 없는 배포** — 로컬 검증 → 운영 배포 → 운영 검증 3단계
10. **중간 멈춤 없는 자동 진행** — 멈춤 조건 충족 시 즉시 보고

### 표준 금지 조항 (R-08 학습, 모든 명령서 표준 포함)
11. **운영 서버 접근 0건** (Code 측)
12. **운영 서버 배포 금지** (Code 측)
13. **git commit 자동 실행 금지** (Code 측)
14. **로컬 AutoTrader 실제 실행 금지** (Code 측)
15. **systemd / 데몬 재시작 금지** (Code 측)

### 문서 금지
16. **vault 의 원본 문서 삭제** — 이식 후에도 백업 보존

---

## 부록 A — R-08 구현 작업 요약 (2026-04-09 ~ 04-10)

### 진단 미션 (M-N)

| # | 미션 | 내용 |
|---|---|---|
| M-14 | watcher.py 인벤토리 (1차) | 좁은 범위 |
| M-15 | watcher.py 인벤토리 (2차) | is_yes 기존 정의 누락 |
| M-16 | watcher.py 추가 인벤토리 | top_n_candidates 누락 |
| M-17 | Coordinator 전체 인벤토리 | 동시성 분석 + 메서드 매트릭스 |
| M-18 | 호가 단위 빈틈 진단 | 매수가/손절가 KRX 위반 확인 |
| M-19-pre | timeout 사실 base | high_confirm_timeout 10분 = 정확히 600초 |
| M-20 | _execute_exit + _on_exit_done 실행 순서 | EXITED tick 처리, _execute_buy active 가드 |
| M-21 | settings.py ↔ yaml 전수 비교 | 6건 불일치 발견 |

### 작업 (W-N)

| # | 작업 | 영역 | 결과 |
|---|---|---|---|
| W-10b | setup_logger 운영 배포 | logger.py | 운영 배포 완료 |
| W-11a | yaml R-08 파라미터 | strategy_params.yaml | top_n 삭제, profit_only false 등 |
| W-11b | Screener/Coordinator/main 정리 | 4 파일 | R-08 명세 반영 |
| W-11c | Watcher T2 콜백 | watcher.py | _t2_callback + get_pullback_pct |
| W-11c-hotfix | is_yes @property 원복 | watcher.py | READY only |
| W-12 (1→rev2→rev3→rev2) | 호가 단위 보정 | price_utils.py 신규 + watcher.py + screener.py | KRX 2023 개편 후 기준 |
| W-13 | high_confirm_timeout_min | yaml | 10→20 |
| W-11d | Coordinator 두 번째 매매 핵심 | watcher.py | +387 라인 |
| W-11e | main + watcher + trader 통합 | 3 파일 | handle_t3 위임, 진입 윈도우 통합, last-line defense |
| W-14 | 매수 비율 변경 | yaml | KOSPI 2.5/3.5, KOSDAQ 3.5/5.5 |
| W-15 | settings.py default 동기화 | settings.py | 6건 yaml 동기화 |

### 운영 배포 (2026-04-10)

**배포 대상 7 파일 + 1 신규**:
1. `config/strategy_params.yaml` (W-11a + W-13 + W-14)
2. `config/settings.py` (W-11b + W-15)
3. `src/utils/price_utils.py` (W-12-rev2 신규)
4. `src/core/screener.py` (W-11b + W-12-rev2)
5. `src/core/watcher.py` (W-11b + W-11c + W-11c-hotfix + W-11d + W-12-rev2 + W-11e)
6. `src/core/trader.py` (W-11e)
7. `src/main.py` (W-11b + W-11e)

**검증 결과**:
- py_compile 5 파일 ✅
- ReservationSnapshot 9 필드 ✅
- WatcherCoordinator W-11d 6 신규 메서드 ✅
- handle_t3 async / _on_t2 sync ✅
- start_screening is_final keyword-only ✅
- trader.py last-line defense ✅
- main.py T3 위임 ✅
- _is_after_buy_deadline 삭제 ✅
- _is_in_entry_window 사용 ✅
- settings W-13/W-14/W-15 8 값 ✅
- price_utils 18 함수 ✅

**가동 상태**: active (running), PID 60305, DRY_RUN 모드, 16:05 KST 기동

### git commit 이력 (R-08 세션, 2026-04-10)

| commit | 메시지 | 변경 |
|---|---|---|
| `59d41aa` | feat: R-08 매매 명세 구현 (W-11~W-15) | 8 파일, +651 / -120 |
| `d64b90c` | docs: CLAUDE.md v3 + vault 진행 메모 + dashboard_sim + 매뉴얼 정리 | 4 파일 |
| `09c1078` | chore: 운영 인프라 git 추적 + W-N 아카이브 + .gitignore 정리 | 11 파일, +2317 |

**최종 동기화 상태** (3-way 일치):
- 운영 서버 (Oracle Cloud): R-08 코드 가동 (DRY_RUN, PID 60305)
- 로컬 작업폴더 (`C:\Users\terryn\AUTOTRADE`): R-08 모든 작업 + CLAUDE.md v3.1
- git: `main` = `feature/dashboard-fix-v1` = `09c1078` (origin push 완료)

R-08 매매 명세 구현 + 운영 배포 + git 보존 모두 정합 상태 완료.

---

## 부록 B — Claude (Code) 대화 시 주의 사항

이 섹션은 신규 Claude 세션이 R-08 진행 중 발견한 자기 약점을 미리 인지할 수 있도록 작성.

### Claude 의 R-08 §5.6 원칙 1 위반 (8회 이상)

R-08 진행 중 Claude 가 자주 추측을 사실로 단정하여 수석님이 매번 정정한 패턴:

1. **호가 단위 단정** (W-12 작업 3회 반복) — 우리 코드가 틀렸다고 추측, 사실은 일부만 틀림
2. **timeout 10분 메모리 단정** — grep 으로 재확인 안 함
3. **W-11e 보고 빈 메시지 단정** — 컨텍스트의 documents 블록 못 봄
4. **재매수 = 사고 무의식 가정** — 명세 사실 base 확인 안 함
5. **결정 답변 좁게 해석** — "결정 1=B" 답을 "결정 1만" 으로 단정
6. **CLAUDE.md 갱신 정확도** — vault 원본 확인 없이 추정 작성

### 대응 원칙

- 모든 결정 전에 "이 결정의 사실 base 가 무엇인가" 자문
- 메모리/이전 대화의 값을 사실 base 로 단정 금지 → grep 재확인
- 컨텍스트가 길 때 마지막 200~300줄 우선 정독
- 답변 모호 시 즉시 명시 재요청, 좁은 해석 금지
- 명령서 작성 시 "이 명령서의 명세 근거는 무엇인가" 자문

### 수석님 작업 스타일

- **간결한 의사소통**: "내려" (배포), "1=B" (선택), 단음절 답
- **명시적 멈춤 조건 요구**: 자동 수정/추측 진행 금지
- **화이트리스트 엄수**: 명령서 외 영역 수정 시 즉시 정정 요청
- **사실 base 즉시 검증**: Claude 의 추측을 매번 짚음
- **범위 외 작업 절단**: 불필요한 아키텍처 가이드/결정 프레임워크 즉시 차단

---

**문서 끝 (v3, 2026-04-10)**
