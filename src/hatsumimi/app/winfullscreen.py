"""Borderless fullscreen on Windows, done the way browsers and mpv do it.

Why not Qt's showFullScreen(): a window that exactly covers the screen with
an OpenGL surface makes Windows switch to exclusive fullscreen, blanking the
screen and dropping frames on every toggle. Instead this:

  enter: saves the exact window placement (incl. maximized) and style, removes
         the title bar / resize edges, and covers the monitor plus one pixel
         (so it's never an exact match);
  leave: puts the saved style and placement back exactly.

Qt itself never enters its fullscreen state, so nothing can get out of sync
(changing the window behind Qt's fullscreen state left the title bar missing).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.MonitorFromWindow.restype = wintypes.HMONITOR

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
MONITOR_DEFAULTTONEAREST = 2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_NOOWNERZORDER = 0x0010, 0x0020, 0x0200


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


class BorderlessFullscreen:
    def __init__(self):
        self._style: int | None = None
        self._placement: WINDOWPLACEMENT | None = None

    @property
    def active(self) -> bool:
        return self._style is not None

    def enter(self, hwnd: int) -> None:
        if self.active:
            return
        h = wintypes.HWND(hwnd)
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        _user32.GetWindowPlacement(h, ctypes.byref(placement))
        style = _user32.GetWindowLongPtrW(h, GWL_STYLE)
        self._style, self._placement = style, placement

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        _user32.GetMonitorInfoW(_user32.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
        r = info.rcMonitor
        _user32.SetWindowLongPtrW(h, GWL_STYLE, style & ~(WS_CAPTION | WS_THICKFRAME))
        _user32.SetWindowPos(h, None, r.left, r.top, r.right - r.left, r.bottom - r.top + 1,
                             SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED)

    def leave(self, hwnd: int) -> None:
        if not self.active:
            return
        h = wintypes.HWND(hwnd)
        _user32.SetWindowLongPtrW(h, GWL_STYLE, self._style)
        _user32.SetWindowPlacement(h, ctypes.byref(self._placement))
        _user32.SetWindowPos(h, None, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED)
        self._style = self._placement = None
