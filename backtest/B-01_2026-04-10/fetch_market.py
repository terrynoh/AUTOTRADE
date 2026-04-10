"""B-01 시장 구분 수집 (KOSPI/KOSDAQ)

KISAPI.get_current_price() 공개 메서드만 사용 → market_name 필드 추출.
결정 1 (_get 직접 호출) 영역 확장 없음.

실행:
  cd C:\\Users\\terryn\\AUTOTRADE
  python backtest/B-01_2026-04-10/fetch_market.py

출력:
  backtest/B-01_2026-04-10/data/market_map.json
  {code: "KOSPI" | "KOSDAQ" | "UNKNOWN:<raw>"}
"""

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.kis_api.kis import KISAPI
from config.settings import Settings

CODES = [
    "009150", "000660", "278470", "034220", "010170", "005290", "010950", "316140",
    "012450", "066970", "062040", "105560", "240810", "010140", "138930", "082920",
    "016360", "298040", "024110", "017800", "078930", "034730", "095610", "213420",
    "047050", "055550", "067310", "079550", "035420", "007600", "183300", "241560",
    "032640", "363440", "218410", "319660", "011930", "077360", "285130", "008770",
    "050890", "020150", "036570", "178320", "139130", "077800", "086450", "281820",
    "420770", "329180", "014680", "003490", "005880", "228760", "011790", "267260",
    "011070", "071050", "012330", "039030", "086790", "357780",
]

OUT = Path(__file__).parent / "data" / "market_map.json"
OUT.parent.mkdir(parents=True, exist_ok=True)


def normalize_market(market_name: str) -> str:
    """KIS rprs_mrkt_kor_name → 'KOSPI' / 'KOSDAQ' / 'UNKNOWN:<raw>'

    KIS 의 rprs_mrkt_kor_name 은 지수명으로 반환되는 경우 있음:
    - KOSPI200, KOSPI100, KOSPI50 → KOSPI
    - KSQ150 (KOSDAQ150), KSQ → KOSDAQ
    """
    if not market_name:
        return "UNKNOWN:"
    n = market_name.upper()
    if "KOSPI" in n or "유가" in market_name:
        return "KOSPI"
    if "KOSDAQ" in n or "코스닥" in market_name or n.startswith("KSQ"):
        return "KOSDAQ"
    return f"UNKNOWN:{market_name}"


async def main() -> None:
    print("=" * 60)
    print(f"B-01 시장 구분 수집 ({len(CODES)} 종목)")
    print("=" * 60)

    # 기존 결과 로드 (idempotent)
    existing: dict[str, str] = {}
    if OUT.exists():
        existing = json.loads(OUT.read_text(encoding="utf-8"))
        print(f"기존 매핑 로드: {len(existing)}")

    s = Settings()
    if not s.kis_app_key:
        print("⛔ KIS_APP_KEY 미설정. .env 확인 필요.")
        sys.exit(1)

    api = KISAPI(
        app_key=s.kis_app_key,
        app_secret=s.kis_app_secret,
        account_no=s.kis_account_no,
        is_paper=False,
    )

    await api.connect()
    print(f"토큰 확보 OK ({api.get_server_type()})\n")

    market_map: dict[str, str] = dict(existing)
    failed: list[tuple] = []

    try:
        for i, code in enumerate(CODES, 1):
            # 이미 확정 값이 있으면 스킵 (UNKNOWN 계열은 재조회)
            if market_map.get(code) in ("KOSPI", "KOSDAQ"):
                print(f"[{i:2d}/{len(CODES)}] {code} skip ({market_map[code]})")
                continue

            print(f"[{i:2d}/{len(CODES)}] {code} ...", end=" ", flush=True)
            try:
                result = await api.get_current_price(code)
                raw = result.get("market_name", "")
                normalized = normalize_market(raw)
                market_map[code] = normalized
                print(f"✅ '{raw}' → {normalized}")

                if normalized.startswith("UNKNOWN"):
                    failed.append((code, raw))

                # rate limit 에러 방어: 멈춤 조건 4
                if not raw and not result.get("name"):
                    print(f"\n⛔ 응답 빈 값 ({code}). market_name 필드 부재 가능성. 멈춤.")
                    break

            except RuntimeError as e:
                msg = str(e)
                if "EGW00201" in msg or "초당 거래건수" in msg:
                    print(f"\n⛔ RATE LIMIT ({code}): {msg}")
                    break
                print(f"❌ {e}")
                failed.append((code, str(e)))
            except Exception as e:
                print(f"❌ {e}")
                failed.append((code, str(e)))

            # rate limit 안전 마진
            await asyncio.sleep(1.0)

    finally:
        await api.disconnect()
        print("\nKIS API 연결 해제")

    # 저장
    OUT.write_text(json.dumps(market_map, ensure_ascii=False, indent=2), encoding="utf-8")

    # 결과 집계
    kospi  = sum(1 for v in market_map.values() if v == "KOSPI")
    kosdaq = sum(1 for v in market_map.values() if v == "KOSDAQ")
    unknown = sum(1 for v in market_map.values() if v.startswith("UNKNOWN"))
    total  = len(market_map)

    print("\n" + "=" * 60)
    print("=== Phase 2.2.A.1 결과 ===")
    print(f"1. 토큰 확보: 성공")
    print(f"2. KOSPI:  {kospi}")
    print(f"3. KOSDAQ: {kosdaq}")
    print(f"4. UNKNOWN: {unknown}" + (f" {[c for c,v in market_map.items() if v.startswith('UNKNOWN')]}" if unknown else ""))
    print(f"5. 실패: {len(failed)}" + (f" {failed}" if failed else ""))
    print(f"6. market_map.json 저장: {OUT} ({OUT.stat().st_size} bytes)")
    ok = (total == len(CODES) and unknown == 0 and len(failed) == 0)
    print(f"7. Phase 2.2 진입 가능: {'YES ✅' if ok else 'NO ❌ — 수석님 확인 필요'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
