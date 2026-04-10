"""B-01 분봉 수집 스크립트 (Phase 1.B)

KIS 실거래 API 에서 2026-04-10 09:00~11:30 분봉 수집.
62 종목 + KOSPI200 선물 근월물 (101S3000).

결정 사항:
- 결정 1: api._get() 직접 호출 허용 (FID_INPUT_HOUR_1 커스텀)
- 결정 2: is_paper=False (실거래 직행)
- 결정 3: 62 종목 전체 스크리닝 통과 가정

수집 전략:
  KIS FHKST03010200 1회 호출 = 기준 시각 이전 ~30분 분봉 반환.
  09:00~11:30 를 커버하기 위해 기준 시각 5개 순차 호출:
    093000, 100000, 103000, 110000, 113000
  → 중복 제거 (time 키) + 시각순 정렬 + 09:00~11:30 필터 적용.

실행:
  cd C:\\Users\\terryn\\AUTOTRADE
  python backtest/B-01_2026-04-10/fetch_minute_bars.py

멈춤 조건:
  - KIS API 에러 (rt_cd != 0): 종목별 재시도 1회, 그래도 실패 시 SKIP 후 계속
  - rate limit 에러 (EGW00201 등): 즉시 멈춤
  - 분봉 0개 이상 종목이 절반 이상: 즉시 멈춤
"""

import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 프로젝트 루트를 sys.path 에 추가 ──────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.kis_api.kis import KISAPI
from src.kis_api.constants import EP_MINUTE_CHART, TR_MINUTE_CHART
from config.settings import Settings, StrategyParams

# ── 상수 ──────────────────────────────────────────────────
TRADE_DATE = "2026-04-10"
BASE_DIR = Path(__file__).resolve().parent
MINUTE_DIR = BASE_DIR / "data" / "minute"
FUTURES_DIR = BASE_DIR / "data" / "futures"

# 09:00~11:30 커버 기준 시각 (각 시각 이전 ~30분 분봉 반환)
FETCH_TIMES = ["093000", "100000", "103000", "110000", "113000"]

# 입력 62 종목 (스크리닝 통과 가정)
CODES = [
    "009150", "000660", "278470", "034220", "010170", "005290",
    "010950", "316140", "012450", "066970", "062040", "105560",
    "240810", "010140", "138930", "082920", "016360", "298040",
    "024110", "017800", "078930", "034730", "095610", "213420",
    "047050", "055550", "067310", "079550", "035420", "007600",
    "183300", "241560", "032640", "363440", "218410", "319660",
    "011930", "077360", "285130", "008770", "050890", "020150",
    "036570", "178320", "139130", "077800", "086450", "281820",
    "420770", "329180", "014680", "003490", "005880", "228760",
    "011790", "267260", "011070", "071050", "012330", "039030",
    "086790", "357780",
]

FUTURES_CODE = "101S3000"  # KOSPI200 선물 근월물

KST = timezone(timedelta(hours=9))


# ── 헬퍼 ──────────────────────────────────────────────────

def now_kst_iso() -> str:
    return datetime.now(KST).isoformat()


def filter_bars(bars: list[dict]) -> list[dict]:
    """09:00~11:30 범위 필터. KIS time 필드는 'HHMMSS' 형식."""
    result = []
    for bar in bars:
        t = bar.get("time", "")
        if len(t) >= 6:
            hhmm = t[:4]
            if "0900" <= hhmm <= "1130":
                result.append(bar)
    return result


def parse_stock_bars(output2: list[dict]) -> dict[str, dict]:
    """output2 → {time: bar} dict. 중복 time 은 먼저 온 값 유지."""
    bars: dict[str, dict] = {}
    for item in output2:
        t = item.get("stck_cntg_hour", "")
        if not t or t in bars:
            continue
        bars[t] = {
            "time": t,
            "open":   int(item.get("stck_oprc", 0) or 0),
            "high":   int(item.get("stck_hgpr", 0) or 0),
            "low":    int(item.get("stck_lwpr", 0) or 0),
            "close":  int(item.get("stck_prpr", 0) or 0),
            "volume": int(item.get("cntg_vol",  0) or 0),
        }
    return bars


