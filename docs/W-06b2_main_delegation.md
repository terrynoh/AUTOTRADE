# W-06b2 — main.py 매매 위임 + 함수 폐기 + 스케줄 정정

## 미션

W-06b 의 2차 작업 — *redesign 의 마지막 큰 작업*. main.py 의 매매 영역 함수들을 *Coordinator 위임* 으로 변경 + 옛 함수 *완전 폐기* + 스케줄 정정 + buy_deadline 신규.

작업 영역:
1. `_on_screening` 정정 (Coordinator.start_screening + 3종목 KIS subscribe)
2. `_start_monitoring_candidate` 폐기 (함수 자체 제거)
3. `_try_next_candidate` 폐기 (함수 자체 제거)
4. `_process_signals` 폐기 (함수 자체 제거)
5. `_monitor_loop_runner` 폐기 (함수 자체 제거)
6. `_schedule_force_liquidate` 단순화 (Coordinator.on_force_liquidate 호출)
7. `_schedule_market_close` 정정 (`_monitors` → `coordinator.watchers`)
8. `_get_status` 정정 (`_monitors` → `coordinator.watchers`)
9. `_network_health_check` 정정 (`_active_monitor` → `coordinator.has_active`)
10. `_schedule_buy_deadline` 신규 (10:55 매수 마감 트리거)
11. `run()` tasks 리스트 정정 (monitor_loop 제거, buy_deadline 추가)
12. `watcher.py` Coordinator._process_signals 에 DRY_RUN 시뮬 분기 추가

자동 진행 모드. 수석님 confirm 없이 끝까지 진행. 멈출 조건은 [멈춤 조건] 참조.

크기 예상: main.py 약 -160행, watcher.py 약 +10행

## 화이트리스트

이 두 파일만 수정 가능:
- `src/main.py`
- `src/core/watcher.py` (Coordinator._process_signals 의 DRY_RUN 분기만)

## 블랙리스트

절대 수정 금지:
- `src/core/trader.py` (W-05c 결과)
- `src/core/monitor.py` (W-07 영역)
- `src/core/screener.py` (W-03 결과)
- `src/core/stock_master.py` (W-02 결과)
- `src/utils/notifier.py` (W-04 결과)
- `src/models/stock.py`
- `src/models/order.py`
- `src/storage/database.py`
- `src/dashboard/*`
- `src/kis_api/*`
- `config/*`
- 그 외 모든 파일

## 배경 — 사실 base

### M-12 결과 (확정됨)

**main.py 의 매매 영역 함수 본문 9개** (M-12 작업 1):
- `_start_monitoring_candidate` (line 307-349, 약 45행) — 폐기
- `_try_next_candidate` (line 353-413, 약 60행) — 폐기
- `_schedule_force_liquidate` (line 417-428, 약 12행) — 단순화
- `_schedule_market_close` (line 432-503, 약 70행) — _monitors 영역만 정정
- `_process_signals` (line 774-835, 약 65행) — 폐기
- `_monitor_loop_runner` (line 768-772, 약 5행) — 폐기
- `_network_health_check` (line 903-935, 약 35행) — 1행 정정
- `_get_status` (line 116-134, 약 20행) — _monitors 영역 정정
- `run()` (line 144-252, 약 110행) — tasks 리스트 정정 + 작업 7+8 의 W-06b1 추가는 보존

### W-06a + W-06b1 결과 (확정됨)

- main.py: 1054 → 1127 (W-06b1 후)
- watcher.py: 749 → 762 (W-06b1 후)
- _coordinator 인스턴스 보유
- _stock_master 인스턴스 보유
- _on_exit_done 콜백 함수 신규 작성됨
- run() 안에 set_exit_callback 등록 추가됨
- run() 안에 초기 set_available_cash 호출 추가됨
- _build_trade_record 시그니처 정정됨 (mon → watcher)
- _on_realtime_price / _on_futures_price 위임됨
- _emergency_cancel_orders 정정됨

### Coordinator (W-05b/c/W-06b1 결과)

- 13 메서드 + set_available_cash + set_exit_callback
- _execute_buy / _execute_exit / _process_signals / on_realtime_price / on_buy_deadline / on_force_liquidate 가 async
- _execute_exit 끝에 콜백 호출 (W-06b1)
- DRY_RUN 시뮬레이션 분기 *없음* (W-06b2 에서 추가)

### 옛 자료구조 잔존 영역 (W-06a 보고)

- `_on_screening` (line 309: `_start_monitoring_candidate(0)`)
- `_start_monitoring_candidate` (TargetMonitor 생성 등)
- `_try_next_candidate` (잔고 갱신)
- `_process_signals` (_active_monitor 의존)
- `_schedule_force_liquidate` (_monitors 순회)
- `_schedule_market_close` (_monitors 순회)
- `_monitor_loop_runner` (_process_signals 호출)
- `_get_status` (_monitors 순회)
- `_network_health_check` (_active_monitor 참조)

