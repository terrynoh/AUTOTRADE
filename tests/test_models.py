"""
기본 단위 테스트 — 모델, 설정, 유틸리티.

실행: pytest tests/ -v
"""
import sys
import os
from datetime import datetime, date, time
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import StrategyParams, Settings
from src.models.stock import StockCandidate, TradeTarget, MarketType
from src.models.order import Order, OrderSide, OrderStatus, Position
from src.models.trade import TradeRecord, DailySummary, ExitReason


class TestStrategyParams:
    def test_load_defaults(self):
        params = StrategyParams()
        assert params.screening.market_cap_min == 800_000_000_000
        assert params.screening.program_net_buy_ratio_min == 3.0
        assert params.entry.initial_buy_pct == 50.0
        assert params.entry.dca_per_buy_pct == 3.0
        assert params.exit.kospi_hard_stop_pct == 4.1
        assert params.exit.kosdaq_hard_stop_pct == 5.1
        assert params.exit.timeout_from_low_min == 25

    def test_load_from_yaml(self):
        yaml_path = Path(__file__).parent.parent / "config" / "strategy_params.yaml"
        if yaml_path.exists():
            params = StrategyParams.load(yaml_path)
            assert params.screening.market_cap_min > 0


class TestStockCandidate:
    def test_program_ratio(self):
        stock = StockCandidate(
            code="005930", name="삼성전자", market=MarketType.KOSPI,
            market_cap=500_000_000_000_000,
            trading_volume_krw=100_000_000_000,  # 1000억
            program_net_buy=5_000_000_000,        # 50억
            price_change_pct=2.5,
            current_price=70000,
        )
        # 비중 = 50억 / 1000억 * 100 = 5%
        assert abs(stock.program_net_buy_ratio - 5.0) < 0.01

    def test_program_ratio_zero_volume(self):
        stock = StockCandidate(
            code="005930", name="삼성전자", market=MarketType.KOSPI,
            market_cap=0, trading_volume_krw=0,
            program_net_buy=0, price_change_pct=0, current_price=0,
        )
        assert stock.program_net_buy_ratio == 0.0


class TestTradeTarget:
    def _make_target(self, market=MarketType.KOSPI):
        stock = StockCandidate(
            code="005930", name="삼성전자", market=market,
            market_cap=500_000_000_000_000,
            trading_volume_krw=100_000_000_000,
            program_net_buy=5_000_000_000,
            price_change_pct=2.5, current_price=70000,
        )
        return TradeTarget(stock=stock, rolling_high=72000)

    def test_entry_trigger_kospi(self):
        target = self._make_target(MarketType.KOSPI)
        # 72000 * 0.98 = 70560
        assert target.entry_trigger_price() == 70560

    def test_entry_trigger_kosdaq(self):
        target = self._make_target(MarketType.KOSDAQ)
        # 72000 * 0.97 = 69840
        assert target.entry_trigger_price() == 69840

    def test_hard_stop_kospi(self):
        target = self._make_target(MarketType.KOSPI)
        # 72000 * (1 - 0.041) = 72000 * 0.959 = 69048
        assert target.hard_stop_price() == 69048

    def test_hard_stop_kosdaq(self):
        target = self._make_target(MarketType.KOSDAQ)
        # 72000 * (1 - 0.051) = 72000 * 0.949 = 68328
        assert target.hard_stop_price() == 68328

    def test_avg_price(self):
        target = self._make_target()
        target.total_buy_amount = 7_000_000  # 700만원
        target.total_buy_qty = 100
        assert target.avg_price == 70000.0

    def test_target_price(self):
        target = self._make_target()
        target.total_buy_amount = 7_056_000
        target.total_buy_qty = 100
        # avg = 70560, rolling_high = 72000
        # target = (72000 + 70560) / 2 = 71280
        assert target.target_price == 71280.0


class TestPosition:
    def test_pnl_calculation(self):
        pos = Position(code="005930", name="삼성전자")

        buy = Order(
            code="005930", side=OrderSide.BUY,
            filled_price=70000, filled_qty=100,
            status=OrderStatus.FILLED, filled_at=datetime.now(),
        )
        pos.add_buy(buy)

        assert pos.total_qty == 100
        assert pos.avg_price == 70000.0

        # 미실현 P&L
        assert pos.pnl(71000) == 100_000  # (71000-70000)*100
        assert abs(pos.pnl_pct(71000) - 1.4285) < 0.01

    def test_close_position(self):
        pos = Position(code="005930", name="삼성전자")

        buy = Order(
            code="005930", side=OrderSide.BUY,
            filled_price=70000, filled_qty=100,
            status=OrderStatus.FILLED, filled_at=datetime.now(),
        )
        pos.add_buy(buy)

        sell = Order(
            code="005930", side=OrderSide.SELL,
            filled_price=71000, filled_qty=100,
            status=OrderStatus.FILLED, filled_at=datetime.now(),
        )
        pos.add_sell(sell)

        assert pos.total_qty == 0
        assert not pos.is_open
        assert pos.pnl() == 100_000


class TestDailySummary:
    def test_win_rate(self):
        summary = DailySummary(summary_date=date.today())

        win_trade = TradeRecord(
            trade_date=date.today(), code="005930", name="삼성전자",
            market="KOSPI", exit_reason=ExitReason.TARGET, pnl=50000,
        )
        loss_trade = TradeRecord(
            trade_date=date.today(), code="035720", name="카카오",
            market="KOSPI", exit_reason=ExitReason.HARD_STOP, pnl=-30000,
        )

        summary.add_trade(win_trade)
        summary.add_trade(loss_trade)

        assert summary.total_trades == 2
        assert summary.winning_trades == 1
        assert summary.losing_trades == 1
        assert summary.win_rate == 50.0
