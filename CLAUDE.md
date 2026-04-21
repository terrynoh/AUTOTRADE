# CLAUDE.md — AUTOTRADE

> **목적**: 신규 Claude 세션이 AUTOTRADE 프로젝트의 정체성, 매매 철학, 아키텍처, 협업 규칙을 즉시 파악할 수 있는 self-contained 명세서.
>
> **버전**: vLive 2.4 (2026-04-22 §3.1 라인 동기화 + W-N-GHOST-01 + M-N-GAP-A 진단)
> **현재 단계**: R16 Phase 1 + Phase 2 B + W-SAFETY-1 + M-LIVE-02-FIX + M-LIVE-08-FIX + W-LIVE-09/10 완료 → Day 4 (2026-04-22 예정) 체결통보 + race fix + orphan safety net 실전 검증

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

**Day 1~3 LIVE 실전 상태** (2026-04-17 ~ 04-21):
- Day 1 (2026-04-17): 초기 LIVE 가동, ISSUE-LIVE-01 ~ 04 발생, M-LIVE-02-FIX (UN→J fallback) 긴급 설계
- Day 2 (2026-04-20): M-LIVE-02-FIX + M-DIAG-PROG v2 + W-SAFETY-1 배포, 체결 0건
- **Day 3 (2026-04-21)**: 삼성전기 009150 트리거 2회 → 10:49:59 매수 접수 3건 체결 (3주 @ 753,000원). **ISSUE-LIVE-08 (cryptography `_cffi_backend` 런타임 실패) 발생** → 체결통보 복호화 전면 실패 → ISSUE-LIVE-09 (Watcher 중복 발주 8회/0.22초) 유발. 당일 장중 서비스 stop + 원격 분석 + **M-LIVE-08-FIX 배포** (cryptography → pycryptodome). F1 SA-5e 자연 발동 검증 완료. 장 마감 후 **W-LIVE-09/10 설계 + 배포** (commit `474d5a7`): lock-first race fix + 3중 방어 + 부팅/10:55 orphan safety net.
- Day 4 (2026-04-22 예정): M-LIVE-08-FIX 체결통보 실전 검증 + W-LIVE-09/10 race fix / orphan safety net 실전 검증.

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

### R15-005 LIVE 체결통보 (2026-04-17, AES 구현 M-LIVE-08-FIX 2026-04-21)

- WebSocket `H0STCNI0` 구독 → AES-256-CBC 복호화 → 26 필드 파싱
- **AES 복호화 구현** (M-LIVE-08-FIX 이후): `pycryptodome` 사용 (`Crypto.Cipher.AES` + `Crypto.Util.Padding.unpad`). 기존 `cryptography.hazmat` 구현은 systemd 런타임 + asyncio `_ws_receiver` + lazy import 조합에서 `_cffi_backend` 로드 실패 발생 (ISSUE-LIVE-08, Day 3 실측)
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
- `main.py::main() L1261` → `AutoTrader.run() L163`
- `main.py::_start_dashboard_server() L1183`
- `kis.py::get_buy_available() L725`

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
- `main.py::_schedule_screening() L374`, `_on_screening() L388`
- `watcher.py::WatcherCoordinator.start_screening() L719`
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
- `main.py::_on_realtime_price() L507`
- `watcher.py::Watcher.on_tick() L282`, `_handle_watching() L317`, `_handle_triggered() L384`, `_handle_entered() L453`
- `watcher.py::WatcherCoordinator._process_signals() L825`

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
- `main.py::_process_execution_notify() L642`, `_check_position_invariant() L605`
- `trader.py::place_buy_orders() L36`, `on_live_buy_filled() L369`
- `watcher.py::Watcher.on_buy_filled() L549`, `WatcherCoordinator.on_buy_filled() L968`

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
- `main.py::_on_exit_done() L906`
- `watcher.py::WatcherCoordinator._execute_exit() L917`, `on_sell_complete() L948`
- `trader.py::execute_exit() L253`, `on_live_sell_filled() L425`, `reset() L510`

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
- `watcher.py::_on_t2() L1124`, `_try_reserve_at_t2() L1176`, `_tiebreaker_for_next() L1212`
- `watcher.py::_verify_reservation_at_t3() L1256`, `handle_t3() L1339`

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
- `main.py::_ratio_updater() L991`
- `watcher.py::Watcher._recalc_prices() L247`
- `trader.py::cancel_and_reorder_buy2() L163`