→ W-06b2 에서 *모두 정리*.

## 흐름

```
작업 1 (watcher.py — Coordinator._process_signals DRY_RUN 분기 추가)
  → 작업 2 (main.py — _on_screening 정정)
  → 작업 3 (main.py — _start_monitoring_candidate 폐기)
  → 작업 4 (main.py — _try_next_candidate 폐기)
  → 작업 5 (main.py — _process_signals 폐기)
  → 작업 6 (main.py — _monitor_loop_runner 폐기)
  → 작업 7 (main.py — _schedule_force_liquidate 단순화)
  → 작업 8 (main.py — _schedule_market_close 정정)
  → 작업 9 (main.py — _get_status 정정)
  → 작업 10 (main.py — _network_health_check 정정)
  → 작업 11 (main.py — _schedule_buy_deadline 신규)
  → 작업 12 (main.py — run() tasks 리스트 정정)
  → 검증 1~8
  → 보고
```

중간에 멈추지 말 것. 각 작업 사이 검증 X.

---

## [작업 1] watcher.py — Coordinator._process_signals 에 DRY_RUN 시뮬 분기 추가

### 1-1. 기존 _process_signals (W-05c 결과)

```python
    async def _process_signals(self, ts: datetime) -> None:
        """매 틱 후 호출. 청산 신호 처리 + 매수 발주 평가."""
        # === 1. active 청산 신호 처리 ===
        active = self.active
        if active is not None and active._exit_signal_pending:
            await self._execute_exit(active, ts)
            self._active_code = None
            return
        
        # === 2. active 가 비어있으면 매수 발주 평가 ===
        if self._active_code is not None:
            return
        
        if self._is_after_buy_deadline(ts):
            return
        
        yes_watchers = [w for w in self.watchers if w.is_yes and not w.is_terminal]
        if not yes_watchers:
            return
        
        chosen = min(
            yes_watchers,
            key=lambda w: abs(w.distance_to_buy1(w.current_price))
        )
        
        await self._execute_buy(chosen, ts)
        self._active_code = chosen.code
```

### 1-2. 변경 — 시작 부분에 DRY_RUN 분기 추가

```python
    async def _process_signals(self, ts: datetime) -> None:
        """매 틱 후 호출. 청산 신호 처리 + 매수 발주 평가."""
        # === 0. DRY_RUN 시뮬레이션 (active watcher 의 미체결 매수만) ===
        active = self.active
        if active is not None and self.trader is not None:
            if self.trader.settings.is_dry_run and (active.buy1_pending or active.buy2_pending):
                filled = self.trader.simulate_fills(active, active.current_price, ts)
                for label in filled:
                    active.on_buy_filled(label, active.current_price, 0, ts)
        
        # === 1. active 청산 신호 처리 ===
        active = self.active
        if active is not None and active._exit_signal_pending:
            await self._execute_exit(active, ts)
            self._active_code = None
            return
        
        # === 2. active 가 비어있으면 매수 발주 평가 ===
        if self._active_code is not None:
            return
        
        if self._is_after_buy_deadline(ts):
            return
        
        yes_watchers = [w for w in self.watchers if w.is_yes and not w.is_terminal]
        if not yes_watchers:
            return
        
        chosen = min(
            yes_watchers,
            key=lambda w: abs(w.distance_to_buy1(w.current_price))
        )
        
        await self._execute_buy(chosen, ts)
        self._active_code = chosen.code
```

주의:
- DRY_RUN 분기는 *active 가 있을 때만* + *trader 가 있을 때만* + *buy1/buy2_pending 일 때만*
- `simulate_fills(active, active.current_price, ts)` — W-05c 에서 Watcher 호환으로 정정됨
- `active.on_buy_filled(label, active.current_price, 0, ts)` — Watcher 의 on_buy_filled (W-05a)
- 필드 0 (filled_qty) 은 옛 main.py 의 패턴 그대로 (실제 수량은 simulate_fills 안에서 처리)
- 분기 후 *active 다시 가져오기* — DRY_RUN 시뮬로 ENTERED 된 직후 청산 신호가 발생할 수 있음
- 비즈니스 로직 (1)~(2) 그대로 보존

---

## [작업 2] main.py — _on_screening 정정

### 2-1. 기존 (W-06a 후, M-12 결과 line 266-309)

