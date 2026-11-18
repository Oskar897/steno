"""Record yourself reading eval sentences, one WAV per sentence (for ASR benchmarks).

Recordings go to the Steno data dir (``<data_dir>/eval/<name>/``), never into the repo:
    NN.wav       16 kHz mono, one per sentence
    refs.tsv     NN<TAB>reference text (what you were asked to read)

Usage:
    uv run python scripts/record_eval.py                       # code-switch set
    uv run python scripts/record_eval.py --only 3,7            # redo some sentences
    uv run python scripts/record_eval.py --sentences other.txt --name other

Press Enter to start a sentence, Enter again to stop. Type r + Enter to redo the
last one, q + Enter to quit (finished sentences are kept).
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from steno.audio import AudioChunk, CaptureSource  # noqa: E402
from steno.paths import data_dir  # noqa: E402

SR = 16000
HERE = Path(__file__).resolve().parent


def read_sentences(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def record_until_enter() -> np.ndarray:
    """Capture the default mic until the user presses Enter."""
    src = CaptureSource("mic")
    sink: queue.Queue[AudioChunk] = queue.Queue()
    stop = threading.Event()
    parts: list[np.ndarray] = []

    def pump() -> None:
        while not stop.is_set():
            try:
                chunk = sink.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk.samples.size:
                parts.append(chunk.samples)

    src.start(sink, time.monotonic())
    worker = threading.Thread(target=pump, daemon=True)
    worker.start()
    try:
        input("  recording... press Enter when done ")
    finally:
        time.sleep(0.3)  # keep the tail of the last word
        src.stop()
        stop.set()
        worker.join()
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--sentences", type=Path, default=HERE / "codeswitch_sentences.txt")
    ap.add_argument("--name", default="codeswitch", help="eval set folder name")
    ap.add_argument("--only", default="", help="comma-separated sentence numbers to (re)record")
    args = ap.parse_args()

    import soundfile as sf

    sentences = read_sentences(args.sentences)
    out = data_dir() / "eval" / args.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "refs.tsv").write_text(
        "".join(f"{i:02d}\t{s}\n" for i, s in enumerate(sentences, 1)), encoding="utf-8"
    )
    todo = [int(x) for x in args.only.split(",") if x.strip()] or list(range(1, len(sentences) + 1))
    print(f"{len(todo)} sentence(s). Saving to {out}")
    print("Read naturally, as in a meeting. Enter = start/stop, r = redo last, q = quit.\n")

    k = 0
    while k < len(todo):
        i = todo[k]
        print(f"[{i:02d}/{len(sentences)}] {sentences[i - 1]}")
        cmd = input("  Enter to start (q quits) ").strip().lower()
        if cmd == "q":
            break
        if cmd == "r" and k > 0:
            k -= 1
            continue
        audio = record_until_enter()
        if audio.size < SR // 2:
            print("  too short, try again\n")
            continue
        sf.write(out / f"{i:02d}.wav", audio, SR, subtype="PCM_16")
        print(f"  saved {audio.size / SR:.1f} s\n")
        k += 1
    print(f"Done. Recordings in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
