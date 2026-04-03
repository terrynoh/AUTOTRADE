"""
실시간 감시 — 신고가 추적, 고가 확정 트리거, 6중 청산 조건 모니터링.

핵심 로직:
1. 9:55 이후 당일 신고가 달성 감시
2. 고가에서 1% 하락 → 고가 확정, 매수 주문 진입
3. 고가 갱신 → 기존 주문 취소, 새 고가에서 1% 하락 대기
4. 매수 후: 하드손절/추세이탈/25분타임아웃/목표가/선물급락/강제청산
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from enum import Enum
from typing import Optional

from loguru import logger

from config.settings import StrategyParams
from src.models.stock import TradeTarget, MarketType


class MonitorState(str, Enum):
    WATCHING_NEW_HIGH = "watching_new_high"    # 9:55 이후 신고가 대기
    TRACKING_HIGH = "tracking_high"           # 신고가 달성, 고가 추적 중
    HIGH_CONFIRMED = "high_confirmed"         # 1% 하락으로 고가 확정, 매수 주문 배치됨
    ENTERED = "entered"                       # 매수 체결, 청산 조건 모니터링 중
    EXITED = "exited"                         # 청산 완료
    SKIPPED = "skipped"                       # 매매 안 함


class TargetMonitor:
    """단일 타겟 종목 실시간 감시."""

    def __init__(self, target: TradeTarget, params: StrategyParams):
        self.target = target
        self.params = params
        self.state = MonitorState.WATCHING_NEW_HIGH

        # 캐시된 시간 파싱
        self._watch_start = time.fromisoformat(params.entry.new_high_watch_start)
        self._entry_deadline = time.fromisoformat(params.entry.entry_deadline)
        self._force_liquidate = time.fromisoformat(params.exit.force_liquidate_time)

        # 가격 추적
        self._pre_955_high: int = target.intraday_high  # 9:55 이전 고가
        self._current_price: int = target.stock.current_price

        # 고가 확정 시각 (타임아웃용)
        self._high_confirmed_at: Optional[datetime] = None

        # 선물 가격
        self._futures_price: int = 0
        self._futures_at_high: int = 0   # 종목 고점 시각의 선물 가격

        # 1분봉 추적 (추세 이탈용)
        self._minute_candle_low: int = 0
        self._minute_candle_start: Optional[datetime] = None

        # ── 시그널 (main.py에서 polling) ──
        self.signal_place_orders: bool = False     # 매수 주문 배치 요청
        self.signal_cancel_orders: bool = False    # 매수 주문 취소 요청 (고가 갱신)
        self.signal_exit: bool = False             # 청산 요청
        self.signal_exit_reason: str = ""
        self.signal_exit_price: int = 0

    # ── 실시간 가격 업데이트 ─────────────────────────────────

    def on_price(self, price: int, ts: datetime) -> None:
        """실시간 체결가 수신 시 호출."""
        self._current_price = price

        if self.state == MonitorState.WATCHING_NEW_HIGH:
            self._handle_watching_new_high(price, ts)
        elif self.state == MonitorState.TRACKING_HIGH:
            self._handle_tracking_high(price, ts)
        elif self.state == MonitorState.HIGH_CONFIRMED:
            self._handle_high_confirmed(price, ts)
        elif self.state == MonitorState.ENTERED:
            self._handle_entered(price, ts)

    def on_futures_price(self, price: int) -> None:
        """선물 실시간 가격 업데이트."""
        self._futures_price = price

    # ── Phase 1: 9:55 이후 신고가 감시 ─────────────────────

    def _handle_watching_new_high(self, price: int, ts: datetime) -> None:
        if ts.time() < self._watch_start:
            # 9:55 이전: 고가만 기록
            if price > self._pre_955_high:
                self._pre_955_high = price
                self.target.intraday_high = price
                self.target.intraday_high_time = ts
            return

        # 9:55 이후: 신고가 달성 체크
        if price > self._pre_955_high:
            self.target.update_intraday_high(price, ts)
            self.target.new_high_achieved = True
            self._futures_at_high = self._futures_price
            self.target.futures_price_at_high = self._futures_price
            self.state = MonitorState.TRACKING_HIGH
            logger.info(
                f"[{self.target.stock.name}] 9:55 이후 신고가 달성: {price:,}원 "
                f"(선물 {self._futures_at_high:,})"
            )
        elif price > self.target.intraday_high:
            # 9:55 이후 고가 갱신 (아직 이전 고가 못 넘음)
            self.target.update_intraday_high(price, ts)

    # ── Phase 2: 고가 추적 + 1% 하락 트리거 ──────────────

    def _handle_tracking_high(self, price: int, ts: datetime) -> None:
        # 매수 진입 마감 시각 체크
        if ts.time() >= self._entry_deadline:
            self.state = MonitorState.SKIPPED
            logger.info(
                f"[{self.target.stock.name}] 매수 진입 마감({self.params.entry.entry_deadline}) → 매매 안 함"
            )
            return

        high = self.target.intraday_high

        # 고가 갱신
        if self.target.update_intraday_high(price, ts):
            self._futures_at_high = self._futures_price
            self.target.futures_price_at_high = self._futures_price
            return

        # 1% 하락 체크 → 고가 확정
        drop_pct = self.params.entry.high_confirm_drop_pct
        trigger_price = int(high * (1 - drop_pct / 100))

        if price <= trigger_price:
            self.target.high_confirmed = True
            self.state = MonitorState.HIGH_CONFIRMED
            self._high_confirmed_at = ts
            self.signal_place_orders = True
            logger.info(
                f"[{self.target.stock.name}] 고가 확정: {high:,}원 → "
                f"현재 {price:,}원 (-{drop_pct}%). 매수 주문 진입"
            )

    # ── Phase 3: 매수 주문 배치됨, 체결/고가갱신 감시 ────

    def _handle_high_confirmed(self, price: int, ts: datetime) -> None:
        # 매수 진입 마감 시각 체크
        if ts.time() >= self._entry_deadline:
            self.signal_cancel_orders = True
            self.state = MonitorState.SKIPPED
            logger.info(
                f"[{self.target.stock.name}] 매수 진입 마감({self.params.entry.entry_deadline}) → 미체결 취소"
            )
            return

        # 고가 확정 후 N분 타임아웃 → 모멘텀 소멸, 주문 취소
        timeout_min = self.params.entry.high_confirm_timeout_min
        if self._high_confirmed_at and (ts - self._high_confirmed_at).total_seconds() >= timeout_min * 60:
            self.signal_cancel_orders = True
            self.state = MonitorState.SKIPPED
            logger.info(
                f"[{self.target.stock.name}] 고가 확정 후 {timeout_min}분 미체결 → 모멘텀 소멸, 주문 취소"
            )
            return

        # 고가 갱신 → 기존 주문 취소 + TRACKING_HIGH로 복귀
        if price > self.target.intraday_high:
            self.target.update_intraday_high(price, ts)
            self._futures_at_high = self._futures_price
            self.target.futures_price_at_high = self._futures_price
            self.signal_cancel_orders = True
            self.state = MonitorState.TRACKING_HIGH
            logger.info(
                f"[{self.target.stock.name}] 고가 갱신: {price:,}원 → "
                f"기존 주문 취소, 새 고가 추적"
            )

    def on_buy_filled(self, label: str, filled_price: int, filled_qty: int, ts: datetime) -> None:
        """매수 체결 통보."""
        self.target.total_buy_amount += filled_price * filled_qty
        self.target.total_buy_qty += filled_qty

        if label == "buy1":
            self.target.buy1_filled = True
        elif label == "buy2":
            self.target.buy2_filled = True

        # 첫 체결 시 ENTERED 상태 전환 + 최저가 초기화
        if self.state != MonitorState.ENTERED:
            self.state = MonitorState.ENTERED
            self.target.post_entry_low = filled_price
            self.target.post_entry_low_time = ts
            self._start_minute_candle(filled_price, ts)

        logger.info(
            f"[{self.target.stock.name}] {label} 체결: {filled_price:,}원 × {filled_qty}주 "
            f"(평단 {self.target.avg_price:,.0f}원)"
        )

    # ── Phase 4: 매수 후 청산 조건 모니터링 ──────────────

    def _handle_entered(self, price: int, ts: datetime) -> None:
        # 최저가 갱신/재터치 → minute_lows 리셋 (눌림 최저가 "이후" higher lows만 추적)
        old_low = self.target.post_entry_low
        self.target.update_post_entry_low(price, ts)
        if price <= old_low:
            # 최저가 갱신됨 → 분봉 저가 추적 리셋
            self.target.minute_lows.clear()
            self._minute_candle_low = price
            self._minute_candle_start = ts.replace(second=0, microsecond=0)
        else:
            self._update_minute_candle(price, ts)

        # ① 하드 손절
        stop_price = self.target.hard_stop_price(self.params)
        if price <= stop_price:
            self._emit_exit("hard_stop", 0, ts)  # 시장가
            logger.warning(
                f"[{self.target.stock.name}] 하드 손절: {price:,}원 ≤ {stop_price:,}원 "
                f"(고가 {self.target.intraday_high:,}원 대비 -{self.target.hard_stop_pct(self.params)}%)"
            )
            return

        # ② 추세 이탈 (higher lows 깨짐)
        if self.params.exit.trend_break_check and self._check_trend_break():
            self._emit_exit("trend_break", price, ts)
            logger.warning(f"[{self.target.stock.name}] 추세 이탈: higher lows 깨짐")
            return

        # ③ 25분 타임아웃
        if self._check_timeout(ts):
            self._emit_exit("timeout", price, ts)
            logger.warning(f"[{self.target.stock.name}] 25분 타임아웃")
            return

        # ④ 목표가
        target_price = self.target.target_price
        if target_price > 0 and price >= target_price:
            self._emit_exit("target", int(target_price), ts)
            logger.info(
                f"[{self.target.stock.name}] 목표가 도달: {price:,}원 ≥ {target_price:,.0f}원"
            )
            return

        # ⑤ 선물 급락
        if self._check_futures_drop():
            self._emit_exit("futures_stop", 0, ts)  # 시장가
            logger.warning(
                f"[{self.target.stock.name}] 선물 급락: "
                f"고점시각 {self._futures_at_high:,} → 현재 {self._futures_price:,}"
            )
            return

        # ⑥ 강제 청산은 main.py 스케줄러에서 처리

    # ── 청산 조건 헬퍼 ────────────────────────────────────

    def _check_trend_break(self) -> bool:
        """1분봉 저가가 직전보다 낮아지면 추세 이탈."""
        lows = self.target.minute_lows
        if len(lows) < 2:
            return False
        return lows[-1] < lows[-2]

    def _check_timeout(self, ts: datetime) -> bool:
        """눌림 최저가 시점부터 N분 경과."""
        low_time = self.target.post_entry_low_time
        if low_time is None:
            return False
        timeout = timedelta(minutes=self.params.exit.timeout_from_low_min)
        return (ts - low_time) >= timeout

    def _check_futures_drop(self) -> bool:
        """종목 고점 시각의 선물가 대비 N% 하락."""
        if self._futures_at_high <= 0 or self._futures_price <= 0:
            return False
        drop_pct = (self._futures_at_high - self._futures_price) / self._futures_at_high * 100
        return drop_pct >= self.params.exit.futures_drop_pct

    def _emit_exit(self, reason: str, price: int, ts: datetime) -> None:
        self.signal_exit = True
        self.signal_exit_reason = reason
        self.signal_exit_price = price
        self.target.exited = True
        self.target.exit_reason = reason
        self.state = MonitorState.EXITED

    # ── 1분봉 추적 ───────────────────────────────────────

    def _start_minute_candle(self, price: int, ts: datetime) -> None:
        self._minute_candle_low = price
        self._minute_candle_start = ts.replace(second=0, microsecond=0)

    def _update_minute_candle(self, price: int, ts: datetime) -> None:
        current_minute = ts.replace(second=0, microsecond=0)

        if self._minute_candle_start is None:
            self._start_minute_candle(price, ts)
            return

        if current_minute > self._minute_candle_start:
            # 이전 분봉 완료 → 저가 기록
            self.target.minute_lows.append(self._minute_candle_low)
            self._minute_candle_low = price
            self._minute_candle_start = current_minute
        else:
            if price < self._minute_candle_low:
                self._minute_candle_low = price

    # ── 강제 청산 (main.py에서 호출) ─────────────────────

    def force_exit(self, ts: datetime) -> None:
        """15:20 강제 청산."""
        if self.state == MonitorState.ENTERED:
            self._emit_exit("force", 0, ts)
            logger.warning(f"[{self.target.stock.name}] 15:20 강제 청산")