```python
    async def _on_screening(self):
        """스크리닝 실행 → 후보 풀 선정 → 1번 종목으로 실시간 감시 시작."""
        if not self.risk.can_open_position(0):
            logger.warning("매매 불가 상태")
            return

        # 자동 스크리닝은 ISSUE-010 (수동 입력 방식 전환)으로 deprecated.
        # 종목 미입력 = 오늘 매매 안 함 (운영자에게 즉시 알림).
        if not self._manual_codes:
            logger.warning(
                "수동 입력 종목 없음 — 매매 진행 불가. "
                "자동 스크리닝은 ISSUE-010에 의해 deprecated."
            )
            self.notifier.notify_system(
                "⚠ 종목 미입력 → 오늘 매매 안 함\n\n"
                "텔레그램 /target 또는 대시보드에서 종목을 입력해주세요."
            )
            await self._fire_state_update()
            return

        logger.info(f"수동 스크리닝 실행 ({len(self._manual_codes)}종목)")
        targets = await self.screener.run_manual(self._manual_codes)

        if not targets:
            logger.info("스크리닝 결과 없음 — 당일 매매 안 함")
            await self._fire_state_update()
            return

        # 후보 풀 저장
        self._candidate_pool = targets
        self._candidate_index = 0
        self._completed_codes = set()

        mt = self.params.multi_trade
        if mt.enabled:
            logger.info(f"후보 풀 {len(targets)}종목 선정 (멀티 트레이드: 최대 {mt.max_daily_trades}회)")
            for i, t in enumerate(targets):
                logger.info(f"  #{i+1} {t.stock.name}({t.stock.code}) {t.stock.market.value} "
                            f"등락={t.stock.price_change_pct:+.2f}% 거래대금={t.stock.trading_volume_krw/1e8:.0f}억")
        else:
            logger.info(f"타겟 {len(targets)}종목 선정")

        # 1번 종목으로 감시 시작
        await self._start_monitoring_candidate(0)
```

### 2-2. 변경

```python
    async def _on_screening(self):
        """스크리닝 실행 → Coordinator 에 watchers 주입 → 3종목 KIS 구독."""
        if not self.risk.can_open_position(0):
            logger.warning("매매 불가 상태")
            return

        # 자동 스크리닝은 ISSUE-010 (수동 입력 방식 전환)으로 deprecated.
        # 종목 미입력 = 오늘 매매 안 함 (운영자에게 즉시 알림).
        if not self._manual_codes:
            logger.warning(
                "수동 입력 종목 없음 — 매매 진행 불가. "
                "자동 스크리닝은 ISSUE-010에 의해 deprecated."
            )
            self.notifier.notify_system(
                "⚠ 종목 미입력 → 오늘 매매 안 함\n\n"
                "텔레그램 /target 또는 대시보드에서 종목을 입력해주세요."
            )
            await self._fire_state_update()
            return

        logger.info(f"수동 스크리닝 실행 ({len(self._manual_codes)}종목)")
        targets = await self.screener.run_manual(self._manual_codes)

        if not targets:
            logger.info("스크리닝 결과 없음 — 당일 매매 안 함")
            await self._fire_state_update()
            return

        mt = self.params.multi_trade
        if mt.enabled:
            logger.info(f"후보 풀 {len(targets)}종목 선정 (멀티 트레이드: 최대 {mt.max_daily_trades}회)")
            for i, t in enumerate(targets):
                logger.info(f"  #{i+1} {t.stock.name}({t.stock.code}) {t.stock.market.value} "
                            f"등락={t.stock.price_change_pct:+.2f}% 거래대금={t.stock.trading_volume_krw/1e8:.0f}억")
        else:
            logger.info(f"타겟 {len(targets)}종목 선정")

        # Coordinator 에 watchers 주입 (W-06b2)
        self._coordinator.start_screening(targets)
        
        # 3종목 KIS WebSocket 구독 (시나리오 K1)
        codes = [w.code for w in self._coordinator.watchers]
        if codes:
            try:
                await self.api.subscribe_realtime(codes)
                self._subscribed_codes = codes
                logger.info(f"실시간 감시 시작: {len(codes)}종목 {codes}")
            except Exception as e:
                logger.error(f"실시간 구독 실패: {e}")

        await self._fire_state_update()
```

주의:
- `self._candidate_pool / _candidate_index / _completed_codes` 라인 *3개 제거* (옛 자료구조)
- `await self._start_monitoring_candidate(0)` 라인 *제거*
- `self._coordinator.start_screening(targets)` 라인 추가
- 3종목 KIS 구독 영역 추가 (`subscribe_realtime(codes)`)
- 멀티 트레이드 분기 로깅 (mt.enabled 영역) *그대로 보존*
- 매수 마감 / 강제 청산 시각은 *별도 _schedule_buy_deadline / _schedule_force_liquidate 가 처리* (별도 작업)

---

## [작업 3] main.py — _start_monitoring_candidate 폐기

### 3-1. 함수 자체 *완전 제거*

main.py 의 `_start_monitoring_candidate` 함수 정의 (W-06a 후 line 307 근방, 약 45행) 를 *완전 제거*.

함수 시그니처부터 마지막 return 까지 *전체 삭제*.

주의:
- 함수 *정의만* 제거. 호출자는 작업 2 에서 이미 제거됨.
- 빈 stub 으로 두지 말 것 — *완전 제거*.

---

## [작업 4] main.py — _try_next_candidate 폐기

