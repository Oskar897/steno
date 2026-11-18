# Steno

Local, private meeting transcription for Swedish and English, with speaker
recognition, built to work alongside [Claude Code](https://claude.com/claude-code).

> **Status:** early development (layer 1: capture → transcript file; layer 2:
> speaker recognition, uncalibrated).

## Principles

- **Local first.** Audio, transcription and voice recognition run on your
  machine (CPU only). Only text you explicitly send (summary / to-do) leaves it.
- **Private by default.** Recordings, transcripts and voice profiles are stored
  in your user data folder (`%USERPROFILE%\.steno`), never in this repository.
- **Consent.** Voiceprints are biometric personal data (GDPR). Tell everyone
  in the meeting that it is being recorded and transcribed.

## Roadmap

1. Capture microphone + system audio → live transcript file (JSONL + Markdown)
2. Speaker recognition: enrol voices, recognise them next time, name unknowns
3. Summary and to-do list via Claude (small, fast model)
4. Desktop app UI

## Development

```powershell
uv sync --all-extras
git config core.hooksPath .githooks   # enable the public-repo guard
uv run pytest
```

See [docs/formats.md](docs/formats.md) for the transcript, label and cursor formats.

## Speaker recognition (layer 2)

Needs the `speakers` extra (`sherpa-onnx`, pinned). The CAM++ embedding model
(~28 MB) is downloaded to `%USERPROFILE%\.steno\models` on first use.

```powershell
uv run steno enroll Alice --seconds 40      # record your mic, create/extend a profile (>= 30 s of speech)
uv run steno record --keep-audio            # live: enrolled people by name, others "Speaker N"
uv run steno name latest S2 Bob             # name a cluster and learn the voice from it
uv run steno refine                         # offline re-clustering + re-matching of a session
uv run steno refine --speakers 2            # ... with a known number of speakers
uv run steno people list                    # also: delete <name>, rename <old> <new>
```

A segment in which the speaker changes (a short interjection inside a long turn) is
split into one transcript line per speaker turn; a new live speaker is only believed
after a few seconds of speech, so a cough does not become "Speaker 6".

Voice profiles and session embeddings are biometric data: stored encrypted with
Windows DPAPI (current user), never auto-enrolled from the live stream, never in
this repository. Only enrol people who agreed. All thresholds in `[speakers]`
are uncalibrated defaults; tune them on your own microphone and rooms.
`--keep-audio` stores raw per-stream FLAC in the session folder (sensitive).

## License

MIT
