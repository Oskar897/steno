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


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)

# The app's mark: four rounded bars of a sound wave (same as the logo in the page), in the
# accent orange on a dark rounded tile. Unit coordinates, y down: (centre x, height).
_BARS = ((0.335, 0.27), (0.445, 0.50), (0.555, 0.64), (0.665, 0.34))
_BARS_SMALL = ((0.30, 0.34), (0.50, 0.62), (0.70, 0.42))  # 3 thicker bars stay crisp <= 24 px


def _round_rect(x, y, x0, y0, x1, y1, r):
    import numpy as np

    dx = np.maximum(np.maximum(x0 + r - x, x - (x1 - r)), 0)
    dy = np.maximum(np.maximum(y0 + r - y, y - (y1 - r)), 0)
    return (dx * dx + dy * dy <= r * r) & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)


def _icon_rgba(size: int):
    """float RGBA (size, size, 4), top row first, straight alpha; anti-aliased by supersampling."""
    import numpy as np

    ss = 8 if size <= 64 else 4
    n = size * ss
    t = (np.arange(n) + 0.5) / n
    x, y = np.meshgrid(t, t)
    small = size <= 24
    pad = 0.04 if small else 0.06
    tile = _round_rect(x, y, pad, pad, 1 - pad, 1 - pad, 0.24 if small else 0.225)
    inner = pad + 0.012
    rim = tile & ~_round_rect(x, y, inner, inner, 1 - inner, 1 - inner, 0.21)

    top, bottom = np.array([0.205, 0.205, 0.235]), np.array([0.07, 0.07, 0.085])
    k = ((y - pad) / (1 - 2 * pad)).clip(0, 1)[..., None]
    rgb = top * (1 - k) + bottom * k
    glow = np.exp(-(((x - 0.5) / 0.32) ** 2 + ((y - 0.62) / 0.26) ** 2))[..., None]
    rgb = rgb + glow * np.array([0.10, 0.035, 0.0]) * 0.55  # faint warm light behind the bars
    rgb = np.where(rim[..., None], rgb + (0.10 - 0.08 * k), rgb)  # hairline, lit from the top

    bars = np.zeros_like(x, dtype=bool)
    width = 0.13 if small else 0.074
    snap = size <= 64  # small icons: bar edges on whole pixels, or they blur
    for cx, height in _BARS_SMALL if small else _BARS:
        x0, x1, y0 = cx - width / 2, cx + width / 2, 0.5 - height / 2
        y1 = y0 + height
        if snap:
            wpx = max(2, round(width * size))
            x0 = round(x0 * size) / size
            x1 = x0 + wpx / size
            y0, y1 = round(y0 * size) / size, round(y1 * size) / size
        r = (x1 - x0) / 2 if size >= 32 else (x1 - x0) * 0.3
        bars |= _round_rect(x, y, x0, y0, x1, y1, r)
    hot, deep = np.array([1.0, 0.56, 0.32]), np.array([1.0, 0.29, 0.06])  # #ff8f52 -> #ff4a10
    kb = ((y - 0.18) / 0.64).clip(0, 1)[..., None]
    rgb = np.where(bars[..., None], hot * (1 - kb) + deep * kb, rgb)

    alpha = tile.astype(float)
    img = np.concatenate([rgb * alpha[..., None], alpha[..., None]], axis=-1)
    img = img.reshape(size, ss, size, ss, 4).mean(axis=(1, 3))  # box filter
    a = img[..., 3:4]
    img[..., :3] = np.where(a > 0, img[..., :3] / np.maximum(a, 1e-6), 0)
    return img.clip(0, 1)


def _icon_pixels(size: int) -> bytes:
    """BGRA rows, bottom-up (the .ico layout)."""
    import numpy as np

    img = (_icon_rgba(size) * 255 + 0.5).astype(np.uint8)
    return img[::-1][..., [2, 1, 0, 3]].tobytes()


def make_icon(path: Path, sizes: tuple[int, ...] = ICON_SIZES) -> Path:
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
