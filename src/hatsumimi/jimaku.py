"""A small client for the Jimaku API (https://jimaku.cc/api/docs)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from . import __version__

import logging

# ffsubsync turns on INFO logging globally; keep httpx's per-request lines out of it.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_URL = "https://jimaku.cc"
USER_AGENT = f"Hatsumimi/{__version__}"


@dataclass(frozen=True)
class Entry:
    id: int
    name: str  # romaji
    english_name: str | None
    japanese_name: str | None
    anilist_id: int | None
    is_movie: bool

    @property
    def names(self) -> list[str]:
        return [n for n in (self.name, self.english_name, self.japanese_name) if n]

    def label(self) -> str:
        extra = " / ".join(n for n in (self.english_name, self.japanese_name) if n)
        kind = "movie" if self.is_movie else "series"
        return f"{self.name}" + (f"  ({extra})" if extra else "") + f"  [{kind}, id {self.id}]"


@dataclass(frozen=True)
class SubFile:
    name: str
    url: str
    size: int


class JimakuError(RuntimeError):
    pass


class JimakuClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        # The docs say the key goes in the Authorization header; they don't say
        # whether a "Bearer " prefix is wanted. Raw key is what works today -
        # if Jimaku ever changes that, this is the one line to edit.
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": api_key, "User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
        self._files_cache: dict[int, list[SubFile]] = {}
        self._download_cache: dict[str, bytes] = {}

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- low level -----------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(4):
            resp = self._http.get(url, params=params)
            if resp.status_code != 429:
                break
            wait = float(resp.headers.get("x-ratelimit-reset-after", 2 ** attempt))
            print(f"  (rate limited by Jimaku, waiting {wait:.1f}s)")
            time.sleep(min(wait, 60) + 0.25)
        if resp.status_code == 401:
            raise JimakuError("Jimaku rejected the API key (401). Check your key.")
        if resp.is_error:
            raise JimakuError(f"Jimaku returned {resp.status_code} for {url}: {resp.text[:200]}")
        return resp

    # -- endpoints -----------------------------------------------------------

    def search(self, query: str) -> list[Entry]:
        """Fuzzy search by name across anime AND live-action entries.

        Both are needed: Jimaku's fuzzy search always returns *something*, so
        "anime first, live-action if empty" would never reach live-action films.
        Results are unranked; see fetch.rank_entries.
        """
        seen: dict[int, Entry] = {}
        for anime in (True, False):
            data = self._get(
                "/api/entries/search",
                {"query": query, "anime": str(anime).lower()},
            ).json()
            for d in data:
                seen.setdefault(d["id"], _entry(d))
        return list(seen.values())

    def entry(self, entry_id: int) -> Entry:
        return _entry(self._get(f"/api/entries/{entry_id}").json())

    def files(self, entry_id: int) -> list[SubFile]:
        """All files for an entry (cached, so a season folder costs one request)."""
        if entry_id not in self._files_cache:
            data = self._get(f"/api/entries/{entry_id}/files").json()
            self._files_cache[entry_id] = [
                SubFile(name=d["name"], url=d["url"], size=d.get("size", 0)) for d in data
            ]
        return self._files_cache[entry_id]

    def download(self, file: SubFile, cache: bool = False) -> bytes:
        """Download a file. cache=True keeps it for the rest of the run
        (used for season .zip packs, which every episode would otherwise re-fetch)."""
        if cache and file.url in self._download_cache:
            return self._download_cache[file.url]
        data = self._get(file.url).content
        if cache:
            self._download_cache[file.url] = data
        return data


def _entry(d: dict) -> Entry:
    flags = d.get("flags") or {}
    return Entry(
        id=d["id"],
        name=d["name"],
        english_name=d.get("english_name"),
        japanese_name=d.get("japanese_name"),
        anilist_id=d.get("anilist_id"),
        is_movie=bool(flags.get("movie")),
    )
