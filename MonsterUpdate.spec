"""PyInstaller spec for the standalone Monster-ww updater window.

One-file, windowed (no console), elevated so it can replace the app directory
even when the app is installed under Program Files. Only tkinter + py7zr are
needed, so the executable stays small and has no Qt DLL/plugin dependencies.
"""
import os
import re
from pathlib import Path

from PyInstaller.building.build_main import Analysis, PYZ
from PyInstaller.building.api import EXE
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VSVersionInfo,
    VarFileInfo, VarStruct,
)
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve()

app_version = os.environ.get('APP_VERSION', '0.0.0').lstrip('v')
match = re.match(r'^v?(\d+)\.(\d+)\.(\d+)(?:-|$)', app_version)
file_version = tuple(map(int, match.groups())) + (0,) if match else (0, 0, 0, 0)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=file_version, prodvers=file_version),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', 'Monster-ww Updater'),
            StringStruct('FileVersion', app_version),
            StringStruct('InternalName', 'monsterww-updater'),
            StringStruct('OriginalFilename', 'MonsterUpdate.exe'),
            StringStruct('ProductName', 'Monster-ww'),
            StringStruct('ProductVersion', app_version),
            StringStruct('Comments', 'Monster-ww standalone update installer.'),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

pyd_data, pyd_binaries, pyd_imports = collect_all('py7zr')

a = Analysis(
    ['updater_app.py'],
    pathex=[str(ROOT)],
    binaries=pyd_binaries,
    datas=pyd_data,
    hiddenimports=['update_core', *pyd_imports, 'tkinter', 'tkinter.ttk'],
    excludes=['PySide6', 'Mon', 'numpy', 'cv2', 'onnxruntime', 'openvino'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='MonsterUpdate',
    console=False,               # GUI window only, never a console
    disable_windowed_traceback=False,
    uac_admin=True,              # replaces app dir, may be under Program Files
    uac_uiaccess=False,
    icon=str(ROOT / 'icons' / 'icon.ico'),
    version=version_info,
    manifest=str(ROOT / '.github' / 'scripts' / 'MonsterUpdater.manifest.xml'),
)
