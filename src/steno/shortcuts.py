"""Start-menu and desktop shortcuts for the desktop app (Windows).

The shortcut runs ``pythonw -m steno.cli app`` from this environment, so the app opens without
a console window, like any other program. The icon is drawn here (no image files in the repo).
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

from steno import paths

APP_NAME = "Steno"
ICON_NAME = "steno.ico"


def _icon_pixels(size: int) -> bytes:
    """BGRA rows, bottom-up: a dark round tile with a small orange dot (the accent)."""
    rows = []
    c = (size - 1) / 2
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            if d <= size * 0.16:
                b, g, r, a = 0x1F, 0x5A, 0xFF, 255  # #ff5a1f
            elif d <= size * 0.47:
                b, g, r, a = 0x1C, 0x18, 0x16, 245
            elif d <= size * 0.5:
                b, g, r, a = 0x3A, 0x36, 0x34, 255  # thin light rim
            else:
                b, g, r, a = 0, 0, 0, 0
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    return b"".join(rows)


def make_icon(path: Path, sizes: tuple[int, ...] = (16, 32, 48, 256)) -> Path:
    """Write a multi-size .ico (32-bit BMP images)."""
    images = []
    for s in sizes:
        pixels = _icon_pixels(s)
        mask = b"\x00" * (((s + 31) // 32) * 4 * s)  # AND mask unused with alpha
        header = struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0, len(pixels) + len(mask),
                             0, 0, 0, 0)
        images.append(header + pixels + mask)
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images, strict=True):
        dim = 0 if s >= 256 else s
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(out + b"".join(images))
    return path


def pythonw() -> Path:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.is_file() else exe


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def install(*, desktop: bool = True, start_menu: bool = True) -> list[Path]:
    """Create (or replace) the shortcuts; returns their paths."""
    if sys.platform != "win32":
        raise RuntimeError("shortcuts are only made on Windows")
    icon = make_icon(paths.data_dir() / ICON_NAME)
    folders = []
    if desktop:
        folders.append("[Environment]::GetFolderPath('Desktop')")
    if start_menu:
        folders.append("[Environment]::GetFolderPath('Programs')")
    script = ["$ws = New-Object -ComObject WScript.Shell"]
    for folder in folders:
        script.append(
            f"$p = Join-Path ({folder}) {_ps_quote(APP_NAME + '.lnk')}; "
            "$s = $ws.CreateShortcut($p); "
            f"$s.TargetPath = {_ps_quote(str(pythonw()))}; "
            "$s.Arguments = '-m steno.cli app'; "
            f"$s.WorkingDirectory = {_ps_quote(str(Path.home()))}; "
            f"$s.IconLocation = {_ps_quote(str(icon))}; "
            "$s.Description = 'Local meeting transcription'; "
            "$s.Save(); Write-Output $p")
    res = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "; ".join(script)],
        capture_output=True, text=True, timeout=60, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"could not create the shortcuts: {res.stderr.strip()}")
    return [Path(line.strip()) for line in res.stdout.splitlines() if line.strip()]
