"""Re-time a subtitle file to match a particular video, using ffsubsync.

Reference priority:
  1. An embedded *text* subtitle track in the video (sub-to-sub alignment is
     the most reliable; music and sound effects can't confuse it).
  2. The video's audio (voice activity detection) as a fallback.
"""

from __future__ import annotations

import json
import io
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from .mux import MUX_TITLE, subtitle_streams

TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
LANG_PREFERENCE = ("jpn", "ja", "eng", "en")
BAD_TRACK_WORDS = ("sign", "song", "forced", "karaoke")


@dataclass
class SyncResult:
    ok: bool
    reference: str  # human-readable description of what we synced against
    offset: float | None = None
    message: str = ""
    sections_moved: int = 0  # parts re-timed separately (e.g. a cold open)


def find_reference_track(video: Path) -> tuple[str, str] | None:
    """Return (ffmpeg stream spec like 's:1', description) or None."""
    candidates = []
    for s in subtitle_streams(video):
        if s.title == MUX_TITLE:
            continue  # our own earlier output: syncing against it proves nothing
        if s.codec not in TEXT_SUB_CODECS:
            continue  # PGS / VobSub are images: useless as a timing reference
        if any(w in s.title.lower() for w in BAD_TRACK_WORDS):
            continue  # "Signs & Songs" tracks only cover a fraction of dialogue
        lang = s.language
        rank = LANG_PREFERENCE.index(lang) if lang in LANG_PREFERENCE else len(LANG_PREFERENCE)
        desc = f"embedded {lang or 'untagged'} subtitles (s:{s.rel_index}"
        desc += f", '{s.title}')" if s.title else ")"
        candidates.append((rank, s.rel_index, desc))

    if not candidates:
        return None
    _, rel_index, desc = min(candidates)
    return f"s:{rel_index}", desc


_sync_lock = threading.Lock()  # ffsubsync keeps module-level logging state


def sync_subtitle(video: Path, sub_in: Path, sub_out: Path, max_offset: float = 120) -> SyncResult:
    """Run ffsubsync in-process (works the same from source and in the
    packaged app, which has no separate Python to launch)."""
    from ffsubsync.ffsubsync import make_parser, run

    ref = find_reference_track(video)
    argv = [str(video), "-i", str(sub_in), "-o", str(sub_out),
            "--max-offset-seconds", str(max_offset), "--output-encoding", "utf-8"]
    if ref:
        argv += ["--reference-stream", ref[0]]
        reference = ref[1]
    else:
        reference = "audio (no usable embedded text subtitles)"

    # Capture ffsubsync's log so a failure can say why.
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    with _sync_lock:
        # ffsubsync installs its own console handler; silence it during the run
        # (its messages go into `buf` instead, shown only if something fails).
        others = root.handlers[:]
        for h in others:
            root.removeHandler(h)
        root.addHandler(handler)
        try:
            result = run(make_parser().parse_args(argv))
        except SystemExit as e:  # argparse / ffsubsync bail-outs
            result = {"retval": e.code or 1}
        except Exception as e:  # noqa: BLE001 - report, don't crash the batch
            result = {"retval": 1}
            buf.write(f"{type(e).__name__}: {e}\n")
        finally:
            root.removeHandler(handler)
            for h in others:
                root.addHandler(h)

    if result.get("retval", 1) != 0 or not sub_out.exists():
        tail = "\n".join(buf.getvalue().strip().splitlines()[-5:])
        return SyncResult(ok=False, reference=reference, message=tail or "ffsubsync failed")
    offset = result.get("offset_seconds")
    moved = 0
    if ref:
        # One global shift can leave a cold open or recap 10 s out when the
        # subtitles were timed for a different broadcast: fix those sections.
        from .refine import refine
        try:
            moved = len(refine(video, sub_out, ref[0]).moved)
        except Exception:  # noqa: BLE001 - the global sync is still a valid result
            moved = 0
    return SyncResult(ok=True, reference=reference, sections_moved=moved,
                      offset=float(offset) if offset is not None else None)
