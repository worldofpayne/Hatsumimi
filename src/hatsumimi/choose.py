"""Choosing which subtitle file belongs to a given episode.

A Jimaku entry is messy: for one episode you may find several files - timed
against different releases (WEB vs BluRay), .srt vs .ass, or with notes in the
name like "CC" (closed captions with sound descriptions) - and the API's own
episode filter is only a best-effort guess.

`choose_subtitle` receives plain filenames that are all real subtitle files
(.srt / .ass / .ssa / .vtt). Archives are handled by the caller: it tries the
loose files first, and only opens a .zip and calls this again on its contents
if nothing matched.
"""

from __future__ import annotations

import re

from .identify import episode_of
from .subtitles import ext_of

# Plain dialogue first: SRT has no sign/song lines or style layers, which suits
# both listening practice and the planned pause panel. ASS is still fine.
FORMAT_RANK = {".srt": 0, ".ass": 1, ".ssa": 2, ".vtt": 3}

# Closed captions add sound descriptions like （足音） and speaker names.
# Useful sometimes, but noisier than plain dialogue, so they rank lower.
CAPTION_MARKERS = re.compile(r"(?<![a-z])(cc|sdh)(?![a-z])|字幕付き|聴覚", re.IGNORECASE)


def _rank(name: str) -> tuple:
    return (
        FORMAT_RANK.get(ext_of(name), 9),
        bool(CAPTION_MARKERS.search(name)),
        len(name),  # shorter names are usually the "plain" release
        name,       # finally alphabetical, so the result is always stable
    )


def choose_subtitle(names: list[str], episode: int | None) -> str | None:
    """Return the best filename for `episode`, or None if nothing fits.

    episode is None for movies (then usually any single file is the movie).

    Files whose episode can't be read from the name are never used for a
    series: a wrong subtitle still "syncs", so a clear failure is better.
    """
    if not names:
        return None

    if episode is None:
        # Movie: several files are usually alternative versions of the same
        # film. Skip any that look like numbered parts/episodes.
        candidates = [n for n in names if episode_of(n) is None] or names
    else:
        candidates = [n for n in names if episode_of(n) == episode]

    return min(candidates, key=_rank) if candidates else None
