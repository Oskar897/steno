"""Benchmark local speech-to-text models on this machine (CPU only).

Each candidate runs in its own subprocess so peak RSS is clean and a crash does
not kill the whole run. Results (JSON + hypothesis texts) are written to the
Steno data dir, never into the repository.

Usage:
    uv run python scripts/benchmark_asr.py --audio talk.wav --reference talk.txt
    uv run python scripts/benchmark_asr.py --self-test

Settings that affect the numbers (also stored in results.json):
    * Whisper models: beam_size=1 (greedy, for speed), vad_filter=True,
      condition_on_previous_text=False, int8 compute type.
    * RTF = transcription wall time / audio duration (load and download excluded).
    * kb-whisper CTranslate2 weights live in `model.bin` on the `main` branch.
      The `standard`/`subtitle`/`strict` tags only contain safetensors (no CT2).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SAMPLE_RATE = 16_000
BEAM_SIZE = 1
RTF_LIMIT = 0.5  # realtime headroom for live use while a call app is running
PARAKEET_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
)
PARAKEET_DIRNAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
PARAKEET_MAX_CHUNK_S = 25.0


@dataclass(frozen=True)
class ModelSpec:
    key: str
    kind: str  # "whisper" | "sherpa"
    source: str  # HF repo id, or download URL
    languages: tuple[str, ...]  # whisper passes; "auto" = language detection


MODELS: dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec("kb-whisper-base", "whisper", "KBLab/kb-whisper-base", ("sv",)),
        ModelSpec("kb-whisper-small", "whisper", "KBLab/kb-whisper-small", ("sv", "auto")),
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


def _download_parakeet(models_root: Path) -> Path:
    target = models_root / PARAKEET_DIRNAME
    if (target / "tokens.txt").exists():
        return target
    models_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=models_root, prefix="dl-") as tmp:
        archive = Path(tmp) / "model.tar.bz2"
        _log(f"downloading {PARAKEET_URL} (~490 MB)")
        urllib.request.urlretrieve(PARAKEET_URL, archive)  # noqa: S310 (fixed https URL)
        extract = Path(tmp) / "x"
        with tarfile.open(archive, "r:bz2") as tf:
            tf.extractall(extract, filter="data")
        (extract / PARAKEET_DIRNAME).rename(target)
    return target


def _prepare(spec: ModelSpec, models_root: Path) -> str | Path:
    """Download (if needed) and return a local path. Not part of the timed load."""
    if spec.kind == "whisper":
        from faster_whisper.utils import download_model

        out = models_root / safe_filename(spec.source)
        return download_model(spec.source, output_dir=str(out))
    return _download_parakeet(models_root)


def _worker_whisper(spec: ModelSpec, local: str, audio, threads: int, rss: PeakRss) -> dict:
    from faster_whisper import WhisperModel

    t0 = time.perf_counter()
    model = WhisperModel(local, device="cpu", compute_type="int8", cpu_threads=threads)
    load_s = time.perf_counter() - t0

    runs = []
    for lang in spec.languages:
        rss.reset()
        t0 = time.perf_counter()
        segments, info = model.transcribe(
            audio,
            language=None if lang == "auto" else lang,
            beam_size=BEAM_SIZE,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        elapsed = time.perf_counter() - t0
        runs.append(
            {
                "language": lang,
                "detected_language": info.language if lang == "auto" else None,
                "transcribe_s": elapsed,
                "peak_rss_mb": rss.peak_mb(),
                "hypothesis": text,
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


def _worker_sherpa(spec: ModelSpec, local: Path, audio, threads: int, rss: PeakRss) -> dict:
    import sherpa_onnx

    t0 = time.perf_counter()
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

    rss.reset()
    t0 = time.perf_counter()
    parts = []
    for start, end in _speech_spans(audio):  # chunk planning (VAD) counts as processing time
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio[start:end])
        recognizer.decode_stream(stream)
        parts.append(stream.result.text.strip())
    text = " ".join(p for p in parts if p)
    elapsed = time.perf_counter() - t0
    return {
        "load_s": load_s,
        "runs": [
            {
                "language": "auto",
                "detected_language": None,
                "transcribe_s": elapsed,
                "peak_rss_mb": rss.peak_mb(),
                "hypothesis": text,
            }
        ],
    }


def run_worker(model_key: str, audio_path: str, threads: int, result_file: str) -> int:
    from steno.paths import models_dir

    spec = MODELS[model_key]
    rss = PeakRss().start()
    try:
        t0 = time.perf_counter()
        local = _prepare(spec, models_dir())
        download_s = time.perf_counter() - t0

        audio = load_audio(audio_path)
        duration = len(audio) / SAMPLE_RATE

        if spec.kind == "whisper":
            out = _worker_whisper(spec, str(local), audio, threads, rss)
        else:
            out = _worker_sherpa(spec, Path(local), audio, threads, rss)
        out.update(model=model_key, download_s=download_s, audio_s=duration)
    finally:
        rss.stop()
    Path(result_file).write_text(json.dumps(out), encoding="utf-8")
    return 0


# --------------------------------------------------------------------------- #
# Parent side
# --------------------------------------------------------------------------- #


def run_model_subprocess(model_key: str, audio_path: Path, threads: int, tmp: Path) -> dict:
    """Run one model in a child process; return its raw result dict or {'error': ...}."""
    result_file = tmp / f"{safe_filename(model_key)}.json"
    result_file.unlink(missing_ok=True)
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        model_key,
        "--audio",
        str(audio_path),
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


def build_rows(raw: dict, model_key: str, reference: str | None) -> tuple[list[dict], dict]:
    """Turn a worker result into table rows; also return {run_name: hypothesis}."""
    if "error" in raw:
        return [{"run": model_key, "model": model_key, "error": raw["error"]}], {}
    rows, hyps = [], {}
    for run in raw["runs"]:
        name = f"{model_key}[{run['language']}]"
        hyp = run.pop("hypothesis")
        hyps[name] = hyp
        row = {
            "run": name,
            "model": model_key,
            "language": run["language"],
            "detected_language": run["detected_language"],
            "audio_s": raw["audio_s"],
            "download_s": raw["download_s"],
            "load_s": raw["load_s"],
            "transcribe_s": run["transcribe_s"],
            "rtf": real_time_factor(run["transcribe_s"], raw["audio_s"]),
            "peak_rss_mb": run["peak_rss_mb"],
            "words": len(hyp.split()),
            "wer": None,
            "cer": None,
            "error": None,
        }
        if reference is not None:
            row["wer"], row["cer"] = error_rates(reference, hyp)
        rows.append(row)
    return rows, hyps


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
        "beam_size": BEAM_SIZE,
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


def benchmark(
    audio_path: Path, model_keys: list[str], threads: int, out_dir: Path, ref: str | None
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="steno-bench-") as tmp:
        for key in model_keys:
            print(f"\n=== {key} ===", flush=True)
            raw = run_model_subprocess(key, audio_path, threads, Path(tmp))
            new_rows, hyps = build_rows(raw, key, ref)
            rows.extend(new_rows)
            for name, text in hyps.items():
                (out_dir / f"{safe_filename(name)}.txt").write_text(text + "\n", encoding="utf-8")

    rows = sort_rows(rows)
    best, why = recommend(rows)
    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "environment": environment_info(threads),
                "reference_given": ref is not None,
                "rows": rows,
                "recommendation": best["run"] if best else None,
                "recommendation_reason": why,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n" + format_table(rows))
    print(f"\nRecommendation: {best['run'] if best else 'none'}  ({why})")
    print(f"Results written to: {out_dir}")
    return 0 if rows and all(r.get("error") is None for r in rows) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--audio", type=Path, help="wav/flac/mp3 file")
    p.add_argument("--reference", type=Path, help="reference transcript (UTF-8 text)")
    p.add_argument(
        "--models",
        default=",".join(MODELS),
        help=f"comma-separated subset of: {', '.join(MODELS)}",
    )
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("--out", type=Path, help="output dir (default: <data_dir>/benchmarks/<ts>)")
    p.add_argument("--self-test", action="store_true", help="5 s synthetic audio, smallest model")
    p.add_argument("--worker", help=argparse.SUPPRESS)
    p.add_argument("--result-file", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.worker:
        return run_worker(args.worker, str(args.audio), args.threads, args.result_file)

    from steno.paths import data_dir

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.self_test:
        out_dir = args.out or data_dir() / "benchmarks" / f"selftest-{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="steno-selftest-") as tmp:
            wav = Path(tmp) / "synthetic.wav"
            synthetic_audio(wav)
            rc = benchmark(wav, [SELF_TEST_MODEL], args.threads, out_dir, None)
        print("SELF-TEST " + ("OK" if rc == 0 else "FAILED"))
        return rc

    if not args.audio:
        p.error("--audio is required (or use --self-test)")
    if not args.audio.is_file():
        p.error(f"audio file not found: {args.audio}")
    keys = [k.strip() for k in args.models.split(",") if k.strip()]
    unknown = [k for k in keys if k not in MODELS]
    if unknown:
        p.error(f"unknown model(s): {', '.join(unknown)}; choose from {', '.join(MODELS)}")
    ref = args.reference.read_text(encoding="utf-8") if args.reference else None
    out_dir = args.out or data_dir() / "benchmarks" / stamp
    return benchmark(args.audio, keys, args.threads, out_dir, ref)


if __name__ == "__main__":
    sys.exit(main())