def parse_futures_bars(output2: list[dict]) -> dict[str, dict]:
    """선물 output2 → {time: bar}. close 를 float 처리."""
    bars: dict[str, dict] = {}
    for item in output2:
        t = item.get("stck_cntg_hour", "")
        if not t or t in bars:
            continue
        bars[t] = {
            "time":   t,
            "open":   float(item.get("stck_oprc", 0) or 0),
            "high":   float(item.get("stck_hgpr", 0) or 0),
            "low":    float(item.get("stck_lwpr", 0) or 0),
            "close":  float(item.get("stck_prpr", 0) or 0),
            "volume": int(item.get("cntg_vol",   0) or 0),
        }
    return bars


# ── 수집 함수 ──────────────────────────────────────────────

async def fetch_stock(api: KISAPI, code: str) -> list[dict]:
    """단일 주식 종목 분봉 수집. 5회 호출 → 중복 제거 → 필터."""
    all_bars: dict[str, dict] = {}

    for fetch_time in FETCH_TIMES:
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": fetch_time,
            "FID_PW_DATA_INCU_YN": "N",
        }
        try:
            data = await api._get(EP_MINUTE_CHART, TR_MINUTE_CHART, params)
            parsed = parse_stock_bars(data.get("output2", []))
            all_bars.update(parsed)
        except RuntimeError as e:
            msg = str(e)
            # rate limit 에러 → 즉시 중단
            if "EGW00201" in msg or "초당 거래건수" in msg:
                print(f"\n⛔ RATE LIMIT 에러 ({code}@{fetch_time}): {msg}")
                raise
            print(f"  ⚠ {code}@{fetch_time} 실패 (1회 재시도): {msg}")
            await asyncio.sleep(2.0)
            try:
                data = await api._get(EP_MINUTE_CHART, TR_MINUTE_CHART, params)
                all_bars.update(parse_stock_bars(data.get("output2", [])))
            except Exception as e2:
                print(f"  ✗ {code}@{fetch_time} 재시도 실패: {e2}")
        except Exception as e:
            print(f"  ✗ {code}@{fetch_time} 예외: {e}")

    bars = filter_bars(list(all_bars.values()))
    bars.sort(key=lambda b: b["time"])
    return bars


async def fetch_futures(api: KISAPI) -> list[dict]:
    """KOSPI200 선물 분봉 수집.
    선물 전용 TR 이 constants 에 없으므로 주식 EP 에 FID_COND_MRKT_DIV_CODE='F' 시도.
    실패 시 WARN 후 빈 리스트 반환 (선물 데이터 없으면 futures_drop 조건 replay 불가 → 보고).
    """
    all_bars: dict[str, dict] = {}

    for fetch_time in FETCH_TIMES:
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "F",   # 선물
            "FID_INPUT_ISCD": FUTURES_CODE,
            "FID_INPUT_HOUR_1": fetch_time,
            "FID_PW_DATA_INCU_YN": "N",
        }
        try:
            data = await api._get(EP_MINUTE_CHART, TR_MINUTE_CHART, params)
            all_bars.update(parse_futures_bars(data.get("output2", [])))
        except Exception as e:
            print(f"  ⚠ 선물@{fetch_time}: {e}")

    bars = filter_bars(list(all_bars.values()))
    bars.sort(key=lambda b: b["time"])
    return bars


# ── 메인 ──────────────────────────────────────────────────

