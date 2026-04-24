"""ChannelResolver — 종목별 시세 채널 자동 분기 (R-17).

스크리닝 후 호출되어:
1. 종목들에 대해 UN + ST dual subscribe (10초 윈도우)
2. 종목별 push 누적 카운트
3. 10초 후 분기 결정 → 불필요한 채널 unsubscribe
4. Watcher 의 channel_used / channel_decided_at / push_count 메타 갱신

설계 원칙:
- ChannelResolver 는 KIS 레이어 (통신) 와 Coordinator (도메인) 사이에 위치
- 분기 결정은 push 카운트 기반 (단순, 빠른)
- ghost high 방지: active 중 main.py 가 coordinator 호출 skip
- 수동/정규 스크리닝 race 처리: start() active 중 재호출 시 reset + 재시작
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from loguru import logger

from src.kis_api.constants import WS_TR_PRICE_UN, WS_TR_PRICE_ST
from src.utils.market_calendar import now_kst


# 분기 윈도우 (수석님 결정 1: 10초)
DUAL_WINDOW_SEC = 10.0


@dataclass
class _PushCount:
    """종목별 dual subscribe 윈도우 동안의 push 누적."""
    code: str
    un_count: int = 0
    st_count: int = 0
    last_un_ts: Optional[datetime] = None
    last_st_ts: Optional[datetime] = None


class ChannelResolver:
    """시세 채널 자동 분기 (UN ↔ ST).

    사용 패턴:
        resolver = ChannelResolver(kis_api)
        coordinator.set_channel_resolver(resolver)  # 콜백 자동 등록
        await resolver.start([code1, code2, ...])
        # ... 10초 후 분기 자동 결정 + Watcher 메타 갱신 + 채널 정리

    주의:
        - is_active() == True 인 동안 main.py 는 coordinator 시세 라우팅을 skip
          (ghost high 방지, 09:50~09:50:10 의 10초만 — 신고가 감시 시작 09:55 이전)
        - active 중 재호출 시 reset + 재시작 (수동/정규 스크리닝 race 처리)
    """

    def __init__(self, kis_api) -> None:
        """
        Args:
            kis_api: KISAPI 인스턴스 (subscribe_realtime, unsubscribe_realtime 호출용)
        """
        self._kis = kis_api
        self._counts: dict[str, _PushCount] = {}
        self._resolved: dict[str, str] = {}     # code → 채택된 tr_id
        self._timer_task: Optional[asyncio.Task] = None
        self._on_decided: Optional[Callable[[str, str, int, int, datetime], None]] = None
        self._active: bool = False

    def set_channel_decided_callback(
        self, cb: Callable[[str, str, int, int, datetime], None]
    ) -> None:
        """채널 결정 콜백 등록.

        Args:
            cb: 시그니처 cb(code, channel_used, un_count, st_count, decided_at)
                WatcherCoordinator._on_channel_decided 를 등록함.
        """
        self._on_decided = cb

    def is_active(self) -> bool:
        """dual 윈도우 진행 중 여부 (main.py 의 시세 라우팅 가드용)."""
        return self._active

    async def start(self, codes: list[str]) -> None:
        """N 종목에 대해 dual subscribe 시작 + 10초 타이머.

        Args:
            codes: 구독 대상 종목 코드 리스트

        active 중 재호출 시 → reset 후 새 codes 로 재시작
        (수동/정규 스크리닝 race 처리).
        """
        if self._active:
            logger.warning(
                f"[ChannelResolver] 이미 active — reset 후 재시작 ({len(codes)} 종목)"
            )
            self.reset()

        if not codes:
            logger.info("[ChannelResolver] codes 비어있음 — skip")
            return

        self._active = True
        for code in codes:
            self._counts[code] = _PushCount(code=code)

        # Dual subscribe: UN 먼저, 그 다음 ST
        # _subscribed_codes dict — 두 번째 호출(ST)이 덮어씀.
        # _resolve() 에서 채택된 tr_id 로 재정정.
        await self._kis.subscribe_realtime(codes, tr_id=WS_TR_PRICE_UN)
        await self._kis.subscribe_realtime(codes, tr_id=WS_TR_PRICE_ST)
        logger.info(
            f"[ChannelResolver] dual subscribe 시작 ({len(codes)} 종목, "
            f"윈도우={DUAL_WINDOW_SEC}초)"
        )

        # 10초 타이머 시작
        self._timer_task = asyncio.create_task(self._wait_and_resolve())

    def on_realtime_price(self, price_data: dict) -> None:
        """실시간 시세 수신 핸들러 (main.py _on_realtime_price 가 위임).

        분기 결정 전 (active=True) 에만 카운트. 결정 후에는 무시
        (Coordinator 가 처리).

        Args:
            price_data: KIS WebSocket 에서 파싱한 dict
                        (code, tr_id, current_price, ... 포함)
        """
        if not self._active:
            return
        code = price_data.get("code")
        tr_id = price_data.get("tr_id")
        if not code or code not in self._counts:
            return
        ts = now_kst()
        if tr_id == WS_TR_PRICE_UN:
            self._counts[code].un_count += 1
            self._counts[code].last_un_ts = ts
        elif tr_id == WS_TR_PRICE_ST:
            self._counts[code].st_count += 1
            self._counts[code].last_st_ts = ts

    async def _wait_and_resolve(self) -> None:
        """10초 대기 후 분기 결정."""
        try:
            await asyncio.sleep(DUAL_WINDOW_SEC)
            await self._resolve()
        except asyncio.CancelledError:
            logger.info("[ChannelResolver] 타이머 취소됨")
        except Exception as e:
            logger.error(f"[ChannelResolver] 타이머 오류: {e}")
        finally:
            self._active = False

    async def _resolve(self) -> None:
        """종목별 분기 결정 + 불필요한 채널 unsubscribe + 콜백 호출."""
        decided_at = now_kst()
        un_to_drop: list[str] = []
        st_to_drop: list[str] = []

        for code, c in self._counts.items():
            if c.un_count > 0:
                # UN tick 수신 → NXT 활성 종목 → UN 채택
                channel = WS_TR_PRICE_UN
                st_to_drop.append(code)
            elif c.st_count > 0:
                # UN 0건 + ST 수신 → KRX-only 종목 → ST 채택
                channel = WS_TR_PRICE_ST
                un_to_drop.append(code)
            else:
                # 양쪽 0건 → UN 폴백 (다음 push 가능성 보존, 보수적)
                channel = WS_TR_PRICE_UN
                st_to_drop.append(code)
                logger.warning(
                    f"[ChannelResolver] {code}: dual 윈도우 양쪽 0건 — UN 폴백"
                )

            self._resolved[code] = channel
            logger.info(
                f"[ChannelResolver] {code}: 채널={channel} "
                f"(UN={c.un_count} ST={c.st_count})"
            )

            if self._on_decided:
                try:
                    self._on_decided(code, channel, c.un_count, c.st_count, decided_at)
                except Exception as e:
                    logger.error(f"[ChannelResolver] 콜백 오류 ({code}): {e}")

        # 불필요한 채널 unsubscribe
        if un_to_drop:
            await self._kis.unsubscribe_realtime(un_to_drop, tr_id=WS_TR_PRICE_UN)
        if st_to_drop:
            await self._kis.unsubscribe_realtime(st_to_drop, tr_id=WS_TR_PRICE_ST)

        # _subscribed_codes 정합 — 채택된 tr_id 로 재등록
        for code, channel in self._resolved.items():
            self._kis._subscribed_codes[code] = channel

        logger.info(f"[ChannelResolver] 분기 결정 완료 ({len(self._resolved)} 종목)")

    def get_channel(self, code: str) -> Optional[str]:
        """결정된 채널 조회.

        Args:
            code: 종목 코드

        Returns:
            WS_TR_PRICE_UN / WS_TR_PRICE_ST / None (미결정)
        """
        return self._resolved.get(code)

    def reset(self) -> None:
        """일별/재시작 리셋. active 중 호출 시 타이머 취소."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._counts.clear()
        self._resolved.clear()
        self._active = False
