"""Embed ("mux") a subtitle file into an .mkv as a Japanese track.

Uses mkvmerge (MKVToolNix). Nothing is re-encoded: every existing stream
(video, audio, subtitles, fonts, chapters) is copied as-is, and the subtitle
is added as one more track.
The new file is written next to the original, checked, and only then swapped
in, so a failure part-way never damages the original video.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Title given to the track we add. Lets reruns replace it instead of adding a
# second copy, and lets the sync step ignore it as a timing reference.
MUX_TITLE = "Japanese (Hatsumimi)"


@dataclass
class SubStream:
    rel_index: int  # position among subtitle streams: ffmpeg's "s:N"
    codec: str
    language: str
    title: str


def subtitle_streams(video: Path) -> list[SubStream]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "s",
             "-show_entries", "stream=index,codec_name:stream_tags=language,title",
             "-of", "json", str(video)],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    streams = []
    for i, s in enumerate(json.loads(out).get("streams", [])):
        tags = s.get("tags") or {}
        streams.append(SubStream(i, s.get("codec_name", ""),
                                 (tags.get("language") or "").lower(), tags.get("title") or ""))
    return streams


def has_muxed_track(video: Path) -> bool:
    return any(s.title == MUX_TITLE for s in subtitle_streams(video))


def _duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


@dataclass
class MuxResult:
    ok: bool
    message: str = ""


def find_mkvmerge() -> str | None:
    """mkvmerge (MKVToolNix) - often installed without being on PATH."""
    found = shutil.which("mkvmerge")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and (p := Path(base) / "MKVToolNix" / "mkvmerge.exe").exists():
            return str(p)
    return None


def mux_subtitle(video: Path, sub: Path) -> MuxResult:
    # mkvmerge, not ffmpeg: VLC 3 shows nothing for text-subtitle tracks muxed
    # by current ffmpeg builds (verified on a real episode), while the same
    # subtitles muxed by mkvmerge display fine. mkvmerge is the reference
    # Matroska muxer, so it's the safe choice for every player.
    if video.suffix.lower() != ".mkv":
        return MuxResult(False, f"muxing is only done for .mkv files (this is {video.suffix})")
    mkvmerge = find_mkvmerge()
    if not mkvmerge:
        return MuxResult(False, "mkvmerge not found - install MKVToolNix (https://mkvtoolnix.download)")

    try:
        info = json.loads(subprocess.run([mkvmerge, "-J", str(video)], capture_output=True,
                                         text=True, encoding="utf-8", check=True).stdout)
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        return MuxResult(False, f"mkvmerge couldn't read the video: {e}")
    tracks = info.get("tracks", [])
    subs = [t for t in tracks if t.get("type") == "subtitles"]
    ours = [t["id"] for t in subs if (t.get("properties") or {}).get("track_name") == MUX_TITLE]
    kept = len(subs) - len(ours)
    tmp = video.with_name(video.stem + ".hatsumimi-tmp.mkv")

    cmd = [mkvmerge, "--quiet", "-o", str(tmp)]
    if ours:  # replace a track added by an earlier run
        cmd += ["--subtitle-tracks", "!" + ",".join(map(str, ours))]
    cmd += [
        str(video),
        "--language", "0:jpn",
        "--track-name", f"0:{MUX_TITLE}",
        "--default-track-flag", "0:no",  # don't force it on; players keep their usual default
        "--sub-charset", "0:UTF-8",
        str(sub),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        # mkvmerge exit codes: 0 = ok, 1 = ok with warnings, 2 = error
        if proc.returncode >= 2 or not tmp.exists():
            msg = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
            return MuxResult(False, "mkvmerge failed: " + " ".join(msg))

        # Check the new file before trusting it with the original's place.
        new_subs = subtitle_streams(tmp)
        if len(new_subs) != kept + 1 or new_subs[-1].title != MUX_TITLE:
            return MuxResult(False, "new file didn't have the expected subtitle tracks")
        d_old, d_new = _duration(video), _duration(tmp)
        if d_old and (d_new is None or abs(d_old - d_new) > 2.0):
            return MuxResult(False, f"new file's length looks wrong ({d_new} vs {d_old}s)")

        st = video.stat()
        try:
            os.replace(tmp, video)  # one step: there's never a moment without a video
        except PermissionError:
            return MuxResult(False, "the video is open in another program (close the player and rerun)")
        # Keep the original dates, so Jellyfin's "recently added" isn't affected.
        os.utime(video, ns=(st.st_atime_ns, st.st_mtime_ns))
        action = "replaced the earlier Japanese track" if ours else "added a Japanese track"
        return MuxResult(True, action)
    finally:
        tmp.unlink(missing_ok=True)