### 4-1. 함수 자체 *완전 제거*

main.py 의 `_try_next_candidate` 함수 정의 (W-06a 후 line 353 근방, 약 60행) 를 *완전 제거*.

주의:
- 호출자는 옛 _process_signals 안에 있었음. _process_signals 도 폐기되므로 호출자도 자동 제거.
- 멀티 트레이드 가드 + 잔고 갱신 로직은 *_on_exit_done 으로 이미 이동* (W-06b1).

---

## [작업 5] main.py — _process_signals 폐기

### 5-1. 함수 자체 *완전 제거*

main.py 의 `_process_signals` 함수 정의 (W-06a 후 line 774 근방, 약 65행) 를 *완전 제거*.

주의:
- 호출자는 _monitor_loop_runner. 그것도 폐기되므로 호출자도 자동 제거.
- 비즈니스 로직 (DRY_RUN 시뮬, 시그널 처리, 청산 후처리) 은 *Coordinator + _on_exit_done 으로 이미 이동*.

---

## [작업 6] main.py — _monitor_loop_runner 폐기

### 6-1. 함수 자체 *완전 제거*

main.py 의 `_monitor_loop_runner` 함수 정의 (W-06a 후 line 768 근방, 약 5행) 를 *완전 제거*.

주의:
- 호출자는 run() 의 tasks 리스트 (작업 12 에서 정정).
- Coordinator 가 *시세 수신 시점에 자체 처리* — 1초 폴링 불필요.

---

## [작업 7] main.py — _schedule_force_liquidate 단순화

### 7-1. 기존 (M-12 결과 line 417-428)

```python
    async def _schedule_force_liquidate(self):
        force_time = time.fromisoformat(self.params.exit.force_liquidate_time)
        await self._wait_until(force_time)

        for mon in self._monitors:
            if mon.state == MonitorState.ENTERED:
                mon.force_exit(now_kst())
            elif mon.state in (MonitorState.HIGH_CONFIRMED, MonitorState.TRACKING_HIGH):
                # 미매수 상태 → 주문 취소
                await self.trader.cancel_buy_orders(mon.target)
                mon.state = MonitorState.SKIPPED
                logger.info(f"[{mon.target.stock.name}] 15:20 미매수 → 주문 취소")
```

### 7-2. 변경

```python
    async def _schedule_force_liquidate(self):
        """강제 청산 시각 도달 시 Coordinator 에 통지."""
        force_time = time.fromisoformat(self.params.exit.force_liquidate_time)
        await self._wait_until(force_time)
        await self._coordinator.on_force_liquidate(now_kst())
        logger.info(f"강제 청산 ({self.params.exit.force_liquidate_time}) — Coordinator 통지 완료")
```

주의:
- 본문 12행 → 4행 (단순화)
- _monitors 순회 *완전 제거* — Coordinator 가 자체 처리
- TargetMonitor / MonitorState 참조 제거
- logger 메시지 변경 (한국어 그대로)

---

## [작업 8] main.py — _schedule_market_close 정정

### 8-1. 기존 _monitors 순회 영역 (M-12 결과 line 442-453)

```python
        # ── 1. 미진입 모니터의 NO_ENTRY 레코드 추가 + 이미 저장된 레코드 합산 ──
        recorded_codes = {r.code for r in self._trade_records}
        for mon in self._monitors:
            t = mon.target
            if t.stock.code not in recorded_codes:
                # 미진입 또는 아직 기록되지 않은 모니터
                record = self._build_trade_record(mon, today)
                self._trade_records.append(record)
                try:
                    self._db.save_trade(record)
                except Exception as e:
                    logger.error(f"거래 DB 저장 실패 ({t.stock.name}): {e}")
```

### 8-2. 변경

```python
        # ── 1. 미진입 watcher 의 NO_ENTRY 레코드 추가 + 이미 저장된 레코드 합산 ──
        recorded_codes = {r.code for r in self._trade_records}
        for watcher in self._coordinator.watchers:
            if watcher.code not in recorded_codes:
                # 미진입 또는 아직 기록되지 않은 watcher
                record = self._build_trade_record(watcher, today)
                self._trade_records.append(record)
                try:
                    self._db.save_trade(record)
                except Exception as e:
                    logger.error(f"거래 DB 저장 실패 ({watcher.name}): {e}")
```

### 8-3. DailySummary 영역 정정 (M-12 결과 line 458-463)

기존:
```python
        # ── 2. DailySummary 생성 + DB 저장 ──
        summary = DailySummary(
            summary_date=today,
            trade_mode=self.settings.trade_mode,
            candidates_count=len(self._candidate_pool),
            targets_count=len(self._monitors),
        )
```

변경:
```python
        # ── 2. DailySummary 생성 + DB 저장 ──
        summary = DailySummary(
            summary_date=today,
            trade_mode=self.settings.trade_mode,
            candidates_count=len(self._coordinator.watchers),
            targets_count=len(self._coordinator.watchers),
        )
```

