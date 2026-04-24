# VI-DEFENSE-01 작업 명령서

> **목적**: VI(변동성완화장치) 발동이 watcher.py 매매 로직에 미치는 5개 위험 경로(A~E)에 대한 방어 코드 설계/배포.
>
> **상태**: 🟡 **대기(Pending)** — 이슈 발생 시 착수. 현 시점 스크리닝 필터는 확률적 안전망일 뿐 구조적 차단 경로 없음.
>
> **생성일**: 2026-04-22
> **기반 진단**: 2026-04-22 VI 시나리오 코드 팩트 매트릭스 (메서드 레벨 검증 완료)
> **연관**: W-SAFETY-1 (VI-Observer Stage 1 인프라 기존 존재)

---

## 1. 배경 (사실 base)

### 1.1 진단으로 확정된 위험 경로

| 경로 | 메서드:라인 | VI 방향 | 동작 | 결과 | 심각도 |
|------|------------|---------|------|------|--------|
| **A** | `watcher.py::update_intraday_high L232` | 상방 | `if price > self.intraday_high` — 단조증가, 감소경로 없음 | 당일 기준선 영구 오염 | 🔴 |
| **B** | `watcher.py::_fire_trigger L365` | 상방→재트리거 | `confirmed_high = intraday_high` — 오염값 상속 | buy1/buy2/stop 재계산 왜곡 | 🔴 |
| **C** | `watcher.py::_handle_entered L458` | 하방 | `hard_stop` 조건 즉시 `_emit_exit` | VI tick 1회로 손절 발주 | 🔴 |
| **D** | `watcher.py::_handle_entered L473` | 상방 | `price >= target_price` 즉시 `_emit_exit("target")` | VI tick 목표가 초과 시 체결 시도 | 🟡 |
| **E** | `watcher.py::_verify_reservation_at_t3` | 상방→재트리거 | `target_buy1_price` 재확인 실패 | T3 예약 폐기 → 재판정 fallback | 🟡 |

### 1.2 구조적 차단 경로 부재

- 스크리닝 필터(프로그램 순매수 ≥500억 · 비중 ≥10%) 는 **확률적 안전망**이지 **결정적 보장**이 아님
- Watcher 대상이 된 상태에서 VI 를 받는 실측 샘플 = **0건** (현 시점)
- 향후 Double 조건(≥10%) 이면서 VI 발동 종목 존재 가능성 원천 차단 없음

### 1.3 VI 2분 갭 동안의 동작

- `on_tick` 호출 없음 → 메서드 미실행
- `timeout_from_low_min` / `futures_drop_pct` 체크 중단
- VI 해제 후 첫 tick 에서 즉시 재평가

---

## 2. 작업 목표

**In Scope**:
1. 경로 A 방어: `update_intraday_high` 가 VI 상방 tick 을 흡수하지 않도록 가드
2. 경로 C 방어: ENTERED 상태에서 VI 중 하드 손절 발주 억제 + 해제 후 정규 tick 재평가
3. 경로 B 자동 해소 확인: 경로 A 수정으로 자연 해소되는지 테스트로 검증
4. 경로 D 확인: 경로 C 대응과 동일 경로로 커버되는지 확인
5. 경로 E 허용: 설계된 fallback 이므로 대응 불필요(문서화만)

**Out of Scope** (이 작업에서 건드리지 않음):
- R-11/R-12/R-13 매매 명세 자체 변경
- VI-Observer Stage 1 인프라 재설계
- `get_program_trade` / `get_current_price` REST 폴링 로직
- KIS API 재시도 로직 (E3 백로그 별건)

---

## 3. 선행 검증 (착수 전 필수)

수석님 §5.6 원칙 "검증 → 관련 메서드/연관성 검토 → 확정 → 작업방향 결정 → 진행".

| # | 검증 항목 | 방법 | Gate |
|---|----------|------|------|
| V1 | VI-Observer Stage 1 인프라 현재 상태 | `grep -rn "VI_STND_PRC\|vi_observer\|is_vi" src/` | VI 상태 조회 API 존재 여부 확정 |
| V2 | KIS 실시간 메시지 중 VI 예고/발동/해제 스키마 | kis.py / constants.py 확인 | 필드명·값 확정 |
| V3 | `update_intraday_high` 호출처 전수 | `grep -rn "update_intraday_high" src/` | 영향 범위 확정 |
| V4 | `_emit_exit` 호출처와 VI 상태 교차 가능 지점 | `grep -rn "_emit_exit" src/watcher.py` | 방어 삽입 지점 확정 |
| V5 | 실제 VI 종목 tick 의 체결강도·거래량 필드 샘플 | 프로덕션 로그 수집 | outlier 판별 기준 확정 |

