"""
Phase 1 검증 스크립트 (KIS Open API).

실행:
    python verify_phase1.py

검증 항목:
1. KIS API 토큰 발급
2. 서버 확인 (모의투자 / 실서버)
3. 주식 현재가 조회 (삼성전자)
4. 거래량 순위 조회
5. 프로그램매매 조회
6. pykrx 시총 조회 (사전 캐시용)
7. 계좌 잔고 조회
"""
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, ".")

from config.settings import Settings
from src.kis_api.kis import KISAPI
from src.kis_api.constants import MARKET_CODE_KOSPI

# 삼성전자 (005930) — 검증용 고정 종목
TEST_CODE = "005930"


async def verify():
    settings = Settings()
    results = []

    print("=" * 60)
    print("AUTOTRADE Phase 1 검증 (KIS Open API)")
    print("=" * 60)

    if not settings.kis_app_key or not settings.kis_app_secret:
        print("\n✗ .env에 KIS_APP_KEY, KIS_APP_SECRET이 설정되지 않았습니다.")
        print("  한국투자증권 Open API에서 앱키를 발급받아 .env에 입력하세요.")
        return

    api = KISAPI(
        app_key=settings.kis_app_key,
        app_secret=settings.kis_app_secret,
        account_no=settings.account_no,
        is_paper=settings.is_paper_mode,
    )

    try:
        # ── 1. 토큰 발급 ────────────────────────────────────────
        print("\n[1] KIS API 토큰 발급 중...")
        await api.connect()
        server = api.get_server_type()
        print(f"    ✓ 토큰 발급 성공")
        print(f"    ✓ 접속 서버: {server}")
        if server == "실서버":
            print("    ⚠️  실서버 접속 — 모의투자 설정 확인 필요")
        results.append(("토큰 발급", True, server))

        # ── 2. pykrx 시총 조회 ───────────────────────────────────
        print("\n[2] pykrx 시총 조회 (KOSPI, 8000억 이상)...")
        try:
            from src.kis_api.api_handlers import fetch_market_cap_rank
            cap_list = fetch_market_cap_rank(None, market=MARKET_CODE_KOSPI, min_cap=800_000_000_000)
            print(f"    ✓ {len(cap_list)}종목 (상위 5개):")
            for item in cap_list[:5]:
                print(f"      {item['name']}({item['code']}) — 시총 {item['market_cap']/1e12:.1f}조")
            results.append(("pykrx 시총", True, f"{len(cap_list)}종목"))
        except Exception as e:
            print(f"    ✗ 실패: {e}")
            results.append(("pykrx 시총", False, str(e)))

        # ── 3. 주식 현재가 (삼성전자) ────────────────────────────
        print(f"\n[3] 주식 현재가 — 삼성전자({TEST_CODE})...")
        try:
            info = await api.get_current_price(TEST_CODE)
            print(f"    ✓ 수신:")
            print(f"      종목명: {info.get('name')}")
            print(f"      현재가: {info.get('current_price'):,}원")
            print(f"      등락율: {info.get('change_pct'):+.2f}%")
            print(f"      거래대금: {info.get('trading_value',0)/1e8:.0f}억")
            results.append(("현재가 조회", True, f"{info.get('name')} {info.get('current_price'):,}원"))
        except Exception as e:
            print(f"    ✗ 실패: {e}")
            results.append(("현재가 조회", False, str(e)))

        # ── 4. 거래량 순위 ───────────────────────────────────────
        print(f"\n[4] 거래량 순위 (KOSPI)...")
        try:
            vol_list = await api.get_volume_rank(market="J", min_volume=0)
            if vol_list:
                print(f"    ✓ {len(vol_list)}건 수신 (상위 3개):")
                for item in vol_list[:3]:
                    print(f"      {item.get('name','?')}({item.get('code','?')}) — 거래대금 {item.get('trading_volume_krw',0)/1e8:.0f}억")
                results.append(("거래량 순위", True, f"{len(vol_list)}건"))
            else:
                print("    ✗ 데이터 없음")
                results.append(("거래량 순위", False, "데이터 없음"))
        except Exception as e:
            print(f"    ✗ 실패: {e}")
            results.append(("거래량 순위", False, str(e)))

        # ── 5. 프로그램매매 (삼성전자) ──────────────────────────
        print(f"\n[5] 프로그램매매 — 삼성전자({TEST_CODE})...")
        print(f"    요청 시각: {datetime.now().strftime('%H:%M:%S')}")
        try:
            t_before = datetime.now()
            prog = await api.get_program_trade(TEST_CODE)
            t_after = datetime.now()
            elapsed_ms = (t_after - t_before).total_seconds() * 1000

            print(f"    ✓ 수신 ({elapsed_ms:.0f}ms):")
            print(f"      프로그램순매수: {prog.get('program_net_buy',0):,}")
            print(f"      프로그램매수:   {prog.get('buy_amount',0):,}")
            print(f"      프로그램매도:   {prog.get('sell_amount',0):,}")
            results.append(("프로그램매매", True, f"순매수 {prog.get('program_net_buy',0):,}"))
        except Exception as e:
            print(f"    ✗ 실패: {e}")
            results.append(("프로그램매매", False, str(e)))

        # ── 6. 계좌 잔고 ────────────────────────────────────────
        print(f"\n[6] 계좌 잔고 조회...")
        try:
            balance = await api.get_balance()
            print(f"    ✓ 수신:")
            print(f"      평가금액: {balance.get('total_eval',0):,}원")
            print(f"      예수금:   {balance.get('available_cash',0):,}원")
            print(f"      보유종목: {len(balance.get('holdings',[]))}건")
            results.append(("계좌 잔고", True, f"평가 {balance.get('total_eval',0):,}원"))
        except Exception as e:
            print(f"    ✗ 실패: {e}")
            results.append(("계좌 잔고", False, str(e)))

        # ── 최종 결과 ────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("검증 결과 요약")
        print("=" * 60)
        all_ok = True
        for name, ok, detail in results:
            icon = "✓" if ok else "✗"
            print(f"  [{icon}] {name}: {detail}")
            if not ok:
                all_ok = False

        print()
        if all_ok:
            print("✓ Phase 1 검증 완료 — 다음 단계로 진행 가능")
        else:
            print("✗ 일부 항목 실패 — 위 오류 확인 후 재시도")
        print("=" * 60)

    finally:
        await api.disconnect()


if __name__ == "__main__":
    asyncio.run(verify())
