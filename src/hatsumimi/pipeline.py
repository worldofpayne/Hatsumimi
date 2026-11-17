"""Preparing a video: find, download, sync and embed its Japanese subtitles.

Shared by the command line (fetch.py) and the app. Anything that would talk to
the user (progress, the "which show is this?" question) goes through a
Reporter, so each front end can present it its own way.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

import httpx

from .choose import choose_subtitle
from .identify import MediaInfo, episode_of, identify
from .jimaku import Entry, JimakuClient, JimakuError
from .mux import MUX_TITLE, has_muxed_track, mux_subtitle
from .subtitles import archive_members, ext_of, is_image_sub_pack, is_subtitle, to_utf8_text

WORK_DIR = ".hatsumimi"  # per-video-folder: cached entry choices + raw downloads
LARGE_OFFSET = 20.0      # seconds; bigger shifts are worth a manual check

# Errors that fail one video without stopping a batch.
RECOVERABLE = (JimakuError, httpx.HTTPError, zipfile.BadZipFile, OSError)


@dataclass
class Options:
    force: bool = False         # redo videos that already have Japanese subs
    sync: bool = True
    mux: bool = True
    entry_id: int | None = None  # skip the search, use this Jimaku entry


@dataclass
class Outcome:
    video: Path
    status: str  # "ok", "skipped", "unsynced", "failed"
    detail: str


class Reporter(Protocol):
    def stage(self, video: Path, text: str) -> None:
        """A step has started (e.g. "Syncing")."""

    def note(self, video: Path, text: str) -> None:
        """Extra information worth showing (e.g. which file was picked)."""

    def choose_entry(self, info: MediaInfo, candidates: list[Entry]) -> Entry | None:
        """Several Jimaku entries fit: ask the user. None = skip this video."""


# -- entry resolution (which Jimaku show is this?) ---------------------------

def _cache_file(folder: Path) -> Path:
    return folder / WORK_DIR / "entries.json"


def _load_cache(folder: Path) -> dict:
    try:
        return json.loads(_cache_file(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(folder: Path, cache: dict) -> None:
    path = _cache_file(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


_SEASON_RE = re.compile(
    r"(\d+)(?:st|nd|rd|th)\s*season|season\s*(\d+)|第\s*(\d+)\s*期|\bs(\d+)\b|(\d+)(?:st|nd|rd|th)\b",
    re.IGNORECASE,
)


def season_in(text: str) -> int | None:
    m = _SEASON_RE.search(text)
    return int(next(g for g in m.groups() if g)) if m else None


def rank_entries(results: list[Entry], info: MediaInfo) -> list[Entry]:
    """Order search results by how well they fit this video, dropping junk.

    Score = best title similarity (romaji / English / Japanese name), plus a
    bonus when the entry's season matches the video's season.
    """
    wanted_season = info.season or season_in(info.title) or 1
    query = info.title.casefold()

    def score(e: Entry) -> float:
        sim = max(SequenceMatcher(None, query, n.casefold()).ratio() for n in e.names)
        entry_season = season_in(e.name) or 1
        return sim + (0.3 if entry_season == wanted_season else 0.0)

    scored = sorted(((score(e), e) for e in results), key=lambda t: -t[0])
    good = [e for s, e in scored if s >= 0.6]
    return good or [e for _, e in scored]  # nothing close: show everything


def resolve_entry(client: JimakuClient, info: MediaInfo, opts: Options,
                  reporter: Reporter) -> Entry | None:
    if opts.entry_id is not None:
        return client.entry(opts.entry_id)

    folder = info.path.parent
    cache = _load_cache(folder)
    if info.cache_key in cache:
        return client.entry(cache[info.cache_key]["id"])

    results = rank_entries(client.search(info.title), info)
    if not results:
        return None
    entry = results[0] if len(results) == 1 else reporter.choose_entry(info, results[:10])
    if entry:
        cache[info.cache_key] = {"id": entry.id, "name": entry.name}
        _save_cache(folder, cache)
    return entry


# -- picking + downloading the file ------------------------------------------

def fetch_subtitle(client: JimakuClient, entry: Entry, info: MediaInfo,
                   reporter: Reporter) -> tuple[str, bytes] | None:
    """Return (filename, raw bytes) of the chosen subtitle, or None."""
    files = client.files(entry.id)
    episode = None if (info.is_movie or entry.is_movie) else info.episode

    # 1. Loose subtitle files first: cheap, and usually per-episode.
    loose = {f.name: f for f in files if is_subtitle(f.name)}
    name = choose_subtitle(list(loose), episode)
    if name:
        return name, client.download(loose[name])

    # 2. Then look inside .zip/.7z packs (one download each, so only when needed).
    #    Packs named for this episode go first: a per-episode pack is a small
    #    download, a whole-season pack (no episode in its name) comes after.
    packs = [f for f in files
             if ext_of(f.name) in {".zip", ".7z"} and not is_image_sub_pack(f.name)]
    if episode is not None:
        packs = [f for f in packs if episode_of(f.name) in (episode, None)]
        packs.sort(key=lambda f: episode_of(f.name) is None)
    for f in packs:
        members = archive_members(f.name, client.download(f, cache=True))
        name = choose_subtitle(list(members), episode)
        if name:
            return name, members[name]

    rars = [f.name for f in files if ext_of(f.name) == ".rar"]
    if rars:
        reporter.note(info.path, f"{len(rars)} .rar archive(s) on Jimaku weren't searched "
                                 f"(not supported), e.g. {rars[0]}")
    return None


def existing_japanese_sub(video: Path) -> Path | None:
    for ext in (".srt", ".ass", ".ssa", ".vtt"):
        for tag in (".ja", ".jpn"):
            p = video.with_name(video.stem + tag + ext)
            if p.exists():
                return p
    return None


# -- per-video pipeline -------------------------------------------------------

def prepare(client: JimakuClient, video: Path, opts: Options, reporter: Reporter) -> Outcome:
    """Run the whole pipeline for one video. Never raises for expected failures."""
    try:
        return _prepare(client, video, opts, reporter)
    except RECOVERABLE as e:
        return Outcome(video, "failed", f"{type(e).__name__}: {e}")


def _prepare(client: JimakuClient, video: Path, opts: Options, reporter: Reporter) -> Outcome:
    reporter.stage(video, "Checking")
    info = identify(video)
    if info is not None:
        where = "movie" if info.is_movie else (
            f"S{info.season or 1:02}E{info.episode:02}" if info.episode is not None else "episode ?")
        reporter.note(video, f"{info.title} ({where})")

    if not opts.force:
        if existing := existing_japanese_sub(video):
            return Outcome(video, "skipped", f"already has {existing.name}")
        if has_muxed_track(video):
            return Outcome(video, "skipped", f"already has an embedded '{MUX_TITLE}' track")

    if info is None:
        return Outcome(video, "failed", "couldn't work out the title from the filename")
    if not info.is_movie and info.episode is None:
        # Otherwise the chooser would treat it as a movie and grab any file.
        return Outcome(video, "failed", "couldn't find an episode number in the filename")

    reporter.stage(video, "Finding on Jimaku")
    entry = resolve_entry(client, info, opts, reporter)
    if entry is None:
        return Outcome(video, "failed", f"no Jimaku entry chosen/found for '{info.title}'")
    reporter.note(video, f"Jimaku: {entry.name} (id {entry.id})")

    reporter.stage(video, "Downloading")
    found = fetch_subtitle(client, entry, info, reporter)
    if found is None:
        return Outcome(video, "failed", f"no file on Jimaku matched {where}")
    sub_name, raw = found
    reporter.note(video, f"Picked: {sub_name}")

    ext = ext_of(sub_name)
    raw_path = video.parent / WORK_DIR / "raw" / f"{video.stem}.ja{ext}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    # Normalised to UTF-8 + LF. newline="\n" stops Windows re-adding \r.
    raw_path.write_text(to_utf8_text(raw), encoding="utf-8", newline="\n")
    final = video.with_name(f"{video.stem}.ja{ext}")

    if not opts.sync:
        shutil.copyfile(raw_path, final)
        return Outcome(video, "unsynced", f"{final.name} (sync skipped)")

    reporter.stage(video, "Syncing")
    from .sync import sync_subtitle  # imported late: only needed when syncing

    res = sync_subtitle(video, raw_path, final)
    if not res.ok:
        shutil.copyfile(raw_path, final)
        return Outcome(video, "unsynced", f"sync failed, saved unsynced copy. {res.message}")
    detail = f"synced to {res.reference}"
    large = False
    if res.offset is not None:
        detail += f", shifted {res.offset:+.2f}s"
        large = abs(res.offset) > LARGE_OFFSET
        if large:
            detail += " (large shift: worth a quick check)"
    if res.sections_moved:
        n = res.sections_moved
        detail += f"; {n} section{'s' if n > 1 else ''} re-timed separately"

    if not opts.mux or video.suffix.lower() != ".mkv":
        # Non-.mkv files (e.g. .mp4) simply keep a separate subtitle file.
        return Outcome(video, "ok", f"{final.name} - {detail}")
    if large:
        # A suspicious sync stays a separate file, easy to inspect or delete.
        return Outcome(video, "unsynced", f"{final.name} - {detail}; not embedded because of the large shift")

    reporter.stage(video, "Embedding")
    m = mux_subtitle(video, final)
    if not m.ok:
        return Outcome(video, "unsynced", f"{final.name} - {detail}; embedding failed: {m.message}")
    final.unlink()  # the track is inside the video now; a copy stays in .hatsumimi/raw
    return Outcome(video, "ok", f"{m.message} - {detail}")