주의:
- `_candidate_pool` / `_monitors` *제거* — `_coordinator.watchers` 사용
- 두 카운트 모두 *동일 값* (Coordinator 가 단일 source of truth)
- 함수의 *나머지 영역* (콘솔 로그, 텔레그램 발송, 분석) *그대로 보존*

---

## [작업 9] main.py — _get_status 정정

### 9-1. 기존 (M-12 결과 line 116-134)

```python
    def _get_status(self) -> dict:
        """현재 매매 상태 반환 (텔레그램 /status용)."""
        monitors = []
        for mon in self._monitors:
            t = mon.target
            monitors.append({
                "code": t.stock.code,
                "name": t.stock.name,
                "state": mon.state.value,
                "intraday_high": t.intraday_high,
            })
        return {
            "trade_mode": self.settings.trade_mode,
            "available_cash": self._available_cash,
            "daily_trades": self.risk.daily_trades,
            "daily_pnl": self.risk.daily_pnl,
            "manual_codes": self._manual_codes,
            "monitors": monitors,
        }
```

### 9-2. 변경

```python
    def _get_status(self) -> dict:
        """현재 매매 상태 반환 (텔레그램 /status용)."""
        monitors = []
        for watcher in self._coordinator.watchers:
            monitors.append({
                "code": watcher.code,
                "name": watcher.name,
                "state": watcher.state.value,
                "intraday_high": watcher.intraday_high,
            })
        return {
            "trade_mode": self.settings.trade_mode,
            "available_cash": self._available_cash,
            "daily_trades": self.risk.daily_trades,
            "daily_pnl": self.risk.daily_pnl,
            "manual_codes": self._manual_codes,
            "monitors": monitors,
        }
```

주의:
- `mon.target` → `watcher` 직접
- `t.stock.code` → `watcher.code`
- `t.stock.name` → `watcher.name`
- `mon.state.value` → `watcher.state.value`
- `t.intraday_high` → `watcher.intraday_high`
- 반환 키 이름 *"monitors"* 그대로 (대시보드 호환성)

---

## [작업 10] main.py — _network_health_check 정정

### 10-1. 기존 (M-12 결과 line 925)

```python
            if not self.api.ws_connected and self._active_monitor and not self._emergency_cancel_done:
```

### 10-2. 변경

```python
            if not self.api.ws_connected and self._coordinator.has_active and not self._emergency_cancel_done:
```

주의:
- 1행 정정. 함수의 *나머지 부분* 그대로 보존.
- `self._active_monitor` (옛, W-06a 에서 폐기됨) → `self._coordinator.has_active` (W-05b 에서 정의된 property)

---

## [작업 11] main.py — _schedule_buy_deadline 신규

### 11-1. 함수 정의 추가

`_schedule_force_liquidate` 함수 *직전* 또는 *직후* 에 추가. 자연스러운 위치 — 시각 기반 스케줄 함수들과 함께.

권장: `_schedule_force_liquidate` 직전 (line 417 근방).

```python
    async def _schedule_buy_deadline(self):
        """매수 마감 시각 도달 시 Coordinator 에 통지 (10:55)."""
        deadline = time.fromisoformat(self.params.entry.entry_deadline)
        await self._wait_until(deadline)
        await self._coordinator.on_buy_deadline(now_kst())
        logger.info(f"매수 마감 ({self.params.entry.entry_deadline}) — Coordinator 통지 완료")
```

주의:
- async 함수
- `self.params.entry.entry_deadline` 사용 (yaml 의 "10:55")
- _wait_until 패턴 (옛 _schedule_force_liquidate 와 동일)
- Coordinator.on_buy_deadline 호출 (W-05c 에서 async)

---

## [작업 12] main.py — run() tasks 리스트 정정

### 12-1. 기존 (M-12 결과 line 211-218)

```python
            tasks = [
                dashboard_task,
                asyncio.create_task(self._schedule_screening(), name="screening"),
                asyncio.create_task(self._schedule_force_liquidate(), name="force_liquidate"),
                asyncio.create_task(self._schedule_market_close(), name="market_close"),
                asyncio.create_task(self._monitor_loop_runner(), name="monitor_loop"),
                asyncio.create_task(self._network_health_check(), name="health_check"),
            ]
```

### 12-2. 변경

```python
            tasks = [
                dashboard_task,
                asyncio.create_task(self._schedule_screening(), name="screening"),
                asyncio.create_task(self._schedule_buy_deadline(), name="buy_deadline"),
                asyncio.create_task(self._schedule_force_liquidate(), name="force_liquidate"),
                asyncio.create_task(self._schedule_market_close(), name="market_close"),
                asyncio.create_task(self._network_health_check(), name="health_check"),
            ]
```

