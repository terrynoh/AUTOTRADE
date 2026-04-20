# CLAUDE.md — AUTOTRADE

> **목적**: 신규 Claude 세션이 AUTOTRADE 프로젝트의 정체성, 매매 철학, 아키텍처, 협업 규칙을 즉시 파악할 수 있는 self-contained 명세서.
>
> **버전**: vLive 2.1.1 (2026-04-20 사실 base 정정)
> **현재 단계**: R16 Phase 1 + Phase 2 B + W-SAFETY-1 + M-LIVE-02-FIX 완료 → Day 3 (2026-04-21) LIVE 본 검증

---

## 목차

1. 프로그램 정체성
2. 매매 명세 (R-11/R-12 반영, 동결본)
3. 아키텍처
4. 파일 구조
5. 자료구조 (핵심)
6. 개발 / 운영 환경
7. 협업 규칙 + 금지 사항
8. 백로그 + 향후 방향
9. vault 참조 원칙

---

## 1. 프로그램 정체성

### 이름
**AUTOTRADE** — 한국 주식 자동 매매 시스템 (단타)

### 운영자
수석님 (K수석). Bangkok 거주, 한국 시니어 소프트웨어 엔지니어. 강점: 시스템 엔지니어링. 학습 중: 매매/금융 도메인.

### 목적
KIS (한국투자증권) Open API 를 통해 KOSPI/KOSDAQ 종목을 *매일 장중 90분 윈도우* (09:50 ~ 11:20) 안에서 *자동 단타 매매*.

### 매매 철학 (2026-04-09 확정)

**상승 메커니즘**:
- 외인/기관 지지 (바닥 방어) → 개인 매수 유입 → 상승
- 개인 매수 유입이 진짜 상승의 동력

**시각별 의미**:
| 시각 | 의미 |
|------|------|
| 09:00~09:49 | 지지 형성 구간 (매매 대상 아님) |
| 09:49 | 지지 구조 확인 시점 (스크리닝, `screening_time`) |
| 09:50 | 신고가 감시 시작 (`new_high_watch_start`) |
| 10:00~10:55 | 신규 진입 발주 윈도우 |
| 10:55 | 매수 발주 마감 (`entry_deadline`) |
| 11:00 | 매매 철학 시한 (`repeat_end`, last-line defense) |
| 11:20 | 강제 청산 (`force_liquidate_time`) |

**보수적 방어**: 조건 미충족 시 매매 0건도 수용. 자본 보호 우선.

### 운영 모드

**R16 이후 LIVE 전용** (2026-04-17 전환):
- DRY_RUN / paper 모드 전면 폐기
- `TRADE_MODE` 환경변수 폐기 (`settings.trade_mode` property 는 "live" 반환, 로그 호환성 유지)
- `USE_LIVE_API`, `DRY_RUN_CASH`, `KIS_ACCOUNT_NO_PAPER` 환경변수 폐기
- 관련 모듈 3개 삭제: `fill_manager.py`, `cash_manager.py`, `simulator.py`

월요일 첫 실행으로 LIVE 검증 대기 중.

---

## 2. 매매 명세 (동결본)

> `config/strategy_params.yaml` 에 인코딩. 임의 변경 금지 — 변경 시 새 R-N redesign 필요.

### 시각 트리거

| 시각 | 이벤트 |
|------|--------|
| 06:35 | cron → `trading_day_start.sh` → `systemctl start autotrade` (평일, 거래일만) |
| 06:40 | cron → `trading_day_url.sh` → 텔레그램 대시보드 URL 발송 (평일, 거래일만) |
| 09:30~09:49 | 수석님 종목 입력 (대시보드) |
| 09:49 | 정규 스크리닝 (`is_final=True`) |
| 09:50 | 신고가 감시 시작 |
| 10:00~10:55 | 신규 진입 발주 윈도우 |
| 10:55 | entry_deadline (미매수 → SKIPPED) |
| 11:00 | repeat_end (last-line defense) |
| 11:20 | 강제 청산 |
| 장 마감 | 일일 리포트 텔레그램 발송 |

### 매수가 (R-11 Double/Single 분기, R-13 실시간 업데이트)

프로그램 순매수 비중에 따라 분기:

| 시장 | 비중 | 1차 매수가 | 2차 매수가 | 손절 |
|------|------|------------|------------|------|
| KOSPI | Double (≥10%) | 고가 -1.9% | 고가 -2.4% | -3.0% |
| KOSPI | Single (<10%) | 고가 -2.5% | 고가 -3.5% | -4.0% |
| KOSDAQ | Double (≥10%) | 고가 -2.9% | 고가 -3.9% | -4.4% |
| KOSDAQ | Single (<10%) | — | — | **매매 제외** |

**R-13 비중 실시간 업데이트**:
- 폴링: 1초 간격 REST (`get_program_trade`) — LIVE 전용 고정 (R16 이후 is_paper_mode 분기 제거)
- Double/Single 변경 시: `_recalc_prices()` 호출 → 매수가/손절가 재계산
- 1차 체결 후 비중 변경 시: `cancel_and_reorder_buy2()` 호출 → 2차 재발주
- 손절선 완화 (Double→Single) 시: 현재가가 기존 손절선 아래라도 홀딩 (의도된 동작)
- 손절선 강화 (Single→Double) 시: 즉시 새 (타이트한) 손절선 적용. 비중 상승 = 프로그램 유입 가속 신호이므로 손절도 타이트하게 관리가 매매 철학. ENTERED 상태에서 현재가가 새 손절선 아래면 즉시 DROPPED 발동 (의도된 동작, 2026-04-20 확정)

