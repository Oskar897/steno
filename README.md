# Steno

Local, private meeting transcription for Swedish and English, with speaker
recognition, built to work alongside [Claude Code](https://claude.com/claude-code).

> **Status:** early development (layer 1: capture → transcript file).

## Principles

- **Local first.** Audio, transcription and voice recognition run on your
  machine (CPU only). Only text you explicitly send (summary / to-do) leaves it.
- **Private by default.** Recordings, transcripts and voice profiles are stored
  in your user data folder (`%APPDATA%\Steno`), never in this repository.
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

See [docs/formats.md](docs/formats.md) for the transcript and cursor formats.

## License

MIT
