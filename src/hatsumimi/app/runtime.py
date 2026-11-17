"""Process-wide fixes for running as a desktop app (especially when packaged)."""

from __future__ import annotations

import subprocess
import sys


def prepare() -> None:
    _give_streams_somewhere()
    _hide_console_windows()


def _give_streams_somewhere() -> None:
    """A windowed app has no console: sys.stdout/stderr are None, and any
    library that prints (tqdm, logging, warnings) would crash. Send it all to
    a log file in the config folder instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from ..config import config_dir
    try:
        path = config_dir() / "app.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        log = open(path, "w", encoding="utf-8", buffering=1)
    except OSError:
        import os
        log = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def _hide_console_windows() -> None:
    """On Windows a GUI app that starts ffmpeg/ffprobe/mkvmerge would flash a
    black console window for each one. Start them with no window."""
    if sys.platform != "win32":
        return
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    original = subprocess.Popen.__init__
    if getattr(original, "_hatsumimi", False):
        return

    def init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | flag
        original(self, *args, **kwargs)

    init._hatsumimi = True
    subprocess.Popen.__init__ = init
