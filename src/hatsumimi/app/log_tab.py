"""The Missed lines tab: every line marked with M, from the app and the mpv script."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ..identify import identify
from ..marks import MarkedLine, delete_marks, load_marks, restore_marks
from . import theme

JA_FONTS = ["Yu Gothic UI", "Noto Sans CJK JP", "Noto Sans JP", "Meiryo", "sans-serif"]
COL_DATE, COL_SHOW, COL_AT, COL_JA, COL_EN = range(5)


@lru_cache(maxsize=512)
def show_label(video: str, title: str) -> str:
    """'Kusuriya no Hitorigoto 54' from the filename, else the stored title."""
    name = Path(video).name if video else ""
    info = identify(Path(name)) if name else None
    if info:
        if info.is_movie:
            return info.title
        if info.episode is not None:
            return f"{info.title} {info.episode}"
    return title or name or "?"


def fmt_clock(t: float) -> str:
    t = int(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def fmt_date(iso: str) -> str:
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%d %b %Y %H:%M")
    except ValueError:
        return iso


class _SortItem(QTableWidgetItem):
    """Sorts by a hidden key (e.g. ISO date / seconds) instead of display text."""

    def __init__(self, text: str, key):
        super().__init__(text)
        self._key = key

    def __lt__(self, other):
        if isinstance(other, _SortItem):
            return self._key < other._key
        return super().__lt__(other)


class LogTab(QWidget):
    play_line_requested = Signal(object, float, float)  # Path, start, stop
    play_from_requested = Signal(object, float)          # Path, time

    def __init__(self, parent=None):
        super().__init__(parent)
        self.marks: list[MarkedLine] = []
        self._last_deleted: list[MarkedLine] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Japanese, English or show…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.count = QLabel()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.count)
        top.addWidget(refresh)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Marked", "Show", "At", "Japanese", "English"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        h = self.table.horizontalHeader()
        for col, mode in ((COL_DATE, QHeaderView.ResizeToContents), (COL_SHOW, QHeaderView.Interactive),
                          (COL_AT, QHeaderView.ResizeToContents), (COL_JA, QHeaderView.Interactive),
                          (COL_EN, QHeaderView.Stretch)):
            h.setSectionResizeMode(col, mode)
        self.table.setColumnWidth(COL_SHOW, 220)
        self.table.setColumnWidth(COL_JA, 380)
        ja_font = QFont()
        ja_font.setFamilies(JA_FONTS)
        self._ja_font = ja_font
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.cellDoubleClicked.connect(lambda *_: self.play_line())

        # Detail area for the selected line
        self.ja_view = QTextBrowser()   # a text browser so phase 4's hover lookup can attach
        self.ja_view.setOpenLinks(False)
        self.ja_view.setMouseTracking(True)
        self.ja_view.setMinimumHeight(90)
        self.ja_view.setStyleSheet(f"QTextBrowser {{ background: {theme.BLUSH}; border: 1px solid {theme.LINE};"
                                   f" border-radius: 10px; padding: 12px; }}")
        from .hover import HoverLookup
        self.hover = HoverLookup(self.ja_view)
        self.en_label = QLabel()
        self.en_label.setWordWrap(True)
        self.en_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 14px; padding: 4px 10px;")
        self.en_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.file_label = QLabel()
        self.file_label.setStyleSheet(f"color: {theme.FAINT}; padding: 2px 10px;")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.play_line_btn = QPushButton("▶ Play line")
        self.play_line_btn.setToolTip("Play just this line, then pause (Enter / double-click)")
        self.play_line_btn.clicked.connect(self.play_line)
        self.play_from_btn = QPushButton("Play from here")
        self.play_from_btn.clicked.connect(self.play_from)
        self.copy_btn = QPushButton("Copy Japanese")
        self.copy_btn.clicked.connect(self.copy_japanese)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setToolTip("Remove the selected line(s) from the log (Del; Ctrl+Z undoes)")
        self.delete_btn.clicked.connect(self.delete_selected)
        buttons = QHBoxLayout()
        for b in (self.play_line_btn, self.play_from_btn, self.copy_btn):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(self.delete_btn)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 6, 0, 0)
        dl.addWidget(self.ja_view, 1)
        dl.addWidget(self.en_label)
        dl.addWidget(self.file_label)
        dl.addLayout(buttons)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)

        QShortcut(QKeySequence(Qt.Key_Delete), self.table, self.delete_selected)
        QShortcut(QKeySequence.Undo, self, self.undo_delete)  # Ctrl+Z
        QShortcut(QKeySequence(Qt.Key_Return), self.table, self.play_line)
        QShortcut(QKeySequence(Qt.Key_Enter), self.table, self.play_line)

        self.reload()

    # -- data ------------------------------------------------------------------------

    def reload(self) -> None:
        selected = {m.raw for m in self._selected()}
        self.marks = load_marks()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.marks))
        for row, m in enumerate(self.marks):
            date = _SortItem(fmt_date(m.marked_at), m.marked_at)
            date.setData(Qt.UserRole, row)
            show = QTableWidgetItem(show_label(m.video, m.title))
            show.setToolTip(m.video)
            at = _SortItem(fmt_clock(m.start), (show.text(), m.start))
            ja = QTableWidgetItem(" ".join(m.ja.splitlines()))
            ja.setFont(self._ja_font)
            ja.setToolTip(m.ja)
            en = QTableWidgetItem(m.en)
            en.setToolTip(m.en)
            if not m.video_exists:
                for it in (date, show, at, ja, en):
                    it.setForeground(QColor(theme.FAINT))
                show.setToolTip(f"{m.video}\n(file not found - moved or deleted?)")
            for col, it in enumerate((date, show, at, ja, en)):
                self.table.setItem(row, col, it)
        self.table.setSortingEnabled(True)
        self._apply_filter()
        # Keep the same rows selected across a refresh.
        for row in range(self.table.rowCount()):
            if self._mark_at(row).raw in selected:
                self.table.selectRow(row)
        if not self._selected() and self.table.rowCount():
            self.table.selectRow(0)
        self._show_detail()

    def _mark_at(self, row: int) -> MarkedLine:
        return self.marks[self.table.item(row, COL_DATE).data(Qt.UserRole)]

    def _selected(self) -> list[MarkedLine]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        return [self._mark_at(r) for r in rows]

    def _apply_filter(self) -> None:
        q = self.search.text().strip().casefold()
        shown = 0
        for row in range(self.table.rowCount()):
            m = self._mark_at(row)
            hay = f"{m.ja}\n{m.en}\n{self.table.item(row, COL_SHOW).text()}".casefold()
            hide = bool(q) and q not in hay
            self.table.setRowHidden(row, hide)
            shown += not hide
        total = self.table.rowCount()
        self.count.setText(f"{shown} of {total} lines" if q else f"{total} lines")

    # -- detail + actions -------------------------------------------------------------

    def _current(self) -> MarkedLine | None:
        sel = self._selected()
        return sel[0] if sel else None

    def _show_detail(self) -> None:
        m = self._current()
        has = m is not None
        playable = has and m.video_exists
        for b in (self.play_line_btn, self.play_from_btn):
            b.setEnabled(playable)
        self.copy_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)
        if not has:
            self.ja_view.clear()
            self.en_label.clear()
            self.file_label.setText("No marked lines yet. Press M on a line while paused in the player.")
            return
        self.ja_view.clear()
        f = self.ja_view.currentCharFormat()
        f.setFontFamilies(JA_FONTS)
        f.setFontPointSize(22)
        f.setForeground(QColor(theme.TEXT))
        self.ja_view.setCurrentCharFormat(f)
        self.ja_view.insertPlainText(m.ja)
        self.en_label.setText(m.en or "(no English)")
        where = f"{show_label(m.video, m.title)} at {fmt_clock(m.start)}  ·  {m.video}"
        if not m.video_exists:
            where += "   (file not found - moved or deleted?)"
        self.file_label.setText(where)

    def play_line(self) -> None:
        m = self._current()
        if m and m.video_exists:
            self.play_line_requested.emit(Path(m.video), m.start, m.stop)

    def play_from(self) -> None:
        m = self._current()
        if m and m.video_exists:
            self.play_from_requested.emit(Path(m.video), m.start)

    def copy_japanese(self) -> None:
        sel = self._selected()
        if sel:
            QGuiApplication.clipboard().setText("\n".join(" ".join(m.ja.splitlines()) for m in sel))

    def delete_selected(self) -> None:
        sel = self._selected()
        if not sel:
            return
        row = min(self.table.currentRow(), self.table.rowCount() - 1)
        delete_marks(sel)
        self._last_deleted = sel
        self.reload()
        # Keep the selection near where it was, so Del can be pressed repeatedly.
        if self.table.rowCount():
            self.table.selectRow(max(0, min(row, self.table.rowCount() - 1)))
        n = len(sel)
        self.window().statusBar().showMessage(
            f"Deleted {n} line{'s' if n > 1 else ''}. Press Ctrl+Z to undo.", 8000)

    def undo_delete(self) -> None:
        if not self._last_deleted:
            return
        restore_marks(self._last_deleted)
        n = len(self._last_deleted)
        self._last_deleted = []
        self.reload()
        self.window().statusBar().showMessage(f"Restored {n} line{'s' if n > 1 else ''}.", 4000)
