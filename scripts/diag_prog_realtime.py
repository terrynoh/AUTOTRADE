"""M-DIAG-PROG v2 장중 실측 진단 스크립트."""
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
from src.utils.market_calendar import now_kst  # noqa: E402


TARGETS: list[tuple[str, str, str]] = [
    ("036930", "주성엔지니어링", "anomaly-KOSDAQ"),
    ("032820", "우리기술",        "anomaly-KOSDAQ"),
    ("450080", "에코프로머티",    "anomaly-KOSPI"),
    ("108490", "로보티즈",        "normal-compare-KOSDAQ"),
    ("489790", "한화비전",        "normal-baseline-KOSPI"),
]

ROUNDS = 10
INTERVAL_SEC = 30


async def diag_round(api: KISAPI, round_num: int, raw_records: list[dict[str, Any]]) -> None:
    ts_obj = now_kst()
    ts = ts_obj.strftime("%H:%M:%S")
    logger.info(f"=== Round {round_num}/{ROUNDS} @ {ts} KST ===")

    for code, name, label in TARGETS:
        record: dict[str, Any] = {
            "round": round_num,
            "ts_kst": ts_obj.isoformat(),
            "code": code,
            "name": name,
            "label": label,
        }

        try:
            price = await api.get_current_price(code)
            record["price"] = price
        except Exception as e:
            logger.error(f"[PRICE-ERR] {code}({name}): {type(e).__name__}: {e}")
            record["price_error"] = f"{type(e).__name__}: {e}"
            price = None

        try:
            prog = await api.get_program_trade(code)
            record["prog"] = prog
        except Exception as e:
            logger.error(f"[PROG-ERR] {code}({name}): {type(e).__name__}: {e}")
            record["prog_error"] = f"{type(e).__name__}: {e}"
            prog = None

        if price and prog:
            tv_krw = price["trading_value"]
            tv_eok = tv_krw / 1e8
            ratio = (prog["program_net_buy"] / tv_krw * 100) if tv_krw > 0 else 0.0
            is_double = ratio >= 10.0

            record["ratio_pct"] = round(ratio, 4)
            record["is_double"] = is_double

            tag = ""
            if label.startswith("anomaly") and ratio > 0:
                tag = " <ANOMALY-RECOVERED>"
            elif label.startswith("normal") and ratio == 0 and tv_krw > 0:
                tag = " <NORMAL-NOW-ZERO>"

            logger.info(
                f"[DIAG-{label}] {code}({name}) "
                f"거래대금={tv_eok:>6.0f}억 "
                f"net={prog['program_net_buy']:>15,} "
                f"buy={prog['buy_amount']:>15,} "
                f"sell={prog['sell_amount']:>15,} "
                f"비중={ratio:>6.2f}%{' D' if is_double else ' S'}{tag}"
            )

        raw_records.append(record)

    logger.info("")


async def main() -> None:
    stamp = now_kst().strftime("%Y-%m-%d_%H%M%S")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"diag_prog_{stamp}.log"
    json_path = log_dir / f"diag_prog_{stamp}.json"

    logger.add(str(log_path), level="DEBUG", rotation=None, encoding="utf-8")
    logger.info(f"M-DIAG-PROG v2 실측 시작 (ROUNDS={ROUNDS}, INTERVAL={INTERVAL_SEC}s)")
    logger.info(f"로그: {log_path}")
    logger.info(f"JSON: {json_path}")
    logger.info(f"타겟 {len(TARGETS)}종목: {[t[1] for t in TARGETS]}")

    settings = Settings()
    if not settings.kis_app_key or not settings.kis_app_secret or not settings.kis_account_no:
        logger.error(".env 에 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 필요")
        sys.exit(1)

    api = KISAPI(
        app_key=settings.kis_app_key,
        app_secret=settings.kis_app_secret,
        account_no=settings.kis_account_no,
    )
    await api.connect()

    raw_records: list[dict[str, Any]] = []
    try:
        for i in range(1, ROUNDS + 1):
            await diag_round(api, i, raw_records)
            if i < ROUNDS:
                await asyncio.sleep(INTERVAL_SEC)
    except KeyboardInterrupt:
        logger.warning("Ctrl+C 인터럽트 — 현재까지 기록 저장 후 종료")
    finally:
        await api.disconnect()

        try:
            json_path.write_text(
                json.dumps(
                    {
                        "started_at": stamp,
                        "targets": [
                            {"code": c, "name": n, "label": l}
                            for (c, n, l) in TARGETS
                        ],
                        "rounds": ROUNDS,
                        "interval_sec": INTERVAL_SEC,
                        "records": raw_records,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            logger.info(f"JSON 저장 완료: {json_path} ({len(raw_records)} 레코드)")
        except Exception as e:
            logger.error(f"JSON 저장 실패: {e}")

        logger.info("M-DIAG-PROG v2 실측 완료")


if __name__ == "__main__":
    asyncio.run(main())
