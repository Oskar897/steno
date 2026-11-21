"""Summary, to-do list and transcript fixes through the Claude Code CLI (text only).

Steno never holds an API key: ``claude -p`` runs with the user's own Claude Code login.
Only transcript *text* is sent (on stdin, never in argv); audio and voiceprints never
leave the machine. The CLI is started in an empty temporary directory with all tools
switched off and no session saved, so it cannot read or change files.

Four features, each with its prompt in ``steno/prompts/*.md``:

* ``summarize``  -> ``summary.md`` (overview, decisions, open questions)
* ``extract_todos`` -> ``todo.json`` / ``todo.md``: explicit commitments only, taken from the
  transcript (never from the summary). An item whose ``evidence_quote`` is not found
  verbatim (after normalising case, punctuation and whitespace) in the transcript is dropped.
* ``llm_fix`` (``steno finalize --llm-fix``): the model returns diffs ``{id, old, new}``;
  an edit is applied only when ``old`` occurs in that line and the change is small.
* ``suggest_title``: a short meeting title from the start of the transcript (the app's
  automatic title, only for a meeting the user did not name).

Whether the CLI is installed and logged in is asked with ``claude auth status``
(``probe_status`` / ``StatusCache``); ``start_login`` opens ``claude auth login`` in a visible
console so the user completes the browser sign-in themselves.

The clean transcript (``steno clean``, see ``steno.clean``) is used when it exists and still
matches its source, else the final transcript, else the live one with current speaker names;
``summary.md`` and ``todo.md`` end with a line saying which text was used.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any

from steno import labels, transcript
from steno.config import LlmConfig
from steno.transcript import Segment, atomic_write_text, format_clock

log = logging.getLogger(__name__)

SUMMARY_NAME = "summary.md"
TODO_MD_NAME = "todo.md"
TODO_JSON_NAME = "todo.json"
LANGUAGE_NAMES = {"sv": "Swedish", "en": "English", "no": "Norwegian", "da": "Danish",
                  "de": "German", "fr": "French", "es": "Spanish", "fi": "Finnish"}
FIX_BATCH_CHARS = 8000

Runner = Callable[..., subprocess.CompletedProcess]


class LlmError(RuntimeError):
    """The Claude CLI is missing, failed, or answered with something unusable."""


class LlmAuthError(LlmError):
    """Claude Code is not logged in (never, or the session expired)."""


class LlmLimitError(LlmError):
    """The account's usage limit or credit balance is used up."""


AUTH_MESSAGE = "Not logged in to Claude: log in to use AI features."
LIMIT_MESSAGE = "Claude usage limit or credit balance reached: try again later."
INSTALL_URL = "https://docs.claude.com/en/docs/claude-code/setup"
MAX_DETAIL = 300

_AUTH_RE = re.compile(
    r"failed to authenticate|oauth|not logged in|not authenticated|/login|invalid api key"
    r"|invalid x-api-key|authentication[_ ]error|unauthori[sz]ed|session (?:has )?expired"
    r"|token (?:has )?expired|credentials? (?:are |is )?(?:missing|invalid|expired)",
    re.IGNORECASE)
_LIMIT_RE = re.compile(
    r"credit balance|usage limit|limit reached|out of (?:extra )?usage|quota", re.IGNORECASE)


def classify_failure(detail: str, prefix: str = "the Claude CLI failed") -> LlmError:
    """The error to raise for a failure message of the CLI (friendly text for known causes)."""
    detail = " ".join((detail or "").split()) or "no output"
    if _AUTH_RE.search(detail):
        return LlmAuthError(AUTH_MESSAGE)
    if _LIMIT_RE.search(detail):
        return LlmLimitError(LIMIT_MESSAGE)
    return LlmError(f"{prefix}: {detail[:MAX_DETAIL]}")


def _json_of(text: str) -> dict[str, Any] | None:
    """The CLI's JSON object: the whole output, else its last line."""
    text = (text or "").strip()
    for candidate in (text, text.rsplit("\n", 1)[-1]):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


# --------------------------------------------------------------------------- the CLI


