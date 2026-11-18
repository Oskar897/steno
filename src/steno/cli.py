"""Command line interface (thin layer over the library). Entry point: ``steno``."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import replace

from steno import transcript
from steno.config import Config, ConfigError, load_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steno", description="Local meeting transcription.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--config", metavar="PATH", help="config.toml to use")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("devices", help="list audio devices")
    p.add_argument("--all", action="store_true", help="include non-WASAPI devices")

    p = sub.add_parser("record", help="record mic and/or system audio and transcribe live")
    p.add_argument("--no-mic", action="store_true")
    p.add_argument("--no-loopback", action="store_true")
    _add_asr_overrides(p)

    p = sub.add_parser("new", help="print transcript text not yet seen by a consumer")
    p.add_argument("--session", default="latest", help="'latest' (default) or a session id")
    p.add_argument("--consumer", default="claude")
    p.add_argument("--peek", action="store_true", help="do not advance the cursor")

    sub.add_parser("sessions", help="list recorded sessions")

    p = sub.add_parser("transcribe-file", help="transcribe an audio file into a new session")
    p.add_argument("audio")
    p.add_argument("--source", choices=["mic", "loopback"], default="loopback",
                   help="which stream the file counts as (sets the default speaker label)")
    p.add_argument("--speaker", help="speaker label (default: from config for --source)")
    _add_asr_overrides(p)
    return parser


def _add_asr_overrides(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", help="ASR model id (overrides config)")
    p.add_argument("--language", help="sv, en or auto (overrides config)")


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    asr = cfg.asr
    if getattr(args, "model", None):
        asr = replace(asr, model=args.model)
    if getattr(args, "language", None):
        asr = replace(asr, language=args.language)
    return replace(cfg, asr=asr)


def _fmt_segment(seg: transcript.Segment) -> str:
    return f"[{transcript.format_clock(seg.t_start)}] {seg.speaker}: {seg.text}"


# --------------------------------------------------------------------------- commands


def cmd_devices(args: argparse.Namespace) -> int:
    from steno.audio import AudioError, list_devices

    try:
        devices = list_devices()
    except AudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for d in devices:
        if not args.all and d.host_api != "Windows WASAPI":
            continue
        kind = "loopback" if d.is_loopback else ("input" if d.max_input_channels else "output")
        marks = []
        if d.is_default_input:
            marks.append("default mic")
        if d.is_default_output_loopback:
            marks.append("default loopback")
        suffix = f"  <- {', '.join(marks)}" if marks else ""
        print(f"{d.index:>3}  {kind:<8} {d.default_rate:>6} Hz  {d.name}{suffix}")
    return 0


def _run_engine(cfg: Config, sources, labels=None, *, finite: bool) -> int:
    from steno.asr import create_transcriber
    from steno.engine import Engine

    transcriber = create_transcriber(cfg.asr)
    engine = Engine(cfg, transcriber, sources, speaker_labels=labels)
    engine.subscribe(lambda seg: print(_fmt_segment(seg), flush=True))
    print(f"Loading model {transcriber.name} ...", file=sys.stderr, flush=True)
    session_dir = engine.start()
    print(f"Session {session_dir.name}", file=sys.stderr, flush=True)
    interrupted = False
    try:
        if finite:
            engine.wait()
        else:
            print("Recording. Ctrl+C to stop.", file=sys.stderr, flush=True)
            while True:
                time.sleep(0.25)
    except KeyboardInterrupt:
        interrupted = True
    if interrupted:
        print(f"\nStopping, transcribing {engine.backlog} pending utterance(s) "
              "(Ctrl+C again to skip) ...", file=sys.stderr, flush=True)
    try:
        engine.stop(drain=True)
    except KeyboardInterrupt:
        engine.stop(drain=False)
    print(f"Saved session {session_dir.name}", file=sys.stderr)
    return 130 if interrupted and not finite else 0


def cmd_record(args: argparse.Namespace, cfg: Config) -> int:
    from steno.audio import AudioError, create_capture_sources

    mic = cfg.audio.capture_mic and not args.no_mic
    loopback = cfg.audio.capture_loopback and not args.no_loopback
    if not (mic or loopback):
        print("error: nothing to record (both streams disabled)", file=sys.stderr)
        return 2
    try:
        return _run_engine(cfg, create_capture_sources(mic=mic, loopback=loopback), finite=False)
    except AudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_transcribe_file(args: argparse.Namespace, cfg: Config) -> int:
    from steno.audio import ArraySource, AudioError, load_audio_file

    try:
        audio = load_audio_file(args.audio)
    except AudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: cannot decode {args.audio}: {exc}", file=sys.stderr)
        return 1
    labels = {args.source: args.speaker} if args.speaker else None
    return _run_engine(cfg, [ArraySource(args.source, audio)], labels, finite=True)


def cmd_new(args: argparse.Namespace) -> int:
    session_dir = transcript.resolve_session(args.session)
    if session_dir is None:
        print(f"(no such session: {args.session})", file=sys.stderr)
        return 0
    segments = transcript.fetch_new(session_dir, args.consumer, peek=args.peek)
    if segments:
        print(transcript.render_markdown(segments))
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    sessions = transcript.list_sessions()
    if not sessions:
        print("(no sessions)", file=sys.stderr)
        return 0
    for s in sessions:
        state = "recording" if s.recording else "ended"
        print(f"{s.id}  {s.segments:>4} segments  {state:<9}  {s.model}  [{', '.join(s.sources)}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "devices":
            return cmd_devices(args)
        if args.command == "new":
            return cmd_new(args)
        if args.command == "sessions":
            return cmd_sessions(args)
        cfg = _apply_overrides(load_config(args.config), args)
        if args.command == "record":
            return cmd_record(args, cfg)
        if args.command == "transcribe-file":
            return cmd_transcribe_file(args, cfg)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
