"""Rikaikun-style hover lookups on any QTextBrowser showing Japanese.

    HoverLookup(browser, accept=lambda block: True)

Move the mouse over Japanese text: the longest dictionary word starting at
that character is highlighted and a popup shows its readings and meanings,
plus details of the kanji under the mouse.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFrame, QLabel, QTextEdit, QVBoxLayout

from ..dictionary import lookup as dictmod
from . import theme

LINE_SEP = " "
POPUP_WIDTH = 460
MAX_WORDS = 5
MAX_SENSES = 4


def _e(s: str) -> str:
    return html.escape(s, quote=False)


class DictPopup(QFrame):
    """The lookup card, drawn as an overlay *inside* the main window.

    Not a separate top-level window: Windows hides other windows over a
    fullscreen video window, and Wayland doesn't let apps place top-level
    windows at a screen position. A child widget avoids both.
    """

    def __init__(self, window):
        super().__init__(window)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # never "steals" the hover
        self.setStyleSheet(f"DictPopup {{ background: {theme.WHITE}; border: 1.5px solid {theme.SAKURA};"
                           f" border-radius: 10px; }}"
                           f"QLabel {{ color: {theme.TEXT}; background: transparent; }}")
        self.label = QLabel()
        self.label.setTextFormat(Qt.RichText)
        self.label.setWordWrap(True)
        self.label.setFixedWidth(POPUP_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self.label)

    def show_html(self, body: str, global_at: QPoint) -> None:
        self.label.setText(body)
        self.adjustSize()
        area = self.parentWidget().rect()
        at = self.parentWidget().mapFromGlobal(global_at)
        x, y = at.x() + 16, at.y() + 20
        if x + self.width() > area.right() - 4:
            x = at.x() - self.width() - 16      # flip to the left of the cursor
        if y + self.height() > area.bottom() - 4:
            y = at.y() - self.height() - 12     # or above it
        self.move(max(4, min(x, area.right() - self.width() - 4)),
                  max(4, min(y, area.bottom() - self.height() - 4)))
        self.show()
        self.raise_()


def render_results(words, kanji, tags: dict[str, str]) -> str:
    t = theme
    parts = []
    for w in words[:MAX_WORDS]:
        head = "、".join(w.kanji[:3]) if w.kanji else "、".join(w.readings[:3])
        reading = f" <span style='color:{t.ACCENT}'>【{_e('、'.join(w.readings[:3]))}】</span>" if w.kanji else ""
        common = f" <span style='color:{t.ACCENT};font-size:11px'>✿ common</span>" if w.common else ""
        parts.append(f"<div style='font-size:20px'>{_e(head)}{reading}{common}</div>")
        if w.reasons:
            parts.append(f"<div style='color:{t.DEEP};font-size:11px'>{_e(w.matched)}: "
                         f"{_e(' · '.join(w.reasons))}</div>")
        senses = []
        last_pos = None
        for s in w.senses[:MAX_SENSES]:
            pos = ", ".join(s.pos)
            pos_html = (f"<span style='color:{t.MUTED};font-style:italic'>({_e(pos)})</span> "
                        if pos and pos != last_pos else "")
            last_pos = pos or last_pos
            misc = f" <span style='color:{t.FAINT}'>[{_e(', '.join(s.misc))}]</span>" if s.misc else ""
            senses.append(f"<li>{pos_html}{_e('; '.join(s.glosses))}{misc}</li>")
        more = len(w.senses) - MAX_SENSES
        if more > 0:
            senses.append(f"<li style='list-style:none;color:{t.FAINT}'>…{more} more</li>")
        parts.append(f"<ol style='margin-top:2px;margin-bottom:8px;margin-left:-18px'>{''.join(senses)}</ol>")
    if kanji:
        bits = []
        if kanji.strokes:
            bits.append(f"{kanji.strokes} strokes")
        if kanji.grade:
            bits.append(f"grade {kanji.grade}")
        if kanji.jlpt:
            bits.append(f"old JLPT {kanji.jlpt}")
        if kanji.freq:
            bits.append(f"#{kanji.freq} most used")
        parts.append(
            f"<table width='100%' cellspacing=0 cellpadding=8 style='background:{theme.BLUSH}'><tr>"
            f"<td width='64' align='center' style='font-size:40px;color:{theme.DEEP}'>{_e(kanji.char)}</td><td>"
            f"<div><span style='color:{theme.MUTED}'>On</span> {_e('、'.join(kanji.on)) or '–'}</div>"
            f"<div><span style='color:{theme.MUTED}'>Kun</span> {_e('、'.join(kanji.kun)) or '–'}</div>"
            f"<div>{_e(', '.join(kanji.meanings[:6]))}</div>"
            f"<div style='color:{theme.MUTED};font-size:11px'>{_e(' · '.join(bits))}</div>"
            "</td></tr></table>")
    return "".join(parts)


class HoverLookup(QObject):
    """Watches the mouse over a QTextBrowser and shows dictionary popups."""

    def __init__(self, browser: QTextEdit, accept=None, parent=None):
        super().__init__(parent or browser)
        self.browser = browser
        self.accept = accept or (lambda block: True)
        self._last = None          # (block number, position) last looked up
        self._pos = QPoint()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)  # small debounce: feels instant, avoids work mid-swipe
        self._timer.timeout.connect(self._update)
        browser.setMouseTracking(True)
        browser.viewport().setMouseTracking(True)
        browser.viewport().installEventFilter(self)
        browser.textChanged.connect(self.hide)
        browser.verticalScrollBar().valueChanged.connect(self.hide)

    def popup(self) -> DictPopup:
        """One popup per main window, shared by every browser in it."""
        win = self.browser.window()
        pop = getattr(win, "_hatsumimi_popup", None)
        if pop is None:
            pop = DictPopup(win)
            win._hatsumimi_popup = pop
        return pop

    def _existing_popup(self) -> DictPopup | None:
        return getattr(self.browser.window(), "_hatsumimi_popup", None)

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t == QEvent.MouseMove:
            self._pos = ev.position().toPoint()
            self._timer.start()
        elif t in (QEvent.Leave, QEvent.Hide, QEvent.Wheel, QEvent.MouseButtonPress):
            self.hide()
        return False

    def hide(self) -> None:
        self._timer.stop()
        self._last = None
        self.browser.setExtraSelections([])
        if (pop := self._existing_popup()) is not None:
            pop.hide()

    def _char_at(self, pos: QPoint) -> int:
        """Document position of the character under pos, or -1."""
        doc = self.browser.document()
        offset = QPoint(self.browser.horizontalScrollBar().value(), self.browser.verticalScrollBar().value())
        p = doc.documentLayout().hitTest(pos + offset, Qt.ExactHit)
        if p < 0:
            return -1
        # hitTest gives the nearest cursor position; if the mouse is past the
        # middle of a character that's the position *after* it, so check which
        # character's box actually contains the point.
        for cand in (p, p - 1):
            if cand < 0:
                continue
            c = QTextCursor(doc)
            c.setPosition(cand)
            left = self.browser.cursorRect(c)
            c.setPosition(cand + 1)
            right = self.browser.cursorRect(c)
            if not (left.top() <= pos.y() <= left.bottom()):
                continue
            if left.top() == right.top():
                end = right.left()
            else:  # last character on a visual line: assume a full-width glyph
                end = left.left() + left.height()
            if left.left() <= pos.x() < end:
                return cand
        return -1

    def _update(self) -> None:
        if not self.browser.isVisible():
            return self.hide()
        p = self._char_at(self._pos)
        if p < 0:
            return self.hide()
        doc = self.browser.document()
        block = doc.findBlock(p)
        if not self.accept(block):
            return self.hide()
        rel = p - block.position()
        text = block.text()[rel:].split(LINE_SEP, 1)[0]
        if not text or not dictmod.is_japanese(text[0]):
            return self.hide()
        if self._last == (block.blockNumber(), p):
            return
        self._last = (block.blockNumber(), p)

        d = dictmod.shared()
        if d is None:
            self.popup().show_html("<b>Dictionary not installed.</b><br>Open <i>Settings…</i> and choose "
                                   "<i>Download dictionary</i>.", QCursor.pos())
            return
        words = d.lookup(text)
        kanji = d.kanji(text[0]) if dictmod.is_kanji(text[0]) else None
        if not words and not kanji:
            return self.hide()

        # Highlight what the top result covers.
        length = words[0].length if words else 1
        sel = QTextEdit.ExtraSelection()
        cur = QTextCursor(doc)
        cur.setPosition(p)
        cur.setPosition(p + length, QTextCursor.KeepAnchor)
        sel.cursor = cur
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(theme.PETAL))
        sel.format = fmt
        self.browser.setExtraSelections([sel])

        self.popup().show_html(render_results(words, kanji, d.tags), QCursor.pos())
