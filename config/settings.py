"""
설정 관리 — .env에서 환경변수, strategy_params.yaml에서 전략 파라미터 로드.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

def _get_project_root() -> Path:
    """PyInstaller exe 환경에서도 올바른 루트 경로 반환."""
    if getattr(sys, "frozen", False):
        # exe 실행 시: exe가 있는 폴더를 루트로 사용
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _get_project_root()


class Settings(BaseSettings):
    """환경변수 (.env) 기반 설정."""

    # 한국투자증권 KIS
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    kis_account_no_paper: str = ""

    # 매매 모드
    trade_mode: Literal["dry_run", "paper", "live"] = "dry_run"

    # 텔레그램
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 로깅
    log_level: str = "DEBUG"

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
    }

    @property
    def account_no(self) -> str:
        if self.trade_mode == "live":
            return self.kis_account_no
        return self.kis_account_no_paper or self.kis_account_no

    @property
    def is_live(self) -> bool:
        return self.trade_mode == "live"

    @property
    def is_paper(self) -> bool:
        return self.trade_mode == "paper"

    @property
    def is_dry_run(self) -> bool:
        return self.trade_mode == "dry_run"

    @property
    def is_paper_mode(self) -> bool:
        """KIS API 모의투자 여부 (dry_run도 모의투자 URL 사용)."""
        return self.trade_mode != "live"


# ── 전략 파라미터 (YAML) ────────────────────────────────────────

class ScreeningParams(BaseModel):
    screening_time: str = "09:50"
    volume_min: int = Field(default=50_000_000_000, ge=0, le=1_000_000_000_000)  # 500억
    program_net_buy_ratio_min: float = Field(default=5.0, ge=0.0, le=100.0)  # 프로그램순매수비중 ≥ 5%
    top_n_gainers: int = Field(default=1, ge=1, le=50)  # 상승률 최고 1종목 (단일 매매 시)
    top_n_candidates: int = Field(default=5, ge=1, le=50)  # 후보 풀 크기 (멀티 트레이드용)
    max_change_pct: float = Field(default=29.5, ge=1.0, le=30.0)  # 상한가(+30%) 제외 임계값


class MultiTradeParams(BaseModel):
    enabled: bool = True                          # 멀티 트레이드 활성화
    max_daily_trades: int = Field(default=3, ge=1, le=20)  # 일일 최대 매매 횟수
    repeat_start: str = "10:00"                   # 다음 종목 진입 가능 시작 시각
    repeat_end: str = "11:00"                     # 다음 종목 진입 가능 마감 시각
    profit_only: bool = True                      # 수익 청산 시에만 다음 종목 (손절 시 중단)


class EntryParams(BaseModel):
    new_high_watch_start: str = "09:55"           # 신고가 감시 시작
    entry_deadline: str = "11:00"                 # 매수 진입 마감 시각 (이후 신규 매수 불가)
    high_confirm_drop_pct: float = Field(default=1.0, ge=0.1, le=10.0)  # 고가 확정 트리거: 1% 하락
    high_confirm_timeout_min: int = Field(default=10, ge=1, le=120)  # 고가 확정 후 N분 내 미체결 시 주문 취소

    # KOSPI 분할매수 (고가 대비 %)
    kospi_buy1_pct: float = Field(default=2.5, ge=0.1, le=15.0)
    kospi_buy2_pct: float = Field(default=3.5, ge=0.1, le=15.0)

    # KOSDAQ 분할매수
    kosdaq_buy1_pct: float = Field(default=3.75, ge=0.1, le=15.0)
    kosdaq_buy2_pct: float = Field(default=4.25, ge=0.1, le=15.0)

    # 매수 비중 (예수금 대비 %)
    buy1_ratio: float = Field(default=50.0, ge=1.0, le=100.0)
    buy2_ratio: float = Field(default=50.0, ge=1.0, le=100.0)


class ExitParams(BaseModel):
    profit_target_recovery_pct: float = Field(default=50.0, ge=1.0, le=100.0)  # 고가-저가 50% 회복
    timeout_from_low_min: int = Field(default=20, ge=1, le=120)  # 최저가 후 20분
    trend_break_check: bool = True                # higher lows 깨짐

    kospi_hard_stop_pct: float = Field(default=4.5, ge=0.1, le=30.0)  # KOSPI 하드 손절 (고가 대비 %)
    kosdaq_hard_stop_pct: float = Field(default=6.5, ge=0.1, le=30.0)  # KOSDAQ 하드 손절

    futures_drop_pct: float = Field(default=1.0, ge=0.1, le=20.0)  # 선물 급락 손절 (%)

    force_liquidate_time: str = "15:20"


class OrderParams(BaseModel):
    slippage_ticks: int = Field(default=2, ge=0, le=20)
    unfilled_timeout_sec: int = Field(default=30, ge=1, le=600)
    max_simultaneous_positions: int = Field(default=1, ge=1, le=10)


class RiskParams(BaseModel):
    daily_loss_limit_pct: float = Field(default=3.0, ge=0.1, le=50.0)
    max_position_size_pct: float = Field(default=100.0, ge=1.0, le=100.0)  # 예수금 대비 100%


class ApiParams(BaseModel):
    rate_limit_per_sec: int = Field(default=20, ge=1, le=100)
    screening_start: str = "09:45"                # 데이터 사전 수집 시작
    polling_interval_sec: int = Field(default=30, ge=1, le=300)


class StrategyParams(BaseModel):
    """strategy_params.yaml 전체 로드."""

    screening: ScreeningParams = ScreeningParams()
    multi_trade: MultiTradeParams = MultiTradeParams()
    entry: EntryParams = EntryParams()
    exit: ExitParams = ExitParams()
    order: OrderParams = OrderParams()
    risk: RiskParams = RiskParams()
    api: ApiParams = ApiParams()

    @classmethod
    def load(cls, path: str | Path | None = None) -> StrategyParams:
        if path is None:
            path = PROJECT_ROOT / "config" / "strategy_params.yaml"
        path = Path(path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()
