"""Simulated live session: real engine, real models, audio paced to the wall clock.

Feeds your own eval recordings (``<data_dir>/eval/codeswitch/NN.wav``, joined with a gap, the
reference text from ``refs.tsv``) through the real ``Engine`` at real-time pace, with the live
draft (``[asr]`` model) and the rolling refine worker (``[refine]`` model) running at the same
time, and prints only numbers, never any transcript text:

* draft WER and refined WER (what ``steno new`` / the transcript shows once refinement caught
  up) and the WER of ``final.jsonl`` written by ``steno finalize`` (default settings),
* live draft latency (seconds from the end of an utterance until its draft line is written),
  to see whether refinement slows the draft down (run once with ``--refine off`` for the base),
* refine lag (seconds from the end of a refined window's last line until its revision),
* system CPU use (sampled every second) and the time from "stop" (end of audio) to the
  finished transcript: draining the draft queue, refining the last window, finalize.

Everything runs against a temporary ``STENO_DATA_DIR`` (models are linked in, no profiles, no
sessions of yours); it is deleted at the end. ``--repeat N`` loops the audio to simulate a
longer meeting.

    uv run python scripts/simulate_live.py --refine small
    uv run python scripts/simulate_live.py --refine turbo --repeat 3
    uv run python scripts/simulate_live.py --refine off      # baseline of the live draft
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

SR = 16000
MODELS = {
    "small": "KBLab/kb-whisper-small",
    "turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
}
#: A model downloaded by scripts/benchmark_asr.py lives in a plain folder of the models dir; the
#: HF id would make faster-whisper download its own copy (1.6 GB for turbo), so reuse the folder.
LOCAL_COPY = {"turbo": "deepdml_faster-whisper-large-v3-turbo-ct2"}


def load_eval(name: str, gap_s: float, repeat: int) -> tuple[np.ndarray, str, float]:
    """(audio, reference text, seconds of speech) for the eval set, repeated ``repeat`` times."""
    import soundfile as sf

    from steno import paths

    root = paths.data_dir() / "eval" / name
    refs = {}
    for line in (root / "refs.tsv").read_text(encoding="utf-8").splitlines():
        key, _, text = line.partition("\t")
        if key.strip() and text.strip():
            refs[key.strip()] = text.strip()
    parts, texts, speech = [], [], 0.0
    gap = np.zeros(int(gap_s * SR), dtype=np.float32)
    for _ in range(repeat):
        for key in sorted(refs):
            wav = root / f"{key}.wav"
            if not wav.is_file():
                continue
            data, rate = sf.read(str(wav), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
            assert rate == SR, f"{wav.name}: expected {SR} Hz"
            parts += [data.astype(np.float32), gap]
            texts.append(refs[key])
            speech += len(data) / SR
    return np.concatenate(parts), " ".join(texts), speech


def wer(reference: str, hypothesis: str) -> float:
    import jiwer
    from benchmark_asr import normalize_text

    ref, hyp = normalize_text(reference), normalize_text(hypothesis)
    return 1.0 if not hyp else float(jiwer.wer(ref, hyp))


class CpuMonitor:
    def __init__(self) -> None:
        import psutil

        self._psutil = psutil
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self._psutil.cpu_percent(None)
        while not self._stop.wait(1.0):
            self.samples.append(self._psutil.cpu_percent(None))

    def __enter__(self) -> CpuMonitor:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--refine", default="small",
                    help="small, turbo, off, or a model id (default small)")
    ap.add_argument("--dataset", default="codeswitch")
    ap.add_argument("--repeat", type=int, default=1, help="loop the audio this many times")
    ap.add_argument("--gap", type=float, default=1.0, help="seconds of silence between clips")
    ap.add_argument("--no-speakers", action="store_true", help="live draft without speaker model")
    ap.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
                    help="override a config value (TOML scalar), e.g. refine.window_speech_s=15")
    ap.add_argument("--keep", action="store_true", help="keep the temporary data dir")
    args = ap.parse_args(argv)

    real_models = Path(os.environ.get("STENO_DATA_DIR") or Path.home() / ".steno") / "models"
    tmp = Path(tempfile.mkdtemp(prefix="steno-sim-"))
    (tmp / "data").mkdir()
    # models are big: link them instead of copying (a junction needs no privileges on Windows)
    link = tmp / "data" / "models"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real_models)],
                       check=True, stdout=subprocess.DEVNULL)
    else:
        link.symlink_to(real_models, target_is_directory=True)
    audio_all = None
    try:
        # The eval audio lives in the real data dir; read it before switching.
        audio_all, reference, speech_s = load_eval(args.dataset, args.gap, args.repeat)
        os.environ["STENO_DATA_DIR"] = str(tmp / "data")
        return run(args, tmp, audio_all, reference, speech_s)
    finally:
        os.environ.pop("STENO_DATA_DIR", None)
        if not args.keep:
            if link.exists():
                try:
                    os.rmdir(link)  # a junction/symlink: never recurse into the real models
                except OSError:
                    link.unlink(missing_ok=True)
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def run(args, tmp: Path, audio: np.ndarray, reference: str, speech_s: float) -> int:
    from steno import finalize, paths, transcript
    from steno.asr import create_transcriber
    from steno.audio import ArraySource
    from steno.config import load_config
    from steno.engine import Engine
    from steno.profiles import ProfileStore
    from steno.refine import create_refine_models
    from steno.speakers import create_embedder

    sys.path.insert(0, str(HERE))
    from eval_diarization import apply_overrides  # noqa: E402

    cfg = load_config(None)
    cfg = apply_overrides(cfg, args.set) if args.set else cfg
    model = MODELS.get(args.refine, args.refine)
    if args.refine in LOCAL_COPY:
        folder = paths.models_dir() / LOCAL_COPY[args.refine]
        if not folder.is_dir():
            print(f"error: {folder.name} not found in the models dir; pass a model id or path "
                  "with --refine (this script never starts a model download by itself)",
                  file=sys.stderr)
            return 2
        model = str(folder)
    refine_on = args.refine != "off"
    cfg = replace(cfg, refine=replace(cfg.refine, enabled=refine_on, model=model))
    cfg = replace(cfg, speakers=replace(cfg.speakers, enabled=not args.no_speakers))

    live_tr = create_transcriber(cfg.asr)
    refine = create_refine_models(cfg)
    embedder = create_embedder(cfg.speakers) if cfg.speakers.enabled else None
    audio_s = len(audio) / SR

    class PacedSource(ArraySource):
        t_start = 0.0

        def start(self, sink, t0):
            PacedSource.t_start = time.perf_counter()
            super().start(sink, t0)

    engine = Engine(
        cfg, live_tr, [PacedSource("mic", audio, chunk_s=0.1, realtime=True)],
        sessions_root=tmp / "sessions", embedder=embedder, profile_store=ProfileStore(),
        refine_transcriber=refine.transcriber, refine_fallback=refine.fallback,
        refine_diarizer=refine.diarizer, keep_audio=False,
    )
    draft_latency: list[float] = []
    rev_latency: list[float] = []
    engine.subscribe(lambda s: draft_latency.append(
        time.perf_counter() - PacedSource.t_start - s.t_end))
    engine.subscribe_revisions(lambda s: rev_latency.append(
        time.perf_counter() - PacedSource.t_start - s.t_end))

    print(f"audio {audio_s:.0f} s ({speech_s:.0f} s speech), live={cfg.asr.model}, "
          f"refine={'off' if not refine_on else model}, "
          f"speakers={'on' if embedder else 'off'}, "
          f"incremental diarization={'on' if refine.diarizer else 'off'}", flush=True)
    t_load = time.perf_counter()
    with CpuMonitor() as cpu:
        session_dir = engine.start()  # loads the models first, then capture starts
        load_s = time.perf_counter() - t_load
        engine.wait(timeout=audio_s * 4 + 600)
        t_end = time.perf_counter()
        drain_s = max(0.0, t_end - (PacedSource.t_start + audio_s))  # draft queue after the end
        engine.stop()
        stop_s = time.perf_counter() - t_end  # tail refine (the ASR queue is already drained)
        t_fin = time.perf_counter()
        res = finalize.finalize_session(session_dir, cfg, ProfileStore())
        fin_s = time.perf_counter() - t_fin

    drafts = transcript.read_segments(session_dir)
    best = transcript.read_best(session_dir)
    final = finalize.read_final(session_dir)
    wer_draft = wer(reference, " ".join(s.text for s in drafts))
    wer_best = wer(reference, " ".join(s.text for s in best))
    wer_final = wer(reference, " ".join(s.text for s in final))
    revised = sum(1 for s in best if s.rev is not None)
    print(f"model load+start {load_s:.1f} s")
    print(f"lines {len(drafts)}, revised {revised}; "
          f"WER draft {wer_draft * 100:.1f} %  refined {wer_best * 100:.1f} %  "
          f"final.jsonl {wer_final * 100:.1f} %")
    print(f"draft latency (s after utterance end): median {statistics.median(draft_latency):.1f}"
          f"  p90 {pct(draft_latency, 0.9):.1f}  max {max(draft_latency):.1f}")
    ref_worker = engine.refiner
    if ref_worker is not None:
        st = ref_worker.stats
        lags = [lag.lag_s for lag in st.lags]
        comp = [lag.compute_s / max(lag.speech_s, 1e-6) for lag in st.lags]
        print(f"refine: {st.windows} windows, {st.revisions} revisions, {st.speech_s:.0f} s speech "
              f"in {st.compute_s:.0f} s decoding (x{st.compute_s / max(st.speech_s, 1e-6):.2f} per "
              f"speech second), skipped {st.skipped_s:.0f} s, failed {st.failed}")
        print(f"refine lines: {st.lines} refined, {st.unchanged} unchanged, {st.rejected} "
              f"rejected, {st.no_words} without words")
        if lags:
            print("refine lag per window (s after its last line): "
                  + " ".join(f"{x:.0f}" for x in lags))
            print(f"refine lag median {statistics.median(lags):.1f}  max {max(lags):.1f}; "
                  f"decode/speech per window median {statistics.median(comp):.2f}")
    if refine.diarizer is not None:
        dz = refine.diarizer
        print(f"incremental diarization: {dz.windows} windows run, {dz.skipped} skipped (no live "
              f"speech), {dz.compute_s:.0f} s of compute "
              f"(x{dz.compute_s / audio_s:.2f} per audio second)")
    if cpu.samples:
        print(f"system CPU (12 logical): mean {statistics.mean(cpu.samples):.0f} %  "
              f"p90 {pct(cpu.samples, 0.9):.0f} %  max {max(cpu.samples):.0f} %")
    print(f"final.json: diarization {res.diarization} ({res.diar_source or '-'}), "
          f"{len(res.speakers)} speaker(s)")
    print(f"STOP -> final: draft drain {drain_s:.1f} s + tail refine {stop_s:.1f} s + finalize "
          f"{fin_s:.1f} s ({res.diarization} speakers, {res.asr_mode}) = "
          f"{drain_s + stop_s + fin_s:.1f} s for {audio_s / 60:.1f} min of audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
