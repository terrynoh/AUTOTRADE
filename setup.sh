#!/usr/bin/env bash
set -e
echo "═══════════════════════════════════════"
echo "  AUTOTRADE 설치"
echo "═══════════════════════════════════════"

# Python 버전 확인
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python3이 설치되어 있지 않습니다."
    exit 1
fi

# 가상환경 생성
if [ ! -d ".venv" ]; then
    echo "[1/3] 가상환경 생성 중..."
    python3 -m venv .venv
else
    echo "[1/3] 가상환경 이미 존재"
fi

# 패키지 설치
echo "[2/3] 패키지 설치 중..."
source .venv/bin/activate
pip install -e . --quiet

# .env 파일 확인
if [ ! -f ".env" ]; then
    echo "[3/3] .env 파일 생성..."
    cp .env.example .env
    echo ""
    echo "═══════════════════════════════════════"
    echo "  .env 파일을 열어 API 키를 설정하세요!"
    echo "═══════════════════════════════════════"
else
    echo "[3/3] .env 파일 이미 존재"
fi

echo ""
echo "설치 완료! 실행: ./run.sh"
