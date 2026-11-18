"""Objective, repeatable diarization evaluation for Steno (single laptop mic, CPU only).

Scores "who spoke when" against reference RTTMs with the diarization error rate
(``steno.der``: 10 ms frames, optimal speaker mapping, 0.25 s collar by default).

Data
    * AMI meetings, one distant mic (Array1-01 channel) + word-level reference RTTMs
      from the pyannote "AMI-diarization-setup" repo. ``--download`` fetches only the
      first ``--minutes`` of each recording (HTTP range request) into
      ``<data_dir>/eval/ami/``. Public, no registration (AMI is CC BY 4.0).
    * ``--session <id> --rttm <path>``: one of your own Steno sessions (needs
      ``audio-mic.flac``, i.e. recorded with ``--keep-audio``) against an RTTM you
      made yourself. Private data never leaves the data dir; nothing is written to
      the repository.

Systems (``--system a,b``)
    steno-live           Steno's real live pipeline replayed offline (Silero VAD ->
                         CAM++ embedding -> leader-follower clustering, merges
                         resolved), with a fake transcriber so it runs fast.
    steno-refine         the same session, then ``steno refine`` (offline
                         agglomerative re-clustering) and its labels overlay.
    steno-finalize       the diarization half of `steno finalize` on the audio alone:
                         pyannote segmentation-3.0 windows + CAM++ embeddings per local
                         speaker, Steno's spectral clustering and overlap-aware stitching
                         (``steno.diarize``). Speaker count estimated; the ``-oracle``
                         variant is told the true count. Tune with ``--set post.<key>=...``.
    sherpa-offline       sherpa-onnx OfflineSpeakerDiarization (pyannote
                         segmentation-3.0 + CAM++), speaker count estimated
                         (``--cluster-threshold``, or fixed with ``--num-speakers N``).
    sherpa-offline-oracle  the same, told the true number of speakers.

Steno systems run against a fresh temporary ``STENO_DATA_DIR`` (models linked in,
empty profiles), so the evaluation never reads or writes your real profiles.
Segments labelled unknown ("U") are dropped from the hypothesis (they count as miss).

Usage:
    uv run python scripts/eval_diarization.py --download --minutes 10
    uv run python scripts/eval_diarization.py --system steno-live,sherpa-offline
    uv run python scripts/eval_diarization.py --session 20260101-100000 --rttm mine.rttm

Results: markdown tables on stdout and ``<data_dir>/eval/results/<timestamp>.json``
(with ``--save-rttm`` also the hypothesis RTTMs next to it).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import struct
import sys
import tempfile
import time
import urllib.request
import wave
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from steno import der, paths

SAMPLE_RATE = 16_000
UA = {"User-Agent": "steno-eval/1.0"}

AMI_MEETINGS = ("EN2002a", "ES2004a", "IS1009a")  # all in the pyannote AMI "test" split
AMI_AUDIO_URL = (
    "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{id}/audio/{id}.Array1-01.wav"
)
AMI_RTTM_URL = (
    "https://raw.githubusercontent.com/pyannote/AMI-diarization-setup/main/"
    "only_words/rttms/test/{id}.rttm"
)
DEFAULT_SYSTEMS = (
    "steno-live,steno-refine,steno-finalize,steno-finalize-oracle,"
    "sherpa-offline,sherpa-offline-oracle"
)
DEFAULT_MINUTES = 10.0


class EvalError(RuntimeError):
    pass


# --------------------------------------------------------------------------- locations


#: The real models dir, fixed before the steno-* systems swap in a temporary data dir.
_REAL_MODELS: Path | None = None


def models_root() -> Path:
    return _REAL_MODELS if _REAL_MODELS is not None else paths.models_dir()


def eval_dir() -> Path:
    path = paths.data_dir() / "eval"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ami_dir() -> Path:
    path = eval_dir() / "ami"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ami_wav(meeting: str, minutes: float) -> Path:
    return ami_dir() / f"{meeting}.{minutes:g}min.wav"


def ami_rttm(meeting: str) -> Path:
    return ami_dir() / f"{meeting}.rttm"


# --------------------------------------------------------------------------- downloads


def _http_get(url: str, byte_range: tuple[int, int] | None = None) -> bytes:
    headers = dict(UA)
    if byte_range is not None:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    req = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        if byte_range is None:
            return resp.read()
        return resp.read(byte_range[1] - byte_range[0] + 1)  # also cuts a server ignoring Range


def download_ami_audio(meeting: str, minutes: float, dest: Path) -> None:
    """Fetch the first ``minutes`` of the Array1-01 channel as a 16 kHz mono WAV.

    Uses an HTTP range request, so a 10-minute crop is ~19 MB instead of the full file.
    """
    url = AMI_AUDIO_URL.format(id=meeting)
    head = _http_get(url, (0, 127))
    pos = head.find(b"data")
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE" or pos < 0:
        raise EvalError(f"{meeting}: unexpected WAV header")
    channels, rate, _, _, bits = struct.unpack("<HIIHH", head[22:36])
    if (channels, rate, bits) != (1, SAMPLE_RATE, 16):
        raise EvalError(f"{meeting}: expected 16 kHz mono 16-bit, got {channels}ch/{rate}/{bits}")
    offset = pos + 8
    total = struct.unpack("<I", head[pos + 4 : pos + 8])[0]
    n_bytes = min(total, int(minutes * 60 * rate) * 2)
    n_bytes -= n_bytes % 2
    pcm = _http_get(url, (offset, offset + n_bytes - 1))
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    tmp = dest.with_name(dest.name + ".part")
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    os.replace(tmp, dest)


def download_ami(meetings: tuple[str, ...], minutes: float) -> None:
    for meeting in meetings:
        rttm = ami_rttm(meeting)
        if not rttm.is_file():
            print(f"downloading {meeting}.rttm", file=sys.stderr)
            rttm.write_bytes(_http_get(AMI_RTTM_URL.format(id=meeting)))
        wav = ami_wav(meeting, minutes)
        if not wav.is_file():
            print(f"downloading {meeting} first {minutes:g} min", file=sys.stderr)
            download_ami_audio(meeting, minutes, wav)


def ensure_segmentation_model() -> Path:
    """pyannote segmentation-3.0 (sherpa-onnx export) in the real models dir (downloaded once)."""
    from steno import segmentation

    return segmentation.ensure_model(models_root() / segmentation.SEG_DIRNAME)


# --------------------------------------------------------------------------- cases


@dataclass
class Case:
    name: str
    audio: np.ndarray  # 16 kHz mono float32
    ref: list[der.Turn]

    @property
    def duration(self) -> float:
        return len(self.audio) / SAMPLE_RATE

    @property
    def n_ref_speakers(self) -> int:
        return len({t[2] for t in self.ref})


def _load_audio(path: Path, minutes: float | None) -> np.ndarray:
    from steno.audio import load_audio_file

    audio = load_audio_file(str(path))
    if minutes:
        audio = audio[: int(minutes * 60 * SAMPLE_RATE)]
    return audio


def ami_cases(meetings: tuple[str, ...], minutes: float) -> list[Case]:
    cases = []
    for meeting in meetings:
        wav, rttm = ami_wav(meeting, minutes), ami_rttm(meeting)
        if not (wav.is_file() and rttm.is_file()):
            raise EvalError(
                f"missing data for {meeting} ({minutes:g} min): run with --download first"
            )
        audio = _load_audio(wav, minutes)
        turns = der.crop_turns(
            next(iter(der.read_rttm(rttm).values())), 0.0, len(audio) / SAMPLE_RATE
        )
        cases.append(Case(meeting, audio, turns))
    return cases


def local_case(session: str, rttm: Path, minutes: float | None) -> Case:
    from steno import transcript

    session_dir = transcript.resolve_session(session)
    if session_dir is None:
        raise EvalError(f"no such session: {session}")
    flac = session_dir / "audio-mic.flac"
    if not flac.is_file():
        raise EvalError(f"{session_dir.name} has no audio-mic.flac (record with --keep-audio)")
    audio = _load_audio(flac, minutes)
    by_file = der.read_rttm(rttm)
    if not by_file:
        raise EvalError("the RTTM has no SPEAKER lines")
    key = session_dir.name if session_dir.name in by_file else next(iter(by_file))
    turns = der.crop_turns(by_file[key], 0.0, len(audio) / SAMPLE_RATE)
    return Case(f"local-{session_dir.name}", audio, turns)


# --------------------------------------------------------------------------- systems


@dataclass
class Hypothesis:
    turns: list[der.Turn]
    seconds: float  # processing wall time (model loading excluded)
    note: str = ""


@dataclass
class Context:
    args: argparse.Namespace
    cfg: object  # steno.config.Config
    work: Path  # scratch dir for sessions
    live_cache: dict[str, tuple[Path, float]] = field(default_factory=dict)
    models: dict = field(default_factory=dict)  # loaded segmenter/embedder, reused per case


class FakeTranscriber:
    """Skips ASR: every utterance becomes "x", so only the speaker turns matter."""

    name = "fake-asr"

    def load(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        return "x", "en"

    def transcribe_words(self, audio: np.ndarray):
        """One fake word per 0.25 s, so every speaker turn of the segment gets text."""
        from steno.windows import Word

        n = max(1, int(len(audio) / SAMPLE_RATE / 0.25))
        return "x", "en", [Word(i * 0.25, (i + 1) * 0.25, "x") for i in range(n)]


def _segments_to_turns(session_dir: Path) -> list[der.Turn]:
    from steno import labels, transcript

    segments = transcript.read_segments(session_dir)
    ids = labels.effective_speaker_ids(session_dir, segments)  # overlay + merges resolved
    return [
        (s.t_start, s.t_end, ids[s.seq])
        for s in segments
        if ids.get(s.seq) not in (None, labels.UNKNOWN_ID)
    ]


def _live_session(case: Case, ctx: Context) -> tuple[Path, float]:
    """Replay the audio through Steno's live engine once per case (cached)."""
    if case.name in ctx.live_cache:
        return ctx.live_cache[case.name]
    from steno.audio import ArraySource
    from steno.engine import Engine
    from steno.profiles import ProfileStore
    from steno.speakers import create_embedder

    embedder = create_embedder(ctx.cfg.speakers)  # type: ignore[attr-defined]
    embedder.load()  # model load stays outside the timing
    root = ctx.work / f"live-{case.name}"
    engine = Engine(
        ctx.cfg,  # type: ignore[arg-type]
        FakeTranscriber(),
        [ArraySource("mic", case.audio)],
        sessions_root=root,
        embedder=embedder,
        profile_store=ProfileStore(),  # the temporary, empty data dir
    )
    t0 = time.perf_counter()
    session_dir = engine.start()
    try:
        if not engine.wait(timeout=max(600.0, case.duration * 3)):
            raise EvalError("live replay timed out")
    finally:
        engine.stop()
    result = (session_dir, time.perf_counter() - t0)
    ctx.live_cache[case.name] = result
    return result