**호가 단위 보정**: KRX 2023-01-25 개편 기준. 매수가 floor, 손절가 ceil.

### 5 청산 조건

| 조건 | 임계값 |
|------|--------|
| 하드 손절 | Double/Single별 차등 |
| 타임아웃 | 매수 후 저점 갱신 후 20분 |
| 목표가 | (confirmed_high + post_entry_low) / 2 |
| 선물 급락 | KOSPI200 선물 -1% |
| 강제 청산 | 11:20 |

### R-12 리스크 관리 (전역)

| 조건 | 임계값 | 효과 |
|------|--------|------|
| 지수 급락 | 당일 선물 고점 대비 -1.5% | 신규 매수 차단 |
| 손절 횟수 | 일일 2회 도달 | 신규 매수 차단 |

### 다음 종목 매매 (T1/T2/T3)

- **T1**: 첫 종목 ENTERED
- **T2**: 첫 종목 buy2 체결 → 두 번째 매매 후보 예약 (진입 윈도우 10:00~10:55 내 YES 종목 중 tiebreaker 선정)
  - KOSPI: 눌림폭 ≥3.8% (`multi_trade.kospi_next_entry_max_pct`)
  - KOSDAQ: 눌림폭 ≥5.6% (`multi_trade.kosdaq_next_entry_max_pct`)
  - 필터 통과 종목 중 **눌림폭 최대** 종목 선정
- **T3**: 첫 종목 EXITED (매도 전량 체결 확정) → 예약 종목 5항목 재확인 후 진입
  - 재확인: ①watchers 존재 ②is_yes (READY) ③not is_terminal ④target_buy1_price 변경 없음 ⑤tiebreaker 조건 여전히 충족
  - 재확인 실패 시 → 예약 폐기 + 재판정 (YES 종목 중 tiebreaker 재적용)
- N차 매매 연쇄 가능. 진입 윈도우 (10:00~10:55) 끝나면 자연 종료.

### 주요 파라미터

**진입 관련**:
- `high_confirm_drop_pct`: 1.0 (1% 하락 시 TRIGGERED)
- `high_confirm_timeout_min`: 20 (TRIGGERED 후 미체결 timeout)
- `new_high_watch_start`: "09:55" (신고가 감시 시작)
- `entry_deadline`: "10:55" (매수 마감)

**Double/Single 매수가 차등** (R-11):
- `kospi_double_buy1_pct/buy2_pct`: 1.9 / 2.4
- `kospi_single_buy1_pct/buy2_pct`: 2.5 / 3.5
- `kosdaq_double_buy1_pct/buy2_pct`: 2.9 / 3.9
- `program_net_buy_ratio_double`: 10.0 (Double/Single 경계)

**손절**:
- `kospi_double_hard_stop_pct`: 3.0
- `kospi_single_hard_stop_pct`: 4.0
- `kosdaq_double_hard_stop_pct`: 4.4

**청산**:
- `timeout_from_low_min`: 20
- `timeout_start_after_kst`: "10:00"
- `futures_drop_pct`: 1.0
- `force_liquidate_time`: "11:20"

**다음 매매 (T2/T3)**:
- `multi_trade.enabled`: true
- `multi_trade.repeat_start`: "10:00"
- `multi_trade.repeat_end`: "11:00"
- `multi_trade.kospi_next_entry_max_pct`: 3.8
- `multi_trade.kosdaq_next_entry_max_pct`: 5.6

**리스크 관리** (R-12):
- `daily_loss_limit_pct`: 3.0
- `index_drop_halt_pct`: 1.5
- `max_hard_stops_daily`: 2
- `max_position_size_pct`: 100 (매매 가용 현금 = 주문가능금액 × %)

**예수금 관리** (R15-007, R16 신규):
- KIS `inquire-psbl-order` (TTTC8908R) 의 `nrcvb_buy_amt` (미수없는매수금액) 를 주 사용 필드로 채택
- 시작 시 + 매 청산 후 실시간 조회로 P&L 반영

---

## 3. 아키텍처

### 컴포넌트 개요

```
┌─────────────────────────────────────────────┐
│           AutoTrader (main.py)              │
│  전체 매매 루프 + 외부 I/O 오케스트레이션   │
│  R15-005: 체결통보 (H0STCNI0)                │
│  R15-007: 매수가능조회 (TTTC8908R)           │
│  Phase 2 B: Position invariant 검증          │
└─────────────────────────────────────────────┘
       │           │           │           │
       ▼           ▼           ▼           ▼
  Screener   Coordinator   Trader    Notifier
                  │
                  ├── Watcher (N instances)
                  │     상태: 8개 (WATCHING → ... → EXITED)
                  │     Phase 2 B: watcher.position (dual-write)
                  │
                  └── 두 번째 매매 예약 (ReservationSnapshot)
```