### H. 시각 기반 이벤트

```
10:55 _schedule_buy_deadline() → coordinator.on_buy_deadline(ts)
  └─ 비-ENTERED watcher 모두 SKIPPED + 미체결 매수 취소
  └─ [Day 3 실측 부산물] Watcher 루프가 고장난 상태에서도
     전체 Watcher 를 SKIPPED 로 일괄 잠금 → 중복 발주 루프 차단 효과 확인
     (10:55 매수 마감이 W-SAFETY-1 과 독립된 별개의 시각 기반 safety 역할 수행)

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
- `main.py::_on_ws_disconnect() L842`, `_emergency_cancel_orders() L863`, `_network_health_check() L1143`
- `kis.py::inquire_unfilled_orders() L762` (LIVE-10, F1 SA-5e 확장 + 10:55 safety net 에서 호출, `INQR_DVSN_2="2"` 매수만 조회)

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

### Git 배포 권한 (Day 3 정책 변경, 2026-04-21)

**종전**: GCP 서버는 `git pull` 전용. 모든 `git push` 는 로컬 Windows 에서만.

**변경**: GCP 서버에도 GitHub SSH 키 (`~/.ssh/id_ed25519`, GitHub 등록명 "AUTOTRADE GCP Seoul (34.47.69.63)") 설치 완료. Day 3 장중 ISSUE-LIVE-08 긴급 대응 중 GCP 로컬 변경사항의 origin 푸시가 필요해져 도입.

**운영 원칙**:
- 일상 개발은 로컬 Windows 에서 → push → GCP pull (기존 패턴 유지)
- 긴급 장중 핫픽스 또는 로컬 PC 접근 불가 시에만 GCP 직접 push 허용
- GCP 직접 push 후 즉시 로컬 Windows 에서 fetch/pull 로 3-way 동기화 복원
- CRLF/LF 충돌 방지: 별도 `.gitattributes` 추가 예정 (본 문서 §8 백로그)

### Python 의존성 (주요, Day 3 이후)

| 패키지 | 용도 | 비고 |
|--------|------|------|
| `pycryptodome` | R15-005 AES-256-CBC 복호화 | M-LIVE-08-FIX 이후 주 사용. cffi 무의존, 순수 C extension |
| `~~cryptography~~` | (제거 예정) | Day 3 이전 R15-005 에서 사용했으나 `_cffi_backend` 런타임 실패로 제거 전환. requirements.txt cleanup 예정 |
| `aiohttp` | KIS REST + WebSocket | asyncio 기반 |
| `loguru` | 로깅 | 파일 + 콘솔 |
| `fastapi` / `uvicorn` | 대시보드 | 실시간 WS |
| `pydantic-settings` | settings | .env → Settings |

**의존성 변경 주의**: `cryptography` 와 `pycryptodome` 은 **상호 대체 불가**. AES 코드 경로에서 두 라이브러리 혼용 금지. Day 3 ISSUE-LIVE-08 참고.

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

**Day 3 추가 학습 (2026-04-21)**:
- **클래스/함수/파일명을 머릿속 기억으로 단정 금지** — grep/sed 로 실제 파일 선확인. Day 3 세션에서 `KISClient` 임의 추측 후 수석님이 `KISAPI` 로 정정. Claude 측 사실 base 결여 사례
- **3-way 동기화(로컬 ↔ GCP ↔ origin) 불일치 상황에서 섣부른 merge/pull 금지** — Day 3 main.py CRLF/LF 이슈 사례. 2434줄 diff 를 "전면 다름" 으로 오판 가능. 반드시 `file` / `diff -w` / 함수 시그니처 비교 선행
- **오늘 세션에서 수정하지 않은 파일은 "내가 안 건드렸다"는 사실이 판단 근거** — 수석님 Day 3 지적. 원본이 동일하다는 전제 위에 작업 중이었다면, 오늘 수정 안 한 파일의 차이는 대부분 환경(라인엔딩, 인코딩) 이슈

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

### R15-007 실측 검증 포인트 (Day 1~3 경과 업데이트)

1. **시작 시 `[R15-007] 초기 주문가능금액` 로그 실측** — ✅ Day 3 실측 확인 (`buyable_cash=2,497,492원` 정상 조회, MTS 일치 여부 수석님 직접 확인 대기)
2. **청산 후 `[R15-007] 청산 후 예수금 갱신` 로그** — ⚠️ Day 3 삼성전기 매수 체결은 발생했으나 청산 미발생 (장 중 service stop) → 미검증 유지
3. **`[Phase 2 B] Position 불일치` critical 로그 미발생 확인** — ⚠️ Day 3 AES 복호화 실패로 체결통보 자체가 시스템에 인식되지 않아 Phase 2 B 경로 미도달 → 미검증. Day 4 M-LIVE-08-FIX 실전 작동 시 재검증 필요

불일치 발생 시 → 안 B (내부 예수금 추적 + API 교차 검증) 로 전환 논의.

### ISSUE-LIVE-09 Watcher `_execute_buy` 중복 호출 (✅ 해결 완료, 2026-04-21 W-LIVE-09)

**원인 확정** (Day 3 실측 + 분석):
- (c) asyncio tick handler 경쟁. `_process_signals` 가 `await _execute_buy` 전에 `_active_code` 를 설정하지 않아, await 동안 다음 tick 재진입 시 `_active_code is None` → 같은 chosen watcher 로 중복 진입.
- (a)/(b) 는 기각. AES 복호화 실패(ISSUE-LIVE-08)는 증폭 요인이었으나 근본 원인 아님.

**해결 (W-LIVE-09 배포, commit `474d5a7`)**:
- **Coordinator lock-first**: `_process_signals` 3 사이트 (첫 매수 / T3 예약 진입 / T3 재판정) 에 `self._active_code = chosen.code` 를 `await _execute_buy` **전**에 설정. 실패 시 except 로 active 해제. 다음 tick 재진입 차단.
- **`_execute_buy` idempotency guard**: 같은 watcher 에 `buy1_placed=True` 시 재진입 거부.
- **`Trader.place_buy_orders` 재진입 차단**: `self.pending_buy_orders` 중 `is_active` 존재 시 즉시 return (3중 방어).

**Day 4 실전 검증 포인트**:
- `[Coordinator] 매수 발주: {code}` 로그 1종목당 1회만 출력 (0.22초에 8회 재발 금지)
- `[Trader] CRITICAL: place_buy_orders 재진입 차단` 로그 0건 (방어선 자체가 작동하면 근본 fix 보완 필요)

### ISSUE-LIVE-10 미체결 매수 고아 주문 (✅ 해결 완료, 2026-04-21 W-LIVE-10)

**원인 확정**: Trader `cancel_buy_orders` 가 `self.pending_buy_orders` (tracked) 만 취소 → Watcher 밖에서 발생한 고아 주문 (LIVE-09 중복 발주 리젝 외의 실제 접수 건 등) 회수 불가.

**설계 원칙** (수석님 결정):
- `cancel_buy_orders` 의 **실시간 타이밍 경로는 tracked-only 유지**. cancel 경로에 inquire API 삽입은 timing-critical 을 해친다. HTS 매매 개입 금지 정책과 유사하게, 실시간 경로에서는 추가 API 호출 최소화.
- 대신 **시각 기반 독립 safety net** 으로 보강.

**해결 (W-LIVE-10 배포, commit `474d5a7`)**:
- `kis_api/constants.py`: `TR_UNFILLED = "TTTC0084R"` (구 TTTC8036R) 추가. 공식 API 문서 검증.
- `kis_api/kis.py`: `inquire_unfilled_orders()` 신규 (`INQR_DVSN_2="2"` 매수만 필터).
- `main.py`: **F1 SA-5e 확장** — 부팅 직후 미체결 매수 조회 + 발견 시 전량 `cancel_order` + 텔레그램 critical 알림. "포지션 0 + 미체결 있음" 시나리오 커버 (holdings-기반 SA-5e 가 놓치던 공백).
- `watcher.py`: `on_buy_deadline` 말미에 **10:55 일일 safety net** 추가 — 같은 inquire → 전량 취소. 루프 재발 시 마지막 차단선.

**Day 4 실전 검증 포인트**:
- 부팅 시 `[F1 확장] ... 미체결 매수 ...건 감지` INFO 로그 (없으면 미체결 0 = 정상)
- 10:55 `[Coordinator] 10:55 safety net:` INFO 로그 (발생 시 orphan 있었음 = LIVE-09 재발 의심)

### ISSUE-LIVE-08 해결 경위 (완료, 참고용)

**원인**: `cryptography.hazmat.primitives.ciphers` lazy import 가 systemd 런타임 + asyncio `_ws_receiver` 태스크 컨텍스트에서 `_cffi_backend` 모듈 로드 실패. 동일 venv 에서 `python -c` 직접 실행 시 성공, systemd-run 으로 동일 sandbox 옵션 재현해도 성공, 오직 실제 서비스 런타임 + KIS 실시간 AES 메시지 수신 조합에서만 발생.

**해결 (M-LIVE-08-FIX)**: `_decrypt_execution` 함수 단일 변경. `cryptography` → `pycryptodome` (`from Crypto.Cipher import AES` + `from Crypto.Util.Padding import unpad`). Round-trip 검증: cryptography 로 암호화한 블록을 pycryptodome 이 정확히 복호화 확인.

**커밋**: `96fb74a` (LIVE-08 fix). origin `feature/dashboard-fix-v1` 푸시 완료 (Day 3 종료 시점).

**근본 원인 완전 규명 미완료**: 재현 실패로 cryptography 의 어느 내부 경로가 실패했는지 불명. 환경 이전(Oracle Cloud → GCP Seoul) 시 wheel ABI 미스매치 추정. 향후 cryptography 의존성 제거 (requirements.txt cleanup) 추진.

### GAP-A 매도 체결 WS notification 유실 공백 (🔴 발견, M-N-GAP-A 2026-04-22)

**상태**: 미해결, 미구현. 의미론적 트레이스로 갭 확정.

**의미론적 체인 (유실 시나리오)**:
1. `_execute_exit` (watcher.py L917) → `_exit_signal_pending=False` + Watcher `state=EXITED` + `trader.execute_exit` REST 매도 발주 성공
2. KIS 측 체결 완료 — 그러나 WS 유실로 execution notification 미수신
3. `trader.on_live_sell_filled` / `WatcherCoordinator.on_sell_filled` / `on_sell_complete` / `_on_exit_done` 체인 **전원 미발화**
4. `trader.position.is_open=True` 잔존, `_active_code` 미리셋

**방어 사각지대 전수 확인**:
| 방어선 | 커버 범위 | 유실 커버 |
|------|----------|---------|
| F2-Timeout (main.py L766-798) | notification 도착 + `order_id` unmatched | ✗ 유실은 도달 자체 실패 |
| `_emergency_cancel_orders` (main.py L863) | 미체결 매수 REST 취소 | ✗ 매도 대상 아님 |
| F1 SA-5e + 10:55 safety net | 매수 unfilled (`INQR_DVSN_2="2"`) | ✗ 매도 미쿼리 |
| `on_force_liquidate` (watcher.py L1090) | `state == ENTERED` 만 iterate | ✗ EXITED skip (L1097) |
| `_check_position_invariant` (main.py L605) | notification 트리거 | ✗ 트리거 없음 |

**영향**:
- T3 연쇄 중단 (다음 매매 봉쇄)
- `_trade_logger.record_trade` 미호출 → DB/Summary 누락
- 11:20 `on_force_liquidate` 복구 경로 없음 (EXITED skip)
- 운영자 인지 실패 가능 (critical 알림 미발화)

**Mitigation 후보** (구현은 별도 W-N, 우선순위 검토 필요):
1. 주기적 `api.inquire_balance` 호출 → position reconciliation
2. `trader.execute_exit` 발주 후 N초 timeout → `api.inquire_ccld` 조회 → 체결 확인 → 누락 시 강제 on_sell_complete
3. `on_force_liquidate` 를 "position 잔존" 기준으로 확장 (`trader.position.is_open == True` 도 포함 → EXITED dead lock 강제 recovery)

### W-N-GHOST-01 완료 기록 (2026-04-22)

**수정**: src/main.py L76 + L434 `self._subscribed_codes` 고스트 변수 2곳 제거.

**근거**: main.py 내 소비처 0건 (Read 경로 없음). 실제 WS 재구독은 `kis.py::_subscribed_codes` (set, L180/L885-887/L899/L924/L1130) 별개 소유.

**검증**: grep 재확인 → main.py 참조 0 (백업 `.pre-staleness*` + 역사 문서 `docs/W-06*` 만 잔존, 정상). `python -m py_compile src/main.py` OK. `AutoTrader` import OK.

### 환경 백로그

| # | 항목 | 우선순위 |
|---|------|----------|
| E1 | 토큰 캐시 read-only 재발 감시 | 중 |
| E3 | KIS API 재시도 로직 강화 | 중상 |
| E4 | systemd SIGTERM 원인 식별 | 중 |
| E5 | `.gitattributes` 추가 + `core.autocrlf` 설정 (CRLF/LF 방지, Day 3 이후) | 중 |
| E6 | requirements.txt 정리 (`cryptography` 제거, `pycryptodome` 유지) | 중 |
| E7 | 환경 이전 체크리스트 문서화 (`docs/OPS_ENVIRONMENT_MIGRATION_CHECKLIST.md`) | 중 |
| E8 | trades.db 에 Day 3 체결분 3주 수기 INSERT (ISSUE-LIVE-08 로 미기록) | 높음 |

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

**문서 끝 (vLive 2.4, 2026-04-22 §3.1 라인 동기화 + W-N-GHOST-01 + M-N-GAP-A 진단)**

**변경 이력**:
- v4.1 (2026-04-16): R-14 DRY_RUN 시뮬레이션 모듈 문서화
- vLive 1 (2026-04-17): Live 검증 대기 초기본
- **vLive 2 (2026-04-17)**: R16 Phase 1 (DRY_RUN/paper 제거 + R15-007) + Phase 2 B (Position dual-write) 반영. 파일 구조 / .env 변수 / 아키텍처 / 자료구조 전면 갱신.
- **vLive 2.1 (2026-04-17)**: W-SAFETY-1 (F1 SA-5e + F2′ 알림 + H2 알림 + VI-Observer) 반영. 외부 인프라 Oracle Cloud Seoul → GCP Seoul (34.47.69.63) 이전 반영.
- **vLive 2.1.1 (2026-04-20)**: 사실 base 정정 (구조 재편 없음). §1/§2 시각 테이블 yaml 기준 정정 (09:49 스크리닝 / 09:50 신고가). §2 시각 트리거 cron 06:35+06:40 2줄 분리. §2 R-13 Single→Double 강화 방향 대칭 기술 추가 (의도된 손절 타이트화, 2026-04-20 Terry 확정). §3 도메인 `hwrim.trade` 단일. §6 `.env` 변수 9개 (`DASHBOARD_URL` 추가). vLive 2.2 (P1~P12 시간축 경로 재편 + control_api.py / daily_archive.sh 추가) 는 별도 세션 예정.
- **vLive 2.2 (2026-04-21)**: Day 3 LIVE 검증 결과 반영.
  - §1 운영 모드에 Day 1~3 LIVE 실전 진행 상황 기록 추가. ISSUE-LIVE-08 발생 + M-LIVE-08-FIX 배포 + F1 SA-5e 자연 검증 완료 요약.
  - §3 R15-005 AES 복호화 구현을 `cryptography` → `pycryptodome` 으로 변경 (M-LIVE-08-FIX).
  - §3.1 H. 10:55 매수 마감의 독립 safety 역할 Day 3 실측 주석 추가.
  - §6 Git 배포 권한 정책 변경 (GCP 서버 직접 push 허용). Python 의존성 관리 섹션 신규 추가 (cryptography 제거 예정, pycryptodome 주 사용).
  - §7 Claude 자기 주의사항에 Day 3 학습 3건 추가 (클래스명 확인, 3-way 동기화 판단, "오늘 안 건드린 파일" 판단 근거).
  - §8 R15-007 검증 포인트 Day 1~3 경과 업데이트. ISSUE-LIVE-09 / ISSUE-LIVE-10 / ISSUE-LIVE-08 해결 경위 3건 백로그 기록. 환경 백로그에 E5 (.gitattributes) / E6 (requirements.txt 정리) / E7 (이전 체크리스트) / E8 (Day 3 체결 수기 INSERT) 추가.
- **vLive 2.3 (2026-04-21)**: W-LIVE-09 + W-LIVE-10 설계/배포 완료 반영.
  - §1 운영 모드 Day 3 라인에 W-LIVE-09/10 배포 (commit `474d5a7`) 추가. Day 4 검증 항목 갱신.
  - §8 ISSUE-LIVE-09 를 "해결 완료" 로 전환 — 원인 확정 (c) asyncio tick race, 해결 3중 방어 (Coordinator lock-first + `_execute_buy` idempotency + Trader 재진입 차단), Day 4 검증 포인트 기술.
  - §8 ISSUE-LIVE-10 을 "해결 완료" 로 전환 — cancel_buy_orders tracked-only 유지 결정 + 시각 기반 독립 safety net (F1 SA-5e 확장 부팅 1회 + on_buy_deadline 10:55 1회 inquire_unfilled_orders).
  - 헤더 현재 단계 라인 갱신 (Day 4 체결통보 + race fix + orphan safety net 실전 검증 예정).
- **vLive 2.4 (2026-04-22)**: §3.1 라인 드리프트 동기화 + W-N-GHOST-01 + M-N-GAP-A 진단.
  - §3.1 A-I 전 메서드 라인 번호 13개 실제 소스 기준 재확정 (W-LIVE-09/10 + STALENESS-01b 누적 패치로 +14~+269 드리프트 존재).
  - §3.1 I 네트워크 방어에 `kis.py::inquire_unfilled_orders() L762` (LIVE-10) 문서화 추가.
  - §8 **GAP-A 매도 체결 WS notification 유실 공백** 신규 백로그 — 의미론적 트레이스 사실 기반 (방어 사각지대 5개 전수 확인, Mitigation 후보 3 기록, 구현은 별도 W-N 대기).
  - §8 **W-N-GHOST-01 완료 기록** — `main.py._subscribed_codes` 고스트 변수 제거 (L76 Init + L434 Set 소비처 0 확인 후 삭제, grep + py_compile + import 검증).
