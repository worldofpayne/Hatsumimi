"""Load python-mpv with the right libmpv.

Windows: libmpv-2.dll lives in <project>/vendor/libmpv (or next to the
packaged app). Linux: the system libmpv (pacman: mpv) is found automatically.
Import `mpv` from here, never directly, so the DLL is findable first.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _candidate_dirs() -> list[Path]:
    dirs = []
    if getattr(sys, "frozen", False):  # packaged app (phase 5)
        dirs.append(Path(sys.executable).parent)
        if hasattr(sys, "_MEIPASS"):
            dirs.append(Path(sys._MEIPASS))
    dirs.append(Path(__file__).resolve().parents[3] / "vendor" / "libmpv")
    return dirs


if sys.platform == "win32":
    for d in _candidate_dirs():
        if (d / "libmpv-2.dll").exists():
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            os.add_dll_directory(str(d))
            break

import mpv  # noqa: E402  (must come after the PATH setup above)

__all__ = ["mpv"]