### 핵심 컴포넌트

| 컴포넌트 | 파일 | 책임 |
|----------|------|------|
| AutoTrader | main.py | 전체 루프, 스케줄링, _on_exit_done, 체결통보 라우팅, Position invariant 검증 |
| WatcherCoordinator | watcher.py | N Watcher 관리, single active rotation, T2/T3 예약, dual-write 라우팅 |
| Watcher | watcher.py | 단일 종목 수명주기 (8 상태), `position` 필드 (Phase 2 B) |
| Trader | trader.py | LIVE 주문 발주 + 체결통보 처리, `trader.position` 관리, last-line defense |
| Screener | screener.py | 수동 스크리닝 → StockCandidate 반환 (프로그램매매 필터 항상 적용) |
| RiskManager | risk_manager.py | 일일 손실, 지수 급락, 손절 횟수 추적 |
| StockMaster | stock_master.py | 종목 코드 ↔ 종목명 검증 (로컬 캐시) |
| Dashboard | dashboard/app.py | FastAPI 실시간 대시보드 |
| Notifier | notifier.py | Telegram 알림 |
| TradeLogger | storage/trade_logger.py | SQLite 거래 기록 + 일별 요약 |
| KISAPI | kis_api/kis.py | KIS REST + WebSocket (가격/선물/체결통보) + `get_buy_available()` |

### R15-005 LIVE 체결통보 (2026-04-17)

- WebSocket `H0STCNI0` 구독 → AES-256-CBC 복호화 → 26 필드 파싱
- 5 케이스 분기:
  - `RFUS_YN=1` → 거부 → trader.on_live_rejected
  - `CNTG_YN=1, RCTF_CLS=0` → 정상 접수 → trader.on_live_acknowledged
  - `CNTG_YN=1, RCTF_CLS=1/2` → 외부 정정/취소 감지 → critical 알림 (AUTOTRADE 는 정정/취소 발주 안 함)
  - `CNTG_YN=2, SELN_BYOV_CLS=02` → 매수 체결 → trader + Coordinator dual-write
  - `CNTG_YN=2, SELN_BYOV_CLS=01` → 매도 체결 → trader + Coordinator dual-write → 전량 체결 시 on_sell_complete 체인

### R15-007 매수가능조회 (2026-04-17)

- KIS `inquire-psbl-order` / `TTTC8908R` 엔드포인트
- 파라미터: `PDNO=""`, `ORD_UNPR=""`, `ORD_DVSN="01"` (시장가 증거금율 반영)
- 반환 dict: `buyable_cash` (nrcvb_buy_amt, **주 사용**), `ord_psbl_cash`, `ruse_psbl_amt`, `raw`
- 호출 시점 3곳:
  - `run()` 초기 예수금 확인 → `_initial_cash` / `_available_cash` 초기화 + TradeLogger.capital 갱신
  - `_on_exit_done` 청산 후 → 다음 매매 가용 예수금 갱신 (P&L 반영 기대)
  - `_ratio_updater` buy2 재발주 시 → 재발주 자금 계산

### Phase 2 B Position dual-write (2026-04-17)

- Watcher 에 `position: Optional[Position] = None` 필드 추가
- 체결통보 시 `trader.position` 과 `watcher.position` 양쪽 동시 갱신 (dual-write)
- `Watcher.on_buy_filled(..., *, order=None)` / `Coordinator.on_sell_filled(..., *, order=None)` 시그니처 확장
- "이번 체결분만 직접 누적" 패턴 (Position.add_buy() 는 부분체결 중복 문제로 미사용)
- `main._check_position_invariant()` 헬퍼 → 매수/매도 체결 직후 trader/watcher position 정합성 검증
- 불일치 시 critical 로그 + 텔레그램 알림 (매매는 계속 — primary 경로 trader.position 기준)
- Phase 3 에서 `trader.position` 제거, `watcher.position` 단일 소스로 통합 예정

### 외부 인프라

- GCP Seoul (asia-northeast3-a, 34.47.69.63, Ubuntu 22.04 LTS)
- Cloudflare Named Tunnel (도메인: hwrim.trade)
- Telegram Bot (알림)
- cron + systemd (자동 가동)

---

## 3.1 전체 서비스 흐름도 + 메서드 맵핑

> 각 단계별 실제 메서드 경로를 명시. 신규 Claude 세션은 이 맵을 기준으로 코드 탐색.

### A. 기동 및 초기화 (06:40 ~ 09:00)

```
cron → autotrade.service → python -m src.main
  ↓
main() [main.py]
  ↓
AutoTrader().run()
  ├─ api.connect() — KIS 토큰 발급
  ├─ add_realtime_callback(WS_TR_PRICE, _on_realtime_price)
  ├─ add_realtime_callback(WS_TR_FUTURES, _on_futures_price)
  ├─ add_execution_callback(_on_execution_notify)        ← R15-005 체결통보
  ├─ subscribe_execution(kis_hts_id)                     ← H0STCNI0 구독
  ├─ _coordinator.set_exit_callback(_on_exit_done)
  ├─ _coordinator.set_risk_manager(self.risk)            ← R-14
  ├─ subscribe_futures()                                 ← KOSPI200 선물
  ├─ api.get_buy_available() → buyable_cash              ← R15-007
  │   └─ _initial_cash, _available_cash 설정
  │   └─ _trade_logger.capital 갱신
  ├─ _start_dashboard_server()                           ← FastAPI + WebSocket
  └─ asyncio.wait(스케줄 태스크 7개)
```