def sys_steno_live(case: Case, ctx: Context) -> Hypothesis:
    session_dir, seconds = _live_session(case, ctx)
    return Hypothesis(_segments_to_turns(session_dir), seconds)


def sys_steno_refine(case: Case, ctx: Context) -> Hypothesis:
    from steno import ops
    from steno.profiles import ProfileStore

    live_dir, live_s = _live_session(case, ctx)
    copy = ctx.work / f"refine-{case.name}"
    shutil.copytree(live_dir, copy, dirs_exist_ok=True)  # refine rewrites labels in place
    t0 = time.perf_counter()
    ops.refine_session(copy, ctx.cfg.speakers, ProfileStore())  # type: ignore[attr-defined]
    seconds = live_s + (time.perf_counter() - t0)
    return Hypothesis(_segments_to_turns(copy), seconds, "live + refine")


def _finalize_diarization(case: Case, ctx: Context, num_speakers: int | None) -> Hypothesis:
    from steno.diarize import diarize
    from steno.finalize import diarize_params
    from steno.segmentation import PyannoteSegmenter
    from steno.speakers import create_embedder

    cfg = ctx.cfg
    if "segmenter" not in ctx.models:  # model loading stays outside the timing
        embedder = create_embedder(
            replace(cfg.speakers, num_threads=cfg.post.threads)  # type: ignore[attr-defined]
        )
        embedder.load()
        segmenter = PyannoteSegmenter(ensure_segmentation_model(), cfg.post.threads)  # type: ignore[attr-defined]
        segmenter.load()
        ctx.models.update(embedder=embedder, segmenter=segmenter)
    t0 = time.perf_counter()
    result = diarize(
        {"mic": case.audio}, ctx.models["segmenter"], ctx.models["embedder"],
        cfg.speakers,  # type: ignore[attr-defined]
        diarize_params(cfg),  # type: ignore[arg-type]
        num_speakers=num_speakers,
    )
    seconds = time.perf_counter() - t0
    turns = [(a, b, spk) for a, b, spk, _ in result.labelled()]
    note = f"num_speakers={num_speakers}" if num_speakers else f"{len(result.speaker_ids)} found"
    return Hypothesis(turns, seconds, note)


