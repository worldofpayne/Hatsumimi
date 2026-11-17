"""Work out what a video file is (title / season / episode) from its filename."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from guessit import guessit

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".webm", ".ts", ".mov"}


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    title: str
    season: int | None
    episode: int | None  # None for movies
    is_movie: bool

    @property
    def cache_key(self) -> str:
        """Key used to remember which Jimaku entry this show maps to."""
        return f"{self.title.casefold()}|s{self.season or 1}" if not self.is_movie else self.title.casefold()


def identify(path: Path) -> MediaInfo | None:
    g = guessit(path.name)
    title = g.get("title")
    if not title:
        return None
    episode = g.get("episode")
    if isinstance(episode, list):  # e.g. "E01-E02" double episodes: use the first
        episode = episode[0]
    season = g.get("season")
    if isinstance(season, list):
        season = season[0]
    return MediaInfo(
        path=path,
        title=str(title),
        season=season,
        episode=episode,
        is_movie=g.get("type") == "movie" and episode is None,
    )


def episode_of(filename: str) -> int | None:
    """Episode number guessed from any filename (used on subtitle names too)."""
    ep = guessit(filename).get("episode")
    if isinstance(ep, list):
        ep = ep[0]
    return ep if isinstance(ep, int) else None


def find_videos(paths: list[Path], recursive: bool = False) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            out.extend(sorted(f for f in it if f.is_file() and f.suffix.lower() in VIDEO_EXTS))
        elif p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            out.append(p)
        else:
            print(f"Skipping {p} (not a video file or folder)")
    return out
