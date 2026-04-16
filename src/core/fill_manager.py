"""체결 처리 추상 레이어 (R-14: W-35).

DRY_RUN과 LIVE 모드의 체결 처리를 통합하는 인터페이스.
- DRY_RUN: AdvancedSimulator 사용
- LIVE: 향후 R-15에서 H0STCNI0 체결통보 연동

사용 예:
    fill_manager = FillManager(settings, cash_manager, params)
    
    # 주문 시 (DRY_RUN에서만 예수금 검증/동결)
    ok, reason = fill_manager.try_place_order(order)
    
    # 매 틱마다 체결 확인
    filled = fill_manager.check_fills(watcher, orders, ts)
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from loguru import logger

from config.settings import Settings, StrategyParams, SimulationParams
from src.core.simulator import AdvancedSimulator
from src.models.order import Order

if TYPE_CHECKING:
    from src.core.cash_manager import CashManager
    from src.core.watcher import Watcher


class FillManager:
    """체결 처리 추상 레이어. DRY_RUN/LIVE 통합.
    
    책임:
    1. 모드(DRY_RUN/LIVE)에 따라 적절한 핸들러 선택
    2. 주문 시 예수금 검증/동결 (DRY_RUN)
    3. 체결 조건 확인 및 처리
    4. 주문 취소 시 동결 해제
    
    DRY_RUN 모드:
    - AdvancedSimulator 사용
    - 예수금 동결/확정/해제 시뮬레이션
    - 부분체결, 체결지연, 주문거부 시뮬레이션
    
    LIVE 모드:
    - 향후 R-15에서 구현
    - H0STCNI0 WebSocket 체결통보 연동
    - 현재는 passthrough (기존 Trader 로직 사용)
    """
    
    def __init__(
        self,
        settings: Settings,
        cash_manager: "CashManager",
        params: StrategyParams,
    ):
        """
        Args:
            settings: 환경 설정 (trade_mode 확인용)
            cash_manager: 예수금 관리자 (W-30)
            params: 전략 파라미터 (simulation 섹션 포함)
        """
        self.settings = settings
        self.cash = cash_manager
        self.params = params
        
        # 모드별 핸들러 초기화
        if settings.is_dry_run:
            self._simulator = AdvancedSimulator(cash_manager, params.simulation)
            logger.info("[FillManager] DRY_RUN 모드: AdvancedSimulator 사용")
        else:
            self._simulator = None
            logger.info("[FillManager] LIVE 모드: 체결통보 대기 (R-15 구현 예정)")
    
    # ── 주문 처리 ─────────────────────────────────────────
    
    def try_place_order(self, order: Order) -> tuple[bool, str]:
        """주문 시도. DRY_RUN에서만 예수금 검증/동결.
        
        Args:
            order: 주문 객체
            
        Returns:
            (True, "OK"): 주문 가능
            (False, reason): 주문 거부 (잔고 부족, 호가 오류 등)
            
        Note:
            LIVE 모드에서는 항상 (True, "LIVE_PASSTHROUGH") 반환.
            실제 주문 거부는 KIS API에서 처리.
        """
        if self.settings.is_dry_run:
            return self._simulator.try_place_order(order)
        
        # LIVE: 기존 Trader 로직이 API 직접 호출
        return True, "LIVE_PASSTHROUGH"
    
    # ── 체결 확인 ─────────────────────────────────────────
    
    def check_fills(
        self,
        watcher: "Watcher",
        orders: list[Order],
        ts: datetime,
    ) -> list[tuple[str, int, int]]:
        """체결 조건 확인 및 처리.
        
        Args:
            watcher: Watcher 객체 (current_price 참조)
            orders: 체결 대상 주문 목록
            ts: 현재 시각
            
        Returns:
            [(label, filled_price, filled_qty), ...] 체결된 주문 정보
            
        Note:
            LIVE 모드에서는 빈 리스트 반환.
            실제 체결은 H0STCNI0 콜백이 처리 (향후 R-15).
        """
        if self.settings.is_dry_run:
            return self._simulator.check_fills(watcher, orders, ts)
        
        # LIVE: H0STCNI0 체결통보가 직접 on_buy_filled 호출 (R-15)
        return []
    
    # ── 주문 취소 ─────────────────────────────────────────
    
    def cancel_order(self, order: Order) -> bool:
        """주문 취소. DRY_RUN에서만 동결 해제.
        
        Args:
            order: 취소할 주문
            
        Returns:
            True: 취소 성공
            False: 취소 불가
            
        Note:
            LIVE 모드에서는 항상 True 반환.
            실제 취소는 Trader가 KIS API 호출.
        """
        if self.settings.is_dry_run:
            return self._simulator.cancel_order(order)
        
        # LIVE: Trader가 API 취소 호출
        return True
    
    # ── 상태 조회 ─────────────────────────────────────────
    
    def get_status(self) -> dict:
        """FillManager 상태 요약."""
        status = {
            "mode": "DRY_RUN" if self.settings.is_dry_run else "LIVE",
            "simulator_active": self._simulator is not None,
        }
        
        if self._simulator:
            status["simulation_params"] = self._simulator.get_params_summary()
        
        return status
    
    @property
    def is_dry_run(self) -> bool:
        """DRY_RUN 모드 여부."""
        return self.settings.is_dry_run