def sys_steno_finalize(case: Case, ctx: Context) -> Hypothesis:
    return _finalize_diarization(case, ctx, None)


def sys_steno_finalize_oracle(case: Case, ctx: Context) -> Hypothesis:
    return _finalize_diarization(case, ctx, case.n_ref_speakers)


def _sherpa(case: Case, ctx: Context, num_speakers: int) -> Hypothesis:
    import sherpa_onnx

    from steno.speakers import download_model

    args = ctx.args
    seg_model = ensure_segmentation_model()
    name = ctx.cfg.speakers.model  # type: ignore[attr-defined]
    emb_model = download_model(name, models_root() / name)
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(seg_model)
            ),
            num_threads=args.threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(emb_model), num_threads=args.threads
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers, threshold=args.cluster_threshold
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise EvalError("invalid sherpa-onnx diarization config")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    if diarizer.sample_rate != SAMPLE_RATE:
        raise EvalError(f"unexpected diarizer sample rate {diarizer.sample_rate}")
    t0 = time.perf_counter()
    result = diarizer.process(np.ascontiguousarray(case.audio, dtype=np.float32))
    seconds = time.perf_counter() - t0
    turns = [(r.start, r.end, f"spk{r.speaker}") for r in result.sort_by_start_time()]
    note = f"num_clusters={num_speakers}" if num_speakers > 0 else f"thr={args.cluster_threshold}"
    return Hypothesis(turns, seconds, note)


