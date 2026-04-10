"""
B-01 백테스트 단순 시뮬레이터
- R-08 매매 명세 (CLAUDE.md §2 동결본) 1:1 별도 구현
- 운영 코드 import 0건 (from src... 0줄)
- yaml 읽기 전용 로드
- 정합 검증은 별도 트랙. 본 결과는 분석/참고용.
"""
import json
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).parent / "data" / "minute"
MARKET_MAP_PATH = Path(__file__).parent / "data" / "market_map.json"
YAML_PATH = ROOT / "config" / "strategy_params.yaml"
RESULT_PATH = Path(__file__).parent / "result_2026-04-10.md"

INITIAL_CASH = 50_000_000
TRADE_DATE = "2026-04-10"


# ── 시각 형식 정규화 ─────────────────────────────────────────
# KIS 분봉 time 필드: "HHMMSS" (e.g. "090100")
# yaml 파라미터: "HH:MM" (e.g. "09:55")
# 시뮬 내부 통일 형식: "HH:MM"

def normalize_time(t: str) -> str:
    """HHMMSS 또는 HH:MM → HH:MM"""
    t = str(t)
    if ":" in t:
        return t[:5]
    return f"{t[:2]}:{t[2:4]}"


# ── 호가 단위 (KRX 2023 개편 후, CLAUDE.md §2) ───────────────

def get_tick_size(price: int) -> int:
    if price < 2000:    return 1
    if price < 5000:    return 5
    if price < 20000:   return 10
    if price < 50000:   return 50
    if price < 200000:  return 100
    if price < 500000:  return 500
    return 1000


