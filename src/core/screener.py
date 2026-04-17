"""
09:50 스크리닝 — 거래대금, 프로그램순매수비중, 상승률 기준 타겟 종목 선정.

ETF/ETN/스팩/우선주 제외.
상한가 도달 종목 제외 (장중 고가 == 상한가 → 눌림 전략 무효).

R16: LIVE 전용 (paper/dry_run 폐기). 프로그램매매 필터 항상 적용.
"""
from __future__ import annotations

import re

from loguru import logger

from config.settings import StrategyParams
from src.core.stock_master import StockMaster
from src.kis_api.kis import KISAPI
from src.models.stock import StockCandidate, MarketType
from src.utils.price_utils import get_tick_size


def _upper_limit_price(prev_close: int, multiplier: float = 1.30) -> int:
    """전일종가 기준 상한가 계산 (호가단위 내림)."""
    raw = prev_close * multiplier
    tick = get_tick_size(int(raw))
    return int(raw // tick) * tick


# ── ETF/ETN/스팩/우선주 제외 패턴 ──
# ETF: 종목명에 "KODEX", "TIGER", "KOSEF", "KBSTAR", "HANARO", "SOL", "ACE", "ARIRANG" 등
# ETN: 종목명에 "ETN" 포함
# 스팩: 종목명에 "스팩" 포함
# 우선주: 종목코드 끝자리 5,7,8,9 또는 종목명 끝 "우", "우B", "우C"
ETF_NAME_PATTERNS = re.compile(
    r"KODEX|TIGER|KOSEF|KBSTAR|HANARO|SOL |ACE |ARIRANG|PLUS |BNK|"
    r"마이다스|파워|미래에셋|삼성 레버|삼성 인버|신한 |KB |"
    r"히어로|타이거|코덱스",
    re.IGNORECASE,
)
EXCLUDE_NAME_PATTERNS = re.compile(r"ETN|스팩|SPAC", re.IGNORECASE)
PREFER_SUFFIX = re.compile(r"우$|우B$|우C$")


def _is_etf_or_excluded(code: str, name: str) -> bool:
    """ETF/ETN/스팩/우선주 여부 판별."""
    # ETF 종목명 패턴
    if ETF_NAME_PATTERNS.search(name):
        return True
    # ETN/스팩
    if EXCLUDE_NAME_PATTERNS.search(name):
        return True
    # 우선주: 코드 끝자리 5,7,8,9
    if code and code[-1] in ("5", "7", "8", "9"):
        return True
    # 종목명 끝 "우", "우B", "우C"
    if PREFER_SUFFIX.search(name):
        return True
    return False


class Screener:
    """09:50 스크리닝 엔진."""

    def __init__(self, api: KISAPI, params: StrategyParams, stock_master: StockMaster):
        self.api = api
        self.params = params
        self._stock_master = stock_master

    async def run_manual(self, codes: list[str]) -> list[StockCandidate]:
        """
        수동 입력 종목 스크리닝.

        사용자가 입력한 종목코드 리스트를 받아서:
        1. 각 종목의 현재가/등락률/거래대금 조회
        2. 상승률 > 0% 필터
        3. 거래대금 ≥ 500억 필터
        4. 상한가 미도달 (< 20%) 필터
        5. 프로그램순매수비중 ≥ 5% 필터
        6. 상승률 최고 1종목 선택
        """
        sp = self.params.screening
        logger.info(f"수동 스크리닝 시작: {len(codes)}종목 입력 ({', '.join(codes)})")

        if not codes:
            logger.warning("입력 종목 없음")
            return []

        # ── 1) 각 종목 현재가 조회 ──
        candidates: list[StockCandidate] = []
        for code in codes:
            code = code.strip()
            if not code:
                continue
            try:
                price_info = await self.api.get_current_price(code)
                api_name = price_info.get("name", "").strip()
                name = api_name if api_name else self._stock_master.lookup_name(code, default=code)
                current_price = price_info.get("current_price", 0)
                change_pct = price_info.get("change_pct", 0.0)
                trading_val = price_info.get("trading_value", 0)
                market_name = price_info.get("market_name", "")

                # 시장 구분 (KSQ150 등 KSQ 변형 처리)
                if any(k in market_name.upper() for k in ("KOSDAQ", "KSQ")):
                    market = MarketType.KOSDAQ
                else:
                    market = MarketType.KOSPI

                logger.info(
                    f"  {name}({code}) {market.value} "
                    f"현재가={current_price:,} 등락={change_pct:+.2f}% "
                    f"거래대금={trading_val/1e8:.0f}억"
                )

                # ── 2) 상승률 > 0% 필터 ──
                if change_pct <= 0:
                    logger.info(f"  → 제외(하락/보합): {name}({code}) {change_pct:+.2f}%")
                    continue

                # ── 3) 거래대금 ≥ 500억 필터 ──
                if trading_val < sp.volume_min:
                    logger.info(f"  → 제외(거래대금 부족): {name}({code}) {trading_val/1e8:.0f}억 < {sp.volume_min/1e8:.0f}억")
                    continue

                # ── 4) 상한가 미도달 필터 ──
                infra = self.params.infra
                if change_pct >= infra.upper_limit_check_pct:
                    # 상한가 정밀 체크
                    intraday_high = price_info.get("high", 0)
                    if change_pct != 0:
                        prev_close_calc = int(current_price / (1 + change_pct / 100))
                    else:
                        prev_close_calc = current_price
                    upper_limit = _upper_limit_price(prev_close_calc, infra.upper_limit_multiplier)
                    if intraday_high >= upper_limit:
                        logger.info(
                            f"  → 제외(상한가 도달): {name}({code}) "
                            f"고가={intraday_high:,} ≥ 상한가={upper_limit:,}"
                        )
                        continue

                # R-09: 당일 고가 조회 (pre_955_high 초기화용)
                intraday_high = price_info.get("high", current_price) or current_price

                candidates.append(StockCandidate(
                    code=code,
                    name=name,
                    market=market,
                    trading_volume_krw=trading_val,
                    program_net_buy=0,
                    price_change_pct=change_pct,
                    current_price=current_price,
                    intraday_high=intraday_high,
                ))

            except Exception as e:
                logger.error(f"  {code} 현재가 조회 실패: {e}")
                continue

        logger.info(f"기본 필터 통과: {len(candidates)}종목")

        if not candidates:
            logger.warning("기본 필터 통과 종목 없음")
            return []

        # ── 5) 프로그램매매 순매수 비중 필터 (LIVE 전용) ──
        filtered = []
        for cand in candidates:
            try:
                prog = await self.api.get_program_trade(cand.code)
                cand.program_net_buy = prog.get("program_net_buy", 0)
                ratio = cand.program_net_buy_ratio
                passed = ratio >= sp.program_net_buy_ratio_min
                logger.info(
                    f"  {cand.name}({cand.code}) 프로그램순매수={cand.program_net_buy:,} "
                    f"비중={ratio:.2f}% {'통과' if passed else '미달'}"
                )
            except Exception as e:
                logger.warning(f"{cand.name}({cand.code}) 프로그램매매 조회 실패: {e}")
                continue

            if passed:
                filtered.append(cand)

        logger.info(f"프로그램순매수비중 {sp.program_net_buy_ratio_min}% 이상: {len(filtered)}종목")

        if not filtered:
            logger.warning("프로그램순매수비중 조건 통과 종목 없음")
            return []

        # ── R-11: KOSDAQ Single 제외 (비중 <10%) ──
        double_threshold = sp.program_net_buy_ratio_double
        final_filtered = []
        for cand in filtered:
            ratio = cand.program_net_buy_ratio
            if cand.market == MarketType.KOSDAQ and ratio < double_threshold:
                logger.info(
                    f"  → 제외(KOSDAQ Single): {cand.name}({cand.code}) "
                    f"비중={ratio:.2f}% < {double_threshold}%"
                )
                continue
            final_filtered.append(cand)

        if len(final_filtered) < len(filtered):
            logger.info(
                f"KOSDAQ Single 제외: {len(filtered)} → {len(final_filtered)}종목"
            )
        filtered = final_filtered

        if not filtered:
            logger.warning("KOSDAQ Single 제외 후 남은 종목 없음")
            return []

        # ── 6) 상승률 내림차순 정렬 후 전체 반환 (top_n 제한 없음, R-08)
        filtered.sort(key=lambda c: c.price_change_pct, reverse=True)

        targets = []
        for cand in filtered:
            targets.append(cand)
            logger.info(
                f"타겟 선정: {cand.name}({cand.code}) {cand.market.value} "
                f"등락={cand.price_change_pct:+.2f}% 거래대금={cand.trading_volume_krw/1e8:.0f}억"
            )

        return targets
