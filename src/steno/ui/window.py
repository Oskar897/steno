"""Desktop window for the UI (``steno app``): the local UI server inside a native window.

The page is the same as ``steno ui`` serves, shown by pywebview (WebView2 on Windows) instead
of a browser. On Windows 11 the window gets the system Acrylic backdrop (the desktop shows
through, blurred) and a dark title bar; elsewhere it is a plain dark window.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from steno.config import Config

log = logging.getLogger(__name__)

TITLE = "Steno"
_MUTEX_NAME = "Local\\steno-desktop-app"
_ERROR_ALREADY_EXISTS = 183
_mutex = None  # held for the life of the process

# DwmSetWindowAttribute ids (Windows 11)
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_TRANSIENTWINDOW = 3  # Acrylic
_DWMWCP_ROUND = 2


class _Margins(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class Bridge:
    """Functions the page may call as ``window.pywebview.api.<name>()``."""

    def __init__(self) -> None:
        self._window = None  # private: pywebview exposes public attributes to the page

    def pick_folder(self) -> str | None:
        import webview

        if self._window is None:
            return None
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        if kind is None:
            kind = webview.FOLDER_DIALOG
        result = self._window.create_file_dialog(kind)
        if not result:
            return None
        return str(result[0] if isinstance(result, list | tuple) else result)


def apply_glass(window) -> bool:
    """Windows 11 Acrylic backdrop behind a transparent page. False when not possible."""
    if sys.platform != "win32":
        return False
    try:
        import clr  # pythonnet, installed with pywebview on Windows

        clr.AddReference("System.Drawing")
        import System
        from System.Drawing import Color

        form = window.native
        hwnd = int(str(form.Handle))

        def paint_black() -> None:  # black client area = where DWM shows the backdrop
            form.BackColor = Color.Black
            for control in form.Controls:
                control.BackColor = Color.Black

        form.Invoke(System.Action(paint_black))
        dwm = ctypes.windll.dwmapi

        def set_attr(attr: int, value: int) -> int:
            v = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        set_attr(_DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        set_attr(_DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
        ok = set_attr(_DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_TRANSIENTWINDOW) == 0
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(-1, -1, -1, -1)))
        return ok
    except Exception:
        log.warning("Glass backdrop not available; using a plain dark window", exc_info=True)
        return False


def setup_logging() -> Path:
    """Log to ``<data dir>/logs/app.log`` (rotated); also uncaught errors of every thread.

    Started without a console (shortcut, ``pythonw``) there is nowhere else to see a crash.
    """
    from steno import paths

    for name in ("stdout", "stderr"):  # pythonw: no console streams at all
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    log_dir = paths.data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "app.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def on_error(exc_type, exc, tb) -> None:
        logging.getLogger("steno").critical("Uncaught error", exc_info=(exc_type, exc, tb))

    sys.excepthook = on_error
    threading.excepthook = lambda a: on_error(a.exc_type, a.exc_value, a.exc_traceback)
    return path


def claim_single_instance() -> bool:
    """True for the first app; a second start brings the running window forward instead."""
    global _mutex
    if sys.platform != "win32":
        return True
    try:
        kernel, user = ctypes.windll.kernel32, ctypes.windll.user32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        _mutex = kernel.CreateMutexW(None, False, _MUTEX_NAME)
        if kernel.GetLastError() != _ERROR_ALREADY_EXISTS:
            return True
        hwnd = user.FindWindowW(None, TITLE)
        if hwnd:
            user.ShowWindow(hwnd, 9)  # SW_RESTORE
            user.SetForegroundWindow(hwnd)
        else:
            user.MessageBoxW(None, "Steno is already running.", TITLE, 0x40)
        return False
    except Exception:
        log.debug("single-instance check failed", exc_info=True)
        return True


def run_app(cfg: Config, *, debug: bool = False) -> int:
    """Open the desktop window; returns when it is closed (a recording is stopped first)."""
    try:
        import webview
    except ImportError:
        print("error: the desktop window needs pywebview: uv sync --extra app",
              file=sys.stderr)
        return 1
    from steno.ui.api import UiApp
    from steno.ui.server import UiServer

    if not claim_single_instance():
        return 0
    log.info("Steno app starting (log: %s)", setup_logging())
    app = UiApp(cfg)
    server = UiServer(app)
    server.start()
    bridge = Bridge()
    width, height = 1240, 820
    try:  # fit on smaller or scaled screens (screens are physical pixels, the size is not)
        screen = webview.screens[0]
        scale = 1.0
        if sys.platform == "win32":
            scale = max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96)
        width = min(width, int(screen.width / scale * 0.85))
        height = min(height, int(screen.height / scale * 0.85))
    except Exception:
        pass
    window = webview.create_window(
        TITLE, server.url, js_api=bridge, width=width, height=height, min_size=(760, 520),
        transparent=sys.platform == "win32", background_color="#000000",
    )
    bridge._window = window

    def on_shown() -> None:
        if apply_glass(window):
            window.evaluate_js("document.documentElement.classList.add('glass')")

    def on_closing() -> bool:
        """Closing during a recording asks first; the recording is then stopped cleanly."""
        if not app.recorder.active:
            return True
        return bool(window.create_confirmation_dialog(
            TITLE, "A recording is running. Stop it and close Steno?\n\n"
                   "What was said so far is kept."))

    window.events.shown += on_shown
    window.events.closing += on_closing
    try:
        webview.start(debug=debug, gui="edgechromium" if sys.platform == "win32" else None)
    except Exception:
        log.exception("The window failed")
        raise
    finally:
        server.stopping.set()
        if app.recorder.active:
            app.recorder.shutdown(drain=True)
        server.close()
        log.info("Steno app closed")
    return 0
