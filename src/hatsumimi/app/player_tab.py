"""The Player tab: mpv inside the app, with the pause panel beside the video.

Watch with subtitles hidden. Pause and the last few Japanese lines (with the
English underneath) appear beside the video.

Keys: Space play/pause · ←/→ seek 5 s · while paused: ↑/↓ choose a line,
R replay it, M mark it as missed, E show/hide English · F / double-click
fullscreen · Esc leave fullscreen
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QProxyStyle, QPushButton, QSlider, QSplitter,
    QStackedWidget, QStyle, QStyleFactory, QTextBrowser, QVBoxLayout, QWidget,
)

from ..history import Line, LineHistory, choose_tracks
from ..identify import VIDEO_EXTS
from ..marks import append_mark
from . import theme

PANEL_LINES = 5
REPLAY_PADDING = 0.25
CURSOR_HIDE_MS = 2000  # fullscreen playback: hide the cursor after this long still
JA_FONTS = ["Yu Gothic UI", "Noto Sans CJK JP", "Noto Sans JP", "Meiryo", "sans-serif"]
SPEAKER_RE = re.compile(r"^（[^）]*）")
SOUND_ONLY_RE = re.compile(r"^(?:（[^）]*）|\([^)]*\)|[♪～〜\s])*$")

# Block "kinds", stored in each panel block's userState as line_index*10 + kind,
# so a position in the panel maps straight back to its line (hover lookups).
KIND_JA, KIND_EN, KIND_GAP = 0, 1, 9


def _fmt_time(t: float | None) -> str:
    if t is None:
        return "--:--"
    t = int(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class _JumpClickStyle(QProxyStyle):
    """Left-click on a slider's track jumps the handle there (and can keep
    dragging), instead of Qt's default of stepping a page at a time."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_Slider_AbsoluteSetButtons:
            return Qt.LeftButton.value
        return super().styleHint(hint, option, widget, returnData)


class JumpSlider(QSlider):
    _style = None  # one shared proxy style

    def __init__(self, *args):
        super().__init__(*args)
        if JumpSlider._style is None:
            JumpSlider._style = _JumpClickStyle(QStyleFactory.create("Fusion"))
        self.setStyle(JumpSlider._style)


class PetalCanvas(QWidget):
    """White background with a scatter of sakura petals."""

    def __init__(self, seed: int = 7, parent=None):
        super().__init__(parent)
        self._seed = seed

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.WHITE))
        theme.paint_petals(p, QRectF(self.rect()), seed=self._seed)


class _MpvBridge(QObject):
    """mpv calls back on its own thread; these signals hop to the GUI thread."""
    sub = Signal(str, object, object, object)  # lang, text, start, stop
    paused = Signal(bool)
    file_loaded = Signal()
    restarted = Signal()


