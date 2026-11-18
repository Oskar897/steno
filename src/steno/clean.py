"""``steno clean``: minimal, validated corrections of a transcript by Claude (text only).

The best available text of a session (``final.jsonl`` when it exists, else the live
transcript with current speaker names, see ``llm.load_source_transcript``) is sent to the
Claude CLI in chunks of whole lines, ``[id] Speaker: text``. The model answers with diffs
``{id, old, new}`` (``prompts/clean.md``) and nothing else; every diff is validated here
before it is applied, so a model that paraphrases, drops content or changes a number can
only make its edit be rejected, never make the transcript worse.

Rules enforced per edit (the reason is recorded when one fails):

* the line id must be one of the chunk's lines, ``old`` must occur in that line as whole
  words (the first occurrence is replaced) and ``new`` must differ from it;
* an edit that only changes case or punctuation is always fine, any other edit may change at
  most ``[llm] fix_max_edit`` of the line (Levenshtein on the case-folded text, line length
  counted as at least 30 characters, also summed over all edits of the line) and must not
  replace a word by something unrelated (edit distance below 85 % of the longer side);
* numbers, digit tokens, weekdays, months and yes/no/negation words must be identical in
  ``old`` and ``new``; a capitalised word in ``old`` that is not at the start of a
  sentence (a name) must come back as a similar word, and a capitalised word that appears in
  ``new`` must be similar to a word in ``old`` or already occur in the transcript (so a
  mis-hearing can be corrected towards a name, but no new name can be invented);
* words may only disappear when a word is repeated directly after itself (stutter), or
  when words are merged into a similar-looking one ("over fit" -> "overfit"); adding more
  than two words is refused.

Nothing of ``transcript.jsonl`` or ``final.jsonl`` is changed. The result is ``clean.jsonl``
(same line format, ``seq`` of the source text, ``words`` dropped on changed lines),
``clean.md`` and ``clean.json`` (counts and the edits). ``llm.load_transcript`` (summary and
to-do) uses ``clean.jsonl`` while it still matches its source text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from steno import llm
from steno.config import LlmConfig
from steno.llm import LlmError, Runner
from steno.transcript import Segment, atomic_write_text, format_line, read_segments

log = logging.getLogger(__name__)

CLEAN_NAME = "clean.jsonl"
CLEAN_MARKDOWN_NAME = "clean.md"
CLEAN_META_NAME = "clean.json"

#: Largest chunk sent in one call (characters of rendered lines; a longer line is alone).
CHUNK_CHARS = 6000
#: The line length used for the edit budget is at least this, so a short line can be fixed.
MIN_LINE_BUDGET = 30
#: Largest share of the longer of ``old``/``new`` that may differ in a substantive edit.
MAX_TOKEN_CHANGE = 0.85
#: Words that count as "the same word" for the name checks (share of the longer word).
NAME_SIMILARITY = 0.5
#: Words merged into fewer ("over fit" -> "overfit") must keep the letters (share that differs).
MERGE_SIMILARITY = 0.25
MAX_ADDED_WORDS = 2

Progress = Callable[[str, int, int], None]

_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*|\d+(?:[.,:]\d+)*", re.UNICODE)
_LINE_ID_RE = re.compile(r"^\[?\s*(\d+)\s*\]?$")

#: Tokens that must not change (compared as a multiset, case-folded, besides digit tokens).
GUARDED_WORDS = frozenset("""
noll två tre fyra fem sex sju åtta nio tio elva tolv tretton fjorton femton sexton
sjutton arton nitton tjugo trettio fyrtio femtio sextio sjuttio åttio nittio hundra tusen
miljon miljoner
zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen
fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty
ninety hundred thousand million
måndag tisdag onsdag torsdag fredag lördag söndag
monday tuesday wednesday thursday friday saturday sunday
januari februari mars april maj juni juli augusti september oktober november december
january february march may june july august october
ja nej inte aldrig ingen inget inga
yes no not never none
""".split())


# --------------------------------------------------------------------------- source text


def source_digest(segments: list[Segment]) -> str:
    """Fingerprint of the ``seq`` and text of the source lines (speaker names excluded)."""
    h = hashlib.sha256()
    for s in segments:
        h.update(f"{s.seq}\t{s.text}\n".encode())
    return h.hexdigest()


def load_source(session_dir: Path) -> tuple[list[Segment], str]:
    """The text that gets cleaned and which transcript it is (one place to extend)."""
    return llm.load_source_transcript(session_dir)


# --------------------------------------------------------------------------- chunking


def render_line(seg: Segment) -> str:
    return f"[{seg.seq}] {seg.speaker}: {seg.text}"


def chunk_segments(
    segments: list[Segment], max_chars: int | None = None
) -> list[list[Segment]]:
    """Consecutive whole lines, each chunk at most ``max_chars`` rendered (a long line alone)."""
    max_chars = max_chars or CHUNK_CHARS
    chunks: list[list[Segment]] = []
    size = 0
    for s in segments:
        n = len(render_line(s)) + 1
        if not chunks or (size and size + n > max_chars):
            chunks.append([])
            size = 0
        chunks[-1].append(s)
        size += n
    return chunks


# --------------------------------------------------------------------------- validation


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _is_name(token: str) -> bool:
    """Capitalised, rest lower case ("Stockholm"): not an acronym, not a camel-case term."""
    return len(token) > 1 and token[0].isupper() and token[1:].islower()


def _guarded(text: str) -> Counter[str]:
    return Counter(
        w.casefold() for w in _words(text) if w[0].isdigit() or w.casefold() in GUARDED_WORDS
    )


def _close(a: str, b: str, limit: float = NAME_SIMILARITY) -> bool:
    a, b = a.casefold(), b.casefold()
    return llm.edit_distance(a, b) <= limit * max(len(a), len(b), 1)


def _name_tokens(text: str, starts_sentence: bool) -> list[str]:
    """Capitalised words of ``text`` that are not the first word of a sentence."""
    out = []
    for m in _WORD_RE.finditer(text):
        before = text[: m.start()].rstrip()
        if (not before and starts_sentence) or (before and before[-1] in ".!?"):
            continue
        if _is_name(m.group()):
            out.append(m.group())
    return out


def _pattern(old: str) -> re.Pattern[str]:
    lead = r"(?<!\w)" if old[0].isalnum() or old[0] == "_" else ""
    trail = r"(?!\w)" if old[-1].isalnum() or old[-1] == "_" else ""
    return re.compile(lead + re.escape(old) + trail)


def _collapse_repeats(words: list[str]) -> list[str]:
    out: list[str] = []
    for w in words:
        if not out or out[-1] != w:
            out.append(w)
    return out


def parse_edit_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = _LINE_ID_RE.match(value)
        return int(m.group(1)) if m else None
    return None


def validate_edit(
    edit: Any,
    current: dict[int, str],
    original: dict[int, str],
    known_words: set[str],
    max_edit: float,
) -> tuple[bool, str]:
    """Is ``{id, old, new}`` a safe edit of the current text of its line? ``(ok, reason)``.

    ``current`` holds the text with the earlier edits applied (and only the lines this edit
    may address), ``original`` the source text, ``known_words`` the case-folded words that
    occur in the source transcript.
    """
    if not isinstance(edit, dict):
        return False, "not an object"
    seg_id = parse_edit_id(edit.get("id"))
    if seg_id is None:
        return False, "bad id"
    if seg_id not in current:
        return False, "unknown id"
    old, new = edit.get("old"), edit.get("new")
    if not isinstance(old, str) or not isinstance(new, str) or not old.strip() or not new.strip():
        return False, "empty old/new"
    if "\n" in new or "\r" in new:
        return False, "new spans lines"
    if old == new:
        return False, "no change"
    text = current[seg_id]
    m = _pattern(old).search(text)
    if m is None:
        return False, "old not in line"
    if _guarded(old) != _guarded(new):
        return False, "changes a number or a guarded word"

    old_words, new_words = _words(old), _words(new)
    old_cf = [w.casefold() for w in old_words]
    new_cf = [w.casefold() for w in new_words]
    if len(new_words) > len(old_words) + MAX_ADDED_WORDS:
        return False, "adds content"
    if len(new_words) < len(old_words) and _collapse_repeats(old_cf) != new_cf and not _close(
        "".join(old_cf), "".join(new_cf), MERGE_SIMILARITY
    ):
        return False, "removes content"

    starts = not text[: m.start()].strip() or text[: m.start()].rstrip()[-1:] in (".", "!", "?")
    new_names = _name_tokens(new, starts)
    for name in _name_tokens(old, starts):
        if not any(_close(name, w) for w in new_words):
            return False, "changes a name"
    for name in new_names:
        if name.casefold() not in known_words and not any(_close(name, w) for w in old_words):
            return False, "introduces a name"

    if _normalise(old) == _normalise(new):  # case and punctuation only
        return True, ""
    d = llm.edit_distance(old.casefold(), new.casefold())
    if d > MAX_TOKEN_CHANGE * max(len(old), len(new)):
        return False, "replaces a word with something unrelated"
    budget = max_edit * max(len(original[seg_id]), MIN_LINE_BUDGET)
    if d > budget:
        return False, "edit too large"
    after = text[: m.start()] + new + text[m.end():]
    if llm.edit_distance(original[seg_id].casefold(), after.casefold()) > budget:
        return False, "line changed too much"
    return True, ""


def _normalise(text: str) -> str:
    return " ".join(w.casefold() for w in _words(text))


def apply_edits(
    chunk_ids: set[int],
    texts: dict[int, str],
    original: dict[int, str],
    known_words: set[str],
    edits: list[Any],
    max_edit: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate and apply ``edits`` to ``texts`` (changed in place); ``(applied, rejected)``."""
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for edit in edits:
        view = {i: texts[i] for i in chunk_ids}
        ok, reason = validate_edit(edit, view, original, known_words, max_edit)
        if not ok:
            item = {"reason": reason}
            if isinstance(edit, dict):
                item.update(seq=parse_edit_id(edit.get("id")), old=edit.get("old"),
                            new=edit.get("new"))
            rejected.append(item)
            continue
        seg_id = parse_edit_id(edit["id"])
        assert seg_id is not None
        text = texts[seg_id]
        m = _pattern(edit["old"]).search(text)
        assert m is not None
        texts[seg_id] = text[: m.start()] + edit["new"] + text[m.end():]
        applied.append({"seq": seg_id, "old": edit["old"], "new": edit["new"]})
    return applied, rejected


