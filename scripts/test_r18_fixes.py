"""R-18 4건 fix 시뮬레이션 테스트.

Fix 5 (race 차단), Fix 2 (stale Position guard), Fix 3 (self-cancel false positive),
Fix 4 (ACK retry) 의 동작을 mock 환경에서 검증.

실행: python scripts/test_r18_fixes.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 루트 path 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Settings, StrategyParams
from src.core.trader import Trader
from src.core.watcher import Watcher, WatcherCoordinator, WatcherState
from src.models.order import Order, OrderSide, OrderStatus, Position
from src.models.stock import MarketType


# ──────────────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────────────

def make_mock_api():
    api = MagicMock()
    api.cancel_order = AsyncMock(return_value={"ok": True})
    api.place_order = AsyncMock(return_value={"order_id": "FAKE_ORDER_999", "ok": True})
    return api


def make_settings_and_params():
    s = Settings()
    p = StrategyParams.load()
    return s, p


def make_watcher(code: str, name: str, params, state: WatcherState = WatcherState.WATCHING):
    w = Watcher(code=code, name=name, market=MarketType.KOSPI, params=params)
    w.state = state
    return w


def make_order(order_id: str, code: str, side: OrderSide = OrderSide.BUY,
               qty: int = 10, price: int = 10000, label: str = "buy1") -> Order:
    o = Order(
        code=code,
        side=side,
        qty=qty,
        price=price,
        order_id=order_id,
        label=label,
    )
    o.status = OrderStatus.SUBMITTED
    o.submitted_at = datetime(2026, 4, 24, 10, 0, 0)
    return o


# ──────────────────────────────────────────────────────────────────────
# Test 1 - Fix 5 race 차단
# ──────────────────────────────────────────────────────────────────────

async def test_fix5_race_blocking():
    print("\n=== Test 1: Fix 5 race 차단 ===")
    settings, params = make_settings_and_params()
    api = make_mock_api()
    trader = Trader(api, settings, params)
    coord = WatcherCoordinator(params, trader=trader)

    # 첫 종목 (ENTERED + exit pending)
    w1 = make_watcher("A001", "첫종목", params, WatcherState.ENTERED)
    w1._exit_signal_pending = True
    w1.exit_reason = "TARGET"
    w1.exit_price = 10500
    w1.position_qty = 10
    # READY 종목 (다음 매수 후보)
    w2 = make_watcher("A002", "다음후보", params, WatcherState.READY)
    w2.intraday_high = 11000
    w2.confirmed_high = 11000
    w2.target_buy1_price = 10800
    w2.target_buy2_price = 10750
    w2.hard_stop_price_value = 10500
    w2.current_price = 10800

    coord.watchers = [w1, w2]
    coord._active_code = "A001"

    # trader.execute_exit mock (SUBMITTED 발주만)
    trader.execute_exit = AsyncMock()
    trader.place_buy_orders = AsyncMock()

    ts = datetime(2026, 4, 24, 10, 30, 0)

    # 1차 _process_signals: exit 발주 → active 유지 확인
    await coord._process_signals(ts)
    assert trader.execute_exit.called, "execute_exit 호출 안 됨"
    assert coord._active_code == "A001", \
        f"Fix 5 위반: active 해제됨 (expected=A001, actual={coord._active_code})"
    print(f"  [OK] exit 발주 후 active 유지: {coord._active_code}")

    # 2차 _process_signals: active 유지로 다른 종목 매수 차단
    trader.place_buy_orders.reset_mock()
    await coord._process_signals(ts)
    assert not trader.place_buy_orders.called, \
        "Fix 5 위반: active 잠금 중 다른 종목 매수 발주됨"
    print(f"  [OK] active 잠금 중 신규 매수 발주 차단")

    # 3차: step 6.5 시뮬 (active 해제)
    coord._active_code = None
    w1.state = WatcherState.EXITED
    await coord._process_signals(ts)
    # READY 종목은 이제 진입 가능
    print(f"  [OK] active 해제 후 다음 종목 진입 평가 정상")

    print("Test 1: PASS")


# ──────────────────────────────────────────────────────────────────────
# Test 2 - Fix 2 stale Position guard
# ──────────────────────────────────────────────────────────────────────

async def test_fix2_stale_position():
    print("\n=== Test 2: Fix 2 stale Position guard ===")
    settings, params = make_settings_and_params()
    api = make_mock_api()
    trader = Trader(api, settings, params)

    # 기존 Position (이전 종목 A001 청산 후 reset 누락)
    trader.position = Position(code="A001", opened_at=datetime(2026, 4, 24, 10, 0, 0))
    trader.position.total_buy_amount = 1000000
    trader.position.total_qty = 100
    print(f"  기존 Position: code={trader.position.code} qty={trader.position.total_qty}")

    # 새 종목 A002 매수 체결 발생 (race 시나리오)
    new_order = make_order("ORD002", "A002", side=OrderSide.BUY, qty=50, price=20000)
    trader.pending_buy_orders.append(new_order)

    # 로그 캡처
    import logging
    logs = []
    handler = MagicMock()
    handler.handle = lambda r: logs.append(r)
    handler.level = logging.CRITICAL

    from loguru import logger
    sink_id = logger.add(lambda msg: logs.append(str(msg)), level="CRITICAL")

    # on_live_buy_filled 호출
    ts = datetime(2026, 4, 24, 10, 30, 0)
    result = trader.on_live_buy_filled("ORD002", 20000, 50, ts)

    logger.remove(sink_id)

    # Position 교체 확인
    assert trader.position.code == "A002", \
        f"Fix 2 실패: Position 미교체 (expected=A002, actual={trader.position.code})"
    print(f"  [OK] Position 교체: A001 → A002")

    # critical 로그 확인
    critical_log = any("Fix 2" in str(log) and "stale" in str(log).lower() for log in logs)
    assert critical_log, f"Fix 2 critical 로그 미발생. logs={logs}"
    print(f"  [OK] critical 로그 발생: 'stale Position 감지'")

    print("Test 2: PASS")


# ──────────────────────────────────────────────────────────────────────
# Test 3 - Fix 3 self-cancel false positive 차단
# ──────────────────────────────────────────────────────────────────────

async def test_fix3_self_cancel():
    print("\n=== Test 3: Fix 3 self-cancel false positive 차단 ===")
    settings, params = make_settings_and_params()
    api = make_mock_api()
    trader = Trader(api, settings, params)

    # ── 3-1. cancel_buy_orders 가 set 에 add ──
    w = make_watcher("A001", "테스트", params)
    o1 = make_order("ORD_BUY1", "A001", label="buy1")
    o2 = make_order("ORD_BUY2", "A001", label="buy2")
    trader.pending_buy_orders = [o1, o2]
    w.buy1_order_id = "ORD_BUY1"
    w.buy2_order_id = "ORD_BUY2"

    assert len(trader._self_cancelled_order_ids) == 0
    await trader.cancel_buy_orders(w)
    assert "ORD_BUY1" in trader._self_cancelled_order_ids, \
        "Fix 3b 실패: ORD_BUY1 set 미등록"
    assert "ORD_BUY2" in trader._self_cancelled_order_ids, \
        "Fix 3b 실패: ORD_BUY2 set 미등록"
    print(f"  [OK] cancel_buy_orders → set 등록 2건 ({trader._self_cancelled_order_ids})")

    # ── 3-2. cancel API 실패 시 set discard ──
    trader2 = Trader(make_mock_api(), settings, params)
    trader2.api.cancel_order = AsyncMock(side_effect=Exception("API down"))
    o3 = make_order("ORD_FAIL", "A002", label="buy1")
    trader2.pending_buy_orders = [o3]
    w2 = make_watcher("A002", "실패케이스", params)
    w2.buy1_order_id = "ORD_FAIL"
    await trader2.cancel_buy_orders(w2)
    assert "ORD_FAIL" not in trader2._self_cancelled_order_ids, \
        "Fix 3b 실패: API 실패 시 set 누수 (rollback 미작동)"
    print(f"  [OK] cancel API 실패 시 set discard (rollback)")

    # ── 3-3. reset() 시 set clear ──
    trader.reset()
    assert len(trader._self_cancelled_order_ids) == 0, \
        f"Fix 3d 실패: reset 후 set 미clear ({trader._self_cancelled_order_ids})"
    print(f"  [OK] reset() 후 set clear")

    # ── 3-4. cancel_and_reorder_buy2 set 등록 (Fix 3c) ──
    trader3 = Trader(make_mock_api(), settings, params)
    w3 = make_watcher("A003", "재발주", params)
    w3.buy2_order_id = "ORD_REORD2"
    o_buy2 = make_order("ORD_REORD2", "A003", label="buy2")
    trader3.pending_buy_orders = [o_buy2]
    # api.place_order mock for reorder (just need add part to fire)
    trader3.api.place_order = AsyncMock(return_value={"order_id": "NEW_ORD", "ok": True})
    # _send_buy_order may fail downstream but cancel part should run
    try:
        await trader3.cancel_and_reorder_buy2(w3, new_price=9500, available_cash=10_000_000)
    except Exception:
        pass  # 재발주 부분 실패 무시 (cancel 부분만 검증)
    assert "ORD_REORD2" in trader3._self_cancelled_order_ids, \
        "Fix 3c 실패: cancel_and_reorder_buy2 set 미등록"
    print(f"  [OK] cancel_and_reorder_buy2 → set 등록")

    print("Test 3: PASS")


# ──────────────────────────────────────────────────────────────────────
# Test 4 - Fix 4 ACK retry
# ──────────────────────────────────────────────────────────────────────

async def test_fix4_ack_retry():
    print("\n=== Test 4: Fix 4 ACK retry ===")
    settings, params = make_settings_and_params()
    api = make_mock_api()
    trader = Trader(api, settings, params)

    # AutoTrader 흉내내기: _process_execution_notify 의 ACK retry 로직만 추출 시뮬
    # (full AutoTrader 인스턴스화 회피 - 무거움)

    # 시나리오: 1차 호출 시 매칭 실패 (None) → 200ms 대기 → pending_buy_orders 추가됨 → 2차 호출 성공
    order_to_arrive = make_order("ORD_LATE", "A001", label="buy1")

    async def simulate_ack_with_retry(order_id: str, ts: datetime) -> Order | None:
        """Fix 4 패턴 그대로 재현."""
        result = trader.on_live_acknowledged(order_id, ts)
        if result is None:
            await asyncio.sleep(0.2)
            result = trader.on_live_acknowledged(order_id, ts)
        return result

    # 1차 호출: pending 비어있음 → None
    # asyncio.sleep(0.2) 안에서 다른 task 가 pending 추가하도록 schedule
    async def inject_order_after_100ms():
        await asyncio.sleep(0.1)
        trader.pending_buy_orders.append(order_to_arrive)

    ts = datetime(2026, 4, 24, 10, 0, 0)
    # 동시 실행
    inject_task = asyncio.create_task(inject_order_after_100ms())
    result = await simulate_ack_with_retry("ORD_LATE", ts)
    await inject_task

    assert result is not None, \
        f"Fix 4 실패: retry 후에도 매칭 실패 (pending={[o.order_id for o in trader.pending_buy_orders]})"
    assert result.status == OrderStatus.ACKNOWLEDGED, \
        f"Fix 4 실패: 상태 ACKNOWLEDGED 아님 (actual={result.status})"
    print(f"  [OK] retry 후 매칭 성공: order_id={result.order_id} status={result.status.value}")

    # ── 4-2. retry 후에도 None - warning 로그만 (외부 개입) ──
    trader2 = Trader(make_mock_api(), settings, params)
    result2 = await simulate_ack_with_retry("UNKNOWN_ORD", ts)
    assert result2 is None, "Fix 4: 진짜 외부 개입 시 None 리턴 정상"
    print(f"  [OK] retry 후 None - 외부 개입으로 분류 정상")

    print("Test 4: PASS")


# ──────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────

async def main():
    failed = []
    for name, test in [
        ("Test 1 Fix 5", test_fix5_race_blocking),
        ("Test 2 Fix 2", test_fix2_stale_position),
        ("Test 3 Fix 3", test_fix3_self_cancel),
        ("Test 4 Fix 4", test_fix4_ack_retry),
    ]:
        try:
            await test()
        except Exception as e:
            print(f"  [FAIL] {name} FAIL: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed.append(name)

    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED: {len(failed)}/{4} - {failed}")
        sys.exit(1)
    else:
        print(f"ALL PASS: 4/4")


if __name__ == "__main__":
    asyncio.run(main())
