"""Background thread that prepares videos one at a time.

The pipeline is slow (downloads, syncing), so it must never run on the GUI
thread. Questions for the user are asked by emitting a signal and waiting:
the GUI shows a dialog and hands the answer back.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..identify import MediaInfo
from ..jimaku import Entry, JimakuClient
from ..pipeline import Options, prepare


class PrepareWorker(QThread):
    stage = Signal(str, str)            # video path, stage text
    note = Signal(str, str)             # video path, note text
    finished_one = Signal(str, str, str)  # video path, status, detail
    ask_entry = Signal(object, object)  # MediaInfo, list[Entry]
    idle = Signal()                     # queue is empty

    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._jobs: queue.Queue[tuple[Path, Options] | None] = queue.Queue()
        self._answer: Entry | None = None
        self._answered = threading.Event()

    # -- called from the GUI thread ------------------------------------------

    def enqueue(self, video: Path, opts: Options) -> None:
        self._jobs.put((video, opts))

    def clear_pending(self) -> list[Path]:
        """Drop everything not yet started; returns what was dropped."""
        dropped = []
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return dropped
            if job is not None:
                dropped.append(job[0])

    def answer(self, entry: Entry | None) -> None:
        self._answer = entry
        self._answered.set()

    def shutdown(self) -> None:
        self.clear_pending()
        self._jobs.put(None)
        self._answered.set()  # release a thread waiting on a dialog

    # -- Reporter interface (called on the worker thread) --------------------

    def _stage(self, video: Path, text: str) -> None:
        self.stage.emit(str(video), text)

    def _note(self, video: Path, text: str) -> None:
        self.note.emit(str(video), text)

    def _choose(self, info: MediaInfo, candidates: list[Entry]) -> Entry | None:
        self._answered.clear()
        self.ask_entry.emit(info, candidates)
        self._answered.wait()
        return self._answer

    # -- thread body ----------------------------------------------------------

    def run(self) -> None:
        worker = self

        class _Reporter:
            def stage(self, video, text): worker._stage(video, text)
            def note(self, video, text): worker._note(video, text)
            def choose_entry(self, info, candidates): return worker._choose(info, candidates)

        reporter = _Reporter()
        with JimakuClient(self._api_key) as client:
            while True:
                if self._jobs.empty():
                    self.idle.emit()
                job = self._jobs.get()
                if job is None:
                    return
                video, opts = job
                try:
                    out = prepare(client, video, opts, reporter)
                    self.finished_one.emit(str(video), out.status, out.detail)
                except Exception as e:  # never let one file kill the thread
                    self.finished_one.emit(str(video), "failed", f"Unexpected error: {e!r}")
