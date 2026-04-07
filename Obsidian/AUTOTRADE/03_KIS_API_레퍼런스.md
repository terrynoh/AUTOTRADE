# KIS Open API 레퍼런스
> 파일: `C:\Users\terryn\AUTOTRADE\src\kis_api\constants.py`
> 최종 업데이트: 2026-04-05

---

## 베이스 URL

| 환경 | URL |
|------|-----|
| 실거래 | `https://openapi.koreainvestment.com:9443` |
| 모의투자 | `https://openapivts.koreainvestment.com:29443` |

## WebSocket URL

| 환경 | URL |
|------|-----|
| 실거래 | `ws://ops.koreainvestment.com:21000` |
| 모의투자 | `ws://ops.koreainvestment.com:31000` |

---

## 엔드포인트 & TR ID

### 시세 조회

| 용도 | 엔드포인트 | TR ID |
|------|-----------|-------|
| 주식현재가 | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` |
| 주식현재가 일자별 | `/uapi/domestic-stock/v1/quotations/inquire-daily-price` | `FHKST01010400` |
| 분봉차트 | `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` | `FHKST03010200` |
| 거래량순위 | `/uapi/domestic-stock/v1/quotations/volume-rank` | `FHPST01710000` |
| 프로그램매매 당일 | `/uapi/domestic-stock/v1/quotations/comp-program-trade-today` | `FHPPG04600101` |
| 프로그램매매 일별 | `/uapi/domestic-stock/v1/quotations/comp-program-trade-daily` | `FHPPG04600001` |

### 주문

| 용도 | 엔드포인트 | TR ID (실거래) | TR ID (모의) |
|------|-----------|-------------|------------|
| 현금매수 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0012U` | `VTTC0012U` |
| 현금매도 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0011U` | `VTTC0011U` |
| 주문취소 | `/uapi/domestic-stock/v1/trading/order-rvsecncl` | `TTTC0803U` | `VTTC0803U` |
| 잔고조회 | `/uapi/domestic-stock/v1/trading/inquire-balance` | `TTTC8434R` | `VTTC8434R` |
| 미체결조회 | `/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl` | — | — |

### 인증

| 용도 | 엔드포인트 |
|------|-----------|
| 토큰 발급 | `/oauth2/tokenP` |
| 토큰 취소 | `/oauth2/revokeP` |
| WebSocket Key | `/oauth2/Approval` |

---

## WebSocket TR ID

| TR ID | 용도 |
|-------|------|
| `H0STCNT0` | 실시간 체결가 (KRX) |
| `H0STASP0` | 실시간 호가 (KRX) |
| `H0STPGM0` | 실시간 프로그램매매 |
| `H0IFCNT0` | 실시간 선물 체결 (KOSPI200) |

---

## Rate Limit

| 환경 | 초당 최대 요청 | 간격 |
|------|-------------|------|
| 실거래 | 20건 | 0.05초 |
| 모의투자 | 2건 | 0.5초 |

→ `asyncio.Semaphore`로 제어 (`src/kis_api/kis.py`)

---

## 인증 패턴

```python
await self.api.connect()       # 토큰 자동 발급/갱신 (24시간 유효)
# 만료 1시간 전 자동 갱신
```

---

## 선물 종목코드

```
KOSPI200 선물 근월물: 101S3000
# 분기마다 코드 변경됨 → constants.py 업데이트 필요
```

---

## 주문유형 코드

| 코드 | 유형 |
|------|------|
| `00` | 지정가 |
| `01` | 시장가 |
| `02` | 조건부지정가 |
| `03` | 최유리지정가 |

---

## 에러코드

| 코드 | 설명 |
|------|------|
| `EGW00000` | 정상 |
| `EGW00121` | 장 운영시간 외 요청 |
| `EGW00123` | 호가 접수 불가 시간 |
| `IGW00006` | 토큰 만료 |
| `IGW00007` | 인가되지 않은 IP |

---

## 코딩 패턴

```python
# REST 조회
info = await self.api.get_current_price("005930")
rank = await self.api.get_volume_rank(market="J")

# WebSocket 실시간
await self.api.subscribe_realtime(codes=["005930"])
self.api.add_realtime_callback("H0STCNT0", self._on_price)

# 주문
order = await self.api.buy_order(
    code="005930", qty=100, price=70000,
    price_type="00",   # 지정가
)
```
