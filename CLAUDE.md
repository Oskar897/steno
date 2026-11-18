# Steno — project rules

Local meeting transcription (Swedish + English) with speaker recognition.
Final product is a desktop app; built in layers that are useful on their own.

## Public repo — privacy is non-negotiable
- Never commit audio, transcripts, voiceprints/embeddings, models, `.env`,
  `config.toml`, personal names, e-mail addresses or absolute user paths.
- User data lives in the platform data dir (`%APPDATA%\Steno`), resolved via
  `steno.paths`. Never write user data inside the repo.
- Tests use synthetic audio (generated sine/noise) and fake names only.
- The `.githooks/pre-commit` guard must stay enabled
  (`git config core.hooksPath .githooks`). Never use `--no-verify`.
- Before every commit: review `git status` and `git diff --cached`.

## Constraints
- Runs on CPU only (i5-1235U, 16 GB RAM). Prefer ONNX / CTranslate2, avoid PyTorch.
- Free and open-source dependencies only.

## Conventions
- Python 3.12, `uv`, src layout (`src/steno`). Lint with `ruff`, test with `pytest`.
- Code, comments, docstrings and README in English.
- Transcript format: append-only JSONL, one segment per line (see `docs/formats.md`).
