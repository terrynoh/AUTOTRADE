"""
KRX 공식 전종목 기본정보 크롤링.

목표: KRX_only vs KRX_NXT 종목 명확 분류 → W-31 가설 검증용 ground truth 확보.

KRX 데이터 시스템:
- OTP 발급: http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd
- CSV 다운로드: http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd
- 전종목 기본정보 path: dbms/MDC/STAT/standard/MDCSTAT01901

read-only. 1회성 데이터 수집.
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime
from pathlib import Path

import requests


OTP_URL = "https://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
CSV_URL = "https://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"

OUT_DIR = Path(r"C:\Users\terryn\AUTOTRADE\logs\ws_runtime")
TS = datetime.now().strftime("%Y%m%d_%H%M%S")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Origin": "https://data.krx.co.kr",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        # KRX 메인 페이지 방문 → 세션 쿠키 획득
        s.get("https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd", timeout=15)
        _session = s
    return _session


def fetch_otp(form: dict) -> str:
    s = _get_session()
    r = s.post(OTP_URL, data=form, timeout=15)
    r.raise_for_status()
    otp = r.text.strip()
    if not otp or otp.upper() == "LOGOUT":
        raise RuntimeError(f"OTP 응답 비정상: {otp[:100]!r}")
    return otp


def fetch_csv(otp: str) -> str:
    s = _get_session()
    r = s.post(CSV_URL, data={"code": otp}, timeout=30)
    r.raise_for_status()
    # KRX CSV는 EUC-KR
    return r.content.decode("euc-kr", errors="replace")


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"=== KRX 전종목 기본정보 크롤링 ===")
    print(f"개시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST")
    print()

    # === Step 1: KRX 전종목 기본정보 (MDCSTAT01901) ===
    print("[1/2] KRX 전종목 기본정보 (MDCSTAT01901) 다운로드")
    form_basic = {
        "mktId": "ALL",
        "share": "1",
        "csvxls_isNo": "false",
        "name": "fileDown",
        "url": "dbms/MDC/STAT/standard/MDCSTAT01901",
    }
    otp = fetch_otp(form_basic)
    print(f"  OTP: {otp[:30]}...")
    csv_text = fetch_csv(otp)
    header, rows = parse_csv(csv_text)
    print(f"  컬럼: {header}")
    print(f"  종목 수: {len(rows)}")
    print()

    # 저장
    out_basic = OUT_DIR / f"krx_basic_{TS}.csv"
    out_basic.write_text(csv_text, encoding="utf-8")
    print(f"  저장: {out_basic}")
    print()

    # 컬럼 분석 — '시장구분' 또는 'NXT' 관련 컬럼 찾기
    print("[2/2] 컬럼 분석 — NXT 식별 가능성")
    nxt_related_cols = [i for i, c in enumerate(header) if "구분" in c or "NXT" in c or "넥" in c.upper()]
    print(f"  '구분/NXT' 관련 컬럼 인덱스: {nxt_related_cols}")
    for idx in nxt_related_cols:
        print(f"    [{idx}] {header[idx]}")

    # 각 분류 컬럼의 unique 값 확인
    for idx in nxt_related_cols:
        unique_vals = set()
        for row in rows:
            if idx < len(row):
                unique_vals.add(row[idx])
        print(f"  [{idx}] {header[idx]} unique 값: {sorted(unique_vals)[:30]}")
    print()

    # === Step 3: 검증 — 우리 12종목의 분류 확인 ===
    test_codes = {
        "035420", "051910", "068270", "207940", "006400",
        "034730", "018260", "032830", "086790", "105560",
        "278470", "010140",
        # 어제 0건 4종목 추가
        "001250", "011930", "092190", "097230",
    }

    print("=== 우리 검증 대상 종목 분류 확인 ===")
    code_idx = None
    name_idx = None
    for i, c in enumerate(header):
        if "단축" in c and "코드" in c:
            code_idx = i
        if c.strip() == "한글 종목약명" or c.strip() == "한글종목약명" or c.strip() == "종목명":
            name_idx = i

    if code_idx is None:
        # fallback: 첫 컬럼이 보통 단축코드
        for i, c in enumerate(header):
            if "코드" in c:
                code_idx = i
                break

    print(f"  코드 컬럼 idx={code_idx} ({header[code_idx] if code_idx is not None else 'N/A'})")
    print(f"  종목명 컬럼 idx={name_idx} ({header[name_idx] if name_idx is not None else 'N/A'})")
    print()

    print(f"{'코드':<8} {'종목명':<22}", end="")
    for idx in nxt_related_cols:
        print(f"{header[idx]:<20}", end="")
    print()

    found = {}
    for row in rows:
        if code_idx is None or code_idx >= len(row):
            continue
        code = row[code_idx].strip()
        if code in test_codes:
            name = row[name_idx].strip() if name_idx is not None and name_idx < len(row) else ""
            classifications = []
            for idx in nxt_related_cols:
                classifications.append(row[idx].strip() if idx < len(row) else "")
            found[code] = (name, classifications)

    for code in sorted(test_codes):
        if code in found:
            name, cls = found[code]
            print(f"{code:<8} {name:<20}", end="")
            for c in cls:
                print(f"{c:<20}", end="")
            print()
        else:
            print(f"{code:<8} (없음)")

    print()
    print(f"발견: {len(found)}/{len(test_codes)} 종목")


if __name__ == "__main__":
    main()
