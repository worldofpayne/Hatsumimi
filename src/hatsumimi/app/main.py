"""Hatsumimi main window."""

from __future__ import annotations

import locale
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMainWindow, QTabWidget, QWidget

from .. import __version__
from ..config import find_api_key, load_config, save_config
from . import runtime, theme
from .dialogs import EntryChoiceDialog, SettingsDialog
from .log_tab import LogTab
from .player_tab import PlayerTab
from .prepare_tab import PrepareTab
from .worker import PrepareWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hatsumimi")
        self.resize(1100, 680)
        self.cfg = load_config()
        self.worker: PrepareWorker | None = None

        self.tabs = QTabWidget()
        self.prepare = PrepareTab(self.cfg)
        self.prepare.options_changed.connect(self._save_options)
        self.prepare.open_video.connect(self.play)
        self.tabs.addTab(self.prepare, "Prepare")
        self.player = PlayerTab()
        self.player.fullscreen_requested.connect(self._set_fullscreen)
        self.tabs.addTab(self.player, "Player")
        self.log = LogTab()
        self.log.play_line_requested.connect(self._play_line)
        self.log.play_from_requested.connect(self._play_from)
        self.player.marked.connect(self.log.reload)
        self.tabs.addTab(self.log, "Missed lines")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.setCornerWidget(self._brand(), Qt.TopRightCorner)
        self.setCentralWidget(self.tabs)
        self._was_maximized = False
        if sys.platform == "win32":
            from .winfullscreen import BorderlessFullscreen
            self._fs = BorderlessFullscreen()

        settings = QAction("Settings…", self)
        settings.triggered.connect(self.open_settings)
        self.menuBar().addAction(settings)
        self.statusBar().showMessage(f"Hatsumimi {__version__}")

        self._start_worker()

    @staticmethod
    def _brand() -> QWidget:
        """Butterfly + name, at the right end of the tab bar."""
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 2, 12, 2)
        lay.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(theme.logo_pixmap(28))
        name = QLabel(f"Hatsumimi <span style='color:{theme.SAKURA}'>初耳</span>")
        name.setStyleSheet(f"font-size: 16px; color: {theme.DEEP}; font-weight: 600;")
        lay.addWidget(logo)
        lay.addWidget(name)
        return w

    # -- player ---------------------------------------------------------------------

    def play(self, path) -> None:
        self.tabs.setCurrentWidget(self.player)
        self.player.open_file(path)

    def _play_line(self, path, start: float, stop: float) -> None:
        self.tabs.setCurrentWidget(self.player)
        self.player.play_line(path, start, stop)

    def _play_from(self, path, t: float) -> None:
        self.tabs.setCurrentWidget(self.player)
        self.player.play_from(path, t)

    def _tab_changed(self, _idx: int) -> None:
        if self.tabs.currentWidget() is self.log:
            self.log.reload()  # picks up lines marked in standalone mpv too
        if self.tabs.currentWidget() is self.player:
            self.player.setFocus()
        elif self.player.player and self.player.current_path and not self.player.player.pause:
            self.player.player.pause = True  # don't keep playing behind another tab

    def _set_fullscreen(self, on: bool) -> None:
        # Batch all the layout changes into one repaint instead of several.
        self.setUpdatesEnabled(False)
        try:
            self.menuBar().setVisible(not on)
            self.statusBar().setVisible(not on)
            self.tabs.tabBar().setVisible(not on)
            self.tabs.setDocumentMode(on)  # drops the tab frame's border
            # The theme gives the tab area a 1px top border; in fullscreen that
            # showed as a white line along the top of the screen.
            self.tabs.setStyleSheet("QTabWidget::pane { border: none; }" if on else "")
            if sys.platform == "win32":
                # Borderless fullscreen (see winfullscreen.py): instant, no black flash.
                if on:
                    self._fs.enter(int(self.winId()))
                else:
                    self._fs.leave(int(self.winId()))
            elif on:
                self._was_maximized = self.isMaximized()
                self.showFullScreen()
            elif self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        finally:
            self.setUpdatesEnabled(True)

    # -- worker lifecycle -------------------------------------------------------

    def _start_worker(self) -> None:
        key = find_api_key()
        if not key:
            self.statusBar().showMessage("No Jimaku API key yet: open Settings to add one.")
            return
        self.worker = PrepareWorker(key, self)
        self.worker.ask_entry.connect(self._ask_entry)
        self.prepare.attach_worker(self.worker)
        self.worker.start()

    def _stop_worker(self) -> None:
        if self.worker:
            self.worker.shutdown()
            self.worker.wait(5000)
            self.worker = None

    def _ask_entry(self, info, candidates) -> None:
        dlg = EntryChoiceDialog(info, candidates, self)
        chosen = dlg.chosen() if dlg.exec() else None
        self.worker.answer(chosen)

    # -- settings -------------------------------------------------------------------

    def _save_options(self, opts: dict) -> None:
        self.cfg.update(opts)
        save_config(self.cfg)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if not dlg.exec():
            return
        old_key = self.cfg.get("jimaku_api_key")
        self.cfg = dlg.result_config()
        save_config(self.cfg)
        self.prepare.sync.setChecked(self.cfg["sync"])
        self.prepare.mux.setChecked(self.cfg["mux"])
        if self.cfg["jimaku_api_key"] != old_key or self.worker is None:
            self._stop_worker()
            self._start_worker()
            if self.worker:
                self.statusBar().showMessage("Settings saved.")

    def closeEvent(self, e) -> None:
        self.player.shutdown()
        self._stop_worker()
        super().closeEvent(e)