**핵심 메서드 파일:라인**:
- `main.py::main() L1007` → `AutoTrader.run() L151`
- `main.py::_start_dashboard_server() L914`
- `kis.py::get_buy_available() L672`

### B. 09:50 스크리닝 → Watcher 생성

```
_schedule_screening() [while loop, 매일]
  ↓ (09:50 도달)
_on_screening(is_final=True)
  ├─ _manual_codes 체크 (없으면 알림 + return)
  ├─ screener.run_manual(_manual_codes)
  │   └─ KIS 가격/프로그램매매 조회 → StockCandidate 생성
  ├─ _coordinator.start_screening(targets, is_final=True)
  │   └─ Watcher 3~5개 생성 (is_double_digit 판정)
  │   └─ 각 watcher._t2_callback = _on_t2
  └─ api.subscribe_realtime(codes) — 실시간 체결가 구독
```

**핵심 메서드**:
- `main.py::_schedule_screening() L296`, `_on_screening() L310`
- `watcher.py::WatcherCoordinator.start_screening() L688`
- `screener.py::Screener.run_manual()`

### C. 실시간 가격 라우팅 → state 전이

```
KIS WebSocket → 체결가 메시지
  ↓
_on_realtime_price(data) [main.py]
  ↓
asyncio.create_task(coordinator.on_realtime_price(code, price, ts))
  ↓
각 watcher.on_tick(price, ts, futures_price)
  ├─ state == WATCHING → _handle_watching() → (신고가 -1% 하락) → _fire_trigger() → TRIGGERED
  ├─ state == TRIGGERED/READY/PASSED → _handle_triggered() → _evaluate_target()
  │   ├─ price ≤ hard_stop → DROPPED
  │   ├─ price > buy1 → PASSED
  │   └─ buy1 ≥ price > hard_stop → READY (YES 신호)
  └─ state == ENTERED → _handle_entered() → 5 청산 조건 평가
      ├─ hard_stop / timeout / target / futures_stop → _emit_exit() → EXITED
      └─ 11:20 도달 → force_exit() (by Coordinator.on_force_liquidate)
  ↓
coordinator._process_signals(ts)
  ├─ DROPPED 미체결 취소 (R-14 버그픽스)
  ├─ active 청산 신호 → _execute_exit()
  └─ active 없고 R-12 허용 시 → YES 종목 tiebreaker → _execute_buy()
```

**핵심 메서드**:
- `main.py::_on_realtime_price() L405`
- `watcher.py::Watcher.on_tick() L281`, `_handle_watching() L302`, `_handle_triggered() L369`, `_handle_entered() L438`
- `watcher.py::WatcherCoordinator._process_signals() L786`

### D. 매수 발주 → 체결통보

```
_process_signals() → _execute_buy(chosen_watcher)
  ↓
trader.place_buy_orders(watcher, _available_cash)
  ├─ _send_buy_order(code, qty, buy1_price, "buy1") → api.buy_order() → SUBMITTED
  └─ _send_buy_order(code, qty, buy2_price, "buy2") → api.buy_order() → SUBMITTED
  ↓ (KIS 접수 → AES 암호화 체결통보)
KIS WebSocket → _on_execution_notify(parsed) → _process_execution_notify()
  ├─ CNTG_YN=1, RCTF_CLS=0 → trader.on_live_acknowledged() → ACKNOWLEDGED
  ├─ CNTG_YN=1, RCTF_CLS=1/2 → critical 알림 (외부 정정/취소)
  ├─ RFUS_YN=1 → trader.on_live_rejected() + notify_error
  └─ CNTG_YN=2, SELN_BYOV_CLS=02 (매수 체결)
      ├─ trader.on_live_buy_filled() → trader.position 갱신 (이번 체결분만)
      ├─ coordinator.on_buy_filled(..., order=order)
      │   └─ watcher.on_buy_filled(..., order=order)
      │       ├─ total_buy_qty/amount 누적
      │       ├─ ENTERED 전이 (첫 체결 시)
      │       ├─ buy2 최초 체결 시 _t2_callback → _on_t2 (T2 예약)
      │       └─ Phase 2 B: watcher.position dual-write
      └─ _check_position_invariant(order, label="buy") — 정합성 검증
```

**핵심 메서드**:
- `main.py::_process_execution_notify() L479`, `_check_position_invariant() L442`
- `trader.py::place_buy_orders() L36`, `on_live_buy_filled() L355`
- `watcher.py::Watcher.on_buy_filled() L519`, `WatcherCoordinator.on_buy_filled() L913`

### E. 청산 → 매도 체결 → _on_exit_done

