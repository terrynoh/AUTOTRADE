"""
W-31 검증: KRX_only 종목 UN 채널(H0UNCNT0) WebSocket 미수신 가설 검증.

목적:
- 가설 H4: KRX_only 종목은 UN 채널 구독해도 ws_handler 이벤트 0건
- 어제 4종목 패턴이 다른 KRX_only 종목에서도 재현되는지 확인

방법:
- 운영 시스템과 독립적으로 별도 approval_key 발급 → WebSocket 연결
- 12개 코드 구독 (10 KRX_only 추정 + 2 KRX_NXT 대조군)
- N분간 코드별 raw 메시지 카운트
- jsonl 결과 출력

read-only: 주문/체결 API 호출 0건.
1회성 검증 스크립트 — 영구 통합 아님.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import aiohttp
import websockets
from dotenv import load_dotenv


# === 검증 대상 ===
# 가설 H4-역: KRX_only 종목 (NXT 미상장) 자체가 보편적으로 UN 채널 미수신
# 검증 방법: KRX_only 광범위 표본 + NXT 대조군 → 패턴 비교
TEST_CODES = {
    # === KRX_only — 어제 ws=0 재현 확인 (2종목) ===
    "011930": ("신성이엔지",       "krx_only_yesterday_ws0"),
    "092190": ("서울바이오시스",   "krx_only_yesterday_ws0"),
    # === KRX_only — 무작위 표본 (random.seed=42, 15종목) ===
    "180400": ("DXVX",             "krx_only_sample"),
    "005810": ("풍산홀딩스",       "krx_only_sample"),
    "001070": ("대한방직",         "krx_only_sample"),
    "264900": ("크라운제과",       "krx_only_sample"),
    "032280": ("삼일",             "krx_only_sample"),
    "023790": ("동일스틸럭스",     "krx_only_sample"),
    "018120": ("진로발효",         "krx_only_sample"),
    "008060": ("대덕",             "krx_only_sample"),
    "263690": ("디알젬",           "krx_only_sample"),
    "005257": ("녹십자홀딩스2우",  "krx_only_sample"),
    "214610": ("롤링스톤",         "krx_only_sample"),
    "264450": ("유비쿼스",         "krx_only_sample"),
    "446070": ("유니드비티플러스", "krx_only_sample"),
    "101680": ("한국정밀기계",     "krx_only_sample"),
    "004270": ("남성",             "krx_only_sample"),
    # === NXT 대조군 (3종목) — 정상 수신 기대 ===
    "035420": ("NAVER",            "nxt_control"),
    "278470": ("에이피알",         "nxt_control"),
    "010140": ("삼성중공업",       "nxt_control"),
}

# === KIS endpoints ===
BASE_URL = "https://openapi.koreainvestment.com:9443"
WS_URL = "ws://ops.koreainvestment.com:21000"
EP_APPROVAL = "/oauth2/Approval"
WS_TR_PRICE = "H0UNCNT0"

# === 출력 ===
OUT_DIR = Path(r"C:\Users\terryn\AUTOTRADE\logs\ws_runtime")
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_JSONL = OUT_DIR / f"ws_krx_only_test_{TS}.jsonl"

# 관측 시간 (초)
DURATION_SEC = 300  # 5분 (광범위 KRX_only 검증)


async def fetch_approval_key(session: aiohttp.ClientSession, app_key: str, app_secret: str) -> str:
    """approval_key 발급 — 운영과 독립."""
    url = f"{BASE_URL}{EP_APPROVAL}"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "secretkey": app_secret,
    }
    async with session.post(url, json=body) as resp:
        data = await resp.json()
        if resp.status != 200:
            raise RuntimeError(f"approval 실패: {resp.status} {data}")
        return data["approval_key"]


async def run_test() -> None:
    load_dotenv(r"C:\Users\terryn\AUTOTRADE\.env")
    app_key = os.environ["KIS_APP_KEY"]
    app_secret = os.environ["KIS_APP_SECRET"]

    print(f"=== W-31 어제 ws=0 4종목 직접 재현 검증 ===")
    print(f"개시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST")
    print(f"관측: {DURATION_SEC}초")
    print(f"코드 수: {len(TEST_CODES)}")
    print(f"출력: {OUT_JSONL}")
    print()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jsonl = open(OUT_JSONL, "w", encoding="utf-8")

    async with aiohttp.ClientSession() as session:
        approval_key = await fetch_approval_key(session, app_key, app_secret)
        print(f"[OK] approval_key 발급 완료 (운영과 별개 세션)")
        print()

    counts: dict[str, int] = defaultdict(int)
    first_tick_ts: dict[str, float] = {}
    last_tick_ts: dict[str, float] = {}

    # KIS WebSocket: ping_interval=None (KIS는 PINGPONG TR_ID로 자체 keepalive)
    ws = await websockets.connect(WS_URL, ping_interval=None, ping_timeout=None,
                                   max_size=10 * 1024 * 1024)
    print(f"[OK] WebSocket 연결 완료: {WS_URL}")

    deadline = time.monotonic() + DURATION_SEC
    subscribe_responses: list[dict] = []
    raw_total = 0
    sent_count = 0

    async def receiver():
        nonlocal raw_total
        try:
            while time.monotonic() < deadline:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remain, 5.0))
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    print(f"[!] WebSocket closed (received {raw_total} msgs so far)")
                    return
                raw_total += 1
                now_ts = time.time()

                # 디버그: 모든 raw head 기록
                jsonl.write(json.dumps({
                    "ts": now_ts,
                    "raw_head": raw[:120] if isinstance(raw, str) else str(raw)[:120],
                    "_debug": True,
                }, ensure_ascii=False) + "\n")
                if raw.startswith("{"):
                    try:
                        data = json.loads(raw)
                        header = data.get("header", {})
                        tr_id_hdr = header.get("tr_id", "")
                        if tr_id_hdr == "PINGPONG":
                            await ws.send(raw)
                            continue
                        body_resp = data.get("body", {}) or {}
                        tr_key = header.get("tr_key", "")
                        rt_cd = body_resp.get("rt_cd", "")
                        msg1 = body_resp.get("msg1", "")
                        subscribe_responses.append({
                            "tr_id": tr_id_hdr, "tr_key": tr_key,
                            "rt_cd": rt_cd, "msg1": msg1,
                        })
                        print(f"  [구독응답] tr_key={tr_key} rt_cd={rt_cd} msg={msg1}")
                    except Exception as e:
                        print(f"  [JSON 파싱 실패] {e}: {raw[:100]}")
                    continue

                parts = raw.split("|", 3)
                if len(parts) < 4:
                    continue
                tr_id = parts[1]
                body = parts[3]
                if tr_id != WS_TR_PRICE:
                    continue
                try:
                    code_received = body.split("^", 1)[0]
                except Exception:
                    continue

                counts[code_received] += 1
                if code_received not in first_tick_ts:
                    first_tick_ts[code_received] = now_ts
                last_tick_ts[code_received] = now_ts

                jsonl.write(json.dumps({
                    "ts": now_ts,
                    "code": code_received,
                    "tr_id": tr_id,
                    "raw_head": body[:80],
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[!] receiver 예외: {type(e).__name__}: {e}")

    recv_task = asyncio.create_task(receiver())
    # receiver 먼저 띄운 후 구독 발송 (운영 패턴)
    await asyncio.sleep(0.2)

    try:
        for code in TEST_CODES.keys():
            msg = {
                "header": {
                    "approval_key": approval_key,
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
            await ws.send(json.dumps(msg))
            sent_count += 1
            await asyncio.sleep(0.1)
        print(f"[OK] {sent_count}개 구독 발송 완료")
        print(f"--- 관측 시작 ({DURATION_SEC}초) ---\n")
    except Exception as e:
        print(f"[!] 구독 발송 실패: {type(e).__name__}: {e}")

    try:
        await recv_task
    finally:
        try:
            await ws.close()
        except Exception:
            pass
        jsonl.close()

    # === 결과 출력 ===
    print(f"\n--- 관측 종료 ---")
    print(f"raw 메시지 총: {raw_total}건")
    print()

    print(f"=== 코드별 tick 카운트 ===")
    print(f"{'코드':<8} {'종목명':<20} {'분류':<20} {'tick수':>8} {'first_ts':<10} {'last_ts':<10}")

    # 그룹별 집계
    groups = defaultdict(lambda: {"now": [], "zero": []})
    for code, (name, group) in TEST_CODES.items():
        cnt = counts[code]
        ft = datetime.fromtimestamp(first_tick_ts[code]).strftime("%H:%M:%S") if code in first_tick_ts else "-"
        lt = datetime.fromtimestamp(last_tick_ts[code]).strftime("%H:%M:%S") if code in last_tick_ts else "-"
        print(f"{code:<8} {name:<18} {group:<26} {cnt:>8} {ft:<10} {lt:<10}")
        groups[group]["now" if cnt > 0 else "zero"].append(code)

    print()
    print(f"=== 그룹별 집계 ===")
    for g, dat in sorted(groups.items()):
        total = len(dat["now"]) + len(dat["zero"])
        rate = len(dat["zero"]) / total * 100 if total else 0
        print(f"  {g:<28} 총 {total}, 0건 {len(dat['zero'])} ({rate:.1f}%), 정상 {len(dat['now'])}")

    print()
    print(f"=== H4-역 판정 (KRX_only 광범위 문제 가설) ===")
    krx_only_groups = ["krx_only_yesterday_ws0", "krx_only_sample"]
    nxt_zero = len(groups["nxt_control"]["zero"])
    nxt_total = len(groups["nxt_control"]["now"]) + nxt_zero
    krx_zero = sum(len(groups[g]["zero"]) for g in krx_only_groups)
    krx_total = sum(len(groups[g]["now"]) + len(groups[g]["zero"]) for g in krx_only_groups)
    krx_zero_rate = krx_zero / krx_total * 100 if krx_total else 0

    print(f"  KRX_only 0건 비율: {krx_zero}/{krx_total} ({krx_zero_rate:.1f}%)")
    print(f"  NXT 대조군 0건: {nxt_zero}/{nxt_total}")
    print()

    if nxt_zero == nxt_total and nxt_total > 0:
        print("  [판정] 대조군까지 0건 → 환경 문제 (KIS/네트워크/시간대), KRX_only 가설 검증 불가")
    elif nxt_zero == 0 and krx_zero_rate >= 80:
        print(f"  [판정] H4-역 강력 지지 — KRX_only {krx_zero_rate:.0f}% 0건, 대조군 정상")
        print(f"         → KRX_only 종목은 H0UNCNT0 채널 미수신이 보편적 (구조적 문제)")
    elif nxt_zero == 0 and krx_zero_rate >= 30:
        print(f"  [판정] H4-역 부분 지지 — KRX_only {krx_zero_rate:.0f}% 0건")
        print(f"         → 일부 KRX_only 종목이 미수신 (선택적 문제)")
    elif nxt_zero == 0 and krx_zero_rate < 30:
        print(f"  [판정] H4-역 기각 — KRX_only {krx_zero_rate:.0f}% 0건만")
        print(f"         → KRX_only 자체는 문제 아님, 다른 원인 추적 필요")
    else:
        print(f"  [판정] 혼재 — 대조군 {nxt_zero}/{nxt_total} 0건, KRX_only {krx_zero_rate:.0f}% 0건")

    print()
    print(f"jsonl 저장: {OUT_JSONL}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run_test())