# --------------------------------------------------------------------------- the run


@dataclass
class CleanResult:
    segments: list[Segment]
    meta: dict[str, Any]
    written: bool


def clean_session(
    session_dir: Path,
    cfg: LlmConfig,
    *,
    dry_run: bool = False,
    runner: Runner | None = None,
    progress: Progress | None = None,
) -> CleanResult:
    """Clean the session's best text; write ``clean.*`` unless ``dry_run``.

    A failed chunk is counted and skipped (its lines stay as they are); when every chunk
    fails the error is raised and nothing is written.
    """
    session_dir = Path(session_dir)
    segments, source = load_source(session_dir)
    if not segments:
        raise LlmError("the session has no transcript text")
    started = time.monotonic()
    prompt = llm.load_prompt("clean", cfg.language)
    original = {s.seq: s.text for s in segments}
    texts = dict(original)
    known = {w.casefold() for t in original.values() for w in _words(t)}
    chunks = chunk_segments(segments)
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    proposed = 0
    failed = 0
    last_error: LlmError | None = None
    for i, chunk in enumerate(chunks):
        if progress:
            progress("waiting for Claude", i, len(chunks))
        try:
            edits = llm.parse_json_array(
                llm.run_claude(prompt, "\n".join(render_line(s) for s in chunk), cfg, runner))
        except LlmError as exc:
            log.warning("clean chunk %d/%d failed: %s", i + 1, len(chunks), exc)
            failed += 1
            last_error = exc
            continue
        ok, bad = apply_edits(
            {s.seq for s in chunk}, texts, original, known, edits, cfg.fix_max_edit)
        proposed += len(edits)
        applied += ok
        rejected += bad
    if progress:
        progress("waiting for Claude", len(chunks), len(chunks))
    if failed == len(chunks) and last_error is not None:
        raise last_error
    changed = {seq for seq, text in texts.items() if text != original[seq]}
    out = [replace(s, text=texts[s.seq], words=None) if s.seq in changed else s
           for s in segments]
    reasons = Counter(r["reason"] for r in rejected)
    meta: dict[str, Any] = {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": cfg.model,
        "effort": cfg.effort,
        "source": source,
        "source_sha256": source_digest(segments),
        "lines": len(segments),
        "changed_lines": len(changed),
        "chunks": len(chunks),
        "failed_chunks": failed,
        "proposed": proposed,
        "applied": len(applied),
        "rejected": len(rejected),
        "rejected_by_reason": dict(sorted(reasons.items())),
        "seconds": round(time.monotonic() - started, 1),
        "edits": applied,
        "rejected_edits": rejected,
    }
    if not dry_run:
        write_clean(session_dir, out, meta)
    return CleanResult(out, meta, not dry_run)