**V1~V5 미완 시 착수 금지.**

---

## 4. 영향 파일 (화이트리스트)

```
src/watcher.py         # 경로 A/B/C/D/E 전부
src/kis_api/kis.py     # VI 상태 조회 wrapper (필요 시)
src/main.py            # 체결통보 라우팅 교차 검증 (읽기만)
tests/test_watcher_vi.py   # 신규 — VI 시나리오 단위 테스트
config/strategy_params.yaml  # 파라미터 추가 시만
```

**화이트리스트 외 수정 금지.** 범위 확장 필요 시 수석님 승인 후 명령서 개정.

---

## 5. 단계별 작업 항목

### Stage 1 — 인프라 확인 및 보강 (1~2시간)

- [ ] V1 검증 결과에 따라 `Watcher.is_vi_active` / `Watcher.vi_reference_price` 속성 추가 여부 결정
- [ ] VI 상태 주입 경로 설계:
  - Option α: `on_tick` 시그니처에 `vi_state` 추가
  - Option β: `WatcherCoordinator` 에서 주입 후 `watcher._vi_state` 보관
  - Option γ: kis.py 글로벌 상태 조회
- [ ] **수석님 선택 대기 게이트** — Option α/β/γ 중 결정 후 Stage 2 진입

### Stage 2 — 경로 A 방어 (가장 중요)

**대상**: `watcher.py::update_intraday_high L232` 구역

```python
# 의사코드 (실 구현 아님)
def update_intraday_high(self, price: float) -> None:
    if self._vi_state.is_active:
        # VI 발동 중 상방 tick 은 intraday_high 갱신에서 배제
        return
    if self._is_outlier(price):
        # 거래량/체결강도 기반 outlier 필터 (V5 결과로 기준 확정)
        logger.warning(f"[VI-A] outlier tick 배제 {self.code} price={price}")
        return
    if price > self.intraday_high:
        self.intraday_high = price
```

**멈춤 조건**:
- Outlier 판별 기준(V5)이 불확실하면 **VI 상태 체크만** 적용하고 outlier 필터는 Stage 2-b 로 분리
- 기존 max 누적 동작의 테스트 100% 통과 확인 후에만 배포

### Stage 3 — 경로 C 방어

**대상**: `watcher.py::_handle_entered L455/458` 구역

```python
# 의사코드
def _handle_entered(self, price, ts, futures_price):
    if self._vi_state.is_active:
        # VI 중 손절/목표가 발주 모두 억제
        logger.info(f"[VI-C] {self.code} VI 중 ENTERED 평가 skip")
        return
    # 기존 로직 (하드 손절 / 목표가 / 타임아웃 / 선물 -1%)
    ...
```

**동시 처리**: 경로 D 도 이 분기로 자동 차단됨.

**멈춤 조건**:
- VI 해제 후 첫 정규 tick 이 여전히 hard_stop 이하면 **즉시** `_emit_exit("hard_stop")` 발동 검증 필요
- 11:20 강제청산 시각 근처에서 VI 발동 중이면 예외 처리 별도 설계 필요 — **이 경우 Stage 3-b 로 분리**

### Stage 4 — 경로 B 해소 검증

- 경로 A 수정 후 `_fire_trigger L365` 의 `confirmed_high = intraday_high` 가 깨끗한 값을 상속하는지 테스트
- **코드 수정 없음**, 테스트만 추가

### Stage 5 — 경로 E 문서화

- `_verify_reservation_at_t3` 의 VI 후 재판정 경로는 **설계된 fallback** 임을 주석으로 명시
- 코드 수정 없음

### Stage 6 — 통합 검증

- [ ] 단위 테스트: VI 상태 조합 × Watcher 상태 조합 매트릭스
- [ ] 통합 테스트: VI 시뮬레이션 메시지 주입 → 전 라이프사이클 추적
- [ ] 프로덕션 로그 회귀: 최근 1주일 실거래 로그로 새 로직 동작 재현 확인
- [ ] 4 페르소나 검토:
  - **펀드매니저**: 매매 철학(자본 보호 우선) 유지 여부
  - **시스템 엔지니어**: 모듈 분리, 테스트 커버리지
  - **DBA**: TradeRecord 스키마 영향 없음 확인
  - **보안팀장**: 영향 없음(I/O 미추가)

