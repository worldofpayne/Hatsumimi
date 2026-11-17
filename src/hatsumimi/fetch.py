"""fetch-subs: download Japanese subtitles from Jimaku and sync them to your videos.

    uv run fetch-subs "D:\\Anime\\Frieren"            # every video in a folder
    uv run fetch-subs "episode.mkv" --entry-id 1234   # skip the search
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import jimaku_api_key
from .identify import MediaInfo, find_videos
from .jimaku import Entry, JimakuClient
from .pipeline import Options, Outcome, prepare


class CliReporter:
    """Prints progress to the terminal and asks questions with input()."""

    def __init__(self):
        self._current: Path | None = None

    def _header(self, video: Path) -> None:
        if video != self._current:
            self._current = video
            print(f"\n{video.name}")

    def stage(self, video: Path, text: str) -> None:
        self._header(video)
        if text in ("Syncing", "Embedding"):
            print(f"  {text}...", flush=True)

    def note(self, video: Path, text: str) -> None:
        self._header(video)
        print(f"  {text}")

    def choose_entry(self, info: MediaInfo, candidates: list[Entry]) -> Entry | None:
        what = info.title + (f" season {info.season}" if info.season else "")
        print(f"\n  Several Jimaku entries match '{what}':")
        for i, e in enumerate(candidates, 1):
            print(f"    {i}. {e.label()}")
        print("    0. none of these (skip)")
        if not sys.stdin.isatty():
            print("  (not interactive - using 1; pass --entry-id to choose)")
            return candidates[0]
        while True:
            choice = input("  Which one? ").strip()
            if choice.isdigit() and 0 <= int(choice) <= len(candidates):
                return candidates[int(choice) - 1] if int(choice) else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fetch-subs", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+", type=Path, help="video files and/or folders")
    p.add_argument("-r", "--recursive", action="store_true", help="look inside sub-folders too")
    p.add_argument("--entry-id", type=int, help="use this Jimaku entry instead of searching")
    p.add_argument("--no-sync", action="store_true", help="download only, don't re-time")
    p.add_argument("-f", "--force", action="store_true", help="replace existing Japanese subtitles")
    p.add_argument("--mux", action=argparse.BooleanOptionalAction, default=True,
                   help="embed the synced subtitles into .mkv files (default); "
                        "--no-mux keeps them as a separate .ja.srt/.ass file")
    args = p.parse_args(argv)

    videos = find_videos(args.paths, args.recursive)
    if not videos:
        print("No video files found.")
        return 1

    opts = Options(force=args.force, sync=not args.no_sync, mux=args.mux, entry_id=args.entry_id)
    reporter = CliReporter()
    outcomes: list[Outcome] = []
    with JimakuClient(jimaku_api_key()) as client:
        for video in videos:
            try:
                outcomes.append(prepare(client, video, opts, reporter))
            except KeyboardInterrupt:
                print("\nStopped.")
                break

    print("\n" + "=" * 60)
    marks = {"ok": "OK  ", "skipped": "SKIP", "unsynced": "WARN", "failed": "FAIL"}
    for o in outcomes:
        print(f"[{marks[o.status]}] {o.video.name}\n       {o.detail}")
    return 0 if all(o.status in ("ok", "skipped") for o in outcomes) else 2


if __name__ == "__main__":
    raise SystemExit(main())