def load_prompt(name: str, language: str = "sv") -> str:
    text = (resources.files("steno") / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    return text.replace("{language}", language_phrase(language))


def language_phrase(code: str) -> str:
    code = code.strip().lower()
    if code in ("", "auto"):
        return "the language the meeting is held in (the language of the transcript)"
    return LANGUAGE_NAMES.get(code, code)


def build_command(cfg: LlmConfig, prompt_file: Path) -> list[str]:
    """The ``claude -p`` command line.

    The instructions go in a file (a multi-line argument breaks through ``claude.cmd``
    shims on Windows). ``--setting-sources ""`` and ``--strict-mcp-config`` keep the user's
    own CLAUDE.md, hooks and MCP servers out of the call: the run is deterministic, cheap
    (about 1k instead of 15k tokens of overhead) and nothing but the transcript is sent.
    """
    exe = find_cli(cfg.command) or cfg.command
    cmd = [exe, "-p", "--model", cfg.model, "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--disable-slash-commands",
           "--setting-sources", "", "--strict-mcp-config",
           "--system-prompt-file", str(prompt_file)]
    if cfg.effort:
        cmd += ["--effort", cfg.effort]
    return cmd


def no_window() -> dict[str, Any]:
    """subprocess options that keep a console window from flashing up on Windows.

    The desktop app runs under ``pythonw`` (no console), so each child would otherwise get a
    new console window of its own (``claude.cmd`` goes through ``cmd.exe``).
    """
    if sys.platform != "win32":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": info}


def run_claude(
    system_prompt: str, user_text: str, cfg: LlmConfig, runner: Runner | None = None
) -> str:
    """One ``claude -p`` call: instructions as system prompt, the transcript on stdin."""
    runner = runner or subprocess.run  # looked up late so tests can forbid the real call
    try:
        with tempfile.TemporaryDirectory(prefix="steno-llm-") as empty:
            prompt_file = Path(empty) / "system-prompt.md"
            prompt_file.write_text(system_prompt, encoding="utf-8")
            cmd = build_command(cfg, prompt_file)
            proc = runner(
                cmd, input=user_text, capture_output=True, text=True, encoding="utf-8",
                timeout=cfg.timeout_s, cwd=empty, check=False, **no_window(),
            )
    except FileNotFoundError as exc:
        raise LlmError(
            f"cannot run '{cfg.command}': install Claude Code and log in, or set [llm] command"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LlmError(f"the Claude CLI did not answer within {cfg.timeout_s} s") from exc
    out = (proc.stdout or "").strip()
    data = _json_of(out)
    if proc.returncode != 0:
        # The real reason is the JSON's `result` on stdout; stderr is often empty.
        reason = data.get("result") if data is not None else None
        if not isinstance(reason, str) or not reason.strip():
            lines = (proc.stderr or "").strip().splitlines() or out.splitlines()
            reason = lines[-1] if lines else "no output"
        raise classify_failure(reason, f"the Claude CLI failed (exit {proc.returncode})")
    if data is None:
        return out  # plain text: older CLI or a different output format
    if data.get("is_error"):
        raise classify_failure(str(data.get("result") or ""), "the Claude CLI reported an error")
    if isinstance(data.get("result"), str):
        return data["result"].strip()
    raise LlmError("unexpected output from the Claude CLI")


# --------------------------------------------------------------------------- login status


def find_cli(command: str) -> str | None:
    """Path of the Claude Code executable, or None.

    ``PATH`` first, then the usual install places: the desktop app runs under ``pythonw`` and
    may not see the PATH of the user's terminal (native installer: ``~/.local/bin``,
    npm: ``%APPDATA%/npm``).
    """
    found = shutil.which(command)
    if found:
        return found
    if Path(command).is_file():
        return command
    name = Path(command).name
    dirs = [Path.home() / ".local" / "bin", Path.home() / ".claude" / "local",
            Path.home() / ".npm-global" / "bin"]
    if os.environ.get("APPDATA"):
        dirs.append(Path(os.environ["APPDATA"]) / "npm")
    if sys.platform != "win32":
        dirs += [Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
    for d in dirs:
        for candidate in (name, f"{name}.exe", f"{name}.cmd"):
            if (d / candidate).is_file():
                return str(d / candidate)
    return None


@dataclass(frozen=True)
class ClaudeStatus:
    """``state``: ``ok`` (logged in), ``logged_out``, ``missing`` (no CLI) or ``error``."""

    state: str
    method: str = ""  # how it is logged in ("claude.ai", "api_key", ...), never an account name
    plan: str = ""  # subscription type when the CLI says so ("pro", "max", ...)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "method": self.method, "plan": self.plan,
                "detail": self.detail}


def _label(value: Any) -> str:
    return " ".join(value.split())[:40] if isinstance(value, str) else ""


def probe_status(cfg: LlmConfig, runner: Runner | None = None) -> ClaudeStatus:
    """Ask ``claude auth status`` whether Claude Code is installed and logged in.

    Costs no tokens. The CLI exits 1 when logged out but still prints its JSON, so the output
    is read whatever the exit code. The account's e-mail address is never kept.
    """
    exe = find_cli(cfg.command)
    if exe is None:
        return ClaudeStatus("missing", detail=f"'{cfg.command}' was not found")
    runner = runner or subprocess.run
    try:
        proc = runner([exe, "auth", "status"], capture_output=True, text=True,
                      encoding="utf-8", timeout=20, check=False, stdin=subprocess.DEVNULL,
                      **no_window())
    except FileNotFoundError:
        return ClaudeStatus("missing", detail=f"'{cfg.command}' could not be started")
    except subprocess.TimeoutExpired:
        return ClaudeStatus("error", detail="Claude Code did not answer in time")
    data = _json_of(proc.stdout or "")
    if data is not None and isinstance(data.get("loggedIn"), bool):
        if not data["loggedIn"]:
            return ClaudeStatus("logged_out")
        return ClaudeStatus("ok", method=_label(data.get("authMethod")),
                            plan=_label(data.get("subscriptionType")))
    text = " ".join(((proc.stderr or "") + " " + (proc.stdout or "")).split())
    if _AUTH_RE.search(text):
        return ClaudeStatus("logged_out")
    return ClaudeStatus("error", detail=text[:MAX_DETAIL] or f"exit {proc.returncode}")


class StatusCache:
    """``probe()`` result kept for ``ttl`` seconds; ``invalidate()`` after an auth failure."""

    def __init__(self, probe: Callable[[], ClaudeStatus], ttl: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self._probe, self._ttl, self._clock = probe, ttl, clock
        self._lock = threading.Lock()
        self._value: ClaudeStatus | None = None
        self._at = 0.0

    def get(self, refresh: bool = False) -> ClaudeStatus:
        with self._lock:  # one probe at a time; the others reuse its answer
            value = self._value
            if refresh or value is None or self._clock() - self._at >= self._ttl:
                try:
                    value = self._probe()
                except Exception as exc:  # a status check must never break the page
                    log.warning("Claude status check failed: %s", exc)
                    value = ClaudeStatus("error", detail=str(exc)[:MAX_DETAIL])
                self._value, self._at = value, self._clock()
            return value

    def invalidate(self) -> None:
        with self._lock:
            self._value = None


def start_login(cfg: LlmConfig, popen: Callable[..., Any] | None = None) -> Any:
    """Start ``claude auth login`` in a visible console; the user finishes it in the browser.

    Returns the process. Windows: a console window of its own (``CREATE_NEW_CONSOLE``, not the
    hidden-window flags of the normal calls) so the user sees what the CLI prints.
    """
    exe = find_cli(cfg.command)
    if exe is None:
        raise LlmError(f"cannot run '{cfg.command}': install Claude Code first")
    popen = popen or subprocess.Popen
    if sys.platform == "win32":
        extra: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_CONSOLE}
    else:
        extra = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                 "stderr": subprocess.DEVNULL, "start_new_session": True}
    try:
        return popen([exe, "auth", "login"], **extra)
    except OSError as exc:
        raise LlmError(f"could not start the Claude login: {exc}") from exc



# --------------------------------------------------------------------------- transcript


def load_source_transcript(session_dir: Path) -> tuple[list[Segment], str]:
    """The best text before any clean-up: the final transcript, else the live one.

    Returns ``(segments, "final" | "live")``. This is the single place where the text of
    a session is chosen (``steno clean`` reads it too).
    """
    session_dir = Path(session_dir)
    from steno.finalize import has_final, read_final

    if has_final(session_dir):
        segments = read_final(session_dir)
        if segments:
            return segments, "final"
    return labels.resolve_segments(session_dir, transcript.read_best(session_dir)), "live"


def load_transcript(session_dir: Path) -> tuple[list[Segment], str]:
    """Segments for the LLM and which text they are: ``"clean"``, ``"final"`` or ``"live"``.

    ``clean`` (``steno clean``) is used while it still matches the text it was made from.
    """
    from steno import clean

    segments, kind = load_source_transcript(session_dir)
    cleaned = clean.clean_overlay(Path(session_dir), segments) if segments else None
    return (cleaned, "clean") if cleaned is not None else (segments, kind)


TEXT_FILES = {"clean": "clean.jsonl", "final": "final.jsonl", "live": "transcript.jsonl"}


def text_note(kind: str) -> str:
    """The line at the end of ``summary.md`` / ``todo.md`` saying which text was used."""
    return f"_Text used: {kind} transcript ({TEXT_FILES.get(kind, kind)})._"


def render_for_llm(segments: list[Segment]) -> str:
    return "\n".join(f"[{format_clock(s.t_start)}] {s.speaker}: {s.text}" for s in segments)


# --------------------------------------------------------------------------- JSON replies


def parse_json_array(text: str) -> list[Any]:
    """The first JSON array in a model reply (code fences and chatter around it tolerated)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        a, b = text.find("["), text.rfind("]")
        if a < 0 or b <= a:
            raise LlmError("the model did not return a JSON array") from None
        try:
            data = json.loads(text[a : b + 1])
        except ValueError:
            raise LlmError("the model's JSON could not be parsed") from None
    if not isinstance(data, list):
        raise LlmError("the model did not return a JSON array")
    return data


_NORM_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalise(text: str) -> str:
    """Case-folded text with every run of punctuation/whitespace reduced to one space."""
    return _NORM_RE.sub(" ", text.casefold()).strip()


def parse_clock(value: Any) -> float | None:
    """``"mm:ss"`` / ``"h:mm:ss"`` / seconds -> seconds, None when unparsable."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    parts = value.strip().strip("[]").split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not 1 <= len(nums) <= 3:
        return None
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


# --------------------------------------------------------------------------- summary


def summarize(
    session_dir: Path, cfg: LlmConfig, runner: Runner | None = None
) -> tuple[Path, str]:
    """Write ``summary.md``; returns its path and the transcript kind that was used."""
    segments, kind = load_transcript(session_dir)
    if not segments:
        raise LlmError("the session has no transcript text")
    reply = run_claude(load_prompt("summary", cfg.language), render_for_llm(segments), cfg, runner)
    if not reply.strip():
        raise LlmError("the model returned an empty summary")
    path = Path(session_dir) / SUMMARY_NAME
    atomic_write_text(path, f"{reply.strip()}\n\n{text_note(kind)}\n")
    return path, kind


# --------------------------------------------------------------------------- title

TITLE_SAMPLE_CHARS = 6000  # sent to the model: the start plus a piece of the middle
TITLE_MIN_CHARS = 40  # less text than this says nothing about the topic
MAX_TITLE_CHARS = 60
MAX_TITLE_WORDS = 10
_TITLE_BAD_RE = re.compile(r'[<>:"/\|?*\x00-\x1f]')
_TITLE_LABEL_RE = re.compile(r"^(?:title|titel|meeting title|mötestitel)\s*[:\-]\s*", re.I)
_TITLE_QUOTES = "\"'`“”„‘’«»*_# \t"
_TITLE_TRAIL = " .,:;!?-–—…"
_GENERIC_TITLES = {"meeting", "möte", "untitled", "untitled meeting", "namnlöst möte"}


def sample_for_title(segments: list[Segment], cap: int = TITLE_SAMPLE_CHARS) -> str:
    """The start of the transcript, plus a piece of the middle when it is longer than ``cap``."""
    lines = [f"{s.speaker}: {s.text.strip()[:500]}" for s in segments if s.text.strip()]
    if sum(len(x) + 1 for x in lines) <= cap:
        return "\n".join(lines)
    head_budget = cap * 3 // 4
    head: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > head_budget:
            break
        head.append(line)
        used += len(line) + 1
    rest = lines[len(head):]
    mid: list[str] = []
    used = 0
    for line in rest[len(rest) // 2:]:
        if used + len(line) + 1 > cap - head_budget:
            break
        mid.append(line)
        used += len(line) + 1
    return "\n".join(head + ["[...]"] + mid) if mid else "\n".join(head)


def clean_title_reply(text: str) -> str | None:
    """A usable meeting title from a model reply, or None.

    First line only; markdown, quotes, a leading "Title:" and trailing punctuation removed;
    characters a folder name cannot hold dropped; at most 60 characters (cut at a word).
    """
    line = next((x for x in (text or "").splitlines() if x.strip()), "")
    line = line.strip().lstrip("#").strip(_TITLE_QUOTES)
    line = _TITLE_BAD_RE.sub(" ", _TITLE_LABEL_RE.sub("", line))  # label first: it has a colon
    line = " ".join(line.split()).strip(_TITLE_TRAIL + _TITLE_QUOTES)
    if len(line) > MAX_TITLE_CHARS:
        line = line[:MAX_TITLE_CHARS].rsplit(" ", 1)[0] if " " in line[:MAX_TITLE_CHARS] \
            else line[:MAX_TITLE_CHARS]
        line = line.strip(_TITLE_TRAIL + _TITLE_QUOTES)
    if len(line) < 2 or len(line.split()) > MAX_TITLE_WORDS or line.casefold() in _GENERIC_TITLES:
        return None
    return line


def suggest_title(
    session_dir: Path, cfg: LlmConfig, runner: Runner | None = None
) -> str:
    """A short title for the meeting, from the best transcript text (nothing is written)."""
    segments, _ = load_transcript(session_dir)
    text = sample_for_title(segments)
    if len(text) < TITLE_MIN_CHARS:
        raise LlmError("too little text to name the meeting")
    title = clean_title_reply(run_claude(load_prompt("title"), text, cfg, runner))
    if title is None:
        raise LlmError("the model did not return a usable title")
    return title


# --------------------------------------------------------------------------- to-do


@dataclass(frozen=True)
class TodoItem:
    owner: str
    task: str
    due: str | None
    confidence: float
    evidence_quote: str
    t: float

    def as_dict(self) -> dict[str, Any]:
        return {"owner": self.owner, "task": self.task, "due": self.due,
                "confidence": self.confidence, "evidence_quote": self.evidence_quote,
                "t": round(self.t, 1)}


def filter_todos(
    raw: list[Any], segments: list[Segment]
) -> tuple[list[TodoItem], list[dict[str, str]]]:
    """Keep the items whose ``evidence_quote`` occurs in the transcript.

    The quote is searched (normalised) in the lines' texts joined by spaces, so it may
    span two lines. ``t`` is taken from the line where the quote starts, not from the
    model. Returns ``(kept, dropped)`` where each dropped entry says why.
    """
    bodies = [normalise(s.text) for s in segments]
    starts: list[int] = []
    joined = " "
    for body in bodies:
        starts.append(len(joined))
        joined += body + " "
    kept: list[TodoItem] = []
    dropped: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            dropped.append({"reason": "not an object"})
            continue
        task = str(item.get("task") or "").strip()
        quote = normalise(str(item.get("evidence_quote") or ""))
        if not task:
            dropped.append({"reason": "empty task"})
            continue
        # whole words only: " <quote> " inside the space-delimited transcript
        pos = joined.find(f" {quote} ") + 1 if quote else 0
        if pos <= 0:
            dropped.append({"reason": "evidence not in transcript", "task": task})
            continue
        index = max(i for i, st in enumerate(starts) if st <= pos)
        try:
            conf = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        due = item.get("due")
        kept.append(
            TodoItem(
                owner=str(item.get("owner") or "").strip() or "TBD",
                task=task,
                due=str(due).strip() if due not in (None, "") else None,
                confidence=min(1.0, max(0.0, conf)),
                evidence_quote=str(item.get("evidence_quote")).strip(),
                t=segments[index].t_start,
            )
        )
    return kept, dropped


def filter_person(items: list[TodoItem], person: str) -> list[TodoItem]:
    want = normalise(person)
    return [i for i in items if normalise(i.owner) == want]


def render_todo_md(items: list[TodoItem], title: str = "To-do", note: str | None = None) -> str:
    """Markdown grouped by owner (TBD last), each item with due date, confidence and time."""
    by_owner: dict[str, list[TodoItem]] = {}
    for it in sorted(items, key=lambda i: i.t):
        by_owner.setdefault(it.owner, []).append(it)
    owners = sorted(by_owner, key=lambda o: (o == "TBD", o.casefold()))
    lines = [f"# {title}", ""]
    if not items:
        lines.append("_No explicit commitments found._")
    for owner in owners:
        lines += [f"## {owner}", ""]
        for it in by_owner[owner]:
            due = f" (due: {it.due})" if it.due else ""
            lines.append(
                f"- [ ] {it.task}{due} - {format_clock(it.t)}, confidence {it.confidence:.2f}"
            )
            lines.append(f"  > {it.evidence_quote}")
        lines.append("")
    if note:
        lines += [note, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def extract_todos(
    session_dir: Path,
    cfg: LlmConfig,
    *,
    person: str | None = None,
    runner: Runner | None = None,
) -> tuple[list[TodoItem], int, str]:
    """Run the extraction, validate against the transcript, write ``todo.md``/``todo.json``.

    Returns ``(kept items, number of dropped items, transcript kind)``. With ``person``
    only that owner's tasks are written (the extraction itself always covers everyone).
    """
    segments, kind = load_transcript(session_dir)
    if not segments:
        raise LlmError("the session has no transcript text")
    reply = run_claude(load_prompt("todo", cfg.language), render_for_llm(segments), cfg, runner)
    items, dropped = filter_todos(parse_json_array(reply), segments)
    if person:
        items = filter_person(items, person)
    session_dir = Path(session_dir)
    title = f"To-do: {person}" if person else "To-do"
    atomic_write_text(session_dir / TODO_MD_NAME, render_todo_md(items, title, text_note(kind)))
    atomic_write_text(
        session_dir / TODO_JSON_NAME,
        json.dumps([i.as_dict() for i in items], indent=2, ensure_ascii=False) + "\n",
    )
    return items, len(dropped), kind


# --------------------------------------------------------------------------- fix


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def validate_edit(edit: Any, text_by_id: dict[int, str], max_edit: float) -> tuple[bool, str]:
    """Is ``{id, old, new}`` a safe edit of the current text of its line? ``(ok, reason)``."""
    if not isinstance(edit, dict):
        return False, "not an object"
    try:
        seg_id = int(edit["id"])
    except (KeyError, TypeError, ValueError):
        return False, "bad id"
    old, new = edit.get("old"), edit.get("new")
    if seg_id not in text_by_id:
        return False, "unknown id"
    if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
        return False, "empty old/new"
    if old == new:
        return False, "no change"
    text = text_by_id[seg_id]
    if old not in text:
        return False, "old not in line"
    if edit_distance(old, new) / max(len(text), 1) > max_edit:
        return False, "edit too large"
    return True, ""


def apply_edits(
    segments: list[Segment], edits: list[Any], max_edit: float
) -> tuple[list[Segment], dict[str, Any]]:
    """Apply the valid edits (first occurrence of ``old``); a changed line loses ``words``."""
    texts = {s.seq: s.text for s in segments}
    changed: set[int] = set()
    rejected: list[dict[str, Any]] = []
    applied = 0
    for edit in edits:
        ok, reason = validate_edit(edit, texts, max_edit)
        if not ok:
            rejected.append({"reason": reason, "edit": edit if isinstance(edit, dict) else None})
            continue
        seg_id = int(edit["id"])
        texts[seg_id] = texts[seg_id].replace(edit["old"], edit["new"], 1)
        changed.add(seg_id)
        applied += 1
    out = [
        replace(s, text=texts[s.seq], words=None) if s.seq in changed else s for s in segments
    ]
    return out, {"proposed": len(edits), "applied": applied, "rejected": rejected}


def llm_fix(
    segments: list[Segment], cfg: LlmConfig, runner: Runner | None = None
) -> tuple[list[Segment], dict[str, Any]]:
    """Ask the model for obvious misrecognition fixes in batches and apply the safe ones.

    A failed batch is reported and skipped; the transcript is never made worse by it.
    """
    prompt = load_prompt("fix", cfg.language)
    batches: list[list[Segment]] = [[]]
    size = 0
    for s in segments:
        if size and size + len(s.text) > FIX_BATCH_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(s)
        size += len(s.text)
    current = list(segments)
    report: dict[str, Any] = {"proposed": 0, "applied": 0, "rejected": [], "failed_batches": 0}
    for n, batch in enumerate(batches):
        text = "\n".join(f"[{s.seq}] {s.text}" for s in batch)
        try:
            edits = parse_json_array(run_claude(prompt, text, cfg, runner))
        except LlmAuthError as exc:  # every other batch would fail the same way
            log.warning("llm-fix stopped: %s", exc)
            report["failed_batches"] += len(batches) - n
            break
        except LlmError as exc:
            log.warning("llm-fix batch failed: %s", exc)
            report["failed_batches"] += 1
            continue
        current, sub = apply_edits(current, edits, cfg.fix_max_edit)
        report["proposed"] += sub["proposed"]
        report["applied"] += sub["applied"]
        report["rejected"] += sub["rejected"]
    return current, report