def sys_sherpa_offline(case: Case, ctx: Context) -> Hypothesis:
    return _sherpa(case, ctx, ctx.args.num_speakers)


def sys_sherpa_oracle(case: Case, ctx: Context) -> Hypothesis:
    return _sherpa(case, ctx, case.n_ref_speakers)


SYSTEMS = {
    "steno-live": sys_steno_live,
    "steno-refine": sys_steno_refine,
    "steno-finalize": sys_steno_finalize,
    "steno-finalize-oracle": sys_steno_finalize_oracle,
    "sherpa-offline": sys_sherpa_offline,
    "sherpa-offline-oracle": sys_sherpa_oracle,
}


@contextlib.contextmanager
def isolated_data_dir(cfg):
    """Temporary STENO_DATA_DIR with the ONNX models linked in and no profiles/sessions."""
    real_models = paths.models_dir()
    old = os.environ.get("STENO_DATA_DIR")
    with tempfile.TemporaryDirectory(prefix="steno-eval-") as tmp:
        root = Path(tmp)
        models = root / "models"
        models.mkdir()
        for src in real_models.glob("*.onnx"):
            dst = models / src.name
            try:
                os.link(src, dst)
            except OSError:
                if src.name == cfg.speakers.model:  # copy only what the engine needs
                    shutil.copy2(src, dst)
        os.environ["STENO_DATA_DIR"] = str(root)
        try:
            yield root
        finally:
            if old is None:
                os.environ.pop("STENO_DATA_DIR", None)
            else:
                os.environ["STENO_DATA_DIR"] = old


def apply_overrides(cfg, overrides: list[str]):
    """Apply ``section.key=value`` strings (TOML scalars) on top of a Config."""
    import tomllib
    for item in overrides:
        key, sep, raw = item.partition("=")
        section, dot, name = key.strip().partition(".")
        if not (sep and dot) or not hasattr(cfg, section):
            raise ValueError(f"bad --set {item!r}; expected section.key=value")
        sub = getattr(cfg, section)
        if not hasattr(sub, name):
            raise ValueError(f"unknown config key {key!r}")
        try:
            value = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw  # bare word -> string
        if isinstance(getattr(sub, name), float) and isinstance(value, int):
            value = float(value)
        cfg = replace(cfg, **{section: replace(sub, **{name: value})})
    return cfg


# --------------------------------------------------------------------------- reporting


def pct(x: float) -> str:
    return f"{100 * x:.1f}"


