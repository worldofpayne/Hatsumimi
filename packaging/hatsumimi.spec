# PyInstaller recipe for Hatsumimi.  Build with:
#     uv run pyinstaller packaging/hatsumimi.spec --noconfirm
# Output: dist/Hatsumimi/  (Hatsumimi.exe on Windows, Hatsumimi on Linux)
#
# A one-folder build, not one-file: libmpv alone is ~120 MB, and a one-file
# exe would unpack all of it to a temp folder on every launch.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
src = root / "src" / "hatsumimi"

datas = [
    (str(src / "app" / "assets"), "hatsumimi/app/assets"),
    (str(src / "mpv"), "hatsumimi/mpv"),   # the standalone mpv script (install-mpv-script)
]
# guessit / babelfish / rebulk read config and language tables from package files.
for pkg in ("guessit", "babelfish", "rebulk"):
    datas += collect_data_files(pkg)

hiddenimports = []
for pkg in ("guessit", "rebulk", "babelfish", "ffsubsync", "py7zr", "pysubs2"):
    hiddenimports += collect_submodules(pkg)

binaries = []
if sys.platform == "win32":
    # Linux uses the system libmpv (pacman: mpv) instead.
    binaries.append((str(root / "vendor" / "libmpv" / "libmpv-2.dll"), "."))

icon = str(src / "app" / "assets" / ("hatsumimi.ico" if sys.platform == "win32" else "hatsumimi.png"))

a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter", "matplotlib", "IPython", "pytest",
        # Qt parts the app doesn't use (keeps the download smaller)
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtDesigner",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Hatsumimi",
    console=False,          # a normal windowed app: no console window
    icon=icon,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Hatsumimi", upx=False)