```
_handle_entered() → _emit_exit() → _exit_signal_pending=True, state=EXITED
  ↓ (다음 tick 에서)
_process_signals() → _execute_exit(watcher)
  ↓
trader.execute_exit(watcher, reason, price)
  └─ api.sell_order(시장가 또는 지정가) → SUBMITTED
  ↓ (KIS 체결 → 체결통보)
_process_execution_notify() → CNTG_YN=2, SELN_BYOV_CLS=01
  ├─ trader.on_live_sell_filled() → trader.position.total_qty 차감
  ├─ coordinator.on_sell_filled(..., order=order)
  │   └─ watcher.avg_sell_price / total_sell_amount 갱신
  │   └─ Phase 2 B: watcher.position.total_qty 차감, is_open=False 시 closed_at
  ├─ _check_position_invariant(order, label="sell")
  └─ trader.position.is_open == False 확정 시 → coordinator.on_sell_complete(watcher, ts)
      ↓
_on_exit_done(watcher) [main.py]  ← 최종 체인
  ├─ (1) pnl = trader.get_pnl(watcher.current_price)
  ├─ (2) risk.record_trade_result(pnl)
  ├─ (3) exit_reason == "hard_stop" → risk.record_hard_stop() → R-12 체크
  ├─ (4) _trade_logger.record_trade(watcher, trader) + notify_trade_complete
  ├─ (5) balance = api.get_balance() → risk.check_daily_loss_limit()
  ├─ (6) multi_trade.enabled 체크
  ├─ (7) buy_info = api.get_buy_available() → R15-007
  │      └─ new_cash = risk.calculate_available_cash(buyable_cash)
  │      └─ _available_cash = new_cash, coordinator.set_available_cash()
  ├─ (8) trader.reset() — trader.position = None
  ├─ (9) coordinator._active_code = None → handle_t3() → 다음 매매
  └─ (10) _fire_state_update() — 대시보드 동기화
```

**핵심 메서드**:
- `main.py::_on_exit_done() L684`
- `watcher.py::WatcherCoordinator._execute_exit() L862`, `on_sell_complete() L893`
- `trader.py::execute_exit() L239`, `on_live_sell_filled() L411`, `reset() L496`

### F. T1/T2/T3 연쇄 매매

```
T1: 첫 종목 ENTERED (Watcher 가 active 됨)
  ↓
T2: buy2 최초 체결 시점
  ↓ (watcher.on_buy_filled 내 _t2_callback 트리거)
coordinator._on_t2(code, ts)
  ├─ active_code 일치 확인
  ├─ _is_in_entry_window(ts) (10:00 ~ 10:55)
  └─ _try_reserve_at_t2(ts)
      └─ YES 종목 중 _tiebreaker_for_next 적용
          (KOSPI 3.8% / KOSDAQ 5.6% 이상 눌림 + 최대)
          → ReservationSnapshot 생성
  ↓
T3: 첫 종목 EXITED 확정 → _on_exit_done() → handle_t3(ts)
  ├─ 진입 윈도우 밖 → 예약 폐기 후 return
  ├─ 예약 존재 시 → _verify_reservation_at_t3()
  │   ① watchers 존재 ② is_yes ③ not terminal
  │   ④ target_buy1_price 변경 없음 ⑤ tiebreaker 유지
  │   ├─ 통과 → _execute_buy(예약 종목) → active_code 설정
  │   └─ 실패 → 예약 폐기 + 재판정
  └─ 예약 없거나 재확인 실패 → YES 종목 재판정 (tiebreaker 재적용)
      ├─ 통과 종목 있음 → _execute_buy()
      └─ 없음 → 다음 매매 포기
```

**핵심 메서드**:
- `watcher.py::_on_t2() L1031`, `_try_reserve_at_t2() L1083`, `_tiebreaker_for_next() L1119`
- `watcher.py::_verify_reservation_at_t3() L1163`, `handle_t3() L1246`

### G. R-13 실시간 비중 업데이트

```
_ratio_updater() [1초 간격 loop]
  ↓
대상 상태 (WATCHING/TRIGGERED/READY/ENTERED) 순회
  ├─ api.get_program_trade(code) → 비중 조회
  ├─ api.get_current_price(code) → trading_value
  ├─ new_ratio = (net_buy / trading_value) × 100
  ├─ is_double 변경 시
  │   ├─ program_ratio / is_double_digit 갱신
  │   ├─ state == TRIGGERED → _recalc_prices() (buy1/buy2/hard_stop)
  │   └─ state == ENTERED → _recalc_prices() + buy2 재발주
  │       └─ api.get_buy_available() → cash
  │       └─ trader.cancel_and_reorder_buy2(watcher, new_price, cash)
```

**핵심 메서드**:
- `main.py::_ratio_updater() L769`
- `watcher.py::Watcher._recalc_prices() L246`
- `trader.py::cancel_and_reorder_buy2() L149`

### H. 시각 기반 이벤트

```
10:55 _schedule_buy_deadline() → coordinator.on_buy_deadline(ts)
  └─ 비-ENTERED watcher 모두 SKIPPED + 미체결 매수 취소

11:20 _schedule_force_liquidate() → coordinator.on_force_liquidate(ts)
  └─ ENTERED watcher 전부 force_exit() → _exit_signal_pending=True
  └─ _process_signals() 즉시 한 번 더 호출 → _execute_exit() 체인

15:30 _schedule_market_close()
  └─ _trade_logger.update_daily_summary() → notify_daily_summary
  └─ _running = False
```

### I. 네트워크 장애 방어

