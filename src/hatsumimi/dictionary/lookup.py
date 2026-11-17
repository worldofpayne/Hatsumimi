"""Rikaikun-style lookup: the longest dictionary word starting at a character.

Given the text from the hovered character onwards, try the longest piece
first and shrink it; for each piece, try every deinflected form against the
dictionary. Results are ordered longest match first, then common words.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from functools import lru_cache

from .build import dict_path
from .deinflect import deinflect, pos_matches

MAX_CHARS = 16
MAX_RESULTS = 8


def to_hiragana(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def is_kanji(c: str) -> bool:
    return "一" <= c <= "鿿" or "㐀" <= c <= "䶿" or c == "々"


def is_japanese(c: str) -> bool:
    return ("぀" <= c <= "ヿ") or is_kanji(c) or c in "ー〜～"


@dataclass
class Sense:
    pos: list[str]
    glosses: list[str]
    misc: list[str]
    info: list[str]


@dataclass
class WordResult:
    id: int
    kanji: list[str]
    readings: list[str]
    common: bool
    senses: list[Sense]
    matched: str            # the text as it appeared (e.g. 書かなかった)
    length: int             # how many characters of the text it covers
    reasons: tuple[str, ...] = field(default_factory=tuple)  # ("past", "negative")


@dataclass
class KanjiResult:
    char: str
    on: list[str]
    kun: list[str]
    meanings: list[str]
    strokes: int | None
    grade: int | None
    jlpt: int | None
    freq: int | None


class Dictionary:
    """Read-only access to the built dictionary. Safe to share between threads."""

    def __init__(self, path=None):
        path = path or dict_path()
        if not path.exists():
            raise FileNotFoundError(path)
        self._con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self._lock = threading.Lock()
        self.tags: dict[str, str] = dict(self._con.execute("SELECT tag, label FROM tags"))
        self._entry = lru_cache(maxsize=4096)(self._entry_uncached)
        self._ids_for = lru_cache(maxsize=16384)(self._ids_for_uncached)

    def _q(self, sql, args=()):
        with self._lock:
            return self._con.execute(sql, args).fetchall()

    def _ids_for_uncached(self, key: str) -> tuple[tuple[int, int], ...]:
        keys = {key, to_hiragana(key)}
        rows = self._q(f"SELECT id, MAX(prio) FROM keys WHERE key IN ({','.join('?' * len(keys))}) GROUP BY id",
                       tuple(keys))
        return tuple(rows)

    def _entry_uncached(self, eid: int) -> dict:
        return json.loads(self._q("SELECT data FROM entries WHERE id = ?", (eid,))[0][0])

    def lookup(self, text: str) -> list[WordResult]:
        """Words starting at text[0], longest first."""
        results = self._lookup(text)
        # Honorific prefix: お嬢ちゃん isn't in JMdict but 嬢ちゃん is.
        if len(text) > 1 and text[0] in "おご御":
            best = results[0].length if results else 0
            extra = [r for r in self._lookup(text[1:]) if r.length + 1 > best]
            for r in extra:
                r.length += 1
                r.matched = text[0] + r.matched
                r.reasons = ("honorific prefix",) + r.reasons
            results = sorted(extra + results, key=lambda r: -r.length)
        return results[:MAX_RESULTS]

    def _lookup(self, text: str) -> list[WordResult]:
        text = text[:MAX_CHARS]
        results: list[WordResult] = []
        seen: set[int] = set()
        for n in range(len(text), 0, -1):
            piece = text[:n]
            found: list[tuple[int, int, WordResult]] = []
            for cand in deinflect(piece):
                for eid, prio in self._ids_for(cand.text):
                    if eid in seen:
                        continue
                    e = self._entry(eid)
                    all_pos = [p for s in e["s"] for p in s["p"]]
                    if not pos_matches(cand.types, all_pos):
                        continue
                    seen.add(eid)
                    exact = cand.text == piece
                    found.append((0 if exact else 1, -prio, WordResult(
                        id=eid, kanji=e["k"], readings=e["r"], common=e["c"],
                        senses=[Sense(s["p"], s["g"], s["m"], s["i"]) for s in e["s"]],
                        matched=piece, length=n, reasons=cand.reasons)))
            found.sort(key=lambda t: (t[0], t[1], len(t[2].reasons)))
            results += [r for *_, r in found]
            if len(results) >= MAX_RESULTS:
                break
        return results[:MAX_RESULTS]

    def kanji(self, c: str) -> KanjiResult | None:
        rows = self._q("SELECT data FROM kanji WHERE c = ?", (c,))
        if not rows:
            return None
        d = json.loads(rows[0][0])
        return KanjiResult(c, d["on"], d["kun"], d["m"], d.get("sc"), d.get("g"), d.get("j"), d.get("f"))

    def close(self) -> None:
        self._con.close()


_shared: Dictionary | None = None


def shared() -> Dictionary | None:
    """The app-wide dictionary, or None if it isn't installed yet."""
    global _shared
    if _shared is None:
        try:
            _shared = Dictionary()
        except (FileNotFoundError, sqlite3.Error):
            return None
    return _shared


def reset_shared() -> None:
    """Call after (re)installing the dictionary."""
    global _shared
    if _shared is not None:
        _shared.close()
    _shared = None
