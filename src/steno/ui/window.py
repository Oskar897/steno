"""Desktop window for the UI (``steno app``): the local UI server inside a native window.

The page is the same as ``steno ui`` serves, shown by pywebview (WebView2 on Windows) instead
of a browser. On Windows 11 the window gets the system Mica backdrop (the wallpaper shows
through, blurred) and a dark title bar; elsewhere it is a plain dark window.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path

from steno.config import Config

log = logging.getLogger(__name__)

TITLE = "Steno"
_MUTEX_NAME = "Local\\steno-desktop-app"
_ERROR_ALREADY_EXISTS = 183
_APP_ID = "Steno.Desktop"  # taskbar identity (AppUserModelID)
_mutex = None  # held for the life of the process

# DwmSetWindowAttribute ids (Windows 11)
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_BORDER_COLOR = 34
_DWMWA_COLOR_NONE = -2  # 0xFFFFFFFE as a signed int: DWM draws no border line around the window
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_MAINWINDOW = 2  # Mica: the wallpaper, smoothly blurred (not the windows behind)
_DWMWCP_ROUND = 2
_WDA_EXCLUDEFROMCAPTURE = 0x11  # SetWindowDisplayAffinity: not in screen shares/recordings

MINI_TITLE = "Steno recording"
MINI_SIZE = (196, 34)
MINI_PLACE_NAME = "mini-strip.json"  # in the data dir: where the strip was dragged to
MINI_SHOW_STATES = ("starting", "recording")  # Stop hides it at once ("stopping")


class _Margins(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class Bridge:
    """Functions the page may call as ``window.pywebview.api.<name>()``."""

    def __init__(self) -> None:
        self._window = None  # private: pywebview exposes public attributes to the page
        self._hwnd = 0  # set once the custom frame is on
        self._glass_hwnd = 0  # set once the backdrop is on

    def set_look(self, dark: bool) -> bool:
        """Light or dark window backdrop and frame, to match the page's theme."""
        if not self._glass_hwnd or not isinstance(dark, bool):
            return False
        return _dwm_look(self._glass_hwnd, dark)

    def window_control(self, action: str) -> bool:
        """The page's own window buttons: minimize | maximize | close."""
        from steno.ui import frame

        if not self._hwnd or action not in ("minimize", "maximize", "close"):
            return False
        log.debug("window button: %s", action)
        return frame.window_command(self._hwnd, action)

    def window_maximized(self) -> bool:
        from steno.ui import frame

        return bool(self._hwnd) and frame.is_maximized(self._hwnd)

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

    def open_folder(self, kind: str, ref: str | None = None, mtype: str | None = None) -> bool:
        """Show a folder in Explorer: ``meeting`` (ref = session id), ``project`` (ref = project
        id), ``type`` (ref = project id, mtype = subfolder name) or ``inbox``. The path is
        resolved here from ids and names (``projects.folder_to_open``), never from the page."""
        from steno import projects

        if sys.platform != "win32" or not isinstance(kind, str):
            return False
        if not all(v is None or isinstance(v, str) for v in (ref, mtype)):
            return False
        try:
            folder = projects.folder_to_open(kind, ref, mtype)
        except Exception:
            log.debug("could not resolve the folder to open", exc_info=True)
            return False
        if folder is None or not folder.is_dir():
            return False
        os.startfile(folder)  # noqa: S606 - a folder path we resolved ourselves
        return True

    def open_meeting_folder(self, session_id: str) -> bool:
        """Show a meeting's folder in Explorer (an inbox session: its session folder)."""
        return self.open_folder("meeting", session_id)


