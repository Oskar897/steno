"""Shortcut icon and the desktop-window helpers that run without a window."""

from __future__ import annotations

import logging
import struct
import sys

from steno import shortcuts
from steno.ui import window


def test_icon_is_a_valid_multi_size_ico(tmp_path):
    data = shortcuts.make_icon(tmp_path / "a.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(shortcuts.ICON_SIZES))
    for i in range(count):
        w, h, _, _, planes, bpp, size, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i: 22 + 16 * i])
        assert planes == 1 and bpp == 32 and offset + size <= len(data)
        assert struct.unpack("<I", data[offset:offset + 4])[0] == 40  # BITMAPINFOHEADER


def test_icon_has_the_accent_in_the_middle_and_transparent_corners():
    px = shortcuts._icon_pixels(32)
    def at(x, y):  # rows are bottom-up
        i = ((31 - y) * 32 + x) * 4
        return tuple(px[i:i + 4])
    b, g, r, a = at(17, 16)  # on the tallest bar: accent orange, opaque
    assert r == 255 and b < 80 and a == 255
    b, g, r, a = at(16, 4)  # tile above the bars: dark, opaque
    assert max(b, g, r) < 80 and a == 255
    assert at(0, 0)[3] == 0  # rounded corner: transparent


def test_app_log_goes_to_the_data_dir(steno_data_dir, monkeypatch):
    root = logging.getLogger()
    before = list(root.handlers)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    import threading
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    try:
        path = window.setup_logging()
        logging.getLogger("steno.test").info("hello log")
        for h in root.handlers:
            h.flush()
        assert path.parent == steno_data_dir / "logs"
        assert "hello log" in path.read_text(encoding="utf-8")
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_bridge_does_not_expose_the_window_to_the_page():
    b = window.Bridge()
    assert [n for n in vars(b) if not n.startswith("_")] == []
    assert b.pick_folder() is None


def test_window_buttons_do_nothing_without_the_custom_frame():
    bridge = window.Bridge()
    assert bridge.window_control("maximize") is False  # no frame set up yet
    assert bridge.window_maximized() is False
    bridge._hwnd = 1
    assert bridge.window_control("format-disk") is False  # only the three window commands


def test_window_commands_are_limited_to_three():
    from steno.ui import frame

    assert frame.window_command(0, "explode") is False
