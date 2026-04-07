"""
Phase α-0 항목 1 — 10시 이후 가드 단위 테스트.

명세: "10시 이후 최저가에서 20분 내 목표가 달성 못하면 매도"
해석: "10시 이후에 찍힌 최저가"에서만 타이머 시작.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
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


def _dt(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 4, 7, h, m, s)


class TestTimeoutGuard:
    """_check_timeout 10시 이후 가드 테스트."""

    def test_low_before_10_returns_false(self):
        """09:35 최저가 → 가드 작동, 타이머 시작 안 함."""
        mon = _make_monitor()
        mon.target.post_entry_low_time = _dt(9, 35)
        assert mon._check_timeout(_dt(10, 30)) is False

    def test_low_at_0950_returns_false(self):
        """09:50 최저가 → 가드 작동."""
        mon = _make_monitor()
        mon.target.post_entry_low_time = _dt(9, 50)
        assert mon._check_timeout(_dt(10, 30)) is False

    def test_low_after_10_19min_returns_false(self):
        """10:05 최저가 + 19분 경과 → 아직 타임아웃 아님."""
        mon = _make_monitor()
        mon.target.post_entry_low_time = _dt(10, 5)
        assert mon._check_timeout(_dt(10, 24)) is False

    def test_low_after_10_20min_returns_true(self):
        """10:05 최저가 + 20분 경과 → 타임아웃 매도."""
        mon = _make_monitor()
        mon.target.post_entry_low_time = _dt(10, 5)
        assert mon._check_timeout(_dt(10, 25)) is True

    def test_low_at_exactly_10_returns_true_after_timeout(self):
        """10:00 정각 최저가 + 20분 → 타임아웃."""
        mon = _make_monitor()
        mon.target.post_entry_low_time = _dt(10, 0)
        assert mon._check_timeout(_dt(10, 20)) is True

    def test_low_at_exactly_10_returns_false_before_timeout(self):
        """10:00 정각 최저가 + 19분 → 아직 아님."""
        mon = _make_monitor()
        mon.target.post_entry_low_time = _dt(10, 0)
        assert mon._check_timeout(_dt(10, 19)) is False

    def test_no_low_time_returns_false(self):
        """최저가 시각 없음 → False."""
        mon = _make_monitor()
        mon.target.post_entry_low_time = None
        assert mon._check_timeout(_dt(10, 30)) is False