```
WebSocket 끊김 감지
  ↓
kis._ws_receiver() → _on_ws_disconnect 콜백
  ↓
_on_ws_disconnect() [main.py] — 동기
  └─ asyncio.create_task(_emergency_cancel_orders())
     ├─ trader.pending_buy_orders 있으면 trader.cancel_buy_orders() (REST)
     └─ trader.has_position() → 수동 청산 안내 알림

병렬: _network_health_check() [30초 간격]
  └─ WS 무응답 2×timeout 초과 → 좀비 강제 끊기 → 재접속 트리거
  └─ 끊김 + active 모니터 보유 시 → _emergency_cancel_orders()
```

**핵심 메서드**:
- `main.py::_on_ws_disconnect() L620`, `_emergency_cancel_orders() L641`, `_network_health_check() L874`

---

## 4. 파일 구조

```
AUTOTRADE/
├── src/
│   ├── main.py                    # AutoTrader 엔진 (R15-005/007 + Phase 2 B)
│   ├── core/
│   │   ├── watcher.py             # Watcher + Coordinator (Phase 2 B position 필드)
│   │   ├── trader.py              # Trader (LIVE 전용)
│   │   ├── screener.py            # Screener (LIVE 전용)
│   │   ├── stock_master.py        # StockMaster
│   │   └── risk_manager.py        # RiskManager
│   ├── models/                    # StockCandidate, Order, Position, TradeRecord
│   ├── kis_api/
│   │   ├── kis.py                 # KIS REST + WebSocket + get_buy_available()
│   │   └── constants.py           # 엔드포인트/TR ID 상수
│   ├── storage/                   # SQLite trades.db + TradeLogger
│   ├── dashboard/                 # FastAPI
│   └── utils/
│       ├── price_utils.py         # KRX 호가 단위 보정
│       ├── notifier.py            # Telegram
│       ├── tunnel.py              # Cloudflare
│       ├── market_calendar.py     # 거래일 확인
│       └── logger.py              # loguru
├── config/
│   ├── settings.py                # Pydantic Settings (R16: paper/dry_run 필드 제거)
│   ├── strategy_params.yaml       # 매매 명세 (동결)
│   └── stock_master.json          # 종목 캐시
├── scripts/                       # cron 스크립트
├── logs/                          # 런타임 로그
├── data/                          # SQLite DB
├── docs/                          # W-N 명령서 아카이브
└── .env                           # 환경 변수 (8개, R16 정리 후)
```

**R16 에서 삭제된 파일 (3개)**:
- `src/core/fill_manager.py` (DRY_RUN/LIVE 체결 처리 추상 레이어)
- `src/core/cash_manager.py` (예수금 동결/확정/복원)
- `src/core/simulator.py` (호가/지연/부분체결 시뮬레이션)

---

## 5. 자료구조 (핵심)

### WatcherState (8 상태)

```
WATCHING → TRIGGERED → READY ↔ PASSED
   ↑          ↓          ↓        ↓
   │ (신고가  ↓        ENTERED  DROPPED (terminal)
   │  갱신)  ↓          ↓
   └─────────┘       EXITED (terminal)

또는 임의 상태 → SKIPPED (terminal)
```

| 상태 | 의미 |
|------|------|
| WATCHING | 신고가 대기 |
| TRIGGERED | 신고가 확정 (1% 하락) → 목표가 계산 완료 |
| READY | 매수 범위 내 (YES) |
| PASSED | 자리 지나감 (현재가 > 1차 매수가) |
| DROPPED | 손절선 이하 (terminal) |
| ENTERED | 매수 체결 (보유 중) |
| EXITED | 청산 완료 (terminal) |
| SKIPPED | 포기 (terminal) |

### Watcher (Phase 2 B 이후)

```python
@dataclass
class Watcher:
    # 기존 필드
    code: str
    name: str
    market: MarketType
    state: WatcherState = WatcherState.WATCHING
    intraday_high: int = 0
    confirmed_high: int = 0
    target_buy1_price: int = 0
    target_buy2_price: int = 0
    hard_stop_price_value: int = 0
    is_double_digit: bool = False      # R-11
    program_ratio: float = 0.0         # R-13
    buy1_filled: bool = False
    buy2_filled: bool = False
    total_buy_amount: int = 0
    total_buy_qty: int = 0
    avg_sell_price: int = 0
    total_sell_amount: int = 0
    exit_reason: str = ""
    # ... (기타 생략)

    # === Phase 2 B: dual-write Position ===
    position: Optional[Position] = None
```

### Order / Position (R15-005 + Phase 2 B)

