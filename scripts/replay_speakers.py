"""Replay a recorded session through the live speaker logic (``SpeakerTracker``).

Used to tune and check live speaker recognition on stored data without any audio model
in the loop: the 1.5 s window embeddings of a session (``windows.bin``, or computed
once from ``audio-mic.flac`` with ``--embed`` for sessions that predate it) are fed back
to a fresh tracker segment by segment, exactly the way the engine calls it.

Only ids, scores and counts are printed, never transcript text. The session and the
profiles are read from ``STENO_DATA_DIR`` (use a *copy*: ``--auto-update`` really
changes the profiles of that data dir).

    STENO_DATA_DIR=/tmp/copy uv run python scripts/replay_speakers.py 20260101-100000
    ... --set speakers.cluster_match_threshold=0.45 --no-profiles --embed
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from steno import labels as lab
from steno import transcript
from steno.config import load_config
from steno.profiles import ProfileStore, SessionWindows, WindowSet
from steno.speakers import SAMPLE_RATE, SpeakerTracker, create_embedder
from steno.windows import window_spans


class ReplayEmbedder:
    """Returns the next stored embedding for every ``embed`` call."""

    name = "replay"

    def __init__(self, embeddings: list[np.ndarray]):
        self._queue = deque(embeddings)

    def load(self) -> None:
        pass

    def embed(self, audio: np.ndarray) -> np.ndarray:
        return self._queue.popleft()


@dataclass(frozen=True)
class Group:
    stream: str
    t0: float
    t1: float
    emb: list[np.ndarray]


def group_windows(windows: WindowSet) -> list[Group]:
    """Windows of one VAD segment overlap each other; a gap in time starts a new segment."""
    groups: list[Group] = []
    cur: list[int] = []
    for i in range(len(windows)):
        if cur and (
            windows.stream[i] != windows.stream[cur[-1]]
            or windows.t0[i] >= windows.t1[cur[-1]] - 1e-6
        ):
            groups.append(_group(windows, cur))
            cur = []
        cur.append(i)
    if cur:
        groups.append(_group(windows, cur))
    return groups


def _group(windows: WindowSet, idx: list[int]) -> Group:
    return Group(
        windows.stream[idx[0]],
        float(windows.t0[idx[0]]),
        float(windows.t1[idx[-1]]),
        [windows.emb[i] for i in idx],
    )


def windows_from_audio(session_dir: Path, cfg) -> WindowSet:
    """Embed the transcript's VAD segments (adjacent turns merged) from ``audio-mic.flac``."""
    from steno.audio import load_audio_file

    audio = load_audio_file(str(session_dir / "audio-mic.flac"))
    segs = [s for s in transcript.read_segments(session_dir) if s.source == "mic"]
    spans: list[list[float]] = []
    for s in segs:
        if spans and s.t_start - spans[-1][1] < 0.05:
            spans[-1][1] = s.t_end
        else:
            spans.append([s.t_start, s.t_end])
    embedder = create_embedder(cfg.speakers)
    embedder.load()
    stream, t0s, t1s, embs = [], [], [], []
    sp = cfg.speakers
    for a, b in spans:
        clip = audio[int(a * SAMPLE_RATE) : int(b * SAMPLE_RATE)]
        for wa, wb in window_spans(len(clip) / SAMPLE_RATE, sp.window_s, sp.window_hop_s,
                                   sp.min_embed_s):
            embs.append(embedder.embed(clip[int(wa * SAMPLE_RATE) : int(wb * SAMPLE_RATE)]))
            stream.append("mic")
            t0s.append(a + wa)
            t1s.append(a + wb)
    return WindowSet(stream, np.array(t0s), np.array(t1s), np.vstack(embs))


def replay(
    windows: WindowSet, cfg, store: ProfileStore | None, session_dir: Path, *, grow: bool = False
):
    """Run the tracker over the windows. Returns ``(turns, tracker)``; turns are
    ``(t_start, t_end, live speaker id, resolved speaker id)`` in order."""
    groups = group_windows(windows)
    embedder = ReplayEmbedder([e for g in groups for e in g.emb])
    labels = lab.SessionLabels(session_dir)
    labels.init()
    tracker = SpeakerTracker(
        embedder, cfg.speakers, store, labels, profiles_cfg=cfg.profiles if grow else None
    )
    raw: list[tuple[float, float, str]] = []
    for g in groups:
        audio = np.zeros(int(round((g.t1 - g.t0) * SAMPLE_RATE)), dtype=np.float32)
        for turn in tracker.identify_turns(g.stream, audio, g.t0):
            raw.append((g.t0 + turn.t_start, g.t0 + turn.t_end, turn.label.speaker_id))
    tracker.close()
    data = labels.load()
    turns = [(a, b, sid, lab.SessionLabels.root(data, sid)) for a, b, sid in raw]
    return turns, tracker


def summarise(turns) -> str:
    live = Counter()
    final = Counter()
    for a, b, sid, root in turns:
        live[sid] += b - a
        final[root] += b - a
    fmt = lambda c: ", ".join(f"{k}={v:.1f}s" for k, v in sorted(c.items()))  # noqa: E731
    return f"live labels : {fmt(live)}\nafter merges: {fmt(final)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", help="session id in STENO_DATA_DIR/sessions")
    ap.add_argument("--embed", action="store_true",
                    help="embed audio-mic.flac when windows.bin is missing")
    ap.add_argument("--no-profiles", action="store_true", help="run without enrolled profiles")
    ap.add_argument("--set", action="append", default=[], metavar="section.key=value")
    ap.add_argument("--auto-update", action="store_true",
                    help="let the end of the replay grow the profile (changes STENO_DATA_DIR!)")
    ap.add_argument("--timeline", action="store_true", help="print one id per turn")
    args = ap.parse_args(argv)

    import tomllib
    cfg = load_config()
    for item in args.set:
        key, _, raw = item.partition("=")
        section, _, name = key.partition(".")
        sub = getattr(cfg, section)
        import dataclasses
        value = tomllib.loads(f"v = {raw}")["v"]
        cfg = dataclasses.replace(cfg, **{section: dataclasses.replace(sub, **{name: value})})

    session = transcript.resolve_session(args.session)
    if session is None:
        print("no such session", file=sys.stderr)
        return 2
    windows = SessionWindows.load(session)
    if windows is None or not len(windows):
        if not args.embed:
            print("session has no windows.bin; use --embed", file=sys.stderr)
            return 2
        windows = windows_from_audio(session, cfg)
    store = None if args.no_profiles else ProfileStore()
    with tempfile.TemporaryDirectory(prefix="steno-replay-") as tmp:
        turns, _ = replay(windows, cfg, store, Path(tmp), grow=args.auto_update)
    print(f"windows={len(windows)} turns={len(turns)}")
    print(summarise(turns))
    if args.timeline:
        print(" ".join(f"{root.replace('P:', 'P')[:3]}" for _, _, _, root in turns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