async def main() -> None:
    MINUTE_DIR.mkdir(parents=True, exist_ok=True)
    FUTURES_DIR.mkdir(parents=True, exist_ok=True)

    # Settings 로드 (.env)
    s = Settings()
    if not s.kis_app_key:
        print("⛔ KIS_APP_KEY 미설정. .env 확인 필요.")
        sys.exit(1)

    # KISAPI 인스턴스 (실거래, 조회 전용)
    api = KISAPI(
        app_key=s.kis_app_key,
        app_secret=s.kis_app_secret,
        account_no=s.kis_account_no,
        is_paper=False,
    )

    await api.connect()
    print(f"KIS API 연결: {api.get_server_type()}")
    print(f"수집 대상: {len(CODES)} 종목 + 선물 1")
    print(f"기준 시각: {FETCH_TIMES}")
    print(f"출력 경로: {BASE_DIR / 'data'}\n")

    skipped: list[str] = []       # 이미 존재
    success: list[str] = []       # 정상 수집
    thin: list[tuple] = []        # 분봉 부족 (< 50)
    empty: list[str] = []         # 분봉 0

    try:
        for i, code in enumerate(CODES, 1):
            out_path = MINUTE_DIR / f"{code}.json"

            if out_path.exists():
                print(f"[{i:2d}/{len(CODES)}] {code} — 스킵 (파일 존재)")
                skipped.append(code)
                continue

            bars = await fetch_stock(api, code)
            n = len(bars)

            payload = {
                "code": code,
                "market": "UNKNOWN",   # Phase 2 에서 StockCandidate 로 확정
                "trade_date": TRADE_DATE,
                "fetched_at": now_kst_iso(),
                "bars": bars,
            }
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            status = "✓" if n >= 50 else ("⚠" if n > 0 else "✗")
            print(f"[{i:2d}/{len(CODES)}] {code} {status} {n} 분봉")

            if n == 0:
                empty.append(code)
            elif n < 50:
                thin.append((code, n))
            else:
                success.append(code)

        # 분봉 0 종목이 절반 이상이면 데이터 품질 의심 → 멈춤
        total_attempted = len(CODES) - len(skipped)
        if total_attempted > 0 and len(empty) / total_attempted >= 0.5:
            print(f"\n⛔ 분봉 0 종목 비율 {len(empty)}/{total_attempted} ≥ 50%. 데이터 품질 의심.")
            print("멈춤 — 수석님 확인 필요.")
            return

        # 선물 수집
        futures_path = FUTURES_DIR / f"{FUTURES_CODE}.json"
        if futures_path.exists():
            print(f"\n[선물] {FUTURES_CODE} — 스킵 (파일 존재)")
            futures_n = json.loads(futures_path.read_text(encoding="utf-8"))
            futures_n = len(futures_n.get("bars", []))
        else:
            print(f"\n[선물] {FUTURES_CODE} 수집 중...")
            f_bars = await fetch_futures(api)
            futures_n = len(f_bars)
            payload = {
                "code": FUTURES_CODE,
                "market": "FUTURES",
                "trade_date": TRADE_DATE,
                "fetched_at": now_kst_iso(),
                "bars": f_bars,
            }
            futures_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            status = "✓" if futures_n >= 50 else ("⚠" if futures_n > 0 else "✗")
            print(f"[선물] {FUTURES_CODE} {status} {futures_n} 분봉")
            if futures_n == 0:
                print("  → 선물 분봉 0: futures_drop 청산 조건 replay 불가. Phase 2 에서 해당 조건 비활성 처리 필요.")

    finally:
        await api.disconnect()
        print("\nKIS API 연결 해제")

    # ── 최종 검증 리포트 ──────────────────────────────────
    all_files = list(MINUTE_DIR.glob("*.json"))
    print("\n" + "=" * 50)
    print("=== Phase 1.C 검증 결과 ===")
    print(f"주식 파일: {len(all_files)}/{len(CODES)}")
    print(f"  정상 (≥50 분봉): {len(success)}")
    print(f"  스킵 (기존 존재): {len(skipped)}")
    print(f"  분봉 부족 (<50): {len(thin)}")
    print(f"  분봉 0:          {len(empty)}")
    print(f"선물: {futures_n} 분봉")

    if thin:
        print(f"\n⚠ 분봉 부족 종목:")
        for code, n in thin:
            print(f"  {code}: {n} 분봉")
    if empty:
        print(f"\n✗ 분봉 0 종목:")
        for code in empty:
            print(f"  {code}")

    print("\n수집 완료. Phase 2 replay harness 진행 가능.")


if __name__ == "__main__":
    asyncio.run(main())
