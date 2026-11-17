"""The Prepare tab: drop videos or folders, and they get Japanese subtitles."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..identify import VIDEO_EXTS, find_videos
from ..pipeline import Options
from . import theme

COL_FILE, COL_SHOW, COL_STATUS, COL_DETAIL = range(4)

STATUS_LOOK = {   # status -> (label, colour)
    "queued":   ("Queued", theme.MUTED),
    "ok":       ("✔ Done", theme.OK),
    "skipped":  ("– Skipped", theme.MUTED),
    "unsynced": ("⚠ Check", theme.WARN),
    "failed":   ("✖ Failed", theme.FAIL),
}


class DropZone(QFrame):
    """A soft card with drifting petals: drop videos or folders on it."""
    dropped = Signal(list)  # list[Path]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        self._hover = False
        logo = QLabel()
        logo.setPixmap(theme.logo_pixmap(56))
        logo.setAlignment(Qt.AlignCenter)
        title = QLabel("Drop videos or folders here")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 18px; color: {theme.DEEP}; background: transparent;")
        sub = QLabel("Japanese subtitles are found, re-timed and added for you")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
        lay = QVBoxLayout(self)
        lay.addStretch()
        for w in (logo, title, sub):
            lay.addWidget(w)
        lay.addStretch()

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, QColor("#fff9fb" if not self._hover else "#ffeef3"))
        p.setClipPath(path)
        theme.paint_petals(p, r, seed=3, opacity=1.3 if self._hover else 1.0)
        p.setClipping(False)
        pen = QPen(QColor(theme.ACCENT if self._hover else theme.PETAL), 2, Qt.DashLine)
        p.setPen(pen)
        p.drawPath(path)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, e) -> None:
        self._set_hover(False)

    def dropEvent(self, e: QDropEvent) -> None:
        self._set_hover(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped.emit(paths)
            e.acceptProposedAction()


class PrepareTab(QWidget):
    options_changed = Signal(dict)  # {"sync": bool, "mux": bool}
    open_video = Signal(object)     # Path: double-clicked a row

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.worker = None
        self._rows: dict[str, int] = {}   # video path -> table row
        self._active: set[str] = set()    # queued or running

        self.drop = DropZone()
        self.drop.dropped.connect(self.add_paths)

        add_files = QPushButton("Add videos…")
        add_files.clicked.connect(self._pick_files)
        add_folder = QPushButton("Add folder…")
        add_folder.clicked.connect(self._pick_folder)

        self.sync = QCheckBox("Re-time to video")
        self.sync.setChecked(bool(cfg.get("sync", True)))
        self.mux = QCheckBox("Embed into .mkv")
        self.mux.setChecked(bool(cfg.get("mux", True)))
        self.force = QCheckBox("Redo files that already have Japanese subs")
        for box in (self.sync, self.mux):
            box.toggled.connect(lambda _: self.options_changed.emit(
                {"sync": self.sync.isChecked(), "mux": self.mux.isChecked()}))

        self.stop_btn = QPushButton("Stop after current")
        self.stop_btn.clicked.connect(self._stop_pending)
        self.stop_btn.setEnabled(False)
        clear = QPushButton("Clear finished")
        clear.clicked.connect(self._clear_finished)

        top = QHBoxLayout()
        top.addWidget(add_files)
        top.addWidget(add_folder)
        top.addSpacing(16)
        top.addWidget(self.sync)
        top.addWidget(self.mux)
        top.addWidget(self.force)
        top.addStretch()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File", "Show", "Status", "Details"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_FILE, QHeaderView.Interactive)
        h.setSectionResizeMode(COL_SHOW, QHeaderView.Interactive)
        h.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(COL_DETAIL, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_FILE, 340)
        self.table.setColumnWidth(COL_SHOW, 220)
        self.table.cellDoubleClicked.connect(self._row_double_clicked)
        self.table.setToolTip("Double-click a file to play it")

        bottom = QHBoxLayout()
        self.summary = QLabel("")
        bottom.addWidget(self.summary)
        bottom.addStretch()
        bottom.addWidget(self.stop_btn)
        bottom.addWidget(clear)

        lay = QVBoxLayout(self)
        lay.addWidget(self.drop)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addLayout(bottom)

    # -- wiring to the worker --------------------------------------------------

    def attach_worker(self, worker) -> None:
        self.worker = worker
        worker.stage.connect(self._on_stage)
        worker.note.connect(self._on_note)
        worker.finished_one.connect(self._on_finished)
        worker.idle.connect(self._update_summary)

    def options(self) -> Options:
        return Options(force=self.force.isChecked(), sync=self.sync.isChecked(),
                       mux=self.mux.isChecked())

    # -- adding work -------------------------------------------------------------

    def _pick_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos", "", f"Videos ({exts})")
        if files:
            self.add_paths([Path(f) for f in files])

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a folder of videos")
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]) -> None:
        videos = find_videos(paths, recursive=True)
        if not videos:
            self.summary.setText("No video files in what was dropped.")
            return
        if self.worker is None:
            self.summary.setText("Set your Jimaku API key in Settings first.")
            return
        opts = self.options()
        added = 0
        for v in videos:
            key = str(v)
            if key in self._active:
                continue  # already queued or running
            row = self._rows.get(key)
            if row is None:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._rows[key] = row
            self._set(row, COL_FILE, v.name, tip=key)
            self._set(row, COL_SHOW, "")
            self._set(row, COL_DETAIL, "")
            self._set_status(row, "queued")
            self._active.add(key)
            self.worker.enqueue(v, opts)
            added += 1
        self.stop_btn.setEnabled(bool(self._active))
        self._update_summary()

    def _stop_pending(self) -> None:
        if not self.worker:
            return
        for v in self.worker.clear_pending():
            key = str(v)
            self._active.discard(key)
            row = self._rows[key]
            self._set_status(row, "skipped")
            self._set(row, COL_DETAIL, "stopped before it started")
        self._update_summary()

    def _clear_finished(self) -> None:
        for key, row in sorted(self._rows.items(), key=lambda kv: -kv[1]):
            if key not in self._active:
                self.table.removeRow(row)
        remaining = [k for k, _ in sorted(self._rows.items(), key=lambda kv: kv[1]) if k in self._active]
        self._rows = {k: i for i, k in enumerate(remaining)}
        self._update_summary()

    def _row_double_clicked(self, row: int, _col: int) -> None:
        for key, r in self._rows.items():
            if r == row and key not in self._active and Path(key).exists():
                self.open_video.emit(Path(key))
                return

    # -- worker signals -----------------------------------------------------------

    def _on_stage(self, path: str, text: str) -> None:
        if (row := self._rows.get(path)) is not None:
            self._set_status(row, text + "…", colour=theme.ACCENT)

    def _on_note(self, path: str, text: str) -> None:
        row = self._rows.get(path)
        if row is None:
            return
        if text.startswith("Jimaku: "):
            self._set(row, COL_SHOW, text[len("Jimaku: "):])
        elif not self.table.item(row, COL_SHOW) or not self.table.item(row, COL_SHOW).text():
            self._set(row, COL_SHOW, text)  # the guessed title until Jimaku confirms
        if text.startswith("Picked: "):
            self._set(row, COL_DETAIL, text)

    def _on_finished(self, path: str, status: str, detail: str) -> None:
        self._active.discard(path)
        if (row := self._rows.get(path)) is not None:
            self._set_status(row, status)
            self._set(row, COL_DETAIL, detail, tip=detail)
        self.stop_btn.setEnabled(bool(self._active))
        self._update_summary()

    # -- helpers --------------------------------------------------------------------

    def _set(self, row: int, col: int, text: str, tip: str | None = None) -> None:
        item = self.table.item(row, col) or QTableWidgetItem()
        item.setText(text)
        item.setToolTip(tip or text)
        self.table.setItem(row, col, item)

    def _set_status(self, row: int, status: str, colour: str | None = None) -> None:
        label, col = STATUS_LOOK.get(status, (status, colour or theme.ACCENT))
        self._set(row, COL_STATUS, label)
        self.table.item(row, COL_STATUS).setForeground(QColor(colour or col))

    def _update_summary(self) -> None:
        counts: dict[str, int] = {}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_STATUS)
            if item:
                counts[item.text()] = counts.get(item.text(), 0) + 1
        if not counts:
            self.summary.setText("")
            return
        busy = len(self._active)
        parts = [f"{n} {label.split(' ', 1)[-1].lower()}" for label, n in counts.items()
                 if not label.endswith("…") and label != "Queued"]
        prefix = f"Working on {busy} file(s). " if busy else ""
        self.summary.setText(prefix + ", ".join(parts))
