"""The missed-lines log: one JSON object per line, appended as you mark them.

Two logs are read together:
- the app's own:   Windows %APPDATA%\\hatsumimi\\missed.jsonl
                   Linux   ~/.local/share/hatsumimi/missed.jsonl
- the mpv script's: <mpv config>/hatsumimi-missed.jsonl
Both use the same fields. Environment overrides (used by tests):
HATSUMIMI_MISSED_LOG, HATSUMIMI_MPV_MISSED_LOG.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_data_dir


def missed_log_path() -> Path:
    override = os.environ.get("HATSUMIMI_MISSED_LOG")
    if override:
        return Path(override)
    return Path(user_data_dir("hatsumimi", appauthor=False, roaming=True)) / "missed.jsonl"


def mpv_missed_log_path() -> Path:
    """Where the standalone mpv Lua script writes (its ~~/ = mpv's config folder)."""
    override = os.environ.get("HATSUMIMI_MPV_MISSED_LOG")
    if override:
        return Path(override)
    if sys.platform == "win32":
        from .install_mpv import config_dir  # honours portable_config
        return config_dir()[0] / "hatsumimi-missed.jsonl"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "mpv" / "hatsumimi-missed.jsonl"


def append_mark(video: str, title: str | None, start: float, stop: float, ja: str, en: str) -> None:
    path = missed_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "video": video, "title": title, "start": start, "stop": stop,
        "ja": ja, "en": en,
        "marked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


@dataclass(frozen=True)
class MarkedLine:
    video: str
    title: str
    start: float
    stop: float
    ja: str
    en: str
    marked_at: str          # ISO 8601 UTC
    source: Path            # which log file it came from
    raw: str                # the exact JSON line, so deletion removes only it

    @property
    def video_exists(self) -> bool:
        return bool(self.video) and Path(self.video).exists()


def _read(path: Path) -> list[MarkedLine]:
    out: list[MarkedLine] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
            out.append(MarkedLine(
                video=d.get("video") or "", title=d.get("title") or "",
                start=float(d.get("start") or 0), stop=float(d.get("stop") or 0),
                ja=d.get("ja") or "", en=d.get("en") or "",
                marked_at=d.get("marked_at") or "", source=path, raw=raw,
            ))
        except (ValueError, TypeError, AttributeError):
            continue  # a damaged line shouldn't hide the rest
    return out


def load_marks() -> list[MarkedLine]:
    """Everything from both logs, newest first, without duplicates."""
    seen: set[tuple[str, int]] = set()
    marks: list[MarkedLine] = []
    for path in (missed_log_path(), mpv_missed_log_path()):
        for m in _read(path):
            key = (m.video, round(m.start * 1000))
            if key not in seen:
                seen.add(key)
                marks.append(m)
    marks.sort(key=lambda m: m.marked_at, reverse=True)
    return marks


def restore_marks(lines: list[MarkedLine]) -> None:
    """Undo a delete: put the exact original lines back in their log files."""
    by_file: dict[Path, list[str]] = {}
    for m in lines:
        by_file.setdefault(m.source, []).append(m.raw)
    for path, raws in by_file.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write("".join(r + "\n" for r in raws))


def delete_marks(to_delete: list[MarkedLine]) -> None:
    """Remove these entries from whichever log each came from."""
    by_file: dict[Path, set[str]] = {}
    for m in to_delete:
        by_file.setdefault(m.source, set()).add(m.raw)
    for path, raws in by_file.items():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        kept = [l for l in lines if l not in raws]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(l + "\n" for l in kept), encoding="utf-8", newline="\n")
        os.replace(tmp, path)  # never leaves a half-written log
