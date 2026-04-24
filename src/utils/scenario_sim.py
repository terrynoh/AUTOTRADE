"""What-if 시나리오 시뮬레이터.

실제 50% 회복 기준 매매 결과 아래에, 60% / 70% 기준이었다면 어떻게 체결됐을지
분봉 데이터로 시뮬레이션 + 포맷팅.

설계 원칙:
- pure function (매매 로직 건드리지 않음)
- v1 청산 분기: target hit / 11:20 강제청산 (hard_stop 제외 — 가설엔 적용 안 함)
- 분봉 해상도 한계 명시 (실제 체결은 초 단위)
- target 계산식: confirmed_high + (post_entry_low*) * recovery_pct / 100
  * post_entry_low 는 buy1_price 시점부터 시작, 매 분봉 low 로 갱신

사용:
    from src.utils.scenario_sim import simulate, format_scenario_block_html

    result = simulate(
        minute_chart=candles,
        confirmed_high=441_000,
        initial_low=432_500,       # buy1_price
        buy_time=datetime(2026, 4, 22, 10, 40, 15, tzinfo=KST),
        recovery_pct=60.0,
        force_time="11:20",
    )
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from src.utils.price_utils import floor_to_tick


# ── 결과 타입 ─────────────────────────────────────────────


@dataclass
class ScenarioResult:
    """시뮬레이션 결과.

    Attributes:
        outcome:
            "target"    — 목표가 도달 (정상 익절)
            "force"     — 11:20 강제청산
            "data_end"  — 분봉 데이터 소진 (아직 매매 진행 중)
        exit_time_str:   청산 시각 "HHMMSS" ("10:57" 분봉 = "105700")
        exit_price:      청산가 (target 분기=target, force 분기=해당 분봉 close)
        target_price:    시나리오 target 가 (target 분기에서만 의미 있음)
        final_low:       시뮬 도중 관찰된 최저가 (= 매도 기준이 된 low)
        candle_count:    buy_time 이후 처리한 분봉 수
    """
    outcome: Literal["target", "force", "data_end"]
    exit_time_str: str = ""
    exit_price: int = 0
    target_price: int = 0
    final_low: int = 0
    candle_count: int = 0


# ── 내부 헬퍼 ─────────────────────────────────────────────


def _time_to_hhmmss(dt: datetime) -> str:
    """datetime → 'HHMMSS' 6자리 문자열 (KIS 분봉 time 포맷과 일치)."""
    return dt.strftime("%H%M%S")


def _force_time_to_hhmmss(force_time: str) -> str:
    """'11:20' → '112000' 6자리 문자열."""
    parts = force_time.split(":")
    hh = parts[0].zfill(2)
    mm = parts[1].zfill(2) if len(parts) > 1 else "00"
    ss = parts[2].zfill(2) if len(parts) > 2 else "00"
    return f"{hh}{mm}{ss}"


def _calc_target(confirmed_high: int, low: int, recovery_pct: float) -> int:
    """recovery_pct 기준 목표가 계산. floor_to_tick 적용 (watcher.py:205 과 동일)."""
    if confirmed_high <= 0 or low <= 0:
        return 0
    raw = int(low + (confirmed_high - low) * recovery_pct / 100)
    return floor_to_tick(raw)


# ── 시뮬레이터 ────────────────────────────────────────────


def simulate(
    minute_chart: list[dict],
    confirmed_high: int,
    initial_low: int,
    buy_time: datetime,
    recovery_pct: float,
    force_time: str = "11:20",
) -> ScenarioResult:
    """분봉 데이터로 가설 recovery_pct 회복 시나리오 시뮬.

    알고리즘:
        1. buy_time 이후 분봉부터 순차 처리
        2. 각 분봉:
           - 11:20 이상이면 force 청산 (close 가격)
           - low 갱신: min(low, candle.low)
           - target = floor_to_tick(low + (confirmed_high - low) * pct / 100)
           - candle.high >= target 이면 target hit (target 가격에 체결)
        3. 분봉 소진 시 data_end

    Args:
        minute_chart: KIS get_minute_chart() 반환 형식의 dict 리스트
            [{"time": "HHMMSS", "open": int, "high": int, "low": int, "close": int, ...}, ...]
        confirmed_high: TRIGGERED 시점 신고가
        initial_low: 시뮬 시작 시점 low 값 (보통 buy1_price)
        buy_time: 매수 체결 시각 (KST datetime)
        recovery_pct: 회복 비율 (60.0, 70.0 등)
        force_time: 강제청산 시각 "HH:MM" 또는 "HH:MM:SS" (기본 "11:20")

    Returns:
        ScenarioResult
    """
    if confirmed_high <= 0 or initial_low <= 0 or not minute_chart:
        return ScenarioResult(outcome="data_end")

    buy_hhmmss = _time_to_hhmmss(buy_time)
    force_hhmmss = _force_time_to_hhmmss(force_time)

    # 분봉 데이터는 KIS 에서 최신→과거 순으로 내려오기도 하므로 시간순 정렬 보장
    candles = sorted(minute_chart, key=lambda c: c.get("time", ""))

    low = initial_low
    processed = 0

    for candle in candles:
        c_time = candle.get("time", "")
        if not c_time or len(c_time) < 6:
            continue

        # buy_time 이전 분봉은 skip (매수 전이므로 시뮬 대상 아님)
        if c_time < buy_hhmmss:
            continue

        # 강제청산 시각 도달
        if c_time >= force_hhmmss:
            return ScenarioResult(
                outcome="force",
                exit_time_str=c_time,
                exit_price=candle.get("close", 0),
                target_price=_calc_target(confirmed_high, low, recovery_pct),
                final_low=low,
                candle_count=processed,
            )

        processed += 1

        # low 갱신 (매수 후 저점)
        c_low = candle.get("low", 0)
        if c_low > 0 and c_low < low:
            low = c_low

        # target 재계산 (low 기준)
        target = _calc_target(confirmed_high, low, recovery_pct)

        # high 가 target 을 넘었으면 체결로 간주
        c_high = candle.get("high", 0)
        if target > 0 and c_high >= target:
            return ScenarioResult(
                outcome="target",
                exit_time_str=c_time,
                exit_price=target,
                target_price=target,
                final_low=low,
                candle_count=processed,
            )

    # 분봉 소진 (아직 매매 중)
    return ScenarioResult(
        outcome="data_end",
        target_price=_calc_target(confirmed_high, low, recovery_pct),
        final_low=low,
        candle_count=processed,
    )


# ── 포맷 헬퍼 ────────────────────────────────────────────


def _hhmmss_to_display(hhmmss: str) -> str:
    """'105700' → '10:57'."""
    if len(hhmmss) < 4:
        return hhmmss
    return f"{hhmmss[:2]}:{hhmmss[2:4]}"


def _holding_str(buy_time: datetime, exit_hhmmss: str) -> str:
    """매수 시각 + 청산 분봉 시각 → '약 N분' (분봉 해상도)."""
    if len(exit_hhmmss) < 6:
        return "-"
    try:
        hh = int(exit_hhmmss[:2])
        mm = int(exit_hhmmss[2:4])
        ss = int(exit_hhmmss[4:6])
        exit_dt = buy_time.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        delta_sec = int((exit_dt - buy_time).total_seconds())
        if delta_sec < 0:
            return "-"
        minutes = delta_sec // 60
        return f"약 {minutes}분"
    except Exception:
        return "-"


def _calc_pnl(
    exit_price: int,
    avg_buy_price: float,
    total_buy_qty: int,
    capital: int,
) -> tuple[int, float, float]:
    """청산가 기준 손익 계산 → (pnl_krw, pnl_pct, capital_pnl_pct)."""
    if total_buy_qty <= 0 or avg_buy_price <= 0:
        return (0, 0.0, 0.0)
    pnl = int((exit_price - avg_buy_price) * total_buy_qty)
    entry_amount = avg_buy_price * total_buy_qty
    pnl_pct = (pnl / entry_amount * 100) if entry_amount > 0 else 0.0
    capital_pnl_pct = (pnl / capital * 100) if capital > 0 else 0.0
    return (pnl, pnl_pct, capital_pnl_pct)


def format_scenario_block_html(
    recovery_pct: float,
    sim: ScenarioResult,
    avg_buy_price: float,
    total_buy_qty: int,
    buy_time: datetime,
    capital: int,
) -> str:
    """텔레그램 HTML 형식의 시나리오 블록 생성.

    사용자가 승인한 포맷:
        [60% 가설] target=437,000원
        청산    │ 437,000원 — 10:57분봉 hit ✅
        손익    │ +13,500원 (+1.04%)
        보유시간│ 약 16분
    """
    pct_int = int(recovery_pct)
    header = f"<b>[{pct_int}% 가설]</b>"

    if sim.outcome == "target":
        pnl, pnl_pct, capital_pnl_pct = _calc_pnl(
            sim.exit_price, avg_buy_price, total_buy_qty, capital
        )
        pnl_sign = "+" if pnl >= 0 else ""
        exit_disp = _hhmmss_to_display(sim.exit_time_str)
        holding = _holding_str(buy_time, sim.exit_time_str)
        return (
            f"{header} target={sim.target_price:,}원\n"
            f"청산    │ {sim.exit_price:,}원 — {exit_disp}분봉 hit ✅\n"
            f"손익    │ {pnl_sign}{pnl:,}원 ({pnl_sign}{pnl_pct:.2f}%)\n"
            f"투자금比│ {pnl_sign}{capital_pnl_pct:.2f}%\n"
            f"보유시간│ {holding}"
        )

    elif sim.outcome == "force":
        pnl, pnl_pct, capital_pnl_pct = _calc_pnl(
            sim.exit_price, avg_buy_price, total_buy_qty, capital
        )
        pnl_sign = "+" if pnl >= 0 else ""
        exit_disp = _hhmmss_to_display(sim.exit_time_str)
        holding = _holding_str(buy_time, sim.exit_time_str)
        return (
            f"{header} target={sim.target_price:,}원 (미도달)\n"
            f"청산    │ {sim.exit_price:,}원 — {exit_disp} 강제청산 🔴\n"
            f"손익    │ {pnl_sign}{pnl:,}원 ({pnl_sign}{pnl_pct:.2f}%)\n"
            f"투자금比│ {pnl_sign}{capital_pnl_pct:.2f}%\n"
            f"보유시간│ {holding}"
        )

    else:  # data_end
        return (
            f"{header} target={sim.target_price:,}원\n"
            f"청산    │ — (분봉 데이터 부족, 시뮬 불가) ⚪"
        )


def format_scenario_block_plain(
    recovery_pct: float,
    sim: ScenarioResult,
    avg_buy_price: float,
    total_buy_qty: int,
    buy_time: datetime,
    capital: int,
) -> str:
    """배치/파일용 plain text (HTML 태그 없음)."""
    pct_int = int(recovery_pct)
    header = f"[{pct_int}% 가설]"

    if sim.outcome == "target":
        pnl, pnl_pct, capital_pnl_pct = _calc_pnl(
            sim.exit_price, avg_buy_price, total_buy_qty, capital
        )
        pnl_sign = "+" if pnl >= 0 else ""
        exit_disp = _hhmmss_to_display(sim.exit_time_str)
        holding = _holding_str(buy_time, sim.exit_time_str)
        return (
            f"{header} target={sim.target_price:,}원\n"
            f"청산    │ {sim.exit_price:,}원 — {exit_disp}분봉 hit ✅\n"
            f"손익    │ {pnl_sign}{pnl:,}원 ({pnl_sign}{pnl_pct:.2f}%)\n"
            f"투자금比│ {pnl_sign}{capital_pnl_pct:.2f}%\n"
            f"보유시간│ {holding}"
        )

    elif sim.outcome == "force":
        pnl, pnl_pct, capital_pnl_pct = _calc_pnl(
            sim.exit_price, avg_buy_price, total_buy_qty, capital
        )
        pnl_sign = "+" if pnl >= 0 else ""
        exit_disp = _hhmmss_to_display(sim.exit_time_str)
        holding = _holding_str(buy_time, sim.exit_time_str)
        return (
            f"{header} target={sim.target_price:,}원 (미도달)\n"
            f"청산    │ {sim.exit_price:,}원 — {exit_disp} 강제청산 🔴\n"
            f"손익    │ {pnl_sign}{pnl:,}원 ({pnl_sign}{pnl_pct:.2f}%)\n"
            f"투자금比│ {pnl_sign}{capital_pnl_pct:.2f}%\n"
            f"보유시간│ {holding}"
        )

    else:  # data_end
        return (
            f"{header} target={sim.target_price:,}원\n"
            f"청산    │ — (분봉 데이터 부족, 시뮬 불가) ⚪"
        )


# ── 편의 함수 ────────────────────────────────────────────


def run_scenarios(
    minute_chart: list[dict],
    confirmed_high: int,
    initial_low: int,
    buy_time: datetime,
    recovery_pcts: Optional[list[float]] = None,
    force_time: str = "11:20",
) -> dict[float, ScenarioResult]:
    """여러 recovery_pct 를 한 번에 시뮬. {pct: ScenarioResult} dict 반환.

    기본: [60.0, 70.0] — 실제 50% 아래에 붙일 2개 시나리오.
    """
    if recovery_pcts is None:
        recovery_pcts = [60.0, 70.0]

    results = {}
    for pct in recovery_pcts:
        results[pct] = simulate(
            minute_chart=minute_chart,
            confirmed_high=confirmed_high,
            initial_low=initial_low,
            buy_time=buy_time,
            recovery_pct=pct,
            force_time=force_time,
        )
    return results