def write_clean(session_dir: Path, segments: list[Segment], meta: dict[str, Any]) -> None:
    atomic_write_text(session_dir / CLEAN_NAME, "".join(s.to_json() + "\n" for s in segments))
    atomic_write_text(
        session_dir / CLEAN_MARKDOWN_NAME, "".join(format_line(s) + "\n\n" for s in segments))
    atomic_write_text(
        session_dir / CLEAN_META_NAME, json.dumps(meta, indent=2, ensure_ascii=False) + "\n")


def summary_line(meta: dict[str, Any]) -> str:
    text = (f"{meta['applied']} of {meta['proposed']} edit(s) applied to "
            f"{meta['changed_lines']} of {meta['lines']} line(s), {meta['rejected']} rejected")
    if meta["failed_chunks"]:
        text += f", {meta['failed_chunks']} of {meta['chunks']} chunk(s) failed"
    return text


# --------------------------------------------------------------------------- reading it back


def load_meta(session_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(session_dir) / CLEAN_META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clean_overlay(session_dir: Path, source: list[Segment]) -> list[Segment] | None:
    """``source`` with the cleaned text, or None when there is no clean text or it is stale.

    Stale = the source text (``seq`` and text) is no longer the one that was cleaned, for
    example after a new ``steno finalize``. Speaker names always come from ``source``.
    """
    meta = load_meta(session_dir)
    if not meta or meta.get("source_sha256") != source_digest(source):
        return None
    cleaned = {s.seq: s for s in read_segments(session_dir, name=CLEAN_NAME)}
    if not cleaned:
        return None
    return [
        replace(s, text=c.text, words=None) if (c := cleaned.get(s.seq)) and c.text != s.text
        else s
        for s in source
    ]


def status(session_dir: Path, source: list[Segment] | None = None) -> dict[str, Any] | None:
    """Counts of the last run for the UI, or None; ``fresh`` = still matches its source."""
    meta = load_meta(session_dir)
    if meta is None or not (Path(session_dir) / CLEAN_NAME).is_file():
        return None
    if source is None:
        source, _ = load_source(session_dir)
    return {
        "fresh": meta.get("source_sha256") == source_digest(source),
        "source": meta.get("source"), "model": meta.get("model"),
        "created": meta.get("created"), "applied": meta.get("applied", 0),
        "proposed": meta.get("proposed", 0), "rejected": meta.get("rejected", 0),
        "changed_lines": meta.get("changed_lines", 0), "lines": meta.get("lines", 0),
        "failed_chunks": meta.get("failed_chunks", 0),
    }