def floor_to_tick(price: float) -> int:
    p = int(price)
    t = get_tick_size(p)
    return (p // t) * t


def ceil_to_tick(price: float) -> int:
    p = int(price)
    t = get_tick_size(p)
    return ((p + t - 1) // t) * t


# ── 상태 머신 ────────────────────────────────────────────────

class State(Enum):
    WATCHING  = "WATCHING"
    TRIGGERED = "TRIGGERED"
    ENTERED   = "ENTERED"
    EXITED    = "EXITED"
    SKIPPED   = "SKIPPED"
    DROPPED   = "DROPPED"


@dataclass
class Watcher:
    code: str
    market: str  # "KOSPI" or "KOSDAQ"
    bars: list   # [{"time": "HH:MM", "close": int, ...}] — 정규화 완료

    state: State = State.WATCHING
    intraday_high: int = 0
    intraday_high_time: str = ""
    confirmed_high: int = 0
    triggered_at: str = ""
    target_buy1: int = 0
    hard_stop: int = 0

    entered_at: str = ""
    entered_price: int = 0
    post_entry_low: int = 0
    post_entry_low_time: str = ""
    target_exit: int = 0

    exited_at: str = ""
    exit_price: int = 0
    exit_reason: str = ""
    pnl: int = 0
    pnl_pct: float = 0.0

    def is_terminal(self) -> bool:
        return self.state in (State.EXITED, State.SKIPPED, State.DROPPED)


# ── 파라미터 (yaml 동결값) ────────────────────────────────────

@dataclass
class Params:
    new_high_watch_start: str
    entry_deadline: str
    repeat_start: str
    repeat_end: str
    force_liquidate_time: str
    high_confirm_drop_pct: float
    high_confirm_timeout_min: int
    kospi_buy1_pct: float
    kosdaq_buy1_pct: float
    kospi_hard_stop_pct: float
    kosdaq_hard_stop_pct: float
    timeout_from_low_min: int
    profit_target_recovery_pct: float
    kospi_next_entry_max_pct: float
    kosdaq_next_entry_max_pct: float
    daily_loss_limit_pct: float
    timeout_start_after_kst: str


def load_params() -> Params:
    raw = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    return Params(
        new_high_watch_start      = raw["entry"]["new_high_watch_start"],
        entry_deadline            = raw["entry"]["entry_deadline"],
        repeat_start              = raw["multi_trade"]["repeat_start"],
        repeat_end                = raw["multi_trade"]["repeat_end"],
        force_liquidate_time      = raw["exit"]["force_liquidate_time"],
        high_confirm_drop_pct     = raw["entry"]["high_confirm_drop_pct"],
        high_confirm_timeout_min  = raw["entry"]["high_confirm_timeout_min"],
        kospi_buy1_pct            = raw["entry"]["kospi_buy1_pct"],
        kosdaq_buy1_pct           = raw["entry"]["kosdaq_buy1_pct"],
        kospi_hard_stop_pct       = raw["exit"]["kospi_hard_stop_pct"],
        kosdaq_hard_stop_pct      = raw["exit"]["kosdaq_hard_stop_pct"],
        timeout_from_low_min      = raw["exit"]["timeout_from_low_min"],
        profit_target_recovery_pct= raw["exit"]["profit_target_recovery_pct"],
        kospi_next_entry_max_pct  = raw["multi_trade"]["kospi_next_entry_max_pct"],
        kosdaq_next_entry_max_pct = raw["multi_trade"]["kosdaq_next_entry_max_pct"],
        daily_loss_limit_pct      = raw["risk"]["daily_loss_limit_pct"],
        timeout_start_after_kst   = raw["exit"]["timeout_start_after_kst"],
    )


# ── 헬퍼 ─────────────────────────────────────────────────────

def time_to_min(hhmm: str) -> int:
    """'HH:MM' → 분 단위 정수"""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def bar_at(bars: list, hhmm: str) -> Optional[dict]:
    for b in bars:
        if b["time"] == hhmm:
            return b
    return None


def calc_buy1(high: int, market: str, p: Params) -> int:
    pct = p.kospi_buy1_pct if market == "KOSPI" else p.kosdaq_buy1_pct
    return floor_to_tick(high * (1 - pct / 100))


def calc_hard_stop(high: int, market: str, p: Params) -> int:
    pct = p.kospi_hard_stop_pct if market == "KOSPI" else p.kosdaq_hard_stop_pct
    return ceil_to_tick(high * (1 - pct / 100))


def get_pullback_pct(confirmed_high: int, current_price: int) -> float:
    if confirmed_high <= 0:
        return 0.0
    return (confirmed_high - current_price) / confirmed_high * 100


# ── 매 분 watcher 갱신 (WATCHING → TRIGGERED) ────────────────

def update_watcher(w: Watcher, hhmm: str, p: Params, log: list) -> None:
    if w.is_terminal():
        return
    if w.state != State.WATCHING:
        return

    if hhmm < p.new_high_watch_start:
        return

    bar = bar_at(w.bars, hhmm)
    if not bar:
        return
    close = bar["close"]

    # 신고가 갱신
    if close > w.intraday_high:
        w.intraday_high = close
        w.intraday_high_time = hhmm

    # 신고가 + high_confirm_drop_pct% 하락 시 TRIGGERED
    if (
        w.intraday_high > 0
        and close <= w.intraday_high * (1 - p.high_confirm_drop_pct / 100)
    ):
        w.state = State.TRIGGERED
        w.triggered_at = hhmm
        w.confirmed_high = w.intraday_high
        w.target_buy1 = calc_buy1(w.confirmed_high, w.market, p)
        w.hard_stop = calc_hard_stop(w.confirmed_high, w.market, p)
        log.append(
            f"  [{w.code}] {hhmm} TRIGGERED "
            f"high={w.confirmed_high:,} buy1={w.target_buy1:,} stop={w.hard_stop:,}"
        )


# ── 4 청산 조건 평가 ──────────────────────────────────────────

def check_exit(w: Watcher, hhmm: str, bar: dict, p: Params) -> Optional[str]:
    close = bar["close"]

    # 1. 11:20 강제 청산
    if hhmm >= p.force_liquidate_time:
        return "force_liquidate"

    # 2. 하드 손절
    if close <= w.hard_stop:
        return "hard_stop"

    # 3. 목표가 도달
    if w.target_exit > 0 and close >= w.target_exit:
        return "profit_target"

    # 4. 타임아웃 (저점 갱신 후 N분, timeout_start_after_kst 이후 저점만)
    if w.post_entry_low_time and w.post_entry_low_time >= p.timeout_start_after_kst:
        elapsed = time_to_min(hhmm) - time_to_min(w.post_entry_low_time)
        if elapsed >= p.timeout_from_low_min:
            return "timeout"

    return None


# ── 메인 시뮬레이션 ───────────────────────────────────────────

def simulate():
    p = load_params()
    market_map = json.loads(MARKET_MAP_PATH.read_text(encoding="utf-8"))

    # Watcher 로드 (bar time 정규화: HHMMSS → HH:MM)
    watchers: dict[str, Watcher] = {}
    missing = 0
    for f in sorted(DATA_DIR.glob("*.json")):
        code = f.stem
        if code not in market_map:
            missing += 1
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        raw_bars = data.get("bars", [])
        # time 필드 정규화
        bars = [
            {**b, "time": normalize_time(b["time"])}
            for b in raw_bars
        ]
        if not bars:
            missing += 1
            continue
        watchers[code] = Watcher(code=code, market=market_map[code], bars=bars)

    log = []
    log.append(f"watchers 로드: {len(watchers)} (분봉 없음/매핑 누락: {missing})")

    if missing >= 5:
        print(f"⛔ 분봉 결측 종목 {missing} ≥ 5. 멈춤 조건 2 발동.")
        return {}, [], log, p

    # 시각 시퀀스 (09:55 ~ 11:30, HH:MM 포맷)
    times = sorted({
        b["time"]
        for w in watchers.values()
        for b in w.bars
        if p.new_high_watch_start <= b["time"] <= "11:30"
    })

    active_code: Optional[str] = None
    chain: list = []  # [(code, entered_at, exited_at, exit_reason, pnl, pnl_pct)]

    for hhmm in times:
        # ① 모든 watcher 신고가/TRIGGERED 갱신
        for w in watchers.values():
            update_watcher(w, hhmm, p, log)

        # ② active 종목 청산 평가
        if active_code:
            w = watchers[active_code]
            bar = bar_at(w.bars, hhmm)
            if bar:
                close = bar["close"]

                # post_entry_low 갱신 + 목표가 재계산
                if w.post_entry_low == 0 or close < w.post_entry_low:
                    if hhmm >= p.timeout_start_after_kst:
                        w.post_entry_low = close
                        w.post_entry_low_time = hhmm
                    elif w.post_entry_low == 0:
                        w.post_entry_low = close
                    # 목표가 = (confirmed_high + post_entry_low) / 2
                    w.target_exit = floor_to_tick(
                        (w.confirmed_high + w.post_entry_low) / 2
                    )

                reason = check_exit(w, hhmm, bar, p)
                if reason:
                    w.state = State.EXITED
                    w.exited_at = hhmm
                    w.exit_price = close
                    w.exit_reason = reason
                    w.pnl = close - w.entered_price
                    w.pnl_pct = w.pnl / w.entered_price * 100
                    chain.append((
                        w.code, w.entered_at, w.exited_at,
                        reason, w.pnl, w.pnl_pct
                    ))
                    active_code = None
                    log.append(
                        f"  [{w.code}] {hhmm} EXITED "
                        f"reason={reason} price={close:,} "
                        f"pnl={w.pnl:+,} ({w.pnl_pct:+.2f}%)"
                    )

        # ③ active 없고 진입 윈도우 안: 새 진입 시도
        if (
            not active_code
            and p.repeat_start <= hhmm <= p.entry_deadline
        ):
            candidates = []
            for w in watchers.values():
                if w.state != State.TRIGGERED:
                    continue
                bar = bar_at(w.bars, hhmm)
                if not bar:
                    continue
                close = bar["close"]
                # 매수가 도달 (close <= target_buy1) + 손절선 위
                if w.target_buy1 >= close > w.hard_stop:
                    pb = get_pullback_pct(w.confirmed_high, close)
                    candidates.append((w, pb, close))

            if candidates:
                # T2/T3 (chain 이미 있음): tiebreaker 임계 필터 적용
                if chain:
                    thresh_filtered = []
                    for w, pb, price in candidates:
                        thr = (
                            p.kospi_next_entry_max_pct
                            if w.market == "KOSPI"
                            else p.kosdaq_next_entry_max_pct
                        )
                        if pb >= thr:
                            thresh_filtered.append((w, pb, price))
                    candidates = thresh_filtered

                if candidates:
                    # 눌림폭 최대 종목 선정
                    candidates.sort(key=lambda x: -x[1])
                    w, pb, price = candidates[0]
                    w.state = State.ENTERED
                    w.entered_at = hhmm
                    w.entered_price = price
                    w.post_entry_low = price
                    w.post_entry_low_time = hhmm if hhmm >= p.timeout_start_after_kst else ""
                    w.target_exit = floor_to_tick(
                        (w.confirmed_high + w.post_entry_low) / 2
                    )
                    active_code = w.code
                    log.append(
                        f"  [{w.code}] {hhmm} ENTERED "
                        f"price={price:,} pullback={pb:.2f}% "
                        f"market={w.market} chain#{len(chain)+1}"
                    )

    # 시뮬 종료 후 미처리 상태 정리
    for w in watchers.values():
        if not w.is_terminal():
            if w.state == State.WATCHING:
                w.state = State.SKIPPED
                w.exit_reason = "no_trigger"
            elif w.state == State.TRIGGERED:
                w.state = State.SKIPPED
                w.exit_reason = "no_buy_fill"
            elif w.state == State.ENTERED:
                # 11:30 이후에도 청산 안 된 경우 (11:20 청산이 정상이지만 바 없을 경우)
                w.state = State.SKIPPED
                w.exit_reason = "no_exit_bar"

    return watchers, chain, log, p


# ── 리포트 생성 ───────────────────────────────────────────────

def generate_report(watchers: dict, chain: list, log: list, p: Params) -> str:
    lines = []
    lines.append(f"# B-01 백테스트 결과 — {TRADE_DATE}")
    lines.append("")
    lines.append("> **본 시뮬레이터는 운영 코드와 분리된 reference implementation.**")
    lines.append("> 정합 검증은 별도 트랙. 본 결과는 분석/참고용.")
    lines.append("")
    lines.append("## 단순화 표")
    lines.append("| 항목 | 운영 | 시뮬 |")
    lines.append("|---|---|---|")
    lines.append("| 매수 분할 | buy1 50% + buy2 50% | buy1 체결 = full position |")
    lines.append("| T2 시점 | buy2 체결 시점 | T1 ENTERED 시점 |")
    lines.append("| T3 시점 | T1 EXITED 직후 | T1 EXITED 직후 (동일) |")
    lines.append("| tiebreaker | 눌림폭 최대 | 동일 |")
    lines.append("| tick | 실시간 ws | close only 분봉 (boundary effect 있음) |")
    lines.append("| 선물 청산 | KOSPI200 선물 -1% | 데이터 부재 → 비활성 |")
    lines.append("| active 슬롯 | 1 | 1 (동일) |")
    lines.append("| 호가 단위 | floor(매수)/ceil(손절) | 동일 |")
    lines.append("")
    lines.append("## 입력 종목 상태 분포")
    counts: dict[str, int] = {}
    for w in watchers.values():
        if w.state == State.SKIPPED:
            key = f"SKIPPED({w.exit_reason})"
        else:
            key = w.state.value
        counts[key] = counts.get(key, 0) + 1
    for k in sorted(counts):
        lines.append(f"- {k}: {counts[k]}")
    lines.append("")

    lines.append(f"## 매매 chain ({len(chain)} 건)")
    if not chain:
        lines.append("- 매매 0건 (TRIGGERED 도달 없음 또는 진입 윈도우 미도달)")
    else:
        lines.append("| # | 종목 | 시장 | 진입시각 | 청산시각 | 청산사유 | P&L(원) | 수익률 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, (code, ent, exi, reason, pnl, pnl_pct) in enumerate(chain, 1):
            mkt = watchers[code].market
            lines.append(
                f"| {i} | {code} | {mkt} | {ent} | {exi} "
                f"| {reason} | {pnl:+,} | {pnl_pct:+.2f}% |"
            )
    lines.append("")

    if chain:
        total_pnl = sum(c[4] for c in chain)
        total_pct = total_pnl / INITIAL_CASH * 100
        wins   = sum(1 for c in chain if c[4] > 0)
        losses = sum(1 for c in chain if c[4] < 0)
        lines.append("## 손익 요약")
        lines.append(f"- 총 매매: {len(chain)} 건")
        lines.append(f"- 승/패: {wins} / {losses}")
        lines.append(f"- 누적 P&L: {total_pnl:+,} 원")
        lines.append(
            f"- 누적 수익률: {total_pct:+.3f}% "
            f"(예수금 {INITIAL_CASH:,} 기준)"
        )
        daily_loss_hit = total_pct <= -p.daily_loss_limit_pct
        lines.append(
            f"- daily_loss_limit ({p.daily_loss_limit_pct}%) 도달: "
            f"{'⚠️ YES — 경고' if daily_loss_hit else 'NO'}"
        )
        lines.append("")

    lines.append("## TRIGGERED 종목 상세")
    triggered = [w for w in watchers.values() if w.confirmed_high > 0]
    if triggered:
        triggered.sort(key=lambda x: x.triggered_at or "99:99")
        lines.append("| 종목 | 시장 | 신고가 | 고가시각 | TRIGGERED | target_buy1 | hard_stop | 결과 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for w in triggered:
            result = w.state.value
            if w.exit_reason:
                result = f"{result}({w.exit_reason})"
            lines.append(
                f"| {w.code} | {w.market} | {w.confirmed_high:,} "
                f"| {w.intraday_high_time} | {w.triggered_at} "
                f"| {w.target_buy1:,} | {w.hard_stop:,} | {result} |"
            )
    else:
        lines.append("- TRIGGERED 도달 종목 0건")
    lines.append("")

    lines.append("## 활성 파라미터 (config/strategy_params.yaml 로드)")
    lines.append(f"- new_high_watch_start: {p.new_high_watch_start}")
    lines.append(f"- entry_deadline: {p.entry_deadline}")
    lines.append(f"- 진입 윈도우: {p.repeat_start} ~ {p.entry_deadline}")
    lines.append(f"- repeat_end (last-line): {p.repeat_end}")
    lines.append(f"- force_liquidate_time: {p.force_liquidate_time}")
    lines.append(f"- high_confirm_drop_pct: {p.high_confirm_drop_pct}%")
    lines.append(f"- high_confirm_timeout_min: {p.high_confirm_timeout_min}분 (시뮬 미구현)")
    lines.append(f"- KOSPI  buy1: -{p.kospi_buy1_pct}%, hard_stop: -{p.kospi_hard_stop_pct}%")
    lines.append(f"- KOSDAQ buy1: -{p.kosdaq_buy1_pct}%, hard_stop: -{p.kosdaq_hard_stop_pct}%")
    lines.append(f"- timeout_from_low_min: {p.timeout_from_low_min}분")
    lines.append(f"- KOSPI/KOSDAQ next_entry tiebreaker: {p.kospi_next_entry_max_pct}% / {p.kosdaq_next_entry_max_pct}%")
    lines.append("")
    lines.append("## 시뮬레이터 한계 (boundary effect)")
    lines.append("- **close only tick**: intrabar 움직임 미반영. 실제 진입가/청산가와 차이 가능")
    lines.append("- **high_confirm_timeout (20분)**: 미구현. TRIGGERED → 미체결 시 SKIPPED 처리 없음")
    lines.append("- **선물 청산**: 데이터 부재로 비활성 (결정 4)")
    lines.append("- **매수 분할**: buy1/buy2 대신 단일 진입 (full position)")
    lines.append("- **슬리피지**: 미반영")
    lines.append("")
    return "\n".join(lines)


# ── 엔트리포인트 ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"B-01 단순 시뮬레이터 시작")
    print("=" * 60)

    watchers, chain, log, p = simulate()

    for line in log:
        print(line)

    print("\n" + "=" * 60)
    triggered_n = sum(1 for w in watchers.values() if w.confirmed_high > 0)
    entered_n   = sum(1 for w in watchers.values() if w.state == State.ENTERED
                      or (w.state in (State.EXITED, State.SKIPPED) and w.entered_price > 0))
    print(f"watchers 로드: {len(watchers)}")
    print(f"TRIGGERED 도달: {triggered_n}")
    print(f"매매 chain: {len(chain)} 건")
    if chain:
        total = sum(c[4] for c in chain)
        total_pct = total / INITIAL_CASH * 100
        print(f"누적 P&L: {total:+,} 원 ({total_pct:+.3f}%)")
        if total_pct <= -p.daily_loss_limit_pct:
            print(f"⚠️ daily_loss_limit ({p.daily_loss_limit_pct}%) 초과 — 리포트에 경고 포함")
    print("=" * 60)

    report = generate_report(watchers, chain, log, p)
    RESULT_PATH.write_text(report, encoding="utf-8")
    print(f"\n리포트 저장: {RESULT_PATH}")
    print(f"파일 크기: {RESULT_PATH.stat().st_size:,} bytes")
