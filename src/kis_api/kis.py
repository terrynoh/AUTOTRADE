"""
한국투자증권 KIS Open API 클라이언트.

REST API로 시세 조회/주문, WebSocket으로 실시간 체결가 수신.
출처: https://github.com/koreainvestment/open-trading-api
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import time
from datetime import datetime, timedelta

from src.utils.market_calendar import now_kst
from pathlib import Path
from typing import Callable, Optional

import aiohttp
import websockets
from loguru import logger

# ── 토큰 파일 캐시 유틸 ───────────────────────────────────────────
_TOKEN_CACHE_DIR = Path(__file__).resolve().parent.parent.parent  # PROJECT_ROOT


def _token_cache_path(is_paper: bool) -> Path:
    return _TOKEN_CACHE_DIR / ("token_paper.json" if is_paper else "token_live.json")


def _key_hash(app_key: str) -> str:
    return hashlib.sha256(app_key.encode()).hexdigest()[:16]


def _save_token_cache(path: Path, token: str, expires_at: datetime,
                      app_key: str, ws_key: str = "") -> None:
    """토큰 정보를 파일에 atomic하게 저장."""
    data = {
        "access_token": token,
        "expires_at": expires_at.isoformat(),
        "app_key_hash": _key_hash(app_key),
        "ws_key": ws_key,
    }
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)  # Windows: rename()은 기존 파일 있으면 실패, replace()는 덮어씀
        # 파일 권한 설정: owner-only (600)
        try:
            os.chmod(str(path), stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass  # Windows는 Unix chmod 미지원 — 무시
    except Exception as e:
        logger.warning(f"토큰 캐시 저장 실패: {e}")


def _load_token_cache(path: Path, app_key: str) -> dict | None:
    """유효한 캐시 파일이면 dict 반환, 아니면 None."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # API 키 변경 여부 확인
        if data.get("app_key_hash") != _key_hash(app_key):
            logger.info("토큰 캐시 무효: API 키 변경됨 → 재발급")
            return None
        # 만료 확인
        expires_at = datetime.fromisoformat(data["expires_at"])
        if now_kst() >= expires_at:
            logger.info("토큰 캐시 만료 → 재발급")
            return None
        return data
    except Exception as e:
        logger.warning(f"토큰 캐시 읽기 실패: {e}")
        return None

from src.kis_api.constants import (
    BASE_URL_REAL,
    BASE_URL_PAPER,
    WS_URL_REAL,
    WS_URL_PAPER,
    EP_TOKEN,
    EP_WEBSOCKET_KEY,
    EP_PRICE,
    EP_VOLUME_RANK,
    EP_PROGRAM_TRADE_TODAY,
    EP_PROGRAM_TRADE_BY_STOCK,
    EP_ORDER,
    EP_ORDER_CANCEL,
    EP_BALANCE,
    EP_MINUTE_CHART,
    TR_PRICE,
    TR_VOLUME_RANK,
    TR_PROGRAM_TRADE_TODAY,
    TR_PROGRAM_TRADE_BY_STOCK,
    TR_ORDER_BUY,
    TR_ORDER_SELL,
    TR_ORDER_BUY_PAPER,
    TR_ORDER_SELL_PAPER,
    TR_ORDER_CANCEL,
    TR_ORDER_CANCEL_PAPER,
    TR_BALANCE,
    TR_BALANCE_PAPER,
    TR_MINUTE_CHART,
    WS_TR_PRICE,
    WS_TR_FUTURES,
    FUTURES_KOSPI200_NEAR,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    RATE_LIMIT_REAL,
    RATE_LIMIT_PAPER,
)


