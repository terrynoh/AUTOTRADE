"""
09:50 스크리닝 — 거래대금, 프로그램순매수비중, 상승률 기준 타겟 종목 선정.

ETF/ETN/스팩/우선주 제외.
상한가 도달 종목 제외 (장중 고가 == 상한가 → 눌림 전략 무효).
모의투자(paper/dry_run)에서는 프로그램매매 데이터 미제공 → 필터 건너뜀.
실매매(live)에서는 프로그램매매 필터 정상 적용.
"""
from __future__ import annotations

import re

from loguru import logger

from config.settings import StrategyParams
from src.kis_api.kis import KISAPI
from src.models.stock import StockCandidate, TradeTarget, MarketType


# ── 호가 단위 (상한가 계산용) ──
# KRX 주식 호가 단위: 가격대별 틱 사이즈
def _tick_size(price: int) -> int:
    """가격대에 따른 호가 단위 반환."""
    if price < 2_000:
        return 1
    elif price < 5_000:
        return 5
    elif price < 20_000:
        return 10
    elif price < 50_000:
        return 50
    elif price < 200_000:
        return 100
    elif price < 500_000:
        return 500
    else:
        return 1_000


def _upper_limit_price(prev_close: int, multiplier: float = 1.30) -> int:
    """전일종가 기준 상한가 계산 (호가단위 내림)."""
    raw = prev_close * multiplier
    tick = _tick_size(int(raw))
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

    def __init__(self, api: KISAPI, params: StrategyParams, is_live: bool = False, use_live_data: bool = False):
        self.api = api
        self.params = params
        self.is_live = is_live
        self.use_live_data = use_live_data or is_live

    async def run_manual(self, codes: list[str]) -> list[TradeTarget]:
        """
        수동 입력 종목 스크리닝.

        사용자가 입력한 종목코드 리스트를 받아서:
        1. 각 종목의 현재가/등락률/거래대금 조회
        2. 상승률 > 0% 필터
        3. 거래대금 ≥ 500억 필터
        4. 상한가 미도달 (< 20%) 필터
        5. 프로그램순매수비중 ≥ 5% 필터 (실매매만)
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
                name = price_info.get("name", code)
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

                candidates.append(StockCandidate(
                    code=code,
                    name=name,
                    market=market,
                    trading_volume_krw=trading_val,
                    program_net_buy=0,
                    price_change_pct=change_pct,
                    current_price=current_price,
                ))

            except Exception as e:
                logger.error(f"  {code} 현재가 조회 실패: {e}")
                continue

        logger.info(f"기본 필터 통과: {len(candidates)}종목")

        if not candidates:
            logger.warning("기본 필터 통과 종목 없음")
            return []

        # ── 5) 프로그램매매 순매수 비중 필터 ──
        if not self.use_live_data:
            logger.warning("모의투자 API: 프로그램매매 데이터 미제공 → 필터 건너뜀 (상승률 기준만 적용)")
            filtered = candidates
        else:
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

        # ── 6) 상승률 상위 1종목 선택 ──
        filtered.sort(key=lambda c: c.price_change_pct, reverse=True)
        pool_size = getattr(sp, 'top_n_candidates', sp.top_n_gainers)
        if pool_size > sp.top_n_gainers:
            pick_count = pool_size
        else:
            pick_count = sp.top_n_gainers
        top = filtered[:pick_count]

        targets = []
        for cand in top:
            target = TradeTarget(stock=cand, intraday_high=cand.current_price)
            targets.append(target)
            logger.info(
                f"타겟 선정: {cand.name}({cand.code}) {cand.market.value} "
                f"등락={cand.price_change_pct:+.2f}% 거래대금={cand.trading_volume_krw/1e8:.0f}억"
            )

        return targets

    async def run(self) -> list[TradeTarget]:
        """⚠ DEPRECATED (ISSUE-010): 수동 입력 방식으로 전환됨.

        프로덕션 호출 금지. run_manual(codes)를 사용하세요.

        이 메서드는 KIS API의 get_volume_rank를 호출하지만, market="Q" 미지원
        (ISSUE-007 3-2)으로 KOSDAQ 종목이 누락됩니다. 백테스트 호환성을 위해
        메서드는 보존되며, 다음 sprint에서 정리 예정입니다.
        """
        logger.warning(
            "screener.run() 호출됨 — 이 메서드는 DEPRECATED입니다 (ISSUE-010). "
            "run_manual(codes)를 사용하세요. 백테스트 외 호출 금지."
        )
        sp = self.params.screening
        logger.info(f"스크리닝 시작: 거래대금≥{sp.volume_min/1e8:.0f}억, 프로그램비중≥{sp.program_net_buy_ratio_min}%, 상승률 상위{sp.top_n_gainers}종목")

        # ── 1) 거래대금 상위 종목 수집 ──
        # KIS 모의투자: market="J"로 KOSPI+KOSDAQ 통합 조회 (market="Q" 미지원)
        candidates: list[StockCandidate] = []

        try:
            vol_list = await self.api.get_volume_rank(market="J", min_volume=0)
            logger.info(f"거래량순위(통합): {len(vol_list)}종목 수신")
        except Exception as e:
            logger.error(f"거래량순위 조회 실패: {e}")
            vol_list = []

        if not vol_list:
            logger.error("거래량순위 조회 실패: 빈 결과")
            return []

        if vol_list:
            for item in vol_list[:5]:
                logger.debug(f"  {item.get('name','')}({item.get('code','')}) 거래대금={item.get('trading_volume_krw',0)/1e8:.0f}억 등락={item.get('change_pct',0):+.2f}%")

        excluded_count = 0
        for item in vol_list:
            trading_val = item.get("trading_volume_krw", 0)
            if trading_val < sp.volume_min:
                continue

            code = item.get("code", "")
            name = item.get("name", "")

            # ── 2) ETF/ETN/스팩/우선주 제외 ──
            if _is_etf_or_excluded(code, name):
                excluded_count += 1
                logger.debug(f"  제외(ETF/ETN/스팩/우선주): {name}({code})")
                continue

            # ── 상한가 제외 ──
            change_pct = item.get("change_pct", 0.0)
            if change_pct >= sp.max_change_pct:
                excluded_count += 1
                logger.debug(f"  제외(상한가): {name}({code}) {change_pct:+.2f}%")
                continue

            # KOSDAQ 종목코드: 1,2,3으로 시작
            is_kosdaq = code[0] in ("1", "2", "3") if code else False

            candidates.append(StockCandidate(
                code=code,
                name=name,
                market=MarketType.KOSDAQ if is_kosdaq else MarketType.KOSPI,
                trading_volume_krw=trading_val,
                program_net_buy=0,
                price_change_pct=item.get("change_pct", 0.0),
                current_price=item.get("current_price", 0),
            ))

        logger.info(f"거래대금 {sp.volume_min/1e8:.0f}억 이상: {len(candidates) + excluded_count}종목 (ETF등 {excluded_count}종목 제외 → {len(candidates)}종목)")

        if not candidates:
            logger.warning("거래대금 조건 통과 종목 없음")
            return []

        # ── 3) 프로그램매매 순매수 비중 필터 ──
        if not self.use_live_data:
            # 모의투자 API는 프로그램매매 데이터 미제공
            # use_live_api=True 또는 live 모드 시 자동으로 필터 적용됨
            logger.warning("모의투자 API: 프로그램매매 데이터 미제공 → 필터 건너뜀 (상승률 기준만 적용)")
            filtered = candidates
        else:
            # 실매매: 프로그램매매 데이터 정상 조회 가능
            filtered = []
            for cand in candidates:
                try:
                    prog = await self.api.get_program_trade(cand.code)
                    cand.program_net_buy = prog.get("program_net_buy", 0)
                    ratio = cand.program_net_buy_ratio
                    passed = ratio >= sp.program_net_buy_ratio_min
                    logger.info(f"  {cand.name}({cand.code}) 프로그램순매수={cand.program_net_buy:,} 비중={ratio:.2f}% {'✓' if passed else '✗'}")
                except Exception as e:
                    logger.warning(f"{cand.name}({cand.code}) 프로그램매매 조회 실패: {e}")
                    continue

                if passed:
                    filtered.append(cand)

            logger.info(f"프로그램순매수비중 {sp.program_net_buy_ratio_min}% 이상: {len(filtered)}종목")

            if not filtered:
                logger.warning("프로그램순매수비중 조건 통과 종목 없음")
                return []

        # ── 4) 상한가 도달 종목 제외 (장중 고가 == 상한가) ──
        # 상한가에 도달한 종목은 더 이상 오를 수 없으므로 눌림 전략 무효
        infra = self.params.infra
        limit_check_threshold = infra.upper_limit_check_pct
        safe_filtered = []
        limit_excluded = 0

        for cand in filtered:
            if cand.price_change_pct >= limit_check_threshold:
                try:
                    price_info = await self.api.get_current_price(cand.code)
                    intraday_high = price_info.get("high", 0)
                    prev_close = price_info.get("open", 0)  # 시가 대용 (근사치)

                    # 전일종가 정확 계산: 현재가 / (1 + 등락률/100)
                    if cand.price_change_pct != 0:
                        prev_close_calc = int(cand.current_price / (1 + cand.price_change_pct / 100))
                    else:
                        prev_close_calc = cand.current_price

                    upper_limit = _upper_limit_price(prev_close_calc, infra.upper_limit_multiplier)

                    if intraday_high >= upper_limit:
                        limit_excluded += 1
                        logger.info(
                            f"  제외(상한가 도달): {cand.name}({cand.code}) "
                            f"고가={intraday_high:,} ≥ 상한가={upper_limit:,} "
                            f"(전일종가≈{prev_close_calc:,})"
                        )
                        continue
                    else:
                        # 고가 정보 업데이트
                        cand.current_price = price_info.get("current_price", cand.current_price)
                        logger.debug(
                            f"  상한가 체크 통과: {cand.name}({cand.code}) "
                            f"고가={intraday_high:,} < 상한가={upper_limit:,}"
                        )
                except Exception as e:
                    logger.warning(f"{cand.name}({cand.code}) 상한가 체크 실패: {e}")

            safe_filtered.append(cand)

        if limit_excluded > 0:
            logger.info(f"상한가 도달 제외: {limit_excluded}종목 → {len(safe_filtered)}종목 남음")

        # ── 5) 상승률 상위 N종목 ──
        safe_filtered.sort(key=lambda c: c.price_change_pct, reverse=True)
        # 멀티 트레이드 활성화 시 후보 풀 크기(top_n_candidates) 사용
        pool_size = getattr(sp, 'top_n_candidates', sp.top_n_gainers)
        if pool_size > sp.top_n_gainers:
            pick_count = pool_size
        else:
            pick_count = sp.top_n_gainers
        top = safe_filtered[:pick_count]

        targets = []
        for cand in top:
            # 시장 구분 정확 확인 (종목코드만으로는 KOSPI/KOSDAQ 구분 불가)
            try:
                price_info = await self.api.get_current_price(cand.code)
                market_name = price_info.get("market_name", "")
                if any(k in market_name.upper() for k in ("KOSDAQ", "KSQ")):
                    cand.market = MarketType.KOSDAQ
                else:
                    cand.market = MarketType.KOSPI
                # 최신 고가도 함께 업데이트
                cand.current_price = price_info.get("current_price", cand.current_price)
            except Exception as e:
                logger.warning(f"{cand.name}({cand.code}) 시장 구분 조회 실패: {e}")

            target = TradeTarget(stock=cand, intraday_high=cand.current_price)
            targets.append(target)
            logger.info(f"타겟 선정: {cand.name}({cand.code}) {cand.market.value} 등락={cand.price_change_pct:+.2f}% 거래대금={cand.trading_volume_krw/1e8:.0f}억")

        return targets
