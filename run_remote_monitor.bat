@echo off
chcp 65001 > nul
echo ========================================
echo  AUTOTRADE 원격 모니터링 시작
echo ========================================

REM 대시보드 백그라운드 실행
start "AUTOTRADE Dashboard" python -X utf8 -m src.dashboard.app

echo 대시보드 시작 중... (3초 대기)
timeout /t 3 /nobreak > nul

REM Cloudflare Tunnel 시작 (포그라운드 — URL 출력)
echo.
echo [cloudflared] 터널 시작 중...
echo [cloudflared] 아래 trycloudflare.com URL을 Machine B 브라우저에서 열어주세요
echo ----------------------------------------
cloudflared tunnel --url http://localhost:8503
