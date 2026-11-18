"""Benchmark local speech-to-text models on this machine (CPU only).

Each candidate runs in its own subprocess so peak RSS is clean and a crash does
not kill the whole run. Results (JSON + hypothesis texts) are written to the
Steno data dir, never into the repository.

Usage:
    uv run python scripts/benchmark_asr.py --audio talk.wav --reference talk.txt
    uv run python scripts/benchmark_asr.py --dataset fleurs --n 60 --n-en 30
    uv run python scripts/benchmark_asr.py --models kb-whisper-small,kb-whisper-small@5 ...
    uv run python scripts/benchmark_asr.py --self-test
    uv run python scripts/benchmark_asr.py --dataset local:codeswitch --llm-fix \
        --models kb-whisper-small,kb-whisper-small+prompt,whisper-large-v3-turbo:auto

Model spec grammar: `key[@beam][:language][+prompt][+hot]`, e.g. `whisper-large-v3-turbo:sv+prompt`
(`:language` = sv | en | auto, `+prompt` = Whisper `initial_prompt`, `+hot` = faster-whisper
`hotwords`; Whisper models only).

Settings that affect the numbers (also stored in results.json):
    * Whisper models: beam_size=1 unless `model@N` is given (greedy, for speed),
      vad_filter=True, condition_on_previous_text=False, int8 compute type.
    * RTF = transcription wall time / audio duration (load and download excluded).
    * `--dataset local:<name>` scores your own recordings (<data_dir>/eval/<name>/refs.tsv +
      NN.wav) and adds an "English-term accuracy" column (see `score_english_terms`).
    * kb-whisper CTranslate2 weights live in `model.bin` on the `main` branch.
      The `standard`/`subtitle`/`strict` tags only contain safetensors (no CT2).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from steno.config import DEFAULT_INITIAL_PROMPT

SAMPLE_RATE = 16_000
BEAM_SIZE = 1
RTF_LIMIT = 0.5  # realtime headroom for live use while a call app is running
PARAKEET_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
)
PARAKEET_DIRNAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
PARAKEET_MAX_CHUNK_S = 25.0
QWEN3_DIRNAME = "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
QWEN3_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2"
)
FLEURS_BASE = "https://huggingface.co/datasets/google/fleurs/resolve/main/data"
FLEURS_LICENSE = "CC-BY-4.0 (Google FLEURS, https://huggingface.co/datasets/google/fleurs)"
# dataset key -> (FLEURS config, language passed to whisper models)
DATASETS = {"fleurs-sv": ("sv_se", "sv"), "fleurs-en": ("en_us", "en")}


@dataclass(frozen=True)
class ModelSpec:
    key: str
    kind: str  # "whisper" | "sherpa" (NeMo transducer) | "sherpa-qwen3"
    source: str  # HF repo id, or download URL
    languages: tuple[str, ...]  # whisper passes; "auto" = language detection


MODELS: dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec("kb-whisper-base", "whisper", "KBLab/kb-whisper-base", ("sv",)),
        ModelSpec("kb-whisper-small", "whisper", "KBLab/kb-whisper-small", ("sv", "auto")),
        ModelSpec("kb-whisper-medium", "whisper", "KBLab/kb-whisper-medium", ("sv", "auto")),
        ModelSpec("kb-whisper-large", "whisper", "KBLab/kb-whisper-large", ("sv", "auto")),
        ModelSpec("whisper-small", "whisper", "Systran/faster-whisper-small", ("sv", "auto")),
        ModelSpec(
            "parakeet-tdt-0.6b-v3", "sherpa", PARAKEET_URL, ("auto",)
        ),  # multilingual (25 EU languages incl. Swedish), auto language
        ModelSpec(
            "whisper-large-v3-turbo",
            "whisper",
            "deepdml/faster-whisper-large-v3-turbo-ct2",
            ("sv",),
        ),
        ModelSpec("qwen3-asr-0.6b", "sherpa-qwen3", QWEN3_URL, ("auto",)),  # 30 languages incl. sv
    )
}
SELF_TEST_MODEL = "kb-whisper-base"


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested in tests/test_benchmark_utils.py)
# --------------------------------------------------------------------------- #


def normalize_text(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace. Keeps å, ä, ö and digits."""
    text = unicodedata.normalize("NFC", text).lower()
    text = re.sub(r"['’‘`]", "", text)  # apostrophes: "don't" -> "dont"
    text = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def error_rates(reference: str, hypothesis: str) -> tuple[float, float]:
    """Return (WER, CER) after normalization. Empty hypothesis counts as 100 % errors."""
    import jiwer

    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    if not ref:
        raise ValueError("reference is empty after normalization")
    if not hyp:
        return 1.0, 1.0
    return float(jiwer.wer(ref, hyp)), float(jiwer.cer(ref, hyp))


def corpus_error_rates(references: list[str], hypotheses: list[str]) -> tuple[float, float]:
    """Corpus-level (WER, CER): errors summed over all utterances / total reference length."""
    import jiwer

    if len(references) != len(hypotheses):
        raise ValueError("references and hypotheses differ in length")
    refs = [normalize_text(r) for r in references]
    hyps = [normalize_text(h) for h in hypotheses]
    pairs = [(r, h) for r, h in zip(refs, hyps, strict=True) if r]  # drop empty references
    if not pairs:
        raise ValueError("all references are empty after normalization")
    refs, hyps = [r for r, _ in pairs], [h for _, h in pairs]
    return (
        float(jiwer.process_words(refs, hyps).wer),
        float(jiwer.process_characters(refs, hyps).cer),
    )


def parse_model_arg(text: str) -> tuple[str, int | None]:
    """'kb-whisper-small@5' -> ('kb-whisper-small', 5); no suffix -> beam None (default)."""
    key, sep, beam = text.strip().partition("@")
    if not sep:
        return key, None
    if not beam.isdigit() or int(beam) < 1:
        raise ValueError(f"invalid beam size in {text!r}")
    return key, int(beam)


def run_label(key: str, beam: int | None) -> str:
    return key if beam is None else f"{key}@{beam}"


# Generic, deliberately NOT taken from scripts/codeswitch_sentences.txt (no leakage into the eval):
# the same prompt Steno uses by default (steno.config.DEFAULT_INITIAL_PROMPT), so the benchmark
# measures what the product does.
INITIAL_PROMPT = DEFAULT_INITIAL_PROMPT
# faster-whisper `hotwords`: prepended to the decoder context as a bare word list.
HOTWORDS = "thesis pipeline GitHub Docker Python bug commit workshop laptop server paper"


