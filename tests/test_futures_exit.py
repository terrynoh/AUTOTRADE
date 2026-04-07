"""
Phase α-0 항목 2 — 청산 조건 ④ 선물 급락 단위 테스트.

명세: "종목의 최고점 시각에서 현재까지 선물이 1% 하락 시 전량 청산"
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from config.settings import StrategyParams
from src.core.monitor import TargetMonitor, MonitorState
from src.models.stock import StockCandidate, TradeTarget, MarketType


def _make_monitor(params: StrategyParams | None = None) -> TargetMonitor:
    if params is None:
        with open("config/strategy_params.yaml", encoding="utf-8") as f:
            params = StrategyParams(**yaml.safe_load(f))
    stock = StockCandidate(
        code="005930", name="삼성전자", market=MarketType.KOSPI,
        current_price=70000, price_change_pct=3.0,
        trading_volume_krw=500_000_000_000, program_net_buy=30_000_000_000,
    )
    target = TradeTarget(stock=stock)
    return TargetMonitor(target=target, params=params)


class TestFuturesExit:
    """_check_futures_drop 청산 조건 ④ 테스트."""

    def test_exactly_1pct_drop_returns_true(self):
        """선물 400.0 → 396.0 (-1.0%) → 청산."""
        mon = _make_monitor()
        mon._futures_at_high = 400.0
        mon._futures_price = 396.0
        assert mon._check_futures_drop() is True

    def test_over_1pct_drop_returns_true(self):
        """선물 400.0 → 380.0 (-5.0%) → 청산."""
        mon = _make_monitor()
        mon._futures_at_high = 400.0
        mon._futures_price = 380.0
        assert mon._check_futures_drop() is True

    def test_under_1pct_drop_returns_false(self):
        """선물 400.0 → 396.01 (-0.9975%) → 미청산."""
        mon = _make_monitor()
        mon._futures_at_high = 400.0
        mon._futures_price = 396.01
        assert mon._check_futures_drop() is False

    def test_price_up_returns_false(self):
        """선물 400.0 → 405.0 (+1.25%) → 미청산."""
        mon = _make_monitor()
        mon._futures_at_high = 400.0
        mon._futures_price = 405.0
        assert mon._check_futures_drop() is False

    def test_no_futures_data_returns_false(self):
        """선물 데이터 없음 → 미청산."""
        mon = _make_monitor()
        mon._futures_at_high = 0.0
        mon._futures_price = 396.0
        assert mon._check_futures_drop() is False

    def test_no_current_futures_returns_false(self):
        """현재 선물가 없음 → 미청산."""
        mon = _make_monitor()
        mon._futures_at_high = 400.0
        mon._futures_price = 0.0
        assert mon._check_futures_drop() is False

    def test_on_futures_price_updates_state(self):
        """on_futures_price()가 _futures_price를 갱신하는지."""
        mon = _make_monitor()
        mon.on_futures_price(350.25)
        assert mon._futures_price == 350.25

    def test_float_precision_boundary(self):
        """소수점 정밀도 경계 — 350.25에서 1% 초과 하락 시 청산."""
        mon = _make_monitor()
        mon._futures_at_high = 350.25
        # 1% 약간 초과: 346.74 (drop_pct ≈ 1.002%)
        mon._futures_price = 346.74
        assert mon._check_futures_drop() is True

    def test_float_precision_just_under(self):
        """소수점 정밀도 — 346.75 (-0.9993%) → 미청산."""
        mon = _make_monitor()
        mon._futures_at_high = 350.25
        mon._futures_price = 346.75
        assert mon._check_futures_drop() is False

    def test_high_update_stores_futures(self):
        """고점 갱신 시 현재 선물 가격이 _futures_at_high에 저장되는지."""
        mon = _make_monitor()
        mon._futures_price = 350.50

        # 9:55 이후 신고가 달성 → TRACKING_HIGH 전환
        ts = datetime(2026, 4, 7, 9, 56, 0)
        mon.target.intraday_high = 70000
        mon._pre_955_high = 70000
        mon.state = MonitorState.WATCHING_NEW_HIGH

        mon.on_price(70100, ts)  # 신고가 달성
        assert mon._futures_at_high == 350.50
        assert mon.target.futures_price_at_high == 350.50
