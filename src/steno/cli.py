"""Command line interface (thin layer over the library). Entry point: ``steno``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from steno import labels, transcript
from steno.config import ASR_MODES, Config, ConfigError, load_config


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
    p.add_argument("--keep-audio", action="store_true",
                   help="save each stream as 16 kHz FLAC in the session dir (sensitive data)")
    _add_asr_overrides(p)

    p = sub.add_parser(
        "new", help="print only the transcript text that is new (stored cursor or --since token)")
    p.add_argument("--session", default="latest",
                   help="'latest' (default) or a session id; 'latest' means the latest meeting "
                        "of the project you are in (with --since) or of --project")
    p.add_argument("--consumer", default=None,
                   help="stored per-session cursor of this reader (default 'claude')")
    p.add_argument("--peek", action="store_true", help="do not advance the cursor")
    p.add_argument("--since", metavar="TOKEN",
                   help="stateless mode: 'start', or the token after 'next:' in the previous "
                        "output; delivers what is new after it and writes no cursor files")
    p.add_argument("--project", metavar="PATH",
                   help="a Steno project folder: 'latest' = its latest meeting (with --since "
                        "the project is detected from the current folder if this is left out)")

    sub.add_parser("sessions", help="list recorded sessions")

    p = sub.add_parser("enroll", help="record your voice from the mic and save/extend a profile")
    p.add_argument("name")
    p.add_argument("--seconds", type=float, default=40.0,
                   help="recording length (default 40; aim for at least 30 s of speech)")

    p = sub.add_parser("name", help="name a speaker cluster of a session and learn the voice")
    p.add_argument("session", help="'latest' or a session id")
    p.add_argument("speaker", help='cluster, e.g. S2 or "Speaker 2"')
    p.add_argument("person", help="name to give")

    p = sub.add_parser("people", help="manage enrolled voice profiles")
    people = p.add_subparsers(dest="people_command", required=True)
    people.add_parser("list", help="list enrolled people")
    pd = people.add_parser("delete", help="delete a person and their voice data")
    pd.add_argument("name")
    pr = people.add_parser("rename", help="rename a person")
    pr.add_argument("old")
    pr.add_argument("new")

    p = sub.add_parser("refine", help="re-cluster a session offline and re-match to profiles")
    p.add_argument("session", nargs="?", default="latest",
                   help="'latest' (default) or a session id")
    p.add_argument("--speakers", type=int, metavar="N",
                   help="force exactly N speakers instead of estimating the count")

    p = sub.add_parser(
        "finalize", help="post-meeting pass: re-diarize the kept audio, repair ASR, write final.*")
    p.add_argument("session", nargs="?", default="latest",
                   help="'latest' (default) or a session id (--thorough and --asr-mode "
                        "longform/selective need --keep-audio)")
    p.add_argument("--speakers", type=int, metavar="N",
                   help="force exactly N speakers instead of estimating the count")
    p.add_argument("--asr-mode", choices=ASR_MODES,
                   help="text (overrides [post] asr_mode): refined = the text the refine worker "
                        "wrote during the meeting, no new ASR (default); longform = each kept "
                        "stream once as a whole with [post] asr_model; selective = "
                        "only low-confidence live lines, one by one; off = keep the live draft")
    p.add_argument("--thorough", action="store_true",
                   help="speakers: pyannote segmentation over the kept audio (slower, more "
                        "accurate; overrides [post] diarization = fast, which resolves what the "
                        "live run computed, in seconds)")
    p.add_argument("--full-asr", action="store_true",
                   help="with --asr-mode selective: transcribe every live line again, not only "
                        "the low-confidence ones (longform transcribes everything anyway)")
    p.add_argument("--llm-fix", action="store_true",
                   help="let Claude propose small corrections of obvious misrecognitions "
                        "(sends the transcript text to the Claude CLI)")

    p = sub.add_parser("show", help="print a session transcript with current speaker names")
    p.add_argument("session", nargs="?", default="latest")
    p.add_argument("--final", action="store_true", help="the final transcript (final.jsonl)")

    p = sub.add_parser("summary", help="summarise a session with Claude (writes summary.md)")
    p.add_argument("session", nargs="?", default="latest")

    p = sub.add_parser("todo", help="extract explicit commitments with Claude (todo.md/.json)")
    p.add_argument("session", nargs="?", default="latest")
    who = p.add_mutually_exclusive_group()
    who.add_argument("--person", metavar="NAME", help="only this person's tasks")
    who.add_argument("--all", action="store_true", help="everyone's tasks, grouped (default)")

    p = sub.add_parser(
        "clean", help="clean up the transcript text with Claude (clean.jsonl/.md, text only)")
    p.add_argument("session", nargs="?", default="latest")
    p.add_argument("--dry-run", action="store_true",
                   help="show the edits Claude proposes and which pass validation; write nothing")

    p = sub.add_parser("app", help="open the desktop app (projects, recording, meetings)")
    p.add_argument("--debug", action="store_true", help="enable the web inspector")

    sub.add_parser("install-shortcuts",
                   help="add Steno to the Start menu and the desktop (Windows)")

    p = sub.add_parser("ui", help="open the local web UI (record, transcripts, speakers, people)")
    p.add_argument("--port", type=int, default=0,
                   help="port on 127.0.0.1 (default: a random free one)")
    p.add_argument("--no-browser", action="store_true",
                   help="only print the address, do not open a browser")

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


def _run_engine(
    cfg: Config, sources, speaker_labels=None, *, finite: bool, keep_audio: bool = False,
    auto_finalize: bool = False,
) -> int:
    from steno.asr import create_transcriber
    from steno.engine import Engine
    from steno.refine import create_refine_models
    from steno.speakers import SpeakerError, create_embedder

    transcriber = create_transcriber(cfg.asr)
    embedder = create_embedder(cfg.speakers) if cfg.speakers.enabled else None
    refine = create_refine_models(cfg)
    engine = Engine(cfg, transcriber, sources, speaker_labels=speaker_labels, embedder=embedder,
                    keep_audio=keep_audio, refine_transcriber=refine.transcriber,
                    refine_fallback=refine.fallback, refine_diarizer=refine.diarizer)
    engine.subscribe(lambda seg: print(_fmt_segment(seg), flush=True))
    print(f"Loading model {transcriber.name} ...", file=sys.stderr, flush=True)
    try:
        session_dir = engine.start()
    except SpeakerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
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
    if auto_finalize:
        needs_audio = (cfg.post.diarization == "thorough"
                       or cfg.post.asr_mode in ("longform", "selective"))
        if keep_audio or not needs_audio:
            print("Finalizing (Ctrl+C to skip; `steno finalize` runs it later) ...",
                  file=sys.stderr, flush=True)
            try:
                _finalize(session_dir, cfg)
            except KeyboardInterrupt:
                print("\nFinalize skipped.", file=sys.stderr)
        else:
            print("[post] auto needs --keep-audio; finalize skipped.", file=sys.stderr)
    return 130 if interrupted and not finite else 0


def cmd_record(args: argparse.Namespace, cfg: Config) -> int:
    from steno.audio import AudioError, create_capture_sources

    mic = cfg.audio.capture_mic and not args.no_mic
    loopback = cfg.audio.capture_loopback and not args.no_loopback
    if not (mic or loopback):
        print("error: nothing to record (both streams disabled)", file=sys.stderr)
        return 2
    try:
        return _run_engine(cfg, create_capture_sources(mic=mic, loopback=loopback), finite=False,
                           keep_audio=args.keep_audio, auto_finalize=cfg.post.auto)
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
    forced = {args.source: args.speaker} if args.speaker else None
    return _run_engine(cfg, [ArraySource(args.source, audio)], forced, finite=True)


def cmd_ui(args: argparse.Namespace, cfg: Config) -> int:
    from steno.ui.server import run_ui

    return run_ui(cfg, port=args.port, open_browser=not args.no_browser)


def _project_at(path: Path):
    """The registered Steno project that contains ``path`` (the deepest one), or None."""
    from steno import projects

    here = os.path.normcase(str(path.resolve()))
    best, best_len = None, -1
    for p in projects.list_projects():
        roots = [p.type_dir(t) for t in p.types] + ([p.path] if p.path is not None else [])
        for folder in roots:
            root = os.path.normcase(str(folder.resolve()))
            if (here == root or here.startswith(root.rstrip("\\/") + os.sep)) \
                    and len(root) > best_len:
                best, best_len = p, len(root)
    return best


def _latest_in_project(project) -> Path | None:
    from steno import paths, projects

    for m in reversed(projects.list_meetings(project)):
        d = paths.sessions_dir() / m.session
        if d.is_dir():
            return d
    return None


def _print_new(new: transcript.NewText) -> None:
    if new.new:
        print(transcript.render_markdown(new.new))
    if new.corrections:
        if new.new:
            print()
        print("Corrections: these lines were sent before; the text below replaces the old text.")
        print(transcript.render_markdown(new.corrections))


def cmd_new(args: argparse.Namespace) -> int:
    if args.since is not None:
        return _cmd_new_since(args)
    session_dir = transcript.resolve_session(args.session)
    if args.project:
        project = _project_at(Path(args.project))
        if project is None:
            print(f"error: {args.project} is not inside a Steno project folder", file=sys.stderr)
            return 1
        if args.session == "latest":
            session_dir = _latest_in_project(project)
    if session_dir is None:
        print(f"(no such session: {args.session})", file=sys.stderr)
        return 0
    new = transcript.fetch_new_best(
        session_dir, args.consumer or "claude", peek=args.peek, resolve=labels.resolve_segments
    )
    _print_new(new)
    return 0


def _clock(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "?"


def _meeting_header(session_dir: Path) -> list[str]:
    """Title, project/type, state and time range of a meeting (the top of ``--since`` output)."""
    from steno import projects

    info = transcript.load_session_info(session_dir)
    project = projects.project_of(session_dir)
    kind = projects.type_of(session_dir)
    started = _clock(info.started_at)
    if info.ended_at is None:
        state, span = "recording live", f"{started} - now"
    else:
        ended = _clock(info.ended_at)
        same_day = started[:10] == ended[:10]
        state, span = "finished", f"{started} - {ended[11:] if same_day else ended}"
    parts = [f"project: {project.name}" if project else "project: (none)"]
    if kind:
        parts.append(f"type: {kind}")
    parts += [f"state: {state}", span]
    return [f"# Meeting: {projects.meeting_title(session_dir)}", " | ".join(parts),
            f"session: {session_dir.name}"]


def _summary_hint(session_dir: Path) -> str | None:
    from steno import projects

    folder = projects.meeting_folder(session_dir)
    names = [n for n in projects.COPIED if folder and (folder / n).is_file()]
    if folder is None or not names:
        return None
    project = projects.project_of(session_dir)
    where = folder.name
    if project is not None and project.path is not None:
        try:
            where = folder.relative_to(project.path).as_posix()
        except ValueError:
            pass
    return (f"note: this meeting is finished and has {' and '.join(names)} in {where}/ - "
            "read that first if you only need an overview of it.")


def _cmd_new_since(args: argparse.Namespace) -> int:
    """Stateless "only what is new": the position travels in the token, nothing is stored."""
    if args.consumer is not None or args.peek:
        print("error: --since is stateless; it cannot be combined with --consumer or --peek",
              file=sys.stderr)
        return 2
    try:
        if args.since == "start":
            token_session, last_seq, last_rev = None, 0, 0
        else:
            token_session, last_seq, last_rev = transcript.parse_token(args.since)
    except transcript.TokenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    project = None
    if args.project:
        project = _project_at(Path(args.project))
        if project is None:
            print(f"error: {args.project} is not inside a Steno project folder", file=sys.stderr)
            return 1
    elif args.session == "latest":
        project = _project_at(Path.cwd())
    if args.session == "latest" and project is not None:
        session_dir = _latest_in_project(project)
        missing = f"in project {project.name}"
    else:
        session_dir = transcript.resolve_session(args.session)
        missing = f"({args.session})"
    if session_dir is None:
        print(f"(no meeting found {missing})")
        print("next: start")
        return 0

    lines = _meeting_header(session_dir)
    if token_session is not None and token_session != session_dir.name:
        lines.append(f"note: your token was for meeting {token_session}; this is a different "
                     "meeting, so it is delivered from its start. Earlier text is outdated.")
        last_seq = last_rev = 0
    if (last_seq, last_rev) == (0, 0) and not transcript.load_session_info(session_dir).recording:
        hint = _summary_hint(session_dir)
        if hint:
            lines.append(hint)
    new, next_seq, next_rev = transcript.compute_new(
        session_dir, last_seq, last_rev, resolve=labels.resolve_segments)
    print("\n".join(lines))
    print()
    if new:
        _print_new(new)
    else:
        print("(nothing new)")
    print()
    print(f"next: {transcript.format_token(session_dir.name, next_seq, next_rev)}")
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


def _store():
    from steno.profiles import ProfileStore

    return ProfileStore()


def _session_or_error(spec: str):
    session_dir = transcript.resolve_session(spec)
    if session_dir is None:
        print(f"error: no such session: {spec}", file=sys.stderr)
    return session_dir


def cmd_enroll(args: argparse.Namespace, cfg: Config) -> int:
    from steno import ops
    from steno.audio import AudioError, CaptureSource, record_seconds
    from steno.speakers import SpeakerError, create_embedder

    if args.seconds < 3:
        print("error: --seconds must be at least 3", file=sys.stderr)
        return 2
    try:
        embedder = create_embedder(cfg.speakers)
        embedder.load()
        print(
            f"Recording {args.seconds:g} s from the microphone for '{args.name}'. Speak normally "
            "(read aloud, in the language you use in meetings). Voiceprints are biometric data; "
            "only enrol people who agreed.", file=sys.stderr, flush=True)
        audio = record_seconds(CaptureSource("mic"), args.seconds)
        result = ops.enroll_audio(
            audio, args.name, _store(), embedder, min_chunk_s=cfg.speakers.min_embed_s,
            window_s=cfg.speakers.enroll_window_s, hop_s=cfg.speakers.enroll_hop_s,
            outlier_sigma=cfg.speakers.enroll_outlier_sigma)
    except (AudioError, SpeakerError, ops.OpsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    verb = "Created" if result.created else "Extended"
    print(f"{verb} profile '{result.profile.name}' ({result.used} window(s) added from "
          f"{result.speech_s:.0f} s of speech, {result.dropped} outlier(s) dropped, "
          f"{result.total} stored).")
    if result.speech_s < ops.ENROLL_SUGGESTED_SPEECH_S:
        print(f"Only {result.speech_s:.0f} s of speech: recognition is more reliable with at "
              f"least {ops.ENROLL_SUGGESTED_SPEECH_S:.0f} s. Run enroll again to add more.",
              file=sys.stderr)
    return 0


def cmd_name(args: argparse.Namespace) -> int:
    from steno import ops
    from steno.profiles import ProfileError

    session_dir = _session_or_error(args.session)
    if session_dir is None:
        return 1
    try:
        res = ops.name_cluster(session_dir, args.speaker, args.person, _store())
    except (ops.OpsError, ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    verb = "created" if res.created else "extended"
    print(f"{res.cluster_id} is now '{res.profile.name}' (profile {verb}, learned from "
          f"{res.used} segment(s), {res.skipped} too short or without embedding).")
    if not res.markdown_updated:
        print("transcript.md is in use (still recording); `steno new` shows the new name.",
              file=sys.stderr)
    return 0


def cmd_people(args: argparse.Namespace) -> int:
    from steno.profiles import ProfileError

    store = _store()
    try:
        if args.people_command == "list":
            people = store.list()
            if not people:
                print("(no enrolled people)", file=sys.stderr)
            for p in people:
                print(f"{p.name}  [{p.id}]  {len(p.embeddings)} embeddings  updated {p.updated}")
        elif args.people_command == "delete":
            profile = store.delete(args.name)
            print(f"Deleted '{profile.name}' and their voice data. Old transcripts keep the "
                  "name as plain text.")
        elif args.people_command == "rename":
            profile = store.rename(args.old, args.new)
            n = _rename_in_sessions(profile.id, profile.name)
            print(f"Renamed to '{profile.name}' ({n} session(s) updated).")
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _rename_in_sessions(profile_id: str, name: str) -> int:
    count = 0
    for info in transcript.list_sessions():
        if labels.SessionLabels(info.dir).rename_profile(profile_id, name):
            labels.regenerate_markdown(info.dir)
            count += 1
    return count


def cmd_refine(args: argparse.Namespace, cfg: Config) -> int:
    from steno import ops

    session_dir = _session_or_error(args.session)
    if session_dir is None:
        return 1
    try:
        res = ops.refine_session(
            session_dir, cfg.speakers, _store(), num_speakers=args.speakers)
    except ops.OpsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Refined {session_dir.name}: {res.embedded}/{res.segments} segments embedded, "
          f"{len(res.speakers)} speaker(s).")
    for sid, name, n in sorted(res.speakers, key=lambda t: -t[2]):
        print(f"  {name:<20} {sid:<12} {n} segment(s)")
    if not res.markdown_updated:
        print("transcript.md is in use and was not regenerated.", file=sys.stderr)
    return 0


def _progress_printer():
    """stderr progress for finalize: a line per stage and per 10 % step."""
    state = {"stage": "", "step": -1}

    def show(stage: str, done: int, total: int) -> None:
        step = int(10 * done / total) if total else 0
        if stage != state["stage"] or step != state["step"]:
            state.update(stage=stage, step=step)
            tail = f" {done}/{total}" if total else ""
            print(f"  {stage}{tail}", file=sys.stderr, flush=True)

    return show


def _finalize(session_dir, cfg: Config, *, speakers=None, full_asr=False, llm_fix=False,
              asr_mode=None, thorough=None) -> int:
    from steno import finalize, llm
    from steno.segmentation import SegmentationError
    from steno.speakers import SpeakerError

    post_edit = (lambda segs: llm.llm_fix(segs, cfg.llm)) if llm_fix else None
    try:
        res = finalize.finalize_session(
            session_dir, cfg, _store(), num_speakers=speakers, full_asr=full_asr,
            asr_mode=asr_mode, thorough=thorough, post_edit=post_edit,
            progress=_progress_printer())
    except (finalize.FinalizeError, SpeakerError, SegmentationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if res.asr_mode == "longform" and res.longform is not None:
        lf = res.longform
        asr = f"longform ASR: {lf.words} words in {lf.seconds:.0f} s"
        if lf.echo_dropped:
            asr += f", {lf.echo_dropped} mic word(s) dropped as echo"
    elif res.asr_mode == "refined":
        asr = f"refined text ({res.revised} of {res.live_segments} live line(s) revised)"
    elif res.asr_mode == "selective":
        asr = f"{res.redone}/{res.flagged} line(s) transcribed again"
    else:
        asr = "live words kept"
    if res.fell_back:
        asr += " (no refine revisions in this session)"
    print(f"Finalized {session_dir.name}: {res.segments} lines (live: {res.live_segments}), "
          f"{len(res.speakers)} speaker(s) [{res.diarization}]; {asr}; "
          f"{res.seconds:.0f} s for {res.audio_s / 60:.1f} min of audio.")
    if res.longform is not None and res.longform.fallback:
        print("  kept the live text of: " + ", ".join(res.longform.fallback)
              + " (long-form ASR failed or found no speech; see the log)", file=sys.stderr)
    for sid, name, secs in res.speakers:
        print(f"  {name:<20} {sid:<12} {secs:6.0f} s")
    if len(res.speakers) == 1 and speakers is None:
        print("  Only one speaker found. A quiet participant with short interjections can be "
              "missed; if you know how many people took part, run `steno finalize --speakers N`.",
              file=sys.stderr)
    if res.llm_fix is not None:
        fix = res.llm_fix
        print(f"  llm-fix: {fix.get('applied', 0)} of {fix.get('proposed', 0)} edit(s) applied, "
              f"{len(fix.get('rejected', []))} rejected, "
              f"{fix.get('failed_batches', 0)} batch(es) failed")
    return 0


def cmd_finalize(args: argparse.Namespace, cfg: Config) -> int:
    session_dir = _session_or_error(args.session)
    if session_dir is None:
        return 1
    try:
        return _finalize(session_dir, cfg, speakers=args.speakers, full_asr=args.full_asr,
                         llm_fix=args.llm_fix, asr_mode=args.asr_mode,
                         thorough=True if args.thorough else None)
    except KeyboardInterrupt:
        print("\nInterrupted; nothing was written.", file=sys.stderr)
        return 130


def cmd_show(args: argparse.Namespace) -> int:
    from steno import finalize

    session_dir = _session_or_error(args.session)
    if session_dir is None:
        return 1
    if args.final:
        segments = finalize.read_final(session_dir)
        if not segments:
            print("error: no final transcript; run `steno finalize` first", file=sys.stderr)
            return 1
    else:
        segments = labels.resolve_segments(session_dir, transcript.read_best(session_dir))
    if segments:
        print(transcript.render_markdown(segments))
    return 0


def _llm_command(args: argparse.Namespace, action) -> int:
    from steno import llm

    session_dir = _session_or_error(args.session)
    if session_dir is None:
        return 1
    try:
        return action(session_dir, llm)
    except llm.LlmError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_summary(args: argparse.Namespace, cfg: Config) -> int:
    def run(session_dir, llm) -> int:
        path, kind = llm.summarize(session_dir, cfg.llm)
        print(f"({kind} transcript; saved as {path.name})", file=sys.stderr)
        print(path.read_text(encoding="utf-8"), end="")
        return 0

    return _llm_command(args, run)


def cmd_todo(args: argparse.Namespace, cfg: Config) -> int:
    def run(session_dir, llm) -> int:
        items, dropped, kind = llm.extract_todos(session_dir, cfg.llm, person=args.person)
        print(f"({kind} transcript; {len(items)} item(s), {dropped} dropped without evidence; "
              f"saved as {llm.TODO_MD_NAME} and {llm.TODO_JSON_NAME})", file=sys.stderr)
        print((session_dir / llm.TODO_MD_NAME).read_text(encoding="utf-8"), end="")
        return 0

    return _llm_command(args, run)


def cmd_clean(args: argparse.Namespace, cfg: Config) -> int:
    from steno import clean

    def run(session_dir, llm) -> int:
        print("Sending the transcript text (never audio) to Claude in chunks ...",
              file=sys.stderr, flush=True)
        res = clean.clean_session(session_dir, cfg.llm, dry_run=args.dry_run,
                                  progress=_progress_printer())
        meta = res.meta
        for e in meta["edits"] if args.dry_run else meta["edits"][:10]:
            print(f"  [{e['seq']}] {e['old']!r} -> {e['new']!r}")
        if not args.dry_run and len(meta["edits"]) > 10:
            print(f"  ... {len(meta['edits']) - 10} more in {clean.CLEAN_META_NAME}")
        for r in meta["rejected_edits"] if args.dry_run else []:
            print(f"  rejected ({r['reason']}): [{r.get('seq')}] {r.get('old')!r} "
                  f"-> {r.get('new')!r}")
        done = "Would apply" if args.dry_run else "Cleaned"
        print(f"{done} ({meta['source']} text, {meta['model']}, {meta['seconds']:.0f} s): "
              f"{clean.summary_line(meta)}.")
        if meta["rejected_by_reason"]:
            print("  rejected: " + ", ".join(
                f"{n} {why}" for why, n in meta["rejected_by_reason"].items()), file=sys.stderr)
        if args.dry_run:
            print("(dry run: nothing written)", file=sys.stderr)
        else:
            print(f"(saved as {clean.CLEAN_NAME}, {clean.CLEAN_MARKDOWN_NAME} and "
                  f"{clean.CLEAN_META_NAME}; summary and todo now use it)", file=sys.stderr)
        return 0

    return _llm_command(args, run)


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
        if args.command == "name":
            return cmd_name(args)
        if args.command == "people":
            return cmd_people(args)
        if args.command == "show":
            return cmd_show(args)
        cfg = _apply_overrides(load_config(args.config), args)
        if args.command == "record":
            return cmd_record(args, cfg)
        if args.command == "transcribe-file":
            return cmd_transcribe_file(args, cfg)
        if args.command == "enroll":
            return cmd_enroll(args, cfg)
        if args.command == "refine":
            return cmd_refine(args, cfg)
        if args.command == "finalize":
            return cmd_finalize(args, cfg)
        if args.command == "summary":
            return cmd_summary(args, cfg)
        if args.command == "todo":
            return cmd_todo(args, cfg)
        if args.command == "clean":
            return cmd_clean(args, cfg)
        if args.command == "ui":
            return cmd_ui(args, cfg)
        if args.command == "install-shortcuts":
            from steno import shortcuts

            try:
                for path in shortcuts.install():
                    print(f"created {path}")
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            return 0
        if args.command == "app":
            from steno.ui.window import run_app

            return run_app(cfg, debug=args.debug)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