@dataclass(frozen=True)
class RunSpec:
    """One strategy to benchmark: model + decoding options."""

    key: str
    beam: int | None = None
    language: str | None = None  # "sv" | "en" | "auto"...; None = the dataset's own language
    prompt: bool = False  # Whisper initial_prompt
    hotwords: bool = False  # faster-whisper hotwords

    @property
    def label(self) -> str:
        label = run_label(self.key, self.beam)
        if self.language:
            label += f":{self.language}"
        if self.prompt:
            label += "+prompt"
        if self.hotwords:
            label += "+hot"
        return label


def parse_run_spec(text: str) -> RunSpec:
    """'whisper-large-v3-turbo@5:sv+prompt' -> RunSpec; order is key[@beam][:lang][+flag]..."""
    base, *flags = text.strip().split("+")
    unknown = [f for f in flags if f not in ("prompt", "hot")]
    if unknown:
        raise ValueError(f"unknown option +{unknown[0]} in {text!r} (use +prompt or +hot)")
    base, colon, language = base.partition(":")
    if colon and not re.fullmatch(r"auto|[a-z]{2,3}", language):
        raise ValueError(f"invalid language in {text!r}")
    key, beam = parse_model_arg(base)
    return RunSpec(key, beam, language or None, "prompt" in flags, "hot" in flags)


# --------------------------------------------------------------------------- #
# Local datasets: <data_dir>/eval/<name>/refs.tsv + NN.wav (see scripts/record_eval.py)
# --------------------------------------------------------------------------- #


def parse_refs_tsv(text: str) -> list[tuple[str, str]]:
    """refs.tsv lines `ID<TAB>reference` -> [(id, reference)] in file order. Blank lines,
    `#` comments and lines without a reference are skipped."""
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ident, _, ref = line.partition("\t")
        ident, ref = ident.strip(), ref.strip()
        if ident and ref:
            rows.append((ident, ref))
    return rows


def load_local_dataset(name: str, root: Path) -> tuple[list[dict], list[str]]:
    """Items {"id", "audio", "text"} for every refs.tsv row that has root/<name>/<id>.wav, and
    the ids whose wav is missing (recording may be in progress)."""
    if not name or Path(name).name != name or name in (".", ".."):
        raise ValueError(f"invalid local dataset name {name!r}")
    ds_dir = root / name
    tsv = ds_dir / "refs.tsv"
    if not tsv.is_file():
        raise FileNotFoundError(f"{tsv} not found")
    items, missing = [], []
    for ident, ref in parse_refs_tsv(tsv.read_text(encoding="utf-8")):
        wav = ds_dir / f"{safe_filename(ident)}.wav"
        if wav.is_file():
            items.append({"id": ident, "audio": str(wav), "text": ref})
        else:
            missing.append(ident)
    return items, missing


# --------------------------------------------------------------------------- #
# English-term accuracy (code-switching metric)
#
# Heuristic, documented:
#  * Tokens: lowercase, apostrophes dropped, "&" -> "and", a colon inside a word is joined
#    ("API:et" -> "apiet"), other punctuation splits; å/ä/ö/digits kept.
#  * In a reference sentence with >= 3 unambiguous English function words (EN_STOPWORDS) the
#    whole sentence is English: every token is a unit that must appear exactly in the hypothesis
#    (bucket "en").
#  * Otherwise the units are the ENGLISH_TERMS entries found in it (bucket "cs"). An entry is a
#    token or a multi-word phrase; "a|b" lists accepted spellings of one token. The last token may
#    carry a Swedish suffix (SV_SUFFIXES: "branchen", "API:et", "merga", "testerna"); the same
#    leniency applies to the hypothesis, so "branches"/"branchen" both count as "branch"
#    but a phonetic "brantch" does not.
#  * A unit is a hit when the hypothesis has a matching, not yet used, token span (a repeated
#    unit must be present as often). Accuracy = hits / units, summed over the corpus.
# --------------------------------------------------------------------------- #

SV_SUFFIXES = frozenset(
    "n en et t s es er ar or a ad d de ade at as na erna arna orna ens ets ns ed ted "
    "ing ning ningen ningar ande".split()
)

# Curated: tech / university terms of the kind a Swedish student leaves in English.
# fmt: off
ENGLISH_TERMS: tuple[str, ...] = (
    "pull request", "unit test", "integration test", "status code", "research question",
    "literature review", "peer review", "data augmentation", "early stopping",
    "sprint planning", "action item", "speech recognition", "q and a", "code review",
    "push", "branch", "deadline", "approach", "baseline", "screen", "dashboard", "issue",
    "framework", "workaround", "supervisor", "source", "merg|merge", "main",
    "overfit|overfitt", "dropout", "standup", "team", "dataset", "api", "token", "expire",
    "methodology", "frontend", "backend", "break", "feedback", "abstract", "deploy",
    "production", "slides", "canvas", "bottleneck", "latency", "repository",
    "commit", "pipeline", "thesis", "bug", "docker", "github", "laptop", "workshop",
    "paper", "feature", "server", "script", "cloud", "meeting", "link",
)
# fmt: on
EN_STOPWORDS = frozenset(
    "the of to and we you is are that this with could would should me my our it before after "
    "through think send go up lets for on at have".split()
)
MIN_EN_STOPWORDS = 3

_Unit = tuple[tuple[str, ...], ...]  # one tuple of accepted spellings per token


def term_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text).lower()
    text = re.sub(r"['’‘`]", "", text)
    text = text.replace("&", " and ")
    text = re.sub(r"(?<=\w):(?=\w)", "", text)  # "api:et" -> "apiet"
    text = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)
    return text.split()


def compile_lexicon(entries: tuple[str, ...] = ENGLISH_TERMS) -> list[_Unit]:
    """Entries -> units, longest phrases first (so 'pull request' wins over a shorter match)."""
    units = [
        tuple(tuple(tok.split("|")) for tok in e.lower().split()) for e in entries if e.strip()
    ]
    return sorted(units, key=len, reverse=True)


_LEXICON = compile_lexicon()


def _token_matches(token: str, spellings: tuple[str, ...], suffix_ok: bool) -> bool:
    return any(
        token == s or (suffix_ok and token.startswith(s) and token[len(s) :] in SV_SUFFIXES)
        for s in spellings
    )


