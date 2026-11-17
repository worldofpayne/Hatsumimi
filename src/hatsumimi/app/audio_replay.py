"""Hear a line again without moving the paused video.

mpv can't play a stretch of audio while its video stays frozen, so this keeps
a second, audio-only mpv with the same file loaded in the background. Replaying
a line seeks *that* player and plays just the line's audio; the main player
(and its paused frame and panel) never move.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .libmpv import mpv

PADDING = 0.15  # seconds before/after the line


class AudioReplay(QObject):
    started = Signal(int)   # line key
    finished = Signal(int)  # line key

    def __init__(self, parent=None):
        super().__init__(parent)
        self.p: mpv.MPV | None = None
        self.path: str | None = None
        self._end = 0.0
        self._key: int | None = None
        self._pending: tuple[float, float] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._tick)

    def _player(self) -> "mpv.MPV":
        if self.p is None:
            self.p = mpv.MPV(vid="no", sid="no", vo="null", audio_display="no",
                             input_default_bindings=False, keep_open="yes",
                             pause=True, osc=False, loglevel="error")
        return self.p

    def preload(self, path: str) -> None:
        """Load the file (paused) ahead of time, so the first replay is instant."""
        if path != self.path:
            self.stop()
            self.path = path
            self._player().loadfile(path)
            self._player().pause = True

    def play(self, path: str, key: int, start: float, stop: float, volume: float | None = None) -> None:
        self.preload(path)
        p = self._player()
        if volume is not None:
            p.volume = volume
        if self._key is not None and self._key != key:
            self.finished.emit(self._key)
        self._key = key
        self._end = stop + PADDING
        p.pause = True
        try:
            p.command("seek", str(max(0.0, start - PADDING)), "absolute+exact")
        except Exception:  # still loading: seek once it can
            self._pending = (start, stop)
        p.pause = False
        self._timer.start()
        self.started.emit(key)

    @property
    def playing(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        p = self.p
        if p is None:
            return self._timer.stop()
        if self._pending is not None:
            start, _ = self._pending
            try:
                p.command("seek", str(max(0.0, start - PADDING)), "absolute+exact")
                self._pending = None
            except Exception:
                return
        t = p.time_pos
        if t is not None and t >= self._end:
            self.stop()

    def stop(self) -> None:
        self._timer.stop()
        self._pending = None
        if self.p is not None:
            self.p.pause = True
        if self._key is not None:
            key, self._key = self._key, None
            self.finished.emit(key)

    def close(self) -> None:
        self.stop()
        if self.p is not None:
            self.p.terminate()
            self.p = None
