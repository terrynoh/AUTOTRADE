"""R-17 ChannelResolver 단위 테스트."""
import asyncio
import sys
import os

# 프로젝트 루트를 sys.path 에 추가 (직접 실행 시)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from unittest.mock import AsyncMock, MagicMock
from src.core.channel_resolver import ChannelResolver, DUAL_WINDOW_SEC
from src.kis_api.constants import WS_TR_PRICE_UN, WS_TR_PRICE_ST


async def test_resolver():
    kis = MagicMock()
    kis.subscribe_realtime = AsyncMock()
    kis.unsubscribe_realtime = AsyncMock()
    kis._subscribed_codes = {}

    resolver = ChannelResolver(kis)
    decided = []
    resolver.set_channel_decided_callback(
        lambda c, ch, u, s, t: decided.append((c, ch, u, s))
    )

    await resolver.start(["AAA", "BBB", "CCC"])
    assert resolver.is_active() is True, "start() 후 is_active() 는 True 여야 함"

    # dual subscribe 호출 확인
    assert kis.subscribe_realtime.call_count == 2, \
        f"subscribe_realtime 은 UN+ST 2회 호출 필요, 실제={kis.subscribe_realtime.call_count}"

    # AAA: UN tick 5건 → UN 채택
    for _ in range(5):
        resolver.on_realtime_price({"code": "AAA", "tr_id": WS_TR_PRICE_UN, "current_price": 10000})

    # BBB: ST tick 3건 → ST 채택
    for _ in range(3):
        resolver.on_realtime_price({"code": "BBB", "tr_id": WS_TR_PRICE_ST, "current_price": 20000})

    # CCC: 양쪽 0건 → UN 폴백

    # active 중 카운트 확인
    assert resolver._counts["AAA"].un_count == 5, "AAA UN count 5 기대"
    assert resolver._counts["BBB"].st_count == 3, "BBB ST count 3 기대"

    # 10초 윈도우 대기
    await asyncio.sleep(DUAL_WINDOW_SEC + 0.5)

    assert resolver.is_active() is False, "윈도우 종료 후 is_active() 는 False 여야 함"

    # 분기 결정 콜백 확인
    decided_map = {c: (ch, u, s) for c, ch, u, s in decided}
    assert "AAA" in decided_map, "AAA 결정 콜백 미호출"
    assert decided_map["AAA"][0] == WS_TR_PRICE_UN, f"AAA 채널={decided_map['AAA'][0]} (UN 기대)"
    assert decided_map["AAA"][1] == 5, f"AAA UN count={decided_map['AAA'][1]} (5 기대)"

    assert "BBB" in decided_map, "BBB 결정 콜백 미호출"
    assert decided_map["BBB"][0] == WS_TR_PRICE_ST, f"BBB 채널={decided_map['BBB'][0]} (ST 기대)"
    assert decided_map["BBB"][2] == 3, f"BBB ST count={decided_map['BBB'][2]} (3 기대)"

    assert "CCC" in decided_map, "CCC 결정 콜백 미호출"
    assert decided_map["CCC"][0] == WS_TR_PRICE_UN, f"CCC 채널={decided_map['CCC'][0]} (UN 폴백 기대)"

    # get_channel 확인
    assert resolver.get_channel("AAA") == WS_TR_PRICE_UN, "get_channel('AAA') UN 기대"
    assert resolver.get_channel("BBB") == WS_TR_PRICE_ST, "get_channel('BBB') ST 기대"
    assert resolver.get_channel("CCC") == WS_TR_PRICE_UN, "get_channel('CCC') UN 폴백 기대"
    assert resolver.get_channel("XXX") is None, "get_channel('XXX') None 기대"

    # _subscribed_codes 재정정 확인
    assert kis._subscribed_codes.get("AAA") == WS_TR_PRICE_UN, \
        f"_subscribed_codes['AAA']={kis._subscribed_codes.get('AAA')} (UN 기대)"
    assert kis._subscribed_codes.get("BBB") == WS_TR_PRICE_ST, \
        f"_subscribed_codes['BBB']={kis._subscribed_codes.get('BBB')} (ST 기대)"

    # active 가드 — 두 번째 start 호출 (race 처리)
    # reset 후 재시작이므로 active=True
    await resolver.start(["DDD"])
    assert resolver.is_active() is True, "재시작 후 is_active() True 기대"
    resolver.reset()
    assert resolver.is_active() is False, "reset() 후 is_active() False 기대"

    print("PASS: ChannelResolver 분기 정확 + race 가드 정상")


if __name__ == "__main__":
    asyncio.run(test_resolver())
