@echo off
chcp 65001 >nul
echo ═══════════════════════════════════════
echo   AUTOTRADE EXE 빌드
echo ═══════════════════════════════════════

:: 가상환경 활성화
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [WARN] 가상환경 없음, 시스템 Python 사용
)

:: PyInstaller 설치 확인
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [1/2] PyInstaller 설치 중...
    pip install pyinstaller --quiet
) else (
    echo [1/2] PyInstaller 이미 설치됨
)

:: 빌드
echo [2/2] EXE 빌드 중...
pyinstaller autotrade.spec --noconfirm

echo.
echo ═══════════════════════════════════════
echo   빌드 완료!
echo.
echo   결과 폴더: dist\AUTOTRADE\
echo.
echo   배포 시 포함할 파일:
echo     dist\AUTOTRADE\        (전체 폴더)
echo     .env.example           (사용자가 .env로 복사)
echo     config\strategy_params.yaml  (이미 포함됨)
echo ═══════════════════════════════════════
pause
