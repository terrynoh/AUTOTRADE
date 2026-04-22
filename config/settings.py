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
    """환경변수 (.env) 기반 설정. LIVE 전용 (R15-005 이후)."""

    # 한국투자증권 KIS
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    kis_hts_id: str = ""  # R15-005: H0STCNI0 체결통보 구독 tr_key (HTS ID, 계좌번호 아님)

    # 텔레그램
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 대시보드
    dashboard_admin_token: str = ""
    dashboard_port: int = 8503
    dashboard_url: str = ""          # 로컬 sync용 Cloudflare 외부 URL

    # 로깅
    log_level: str = "DEBUG"

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
    }

    @property
    def account_no(self) -> str:
        return self.kis_account_no

    @property
    def trade_mode(self) -> str:
        """호환성: 로그/대시보드에서 표시용."""
        return "live"

    @property
    def is_live(self) -> bool:
        """호환성: R15-005 이후 항상 True."""
        return True


# ── 전략 파라미터 (YAML) ────────────────────────────────────────
class ScreeningParams(BaseModel):
    screening_time: str = "09:50"
    volume_min: int = Field(default=50_000_000_000, ge=0, le=1_000_000_000_000)  # 500억
    program_net_buy_ratio_min: float = Field(default=5.0, ge=0.0, le=100.0)  # 프로그램순매수비중 ≥ 5%
    program_net_buy_ratio_double: float = Field(default=10.0, ge=0.0, le=100.0)  # Double/Single 분기 기준 (≥10% = Double)
    max_change_pct: float = Field(default=29.5, ge=1.0, le=30.0)  # 상한가(+30%) 제외 임계값


class MultiTradeParams(BaseModel):
    enabled: bool = True                          # 멀티 트레이드 활성화
    repeat_start: str = "10:00"                   # 다음 종목 진입 가능 시작 시각
    repeat_end: str = "11:00"                     # 다음 종목 진입 가능 마감 시각 (last-line defense)
    profit_only: bool = False                     # 청산 사유 무관 다음 종목 진입 허용 (R-08)
    kospi_next_entry_max_pct: float = Field(default=3.8, ge=0.1, le=30.0)   # KOSPI 다음 종목 tiebreaker 필터
    kosdaq_next_entry_max_pct: float = Field(default=5.6, ge=0.1, le=30.0)  # KOSDAQ 다음 종목 tiebreaker 필터


class EntryParams(BaseModel):
    new_high_watch_start: str = "09:55"           # 신고가 감시 시작
    entry_deadline: str = "10:55"                 # 매수 진입 마감 시각 (W-15 2026-04-10: '11:00'→'10:55', yaml 동기화)
    high_confirm_drop_pct: float = Field(default=1.0, ge=0.1, le=10.0)  # 고가 확정 트리거: 1% 하락
    high_confirm_timeout_min: int = Field(default=20, ge=1, le=120)  # 고가 확정 후 N분 내 미체결 시 주문 취소 (W-15 2026-04-10: 10→20, W-13 yaml 동기화)

    # R-11: KOSPI Double (프로그램순매수비중 ≥10%)
    kospi_double_buy1_pct: float = Field(default=1.9, ge=0.1, le=15.0)
    kospi_double_buy2_pct: float = Field(default=2.4, ge=0.1, le=15.0)

    # R-11: KOSPI Single (프로그램순매수비중 <10%)
    kospi_single_buy1_pct: float = Field(default=2.5, ge=0.1, le=15.0)
    kospi_single_buy2_pct: float = Field(default=3.5, ge=0.1, le=15.0)

    # R-11: KOSDAQ Double (프로그램순매수비중 ≥10%) — KOSDAQ Single은 매매 제외
    kosdaq_double_buy1_pct: float = Field(default=2.9, ge=0.1, le=15.0)
    kosdaq_double_buy2_pct: float = Field(default=3.9, ge=0.1, le=15.0)

    # [DEPRECATED] 기존 파라미터 — 하위호환용
    kospi_buy1_pct: float = Field(default=2.5, ge=0.1, le=15.0)
    kospi_buy2_pct: float = Field(default=3.5, ge=0.1, le=15.0)
    kosdaq_buy1_pct: float = Field(default=3.5, ge=0.1, le=15.0)   # W-15 2026-04-10: 3.75→3.5, W-14 yaml 동기화
    kosdaq_buy2_pct: float = Field(default=5.5, ge=0.1, le=15.0)   # W-15 2026-04-10: 5.25→5.5, W-14 yaml 동기화

    # 매수 비중 (예수금 대비 %)
    buy1_ratio: float = Field(default=50.0, ge=1.0, le=100.0)
    buy2_ratio: float = Field(default=50.0, ge=1.0, le=100.0)