```python
class OrderStatus(str, Enum):
    PENDING = "PENDING"             # R16 이후 legacy (LIVE 경로는 SUBMITTED)
    SUBMITTED = "SUBMITTED"         # R15-005: KIS REST 발주 완료, 체결통보 대기
    ACKNOWLEDGED = "ACKNOWLEDGED"   # R15-005: CNTG_YN=1 (접수 확정)
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"           # KIS RFUS_YN=1 또는 REST 실패

@dataclass
class Order:
    order_id: str = ""
    code: str = ""
    side: OrderSide = OrderSide.BUY
    price: int = 0
    qty: int = 0
    filled_price: int = 0
    filled_qty: int = 0                # 누적 (부분체결 지원)
    status: OrderStatus = OrderStatus.PENDING
    submitted_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    label: str = ""                    # "buy1", "buy2", "target", ...

@dataclass
class Position:
    """보유 포지션 (1종목 = 1포지션).

    Phase 2 B: trader.position 과 watcher.position 양쪽에 병렬 유지.
    Phase 3 에서 watcher.position 단일 소스로 통합 예정.
    """
    code: str = ""
    name: str = ""
    buy_orders: list[Order] = field(default_factory=list)
    total_buy_amount: int = 0
    total_qty: int = 0
    sell_orders: list[Order] = field(default_factory=list)
    is_open: bool = True
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
```

### ReservationSnapshot (T2 예약)

```python
@dataclass
class ReservationSnapshot:
    """T2 시점 (첫 종목 buy2 체결) 에 두 번째 매매 후보로 예약된 종목 스냅샷.

    T3 시점 (첫 종목 EXITED) 에 5항목 재확인 기준 데이터:
      ①watchers 존재 ②is_yes ③not is_terminal
      ④target_buy1_price 변경 없음 ⑤tiebreaker 조건 유지
    """
    code: str
    name: str
    market: MarketType
    reserved_at: datetime
    confirmed_high_at_t2: int
    current_price_at_t2: int
    pullback_pct_at_t2: float
    target_buy1_price_at_t2: int
    target_buy2_price_at_t2: int
```

---

## 6. 개발 / 운영 환경

### 개발 환경 (수석님 PC)
- OS: Windows 11, Shell: Git Bash
- Python: 3.11 (venv)
- 경로: `C:\Users\terryn\AUTOTRADE`
- vault: `C:\Users\terryn\Documents\Obsidian\AUTOTRADE`

### 운영 환경 (GCP Seoul)
- IP: 34.47.69.63
- 리전: asia-northeast3-a (서울)
- OS: Ubuntu 22.04 LTS
- 경로: `/home/ubuntu/AUTOTRADE`
- Python: 3.11 venv
- SSH 키: `~/.ssh/autotrade_gcp`

### 배포 패턴
```bash
# SSH 접속
ssh -i ~/.ssh/autotrade_gcp ubuntu@34.47.69.63

# 로컬 → 운영 (scp)
scp -i ~/.ssh/autotrade_gcp <파일> ubuntu@34.47.69.63:/home/ubuntu/AUTOTRADE/<경로>/

# 운영 검증
python3 -m py_compile <파일>

# 재시작
sudo systemctl restart autotrade
```

### .env 변수 (9개, R16 정리 후)

| 변수 | 설명 |
|------|------|
| `KIS_APP_KEY` / `KIS_APP_SECRET` | KIS API 인증 |
| `KIS_ACCOUNT_NO` | 실거래 계좌 (8+2자리 통합 형식) |
| `KIS_HTS_ID` | R15-005 체결통보 구독용 HTS ID (필수) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 텔레그램 알림 |
| `DASHBOARD_PORT` | 대시보드 포트 (기본 8503) |
| `DASHBOARD_ADMIN_TOKEN` | 대시보드 관리자 토큰 |
| `DASHBOARD_URL` | 로컬 `scripts/sync_trades.py` 용 원격 API endpoint |
| `LOG_LEVEL` | 로그 레벨 |

**R16 에서 제거된 환경변수 (4개)**:
- ~~`KIS_ACCOUNT_NO_PAPER`~~ (모의투자 계좌)
- ~~`USE_LIVE_API`~~ (LIVE API 사용 플래그)
- ~~`DRY_RUN_CASH`~~ (가상 예수금)
- ~~`TRADE_MODE`~~ (dry_run / paper / live 선택)

`settings.trade_mode` / `settings.is_live` property 는 호환성 위해 유지 — 각각 `"live"` / `True` 반환.

---

## 7. 협업 규칙 + 금지 사항

### §5.6 협업 규칙 — 8 원칙

1. **사실 base 우선** — 추측 금지. 코드/로그/문서 직접 확인. 메모리를 사실로 단정 금지.
2. **진단 후 작업** — M-N (미션) → 검증 → 전체 메서드와 연관성 확인 → 검토결과 판단 W-N (작업) → 검증 → 보고 순서
3. **멈춤 조건 명시** — 예상 외 결과 시 즉시 멈춤 + 보고. 자동 수정 금지.
4. **화이트리스트 원칙** — 명령서에 명시된 파일만 수정
5. **원자 작업** — 한 작업 = 한 영역
6. **검증 필수** — 모든 작업 후 import / grep / 단위 테스트
7. **추가 발견 기록** — 작업 중 발견한 빈틈 → [발견] 섹션에 기록
8. **통합 관점** — 좁은 영역도 전체 시스템 맥락에서 검토

### 절대 금지

1. 매매 명세 임의 변경 — yaml 동결값. 변경 시 새 R-N 필수
2. 상태 전이 로직 임의 변경 — 검증 없이 금지
3. 운영 환경 직접 코드 수정 — 로컬 → 검증 → 배포 순서

### 표준 금지 조항 (모든 명령서 적용)

