# -*- mode: python ; coding: utf-8 -*-

import os

# 版本资源由 scripts/make_version.py 按 tag 生成（CI 里跑）。
# 本地 build.bat 不设这个变量，打出来的 exe 和以前一样没有版本资源。
VERSION_FILE = os.environ.get('MULTIRDP_VERSION_FILE') or None


a = Analysis(
    ['src/tray_app.py'],
    pathex=['src'],
    binaries=[],
    datas=[('assets/tray.png', 'assets')],
    hiddenimports=['webui.server', 'updater'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MultiRDP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/multirdp.ico',
    version=VERSION_FILE,
)