def _unit_at(tokens: list[str], i: int, unit: _Unit, lenient: bool) -> bool:
    if i + len(unit) > len(tokens):
        return False
    last = len(unit) - 1
    return all(
        _token_matches(tokens[i + j], alts, lenient and j == last) for j, alts in enumerate(unit)
    )


def is_english_sentence(tokens: list[str]) -> bool:
    return sum(t in EN_STOPWORDS for t in tokens) >= MIN_EN_STOPWORDS


def english_units(reference: str, lexicon: list[_Unit] | None = None) -> tuple[str, list[_Unit]]:
    """(bucket, units) of a reference: "en" = whole English sentence, else "cs" lexicon terms."""
    tokens = term_tokens(reference)
    if is_english_sentence(tokens):
        return "en", [((t,),) for t in tokens]
    lexicon = _LEXICON if lexicon is None else lexicon
    found: list[_Unit] = []
    i = 0
    while i < len(tokens):
        for unit in lexicon:
            if _unit_at(tokens, i, unit, True):
                found.append(unit)
                i += len(unit)
                break
        else:
            i += 1
    return "cs", found


def score_english_terms(
    reference: str, hypothesis: str, lexicon: list[_Unit] | None = None
) -> dict[str, tuple[int, int]]:
    """{"cs": (hits, total), "en": (hits, total)} for one utterance (see section comment)."""
    bucket, units = english_units(reference, lexicon)
    hyp = term_tokens(hypothesis)
    used = [False] * len(hyp)
    hits = 0
    for unit in units:
        for i in range(len(hyp) - len(unit) + 1):
            if not any(used[i : i + len(unit)]) and _unit_at(hyp, i, unit, bucket == "cs"):
                used[i : i + len(unit)] = [True] * len(unit)
                hits += 1
                break
    out = {"cs": (0, 0), "en": (0, 0)}
    out[bucket] = (hits, len(units))
    return out


def english_term_accuracy(references: list[str], hypotheses: list[str]) -> dict:
    """Corpus totals: eng_acc over all units, eng_acc_cs / eng_acc_en per bucket (None = no
    units), plus the unit counts."""
    if len(references) != len(hypotheses):
        raise ValueError("references and hypotheses differ in length")
    tot = {"cs": [0, 0], "en": [0, 0]}
    for ref, hyp in zip(references, hypotheses, strict=True):
        for bucket, (hit, total) in score_english_terms(ref, hyp).items():
            tot[bucket][0] += hit
            tot[bucket][1] += total

    def acc(hit: int, total: int) -> float | None:
        return hit / total if total else None

    hit_all, n_all = tot["cs"][0] + tot["en"][0], tot["cs"][1] + tot["en"][1]
    return {
        "eng_acc": acc(hit_all, n_all),
        "eng_acc_cs": acc(*tot["cs"]),
        "eng_acc_en": acc(*tot["en"]),
        "eng_n": n_all,
        "eng_n_cs": tot["cs"][1],
        "eng_n_en": tot["en"][1],
    }


def select_fleurs_rows(
    rows: list[dict], n: int, seed: int = 0, min_s: float = 3.0, max_s: float = 29.0
) -> list[dict]:
    """Pick n utterances with distinct sentences, reproducibly (seeded shuffle, so n=30 is a
    prefix of n=60). Rows need `id`, `text`, `seconds`; duration is bounded to what Whisper
    handles in one window."""
    seen: set[str] = set()
    pool = []
    for r in sorted(rows, key=lambda r: r["id"]):
        key = normalize_text(r["text"])
        if key and key not in seen and min_s <= r["seconds"] <= max_s:
            seen.add(key)
            pool.append(r)
    random.Random(seed).shuffle(pool)
    return pool[:n]


def parse_fleurs_tsv(text: str) -> list[dict]:
    """FLEURS tsv (no header): id, file_name, raw_transcription, transcription, phonemes,
    num_samples, gender. Returns id/file/text/seconds (16 kHz samples -> seconds)."""
    rows = []
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 6 or not cols[5].isdigit():
            continue
        rows.append(
            {
                "id": cols[0],
                "file": cols[1],
                "text": cols[2],
                "seconds": int(cols[5]) / SAMPLE_RATE,
            }
        )
    return rows


def dominant(values: list[str | None]) -> str | None:
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else None


def real_time_factor(proc_seconds: float, audio_seconds: float) -> float:
    return proc_seconds / audio_seconds if audio_seconds > 0 else float("inf")


def sort_rows(rows: list[dict]) -> list[dict]:
    """Sort by WER ascending (failed/no-WER rows last, ordered by RTF)."""

    def key(row: dict):
        failed = row.get("error") is not None
        wer = row.get("wer")
        rtf = row.get("rtf")
        return (
            failed,
            wer is None,
            wer if wer is not None else 0.0,
            rtf if rtf is not None else float("inf"),
        )

    return sorted(rows, key=key)


def recommend(rows: list[dict], rtf_limit: float = RTF_LIMIT) -> tuple[dict | None, str]:
    """Lowest WER among successful rows with RTF < rtf_limit.

    Returns (row, explanation). Needs WER values, i.e. a reference transcript.
    """
    ok = [r for r in rows if r.get("error") is None and r.get("rtf") is not None]
    scored = [r for r in ok if r.get("wer") is not None]
    if not scored:
        return None, "No WER available (run with --reference to get a recommendation)."
    fast = [r for r in scored if r["rtf"] < rtf_limit]
    if fast:
        best = min(fast, key=lambda r: (r["wer"], r["rtf"]))
        return best, f"Lowest WER among runs with RTF < {rtf_limit}."
    best = min(scored, key=lambda r: r["rtf"])
    return best, f"No run has RTF < {rtf_limit}; fastest run shown instead."