1. 운영 서버 접근 0건 (Code 측)
2. 운영 서버 배포 금지 (Code 측)
3. git commit 자동 실행 금지
4. 로컬 AutoTrader 실제 실행 금지
5. systemd / 데몬 재시작 금지

### Claude 자기 주의사항 (R-08 학습)

**위반 패턴**:
- 메모리/이전 대화를 사실 base로 단정 — grep 재확인 필수
- 명세 사실 base 무시 — 머릿속 가정으로 처리
- 컨텍스트 길어질수록 확인 게을리짐
- 답변 모호 시 좁게 해석

**대응**:
- 모든 결정 전에 "이 결정의 사실 base가 무엇인가" 자문
- 모호한 답변 시 즉시 명시 재요청
- 컨텍스트 길 때 마지막 200~300줄 우선 정독

### 수석님 작업 스타일

- 명시적 멈춤 조건 요구
- 화이트리스트 엄수
- 범위 외 작업 즉시 차단
- **신규 .py 작성 (또는 전면 재작성) 시**: 검증 → 관련 메서드/연관성 검토 → 확정 → 작업방향 결정 → 진행

### 4 페르소나

| 페르소나 | 역할 | 관심사 |
|----------|------|--------|
| 펀드매니저 | 매매 명세/철학 결정 | 매매 로직, 리스크, 자본 |
| 시스템 엔지니어 | 아키텍처/코드 품질 | 모듈 분리, 테스트 |
| DBA 센터장 | 데이터/스키마 | SQLite, TradeRecord |
| 보안팀장 | 보안/권한 | API 키, 토큰, 접근 제어 |

---

## 8. 백로그 + 향후 방향

### Phase 3 — watcher.position 단일 소스 통합 (LIVE 1~2주 안정 후)

- `trader.position` 완전 제거
- Dashboard / Notifier / TradeLogger 의 `trader.*` 참조를 `watcher.position.*` 로 전환
- Simple Reconciler 도입 (`get_balance().holdings` 기반 3자 검증 → 2자 검증)
- R15-005 잔여: PANIC MODE + SA-5e (시작 시 holdings 체크)

### R15-007 실측 검증 포인트 (월요일 LIVE 첫 실행)

1. 시작 시 로그 `[R15-007] 초기 주문가능금액` 의 `buyable_cash` == MTS 앱 "주문가능원화" 화면 일치 여부
2. 청산 후 로그 `[R15-007] 청산 후 예수금 갱신` 의 `buyable_cash` 가 직전 _available_cash + P&L 만큼 반영되는지
3. `[Phase 2 B] Position 불일치` critical 로그 **미발생** 확인

불일치 발생 시 → 안 B (내부 예수금 추적 + API 교차 검증) 로 전환 논의.

### 환경 백로그

| # | 항목 | 우선순위 |
|---|------|----------|
| E1 | 토큰 캐시 read-only 재발 감시 | 중 |
| E3 | KIS API 재시도 로직 강화 | 중상 |
| E4 | systemd SIGTERM 원인 식별 | 중 |

---

## 9. vault 참조 원칙

### vault 위치
`C:\Users\terryn\Documents\Obsidian\AUTOTRADE`

### 역할 분리
- **CLAUDE.md = 앵커** — 신규 채팅 시 읽는 핵심 명세
- **vault = 상세 자료** — 심화 내용, 결정 근거, ISSUE 상세, R-N 작업 이력

### vault 주요 문서
- 매매 철학 / R-04 의사결정 원본
- 4 페르소나 정의 원본
- §5.6 협업 규칙 원본
- 8 카테고리 실거래 위험 분석
- R-08 / R-11 / R-12 / R15-005 / R15-007 / R16 작업 상세 이력
- ISSUE 번호 체계 전수 목록

---

**문서 끝 (vLive 2, 2026-04-17)**

**변경 이력**:
- v4.1 (2026-04-16): R-14 DRY_RUN 시뮬레이션 모듈 문서화
- vLive 1 (2026-04-17): Live 검증 대기 초기본
- **vLive 2 (2026-04-17)**: R16 Phase 1 (DRY_RUN/paper 제거 + R15-007) + Phase 2 B (Position dual-write) 반영. 파일 구조 / .env 변수 / 아키텍처 / 자료구조 전면 갱신.
- **vLive 2.1 (2026-04-17)**: W-SAFETY-1 (F1 SA-5e + F2′ 알림 + H2 알림 + VI-Observer) 반영. 외부 인프라 Oracle Cloud Seoul → GCP Seoul (34.47.69.63) 이전 반영.
- **vLive 2.1.1 (2026-04-20)**: 사실 base 정정 (구조 재편 없음). §1/§2 시각 테이블 yaml 기준 정정 (09:49 스크리닝 / 09:50 신고가). §2 시각 트리거 cron 06:35+06:40 2줄 분리. §2 R-13 Single→Double 강화 방향 대칭 기술 추가 (의도된 손절 타이트화, 2026-04-20 Terry 확정). §3 도메인 `hwrim.trade` 단일. §6 `.env` 변수 9개 (`DASHBOARD_URL` 추가). vLive 2.2 (P1~P12 시간축 경로 재편 + control_api.py / daily_archive.sh 추가) 는 별도 세션 예정.
