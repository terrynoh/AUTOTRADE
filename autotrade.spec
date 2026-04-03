# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — AUTOTRADE.exe 빌드 설정.

빌드: pyinstaller autotrade.spec
결과: dist/AUTOTRADE/ 폴더에 exe + 의존 파일 생성
"""

import os

block_cipher = None
ROOT = os.path.abspath(".")

a = Analysis(
    ["run.py"],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # 대시보드 HTML 템플릿
        ("src/dashboard/templates", "src/dashboard/templates"),
        # 전략 파라미터 YAML (기본값)
        ("config/strategy_params.yaml", "config"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "src.dashboard.app",
        "src.main",
        "src.kis_api.kis",
        "src.kis_api.api_handlers",
        "src.kis_api.constants",
        "src.core.screener",
        "src.core.monitor",
        "src.core.trader",
        "src.core.risk_manager",
        "src.models.stock",
        "src.models.order",
        "src.models.trade",
        "src.utils.notifier",
        "src.utils.market_calendar",
        "src.utils.logger",
        "config.settings",
        "pydantic_settings",
        "websockets",
        "aiohttp",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AUTOTRADE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AUTOTRADE",
)
