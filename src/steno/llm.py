"""Summary, to-do list and transcript fixes through the Claude Code CLI (text only).

Steno never holds an API key: ``claude -p`` runs with the user's own Claude Code login.
Only transcript *text* is sent (on stdin, never in argv); audio and voiceprints never
leave the machine. The CLI is started in an empty temporary directory with all tools
switched off and no session saved, so it cannot read or change files.

Three features, each with its prompt in ``steno/prompts/*.md``:

* ``summarize``  -> ``summary.md`` (overview, decisions, open questions)
* ``extract_todos`` -> ``todo.json`` / ``todo.md``: explicit commitments only, taken from the
  transcript (never from the summary). An item whose ``evidence_quote`` is not found
  verbatim (after normalising case, punctuation and whitespace) in the transcript is dropped.
* ``llm_fix`` (``steno finalize --llm-fix``): the model returns diffs ``{id, old, new}``;
  an edit is applied only when ``old`` occurs in that line and the change is small.

The final transcript is used when it exists, else the live one with current speaker names.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
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


# --------------------------------------------------------------------------- the CLI


def load_prompt(name: str, language: str = "sv") -> str:
    text = (resources.files("steno") / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    return text.replace("{language}", language_phrase(language))


def language_phrase(code: str) -> str:
    code = code.strip().lower()
    if code in ("", "auto"):
        return "the language the meeting is held in (the language of the transcript)"
    return LANGUAGE_NAMES.get(code, code)


def build_command(cfg: LlmConfig, system_prompt: str) -> list[str]:
    exe = shutil.which(cfg.command) or cfg.command
    cmd = [exe, "-p", "--model", cfg.model, "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--disable-slash-commands",
           "--system-prompt", system_prompt]
    if cfg.effort:
        cmd += ["--effort", cfg.effort]
    return cmd


def run_claude(
    system_prompt: str, user_text: str, cfg: LlmConfig, runner: Runner = subprocess.run
) -> str:
    """One ``claude -p`` call: instructions as system prompt, the transcript on stdin."""
    cmd = build_command(cfg, system_prompt)
    try:
        with tempfile.TemporaryDirectory(prefix="steno-llm-") as empty:
            proc = runner(
                cmd, input=user_text, capture_output=True, text=True, encoding="utf-8",
                timeout=cfg.timeout_s, cwd=empty, check=False,
            )
    except FileNotFoundError as exc:
        raise LlmError(
            f"cannot run '{cfg.command}': install Claude Code and log in, or set [llm] command"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LlmError(f"the Claude CLI did not answer within {cfg.timeout_s} s") from exc
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = (proc.stderr or out).strip().splitlines()[-1:] or ["no output"]
        raise LlmError(f"the Claude CLI failed (exit {proc.returncode}): {detail[0][:300]}")
    try:
        data = json.loads(out)
    except ValueError:
        return out  # plain text: older CLI or a different output format
    if isinstance(data, dict):
        if data.get("is_error"):
            raise LlmError(f"the Claude CLI reported an error: {str(data.get('result'))[:300]}")
        if isinstance(data.get("result"), str):
            return data["result"].strip()
    raise LlmError("unexpected output from the Claude CLI")


# --------------------------------------------------------------------------- transcript


def load_transcript(session_dir: Path) -> tuple[list[Segment], str]:
    """Segments for the LLM and which transcript they are (``"final"`` or ``"live"``)."""
    session_dir = Path(session_dir)
    from steno.finalize import has_final, read_final

    if has_final(session_dir):
        segments = read_final(session_dir)
        if segments:
            return segments, "final"
    return labels.resolve_segments(session_dir, transcript.read_segments(session_dir)), "live"


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
    session_dir: Path, cfg: LlmConfig, runner: Runner = subprocess.run
) -> tuple[Path, str]:
    """Write ``summary.md``; returns its path and the transcript kind that was used."""
    segments, kind = load_transcript(session_dir)
    if not segments:
        raise LlmError("the session has no transcript text")
    reply = run_claude(load_prompt("summary", cfg.language), render_for_llm(segments), cfg, runner)
    if not reply.strip():
        raise LlmError("the model returned an empty summary")
    path = Path(session_dir) / SUMMARY_NAME
    atomic_write_text(path, reply.strip() + "\n")
    return path, kind


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
    joined = ""
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
        pos = joined.find(quote) if quote else -1
        if pos < 0:
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


def render_todo_md(items: list[TodoItem], title: str = "To-do") -> str:
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
    return "\n".join(lines).rstrip("\n") + "\n"


def extract_todos(
    session_dir: Path,
    cfg: LlmConfig,
    *,
    person: str | None = None,
    runner: Runner = subprocess.run,
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
    atomic_write_text(session_dir / TODO_MD_NAME, render_todo_md(items, title))
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
    segments: list[Segment], cfg: LlmConfig, runner: Runner = subprocess.run
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
    for batch in batches:
        text = "\n".join(f"[{s.seq}] {s.text}" for s in batch)
        try:
            edits = parse_json_array(run_claude(prompt, text, cfg, runner))
        except LlmError as exc:
            log.warning("llm-fix batch failed: %s", exc)
            report["failed_batches"] += 1
            continue
        current, sub = apply_edits(current, edits, cfg.fix_max_edit)
        report["proposed"] += sub["proposed"]
        report["applied"] += sub["applied"]
        report["rejected"] += sub["rejected"]
    return current, report