def _fmt(value, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def format_table(rows: list[dict]) -> str:
    header = (
        "| run | WER | CER | RTF | load s | transcribe s | peak RSS MB | note |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for r in rows:
        if r.get("error"):
            lines.append(f"| {r['run']} | - | - | - | - | - | - | FAILED: {r['error']} |")
            continue
        wer = None if r.get("wer") is None else r["wer"] * 100
        cer = None if r.get("cer") is None else r["cer"] * 100
        note = f"detected {r['detected_language']}" if r.get("detected_language") else ""
        wer_s = "-" if wer is None else f"{wer:.1f}%"
        cer_s = "-" if cer is None else f"{cer:.1f}%"
        lines.append(
            f"| {r['run']} | {wer_s} | {cer_s} | {_fmt(r['rtf'], '.3f')} "
            f"| {_fmt(r['load_s'], '.1f')} | {_fmt(r['transcribe_s'], '.1f')} "
            f"| {_fmt(r['peak_rss_mb'], '.0f')} | {note} |"
        )
    return header + "\n".join(lines)


def merge_spans(spans: list[tuple[int, int]], max_len: int) -> list[tuple[int, int]]:
    """Merge consecutive (start, end) sample spans while the merged span fits in max_len."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and end - merged[-1][0] <= max_len:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def fixed_spans(total: int, size: int) -> list[tuple[int, int]]:
    return [(i, min(i + size, total)) for i in range(0, total, size)]


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


# --------------------------------------------------------------------------- #
# Worker side (runs in a subprocess, one model per process)
# --------------------------------------------------------------------------- #


class PeakRss:
    """Samples this process' RSS in a thread; reset() starts a new measurement window."""

    def __init__(self, interval: float = 0.05) -> None:
        import psutil

        self._proc = psutil.Process()
        self._interval = interval
        self._peak = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self) -> None:
        rss = self._proc.memory_info().rss
        with self._lock:
            self._peak = max(self._peak, rss)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self._interval)

    def start(self) -> PeakRss:
        self._thread.start()
        return self

    def reset(self) -> None:
        with self._lock:
            self._peak = 0
        self._sample()

    def peak_mb(self) -> float:
        self._sample()
        with self._lock:
            return self._peak / (1024 * 1024)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_audio(path: str):
    """Decode to 16 kHz mono float32 via faster-whisper (PyAV, no ffmpeg needed).

    faster-whisper 1.2.x passes `metadata_errors=` to `av.open`, which newer PyAV releases
    (seen with av 19) reject with TypeError; fall back to an equivalent plain PyAV decode.
    """
    from faster_whisper.audio import decode_audio

    try:
        return decode_audio(path, sampling_rate=SAMPLE_RATE)
    except TypeError:
        import av
        import numpy as np

        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        chunks = []
        with av.open(path, mode="r") as container:
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
        pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
        return pcm.astype(np.float32) / 32768.0


def _download_archive(url: str, dirname: str, marker: str, models_root: Path) -> Path:
    """Download a sherpa-onnx .tar.bz2 release and extract it to models_root/dirname."""
    target = models_root / dirname
    if (target / marker).exists():
        return target
    models_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=models_root, prefix="dl-") as tmp:
        archive = Path(tmp) / "model.tar.bz2"
        _log(f"downloading {url}")
        urllib.request.urlretrieve(url, archive)  # noqa: S310 (fixed https URL)
        extract = Path(tmp) / "x"
        with tarfile.open(archive, "r:bz2") as tf:
            tf.extractall(extract, filter="data")
        shutil.rmtree(target, ignore_errors=True)
        (extract / dirname).rename(target)
    return target


def _prepare(spec: ModelSpec, models_root: Path) -> str | Path:
    """Download (if needed) and return a local path. Not part of the timed load."""
    if spec.kind == "whisper":
        from faster_whisper.utils import download_model

        out = models_root / safe_filename(spec.source)
        return download_model(spec.source, output_dir=str(out))
    if spec.kind == "sherpa-qwen3":
        return _download_archive(spec.source, QWEN3_DIRNAME, "conv_frontend.onnx", models_root)
    return _download_archive(spec.source, PARAKEET_DIRNAME, "tokens.txt", models_root)


def _warmup_audio(audios: list):
    """First 5 s of the first item, used to trigger one-off initialisation before timing."""
    return audios[0][: 5 * SAMPLE_RATE]


def _worker_whisper(
    spec: ModelSpec, local: str, sets: list[dict], beam: int, threads: int, rss: PeakRss
) -> dict:
    from faster_whisper import WhisperModel

    t0 = time.perf_counter()
    model = WhisperModel(local, device="cpu", compute_type="int8", cpu_threads=threads)
    load_s = time.perf_counter() - t0

    def transcribe(audio, st):
        segments, info = model.transcribe(
            audio,
            language=None if st["language"] == "auto" else st["language"],
            beam_size=beam,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=st.get("initial_prompt"),
            hotwords=st.get("hotwords"),
        )
        return " ".join(s.text.strip() for s in segments).strip(), info.language

    runs = []
    for st in sets:
        audios = [load_audio(p) for p in st["audio"]]
        if audios:
            transcribe(_warmup_audio(audios), st)
        rss.reset()
        hyps, langs = [], []
        t0 = time.perf_counter()
        for audio in audios:
            text, detected = transcribe(audio, st)
            hyps.append(text)
            langs.append(detected)
        elapsed = time.perf_counter() - t0
        runs.append(
            {
                "set": st["name"],
                "language": st["language"],
                "detected_language": dominant(langs) if st["language"] == "auto" else None,
                "detected_languages": langs if st["language"] == "auto" else None,
                "expected_language": st.get("expected_language"),
                "audio_s": sum(len(a) for a in audios) / SAMPLE_RATE,
                "transcribe_s": elapsed,
                "peak_rss_mb": rss.peak_mb(),
                "hypotheses": hyps,
            }
        )
    return {"load_s": load_s, "runs": runs}


def _speech_spans(audio) -> list[tuple[int, int]]:
    """VAD-based chunk plan (<= PARAKEET_MAX_CHUNK_S each); fixed chunks as fallback."""
    max_len = int(PARAKEET_MAX_CHUNK_S * SAMPLE_RATE)
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        opts = VadOptions(max_speech_duration_s=20.0, min_silence_duration_ms=500)
        spans = [(t["start"], t["end"]) for t in get_speech_timestamps(audio, opts)]
        if spans:
            return merge_spans(spans, max_len)
    except Exception as exc:  # noqa: BLE001 - fall back to fixed chunking
        _log(f"VAD chunking failed ({exc}); using fixed chunks")
    return fixed_spans(len(audio), max_len)