---

## 6. 검증 로그 설계

프로덕션 배포 후 실측 확인용 로그 prefix:

| Prefix | 의미 | 경로 |
|--------|------|------|
| `[VI-A]` | intraday_high 갱신 억제 발생 | 경로 A |
| `[VI-C]` | ENTERED VI 중 평가 skip | 경로 C |
| `[VI-RESUME]` | VI 해제 후 첫 정규 tick 재평가 | C/D 공통 |
| `[VI-OUTLIER]` | Outlier 필터 배제 | 경로 A 보강 |

**Day+1 검증 포인트**: 위 4개 로그가 실거래 중 출력되는지 확인.

---

## 7. 롤백 계획

| 발생 상황 | 롤백 기준 | 절차 |
|----------|----------|------|
| `[VI-A]` 로그가 정상 tick 을 잘못 배제 | 정상 종목 매매 누락 1건 이상 | git revert + systemd restart |
| `[VI-C]` 로그가 비-VI 상태에서 발화 | VI 상태 조회 오탐 | 즉시 revert |
| 기존 max 누적 동작 회귀 | 단위 테스트 fail | 배포 차단 |

GCP Seoul `/home/ubuntu/AUTOTRADE` 기준 롤백:
```bash
cd /home/ubuntu/AUTOTRADE
git log --oneline -5    # 배포 직전 커밋 확인
git revert <commit>
sudo systemctl restart autotrade
```

---

## 8. 리스크 및 미결 사항

| # | 항목 | 대응 |
|---|------|------|
| R1 | VI 상태 조회 API(KIS) 의 신뢰성 불명 | V1 검증으로 확정, 실패 시 Option γ 폐기 |
| R2 | 11:20 강제청산과 VI 충돌 시나리오 | Stage 3-b 별건 분리 |
| R3 | Outlier 판별 기준 데이터 부족 | Stage 2-b 분리, 우선 VI 플래그만 적용 |
| R4 | Watcher 상태 주입 설계(α/β/γ) 미확정 | Stage 1 게이트로 명시 |
| R5 | `post_entry_low` 도 하방 VI tick 에 오염 가능성 (min 방향) | **진단 미포함 항목** — 추가 검증 후 Stage 확장 여부 결정 |

---

## 9. 멈춤 조건 (명시적)

다음 중 **하나라도 해당 시 즉시 중단 + 수석님 보고**:

1. 화이트리스트 외 파일 수정 필요 발생
2. 기존 매매 명세(R-11/R-12/R-13) 수정 필요 발생
3. V1~V5 검증 중 1건이라도 팩트 base 미확보
4. 단위 테스트 실패 시 기존 동작 회귀 여부 불명확
5. VI 상태 조회가 **실시간 보장되지 않음**이 확인되는 경우 (방어 자체가 무의미)

---

## 10. 착수 트리거

다음 중 하나 발생 시 본 명령서 기반 착수:

- **T1**: 실거래에서 Watcher 대상 종목이 VI 발동하여 경로 A~C 중 하나가 실측 발생
- **T2**: 수석님 판단으로 선제적 방어 착수 결정
- **T3**: VI-Observer Stage 2 로 통합 설계 필요성 확인

**현재 상태: T1/T2/T3 모두 미발생 → 대기**

---

## 변경 이력

- **v1 (2026-04-22)**: 초판. 2026-04-22 VI 시나리오 코드 팩트 매트릭스(경로 A~E) 기반. 착수 대기 상태로 등재.

---

## Appendix A — 진단 시 참고한 메서드 라인 (watcher.py)

| 메서드 | 라인 | 관련 경로 |
|--------|------|----------|
| `update_intraday_high` | L232 | A |
| `_recalc_prices` | L276-278 | B |
| `on_tick` | L282 | 전 상태 진입점 |
| `_handle_watching` | L317, 342/347/352/358 | A, B |
| `_fire_trigger` | L365 | B |
| `_handle_triggered` | L384, 404, 419 | 상방 복귀 · 하방 DROPPED |
| `_evaluate_target` | L430 | 하방 DROPPED 최우선 |
| `_handle_entered` | L453, 455, 458, 473 | C, D |
| `_emit_exit` | — | C, D 공통 종단 |
| `_verify_reservation_at_t3` | L1256 | E |

**문서 끝**
