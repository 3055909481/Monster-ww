"""PyInstaller spec for the Monster-ww full-update portable build.

The GUI window belongs to this exe directly (unlike the old pyappify layout
where pythonw.exe hosted it), so Windows taskbar grouping reads our VERSIONINFO
(ProductName=Monster-ww) instead of "Python". This is the same approach xjtu-ww
uses; the difference is our framework package is ``Mon`` (a fork of ``ok``),
and we run elevated because we control the game window.

Build steps (see portable-release.yml):
  1. Cython-compile src/**/*.py + config.py into .pyd (source obfuscation).
  2. build_char_sources.py snapshots built-in character code.
  3. PyInstaller bundles everything into dist/Monster-ww/.
"""
from pathlib import Path
import os
import re

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH)

# Static resources the runtime reads relative to the executable directory.
datas = [(str(root / name), name) for name in ('assets', 'icons', 'i18n', 'workshop_bundle')]
datas += [(str(root / name), '.') for name in ('LICENSE.txt', 'README.md')]

# Character-source snapshots for the workshop "edit built-in character" feature.
char_sources = root / 'build' / 'char_sources'
if not (char_sources / 'BaseChar.py').is_file():
    raise RuntimeError('Run .github/scripts/build_char_sources.py before packaging.')
datas.append((str(char_sources), 'char_sources'))

# Cython-compiled application modules ship as .pyd (no readable source).
compiled = sorted(root.glob('src/**/*.cp312-win_amd64.pyd'))
if not compiled:
    raise RuntimeError('Compiled src/*.pyd modules are missing; run build_ext first.')
for module in compiled:
    datas.append((str(module), str(module.parent.relative_to(root))))
if (root / 'config.cp312-win_amd64.pyd').is_file():
    datas.append((str(root / 'config.cp312-win_amd64.pyd'), '.'))
# Patch modules must stay as source (runtime monkeypatches need real descriptors).
for name in ('__init__.py', 'identity.py', 'schedule_patch.py', 'adaptive_interval.py',
             'team_overlay.py', 'click_jitter.py'):
    src = root / 'src' / name
    if src.is_file():
        datas.append((str(src), 'src'))

# .pyd 模块由 datas 原样复制到 exe 同级 src/ 下，运行时靠 sys.path 加载
# （xjtu 同款模式）。patch 模块是源码，显式列出确保被记录。
hiddenimports = collect_submodules('src')
hiddenimports += ['src.identity', 'src.schedule_patch', 'src.adaptive_interval',
                  'src.team_overlay', 'src.click_jitter']
binaries = collect_dynamic_libs('openvino')
# src/updater.py ships as a Cython .pyd, so PyInstaller cannot see its
# function-level `import py7zr`. Collect it explicitly (with its codecs).
pyd_data, pyd_binaries, pyd_imports = collect_all('py7zr')
datas += pyd_data
binaries += pyd_binaries
hiddenimports += list(pyd_imports)
# Our framework fork is named Mon (upstream is ok). Workshop character code
# still imports "ok"; identity.py aliases ok -> Mon at runtime, so we only
# need to bundle Mon here.
for package in ('Mon', 'onnxocr'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += list(package_imports)

# PySide6 plugins (platforms/styles/imageformats) are found by PyInstaller's hook.
# OpenCV and Playwright need their runtime data.
try:
    cv_data, cv_bin, cv_imports = collect_all('cv2')
    datas += cv_data
    binaries += cv_bin
    hiddenimports += list(cv_imports)
except Exception:
    pass
try:
    pw_data, pw_bin, pw_imports = collect_all('playwright')
    datas += pw_data
    binaries += pw_bin
    hiddenimports += list(pw_imports)
except Exception:
    pass

# Qt uses the ICU ABI supplied by Windows, not a third-party copy on PATH.
app_version = os.environ.get('APP_VERSION', 'dev')
version_file = root / 'build' / 'version.txt'
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(app_version, encoding='utf-8')
datas.append((str(version_file), '.'))

match = re.match(r'^v?(\d+)\.(\d+)\.(\d+)(?:-|$)', app_version)
file_version = tuple(map(int, match.groups())) + (0,) if match else (0, 0, 0, 0)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=file_version, prodvers=file_version),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', 'Monster-ww'),
            StringStruct('FileVersion', app_version),
            StringStruct('InternalName', 'monsterww'),
            StringStruct('OriginalFilename', 'Monster-ww.exe'),
            StringStruct('ProductName', 'Monster-ww'),
            StringStruct('ProductVersion', app_version),
            StringStruct('Comments', 'Monster-ww portable build.'),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

analysis = Analysis(
    [str(root / 'main.py')],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
)
analysis.binaries = [entry for entry in analysis.binaries
                     if Path(entry[0]).name.lower() != 'icuuc.dll'
                     and not Path(entry[0]).name.lower().startswith('icudt')]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    # Keep Chinese logs readable on Windows installations using other code pages.
    [('X utf8', None, 'OPTION')],
    exclude_binaries=True,
    name='Monster-ww',
    console=False,
    icon=str(root / 'icons' / 'icon.ico') if (root / 'icons' / 'icon.ico').is_file()
         else str(root / 'icons' / 'icon.png'),
    version=version_info,
    manifest=str(root / '.github' / 'scripts' / 'MonsterLauncher.manifest.xml'),
    # PyInstaller 6 rewrites requestedExecutionLevel from this flag (default
    # asInvoker), ignoring the level in the manifest file above. Without it
    # the app runs at medium integrity and PostMessage to the elevated game
    # is blocked by UIPI. Keep both in sync.
    uac_admin=True,
    contents_directory='.',
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    name='Monster-ww',
)