def main() -> int:
    runtime.prepare()
    args = sys.argv[1:]
    if args[:1] == ["--self-test"]:
        import locale as _locale
        _locale.setlocale(_locale.LC_NUMERIC, "C")
        from .selftest import run
        return run(args[1] if len(args) > 1 else "hatsumimi-selftest.txt")

    app = QApplication(sys.argv)
    app.setApplicationName("Hatsumimi")
    app.setDesktopFileName("hatsumimi")  # Wayland app id: matches hatsumimi.desktop (icon in the dock/launcher)
    # Qt switches to the system locale; libmpv refuses to start unless numbers
    # use the C locale (matters on Linux, harmless on Windows).
    locale.setlocale(locale.LC_NUMERIC, "C")
    theme.apply(app)
    win = MainWindow()
    win.show()

    # `Hatsumimi video.mkv` (also what "Open with" passes): play it straight away.
    from pathlib import Path
    from ..identify import VIDEO_EXTS
    videos = [Path(a) for a in args if Path(a).suffix.lower() in VIDEO_EXTS and Path(a).exists()]
    if videos:
        win.play(videos[0])
    elif win.worker is None:
        win.open_settings()

    _developer_autoshot(app, win)
    code = app.exec()
    # Everything is closed by now (players terminated, worker stopped, files
    # written). With two libmpv instances in one process (video + audio
    # replay), libmpv's library teardown at interpreter exit can crash, so
    # end the process directly instead of unloading libraries one by one.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    import os
    os._exit(code)


def _developer_autoshot(app, win) -> None:
    """HATSUMIMI_AUTOSHOT="out.png;seconds[;pause]" - save a picture of the
    window after N seconds (pausing first if asked) and quit. Used to test
    packaged builds, which can't run test scripts."""
    import os
    spec = os.environ.get("HATSUMIMI_AUTOSHOT")
    if not spec:
        return
    from PySide6.QtCore import QTimer
    parts = spec.split(";")
    out, secs = parts[0], float(parts[1]) if len(parts) > 1 else 5
    if len(parts) > 2 and parts[2] == "pause" and win.player.player:
        QTimer.singleShot(int(secs * 1000) - 1200, lambda: setattr(win.player.player, "pause", True))
    QTimer.singleShot(int(secs * 1000), lambda: (win.grab().save(out), win.close(), app.quit()))


if __name__ == "__main__":
    raise SystemExit(main())
