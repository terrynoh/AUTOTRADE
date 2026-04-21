"""M-DIAG-PROG v2 확장 — FID_COND_MRKT_DIV_CODE 3종 비교.

가설 E (시장분류 코드 효과) 검증:
- "J"  : KRX 만
- "NX" : NXT 만
- "UN" : 통합 (현재 AUTOTRADE 사용값)

목적: 이상 3종목이 어느 분류에서 빈 배열이고 어느 분류에서 데이터 오는지 확인.

실행:
  cd C:/Users/terryn/AUTOTRADE
  python -m scripts.diag_prog_market_codes

산출:
  logs/diag_mcodes_<timestamp>.log
  logs/diag_mcodes_<timestamp>.json
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Settings  # noqa: E402
from src.kis_api.kis import KISAPI     # noqa: E402
from src.kis_api.constants import (   # noqa: E402
    EP_PROGRAM_TRADE_BY_STOCK,
    TR_PROGRAM_TRADE_BY_STOCK,
)
from src.utils.market_calendar import now_kst  # noqa: E402


TARGETS: list[tuple[str, str, str]] = [
    # Day 2 "UN" 빈 배열 종목
    ("036930", "주성엔지니어링", "anomaly-KOSDAQ"),
    ("032820", "우리기술",        "anomaly-KOSDAQ"),
    ("450080", "에코프로머티",    "anomaly-KOSPI"),
    # Day 2 "UN" 정상 종목
    ("108490", "로보티즈",        "normal-KOSDAQ"),
    ("489790", "한화비전",        "normal-KOSPI"),
]

MARKET_CODES = [
    ("J",  "KRX"),
    ("NX", "NXT"),
    ("UN", "통합"),
]


async def query_raw(api: KISAPI, code: str, mkt_code: str) -> dict[str, Any]:
    """하위 레벨 REST 호출 — get_program_trade 를 거치지 않고 raw 응답 확보."""
    params = {
        "FID_COND_MRKT_DIV_CODE": mkt_code,
        "FID_INPUT_ISCD": code,
    }
    try:
        data = await api._get(
            EP_PROGRAM_TRADE_BY_STOCK,
            TR_PROGRAM_TRADE_BY_STOCK,
            params,
        )
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def main() -> None:
    stamp = now_kst().strftime("%Y-%m-%d_%H%M%S")
    log_dir = ROOT / "logs" / "diag"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"diag_mcodes_{stamp}.log"
    json_path = log_dir / f"diag_mcodes_{stamp}.json"

    logger.add(str(log_path), level="DEBUG", rotation=None, encoding="utf-8")
    logger.info(f"[diag_mcodes] 시장분류 코드 비교 실측 시작")
    logger.info(f"타겟 {len(TARGETS)}종목 × 시장코드 {len(MARKET_CODES)}개 = {len(TARGETS)*len(MARKET_CODES)}회 호출")

    settings = Settings()
    api = KISAPI(
        app_key=settings.kis_app_key,
        app_secret=settings.kis_app_secret,
        account_no=settings.kis_account_no,
    )
    await api.connect()

    results: list[dict[str, Any]] = []
    try:
        for code, name, label in TARGETS:
            logger.info(f"--- {code} {name} ({label}) ---")
            for mkt_code, mkt_name in MARKET_CODES:
                res = await query_raw(api, code, mkt_code)

                record: dict[str, Any] = {
                    "code": code,
                    "name": name,
                    "label": label,
                    "mkt_code": mkt_code,
                    "mkt_name": mkt_name,
                    "ok": res["ok"],
                }

                if res["ok"]:
                    data = res["data"]
                    output = data.get("output", [])
                    rt_cd = data.get("rt_cd")
                    msg1 = data.get("msg1", "")
                    msg_cd = data.get("msg_cd", "")

                    record["rt_cd"] = rt_cd
                    record["msg_cd"] = msg_cd
                    record["msg1"] = msg1
                    record["output_len"] = len(output) if isinstance(output, list) else 0

                    if isinstance(output, list) and output:
                        first = output[0]
                        record["output_0"] = first
                        bsop = first.get("bsop_hour", "")
                        net = first.get("whol_smtn_ntby_tr_pbmn", "0")
                        logger.info(
                            f"  [{mkt_code}:{mkt_name}] rt_cd={rt_cd} len={len(output)} "
                            f"bsop={bsop} net={net}"
                        )
                    else:
                        record["output_0"] = None
                        logger.warning(
                            f"  [{mkt_code}:{mkt_name}] rt_cd={rt_cd} msg={msg1!r} "
                            f"output=EMPTY"
                        )
                else:
                    record["error"] = res["error"]
                    logger.error(f"  [{mkt_code}:{mkt_name}] ERROR {res['error']}")

                results.append(record)

                # rate limit 여유
                await asyncio.sleep(0.1)
    finally:
        await api.disconnect()

        json_path.write_text(
            json.dumps(
                {
                    "started_at": stamp,
                    "targets": [{"code": c, "name": n, "label": l} for (c, n, l) in TARGETS],
                    "market_codes": [{"code": m, "name": n} for (m, n) in MARKET_CODES],
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        logger.info(f"JSON 저장: {json_path}")

        # 요약 매트릭스
        logger.info("")
        logger.info("=" * 70)
        logger.info("매트릭스: 종목 × 시장코드 → output_len")
        logger.info("=" * 70)
        header = f"{'종목':<20} | " + " | ".join(f"{m:>4}:{n:<3}" for m, n in MARKET_CODES)
        logger.info(header)
        logger.info("-" * 70)
        for code, name, label in TARGETS:
            row = f"{code} {name:<14} | "
            cells = []
            for m, _ in MARKET_CODES:
                rec = next(
                    (r for r in results if r["code"] == code and r["mkt_code"] == m),
                    None,
                )
                if rec is None:
                    cells.append("  ??")
                elif not rec.get("ok"):
                    cells.append(" ERR")
                else:
                    ln = rec.get("output_len", 0)
                    cells.append(f"len={ln:>2}")
            row += " | ".join(f"{c:>8}" for c in cells)
            logger.info(row)
        logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
