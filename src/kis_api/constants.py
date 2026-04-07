"""
한국투자증권 KIS Open API 상수 정의.

엔드포인트, TR ID, 시장코드, 주문유형 등.
출처: https://github.com/koreainvestment/open-trading-api
"""

# ── 베이스 URL ──────────────────────────────────────────────────
BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"

# ── WebSocket URL ───────────────────────────────────────────────
WS_URL_REAL = "ws://ops.koreainvestment.com:21000"
WS_URL_PAPER = "ws://ops.koreainvestment.com:31000"

# ── 인증 ────────────────────────────────────────────────────────
EP_TOKEN = "/oauth2/tokenP"
EP_REVOKE_TOKEN = "/oauth2/revokeP"
EP_WEBSOCKET_KEY = "/oauth2/Approval"

# ── 시세 조회 엔드포인트 ────────────────────────────────────────
EP_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
EP_DAILY_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-daily-price"
EP_MINUTE_CHART = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
EP_VOLUME_RANK = "/uapi/domestic-stock/v1/quotations/volume-rank"

# 프로그램매매
EP_PROGRAM_TRADE_TODAY = "/uapi/domestic-stock/v1/quotations/comp-program-trade-today"       # 시장 전체 집계
EP_PROGRAM_TRADE_BY_STOCK = "/uapi/domestic-stock/v1/quotations/program-trade-by-stock"      # 종목별 프로그램매매
EP_PROGRAM_TRADE_DAILY = "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"

# ── 주문 엔드포인트 ────────────────────────────────────────────
EP_ORDER = "/uapi/domestic-stock/v1/trading/order-cash"
EP_ORDER_CANCEL = "/uapi/domestic-stock/v1/trading/order-rvsecncl"

# ── 잔고/계좌 엔드포인트 ──────────────────────────────────────
EP_BALANCE = "/uapi/domestic-stock/v1/trading/inquire-balance"
EP_UNFILLED = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"

# ── TR ID (실거래) ─────────────────────────────────────────────
TR_PRICE = "FHKST01010100"              # 주식현재가 시세
TR_DAILY_PRICE = "FHKST01010400"        # 주식현재가 일자별
TR_MINUTE_CHART = "FHKST03010200"       # 주식현재가 분봉조회
TR_VOLUME_RANK = "FHPST01710000"        # 거래량순위

TR_PROGRAM_TRADE_TODAY = "FHPPG04600101"      # 프로그램매매 당일 시장 집계
TR_PROGRAM_TRADE_BY_STOCK = "FHPPG04650101"   # 프로그램매매 종목별
TR_PROGRAM_TRADE_DAILY = "FHPPG04600001"      # 프로그램매매 일별

TR_ORDER_BUY = "TTTC0012U"              # 현금매수 (실거래)
TR_ORDER_SELL = "TTTC0011U"             # 현금매도 (실거래)
TR_ORDER_CANCEL = "TTTC0803U"           # 주문취소

TR_BALANCE = "TTTC8434R"                # 주식잔고조회

# ── TR ID (모의투자) ───────────────────────────────────────────
TR_ORDER_BUY_PAPER = "VTTC0012U"
TR_ORDER_SELL_PAPER = "VTTC0011U"
TR_ORDER_CANCEL_PAPER = "VTTC0803U"
TR_BALANCE_PAPER = "VTTC8434R"

# ── WebSocket TR ID ────────────────────────────────────────────
WS_TR_PRICE = "H0STCNT0"                # 실시간 체결가 (KRX)
WS_TR_ORDERBOOK = "H0STASP0"            # 실시간 호가 (KRX)
WS_TR_PROGRAM = "H0STPGM0"             # 실시간 프로그램매매 (KRX)
WS_TR_FUTURES = "H0IFCNT0"              # 실시간 선물 체결 (KOSPI200)

# ── 선물 종목코드 ──────────────────────────────────────────────
FUTURES_KOSPI200_NEAR = "101S3000"       # KOSPI200 선물 근월물 (코드는 분기마다 변경)

# ── 시장 코드 ──────────────────────────────────────────────────
MARKET_CODE_KOSPI = "J"
MARKET_CODE_KOSDAQ = "Q"

# ── 주문 유형 ──────────────────────────────────────────────────
ORDER_TYPE_LIMIT = "00"                  # 지정가
ORDER_TYPE_MARKET = "01"                 # 시장가
ORDER_TYPE_CONDITIONAL = "02"            # 조건부지정가
ORDER_TYPE_BEST_LIMIT = "03"             # 최유리지정가

# ── Rate Limit (초당 최대 요청 수) ─────────────────────────────
RATE_LIMIT_REAL = 20    # 실거래: 초당 20건 (0.05초 간격)
RATE_LIMIT_PAPER = 2    # 모의투자: 초당 2건 (0.5초 간격)

# ── 에러 코드 ──────────────────────────────────────────────────
ERROR_CODES = {
    "EGW00000": "정상",
    "EGW00121": "장 운영시간 외 요청",
    "EGW00123": "호가 접수 불가 시간",
    "IGW00006": "토큰 만료",
    "IGW00007": "인가되지 않은 IP",
}