class MiniBridge:
    """Functions the mini strip may call. Also the strip's shared state for ``watch_mini``."""

    def __init__(self) -> None:
        self._main = None
        self._mini = None
        self._main_hwnd = 0  # set once the main window is shown
        self._dismissed = False  # hidden with x for the running recording
        self._drag: tuple | None = None  # (hwnd, x, y, scale) while dragging

    def show_main(self) -> bool:
        if self._main is None:
            return False
        try:
            self._main.restore()
            self._main.show()
            if sys.platform == "win32" and self._main_hwnd:
                ctypes.windll.user32.ShowWindow(self._main_hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(self._main_hwnd)
            return True
        except Exception:
            log.debug("could not bring the main window forward", exc_info=True)
            return False

    def _hide_now(self) -> None:
        if self._mini is not None:
            try:
                self._mini.hide()
            except Exception:
                log.debug("could not hide the mini strip", exc_info=True)

    def stopped(self) -> bool:
        """Stop was pressed on the strip: hide it now and show the meeting."""
        self._hide_now()
        return self.show_main()

    def drag_start(self) -> bool:
        """The strip is being dragged (our own drag: pywebview's move() fails on Windows)."""
        hwnd = _mini_hwnd()
        r = wintypes.RECT()
        if not hwnd or not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False
        scale = ctypes.windll.user32.GetDpiForWindow(hwnd) / 96 or 1.0
        self._drag = (hwnd, r.left, r.top, scale)
        return True

    def drag_to(self, dx: float, dy: float) -> bool:
        """Move by (dx, dy) CSS pixels from where the drag started."""
        drag = getattr(self, "_drag", None)
        if drag is None or not all(isinstance(v, int | float) for v in (dx, dy)):
            return False
        hwnd, x, y, scale = drag
        user = ctypes.windll.user32
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        return bool(user.SetWindowPos(hwnd, None, int(x + dx * scale), int(y + dy * scale), 0, 0,
                                      0x0001 | 0x0004 | 0x0010))  # NOSIZE|NOZORDER|NOACTIVATE

    def drag_end(self) -> bool:
        self._drag = None
        save_mini_place()
        return True

    def dismiss(self) -> bool:
        """x on the strip: hidden for the rest of this recording (back with the next one)."""
        self._dismissed = True
        self._hide_now()
        return True


def _mini_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    return int(ctypes.windll.user32.FindWindowW(None, MINI_TITLE) or 0)


def prepare_mini(window=None) -> bool:
    """Rounded corners and no screen capture for the mini strip's window."""
    hwnd = _mini_hwnd()
    if not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        v = ctypes.c_int(_DWMWCP_ROUND)
        dwm.DwmSetWindowAttribute(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(v),
                                  ctypes.sizeof(v))
        user = ctypes.windll.user32
        user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        return bool(user.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        log.debug("could not prepare the mini strip window", exc_info=True)
        return False


def _mini_place_file() -> Path:
    from steno import paths

    return paths.data_dir() / MINI_PLACE_NAME


def save_mini_place() -> None:
    """Remember where the strip was dragged to (physical pixels, top-left corner)."""
    hwnd = _mini_hwnd()
    if not hwnd:
        return
    r = wintypes.RECT()
    if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        try:
            _mini_place_file().write_text(json.dumps({"x": r.left, "y": r.top}),
                                          encoding="utf-8")
        except OSError:
            log.debug("could not save the mini strip place", exc_info=True)


def restore_mini_place() -> bool:
    """Move the strip to its saved place, if that point is still on a screen."""
    hwnd = _mini_hwnd()
    try:
        data = json.loads(_mini_place_file().read_text(encoding="utf-8"))
        x, y = int(data["x"]), int(data["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not hwnd:
        return False
    user = ctypes.windll.user32
    user.MonitorFromPoint.restype = ctypes.c_void_p
    if not user.MonitorFromPoint(wintypes.POINT(x + 20, y + 10), 0):  # MONITOR_DEFAULTTONULL
        return False
    return bool(user.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010))
    # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE


def _foreground() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow() or 0) if sys.platform == "win32" else 0


def watch_mini(mini, recorder, stopping: threading.Event, every: float = 0.3, *,
               bridge: MiniBridge | None = None, foreground=_foreground) -> None:
    """Show the strip while a recording starts or runs, unless Steno itself is in front, the
    strip was hidden with x for this recording, or Settings turned it off."""
    from steno import settings

    shown = placed = False
    allowed, checked = True, 0.0
    main_hwnd = 0
    while not stopping.wait(every):
        now = time.monotonic()
        if now - checked > 2.0:  # the switch in Settings; re-read now and then
            checked = now
            try:
                allowed = settings.load().mini_strip
            except Exception:
                allowed = True
        if bridge is not None:
            main_hwnd = bridge._main_hwnd
        active = recorder.state in MINI_SHOW_STATES
        if bridge is not None and not active:
            bridge._dismissed = False  # x lasts until this recording ends
        dismissed = bridge is not None and bridge._dismissed
        app_in_front = bool(main_hwnd) and foreground() == main_hwnd
        want = allowed and active and not dismissed and not app_in_front
        if want == shown:
            continue
        try:
            if want:
                if not placed:
                    placed = restore_mini_place() or True
                mini.show()
                prepare_mini(mini)  # each time: cheap, and capture exclusion must hold
            else:
                save_mini_place()
                mini.hide()
            shown = want
        except Exception:
            log.debug("mini strip show/hide failed", exc_info=True)


def set_app_id() -> None:
    """Own taskbar identity: without it Windows groups the window under python(w).exe and shows
    Python's icon. Call before the window is created."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_ID)
    except Exception:
        log.warning("Could not set the taskbar app id", exc_info=True)


def app_icon() -> Path | None:
    """The app icon (.ico, drawn in code) in the data dir, rewritten on each start."""
    from steno import paths, shortcuts

    try:
        return shortcuts.make_icon(paths.data_dir() / shortcuts.ICON_NAME)
    except Exception:
        log.warning("Could not write the app icon", exc_info=True)
        return None


def apply_icon(window, icon: Path | None) -> bool:
    """Window and taskbar icon (title bar, Alt+Tab, taskbar button)."""
    if sys.platform != "win32" or icon is None:
        return False
    try:
        import clr

        clr.AddReference("System.Drawing")
        import System
        from System.Drawing import Icon

        form = window.native
        form.Invoke(System.Action(lambda: setattr(form, "Icon", Icon(str(icon)))))
        return True
    except Exception:
        log.warning("Could not set the window icon", exc_info=True)
        return False


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", ctypes.c_ulong)]


def fit_on_screen(hwnd: int) -> bool:
    """Move (and if needed shrink) the window so it lies inside its monitor's work area.

    A window that starts partly below the screen or under the taskbar never paints that part,
    and dragging it up then shows a white rectangle. True when it was moved.
    """
    if sys.platform != "win32":
        return False
    user = ctypes.windll.user32
    user.MonitorFromWindow.restype = ctypes.c_void_p
    user.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    win = wintypes.RECT()
    if not user.GetMonitorInfoW(user.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
        return False
    if not user.GetWindowRect(hwnd, ctypes.byref(win)):
        return False
    work = info.rcWork
    w = min(win.right - win.left, work.right - work.left)
    h = min(win.bottom - win.top, work.bottom - work.top)
    inside = (win.left >= work.left and win.top >= work.top
              and win.right <= work.right and win.bottom <= work.bottom)
    if inside:
        return False
    x = work.left + (work.right - work.left - w) // 2
    y = work.top + (work.bottom - work.top - h) // 2
    user.SetWindowPos(hwnd, None, x, y, w, h, 0x0004 | 0x0010)  # NOZORDER | NOACTIVATE
    return True


_BLANK = ("<!doctype html><html><head><meta charset='utf-8'></head>"
          "<body style='margin:0;background:transparent'></body></html>")


def setup_frame(window, bridge: Bridge) -> bool:
    """Page-drawn title bar: drag regions on, caption strip off. False = keep the native one."""
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        import System

        from steno.ui import frame

        form = window.native
        result: list[bool] = []

        def run() -> None:
            ok = frame.enable_drag_regions(form) and frame.remove_caption(form)
            result.append(ok)

        form.Invoke(System.Action(run))
        if result and result[0]:
            bridge._hwnd = int(str(form.Handle))
            return True
    except Exception:
        log.warning("Custom window frame not available; keeping the native title bar",
                    exc_info=True)
    return False


def system_dark() -> bool:
    """Windows' app mode (Settings > Personalization > Colors); dark when unknown."""
    if sys.platform != "win32":
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


def start_dark() -> bool:
    """Dark or light at start, from the app settings (before the page can say)."""
    from steno import settings

    theme = settings.load().theme
    return system_dark() if theme == "system" else theme == "dark"


def _dwm_look(hwnd: int, dark: bool) -> bool:
    dwm = ctypes.windll.dwmapi

    def set_attr(attr: int, value: int) -> int:
        v = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

    set_attr(_DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
    return set_attr(_DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_MAINWINDOW) == 0


def apply_glass(window, dark: bool = True) -> bool:
    """Windows 11 Mica backdrop behind a transparent page. False when not possible."""
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

        set_attr(_DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
        set_attr(_DWMWA_BORDER_COLOR, _DWMWA_COLOR_NONE)  # no 1px frame line around the whole app
        ok = _dwm_look(hwnd, dark)
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
    set_app_id()
    icon = app_icon()
    bridge = Bridge()
    width, height = 1240, 820
    x = y = None  # centred, a little above the middle (the taskbar), not where Windows cascades
    sw = 0  # screen width in logical pixels, when known
    try:  # fit on smaller or scaled screens (screens are physical pixels, the size is not)
        screen = webview.screens[0]
        scale = 1.0
        if sys.platform == "win32":
            scale = max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96)
        sw, sh = int(screen.width / scale), int(screen.height / scale)
        width = min(width, int(sw * 0.85))
        height = min(height, int(sh * 0.85))
        x, y = max(0, (sw - width) // 2), max(0, (sh - 48 - height) // 2)
    except Exception:
        pass
    custom = sys.platform == "win32"
    # A blank page first: the WebView2 setting for page-drawn title bars applies from the next
    # navigation, so the real page is loaded once it is set (see on_loaded).
    window = webview.create_window(
        TITLE, html=_BLANK if custom else None, url=None if custom else server.url,
        js_api=bridge, width=width, height=height, x=x, y=y, min_size=(340, 480),
        transparent=custom, background_color="#000000",
    )
    bridge._window = window
    looks: list[str] = []  # html classes for the page: "glass", "custom-frame"
    started = threading.Event()

    def on_shown() -> None:
        try:
            mini_bridge._main_hwnd = int(str(window.native.Handle))
            fit_on_screen(mini_bridge._main_hwnd)
        except Exception:
            log.debug("could not fit the window on screen", exc_info=True)
        apply_icon(window, icon)
        if apply_glass(window, start_dark()):
            bridge._glass_hwnd = int(str(window.native.Handle))
            looks.append("glass")

    def on_loaded() -> None:
        if custom and not started.is_set():
            started.set()
            if setup_frame(window, bridge):
                looks.append("custom-frame")
            window.load_url(server.url)
            return
        if looks:
            names = ", ".join(repr(c) for c in looks)
            window.evaluate_js(f"document.documentElement.classList.add({names})")

    def on_closing() -> bool:
        """Closing during a recording asks first; the recording is then stopped cleanly."""
        if not app.recorder.active:
            return True
        return bool(window.create_confirmation_dialog(
            TITLE, "A recording is running. Stop it and close Steno?\n\n"
                   "What was said so far is kept."))

    moved = threading.Timer(0, lambda: None)

    def on_moved(*_args) -> None:
        """After a move, repaint the whole page once (parts that were off screen may not have
        been painted yet)."""
        nonlocal moved
        moved.cancel()
        moved = threading.Timer(0.25, lambda: window.evaluate_js(
            "window.stenoRepaint && window.stenoRepaint()"))
        moved.daemon = True
        moved.start()

    window.events.shown += on_shown
    window.events.moved += on_moved
    window.events.loaded += on_loaded
    window.events.closing += on_closing

    # The mini strip: hidden until a recording starts, top centre of the screen, always on top.
    mini_bridge = MiniBridge()
    mini_bridge._main = window
    mw, mh = MINI_SIZE
    mx, my = (max(0, (sw - mw) // 2), 8) if sw else (None, None)
    mini = webview.create_window(
        MINI_TITLE, url=server.mini_url, js_api=mini_bridge, width=mw, height=mh, x=mx, y=my,
        resizable=False, frameless=True, easy_drag=False, on_top=True, hidden=True,
        focus=False, background_color="#1b263b", min_size=(mw, mh),
    )
    mini_bridge._mini = mini

    def on_main_closed() -> None:
        try:
            mini.destroy()
        except Exception:
            pass

    window.events.closed += on_main_closed
    threading.Thread(target=watch_mini, args=(mini, app.recorder, server.stopping),
                     kwargs={"bridge": mini_bridge}, name="mini-strip", daemon=True).start()
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
