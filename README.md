# AUTOTRADE — 한국 주식 자동매매 시스템

KOSPI/KOSDAQ 장중 단타 자동매매.
11시 기관 순매수 둔화 시점의 주가 조정을 매수 기회로 활용.

## 요구사항

- Python 3.11+
- 한국투자증권 Open API 앱키 (https://apiportal.koreainvestment.com)

## 설치

```bash
cd C:\Users\terryn\AUTOTRADE
pip install -r requirements.txt

# .env 설정
copy .env.example .env
# .env에 KIS_APP_KEY, KIS_APP_SECRET, 계좌번호 입력
```

## 실행

```bash
# Phase 1 검증
python verify_phase1.py

# DRY_RUN 모드 (주문 없이 시그널만 로깅)
python -m src.main

# 테스트
pytest tests/ -v
```

## 매매 모드

| 모드 | 설명 |
|------|------|
| `dry_run` | 주문 없이 시그널만 로깅 + 가상 P&L 추적 |
| `paper` | KIS 모의투자 계좌로 실제 주문 |
| `live` | 실매매 계좌 (충분한 검증 후!) |

## 전략 파라미터 조정

`config/strategy_params.yaml` 수정 후 재시작.

## 구현 상태

- [x] Phase 1: 프로젝트 구조 + 설정 + KIS API 래퍼
- [ ] Phase 1 검증: KIS API 데이터 수신 확인
- [ ] Phase 2: 스크리닝 검증
- [ ] Phase 3: DRY_RUN 풀가동 검증
- [ ] Phase 4: 모의투자 주문 검증
- [ ] Phase 5: 1~2주 모의투자 후 실매매 전환
