"""Hatsumimi look: white, with sakura-pink highlights and drifting petals."""

from __future__ import annotations

import math
import random
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPixmap,
)
from PySide6.QtWidgets import QApplication, QStyleFactory

ASSETS = Path(__file__).resolve().parent / "assets"
LOGO = ASSETS / "logo.svg"

# -- colours -------------------------------------------------------------------------
WHITE = "#ffffff"
BLUSH = "#fff6f9"        # faint pink tint for panels / alternate rows
PETAL = "#f8c3d1"        # light sakura
SAKURA = "#f29ab2"       # main brand pink
ACCENT = "#e06c8f"       # stronger pink: focus, selected text, progress
DEEP = "#c24d71"         # deep pink: emphasis on white
LINE = "#f0d6de"         # borders
TEXT = "#2b2629"
MUTED = "#8c7f85"
FAINT = "#b9adb2"
OK = "#3f9d63"
WARN = "#c98a00"
FAIL = "#cf3f4f"
JA_FONTS = ["Yu Gothic UI", "Noto Sans CJK JP", "Noto Sans JP", "Meiryo", "sans-serif"]


def logo_icon() -> QIcon:
    return QIcon(str(LOGO))


def icon(name: str) -> QIcon:
    """A small SVG icon from assets/ (play, pause, volume, fullscreen)."""
    return QIcon(str(ASSETS / f"{name}.svg"))


def logo_pixmap(size: int) -> QPixmap:
    return logo_icon().pixmap(size, size)


# -- application-wide style -------------------------------------------------------------

STYLESHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {WHITE}; }}
QToolTip {{ background: {WHITE}; color: {TEXT}; border: 1px solid {SAKURA}; padding: 4px; }}

QMenuBar {{ background: {WHITE}; border-bottom: 1px solid {LINE}; }}
QMenuBar::item {{ padding: 6px 12px; background: transparent; }}
QMenuBar::item:selected {{ background: {BLUSH}; color: {DEEP}; border-radius: 4px; }}
QStatusBar {{ background: {WHITE}; color: {MUTED}; border-top: 1px solid {LINE}; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {LINE}; background: {WHITE}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 18px; margin-right: 2px;
               border: none; border-bottom: 3px solid transparent; font-size: 14px; }}
QTabBar::tab:hover {{ color: {DEEP}; }}
QTabBar::tab:selected {{ color: {DEEP}; border-bottom: 3px solid {SAKURA}; font-weight: 600; }}
QTabBar::tab:disabled {{ color: {FAINT}; }}

QPushButton {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {SAKURA}; background: {BLUSH}; color: {DEEP}; }}
QPushButton:pressed {{ background: {PETAL}; }}
QPushButton:checked {{ background: {SAKURA}; border-color: {SAKURA}; color: {WHITE}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: #eee6e9; background: {WHITE}; }}
QPushButton:default {{ border-color: {SAKURA}; }}
QPushButton#primary {{ background: {SAKURA}; color: {WHITE}; border-color: {SAKURA}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT}; border-color: {ACCENT}; }}

QLineEdit {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 10px;
            selection-background-color: {PETAL}; selection-color: {TEXT}; }}
QLineEdit:focus {{ border-color: {SAKURA}; }}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1.5px solid {FAINT}; border-radius: 4px; background: {WHITE}; }}
QCheckBox::indicator:hover {{ border-color: {SAKURA}; }}
QCheckBox::indicator:checked {{ background: {SAKURA}; border-color: {SAKURA};
    image: url("{(ASSETS / 'check.svg').as_posix()}"); }}

QTableWidget, QListWidget {{ background: {WHITE}; alternate-background-color: {BLUSH};
    border: 1px solid {LINE}; border-radius: 8px; gridline-color: transparent;
    selection-background-color: #fde3ea; selection-color: {TEXT}; outline: none; }}
QTableWidget::item, QListWidget::item {{ padding: 4px 6px; }}
QHeaderView::section {{ background: {WHITE}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
    padding: 6px; font-weight: 600; }}

QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {SAKURA}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {WHITE}; border: 2px solid {SAKURA}; width: 12px; height: 12px;
    margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {PETAL}; }}

QProgressBar {{ border: 1px solid {LINE}; border-radius: 6px; background: {BLUSH}; text-align: center; }}
QProgressBar::chunk {{ background: {SAKURA}; border-radius: 5px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {PETAL}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {LINE}; border-radius: 4px; min-width: 30px; }}

QSplitter::handle {{ background: {LINE}; }}
QSplitter::handle:hover {{ background: {PETAL}; }}
"""


def apply(app: QApplication) -> None:
    """Fusion + an explicit light palette, so the app is white even when the
    OS is in dark mode, and looks the same on Windows and Linux."""
    app.setStyle(QStyleFactory.create("Fusion"))
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    pal = QPalette()
    for role, colour in [
        (QPalette.Window, WHITE), (QPalette.Base, WHITE), (QPalette.AlternateBase, BLUSH),
        (QPalette.Button, WHITE), (QPalette.Text, TEXT), (QPalette.WindowText, TEXT),
        (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, WHITE), (QPalette.ToolTipText, TEXT),
        (QPalette.Highlight, PETAL), (QPalette.HighlightedText, TEXT), (QPalette.Link, ACCENT),
        (QPalette.PlaceholderText, FAINT),
    ]:
        pal.setColor(role, QColor(colour))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FAINT))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(logo_icon())


# -- sakura petals -------------------------------------------------------------------------

def _petal_path() -> QPainterPath:
    """One sakura petal, 1 unit long, base at (0,0), notched tip at y=-1."""
    p = QPainterPath(QPointF(0, 0))
    p.cubicTo(QPointF(-0.30, -0.18), QPointF(-0.42, -0.62), QPointF(-0.22, -0.98))
    p.quadTo(QPointF(-0.10, -1.02), QPointF(0, -0.86))   # the notch
    p.quadTo(QPointF(0.10, -1.02), QPointF(0.22, -0.98))
    p.cubicTo(QPointF(0.42, -0.62), QPointF(0.30, -0.18), QPointF(0, 0))
    return p


_PETAL = _petal_path()


def paint_petals(painter: QPainter, rect: QRectF, seed: int = 7, count: int | None = None,
                 opacity: float = 1.0, size: float = 1.0) -> None:
    """Scatter soft petals over rect. The same seed gives the same layout, and
    positions are fractions of rect, so they stay put while resizing."""
    rng = random.Random(seed)
    area = rect.width() * rect.height()
    n = count if count is not None else max(6, min(60, int(area / 22000)))
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    base = min(rect.width(), rect.height())
    for _ in range(n):
        x = rect.left() + rng.random() * rect.width()
        y = rect.top() + rng.random() * rect.height()
        length = (14 + rng.random() * 22) * size * max(0.7, min(1.6, base / 400))
        angle = rng.random() * 360
        tilt = 0.55 + rng.random() * 0.45  # squash: petals seen at an angle
        alpha = (0.18 + rng.random() * 0.32) * opacity
        painter.save()
        painter.translate(x, y)
        painter.rotate(angle)
        painter.scale(length * tilt, length)
        grad = QLinearGradient(QPointF(0, 0), QPointF(0, -1))
        c1, c2 = QColor(SAKURA), QColor(PETAL)
        c1.setAlphaF(alpha)
        c2.setAlphaF(alpha * 0.7)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        painter.setBrush(grad)
        painter.drawPath(_PETAL)
        painter.restore()
    painter.restore()