주의:
- `_monitor_loop_runner` 라인 *제거*
- `_schedule_buy_deadline` 라인 *추가* (screening 직후)
- 다른 라인 보존
- 작업 7+8 의 W-06b1 추가 (set_exit_callback 등록 + 초기 set_available_cash) 는 *그대로 보존* — 위 tasks 리스트 변경은 그것들과 무관

---

## [검증]

### 검증 1 — main.py + watcher.py import OK

```bash
python -c "
from src.main import AutoTrader
from src.core.watcher import Watcher, WatcherCoordinator
print('main + watcher import OK')
"
```

기대: `main + watcher import OK`

### 검증 2 — 폐기된 함수가 없는지 확인

```bash
python -c "
from src.main import AutoTrader
removed_functions = [
    '_start_monitoring_candidate',
    '_try_next_candidate',
    '_process_signals',
    '_monitor_loop_runner',
]
for f in removed_functions:
    has = hasattr(AutoTrader, f)
    print(f'{f}: {\"잔존\" if has else \"폐기\"}')"
```

기대: 4개 함수 모두 *폐기*

### 검증 3 — 신규 함수 + 정정된 함수 시그니처

```bash
python -c "
import inspect
from src.main import AutoTrader

# 신규 함수
print('_schedule_buy_deadline 존재:', hasattr(AutoTrader, '_schedule_buy_deadline'))
print('_schedule_buy_deadline coroutine:', inspect.iscoroutinefunction(AutoTrader._schedule_buy_deadline))

# 보존된 매매 영역 함수들
print('_on_screening 존재:', hasattr(AutoTrader, '_on_screening'))
print('_schedule_force_liquidate 존재:', hasattr(AutoTrader, '_schedule_force_liquidate'))
print('_schedule_market_close 존재:', hasattr(AutoTrader, '_schedule_market_close'))
print('_get_status 존재:', hasattr(AutoTrader, '_get_status'))
print('_network_health_check 존재:', hasattr(AutoTrader, '_network_health_check'))
print('_on_exit_done 존재:', hasattr(AutoTrader, '_on_exit_done'))
"
```

기대: 모두 존재

### 검증 4 — AutoTrader 인스턴스 생성 + Coordinator 콜백 등록 확인

```bash
python -c "
import os
os.environ.setdefault('KIS_APP_KEY', 'test')
os.environ.setdefault('KIS_APP_SECRET', 'test')
os.environ.setdefault('ACCOUNT_NO', '0000000000')
from src.main import AutoTrader
at = AutoTrader()
print('coordinator:', type(at._coordinator).__name__)
print('subscribed_codes:', at._subscribed_codes)
print('stock_master:', type(at._stock_master).__name__)
# _on_exit_done 콜백이 _coordinator 의 _exit_callback 에 등록될 위치는 run() 안.
# __init__ 시점에는 None 이 정상.
print('_exit_callback (init):', at._coordinator._exit_callback)
"
```

기대:
```
coordinator: WatcherCoordinator
subscribed_codes: []
stock_master: StockMaster
_exit_callback (init): None
```

### 검증 5 — Coordinator._process_signals 의 DRY_RUN 분기 확인

Python 직접 읽기 (S-02-d):

```bash
python -c "
with open('src/core/watcher.py', 'r', encoding='utf-8') as f:
    content = f.read()
has_dry_run_branch = 'simulate_fills(active' in content
print('DRY_RUN 분기 추가:', has_dry_run_branch)
assert has_dry_run_branch, 'DRY_RUN 시뮬 분기 누락'
"
```

기대: `DRY_RUN 분기 추가: True`

### 검증 6 — 옛 자료구조 잔존 검색

```bash
python -c "
with open('src/main.py', 'r', encoding='utf-8') as f:
    content = f.read()
# 옛 필드 / 클래스 잔존 검색
patterns = ['_active_monitor', '_monitors', '_candidate_pool', '_candidate_index', '_completed_codes', 'TargetMonitor', 'MonitorState', 'TradeTarget', 'mon.target', 'mon.state']
for p in patterns:
    count = content.count(p)
    print(f'{p}: {count}건')
"
```

기대:
- 모든 패턴 *0건*
- 단, `_monitors` 라는 *변수명* 이 _get_status 안에서 *반환 키* 로 보존됨 — 그건 OK

### 검증 7 — _schedule_buy_deadline 시그니처

```bash
python -c "
import inspect
from src.main import AutoTrader
sig = inspect.signature(AutoTrader._schedule_buy_deadline)
print('params:', list(sig.parameters.keys()))
print('is coroutine:', inspect.iscoroutinefunction(AutoTrader._schedule_buy_deadline))
"
```

기대:
```
params: ['self']
is coroutine: True
```

### 검증 8 — main.py 라인 수 변화 + watcher.py 라인 수 변화

```bash
python -c "
with open('src/main.py', 'r', encoding='utf-8') as f:
    main_lines = len(f.readlines())
with open('src/core/watcher.py', 'r', encoding='utf-8') as f:
    watcher_lines = len(f.readlines())
print('main.py lines:', main_lines)
print('watcher.py lines:', watcher_lines)
"
```

