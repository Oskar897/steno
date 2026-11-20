"""Custom window frame on Windows: the page draws its own title bar and window buttons.

The native window stays a normal sizable window (snap to screen edges and Snap layouts via
Win+Z, resizing at the edges, Win+arrow keys, minimize/maximize animations, the title in the
taskbar and Alt+Tab). Only the caption strip is removed, by handling ``WM_NCCALCSIZE`` so the
client area covers it. Inside the page, elements with ``app-region: drag`` act as the title bar
(drag to move or snap, double-click to maximize, right-click for the system menu); WebView2
does that once ``IsNonClientRegionSupportEnabled`` is on.

Everything here is best effort: if a step fails, the window keeps its normal title bar.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

_WM_NCCALCSIZE = 0x0083
_WM_SYSCOMMAND = 0x0112
_SC_MINIMIZE, _SC_MAXIMIZE, _SC_RESTORE, _SC_CLOSE = 0xF020, 0xF030, 0xF120, 0xF060
# SetWindowPos: FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER | NOACTIVATE
_SWP_FLAGS = 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010
_SM_CYFRAME, _SM_CXPADDEDBORDER = 33, 92
_GWL_STYLE, _WS_SYSMENU = -16, 0x00080000
_WM_WINDOWPOSCHANGING, _WM_WINDOWPOSCHANGED, _WM_SIZE = 0x0046, 0x0047, 0x0005
_WM_ENTERSIZEMOVE, _WM_EXITSIZEMOVE = 0x0231, 0x0232
_SWP_NOSIZE = 0x0001
_SIZE_RESTORED = 0
_RESTORE_GUARD_S = 0.5


class _WindowPos(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("insert_after", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int),
                ("flags", wintypes.UINT)]

_LRESULT = ctypes.c_ssize_t
_SUBCLASSPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
_keep: list = []  # the subclass callback must outlive the window


def _user32():
    u = ctypes.windll.user32
    u.IsZoomed.argtypes = [wintypes.HWND]
    u.GetDpiForWindow.argtypes = [wintypes.HWND]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    return u


def _frame_thickness(hwnd: int) -> int:
    """Height of the sizing frame that a maximized window pushes off the top of the screen."""
    u = _user32()
    try:
        dpi = u.GetDpiForWindow(hwnd)
        return (u.GetSystemMetricsForDpi(_SM_CYFRAME, dpi)
                + u.GetSystemMetricsForDpi(_SM_CXPADDEDBORDER, dpi))
    except Exception:
        return u.GetSystemMetrics(_SM_CYFRAME) + u.GetSystemMetrics(_SM_CXPADDEDBORDER)


def remove_caption(form) -> bool:
    """Drop the caption strip of a WinForms form, keeping its sizable frame. UI thread only."""
    if sys.platform != "win32":
        return False
    comctl = ctypes.windll.comctl32
    comctl.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM]
    comctl.DefSubclassProc.restype = _LRESULT
    comctl.SetWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t,
                                         ctypes.c_size_t]
    hwnd = int(str(form.Handle))
    user = _user32()
    user.IsIconic.argtypes = [wintypes.HWND]
    # WinForms re-applies its own idea of the size after a restore from minimized or maximized,
    # computed with the caption we removed: the window would grow by a caption each time. The
    # last normal size is kept here and wins for a moment after such a restore.
    state = {"last": _SIZE_RESTORED, "normal": None, "guard": 0.0, "sizing": False}

    def proc(h, msg, wparam, lparam, _uid, _ref):
        if msg == _WM_ENTERSIZEMOVE:
            state["sizing"] = True
        elif msg == _WM_EXITSIZEMOVE:
            state["sizing"] = False
        elif msg == _WM_SIZE:
            if wparam == _SIZE_RESTORED and state["last"] != _SIZE_RESTORED:
                state["guard"] = time.monotonic() + _RESTORE_GUARD_S
            state["last"] = wparam
        elif msg == _WM_WINDOWPOSCHANGING and state["normal"] and not state["sizing"]:
            pos = ctypes.cast(lparam, ctypes.POINTER(_WindowPos)).contents
            if (not pos.flags & _SWP_NOSIZE and time.monotonic() < state["guard"]
                    and not user.IsZoomed(h) and not user.IsIconic(h)):
                pos.cx, pos.cy = state["normal"]
        elif msg == _WM_WINDOWPOSCHANGED and not user.IsZoomed(h) and not user.IsIconic(h):
            pos = ctypes.cast(lparam, ctypes.POINTER(_WindowPos)).contents
            if not pos.flags & _SWP_NOSIZE and state["last"] == _SIZE_RESTORED:
                state["normal"] = (pos.cx, pos.cy)
        if msg == _WM_NCCALCSIZE and wparam:
            rect = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
            top = rect.top
            comctl.DefSubclassProc(h, msg, wparam, lparam)  # side and bottom frame as usual
            rect.top = top + (_frame_thickness(h) if user.IsZoomed(h) else 0)
            return 0
        return comctl.DefSubclassProc(h, msg, wparam, lparam)

    callback = _SUBCLASSPROC(proc)
    if not comctl.SetWindowSubclass(hwnd, callback, 1, 0):
        return False
    _keep.append(callback)
    # With the frame extended into the client area (the Acrylic backdrop needs that), DWM
    # would still draw faint native caption buttons behind the page's own: drop WS_SYSMENU.
    # Sizing, snapping and the minimize/maximize commands do not depend on it.
    user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    style = user.GetWindowLongPtrW(hwnd, _GWL_STYLE)
    user.SetWindowLongPtrW(hwnd, _GWL_STYLE, style & ~_WS_SYSMENU)
    user.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FLAGS)
    r = wintypes.RECT()
    if not user.IsZoomed(hwnd) and user.GetWindowRect(hwnd, ctypes.byref(r)):
        state["normal"] = (r.right - r.left, r.bottom - r.top)
    return True


def enable_drag_regions(form) -> bool:
    """Let CSS ``app-region: drag`` act as the title bar (WebView2 1.0.2420+). UI thread only;
    takes effect from the next navigation."""
    try:
        settings = form.browser.webview.CoreWebView2.Settings
        settings.IsNonClientRegionSupportEnabled = True
        return bool(settings.IsNonClientRegionSupportEnabled)
    except Exception:
        log.warning("WebView2 has no non-client region support", exc_info=True)
        return False


def window_command(hwnd: int, action: str) -> bool:
    """minimize | maximize (toggles restore) | close, through the system menu commands so the
    normal paths run (animations, and the close confirmation while recording)."""
    user = _user32()
    if action == "minimize":
        cmd = _SC_MINIMIZE
    elif action == "maximize":
        cmd = _SC_RESTORE if user.IsZoomed(hwnd) else _SC_MAXIMIZE
    elif action == "close":
        cmd = _SC_CLOSE
    else:
        return False
    return bool(user.PostMessageW(hwnd, _WM_SYSCOMMAND, cmd, 0))


def is_maximized(hwnd: int) -> bool:
    return bool(_user32().IsZoomed(hwnd))