class PlayerTab(QWidget):
    fullscreen_requested = Signal(bool)
    marked = Signal()  # a line was added to the missed-lines log

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.player = None
        self.video = None
        self.history = LineHistory()
        self.active = False          # current file has a Japanese track
        self.panel_open = False
        self.shown: list[Line] = []
        self.selected: int | None = None
        self.keep_key: int | None = None
        self.english_on = True
        self.marked_keys: set[int] = set()
        self.replaying = False
        self.replay_armed = False
        self.replay_end = 0.0
        self.fullscreen = False
        self.current_path: str | None = None
        self._after_load = None
        self._slider_held = False

        self.error: str | None = None
        try:
            self._build()
        except (OSError, ImportError) as e:  # libmpv missing: the rest of the app still works
            self.error = f"The player needs libmpv, which couldn't be loaded:\n{e}"
            lay = QVBoxLayout(self)
            msg = QLabel(self.error)
            msg.setAlignment(Qt.AlignCenter)
            msg.setWordWrap(True)
            lay.addWidget(msg)

    # -- construction ---------------------------------------------------------------

    def _build(self) -> None:
        from ..config import config_dir
        from .video import MpvWidget, make_player

        log_path = config_dir() / "mpv.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "w", encoding="utf-8")
        self.player = make_player(lambda lvl, comp, msg: self._log.write(f"[{lvl}] {comp}: {msg}"))

        self.bridge = _MpvBridge()
        self.bridge.sub.connect(self._on_sub)
        self.bridge.paused.connect(self._on_pause)
        self.bridge.file_loaded.connect(self._on_file_loaded)
        self.bridge.restarted.connect(self._on_restart)
        p = self.player

        # Note: p["x"] in python-mpv reads *options*; live properties are
        # attributes (sub-start -> p.sub_start).
        self._closing = False

        def ja_cb(_name, text):
            if not self._closing:
                self.bridge.sub.emit("ja", text, p.sub_start, p.sub_end)

        def en_cb(_name, text):
            if not self._closing:
                self.bridge.sub.emit("en", text, p.secondary_sub_start, p.secondary_sub_end)

        def pause_cb(_name, value):
            if not self._closing:
                self.bridge.paused.emit(bool(value))

        # Kept so shutdown can detach them before mpv is destroyed: a callback
        # reading a property from a destroyed mpv crashes.
        self._observers = [("sub-text", ja_cb), ("secondary-sub-text", en_cb), ("pause", pause_cb)]
        for name, cb in self._observers:
            p.observe_property(name, cb)
        p.event_callback("file-loaded")(lambda _e: self._closing or self.bridge.file_loaded.emit())
        p.event_callback("playback-restart")(lambda _e: self._closing or self.bridge.restarted.emit())

        # Video + panel
        self.video = MpvWidget(p)
        # Click = play/pause straight away (no waiting to see if a second click
        # follows). A double-click's first click already toggled pause, so the
        # double-click undoes that and toggles fullscreen instead.
        self.video.clicked.connect(self.toggle_pause)
        self.video.double_clicked.connect(lambda: (self.toggle_pause(), self.toggle_fullscreen()))
        self.video.wheel.connect(lambda d: self._change_volume(5 * d))
        self.video.mouse_moved.connect(self._mouse_activity)
        # Fullscreen playback: hide the cursor after a moment without movement.
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(CURSOR_HIDE_MS)
        self._cursor_timer.timeout.connect(self._hide_cursor)

        self.panel = QWidget()
        self.panel_hint = QLabel("click / A  hear line   ↑↓ choose   R replay with video   M mark   E English"
                                 "   ·   hover a word to look it up")
        self.panel_hint.setStyleSheet(f"color: {theme.MUTED}; padding: 8px 12px; background: {theme.BLUSH};"
                                      f" border-bottom: 1px solid {theme.LINE};")
        self.browser = QTextBrowser()
        self.browser.setFocusPolicy(Qt.NoFocus)
        self.browser.setOpenLinks(False)
        self.browser.setStyleSheet(f"QTextBrowser {{ background: {theme.WHITE}; border: none; padding: 10px; }}")
        self.browser.setMouseTracking(True)
        from .hover import HoverLookup
        self.hover = HoverLookup(self.browser, accept=lambda b: self.line_at_block(b)[1] == KIND_JA)
        # Hear a line again (audio only) by clicking it: the video stays put.
        from .audio_replay import AudioReplay
        self.audio = AudioReplay(self)
        self._hearing_key: int | None = None
        self.audio.started.connect(self._audio_started)
        self.audio.finished.connect(self._audio_finished)
        self.browser.viewport().installEventFilter(self)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        pl.addWidget(self.panel_hint)
        pl.addWidget(self.browser, 1)
        self.panel.setStyleSheet(f"background: {theme.WHITE};")
        self.panel.hide()

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.video)
        self.splitter.addWidget(self.panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setChildrenCollapsible(False)

        self.empty = PetalCanvas(seed=11)
        logo = QLabel()
        logo.setPixmap(theme.logo_pixmap(96))
        logo.setAlignment(Qt.AlignCenter)
        name = QLabel("Hatsumimi  <span style='color:%s'>初耳</span>" % theme.SAKURA)
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(f"font-size: 26px; color: {theme.DEEP}; background: transparent;")
        hint = QLabel("Open a video, drop one here,\nor double-click a file in the Prepare tab.")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"font-size: 14px; color: {theme.MUTED}; background: transparent;")
        el = QVBoxLayout(self.empty)
        el.addStretch()
        for w in (logo, name, hint):
            el.addWidget(w)
        el.addStretch()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.splitter)

        # Controls (none take focus: keys must reach this tab)
        def button(text, slot, checkable=False):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCheckable(checkable)
            b.clicked.connect(slot)
            return b

        self.open_btn = button("Open…", self.pick_file)
        self.play_btn = button("", self.toggle_pause)
        self.play_btn.setIcon(theme.icon("play"))
        self.play_btn.setFixedWidth(44)
        self.eng_btn = button("English", self.toggle_english, checkable=True)
        self.eng_btn.setChecked(True)
        self.full_btn = button("", self.toggle_fullscreen)
        self.full_btn.setIcon(theme.icon("fullscreen"))
        self.full_btn.setToolTip("Fullscreen (F)")
        self.full_btn.setFixedWidth(44)
        self.time_label = QLabel("--:-- / --:--")
        self.seek = JumpSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setFocusPolicy(Qt.NoFocus)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_slider_held", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self.volume = JumpSlider(Qt.Horizontal)
        self.volume.setRange(0, 130)
        self.volume.setValue(100)
        self.volume.setFixedWidth(100)
        self.volume.setFocusPolicy(Qt.NoFocus)
        self.volume.valueChanged.connect(lambda v: setattr(self.player, "volume", v))

        self.controls = QWidget()
        self.controls.setObjectName("controls")
        self.controls.setStyleSheet(f"#controls {{ background: {theme.WHITE}; border-top: 1px solid {theme.LINE}; }}")
        cl = QHBoxLayout(self.controls)
        cl.setContentsMargins(6, 4, 6, 4)
        for w in (self.open_btn, self.play_btn):
            cl.addWidget(w)
        cl.addWidget(self.seek, 1)
        cl.addWidget(self.time_label)
        cl.addWidget(self.eng_btn)
        vol_icon = QLabel()
        vol_icon.setPixmap(theme.icon("volume").pixmap(20, 20))
        cl.addWidget(vol_icon)
        cl.addWidget(self.volume)
        cl.addWidget(self.full_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.controls)

        self.pos_timer = QTimer(self)
        self.pos_timer.timeout.connect(self._update_position)
        self.pos_timer.start(250)
        self.replay_timer = QTimer(self)
        self.replay_timer.setInterval(30)
        self.replay_timer.timeout.connect(self._check_replay)

    # -- opening files -----------------------------------------------------------------

    def pick_file(self) -> None:
        if not self.player:
            return
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        f, _ = QFileDialog.getOpenFileName(self, "Open video", "", f"Videos ({exts})")
        if f:
            self.open_file(Path(f))

    def open_file(self, path: Path, then=None) -> None:
        """Load a video; `then` (if given) runs once it has loaded."""
        if not self.player:
            return
        self._after_load = then
        self.current_path = str(path)
        self.stack.setCurrentWidget(self.splitter)
        self.setFocus()
        self._stop_replay()

        def load():
            self.player.pause = False  # pause is global in mpv: don't inherit it from the last file
            self.player.loadfile(str(path))
        # mpv mustn't load before the render context exists (see MpvWidget).
        self.video.when_ready(load)

    def _with_file(self, path: Path, fn) -> None:
        """Run fn now if this video is already open, otherwise after loading it."""
        if self.current_path and Path(self.current_path) == Path(path) and self.player.duration:
            self.setFocus()
            fn()
        else:
            self.open_file(Path(path), then=fn)

    def play_from(self, path: Path, t: float) -> None:
        """Open a video and play from a little before time t."""
        def go():
            self._stop_replay()
            self.player.command("seek", str(max(0.0, t - 3)), "absolute+exact")
            self.player.pause = False
        self._with_file(path, go)

    def play_line(self, path: Path, start: float, stop: float) -> None:
        """Play just one line, then pause with the panel showing it."""
        def go():
            self.keep_key = round(start * 1000)
            self.replaying, self.replay_armed = True, False
            self.replay_end = stop + REPLAY_PADDING
            self._close_panel()
            self.player.command("seek", str(max(0.0, start - REPLAY_PADDING)), "absolute+exact")
            self.player.pause = False
        self._with_file(path, go)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.suffix.lower() in VIDEO_EXTS:
                self.open_file(p)
                return

    # -- mpv events (GUI thread) ---------------------------------------------------------

    def _on_file_loaded(self) -> None:
        self.history.clear()
        self.marked_keys.clear()
        self.keep_key = None
        self._close_panel()
        ja, en = choose_tracks(self.player.track_list or [])
        self.active = ja is not None
        if self.active:
            self.player.sid = ja
            self.player.secondary_sid = en if en is not None else "no"
            self.player.sub_visibility = False
            self.player.secondary_sub_visibility = False
            self.audio.preload(self.current_path)  # so the first "hear line" is instant
        else:  # an ordinary video: behave like a normal player
            self.player.secondary_sid = "no"
            self.player.sub_visibility = True
        title = self.player.media_title or Path(self.current_path or "").name
        self.window().setWindowTitle(f"{title} - Hatsumimi")
        if not self.active:
            self.window().statusBar().showMessage(
                "No Japanese subtitle track in this video: playing normally (prepare it first for the panel).", 8000)
        then, self._after_load = self._after_load, None
        if then:
            then()

    def _on_sub(self, lang: str, text, start, stop) -> None:
        if self.active:
            self.history.record(lang, text, start, stop)

    def _on_pause(self, paused: bool) -> None:
        self.play_btn.setIcon(theme.icon("play" if paused else "pause"))
        self._mouse_activity()  # paused: cursor back; playing: restart the hide timer
        if not paused:
            self.audio.stop()  # resuming the video ends any line being heard
        if not self.active:
            return
        if paused:
            if self.replaying:  # the replay ended, or was paused by hand part-way
                self._stop_replay()
            if not self.panel_open:
                self._open_panel()
        elif not self.replaying:
            self._close_panel()

    def _on_restart(self) -> None:
        """A seek finished."""
        if self.replaying:
            self.replay_armed = True
            self.replay_timer.start()
        elif self.panel_open and self.player.pause:
            self._open_panel(refresh=True)  # seeked while paused: show lines up to the new spot

    # -- the panel -------------------------------------------------------------------------

    def _open_panel(self, refresh: bool = False) -> None:
        now = self.player.time_pos or 0.0
        self.shown = self.history.recent(now, PANEL_LINES)
        self.selected = len(self.shown) - 1 if self.shown else None
        if self.keep_key is not None:
            for i, l in enumerate(self.shown):
                if l.key == self.keep_key:
                    self.selected = i
            self.keep_key = None
        if not self.panel_open:
            self.panel_open = True
            self.panel.show()
            total = max(self.splitter.width(), 1)
            self.splitter.setSizes([int(total * 0.58), int(total * 0.42)])
        self._render_panel()

    def _close_panel(self) -> None:
        if self.panel_open:
            self.panel_open = False
            self.hover.hide()
            self.panel.hide()

    def _render_panel(self) -> None:
        doc = self.browser.document()
        doc.clear()
        cur = QTextCursor(doc)

        ja_fmt = QTextCharFormat()
        ja_fmt.setFontFamilies(JA_FONTS)
        ja_fmt.setFontPointSize(20)
        en_fmt = QTextCharFormat()
        en_fmt.setFontPointSize(11)
        en_fmt.setForeground(QColor(theme.MUTED))
        sel_bg = QColor("#fde3ea")

        if not self.shown:
            f = QTextCharFormat(en_fmt)
            cur.insertText("No lines heard yet.", f)
            return

        for i, line in enumerate(self.shown):
            sel = i == self.selected
            bf = QTextBlockFormat()
            bf.setTopMargin(14 if i else 0)
            bf.setLeftMargin(6)
            if sel:
                bf.setBackground(sel_bg)
            if i:
                cur.insertBlock(bf)
            else:
                cur.setBlockFormat(bf)
            cur.block().setUserState(i * 10 + KIND_JA)

            colour = QColor(theme.DEEP) if sel else QColor(theme.TEXT)
            sound = bool(SOUND_ONLY_RE.match(line.text))
            for n, vis in enumerate(line.text.splitlines()):
                if n:
                    cur.insertText(" ")  # line break within the same block
                f = QTextCharFormat(ja_fmt)
                f.setForeground(QColor(theme.FAINT) if sound else colour)
                m = SPEAKER_RE.match(vis)
                if m and not sound:
                    dim = QTextCharFormat(f)
                    dim.setForeground(QColor(theme.FAINT))
                    cur.insertText(m.group(0), dim)
                    vis = vis[m.end():]
                cur.insertText(vis, f)
            if line.key in self.marked_keys:
                star = QTextCharFormat(ja_fmt)
                star.setForeground(QColor(theme.SAKURA))
                cur.insertText("  ★", star)
            if line.key == self._hearing_key:
                note = QTextCharFormat(ja_fmt)
                note.setForeground(QColor(theme.ACCENT))
                cur.insertText("  ♪", note)

            if self.english_on:
                en = self.history.english_for(line)
                if en:
                    ebf = QTextBlockFormat()
                    ebf.setLeftMargin(6)
                    ebf.setTopMargin(2)
                    if sel:
                        ebf.setBackground(sel_bg)
                    cur.insertBlock(ebf)
                    cur.block().setUserState(i * 10 + KIND_EN)
                    cur.insertText(en, en_fmt)

        # Keep the newest lines in view.
        QTimer.singleShot(0, lambda: self.browser.verticalScrollBar().setValue(
            self.browser.verticalScrollBar().maximum()))

    def line_at_block(self, block) -> tuple[Line | None, int]:
        """(line, kind) for a panel text block: used by the hover dictionary."""
        state = block.userState()
        if state < 0:
            return None, -1
        idx, kind = divmod(state, 10)
        return (self.shown[idx] if idx < len(self.shown) else None), kind

    # -- actions ---------------------------------------------------------------------------

    def toggle_pause(self) -> None:
        if self.player and self.current_path:
            self.player.pause = not self.player.pause

    def _move(self, delta: int) -> None:
        if self.panel_open and self.selected is not None:
            self.selected = max(0, min(len(self.shown) - 1, self.selected + delta))
            self._render_panel()

    # -- hearing a line again (audio only) -------------------------------------------------

    def hear(self) -> None:
        """Play the selected line's audio; the paused video doesn't move."""
        if not (self.panel_open and self.selected is not None and self.shown and self.current_path):
            return
        line = self.shown[self.selected]
        self.audio.play(self.current_path, line.key, line.start, line.stop, volume=self.player.volume)

    def _audio_started(self, key: int) -> None:
        self._hearing_key = key
        if self.panel_open:
            self._render_panel()

    def _audio_finished(self, key: int) -> None:
        if self._hearing_key == key:
            self._hearing_key = None
            if self.panel_open:
                self._render_panel()

    def eventFilter(self, obj, ev) -> bool:
        """Clicking a line in the panel selects it and plays its audio."""
        if (self.video is not None and obj is self.browser.viewport()
                and ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton):
            pos = ev.position().toPoint()
            doc = self.browser.document()
            offset = QPoint(self.browser.horizontalScrollBar().value(), self.browser.verticalScrollBar().value())
            hit = doc.documentLayout().hitTest(pos + offset, Qt.FuzzyHit)
            if hit >= 0:
                block = doc.findBlock(hit)
                rect = doc.documentLayout().blockBoundingRect(block)
                if rect.contains(QPointF(pos + offset)):
                    line, _kind = self.line_at_block(block)
                    if line is not None:
                        self.selected = next(i for i, l in enumerate(self.shown) if l.key == line.key)
                        self.hear()  # its "started" signal redraws the panel with the selection
                        self.setFocus()
                        return True
        return super().eventFilter(obj, ev)

    def replay(self) -> None:
        if not (self.panel_open and self.selected is not None and self.shown):
            return
        line = self.shown[self.selected]
        self.keep_key = line.key
        self.replaying, self.replay_armed = True, False
        self.replay_end = line.stop + REPLAY_PADDING
        self._close_panel()
        self.player.command("seek", str(max(0.0, line.start - REPLAY_PADDING)), "absolute+exact")
        self.player.pause = False

    def _check_replay(self) -> None:
        if not self.replaying:
            self.replay_timer.stop()
            return
        t = self.player.time_pos
        if self.replay_armed and t is not None and t >= self.replay_end:
            self.player.pause = True  # _on_pause ends the replay and reopens the panel

    def _stop_replay(self) -> None:
        self.replaying = self.replay_armed = False
        self.replay_timer.stop()

    def mark(self) -> None:
        if not (self.panel_open and self.selected is not None and self.shown):
            return
        line = self.shown[self.selected]
        if line.key in self.marked_keys:
            return
        append_mark(self.current_path or "", self.player.media_title, line.start, line.stop,
                    line.text, self.history.english_for(line))
        self.marked_keys.add(line.key)
        self._render_panel()
        self.marked.emit()
        self.window().statusBar().showMessage("Marked as missed.", 3000)

    def toggle_english(self) -> None:
        self.english_on = not self.english_on
        self.eng_btn.setChecked(self.english_on)
        if self.panel_open:
            self._render_panel()

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not self.fullscreen)

    def set_fullscreen(self, on: bool) -> None:
        if not self.player or on == self.fullscreen:
            return
        self.fullscreen = on
        lay = self.layout()
        if on:
            # In fullscreen the control bar floats over the bottom of the
            # picture (shown on mouse move) instead of taking up space, so the
            # video never resizes when it appears.
            lay.removeWidget(self.controls)
            self.controls.hide()
        else:
            lay.addWidget(self.controls)
            self.controls.show()
        self.fullscreen_requested.emit(on)
        self.setFocus()
        self._mouse_activity()

    # -- fullscreen: cursor + floating controls -------------------------------------------

    def _playing(self) -> bool:
        return bool(self.player and self.current_path and not self.player.pause)

    def _place_controls(self) -> None:
        h = self.controls.sizeHint().height()
        self.controls.setGeometry(0, self.height() - h, self.width(), h)
        self.controls.raise_()

    def _mouse_activity(self) -> None:
        """Mouse moved (or play state changed): show the cursor, and in
        fullscreen the floating controls; hide both again after a moment if
        the video is playing."""
        self.video.unsetCursor()
        if self.fullscreen:
            self._place_controls()
            self.controls.show()
        if self.fullscreen and self._playing():
            self._cursor_timer.start()
        else:
            self._cursor_timer.stop()

    def _hide_cursor(self) -> None:
        if not (self.fullscreen and self._playing()):
            return
        if self.controls.rect().contains(self.controls.mapFromGlobal(QCursor.pos())):
            self._cursor_timer.start()  # pointer is on the controls: check again later
            return
        self.video.setCursor(Qt.BlankCursor)
        self.controls.hide()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.fullscreen and self.controls.isVisible():
            self._place_controls()

    def _seek_relative(self, secs: float) -> None:
        if self.player and self.current_path:
            self.player.command("seek", str(secs), "relative+exact")

    def _seek_released(self) -> None:
        self._slider_held = False
        dur = self.player.duration if self.player else None
        if dur:
            self.player.command("seek", str(dur * self.seek.value() / 1000), "absolute+exact")

    def _change_volume(self, delta: int) -> None:
        self.volume.setValue(max(0, min(130, self.volume.value() + delta)))

    def _update_position(self) -> None:
        if not self.player or not self.current_path:
            return
        t, dur = self.player.time_pos, self.player.duration
        self.time_label.setText(f"{_fmt_time(t)} / {_fmt_time(dur)}")
        if dur and t is not None and not self._slider_held:
            self.seek.blockSignals(True)
            self.seek.setValue(int(1000 * t / dur))
            self.seek.blockSignals(False)

    # -- keyboard -----------------------------------------------------------------------------

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if not self.player:
            return super().keyPressEvent(e)
        if k == Qt.Key_Space:
            self.toggle_pause()
        elif k == Qt.Key_Left:
            self._seek_relative(-5)
        elif k == Qt.Key_Right:
            self._seek_relative(5)
        elif k == Qt.Key_Up:
            self._move(-1)
        elif k == Qt.Key_Down:
            self._move(1)
        elif k == Qt.Key_A:
            self.hear()
        elif k == Qt.Key_R:
            self.replay()
        elif k == Qt.Key_M:
            self.mark()
        elif k == Qt.Key_E:
            self.toggle_english()
        elif k == Qt.Key_F:
            self.toggle_fullscreen()
        elif k == Qt.Key_Escape:
            self.set_fullscreen(False)
        else:
            super().keyPressEvent(e)

    # -- shutdown ----------------------------------------------------------------------------

    def shutdown(self) -> None:
        if not self.player:
            return
        self._closing = True
        self.pos_timer.stop()
        self.replay_timer.stop()
        for name, cb in self._observers:
            try:
                self.player.unobserve_property(name, cb)
            except Exception:
                pass
        self.audio.close()         # the background audio-only player
        self.video.free()          # render context first...
        self.player.terminate()    # ...then the main player
        self.player = None
        self._log.close()
