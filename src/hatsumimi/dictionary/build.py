"""Download JMdict + KANJIDIC2 and build the local SQLite dictionary.

    uv run hatsumimi-dict            # download the latest release and build
    uv run hatsumimi-dict a.zip b.zip  # build from already-downloaded files
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import httpx
from platformdirs import user_data_dir

from .. import __version__

LATEST = "https://api.github.com/repos/scriptin/jmdict-simplified/releases/latest"
USER_AGENT = f"Hatsumimi/{__version__}"

Progress = Callable[[str, float], None]  # message, fraction 0..1 (or -1 = unknown)


def dict_path() -> Path:
    override = os.environ.get("HATSUMIMI_DICT")
    if override:
        return Path(override)
    return Path(user_data_dir("hatsumimi", appauthor=False, roaming=True)) / "dict" / "dictionary.sqlite"


def installed_info() -> dict | None:
    """{'version':..., 'words':..., 'kanji':...} or None if not installed."""
    path = dict_path()
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        con.close()
        return meta
    except sqlite3.Error:
        return None


# -- downloading -------------------------------------------------------------------

def latest_assets(client: httpx.Client) -> tuple[str, str, str]:
    """(jmdict url, kanjidic url, version) of the newest jmdict-simplified release."""
    rel = client.get(LATEST).raise_for_status().json()
    urls = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    jm = next(u for n, u in urls.items() if n.startswith("jmdict-eng-") and "common" not in n and n.endswith(".json.zip"))
    kd = next(u for n, u in urls.items() if n.startswith("kanjidic2-en-") and n.endswith(".json.zip"))
    return jm, kd, rel.get("tag_name", "?")


def _download(client: httpx.Client, url: str, dest: Path, label: str, progress: Progress) -> None:
    with client.stream("GET", url) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = shown = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if done - shown >= 1_000_000 or done == total:  # about once per MB
                    shown = done
                    progress(f"Downloading {label}… {done / 1e6:.1f} MB", done / total if total else -1)


# -- building ------------------------------------------------------------------------

def _hira(s: str) -> str:
    """Katakana -> hiragana, so カタカナ words can be found from either."""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def _read_json_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".json"))
        with z.open(name) as f:
            return json.load(f)


def build(jmdict_zip: Path, kanjidic_zip: Path, out: Path, version: str, progress: Progress) -> None:
    progress("Reading JMdict…", -1)
    jm = _read_json_zip(jmdict_zip)
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE entries (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE keys (key TEXT NOT NULL, id INTEGER NOT NULL, prio INTEGER NOT NULL);
        CREATE TABLE kanji (c TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE tags (tag TEXT PRIMARY KEY, label TEXT NOT NULL);
    """)
    con.executemany("INSERT INTO tags VALUES (?, ?)", list((jm.get("tags") or {}).items()))

    words = jm["words"]
    entries, keys = [], []
    for n, w in enumerate(words):
        eid = int(w["id"])
        k = [x["text"] for x in w.get("kanji", [])]
        r = [x["text"] for x in w.get("kana", [])]
        common = any(x.get("common") for x in w.get("kanji", []) + w.get("kana", []))
        senses = [{
            "p": s.get("partOfSpeech", []),
            "g": [g["text"] for g in s.get("gloss", [])],
            "m": s.get("misc", []),
            "i": s.get("info", []),
        } for s in w.get("sense", [])]
        entries.append((eid, json.dumps({"k": k, "r": r, "c": common, "s": senses},
                                        ensure_ascii=False, separators=(",", ":"))))
        for x in w.get("kanji", []):
            keys.append((x["text"], eid, 2 if x.get("common") else 0))
        for x in w.get("kana", []):
            keys.append((_hira(x["text"]), eid, 2 if x.get("common") else 0))
        if n % 20000 == 0:
            progress(f"Building word list… {n:,} / {len(words):,}", n / len(words) * 0.9)
    con.executemany("INSERT INTO entries VALUES (?, ?)", entries)
    con.executemany("INSERT INTO keys VALUES (?, ?, ?)", keys)
    del jm, words, entries, keys

    progress("Reading KANJIDIC2…", 0.92)
    kd = _read_json_zip(kanjidic_zip)
    rows = []
    for ch in kd["characters"]:
        on, kun, mean = [], [], []
        for g in (ch.get("readingMeaning") or {}).get("groups", []):
            for rd in g.get("readings", []):
                if rd["type"] == "ja_on":
                    on.append(rd["value"])
                elif rd["type"] == "ja_kun":
                    kun.append(rd["value"])
            mean += [m["value"] for m in g.get("meanings", []) if m.get("lang") == "en"]
        misc = ch.get("misc") or {}
        rows.append((ch["literal"], json.dumps({
            "on": on, "kun": kun, "m": mean,
            "sc": (misc.get("strokeCounts") or [None])[0],
            "g": misc.get("grade"), "j": misc.get("jlptLevel"), "f": misc.get("frequency"),
        }, ensure_ascii=False, separators=(",", ":"))))
    con.executemany("INSERT INTO kanji VALUES (?, ?)", rows)

    progress("Indexing…", 0.96)
    con.execute("CREATE INDEX keys_key ON keys(key)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("version", version),
        ("words", str(con.execute("SELECT COUNT(*) FROM entries").fetchone()[0])),
        ("kanji", str(len(rows))),
    ])
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, out)  # swap in only once complete
    progress("Dictionary ready.", 1.0)


def install(progress: Progress = lambda m, f: None) -> Path:
    """Download the latest release and build it into dict_path()."""
    out = dict_path()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60) as client:
        progress("Finding the latest dictionary release…", -1)
        jm_url, kd_url, version = latest_assets(client)
        with tempfile.TemporaryDirectory() as td:
            jm, kd = Path(td) / "jmdict.zip", Path(td) / "kanjidic.zip"
            _download(client, jm_url, jm, "JMdict", progress)
            _download(client, kd_url, kd, "KANJIDIC2", progress)
            build(jm, kd, out, version, progress)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    def show(msg, frac):
        print(f"\r{msg:<60}", end="", flush=True)

    if len(argv) == 2:
        build(Path(argv[0]), Path(argv[1]), dict_path(), "local files", show)
    else:
        install(show)
    print(f"\nInstalled: {dict_path()}  {installed_info()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
