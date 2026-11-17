"""mpv drawn into a Qt OpenGL widget via libmpv's render API.

The render API (rather than handing mpv a native window id) is what works on
both Windows and Wayland, where embedding foreign windows isn't possible.
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .libmpv import mpv


class MpvWidget(QOpenGLWidget):
    frame_ready = Signal()  # emitted from mpv's thread; drawn on the GUI thread
    gl_ready = Signal()
    clicked = Signal()
    double_clicked = Signal()
    wheel = Signal(int)     # +1 / -1 notches
    mouse_moved = Signal()

    def __init__(self, player: "mpv.MPV", parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.NoFocus)  # keys go to the player tab, not here
        self.setMouseTracking(True)      # movement without a button held (cursor auto-hide)
        self.player = player
        self.ctx = None
        self._when_ready: list = []
        # Keep the callback object alive: mpv holds only a raw pointer to it.
        self._get_proc = mpv.MpvGlGetProcAddressFn(self._get_proc_address)
        self.frame_ready.connect(self.update)

    @staticmethod
    def _get_proc_address(_ctx, name):
        glctx = QOpenGLContext.currentContext()
        if glctx is None:
            return 0
        addr = glctx.getProcAddress(name)
        return int(addr) if addr else 0

    def initializeGL(self) -> None:
        self.ctx = mpv.MpvRenderContext(
            self.player, "opengl",
            opengl_init_params={"get_proc_address": self._get_proc},
        )
        self.ctx.update_cb = self.frame_ready.emit
        self.context().aboutToBeDestroyed.connect(self.free)
        self.gl_ready.emit()
        for fn in self._when_ready:
            fn()
        self._when_ready.clear()

    def when_ready(self, fn) -> None:
        """Run fn once the render context exists (now, if it already does).

        mpv must not load a file before then, or it disables the video track.
        """
        if self.ctx is not None:
            fn()
        else:
            self._when_ready.append(fn)

    def paintGL(self) -> None:
        if self.ctx is None:
            return
        ratio = self.devicePixelRatioF()  # device pixels, for scaled (4K) displays
        self.ctx.render(flip_y=True, opengl_fbo={
            "w": int(self.width() * ratio),
            "h": int(self.height() * ratio),
            "fbo": self.defaultFramebufferObject(),
        })

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, e) -> None:
        # Qt sends the second press of a double-click as this event (not as
        # another mousePressEvent), so a double-click = one "clicked" + this.
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit()

    def mouseMoveEvent(self, e) -> None:
        self.mouse_moved.emit()

    def wheelEvent(self, e) -> None:
        dy = e.angleDelta().y()
        if dy:
            self.wheel.emit(1 if dy > 0 else -1)

    def free(self) -> None:
        """Release the render context (must happen before player.terminate)."""
        if self.ctx is not None:
            self.makeCurrent()
            self.ctx.free()
            self.ctx = None
            self.doneCurrent()


def make_player(log_handler=None) -> "mpv.MPV":
    """An mpv instance isolated from the user's own mpv setup.

    config=no (the default for libmpv) matters: the user's installed
    hatsumimi.lua must not load here and fight the app's own panel.
    """
    return mpv.MPV(
        vo="libmpv",
        input_default_bindings=False,
        input_vo_keyboard=False,
        keep_open="yes",
        osc=False,
        log_handler=log_handler,
        loglevel="info",
    )


__all__ = ["MpvWidget", "make_player", "ctypes"]
