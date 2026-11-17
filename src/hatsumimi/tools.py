"""The external programs Hatsumimi uses, and how to install them."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

from .mux import find_mkvmerge


@dataclass
class Tool:
    name: str
    path: str | None
    needed_for: str


def check_tools() -> list[Tool]:
    return [
        Tool("ffmpeg", shutil.which("ffmpeg"), "re-timing subtitles"),
        Tool("ffprobe", shutil.which("ffprobe"), "finding subtitle tracks"),
        Tool("mkvmerge", find_mkvmerge(), "embedding subtitles into .mkv"),
    ]


def install_hint() -> str:
    if sys.platform == "win32":
        return "winget install Gyan.FFmpeg MKVToolNix.MKVToolNix"
    return "sudo pacman -S ffmpeg mkvtoolnix-cli"
