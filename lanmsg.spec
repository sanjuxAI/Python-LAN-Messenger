# lanmsg.spec
# PyInstaller spec file for LanMsg
# Run with: pyinstaller lanmsg.spec

import sys
from PyInstaller.building.build_main import Analysis, PYZ, EXE

block_cipher = None

a = Analysis(
    ['lanmsg.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'windows_curses',   # curses on Windows
        'curses',
        'socket',
        'threading',
        'json',
        'hashlib',
        'hmac',
        'struct',
        'os',
        'time',
        'datetime',
        'argparse',
        'logging',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'email', 'html', 'http',
        'xml', 'pydoc', 'doctest', 'difflib',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='LanMsg',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # compress with UPX if available (smaller exe)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # must be True — curses needs a real console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # set to 'lanmsg.ico' if you have an icon file
    onefile=True,       # bundle everything into a single LanMsg.exe
)