기대:
- main.py: 약 950~970 행 (1127 - 약 160)
- watcher.py: 약 770~775 행 (762 + 약 8~12)

---

## [검증 실패 시 — 멈춤 조건]

- 검증 1 실패 (import) → 멈춤 + 보고
- 검증 2 실패 (폐기 함수 잔존) → 멈춤 + 보고
- 검증 3 실패 (신규/보존 함수 누락) → 멈춤 + 보고
- 검증 4 실패 (AutoTrader 생성) → 멈춤 + 보고
- 검증 5 실패 (DRY_RUN 분기 누락) → 멈춤 + 보고
- 검증 6 실패 (옛 패턴 잔존) → 멈춤 + 보고
  - 단, `_monitors` 가 _get_status 의 *반환 키* 로만 잔존하면 OK
- 검증 7 실패 (_schedule_buy_deadline 시그니처) → 멈춤 + 보고

멈춤 시 변경 *되돌리지 말 것*. 그대로 두고 보고.

---

## [모호한 케이스 — 사전 결정]

### 케이스 G-1: _on_screening 의 멀티 트레이드 분기 로깅 보존
M-12 결과 line 299-306 의 `mt.enabled` 분기 로깅은 *그대로 보존*. 후보 풀의 종목 정보 로깅도 보존. 단, *_candidate_pool* 변수 사용은 제거 (targets 직접 사용).

### 케이스 G-2: _schedule_buy_deadline 위치
권장: `_schedule_force_liquidate` 직전. 즉 옛 _try_next_candidate 폐기 후 빈 자리 또는 _schedule_force_liquidate 위. 자연스러운 위치 선택.

### 케이스 G-3: Coordinator.start_screening 의 인자 타입
W-05b 작성 시 `candidates: list` 타입. screener.run_manual 의 반환 타입과 일치 가정. 만약 *불일치* 면 멈춤 + 보고.

식별:
```bash
grep -A 3 "def start_screening" src/core/watcher.py
grep -A 3 "def run_manual" src/core/screener.py
```

### 케이스 G-4: subscribe_realtime 의 인자 형태
M-12 결과: `await self.api.subscribe_realtime([code])` — list[str] 받음. 새 패턴: `await self.api.subscribe_realtime(codes)` (codes 도 list[str]). 호환 OK.

### 케이스 G-5: get_balance 호출이 _on_exit_done 안에서 *2번* (손실 한도 + 잔고 갱신)
W-06b1 의 _on_exit_done 본문에 get_balance 호출이 *2번* 있음 (3. 손실 한도 체크 + 5. 잔고 재조회). 비효율적이지만 *옛 패턴 보존*. W-06b2 의 작업 대상 *아님*. 그대로 둠.

### 케이스 G-6: _start_monitoring_candidate 폐기 시 *호출자* 영향
호출자는:
- _on_screening (작업 2 에서 제거)
- _try_next_candidate (작업 4 에서 함수 자체 폐기)

→ 폐기 후 *호출자 0개*. 안전.

### 케이스 G-7: _try_next_candidate 폐기 시 *호출자* 영향
호출자는:
- _process_signals (작업 5 에서 폐기)

→ 폐기 후 *호출자 0개*. 안전.

### 케이스 G-8: _process_signals 폐기 시 *호출자* 영향
호출자는:
- _monitor_loop_runner (작업 6 에서 폐기)

→ 폐기 후 *호출자 0개*. 안전.

### 케이스 G-9: _monitor_loop_runner 폐기 시 *호출자* 영향
호출자는:
- run() 의 tasks 리스트 (작업 12 에서 제거)

→ 폐기 후 *호출자 0개*. 안전.

### 케이스 G-10: _schedule_force_liquidate 의 _emergency_cancel_done 리셋
옛 코드에는 _emergency_cancel_done 리셋이 *없음*. W-06b2 에서도 *추가 안 함* — 다른 영역.

### 케이스 G-11: TargetMonitor / MonitorState / TradeTarget 잔존
W-06a 후 import 영역은 0건. 함수 본문에 잔존했던 1건 (line 332, _start_monitoring_candidate 내부) 은 *작업 3 에서 함수 자체 폐기* 로 *함께 제거*.

→ W-06b2 후 main.py 의 TargetMonitor / TradeTarget *완전 0건*.

### 케이스 G-12: _build_trade_record 의 호출자
호출자는:
- _on_exit_done (W-06b1 작성, watcher 인자 전달)
- _schedule_market_close (작업 8 에서 정정, watcher 인자 전달)

→ 두 호출자 모두 *watcher 인자 전달* — 호환.

### 케이스 G-13: Coordinator 의 set_screening 호출이 sync 인지 async 인지
W-05b 결과: `start_screening` 은 *sync*. _on_screening 안에서 `await` *없이* 호출. 그러나 _on_screening 자체는 async 라 OK.