def aggregate(rows: list[dict]) -> dict:
    """Mean over files and pooled (error time / reference time) over all files."""
    ref_s = sum(r["ref_speech_s"] for r in rows)
    pooled = {
        "der": sum(r["miss_s"] + r["false_alarm_s"] + r["confusion_s"] for r in rows) / ref_s,
        "miss": sum(r["miss_s"] for r in rows) / ref_s,
        "false_alarm": sum(r["false_alarm_s"] for r in rows) / ref_s,
        "confusion": sum(r["confusion_s"] for r in rows) / ref_s,
    }
    mean = {k: float(np.mean([r[k] for r in rows])) for k in pooled}
    mean["n_ref"] = float(np.mean([r["n_ref"] for r in rows]))
    mean["n_hyp"] = float(np.mean([r["n_hyp"] for r in rows]))
    mean["rtf"] = sum(r["seconds"] for r in rows) / sum(r["audio_s"] for r in rows)
    return {"mean": mean, "pooled": pooled}


def system_table(name: str, rows: list[dict], agg: dict) -> str:
    out = [
        f"### {name}",
        "",
        "| file | DER % | miss % | FA % | conf % | hyp/ref spk | RTF |",
        "|---|---:|---:|---:|---:|:---:|---:|",
    ]
    for r in rows:
        out.append(
            f"| {r['file']} | {pct(r['der'])} | {pct(r['miss'])} | {pct(r['false_alarm'])} | "
            f"{pct(r['confusion'])} | {r['n_hyp']}/{r['n_ref']} | {r['rtf']:.3f} |"
        )
    m, p = agg["mean"], agg["pooled"]
    out.append(
        f"| **mean** | **{pct(m['der'])}** | {pct(m['miss'])} | {pct(m['false_alarm'])} | "
        f"{pct(m['confusion'])} | {m['n_hyp']:.1f}/{m['n_ref']:.1f} | {m['rtf']:.3f} |"
    )
    out.append(
        f"| pooled | {pct(p['der'])} | {pct(p['miss'])} | {pct(p['false_alarm'])} | "
        f"{pct(p['confusion'])} | | |"
    )
    return "\n".join(out)


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Diarization error rate of Steno's speaker pipeline vs reference RTTMs."
    )
    p.add_argument("--download", action="store_true",
                   help="fetch the AMI crops/RTTMs and the segmentation model if missing")
    p.add_argument("--download-only", action="store_true", help="download, then exit")
    p.add_argument("--system", default=DEFAULT_SYSTEMS,
                   help=f"comma-separated: {', '.join(SYSTEMS)} (default: all)")
    p.add_argument("--minutes", type=float, default=None,
                   help=f"crop to the first N minutes (AMI default {DEFAULT_MINUTES:g}; "
                        "local sessions default to the whole recording)")
    p.add_argument("--meetings", default=",".join(AMI_MEETINGS),
                   help="comma-separated AMI meeting ids (default: %(default)s)")
    p.add_argument("--session", help="evaluate this Steno session (needs audio-mic.flac) ...")
    p.add_argument("--rttm", type=Path, help="... against this reference RTTM (use with --session)")
    p.add_argument("--collar", type=float, default=der.DEFAULT_COLLAR_S,
                   help="seconds excluded on each side of reference boundaries (default 0.25)")
    p.add_argument("--skip-overlap", action="store_true",
                   help="do not score regions where reference speakers overlap")
    p.add_argument("--num-speakers", type=int, default=-1,
                   help="sherpa-offline: fixed speaker count (default -1 = estimate)")
    p.add_argument("--cluster-threshold", type=float, default=0.5,
                   help="sherpa-offline clustering threshold when estimating (default 0.5)")
    p.add_argument("--threads", type=int, default=4, help="sherpa-offline threads (default 4)")
    p.add_argument("--config", type=Path, help="Steno config.toml for the steno-* systems "
                   "(default: built-in defaults, so results are reproducible)")
    p.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
                   help="override one Steno config value for the steno-* systems, e.g. "
                        "--set speakers.window_s=1.0 --set vad.threshold=0.35 (repeatable)")
    p.add_argument("--save-rttm", action="store_true",
                   help="also write each hypothesis as RTTM next to the results JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    global _REAL_MODELS
    args = build_parser().parse_args(argv)
    _REAL_MODELS = paths.models_dir()
    systems = [s.strip() for s in args.system.split(",") if s.strip()]
    unknown = [s for s in systems if s not in SYSTEMS]
    if unknown:
        print(f"error: unknown system(s) {', '.join(unknown)}; choose from {', '.join(SYSTEMS)}",
              file=sys.stderr)
        return 2
    if bool(args.session) != bool(args.rttm):
        print("error: --session and --rttm go together", file=sys.stderr)
        return 2

    meetings = tuple(m.strip() for m in args.meetings.split(",") if m.strip())
    ami_minutes = args.minutes or DEFAULT_MINUTES
    if args.download or args.download_only:
        try:
            download_ami(meetings, ami_minutes)
            ensure_segmentation_model()
        except Exception as exc:
            print(f"error: download failed: {exc}", file=sys.stderr)
            return 1
        if args.download_only:
            print(f"data ready in {ami_dir().parent.name}/ami (inside the Steno data dir)")
            return 0

    try:
        cases = (
            [local_case(args.session, args.rttm, args.minutes)]
            if args.session
            else ami_cases(meetings, ami_minutes)
        )
    except EvalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    from steno.config import Config, load_config

    cfg = load_config(args.config) if args.config else Config()
    try:
        cfg = apply_overrides(cfg, args.set)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir = eval_dir() / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, dict] = {}
    need_isolation = any(s.startswith("steno-") for s in systems)
    with contextlib.ExitStack() as stack:
        if need_isolation:
            stack.enter_context(isolated_data_dir(cfg))
        work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="steno-eval-work-")))
        ctx = Context(args, cfg, work)
        for name in systems:
            rows: list[dict] = []
            for case in cases:
                print(f"[{name}] {case.name} ({case.duration / 60:.1f} min) ...", file=sys.stderr,
                      flush=True)
                try:
                    hyp = SYSTEMS[name](case, ctx)
                    res = der.compute_der(
                        case.ref,
                        hyp.turns,
                        collar=args.collar,
                        skip_overlap=args.skip_overlap,
                        uem=(0.0, case.duration),
                    )
                except Exception as exc:  # keep going: one broken system must not hide the rest
                    print(f"  failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    continue
                row = {
                    "file": case.name,
                    **res.as_dict(),
                    "audio_s": case.duration,
                    "seconds": hyp.seconds,
                    "rtf": hyp.seconds / case.duration,
                    "note": hyp.note,
                }
                rows.append(row)
                if args.save_rttm:
                    out = results_dir / stamp / name
                    out.mkdir(parents=True, exist_ok=True)
                    der.write_rttm(out / f"{case.name}.rttm", hyp.turns, case.name)
            if rows:
                report[name] = {"files": rows, **aggregate(rows)}

    for name, entry in report.items():
        print(system_table(name, entry["files"], entry))
        print()
    if len(report) > 1:
        print("### summary")
        print()
        print("| system | mean DER % | pooled DER % | miss % | FA % | conf % | hyp/ref spk | RTF |")
        print("|---|---:|---:|---:|---:|---:|:---:|---:|")
        for name, e in report.items():
            m, p = e["mean"], e["pooled"]
            print(f"| {name} | {pct(m['der'])} | {pct(p['der'])} | {pct(m['miss'])} | "
                  f"{pct(m['false_alarm'])} | {pct(m['confusion'])} | "
                  f"{m['n_hyp']:.1f}/{m['n_ref']:.1f} | {m['rtf']:.3f} |")
        print()

    settings = {
        "systems": systems,
        "collar": args.collar,
        "skip_overlap": args.skip_overlap,
        "minutes": args.minutes if args.session else ami_minutes,
        "num_speakers": args.num_speakers,
        "cluster_threshold": args.cluster_threshold,
        "sherpa_threads": args.threads,
        "meetings": [c.name for c in cases],
        "frame_s": der.FRAME_S,
        "overrides": args.set,
    }
    payload = {
        "timestamp": stamp,
        "settings": settings,
        "steno_speakers_config": asdict(cfg.speakers),
        "steno_vad_config": asdict(cfg.vad),
        "steno_post_config": asdict(cfg.post),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "results": report,
    }
    out_path = results_dir / f"{stamp}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"results: {out_path.parent.parent.name}/results/{out_path.name} (in the Steno data dir)",
          file=sys.stderr)
    return 0 if report else 1


if __name__ == "__main__":
    raise SystemExit(main())
