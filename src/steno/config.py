"""Configuration loading.

The dataclass defaults below are the source of truth and mirror
``config.example.toml`` (a test keeps them in sync). A user ``config.toml``
overrides them key by key; unknown keys are ignored with a warning.

Search order for ``config.toml`` when no explicit path is given:
1. ``<data_dir>/config.toml``
2. ``<repo root>/config.toml`` (development checkouts only, git-ignored)
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from steno import paths

log = logging.getLogger(__name__)

CONFIG_NAME = "config.toml"
EXAMPLE_NAME = "config.example.toml"

# Whisper ``initial_prompt`` used by default: natural Swedish meeting text that spells English
# tech/university terms in English (with Swedish suffixes, "pipelinen"), the pattern the model
# should continue. Generic on purpose; never copy sentences from an evaluation set into it.
DEFAULT_INITIAL_PROMPT = (
    "Hej allihopa, välkomna till veckomötet. Vi började med vår thesis och sen fixade vi "
    "några bugs i pipelinen. Jag testade scriptet på servern och committade ändringen på "
    "GitHub. Efter lunch har vi en workshop, så ta med laptopen och en bra paper."
)

#: ``[post] asr_mode`` values, see ``PostConfig``.
ASR_MODES = ("refined", "longform", "selective", "off")
#: ``[post] diarization`` values, see ``PostConfig``.
DIARIZATION_MODES = ("fast", "thorough")


class ConfigError(ValueError):
    """Raised for an unreadable or invalid configuration file."""


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 16000
    capture_mic: bool = True
    capture_loopback: bool = True
    # Fallback labels, used only when [speakers] is disabled (or for an explicit --speaker).
    mic_speaker_label: str = "Me"
    loopback_speaker_label: str = "Remote"


@dataclass(frozen=True)
class AsrConfig:
    backend: str = "faster-whisper"
    model: str = "KBLab/kb-whisper-small"
    compute_type: str = "int8"
    language: str = "sv"  # "sv", "en" or "auto"
    cpu_threads: int = 6
    beam_size: int = 1
    # Whisper initial_prompt (context the decoder continues from); "" = none. Used by live
    # transcription and by finalize.
    initial_prompt: str = DEFAULT_INITIAL_PROMPT


@dataclass(frozen=True)
class VadConfig:
    threshold: float = 0.4
    min_silence_ms: int = 500
    min_speech_ms: int = 250
    max_segment_s: float = 15.0
    # Lift quiet far-field audio before the VAD (see steno.vad.AutoGain); the audio that goes
    # on to ASR and speaker recognition is not changed.
    auto_gain: bool = True
    auto_gain_target: float = 0.05  # RMS the 90th-percentile block level is lifted to
    auto_gain_max: float = 8.0  # never amplify more than this


@dataclass(frozen=True)
class EchoConfig:
    """Drop microphone speech that is just the loopback audio leaking into the mic."""

    enabled: bool = True
    # Mic is treated as echo when its RMS is below ``ratio`` x the overlapping loopback RMS.
    ratio: float = 0.35


@dataclass(frozen=True)
class SpeakersConfig:
    """Speaker recognition (layer 2). All thresholds are cosine similarities of
    L2-normalised embeddings and are UNCALIBRATED starting points: tune them on
    your own microphone and meeting rooms."""

    enabled: bool = True
    model: str = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
    num_threads: int = 2
    min_embed_s: float = 1.0  # shorter segments/windows inherit the previous speaker (conf 0)
    accept_threshold: float = 0.55  # profile match: best score must reach this ...
    margin: float = 0.05  # ... and beat the second-best person by this much
    # Live recognition of an enrolled person is made stable by matching whole session
    # clusters to the profiles: a centroid is far less noisy than one 1.5 s window. A
    # matching cluster is bound to the person for good (earlier lines follow).
    cluster_match_threshold: float = 0.6  # cluster centroid vs profile score; 0 = off
    cluster_match_margin: float = 0.1  # ... and ahead of the second-best person by this much
    cluster_match_s: float = 4.0  # a cluster is only matched after this much speech
    cluster_match_pool: float = 0.75  # ... and must sound like the person's windows this session
    # Sub-segment turns: a VAD segment is cut into overlapping windows, each is labelled
    # on its own, and the segment is split where the label changes.
    window_s: float = 1.5  # embedding window length; 0 = one embedding per segment (old way)
    window_hop_s: float = 0.75
    smooth_windows: int = 3  # median filter over window labels (odd; 1 = off)
    min_turn_s: float = 0.5  # shorter turns are absorbed by the longer neighbour
    snap_boundaries: bool = False  # move turn cuts to the quietest point within 0.4 s
    # Live session clusters.
    cluster_threshold: float = 0.5  # window joins a cluster (moves its centroid) at/above this
    assign_floor: float = 0.4  # between floor and threshold: take the label, keep the centroid
    merge_threshold: float = 0.7  # merge two clusters whose centroids reach this
    confirm_s: float = 3.5  # a new speaker needs this much speech ...
    confirm_segments: int = 2  # ... from this many different segments, else it is merged away
    tentative_ttl: int = 8  # unconfirmed cluster untouched for this many segments is merged
    max_speakers: int = 10
    # `steno refine`: spectral clustering of all windows (AHC below min_spectral windows).
    refine_threshold: float = 0.4  # AHC stop similarity (few windows) / segment-level fallback
    refine_pval: float = 0.012  # p-pruning: fraction of strongest neighbours kept per row ...
    refine_min_pnum: int = 6  # ... but at least this many
    refine_min_spectral: int = 40  # fewer windows than this: agglomerative instead
    refine_max_speakers: int = 10
    refine_minor_windows: int = 4  # clusters this small are reassigned to the nearest large one
    # `steno enroll`: windows over the recording, outliers dropped.
    enroll_window_s: float = 2.5
    enroll_hop_s: float = 1.0
    enroll_outlier_sigma: float = 2.0


@dataclass(frozen=True)
class ProfilesConfig:
    """Voice profiles. A profile only ever changes for a person who was enrolled or named
    by you; ``auto_update`` lets a finished session add a few new voice samples to the
    profile that was recognised in it (encrypted like the rest of the profile)."""

    auto_update: bool = True
    auto_update_min_s: float = 15.0  # speech recognised as the person needed in the session
    auto_update_min_score: float = 0.6  # the session's voice must match the profile this well
    auto_update_max: int = 5  # at most this many new embeddings per session and person
    auto_update_dup: float = 0.93  # skip samples this similar to one the profile already has


@dataclass(frozen=True)
class RefineConfig:
    """Rolling refinement while recording: stretches of speech are transcribed again with more
    context in the background and replace the live draft text (``revisions.jsonl``)."""

    enabled: bool = True
    model: str = "deepdml/faster-whisper-large-v3-turbo-ct2"
    # Cheaper model used instead while the worker is far behind (switching back when it has
    # caught up) and for the tail at stop when `model` would exceed stop_budget_s ("" = none).
    fallback_model: str = "KBLab/kb-whisper-small"
    # At stop: refine the remaining speech with `model` only if that is expected to take at
    # most this many seconds (from the measured speed), else with fallback_model. 0 = always
    # `model`.
    stop_budget_s: float = 12.0
    # [asr] language "sv": 30 s chunks that are English are transcribed as English instead
    # of being translated into Swedish (free: uses the encoder output the decoder needs).
    detect_english: bool = True
    window_speech_s: float = 25.0  # refine a stream once this much unrefined speech is there
    max_wait_s: float = 60.0  # ... or once its oldest unrefined line is this old (stream time)
    pad_s: float = 0.5  # audio context before the first and after the last line of a window
    threads: int = 4  # CPU threads of the refine model (the live model has [asr] cpu_threads)
    beam_size: int = 1
    batch_size: int = 2  # 30 s chunks decoded together
    buffer_s: float = 900.0  # audio kept in memory per stream (16 bit); older audio is dropped
    max_backlog_windows: int = 3  # more unrefined speech than this many windows: degrade
    skip_after_windows: int = 6  # ... and the oldest windows are skipped beyond this many
    # Incremental speaker diarization (pyannote segmentation per 10 s window that held speech,
    # while recording) so that `steno finalize` has it ready; independent of `enabled`.
    diarize: bool = True
    diarize_threads: int = 2  # segmentation and speaker-embedding threads of that worker


@dataclass(frozen=True)
class PostConfig:
    """`steno finalize`: the offline pass over the kept audio after a meeting."""

    auto: bool = False  # run finalize automatically when `steno record` stops
    # Text: "refined" = the text the rolling refine worker wrote during the meeting (no new ASR,
    # no kept audio needed); "longform" = each kept stream is transcribed once as a whole and
    # the words are assigned to the final turns; "selective" = only low-confidence live lines
    # are transcribed again, one by one; "off" = keep the live draft words.
    asr_mode: str = "refined"
    # Speakers: "fast" = cluster the live window embeddings (windows.bin, no audio needed,
    # seconds); "thorough" = pyannote segmentation over the kept audio (slower, more accurate).
    diarization: str = "fast"
    # Model of the re-transcription. Whole-recording decoding (longform) is where a large
    # model is affordable; per-line decoding ("selective") pads every clip to 30 s, so pick a
    # smaller model for it (e.g. KBLab/kb-whisper-medium).
    asr_model: str = "deepdml/faster-whisper-large-v3-turbo-ct2"
    asr_beam_size: int = 5  # selective mode
    longform_beam_size: int = 1
    longform_batch_size: int = 4  # 30 s windows decoded together
    asr_min_logprob: float = -0.7  # live lines below this average log-probability are redone
    asr_max_compression: float = 2.4  # ... and lines whose text compresses better than 1/this
    threads: int = 4  # segmentation and speaker-embedding threads
    seg_step_s: float = 2.5  # a 10 s segmentation window every this many seconds
    seg_min_clean_s: float = 1.0  # solo speech a local speaker needs to take part in clustering
    minor_points: int = 4  # speaker clusters this small are folded into the nearest one
    split_cos: float = 0.78  # one speaker estimated: accept a 2-way split when the cluster
    # centroids' cosine is at most this (0 = never split) ...
    split_min_s: float = 20.0  # ... and the smaller cluster has this many seconds of solo speech
    merge_gap_s: float = 1.0  # same-speaker lines closer than this are joined in final.jsonl


@dataclass(frozen=True)
class LlmConfig:
    """Summary / to-do through the Claude Code CLI (text only; audio never leaves)."""

    command: str = "claude"  # the Claude Code executable
    model: str = "haiku"  # alias or full model name
    effort: str = "medium"  # low, medium, high, xhigh, max; "" = CLI default
    language: str = "sv"  # output language; "auto" = the language of the meeting
    timeout_s: int = 300
    fix_max_edit: float = 0.3  # `finalize --llm-fix`: largest accepted edit (share of the line)


@dataclass(frozen=True)
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    echo: EchoConfig = field(default_factory=EchoConfig)
    speakers: SpeakersConfig = field(default_factory=SpeakersConfig)
    profiles: ProfilesConfig = field(default_factory=ProfilesConfig)
    refine: RefineConfig = field(default_factory=RefineConfig)
    post: PostConfig = field(default_factory=PostConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)


def repo_root() -> Path | None:
    """Repo root when running from a source checkout, else None."""
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else None


def find_config_file() -> Path | None:
    candidates = [paths.data_dir() / CONFIG_NAME]
    root = repo_root()
    if root is not None:
        candidates.append(root / CONFIG_NAME)
    return next((c for c in candidates if c.is_file()), None)


def _coerce(section: str, name: str, default: Any, value: Any) -> Any:
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        value = float(value) if ok else value
    else:
        ok = isinstance(value, type(default))
    if not ok:
        raise ConfigError(
            f"[{section}] {name}: expected {type(default).__name__}, got {value!r}"
        )
    return value


def _apply_section(section: str, obj: Any, data: Any) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"[{section}] must be a table")
    known = {f.name: f for f in fields(obj)}
    updates: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            log.warning("Ignoring unknown config key [%s] %s", section, key)
            continue
        updates[key] = _coerce(section, key, getattr(obj, key), value)
    return replace(obj, **updates)


def config_from_dict(data: dict[str, Any]) -> Config:
    cfg = Config()
    updates: dict[str, Any] = {}
    for key, value in data.items():
        if key not in {f.name for f in fields(cfg)}:
            log.warning("Ignoring unknown config section [%s]", key)
            continue
        updates[key] = _apply_section(key, getattr(cfg, key), value)
    cfg = replace(cfg, **updates)
    if cfg.asr.language not in {"sv", "en", "auto"} and len(cfg.asr.language) != 2:
        raise ConfigError(f"[asr] language: unsupported value {cfg.asr.language!r}")
    if cfg.post.asr_mode not in ASR_MODES:
        raise ConfigError(
            f"[post] asr_mode: expected one of {', '.join(ASR_MODES)}, got {cfg.post.asr_mode!r}"
        )
    if cfg.post.diarization not in DIARIZATION_MODES:
        raise ConfigError(
            f"[post] diarization: expected one of {', '.join(DIARIZATION_MODES)}, "
            f"got {cfg.post.diarization!r}"
        )
    r = cfg.refine
    if r.window_speech_s <= 0 or r.batch_size < 1 or r.beam_size < 1 or r.threads < 1:
        raise ConfigError("[refine] window_speech_s, batch_size, beam_size, threads must be > 0")
    if r.max_backlog_windows < 1 or r.skip_after_windows < r.max_backlog_windows:
        raise ConfigError("[refine] need 1 <= max_backlog_windows <= skip_after_windows")
    if cfg.post.longform_batch_size < 1 or cfg.post.longform_beam_size < 1:
        raise ConfigError("[post] longform_batch_size and longform_beam_size must be at least 1")
    return cfg


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration. An explicit ``path`` must exist; otherwise defaults apply."""
    if path is not None:
        file = Path(path)
        if not file.is_file():
            raise ConfigError(f"Config file not found: {file.name}")
    else:
        file = find_config_file()
        if file is None:
            return Config()
    try:
        with file.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {file.name}: {exc}") from exc
    return config_from_dict(data)
