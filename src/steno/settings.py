"""App settings edited in the desktop app (``<data dir>/settings.json``).

Separate from ``config.toml`` (models, thresholds: set by hand): these are the switches a
user flips in Settings. Unknown keys are ignored, bad values fall back to the defaults.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

from steno import paths
from steno.transcript import atomic_write_text

SETTINGS_NAME = "settings.json"
MAX_TERMS = 200
MAX_TERM_LEN = 60
THEMES = ("system", "dark", "light")
UI_SCALES = (90, 100, 110, 125, 140)  # interface size in percent
INTENSITIES = ("subtle", "medium", "strong")
PALETTES = ("mono", "teal", "wine", "vintage", "deepsea")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class AppSettings:
    # After a recording stops: finalize (better speakers, seconds, local, no kept audio needed
    # with the default [post] modes), then these jobs in this order (each needs the Claude CLI).
    auto_finalize: bool = True
    # Name a meeting the user left untitled from its transcript (Claude, haiku, a few seconds).
    # The dock has a per-meeting switch that starts from this value.
    auto_title: bool = True
    auto_clean: bool = False
    auto_summary: bool = False
    auto_todo: bool = False
    # Words the meetings use (product names, course codes, people's names): the clean-up
    # gets them as the right spelling of what the speech recognition misheard.
    glossary: list[str] = field(default_factory=list)
    # Look of the desktop app: "system" follows Windows' app mode, or "dark" / "light".
    theme: str = "system"
    # Accent colours (surfaces stay grey): one of PALETTES, each with a dark and a light variant.
    palette: str = "mono"
    # How far the surfaces lean toward the palette colour: subtle (grey), medium or strong.
    intensity: str = "subtle"
    # Interface size in percent: text, icons and controls scale together.
    ui_scale: int = 100
    # Desktop app: the desktop shows through the window (Mica); off = solid panels.
    transparent: bool = True
    # Desktop app: a small always-on-top strip (timer, pause, stop) while recording.
    mini_strip: bool = True


def clean_terms(values: Any) -> list[str]:
    """Trimmed, de-duplicated (case-insensitive) terms; non-text entries are dropped."""
    if isinstance(values, str):
        values = re.split(r"[,\n;]", values)
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            continue
        term = re.sub(r"\s+", " ", _CONTROL_RE.sub(" ", v)).strip()[:MAX_TERM_LEN]
        if term and term.casefold() not in seen:
            seen.add(term.casefold())
            out.append(term)
    return out[:MAX_TERMS]


def from_dict(data: dict[str, Any], base: AppSettings | None = None) -> AppSettings:
    """``base`` with the valid values of ``data`` applied."""
    s = base or AppSettings()
    updates: dict[str, Any] = {}
    for f in fields(AppSettings):
        if f.name not in data:
            continue
        value = data[f.name]
        if f.name == "glossary":
            updates[f.name] = clean_terms(value)
        elif f.name == "theme":
            if value in THEMES:
                updates[f.name] = value
        elif f.name == "ui_scale":
            if type(value) is int and value in UI_SCALES:
                updates[f.name] = value
        elif f.name == "intensity":
            if value in INTENSITIES:
                updates[f.name] = value
        elif f.name == "palette":
            if value in PALETTES:
                updates[f.name] = value
        elif isinstance(value, bool):
            updates[f.name] = value
    return replace(s, **updates)


def load() -> AppSettings:
    try:
        data = json.loads((paths.data_dir() / SETTINGS_NAME).read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError):
        return AppSettings()
    return from_dict(data) if isinstance(data, dict) else AppSettings()


def save(settings: AppSettings) -> None:
    atomic_write_text(paths.data_dir() / SETTINGS_NAME,
                      json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n")


def update(data: dict[str, Any]) -> AppSettings:
    s = from_dict(data, load())
    save(s)
    return s
