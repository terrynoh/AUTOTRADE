"""KRX 호가 단위 보정 유틸리티 (W-12-rev2).

KRX 주식 지정가 주문은 가격대별 호가 단위를 맞춰야 접수됨.
위반 시 KIS API 거부 (rt_cd != "0").

참고: KRX 호가 단위 기준 (2024년 기준, 주식·ETF 공통)
  1,000원 미만      → 1원
  1,000원~5,000원  → 5원
  5,000원~20,000원 → 10원
  20,000원~50,000원 → 50원
  50,000원~200,000원 → 100원
  200,000원~500,000원 → 500원
  500,000원 이상    → 1,000원
"""


def get_tick_size(price: int) -> int:
    """가격대에 따른 호가 단위 반환.

    Args:
        price: 기준 가격 (원)

    Returns:
        해당 가격대의 호가 단위 (원)
    """
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


def floor_to_tick(price: int) -> int:
    """가격을 호가 단위로 내림 보정.

    매수 지정가에 사용 — 실제 매수가를 더 낮게 설정하여 안전 마진 확보.

    Args:
        price: 원시 계산 가격 (원)

    Returns:
        호가 단위로 내림한 가격

    Examples:
        >>> floor_to_tick(567_259)  # 50만 이상 → 1,000원 단위
        567000
        >>> floor_to_tick(567_000)  # 이미 단위 맞음
        567000
    """
    tick = get_tick_size(price)
    return (price // tick) * tick


def ceil_to_tick(price: int) -> int:
    """가격을 호가 단위로 올림 보정.

    손절가에 사용 — 손절 기준을 더 높게 설정하여 손실을 조기 차단.

    Args:
        price: 원시 계산 가격 (원)

    Returns:
        호가 단위로 올림한 가격

    Examples:
        >>> ceil_to_tick(559_097)  # 50만 이상 → 1,000원 단위
        560000
        >>> ceil_to_tick(559_000)  # 이미 단위 맞음
        559000
    """
    tick = get_tick_size(price)
    remainder = price % tick
    if remainder == 0:
        return price
    return price + (tick - remainder)


def round_to_tick(price: int, mode: str = "floor") -> int:
    """가격을 호가 단위로 보정 (mode 선택).

    Args:
        price: 원시 계산 가격 (원)
        mode: "floor" (내림, 기본값) 또는 "ceil" (올림)

    Returns:
        호가 단위로 보정된 가격

    Raises:
        ValueError: mode 가 "floor" / "ceil" 외의 값인 경우
    """
    if mode == "floor":
        return floor_to_tick(price)
    elif mode == "ceil":
        return ceil_to_tick(price)
    else:
        raise ValueError(f"mode must be 'floor' or 'ceil', got: {mode!r}")