def _worker_sherpa(
    spec: ModelSpec, local: Path, sets: list[dict], threads: int, rss: PeakRss
) -> dict:
    import sherpa_onnx

    t0 = time.perf_counter()
    if spec.kind == "sherpa-qwen3":
        recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            conv_frontend=str(local / "conv_frontend.onnx"),
            encoder=str(local / "encoder.int8.onnx"),
            decoder=str(local / "decoder.int8.onnx"),
            tokenizer=str(local / "tokenizer"),
            num_threads=threads,
            sample_rate=SAMPLE_RATE,
        )
    else:
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(local / "encoder.int8.onnx"),
            decoder=str(local / "decoder.int8.onnx"),
            joiner=str(local / "joiner.int8.onnx"),
            tokens=str(local / "tokens.txt"),
            num_threads=threads,
            sample_rate=SAMPLE_RATE,
            decoding_method="greedy_search",
            model_type="nemo_transducer",
        )
    load_s = time.perf_counter() - t0

    def transcribe(audio) -> str:
        parts = []
        for start, end in _speech_spans(audio):  # chunk planning (VAD) counts as processing time
            stream = recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, audio[start:end])
            recognizer.decode_stream(stream)
            parts.append(stream.result.text.strip())
        return " ".join(p for p in parts if p)

    runs = []
    for st in sets:
        audios = [load_audio(p) for p in st["audio"]]
        if audios:
            transcribe(_warmup_audio(audios))
        rss.reset()
        t0 = time.perf_counter()
        hyps = [transcribe(a) for a in audios]
        elapsed = time.perf_counter() - t0
        runs.append(
            {
                "set": st["name"],
                "language": "auto",
                "detected_language": None,
                "detected_languages": None,
                "expected_language": st.get("expected_language"),
                "audio_s": sum(len(a) for a in audios) / SAMPLE_RATE,
                "transcribe_s": elapsed,
                "peak_rss_mb": rss.peak_mb(),
                "hypotheses": hyps,
            }
        )
    return {"load_s": load_s, "runs": runs}


def run_worker(model_key: str, beam: int, jobs_file: str, threads: int, result_file: str) -> int:
    from steno.paths import models_dir

    spec = MODELS[model_key]
    sets = json.loads(Path(jobs_file).read_text(encoding="utf-8"))["sets"]
    rss = PeakRss().start()
    try:
        t0 = time.perf_counter()
        local = _prepare(spec, models_dir())
        download_s = time.perf_counter() - t0

        if spec.kind == "whisper":
            out = _worker_whisper(spec, str(local), sets, beam, threads, rss)
        else:
            out = _worker_sherpa(spec, Path(local), sets, threads, rss)
        out.update(model=model_key, download_s=download_s)
    finally:
        rss.stop()
    Path(result_file).write_text(json.dumps(out), encoding="utf-8")
    return 0


# --------------------------------------------------------------------------- #
# Datasets (FLEURS, downloaded once into <data_dir>/eval/asr/, never into the repo)
# --------------------------------------------------------------------------- #


def _http_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (fixed https URL)
        return resp.read().decode("utf-8")


def prepare_fleurs(dataset: str, n: int, root: Path, seed: int = 0) -> list[dict]:
    """Make sure n FLEURS test utterances exist under root/<dataset>/ and return their items.

    Items are {"audio": path, "text": reference}. The audio archive is streamed once (not stored)
    and only the selected files are written; a rerun with the same or smaller n is offline.
    """
    config, _lang = DATASETS[dataset]
    ds_dir = root / dataset
    audio_dir = ds_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    tsv = ds_dir / "test.tsv"
    if not tsv.exists():
        tsv.write_text(_http_text(f"{FLEURS_BASE}/{config}/test.tsv"), encoding="utf-8")
    chosen = select_fleurs_rows(parse_fleurs_tsv(tsv.read_text(encoding="utf-8")), n, seed)
    if len(chosen) < n:
        _log(f"warning: only {len(chosen)} suitable utterances for {dataset} (wanted {n})")

    wanted = {safe_filename(r["file"]): r for r in chosen}
    missing = {name for name in wanted if not (audio_dir / name).exists()}
    if missing:
        url = f"{FLEURS_BASE}/{config}/audio/test.tar.gz"
        _log(f"streaming {url}, keeping {len(missing)} of {len(wanted)} files [{FLEURS_LICENSE}]")
        with (
            urllib.request.urlopen(url, timeout=120) as resp,  # noqa: S310
            tarfile.open(fileobj=resp, mode="r|gz") as tf,
        ):
            for member in tf:
                name = safe_filename(Path(member.name).name)
                if member.isfile() and name in missing:
                    (audio_dir / name).write_bytes(tf.extractfile(member).read())
                    missing.discard(name)
                    if not missing:
                        break
        if missing:
            raise RuntimeError(f"{len(missing)} FLEURS files not found in the archive")
    return [{"audio": str(audio_dir / name), "text": r["text"]} for name, r in wanted.items()]


# --------------------------------------------------------------------------- #
# Parent side
# --------------------------------------------------------------------------- #


