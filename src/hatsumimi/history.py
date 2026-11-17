"""What has been heard: subtitle lines recorded during playback.

Same rules as the mpv Lua script:
- lines are keyed by start time (ms), so replays and backward seeks overwrite
  rather than duplicate, and lines skipped by a forward seek are just absent;
- English is paired to a Japanese line by overlapping time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Line:
    key: int        # start time in ms
    start: float
    stop: float
    text: str


@dataclass
class LineHistory:
    ja: dict[int, Line] = field(default_factory=dict)
    en: dict[int, Line] = field(default_factory=dict)

    def clear(self) -> None:
        self.ja.clear()
        self.en.clear()

    def record(self, lang: str, text: str | None, start: float | None, stop: float | None) -> None:
        if not text or start is None:
            return
        key = round(start * 1000)
        store = self.ja if lang == "ja" else self.en
        store[key] = Line(key, start, stop if stop is not None else start, text)

    def recent(self, now: float, count: int) -> list[Line]:
        """The last `count` Japanese lines that started by `now`, oldest first."""
        heard = sorted((l for l in self.ja.values() if l.start <= now + 0.05), key=lambda l: l.key)
        return heard[-count:]

    def english_for(self, line: Line) -> str:
        parts: list[str] = []
        for e in sorted(self.en.values(), key=lambda l: l.key):
            if e.start < line.stop - 0.1 and e.stop > line.start + 0.1:
                t = " ".join(e.text.splitlines())
                if t not in parts:
                    parts.append(t)
        return " / ".join(parts)


# -- choosing subtitle tracks --------------------------------------------------

MUX_TITLE = "Japanese (Hatsumimi)"
_IMAGE_CODECS = ("pgs", "dvd", "dvb", "xsub")
_BAD_EN_WORDS = ("sign", "song", "forced", "karaoke")


def _is_image(track: dict) -> bool:
    codec = (track.get("codec") or "").lower()
    return any(c in codec for c in _IMAGE_CODECS)


def choose_tracks(track_list: list[dict]) -> tuple[int | None, int | None]:
    """(Japanese sid, English sid) from mpv's track-list."""
    subs = [t for t in track_list if t.get("type") == "sub"]
    ja = next((t for t in subs if t.get("title") == MUX_TITLE), None)
    if ja is None:
        ja = next((t for t in subs if (t.get("lang") or "").lower() in ("jpn", "ja")
                   and not _is_image(t)), None)
    en = next((t for t in subs
               if (t.get("lang") or "").lower() in ("eng", "en")
               and not _is_image(t) and not t.get("forced")
               and not any(w in (t.get("title") or "").lower() for w in _BAD_EN_WORDS)), None)
    return (ja["id"] if ja else None), (en["id"] if en else None)
