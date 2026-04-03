@echo off
chcp 65001 >nul
echo ═══════════════════════════════════════
echo   AUTOTRADE 배포 패키지 생성
echo ═══════════════════════════════════════

:: 먼저 EXE 빌드
call build_exe.bat

:: 배포에 필요한 추가 파일 복사
echo 배포 파일 복사 중...
copy /Y .env.example dist\AUTOTRADE\.env.example >nul

echo.
echo ═══════════════════════════════════════
echo   배포 패키지 준비 완료!
echo.
echo   dist\AUTOTRADE\ 폴더를 통째로 전달하세요.
echo.
echo   받는 사람 사용법:
echo     1. .env.example → .env 로 복사
echo     2. .env 에 KIS API 키 입력
echo     3. AUTOTRADE.exe 실행
echo     4. 브라우저에서 http://localhost:8501
echo ═══════════════════════════════════════
pause