def run_model_subprocess(
    model_key: str, beam: int, sets: list[dict], threads: int, tmp: Path, label: str
) -> dict:
    """Run one model in a child process; return its raw result dict or {'error': ...}."""
    stem = safe_filename(label)
    result_file = tmp / f"{stem}.json"
    result_file.unlink(missing_ok=True)
    jobs_file = tmp / f"{stem}.jobs.json"
    jobs_file.write_text(json.dumps({"sets": sets}), encoding="utf-8")
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        model_key,
        "--beam",
        str(beam),
        "--jobs-file",
        str(jobs_file),
        "--threads",
        str(threads),
        "--result-file",
        str(result_file),
    ]
    env = {**os.environ, "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    proc = subprocess.run(cmd, env=env, check=False)
    if proc.returncode != 0 or not result_file.exists():
        return {"error": f"worker exited with code {proc.returncode}"}
    return json.loads(result_file.read_text(encoding="utf-8"))


def language_match(detected: list[str | None] | None, expected: str | None) -> float | None:
    """Share of utterances whose detected language equals the expected one (auto runs only)."""
    if not detected or not expected:
        return None
    return sum(1 for d in detected if d == expected) / len(detected)


def score_row(row: dict, refs: list[str], hypotheses: list[str], terms: bool = False) -> None:
    """Fill WER/CER (and with `terms` the English-term accuracy fields) of a row in place."""
    row["wer"], row["cer"] = corpus_error_rates(refs, hypotheses)
    if terms:
        row.update(english_term_accuracy(refs, hypotheses))


def build_rows(
    raw: dict,
    label: str,
    beam: int | None,
    references: dict[str, list[str]] | None,
    terms: bool = False,
) -> tuple[list[dict], dict]:
    """Turn a worker result into table rows; also return {run_name: [hypotheses]}.

    `references` maps set name -> reference texts (same order as the set's audio). With
    `terms` the rows also carry the English-term accuracy (code-switching sets)."""
    if "error" in raw:
        return [{"run": label, "model": label, "error": raw["error"]}], {}
    rows, hyps = [], {}
    for run in raw["runs"]:
        name = f"{label}[{run['set']}]"
        hypotheses = run.pop("hypotheses")
        hyps[name] = hypotheses
        row = {
            "run": name,
            "model": label,
            "set": run["set"],
            "beam": beam,
            "language": run["language"],
            "detected_language": run["detected_language"],
            "language_match": language_match(
                run.get("detected_languages"), run.get("expected_language")
            ),
            "n_utterances": len(hypotheses),
            "audio_s": run["audio_s"],
            "download_s": raw["download_s"],
            "load_s": raw["load_s"],
            "transcribe_s": run["transcribe_s"],
            "rtf": real_time_factor(run["transcribe_s"], run["audio_s"]),
            "peak_rss_mb": run["peak_rss_mb"],
            "words": sum(len(h.split()) for h in hypotheses),
            "wer": None,
            "cer": None,
            "error": None,
        }
        refs = (references or {}).get(run["set"])
        if refs is not None:
            score_row(row, refs, hypotheses, terms)
        rows.append(row)
    return rows, hyps


# --------------------------------------------------------------------------- #
# LLM post-correction (Steno's own diff-only fix through the Claude CLI)
# --------------------------------------------------------------------------- #


def llm_fix_texts(texts: list[str], runner=None) -> tuple[list[str], dict]:
    """Run `steno.llm.llm_fix` (prompts/fix.md, `finalize --llm-fix`) over utterance texts with
    the default [llm] settings (haiku, effort medium). Returns (fixed texts, fix report).
    Only the hypothesis texts are sent, one `[n] text` line each."""
    from steno.config import LlmConfig
    from steno.llm import llm_fix
    from steno.transcript import Segment

    segments = [
        Segment(
            seq=i,
            t_start=float(i),
            t_end=i + 1.0,
            wall="",
            source="mic",
            speaker="S",
            text=t,
            lang="sv",
        )
        for i, t in enumerate(texts, 1)
    ]
    fixed, report = llm_fix(segments, LlmConfig(), runner)
    return [seg.text for seg in fixed], report


def fix_row(
    base: dict,
    hypotheses: list[str],
    refs: list[str],
    fixer=llm_fix_texts,
    terms: bool = False,
) -> tuple[dict, list[str]]:
    """Row for `<base>+fix`: the base hypotheses after the LLM fix. RTF includes the fix call
    (a post-pass, so it is not comparable with the live limit); `fix_s` is the call alone."""
    t0 = time.perf_counter()
    fixed, report = fixer(hypotheses)
    fix_s = time.perf_counter() - t0
    row = {k: v for k, v in base.items() if k not in ("wer", "cer")}
    row.update(
        run=f"{base['model']}+fix[{base['set']}]",
        model=f"{base['model']}+fix",
        fix_s=fix_s,
        fix_proposed=report.get("proposed"),
        fix_applied=report.get("applied"),
        fix_failed_batches=report.get("failed_batches"),
        rtf=real_time_factor(base["transcribe_s"] + fix_s, base["audio_s"]),
        words=sum(len(h.split()) for h in fixed),
    )
    score_row(row, refs, fixed, terms)
    return row, fixed


def format_dataset_table(rows: list[dict]) -> str:
    """One line per model/beam: error rates per dataset set (sv, en), RTF, RSS, load time."""
    labels = list(dict.fromkeys(r["model"] for r in rows))
    header = (
        "| model | sv WER | sv CER | en WER | en CER | RTF sv | RTF en | peak RSS MB "
        "| load s | note |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for label in labels:
        mine = [r for r in rows if r["model"] == label]
        if any(r.get("error") for r in mine):
            err = next(r["error"] for r in mine if r.get("error"))
            lines.append(f"| {label} | FAILED: {err} | | | | | | | | |")
            continue
        by_set = {r["set"]: r for r in mine}

        def pct(set_name: str, field: str, by_set=by_set) -> str:
            r = by_set.get(set_name)
            return "-" if r is None or r.get(field) is None else f"{r[field] * 100:.1f}%"

        def rtf(set_name: str, by_set=by_set) -> str:
            r = by_set.get(set_name)
            return "-" if r is None else _fmt(r["rtf"], ".3f")

        rss = max(r["peak_rss_mb"] for r in mine)
        load = mine[0]["load_s"]
        notes = [
            f"{r['set']} language detected correctly in {r['language_match'] * 100:.0f}%"
            for r in mine
            if r.get("language_match") is not None
        ]
        lines.append(
            f"| {label} | {pct('sv', 'wer')} | {pct('sv', 'cer')} | {pct('en', 'wer')} "
            f"| {pct('en', 'cer')} | {rtf('sv')} | {rtf('en')} | {rss:.0f} | {load:.1f} "
            f"| {'; '.join(notes)} |"
        )
    return header + "\n".join(lines)


def format_local_table(rows: list[dict]) -> str:
    """One line per strategy: WER, CER, English-term accuracy (all / in Swedish sentences /
    in English sentences), RTF, RSS and a note."""
    header = (
        "| strategy | WER | CER | EN-term acc | in sv sentences | in en sentences | RTF "
        "| peak RSS MB | note |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )

    def pct(r: dict, field: str) -> str:
        return "-" if r.get(field) is None else f"{r[field] * 100:.1f}%"

    lines = []
    for r in rows:
        if r.get("error"):
            lines.append(f"| {r['model']} | FAILED: {r['error']} | | | | | | | |")
            continue
        notes = []
        if r.get("language_match") is not None:
            notes.append(f"language detected correctly in {r['language_match'] * 100:.0f}%")
        if r.get("fix_s") is not None:
            notes.append(
                f"claude fix {r['fix_s']:.1f} s, {r['fix_applied']}/{r['fix_proposed']} edits "
                f"applied, RTF incl. fix"
                + (", a batch failed" if r.get("fix_failed_batches") else "")
            )
        lines.append(
            f"| {r['model']} | {pct(r, 'wer')} | {pct(r, 'cer')} | {pct(r, 'eng_acc')} "
            f"| {pct(r, 'eng_acc_cs')} | {pct(r, 'eng_acc_en')} | {_fmt(r['rtf'], '.3f')} "
            f"| {_fmt(r.get('peak_rss_mb'), '.0f')} | {'; '.join(notes)} |"
        )
    return header + "\n".join(lines)


def environment_info(threads: int) -> dict:
    from importlib.metadata import PackageNotFoundError, version

    def ver(pkg: str) -> str | None:
        try:
            return version(pkg)
        except PackageNotFoundError:
            return None

    return {
        "python": platform.python_version(),
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "threads": threads,
        "default_beam_size": BEAM_SIZE,
        "vad_filter": True,
        "compute_type": "int8",
        "faster_whisper": ver("faster-whisper"),
        "ctranslate2": ver("ctranslate2"),
        "sherpa_onnx": ver("sherpa-onnx"),
    }


def synthetic_audio(path: Path, seconds: float = 5.0) -> None:
    """Sine + noise, no speech. Plumbing check only."""
    import numpy as np
    import soundfile as sf

    rng = np.random.default_rng(0)
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    sig = 0.1 * np.sin(2 * np.pi * 220 * t) + 0.02 * rng.standard_normal(t.shape)
    sf.write(str(path), sig.astype("float32"), SAMPLE_RATE)


def expand_sets(
    spec: ModelSpec,
    jobs: list[dict],
    dataset_mode: bool,
    language: str | None = None,
    *,
    prompt: str | None = None,
    hotwords: str | None = None,
) -> list[dict]:
    """Worker input for one model. Dataset sets carry their own language (whisper only; other
    engines detect it); `language` overrides it, "auto" = let Whisper detect. Single-file jobs
    are repeated per language the model should be tried with (e.g. sv and auto).
    `prompt` / `hotwords` (Whisper only) become the sets' `initial_prompt` / `hotwords`."""
    extra = {}
    if spec.kind == "whisper":
        if prompt:
            extra["initial_prompt"] = prompt
        if hotwords:
            extra["hotwords"] = hotwords
    if dataset_mode:
        return [
            {
                **j,
                **extra,
                "expected_language": j["language"],
                "language": (language or j["language"]) if spec.kind == "whisper" else "auto",
            }
            for j in jobs
        ]
    return [
        {**job, **extra, "name": lang, "language": lang} for job in jobs for lang in spec.languages
    ]


def benchmark(
    jobs: list[dict],
    models: list[RunSpec],
    threads: int,
    out_dir: Path,
    dataset_mode: bool = False,
    language: str | None = None,
    *,
    local: bool = False,
    fix_labels: frozenset[str] = frozenset(),
    prompt: str = INITIAL_PROMPT,
    hotwords: str = HOTWORDS,
) -> int:
    """Run each strategy over all `jobs`; write results.json after every model.

    A job is {"name", "language", "audio": [paths], "refs": [str] | None}. `local` = own
    recordings (implies dataset mode): adds English-term accuracy. Strategies whose label is in
    `fix_labels` also get a `<label>+fix` row (LLM post-correction of their hypotheses)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_mode = dataset_mode or local
    rows: list[dict] = []

    def scored_rows() -> list[dict]:
        # recommendation in FLEURS mode is based on the Swedish set only
        if local:  # live recommendation: LLM-fix rows are a post-pass
            return [r for r in rows if r.get("fix_s") is None]
        return [r for r in rows if r.get("set") == "sv"] if dataset_mode else rows

    def write_results() -> None:
        best, why = recommend(sort_rows(scored_rows()))
        (out_dir / "results.json").write_text(
            json.dumps(
                {
                    "environment": environment_info(threads),
                    "sets": {j["name"]: len(j["audio"]) for j in jobs},
                    "initial_prompt": prompt,
                    "hotwords": hotwords,
                    "rows": sort_rows(rows),
                    "recommendation": best["run"] if best else None,
                    "recommendation_reason": why,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def save_hyps(hyps: dict[str, list[str]]) -> None:
        for name, texts in hyps.items():
            (out_dir / f"{safe_filename(name)}.txt").write_text(
                "\n".join(texts) + "\n", encoding="utf-8"
            )

    with tempfile.TemporaryDirectory(prefix="steno-bench-") as tmp:
        for spec in models:
            key, beam = spec.key, spec.beam
            is_whisper = MODELS[key].kind == "whisper"
            spec = RunSpec(
                key,
                beam,
                (spec.language or language) if is_whisper else None,
                spec.prompt,
                spec.hotwords,
            )
            label = spec.label
            print(f"\n=== {label} ===", flush=True)
            sets = expand_sets(
                MODELS[key],
                jobs,
                dataset_mode,
                spec.language,
                prompt=prompt if spec.prompt else None,
                hotwords=hotwords if spec.hotwords else None,
            )
            refs = {s["name"]: s["refs"] for s in sets if s.get("refs") is not None}
            raw = run_model_subprocess(key, beam or BEAM_SIZE, sets, threads, Path(tmp), label)
            new_rows, hyps = build_rows(raw, label, beam, refs or None, terms=local)
            if label in fix_labels:
                for base in [r for r in new_rows if not r.get("error")]:
                    print(f"  llm fix on {base['run']} (claude CLI) ...", flush=True)
                    row, fixed = fix_row(base, hyps[base["run"]], refs[base["set"]], terms=local)
                    new_rows.append(row)
                    hyps[row["run"]] = fixed
            rows.extend(new_rows)
            save_hyps(hyps)
            write_results()
            for r in new_rows:
                if r.get("error"):
                    print(f"  FAILED: {r['error']}", flush=True)
                else:
                    wer = "-" if r["wer"] is None else f"{r['wer'] * 100:.1f}%"
                    eng = "" if r.get("eng_acc") is None else f"  EN {r['eng_acc'] * 100:.0f}%"
                    print(f"  {r['model']}: WER {wer}{eng}  RTF {r['rtf']:.3f}", flush=True)

    rows = sort_rows(rows)
    if local:
        table = format_local_table(rows)
    else:
        table = format_dataset_table(rows) if dataset_mode else format_table(rows)
    print("\n" + table)
    best, why = recommend(scored_rows())
    print(f"\nRecommendation: {best['run'] if best else 'none'}  ({why})")
    if local:
        post = [r for r in rows if r.get("fix_s") is not None and r.get("wer") is not None]
        if post:
            top = min(post, key=lambda r: r["wer"])
            print(f"Best post-pass (LLM fix): {top['model']}  WER {top['wer'] * 100:.1f}%")
    print(f"Results written to: {out_dir}")
    return 0 if rows and all(r.get("error") is None for r in rows) else 1


def resolve_fix_labels(requested: list[str] | None, specs: list[RunSpec]) -> frozenset[str]:
    """`--llm-fix` without values = the first strategy (put the base kb-whisper run first)."""
    if requested is None:
        return frozenset()
    labels = [sp.label for sp in specs]
    if not requested:
        return frozenset(labels[:1])
    unknown = [r for r in requested if r not in labels]
    if unknown:
        raise ValueError(f"--llm-fix: no such strategy in --models: {', '.join(unknown)}")
    return frozenset(requested)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--audio", type=Path, help="wav/flac/mp3 file")
    p.add_argument("--reference", type=Path, help="reference transcript (UTF-8 text)")
    p.add_argument(
        "--dataset",
        help="FLEURS test subset (CC-BY-4.0) instead of --audio: fleurs ('fleurs' = sv and en), "
        "fleurs-sv, fleurs-en; or local:<name> = <data_dir>/eval/<name>/refs.tsv + NN.wav "
        "(code-switching set from scripts/record_eval.py; missing wavs are skipped)",
    )
    p.add_argument("--n", type=int, default=60, help="utterances from fleurs-sv (default 60)")
    p.add_argument("--n-en", type=int, default=30, help="utterances from fleurs-en (default 30)")
    p.add_argument(
        "--models",
        default=",".join(MODELS),
        help="comma-separated strategies, each key[@beam][:lang][+prompt][+hot] with key in: "
        f"{', '.join(MODELS)}",
    )
    p.add_argument(
        "--language",
        help="dataset mode: force this language for every Whisper set ('auto' = detect), "
        "default is each set's own language (a `:lang` in --models wins)",
    )
    p.add_argument("--prompt-text", default=INITIAL_PROMPT, help="text used by +prompt")
    p.add_argument("--hotwords-text", default=HOTWORDS, help="words used by +hot")
    p.add_argument(
        "--llm-fix",
        nargs="*",
        metavar="STRATEGY",
        help="also score the LLM post-correction (steno.llm.llm_fix via the claude CLI, haiku, "
        "effort medium) of these strategies' hypotheses (labels as printed, e.g. "
        "kb-whisper-small+prompt); no value = the first strategy in --models. "
        "Sends hypothesis text only.",
    )
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("--out", type=Path, help="output dir (default: <data_dir>/benchmarks/<ts>)")
    p.add_argument("--self-test", action="store_true", help="5 s synthetic audio, smallest model")
    p.add_argument("--worker", help=argparse.SUPPRESS)
    p.add_argument("--beam", type=int, default=BEAM_SIZE, help=argparse.SUPPRESS)
    p.add_argument("--jobs-file", help=argparse.SUPPRESS)
    p.add_argument("--result-file", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.worker:
        return run_worker(args.worker, args.beam, args.jobs_file, args.threads, args.result_file)

    from steno.paths import data_dir

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.self_test:
        out_dir = args.out or data_dir() / "benchmarks" / f"selftest-{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="steno-selftest-") as tmp:
            wav = Path(tmp) / "synthetic.wav"
            synthetic_audio(wav)
            jobs = [{"name": "file", "language": "auto", "audio": [str(wav)], "refs": None}]
            rc = benchmark(jobs, [RunSpec(SELF_TEST_MODEL)], args.threads, out_dir)
        print("SELF-TEST " + ("OK" if rc == 0 else "FAILED"))
        return rc

    try:
        specs = [parse_run_spec(k) for k in args.models.split(",") if k.strip()]
        fix_labels = resolve_fix_labels(args.llm_fix, specs)
    except ValueError as exc:
        p.error(str(exc))
    unknown = [sp.key for sp in specs if sp.key not in MODELS]
    if unknown:
        p.error(f"unknown model(s): {', '.join(unknown)}; choose from {', '.join(MODELS)}")
    for sp in specs:
        if MODELS[sp.key].kind != "whisper" and (sp.prompt or sp.hotwords or sp.language):
            p.error(f"{sp.label}: +prompt, +hot and :lang only apply to Whisper models")
    out_dir = args.out or data_dir() / "benchmarks" / stamp
    opts = {"prompt": args.prompt_text, "hotwords": args.hotwords_text, "fix_labels": fix_labels}

    if args.dataset and args.dataset.startswith("local:"):
        name = args.dataset.removeprefix("local:")
        try:
            items, missing = load_local_dataset(name, data_dir() / "eval")
        except (ValueError, FileNotFoundError) as exc:
            p.error(str(exc))
        if not items:
            p.error(f"no recordings found for local:{name}")
        note = f", {len(missing)} not recorded yet (skipped)" if missing else ""
        print(f"local:{name}: {len(items)} recordings{note}")
        jobs = [
            {
                "name": "local",
                "language": "sv",
                "audio": [i["audio"] for i in items],
                "refs": [i["text"] for i in items],
            }
        ]
        return benchmark(
            jobs, specs, args.threads, out_dir, language=args.language, local=True, **opts
        )

    if args.dataset:
        if args.dataset not in ("fleurs", "fleurs-sv", "fleurs-en"):
            p.error("--dataset must be fleurs, fleurs-sv, fleurs-en or local:<name>")
        keys = ["fleurs-sv", "fleurs-en"] if args.dataset == "fleurs" else [args.dataset]
        root = data_dir() / "eval" / "asr"
        jobs = []
        for key in keys:
            items = prepare_fleurs(key, args.n_en if key == "fleurs-en" else args.n, root)
            jobs.append(
                {
                    "name": key.removeprefix("fleurs-"),
                    "language": DATASETS[key][1],
                    "audio": [i["audio"] for i in items],
                    "refs": [i["text"] for i in items],
                }
            )
        return benchmark(
            jobs, specs, args.threads, out_dir, dataset_mode=True, language=args.language, **opts
        )

    if not args.audio:
        p.error("--audio or --dataset is required (or use --self-test)")
    if not args.audio.is_file():
        p.error(f"audio file not found: {args.audio}")
    ref = args.reference.read_text(encoding="utf-8") if args.reference else None
    jobs = [
        {
            "name": "file",
            "language": "auto",
            "audio": [str(args.audio)],
            "refs": None if ref is None else [ref],
        }
    ]
    return benchmark(jobs, specs, args.threads, out_dir, **opts)


if __name__ == "__main__":
    sys.exit(main())
