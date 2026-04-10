> 🔵 **정적 참조 영역** — KIS API 외부 사양. 변경 시에만 갱신. 마지막 정합: 2026-04-10 (W-16).

# 03 KIS API 레퍼런스

> 출처: 한국투자증권 개발자 포털 + 공식 GitHub open-trading-api
> 분석 기준: AUTOTRADE R-08 구현 시점 (2026-04-10)

---

## 1. WebSocket 다중 종목 동시 구독

### 1.1 핵심 결론

| 질문 | 결론 |
|---|---|
| 단일 JSON 메시지 1회로 다중 종목 구독 | **불가** — `tr_key` 는 단일 종목코드(str) 구조. 근거 없음 |
| 단일 클라이언트 호출로 다중 종목 구독 | **가능** — `data=[종목1, 종목2, ...]` 형태. 내부적으로 N회 전송 |
| 동일 연결에서 3종목 동시 구독 | **가능** — 구독 메시지를 종목 수만큼 순차 전송 |

**결론**: AUTOTRADE 의 N개 Watcher 는 동일 WebSocket 연결에서 종목별 구독 메시지를 반복 전송하는 방식으로 동작. 공식 샘플 설계와 정합.

---

### 1.2 WebSocket 메시지 포맷

```python
# data_fetch(tr_id, tr_type, params) → 메시지 생성
{
    "header": {
        "approval_key": "...",
        "custtype": "P",
        "tr_type": "1",   # 1=구독, 2=해제
        "content-type": "utf-8"
    },
    "body": {
        "input": {
            "tr_id": "H0STASP0",   # TR ID (TR별 다름)
            "tr_key": "005930"     # 종목코드 (단일 문자열)
        }
    }
}
```

**핵심**: `tr_key` 는 단일 종목코드 문자열. 배열/콤마 구분 문자열 방식은 공식 근거 없음.

---

### 1.3 다중 종목 구독 내부 동작

```python
# KISWebSocket.subscribe(request, data=["005930", "000660", "035420"])
# → 내부적으로:
for d in data:
    await self.send(tr_id, tr_type, d)   # 종목별 개별 전송
    await smart_sleep()                  # 전송 간 슬립
```

---

### 1.4 구독 한도 및 전송 빈도 제한

| 항목 | 값 | 출처 |
|---|---|---|
| 구독 상한 | 40 (`open_map` 키 기준) | 공식 샘플 `KISWebSocket.__runner()` |
| 전송 슬립 (실전) | 0.05초 | `_smartSleep` |
| 전송 슬립 (모의) | 0.5초 | `_smartSleep` |
| 접속키 만료 | 86400초 (24시간) | `reAuth_ws()` |

> **주의**: "구독 max 40" 은 `open_map` 의 키(요청 함수명) 기준으로 세는 클라이언트 측 사전 차단. 서버 실제 제한과 1:1 동일하지 않을 수 있음. 포털 "API 호출 유량 안내(웹소켓)" 공지 교차 확인 필요.

---

### 1.5 권장 구독 흐름

```
1) REST POST /oauth2/Approval → approval_key 발급
2) WebSocket 연결
3) 종목별 구독 메시지 전송 (슬립 포함)
4) 실시간 수신 + PINGPONG 처리 (수신 즉시 pong)
5) 필요 시 구독 해제 (tr_type="2")
6) 재연결 시 등록된 구독 목록 재전송
7) 24시간 경과 전 approval_key 재발급
```

---

### 1.6 응답 포맷 및 에러 처리

```python
# system_resp() 판별 기준
body.rt_cd == "0"          → 정상 (isOk)
body.msg1.startswith("UNSUB")  → 구독해제 응답 (isUnSub)
tr_id == "PINGPONG"        → ping/pong (즉시 ws.pong(raw) 응답 필요)

# 암호화 사용 시
header.encrypt             → 암호화 여부
body.output.iv             → IV
body.output.key            → 복호화 키
```

**운영 권장**: `rt_cd != "0"` 시 `msg1` 로깅 + 재시도/백오프/구독 축소 처리 구현.

---

### 1.7 시퀀스 다이어그램

```
Client App          KIS REST (Approval)     KIS WebSocket Server
    │                       │                       │
    ├──POST /oauth2/Approval──▶                      │
    │◀─────approval_key──────┤                      │
    │                                               │
    ├──────────────WebSocket Connect────────────────▶│
    │                                               │
    ├──Subscribe(tr_key="005930")───────────────────▶│
    ├──Subscribe(tr_key="000660")───────────────────▶│
    ├──Subscribe(tr_key="035420")───────────────────▶│
    │                                               │
    │◀──Stream Data for 005930───────────────────────┤
    │◀──Stream Data for 000660───────────────────────┤
    │◀──Stream Data for 035420───────────────────────┤
    │◀──PINGPONG frame────────────────────────────────┤
    ├──PONG──────────────────────────────────────────▶│
```

---

### 1.8 코드 예시 (공식 샘플 스타일)

```python
import kis_auth as ka
from domestic_stock_functions_ws import asking_price_krx

# 1) 인증 (실전)
ka.auth(svr="prod", product="01")
ka.auth_ws(svr="prod", product="01")

# 2) WebSocket 객체 생성
kws = ka.KISWebSocket(api_url="/tryitout")

# 3) 3종목 동시 구독 (내부적으로 3회 메시지 전송)
codes = ["005930", "000660", "035420"]
kws.subscribe(request=asking_price_krx, data=codes)

# 4) 수신 콜백
def on_result(ws, tr_id, df, meta):
    print(tr_id, df.tail(1))

# 5) 시작
kws.start(on_result=on_result)
```

---

## 2. 주요 TR ID

| TR ID | 설명 | 비고 |
|---|---|---|
| `H0STCNT0` | 국내주식 실시간체결가 | AUTOTRADE 사용 |
| `H0STASP0` | 국내주식 실시간호가 | |
| `H0IFCNT0` | KOSPI200 선물 실시간체결가 | AUTOTRADE 사용 |

---

## 3. 공식 소스 링크

| 소스 | URL |
|---|---|
| KIS Developers 포털 | https://apiportal.koreainvestment.com |
| 공식 GitHub | https://github.com/koreainvestment/open-trading-api |
| README (다중 구독 예시) | https://github.com/koreainvestment/open-trading-api/blob/main/README.md |
| kis_auth.py | https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/kis_auth.py |
| domestic_stock_functions_ws.py | https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py |

---

## 4. AUTOTRADE 구현과의 정합

| W-16 기준 사항 | AUTOTRADE 구현 (R-08) |
|---|---|
| WebSocket 다중 구독 | `kis_api/kis.py` — N Watcher 종목별 subscribe 반복 |
| PINGPONG 처리 | `kis_api/kis.py` — on_message 에서 `PINGPONG` 분기 |
| 접속키 갱신 | `kis_api/kis.py` — `reAuth_ws` 패턴 포함 |
| 구독 상한 대비 | AUTOTRADE 최대 종목 수 제한 없음 (수동 입력) → 운영 시 40 미만 유지 권장 |