### 케이스 G-14: _on_screening 의 multi_trade 가드
옛 코드에 `mt.profit_only / max_daily_trades / repeat_start / repeat_end` 가드가 *_try_next_candidate 안에만* 있었음. _on_screening 자체에는 없음. W-06b2 에서도 *추가 안 함* — _on_exit_done 이 처리.

### 케이스 G-15: 잔여 multi_trade 분기
M-12 결과 line 148-150 의 run() 안에:
```python
mt = self.params.multi_trade
if mt.enabled:
    logger.info(f"멀티 트레이드: 최대 {mt.max_daily_trades}회, {mt.repeat_start}~{mt.repeat_end}")
```

→ *그대로 보존*. 시작 로깅용. 비즈니스 영향 없음.

---

## [자체 발견 처리 규칙]

Code 가 작업 중 다음을 발견하면 → 수정하지 말 것 → 보고의 [발견] 섹션에 1~3줄 기록:
- main.py / watcher.py 의 잠재적 버그
- 폐기된 함수가 *예상 못 한 다른 함수에서 호출* 됨
- _build_trade_record 호출자 추가 발견
- subscribe_realtime / unsubscribe_realtime 호출 패턴 불일치
- W-07 (monitor.py 폐기) 에서 처리해야 할 잔존 영역
- 더 깔끔한 패턴

---

## [보고]

### A. 변경 라인 (watcher.py)
- 작업 1 (DRY_RUN 분기): 라인 번호 + 추가 라인 수

### B. 변경 라인 (main.py)
- 작업 2 (_on_screening): 정정 라인 범위
- 작업 3 (_start_monitoring_candidate 폐기): 제거 라인 범위
- 작업 4 (_try_next_candidate 폐기): 제거 라인 범위
- 작업 5 (_process_signals 폐기): 제거 라인 범위
- 작업 6 (_monitor_loop_runner 폐기): 제거 라인 범위
- 작업 7 (_schedule_force_liquidate 단순화): 정정 라인 범위
- 작업 8 (_schedule_market_close 정정): 정정 라인 범위
- 작업 9 (_get_status 정정): 정정 라인 범위
- 작업 10 (_network_health_check 정정): 정정 라인 번호
- 작업 11 (_schedule_buy_deadline 신규): 추가 라인 범위
- 작업 12 (run() tasks 리스트): 정정 라인 범위

### C. 라인 수
- main.py: 1127 → ? (예상 약 950~970, 차이 ≈ -160)
- watcher.py: 762 → ? (예상 약 770~775, 차이 ≈ +8~12)

### D. 검증 결과
- 검증 1 ~ 8: 각각 성공/실패 + 출력

### E. 다른 파일 수정 여부
*반드시 main.py + watcher.py 두 파일만*

### F. 모호한 케이스 처리 결과 (G-1 ~ G-15 중 발생한 것)

### G. [발견] 섹션 — 자체 발견 사항 (있으면)

### H. W-07 진입 준비
- main.py 의 매매 영역 *완전 정상화*
- 옛 자료구조 (TargetMonitor / TradeTarget / _monitors / _active_monitor) *완전 폐기*
- W-07 작업 영역:
  - src/core/monitor.py 파일 자체 제거 (git rm)
  - src/models/stock.py 의 TradeTarget 클래스 제거
  - 잔존 import 검증

---

## [추가 금지 — 자동 모드 강화]

- main.py + watcher.py 외 어떤 파일도 수정 금지
- Trader 클래스 변경 금지 (W-05c 결과)
- monitor.py 수정 금지 (W-07 영역)
- Screener / Notifier / StockMaster / Watcher 클래스 변경 금지 (W-02/03/04/05a 결과)
- WatcherCoordinator 의 다른 메서드 변경 금지 (단, 작업 1 의 _process_signals 만)
- _on_exit_done 본문 변경 금지 (W-06b1 결과)
- _build_trade_record 본문 변경 금지 (W-06a 결과)
- _on_realtime_price / _on_futures_price 변경 금지 (W-06a 결과)
- _emergency_cancel_orders 변경 금지 (W-06a 결과)
- run() 의 본문 다른 영역 변경 금지 (단, 작업 12 의 tasks 리스트만)
- 새 함수 / 새 클래스 추가 금지 (단, _schedule_buy_deadline 만 명령서 명시)
- try-except 추가/제거 금지
- 타입 힌트 추가/제거 금지
- 새 import 금지 (이미 모두 W-06a + W-06b1 에서 추가됨)
- 로깅 메시지 변경 금지 (한국어 그대로, 명령서 명시 패턴)
- 영어 메시지 추가 금지
- TradeTarget 참조 추가 금지
- TargetMonitor 참조 추가 금지
- commit 금지
- 임시 파일 / scratch 파일 생성 금지
- W-07 / W-08 영역 손대지 말 것
