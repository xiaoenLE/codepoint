# -*- mode: python ; coding: utf-8 -*-
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--onedir', action='store_true')
args = parser.parse_args()


a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[('build/icon.ico', 'build')],
    hiddenimports=[],
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
    [] if args.onedir else a.binaries,
    [] if args.onedir else a.datas,
    [],
    exclude_binaries=args.onedir,
    name='CodePoint',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=not args.onedir,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='build/version_info.txt',
    icon=['build/icon.ico'],
)

if args.onedir:
    # Windows 10+ supplies UCRT and its API sets. Keep the separate VC runtime.
    binaries = [entry for entry in a.binaries
                if entry[0].lower() != 'ucrtbase.dll'
                and not entry[0].lower().startswith('api-ms-win-')]
    coll = COLLECT(exe, binaries, a.datas, name='CodePoint', upx=False)