class ExitParams(BaseModel):
    profit_target_recovery_pct: float = Field(default=50.0, ge=1.0, le=100.0)  # 고가-저가 50% 회복
    timeout_from_low_min: int = Field(default=20, ge=1, le=120)  # 최저가 후 20분

    # R-11: 하드 손절 — Double/Single 분기 (고가 대비 %)
    kospi_double_hard_stop_pct: float = Field(default=3.0, ge=0.1, le=30.0)
    kospi_single_hard_stop_pct: float = Field(default=4.0, ge=0.1, le=30.0)
    kosdaq_double_hard_stop_pct: float = Field(default=4.4, ge=0.1, le=30.0)  # KOSDAQ Single은 매매 제외

    # [DEPRECATED] 기존 파라미터 — 하위호환용
    kospi_hard_stop_pct: float = Field(default=4.1, ge=0.1, le=30.0)
    kosdaq_hard_stop_pct: float = Field(default=6.15, ge=0.1, le=30.0)

    futures_drop_pct: float = Field(default=1.0, ge=0.1, le=20.0)  # 선물 급락 손절 (%)

    timeout_start_after_kst: str = "10:00"  # 타임아웃 가드: 이 시각 이전 최저가는 타이머 시작 안 함

    force_liquidate_time: str = "11:20"                              # 강제 청산 시각 (W-15 2026-04-10: '15:20'→'11:20', 매매 철학 동기화)


class OrderParams(BaseModel):
    slippage_ticks: int = Field(default=2, ge=0, le=20)
    unfilled_timeout_sec: int = Field(default=30, ge=1, le=600)
    max_simultaneous_positions: int = Field(default=1, ge=1, le=10)


class RiskParams(BaseModel):
    daily_loss_limit_pct: float = Field(default=3.0, ge=0.1, le=50.0)
    max_position_size_pct: float = Field(default=100.0, ge=1.0, le=100.0)  # 예수금 대비 100%
    max_hard_stops_daily: int = Field(default=2, ge=1, le=10)  # 1회=strict, 2회=halt (2026-04-22 의미 재정의)
    strict_mode_program_ratio_threshold: float = Field(default=18.0, ge=0.0, le=100.0)
    strict_mode_market_whitelist: list[str] = Field(default_factory=lambda: ["KOSPI"])


class ApiParams(BaseModel):
    rate_limit_per_sec: int = Field(default=20, ge=1, le=100)
    screening_start: str = "09:45"                # 데이터 사전 수집 시작
    polling_interval_sec: int = Field(default=30, ge=1, le=300)


class MarketParams(BaseModel):
    open_time: str = "09:00"
    close_time: str = "15:30"
    report_time: str = "15:30"


class InfraParams(BaseModel):
    # HTTP
    http_timeout_total_sec: int = Field(default=10, ge=1, le=120)
    http_timeout_connect_sec: int = Field(default=5, ge=1, le=60)
    http_retry_delay_sec: float = Field(default=1.0, ge=0.1, le=30.0)

    # WebSocket
    ws_ping_interval_sec: int = Field(default=30, ge=5, le=120)
    ws_timeout_sec: int = Field(default=60, ge=10, le=300)
    ws_max_backoff_sec: float = Field(default=30.0, ge=1.0, le=300.0)

    # 헬스체크
    health_check_interval_sec: int = Field(default=5, ge=1, le=60)

    # 대시보드
    dashboard_log_buffer_size: int = Field(default=200, ge=50, le=10000)
    dashboard_log_return_size: int = Field(default=100, ge=10, le=5000)

    # 로깅 — 파일당 10MB 롤링, 7일 보관 (최대 ~70MB)
    log_rotation: str = "10 MB"
    log_retention_main: str = "7 days"
    log_retention_trade: str = "7 days"
    log_retention_error: str = "7 days"

    # 상한가 체크
    upper_limit_check_pct: float = Field(default=20.0, ge=1.0, le=30.0)
    upper_limit_multiplier: float = Field(default=1.30, ge=1.0, le=1.50)


class WSRuntimeParams(BaseModel):
    """W-31 WebSocket 런타임 로깅 (임시, 검증 종료 후 삭제).

    Obsidian/W-31_WebSocket_런타임로깅_검증.md §4.5 참조.
    """
    enabled: bool = False                         # W-31 관측 활성화 (기본 False)
    observe_codes: list[str] = Field(default_factory=list)  # 격리 구독 리스트
    rest_polling_interval_sec: float = Field(default=1.0, ge=0.1, le=60.0)


class StrategyParams(BaseModel):
    """strategy_params.yaml 전체 로드."""

    screening: ScreeningParams = ScreeningParams()
    multi_trade: MultiTradeParams = MultiTradeParams()
    entry: EntryParams = EntryParams()
    exit: ExitParams = ExitParams()
    order: OrderParams = OrderParams()
    risk: RiskParams = RiskParams()
    api: ApiParams = ApiParams()
    market: MarketParams = MarketParams()
    infra: InfraParams = InfraParams()
    ws_runtime: WSRuntimeParams = WSRuntimeParams()  # W-31 임시

    @classmethod
    def load(cls, path: str | Path | None = None) -> StrategyParams:
        if path is None:
            path = PROJECT_ROOT / "config" / "strategy_params.yaml"
        path = Path(path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # R16: simulation 섹션 무시 (DRY_RUN 폐기)
            data.pop("simulation", None)
            return cls(**data)
        return cls()
