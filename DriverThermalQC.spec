# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for DriverThermalQC (single standalone EXE).

Build on Windows:
    pip install -r requirements.txt pyinstaller
    pyinstaller DriverThermalQC.spec --noconfirm

The EXE is onefile and windowed. All persistent data (database, settings,
backups, logs, exports) lives in %LOCALAPPDATA%\\DriverThermalQC, never in
the temporary extraction directory.
"""

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DriverThermalQC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
