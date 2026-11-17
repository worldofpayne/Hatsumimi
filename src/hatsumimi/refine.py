"""Section-by-section re-timing, after the global sync.

Jimaku subtitles are often timed for a different broadcast than your video:
the cold open might match, but everything after the opening song is 10 s
late (a different ad break or recap). One global shift can't fix both, so
after ffsubsync's global shift this:

1. splits the Japanese subtitles into sections at long dialogue gaps (opening
   song, ad break, eyecatch, ending, preview: the places where cuts happen);
2. for each section, tries shifts of up to ±30 s against the English dialogue
   track and moves the section only if another shift is clearly better.

Only used when the video has an embedded text subtitle track as a reference.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pysubs2

RES = 0.1            # seconds per sample
SECTION_GAP = 6.0    # a dialogue gap this long starts a new section
MAX_SHIFT = 30.0     # seconds either way
MIN_CUES = 4         # sections with fewer lines are too small to judge
MIN_EDGE_GAIN = 8    # line edges newly matched by the shift (at least this many)
MIN_GAIN = 1.5       # ...and at least 1.5x as many as where the section is now
MIN_FIT = 0.4        # ...and at least 40% of the section's edges matched
MIN_MARGIN = 3       # ...and clearly better than any other candidate shift

_SIGN_STYLE = re.compile(r"sign|song|kara|lyric|title|note|insert|eyecatch|preview|^op\b|^ed\b|\bop$|\bed$",
                         re.IGNORECASE)
_SOUND_ONLY = re.compile(r"^(?:（[^）]*）|\([^)]*\)|[♪～〜\s]|\\N)*$")


@dataclass
class RefineResult:
    sections: int
    moved: list[tuple[float, float]]  # (section start in s, shift applied in s)


def _dialogue_events(subs: pysubs2.SSAFile) -> list[pysubs2.SSAEvent]:
    """Spoken lines only: no signs, songs or positioned typesetting."""
    out = []
    for e in subs:
        if e.is_comment or e.end <= e.start:
            continue
        if _SIGN_STYLE.search(e.style or ""):
            continue
        if "\\pos" in e.text or "\\move" in e.text:
            continue
        out.append(e)
    return out


def _boundaries(events) -> np.ndarray:
    """Every line start and end, in samples. Line edges follow speech onsets
    and pauses, whoever wrote the subtitles, so they line up between the
    Japanese and English tracks far more sharply than 'speech present' does
    (in dense dialogue, a 10 s shift still overlaps almost perfectly)."""
    pts = []
    for e in events:
        pts += [e.start / 1000 / RES, e.end / 1000 / RES]
    return np.array(sorted(pts))


def _target(points: np.ndarray, length: int, tolerance: float = 0.3) -> np.ndarray:
    """1 within ±tolerance of any reference boundary, else 0."""
    sig = np.zeros(length, dtype=np.float32)
    w = int(round(tolerance / RES))
    for p in points.astype(int):
        sig[max(p - w, 0):max(p + w + 1, 0)] = 1.0
    return sig


def _extract_reference(video: Path, stream: str, dest: Path) -> pysubs2.SSAFile:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", f"0:{stream}", str(dest)],
                   check=True, capture_output=True)
    return pysubs2.load(str(dest))


def _sections(events: list[pysubs2.SSAEvent]) -> list[list[pysubs2.SSAEvent]]:
    events = sorted(events, key=lambda e: e.start)
    out: list[list[pysubs2.SSAEvent]] = []
    last_end = None
    for e in events:
        if last_end is None or e.start - last_end > SECTION_GAP * 1000:
            out.append([])
        out[-1].append(e)
        last_end = e.end if last_end is None else max(last_end, e.end)
    return out


def refine(video: Path, synced_sub: Path, ref_stream: str) -> RefineResult:
    """Re-time synced_sub section by section against the video's text track."""
    with tempfile.TemporaryDirectory() as td:
        ref = _extract_reference(video, ref_stream, Path(td) / "ref.ass")
    subs = pysubs2.load(str(synced_sub), encoding="utf-8")

    ref_events = _dialogue_events(ref)
    if len(ref_events) < 20:
        return RefineResult(0, [])
    length = int((max(max(e.end for e in ref_events), max(e.end for e in subs)) / 1000 + 2 * MAX_SHIFT) / RES)
    target = _target(_boundaries(ref_events), length)

    spoken = [e for e in subs if not _SOUND_ONLY.match(e.plaintext.strip())]
    sections = _sections(spoken)
    max_lag = int(MAX_SHIFT / RES)
    moved: list[tuple[float, float]] = []

    for sec in sections:
        if len(sec) < MIN_CUES:
            continue
        pts = np.round(_boundaries(sec)).astype(int)
        lags = [lag for lag in range(-max_lag, max_lag + 1)
                if pts[0] + lag >= 0 and pts[-1] + lag < length]  # never before 0:00
        # How many of this section's line edges land on an English line edge.
        scores = {lag: int(target[pts + lag].sum()) for lag in lags}
        base = scores.get(0, 0)
        best_lag = max(scores, key=lambda l: (scores[l], -abs(l)))
        best = scores[best_lag]
        runner_up = max((s for l, s in scores.items() if abs(l - best_lag) > int(1 / RES)), default=0)

        # Move only on strong, unambiguous evidence. Measured on real episodes:
        # genuine re-timings gain 15-40 matched edges; chance matches gain 3-6.
        if (best_lag == 0
                or best - base < max(MIN_EDGE_GAIN, 0.2 * len(pts))
                or best < MIN_GAIN * max(base, 1)
                or best < MIN_FIT * len(pts)
                or best - runner_up < MIN_MARGIN):
            continue

        shift_ms = round(best_lag * RES * 1000)
        # Move every line in this section's time span, including the sound
        # captions that were left out of the comparison.
        start, end = sec[0].start, max(e.end for e in sec)
        for e in subs:
            if start <= e.start <= end:
                e.start += shift_ms
                e.end += shift_ms
        moved.append((start / 1000, shift_ms / 1000))

    if moved:
        subs.sort()
        subs.save(str(synced_sub), encoding="utf-8")
    return RefineResult(len(sections), moved)