class KISAPI:
    """한국투자증권 REST + WebSocket 클라이언트."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        is_paper: bool = True,
        infra_params: object | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.is_paper = is_paper

        # 인프라 파라미터 (None이면 기본값 사용)
        from config.settings import InfraParams
        self._infra = infra_params if infra_params else InfraParams()

        self.base_url = BASE_URL_PAPER if is_paper else BASE_URL_REAL
        self.ws_url = WS_URL_PAPER if is_paper else WS_URL_REAL

        # 계좌번호 분리: 앞 8자리(CANO) + 뒤 2자리(ACNT_PRDT_CD)
        self._cano = account_no[:8]
        self._acnt_prdt_cd = account_no[8:] if len(account_no) > 8 else "01"

        self._token: str = ""
        self._token_expires: datetime = datetime.min
        self._ws_key: str = ""
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None

        self._realtime_callbacks: dict[str, list[Callable]] = {}
        self._subscribed_codes: set[str] = set()
        self._futures_subscribed: bool = False  # 선물 구독 상태
        self._prog_trade_logged: bool = False
        self._ws_connected: bool = False  # WebSocket 연결 상태
        self._ws_last_recv: float = 0.0   # 마지막 수신 시각 (time.time())
        self._on_ws_disconnect: Optional[Callable] = None  # 연결 끊김 콜백

        # rate limit: Semaphore(1) + 최소 간격으로 초당 요청 수 제한
        self._min_interval = 0.5 if is_paper else (1.0 / RATE_LIMIT_REAL)
        self._rate_limiter = asyncio.Semaphore(1)
        self._last_request_time: float = 0.0

    # ── 세션 관리 ──────────────────────────────────────────────

    async def connect(self):
        """세션 생성 + 토큰 발급."""
        timeout = aiohttp.ClientTimeout(
            total=self._infra.http_timeout_total_sec,
            connect=self._infra.http_timeout_connect_sec,
        )
        self._session = aiohttp.ClientSession(timeout=timeout)
        await self._get_token()
        logger.info(f"KIS API 연결 완료 ({'모의투자' if self.is_paper else '실거래'})")

    async def disconnect(self):
        """세션 종료."""
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("KIS API 연결 해제")

    # ── 인증 ───────────────────────────────────────────────────

    async def _get_token(self):
        """OAuth2 접근 토큰 발급/갱신. 파일 캐시 우선 사용."""
        # 1. 메모리 캐시 유효
        if self._token and now_kst() < self._token_expires:
            return

        # 2. 파일 캐시 확인
        cache = _load_token_cache(_token_cache_path(self.is_paper), self.app_key)
        if cache:
            self._token = cache["access_token"]
            self._token_expires = datetime.fromisoformat(cache["expires_at"])
            if cache.get("ws_key"):
                self._ws_key = cache["ws_key"]
            logger.info(f"KIS 토큰 캐시 로드 (만료: {self._token_expires.strftime('%Y-%m-%d %H:%M')})")
            return

        # 3. 신규 발급
        url = f"{self.base_url}{EP_TOKEN}"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        async with self._session.post(url, json=body) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise ConnectionError(f"토큰 발급 실패: {data}")

            self._token = data["access_token"]
            expires_in = data.get("expires_in", 86400)
            self._token_expires = now_kst() + timedelta(seconds=expires_in - 3600)
            _save_token_cache(_token_cache_path(self.is_paper), self._token,
                              self._token_expires, self.app_key, self._ws_key)
            logger.info(f"KIS 토큰 발급 완료 (만료: {self._token_expires.strftime('%Y-%m-%d %H:%M')})")

    async def _get_ws_key(self) -> str:
        """WebSocket 접속 키 발급. 파일 캐시 우선 사용."""
        # 1. 메모리 캐시
        if self._ws_key:
            return self._ws_key

        # 2. 파일 캐시
        cache = _load_token_cache(_token_cache_path(self.is_paper), self.app_key)
        if cache and cache.get("ws_key"):
            self._ws_key = cache["ws_key"]
            logger.info("WebSocket 키 캐시 로드")
            return self._ws_key

        # 3. 신규 발급
        url = f"{self.base_url}{EP_WEBSOCKET_KEY}"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,  # WebSocket은 secretkey 사용
        }

        async with self._session.post(url, json=body) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise ConnectionError(f"WebSocket 키 발급 실패: {data}")

            self._ws_key = data["approval_key"]
            # 파일 캐시 갱신 (ws_key 추가)
            _save_token_cache(_token_cache_path(self.is_paper), self._token,
                              self._token_expires, self.app_key, self._ws_key)
            logger.info("WebSocket 키 발급 완료")
            return self._ws_key

    def _auth_headers(self, tr_id: str) -> dict:
        """인증 헤더 생성."""
        return {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8",
            "authorization": f"Bearer {self._token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    # ── HTTP 요청 공통 ─────────────────────────────────────────

    async def _request(
        self,
        method: str,
        endpoint: str,
        tr_id: str,
        params: dict | None = None,
        body: dict | None = None,
    ) -> dict:
        """rate-limited HTTP 요청. GET은 일시적 오류 시 최대 2회 재시도."""
        await self._get_token()

        # GET 요청만 재시도 (POST/주문은 중복 실행 방지)
        max_retries = 2 if method == "GET" else 0
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            async with self._rate_limiter:
                now = time.monotonic()
                elapsed = now - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)

                url = f"{self.base_url}{endpoint}"
                headers = self._auth_headers(tr_id)

                try:
                    if method == "GET":
                        async with self._session.get(url, headers=headers, params=params) as resp:
                            self._last_request_time = time.monotonic()
                            # 5xx 에러 시 재시도
                            if resp.status >= 500 and attempt < max_retries:
                                logger.warning(f"HTTP {resp.status} [{tr_id}], 재시도 {attempt + 1}/{max_retries}")
                                await asyncio.sleep(self._infra.http_retry_delay_sec)
                                continue
                            data = await resp.json()
                    elif method == "POST":
                        async with self._session.post(url, headers=headers, json=body) as resp:
                            self._last_request_time = time.monotonic()
                            data = await resp.json()
                    else:
                        raise ValueError(f"지원하지 않는 HTTP method: {method}")

                    rt_cd = data.get("rt_cd")
                    if rt_cd and rt_cd != "0":
                        msg = data.get("msg1", "알 수 없는 에러")
                        logger.error(f"KIS API 에러 [{tr_id}]: {msg}")
                        raise RuntimeError(f"KIS API 에러: {msg}")

                    return data

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_exc = e
                    if attempt < max_retries:
                        logger.warning(f"HTTP 요청 실패 [{tr_id}], 재시도 {attempt + 1}/{max_retries}: {e}")
                        await asyncio.sleep(1.0)
                        continue
                    logger.error(f"HTTP 요청 실패 [{tr_id}]: {e}")
                    raise

        # max_retries 소진 시 (5xx 재시도 경로)
        raise RuntimeError(f"KIS API 요청 실패 [{tr_id}]: 최대 재시도 횟수 초과")

    async def _get(self, endpoint: str, tr_id: str, params: dict | None = None) -> dict:
        return await self._request("GET", endpoint, tr_id, params=params)

    async def _post(self, endpoint: str, tr_id: str, body: dict | None = None) -> dict:
        return await self._request("POST", endpoint, tr_id, body=body)

    # ── 시세 조회 ──────────────────────────────────────────────

    async def get_current_price(self, code: str) -> dict:
        """주식 현재가 조회."""
        params = {
            "FID_COND_MRKT_DIV_CODE": "UN",
            "FID_INPUT_ISCD": code,
        }
        data = await self._get(EP_PRICE, TR_PRICE, params)
        output = data.get("output", {})
        return {
            "code": code,
            "name": output.get("hts_kor_isnm", ""),
            "current_price": int(output.get("stck_prpr", "0")),
            "change_pct": float(output.get("prdy_ctrt", "0")),
            "volume": int(output.get("acml_vol", "0")),
            "trading_value": int(output.get("acml_tr_pbmn", "0")),
            "market_cap": int(output.get("hts_avls", "0")) * 100_000_000,
            "high": int(output.get("stck_hgpr", "0")),
            "low": int(output.get("stck_lwpr", "0")),
            "open": int(output.get("stck_oprc", "0")),
            "market_name": output.get("rprs_mrkt_kor_name", ""),
        }

    async def get_volume_rank(
        self,
        market: str = "J",
        min_volume: int = 0,
    ) -> list[dict]:
        """거래량순위 + 거래대금순위 + 급등종목 통합 조회.

        KIS API 순위 조회 3종 병합:
          - 20171 (거래량 상위 30종목) — 소형주 포착
          - 20176 (거래대금 상위 30종목) — 대형주 포착 (삼성SDI, SK하이닉스 등)
          - 20170 (급등률 상위 30종목) — 테마주 포착
        세 결과를 합쳐서 중복 제거 후 반환.
        """
        base_params = {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000",
            "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "0",
            "FID_VOL_CNT": "0",
            "FID_INPUT_DATE_1": "",
        }

        seen_codes: set[str] = set()
        results: list[dict] = []

        # 세 가지 순위 조회: 거래량(20171) + 거래대금(20176) + 급등률(20170)
        for scr_code, label in [("20171", "거래량"), ("20176", "거래대금"), ("20170", "급등률")]:
            params = {**base_params, "FID_COND_SCR_DIV_CODE": scr_code}
            try:
                data = await self._get(EP_VOLUME_RANK, TR_VOLUME_RANK, params)
            except Exception as e:
                logger.warning(f"{label}순위 조회 실패: {e}")
                continue

            count = 0
            for item in data.get("output", []):
                code = item.get("mksc_shrn_iscd", "")
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)

                trading_value = int(item.get("acml_tr_pbmn", "0"))
                if trading_value >= min_volume:
                    results.append({
                        "code": code,
                        "name": item.get("hts_kor_isnm", ""),
                        "current_price": int(item.get("stck_prpr", "0")),
                        "change_pct": float(item.get("prdy_ctrt", "0")),
                        "trading_volume_krw": trading_value,
                        "volume": int(item.get("acml_vol", "0")),
                    })
                    count += 1
            logger.debug(f"{label}순위(SCR={scr_code}): {count}종목 수집")

        return results

    async def get_program_trade(self, code: str) -> dict:
        """종목별 프로그램매매 조회.

        program-trade-by-stock (FHPPG04650101) 엔드포인트 사용.
        시간대별 누적 프로그램 순매수 데이터를 반환하며,
        최신(마지막) 레코드에 현재까지의 누적값이 들어있다.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "UN",
            "FID_INPUT_ISCD": code,
        }
        data = await self._get(EP_PROGRAM_TRADE_BY_STOCK, TR_PROGRAM_TRADE_BY_STOCK, params)

        output = data.get("output", [])

        # 디버그: 실제 응답 구조 로깅 (최초 1회)
        if not self._prog_trade_logged:
            self._prog_trade_logged = True
            logger.debug(f"프로그램매매 응답 키: {list(data.keys())}")
            if isinstance(output, list) and output:
                logger.debug(f"프로그램매매 output[0] 키: {list(output[0].keys())}")
                logger.debug(f"프로그램매매 output[0]: {output[0]}")
            else:
                logger.debug(f"프로그램매매 output: {output}")
            logger.debug(f"프로그램매매 rt_cd={data.get('rt_cd')}, msg1={data.get('msg1')}")

        # output이 시간대별 리스트 — 첫 번째가 최신(누적값)
        if isinstance(output, list) and output:
            latest = output[0]
        else:
            return {"program_net_buy": 0, "buy_amount": 0, "sell_amount": 0}

        net_buy = int(latest.get("whol_smtn_ntby_tr_pbmn", "0"))
        buy_amt = int(latest.get("whol_smtn_shnu_tr_pbmn", "0"))
        sell_amt = int(latest.get("whol_smtn_seln_tr_pbmn", "0"))

        return {
            "program_net_buy": net_buy,
            "buy_amount": buy_amt,
            "sell_amount": sell_amt,
        }

    async def get_minute_chart(self, code: str) -> list[dict]:
        """1분봉 차트 조회."""
        now = now_kst()
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "UN",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": now.strftime("%H%M%S"),
            "FID_PW_DATA_INCU_YN": "N",
        }
        data = await self._get(EP_MINUTE_CHART, TR_MINUTE_CHART, params)

        candles = []
        for item in data.get("output2", []):
            candles.append({
                "time": item.get("stck_cntg_hour", ""),
                "open": int(item.get("stck_oprc", "0")),
                "high": int(item.get("stck_hgpr", "0")),
                "low": int(item.get("stck_lwpr", "0")),
                "close": int(item.get("stck_prpr", "0")),
                "volume": int(item.get("cntg_vol", "0")),
            })
        return candles

    # ── 주문 ───────────────────────────────────────────────────

    async def buy_order(
        self,
        code: str,
        qty: int,
        price: int = 0,
        price_type: str = ORDER_TYPE_LIMIT,
    ) -> dict:
        """매수 주문."""
        tr_id = TR_ORDER_BUY_PAPER if self.is_paper else TR_ORDER_BUY

        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": price_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price) if price_type == ORDER_TYPE_LIMIT else "0",
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": "0",
        }
        data = await self._post(EP_ORDER, tr_id, body)
        output = data.get("output", {})

        logger.info(f"매수주문 접수: {code} {qty}주 @ {price}원 ({price_type})")
        return {
            "order_no": output.get("ODNO", ""),
            "order_time": output.get("ORD_TMD", ""),
            "code": code,
            "qty": qty,
            "price": price,
            "side": "buy",
        }

    async def sell_order(
        self,
        code: str,
        qty: int,
        price: int = 0,
        price_type: str = ORDER_TYPE_LIMIT,
    ) -> dict:
        """매도 주문."""
        tr_id = TR_ORDER_SELL_PAPER if self.is_paper else TR_ORDER_SELL

        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": price_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price) if price_type == ORDER_TYPE_LIMIT else "0",
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01",
            "CNDT_PRIC": "0",
        }
        data = await self._post(EP_ORDER, tr_id, body)
        output = data.get("output", {})

        logger.info(f"매도주문 접수: {code} {qty}주 @ {price}원 ({price_type})")
        return {
            "order_no": output.get("ODNO", ""),
            "order_time": output.get("ORD_TMD", ""),
            "code": code,
            "qty": qty,
            "price": price,
            "side": "sell",
        }

    # ── 주문 취소 ──────────────────────────────────────────────

    async def cancel_order(self, order_no: str, code: str) -> dict:
        """주문 취소."""
        tr_id = TR_ORDER_CANCEL_PAPER if self.is_paper else TR_ORDER_CANCEL

        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02: 취소
            "ORD_QTY": "0",              # 0: 전량 취소
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        data = await self._post(EP_ORDER_CANCEL, tr_id, body)
        output = data.get("output", {})

        logger.info(f"주문취소 접수: {order_no} ({code})")
        return {
            "order_no": output.get("ODNO", ""),
            "code": code,
        }

    # ── 잔고 조회 ──────────────────────────────────────────────

    async def get_balance(self) -> dict:
        """계좌 잔고 조회."""
        tr_id = TR_BALANCE_PAPER if self.is_paper else TR_BALANCE

        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = await self._get(EP_BALANCE, tr_id, params)

        holdings = []
        for item in data.get("output1", []):
            qty = int(item.get("hldg_qty", "0"))
            if qty <= 0:
                continue
            holdings.append({
                "code": item.get("pdno", ""),
                "name": item.get("prdt_name", ""),
                "qty": qty,
                "buy_price": int(float(item.get("pchs_avg_pric", "0"))),
                "current_price": int(item.get("prpr", "0")),
                "profit": int(item.get("evlu_pfls_amt", "0")),
                "profit_pct": float(item.get("evlu_pfls_rt", "0")),
            })

        summary = data.get("output2", [{}])
        if isinstance(summary, list) and summary:
            summary = summary[0]

        return {
            "holdings": holdings,
            "total_eval": int(summary.get("tot_evlu_amt", "0")),
            "total_profit": int(summary.get("evlu_pfls_smtl_amt", "0")),
            "available_cash": int(summary.get("dnca_tot_amt", "0")),
        }

    # ── WebSocket 실시간 ──────────────────────────────────────

    def add_realtime_callback(self, tr_id: str, callback: Callable):
        """실시간 데이터 콜백 등록."""
        if tr_id not in self._realtime_callbacks:
            self._realtime_callbacks[tr_id] = []
        # 중복 콜백 방지
        if callback not in self._realtime_callbacks[tr_id]:
            self._realtime_callbacks[tr_id].append(callback)

    def clear_realtime_callbacks(self):
        """모든 실시간 콜백 초기화."""
        self._realtime_callbacks.clear()

    def clear_subscribed_codes(self):
        """구독 종목 코드 초기화 (재스크리닝 시 호출)."""
        self._subscribed_codes.clear()

    async def subscribe_realtime(self, codes: list[str]):
        """실시간 체결가 구독."""
        if not self._ws_key:
            await self._get_ws_key()

        if not self._ws:
            self._ws = await websockets.connect(self.ws_url, ping_interval=self._infra.ws_ping_interval_sec, ping_timeout=self._infra.ws_timeout_sec)
            self._ws_task = asyncio.create_task(self._ws_receiver())

        for code in codes:
            self._subscribed_codes.add(code)
            msg = {
                "header": {
                    "approval_key": self._ws_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": WS_TR_PRICE,
                        "tr_key": code,
                    }
                },
            }
            await self._ws.send(json.dumps(msg))
            logger.info(f"실시간 구독: {code}")

    async def unsubscribe_realtime(self, codes: list[str] | None = None):
        """실시간 구독 해제."""
        if not self._ws:
            return

        if codes:
            for code in codes:
                self._subscribed_codes.discard(code)
                msg = {
                    "header": {
                        "approval_key": self._ws_key,
                        "custtype": "P",
                        "tr_type": "2",
                        "content-type": "utf-8",
                    },
                    "body": {
                        "input": {
                            "tr_id": WS_TR_PRICE,
                            "tr_key": code,
                        }
                    },
                }
                await self._ws.send(json.dumps(msg))
        else:
            if self._ws_task:
                self._ws_task.cancel()
            await self._ws.close()
            self._ws = None

    async def subscribe_futures(self):
        """KOSPI200 선물 실시간 체결가 구독."""
        if not self._ws_key:
            await self._get_ws_key()

        if not self._ws:
            self._ws = await websockets.connect(self.ws_url, ping_interval=self._infra.ws_ping_interval_sec, ping_timeout=self._infra.ws_timeout_sec)
            self._ws_task = asyncio.create_task(self._ws_receiver())

        msg = {
            "header": {
                "approval_key": self._ws_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": WS_TR_FUTURES,
                    "tr_key": FUTURES_KOSPI200_NEAR,
                }
            },
        }
        await self._ws.send(json.dumps(msg))
        self._futures_subscribed = True
        logger.info(f"선물 실시간 구독: {FUTURES_KOSPI200_NEAR} (TR: {WS_TR_FUTURES})")

    async def _ws_receiver(self):
        """WebSocket 메시지 수신 루프. 연결 끊김 시 지수 백오프로 재접속."""
        backoff = 1.0
        max_backoff = self._infra.ws_max_backoff_sec

        while True:
            try:
                async for raw in self._ws:
                    if not self._ws_connected:
                        self._ws_connected = True
                        logger.info("WebSocket 데이터 수신 확인 — 연결 정상")
                    backoff = 1.0  # 정상 수신 시 백오프 리셋
                    self._ws_last_recv = time.time()
                    try:
                        if raw.startswith("{"):
                            data = json.loads(raw)
                            header = data.get("header", {})
                            if header.get("tr_id") == "PINGPONG":
                                await self._ws.send(raw)
                                continue
                        else:
                            # '0|TR_ID|count|data^data^...' 형식
                            parts = raw.split("|")
                            if len(parts) < 4:
                                continue

                            tr_id = parts[1]
                            body = parts[3]

                            if tr_id == WS_TR_PRICE:
                                fields = body.split("^")
                                if len(fields) >= 12:
                                    price_data = {
                                        "code": fields[0],
                                        "time": fields[1],
                                        "current_price": int(fields[2]),
                                        "change_sign": fields[3],
                                        "change": int(fields[4]),
                                        "change_pct": float(fields[5]),
                                        "volume": int(fields[9]) if len(fields) > 9 else 0,
                                    }
                                    for cb in self._realtime_callbacks.get(WS_TR_PRICE, []):
                                        try:
                                            cb(price_data)
                                        except Exception as e:
                                            logger.error(f"실시간 콜백 에러: {e}")

                            elif tr_id == WS_TR_FUTURES:
                                fields = body.split("^")
                                if len(fields) >= 3:
                                    futures_data = {
                                        "code": fields[0],
                                        "time": fields[1],
                                        "current_price": float(fields[2]),
                                    }
                                    for cb in self._realtime_callbacks.get(WS_TR_FUTURES, []):
                                        try:
                                            cb(futures_data)
                                        except Exception as e:
                                            logger.error(f"선물 콜백 에러: {e}")

                    except Exception as e:
                        logger.error(f"WebSocket 메시지 파싱 에러: {e}")

            except asyncio.CancelledError:
                self._ws_connected = False
                return
            except (websockets.ConnectionClosed, Exception) as e:
                self._ws_connected = False
                logger.warning(f"WebSocket 연결 끊김: {e}, {backoff:.1f}초 후 재접속 시도")
                # 끊김 콜백 호출 (매수 취소 등 긴급 조치)
                if self._on_ws_disconnect:
                    try:
                        self._on_ws_disconnect()
                    except Exception as cb_err:
                        logger.error(f"WS 끊김 콜백 에러: {cb_err}")

            # ── 재접속 루프 ──
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

            try:
                self._ws = await websockets.connect(self.ws_url, ping_interval=self._infra.ws_ping_interval_sec, ping_timeout=self._infra.ws_timeout_sec)
                self._ws_connected = True
                self._ws_last_recv = time.time()
                logger.info("WebSocket 재접속 성공")


                # ws_key 재발급 (기존 키 만료 가능)
                self._ws_key = ""
                await self._get_ws_key()
                logger.info("WebSocket 키 재발급 완료")
                # 기존 구독 코드 재구독
                for code in list(self._subscribed_codes):
                    msg = {
                        "header": {
                            "approval_key": self._ws_key,
                            "custtype": "P",
                            "tr_type": "1",
                            "content-type": "utf-8",
                        },
                        "body": {
                            "input": {
                                "tr_id": WS_TR_PRICE,
                                "tr_key": code,
                            }
                        },
                    }
                    await self._ws.send(json.dumps(msg))
                    logger.info(f"재구독 완료: {code}")

                # 선물 재구독
                if self._futures_subscribed:
                    futures_msg = {
                        "header": {
                            "approval_key": self._ws_key,
                            "custtype": "P",
                            "tr_type": "1",
                            "content-type": "utf-8",
                        },
                        "body": {
                            "input": {
                                "tr_id": WS_TR_FUTURES,
                                "tr_key": FUTURES_KOSPI200_NEAR,
                            }
                        },
                    }
                    await self._ws.send(json.dumps(futures_msg))
                    logger.info(f"선물 재구독 완료: {FUTURES_KOSPI200_NEAR}")

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"WebSocket 재접속 실패: {e}")

    # ── 네트워크 상태 ─────────────────────────────────────────

    @property
    def ws_connected(self) -> bool:
        """WebSocket 연결 상태."""
        return self._ws_connected

    @property
    def ws_last_recv_age(self) -> float:
        """마지막 WebSocket 수신 후 경과 시간(초)."""
        if self._ws_last_recv == 0:
            return float("inf")
        return time.time() - self._ws_last_recv

    def set_ws_disconnect_callback(self, cb: Callable) -> None:
        """WebSocket 끊김 시 호출할 콜백 등록."""
        self._on_ws_disconnect = cb

    # ── 서버 정보 ──────────────────────────────────────────────

    def get_server_type(self) -> str:
        return "모의투자" if self.is_paper else "실서버"
