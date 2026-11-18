# Steno

Local, private meeting transcription for Swedish and English, with speaker
recognition, built to work alongside [Claude Code](https://claude.com/claude-code).

> **Status:** early development (layer 1: capture → transcript file; layer 2:
> speaker recognition, uncalibrated; layer 3: post-meeting finalize, summary and to-do).

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
3. Post-meeting finalize (better speakers and text), summary and to-do list via Claude
   (small, fast model)
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

## After the meeting (layer 3)

```powershell
uv run steno record --keep-audio            # keep the audio, it is what finalize works on
uv run steno finalize                       # better speakers + repaired text -> final.jsonl / final.md
uv run steno finalize --speakers 3          # ... with a known number of speakers
uv run steno show --final                   # print it (`steno show` prints the live transcript)
uv run steno summary                        # overview, decisions, open questions -> summary.md
uv run steno todo --person Alice            # explicit commitments only -> todo.md / todo.json
```

`finalize` re-diarizes the kept audio (pyannote segmentation-3.0 windows linked by CAM++
embeddings, Steno's spectral clustering, enrolled people matched by voice; overlapping speech
can get two speakers), transcribes low-confidence live lines again (`--full-asr`: every line;
`[post] asr_model` picks the model), and assigns each word to the speaker turn it overlaps
most. Names given with `steno name` are locked and carried over. The live transcript is never
changed; `[post] auto = true` runs finalize when `steno record` stops (Ctrl+C skips it).
Needs `uv sync --extra asr --extra speakers --extra audio`; the 6 MB segmentation model is
downloaded on first use. On the 3 x 10 min AMI far-field test it halves the live DER, see
[scripts/eval_diarization.py](scripts/eval_diarization.py) (`--system steno-finalize`).

`summary`, `todo` and `finalize --llm-fix` call the Claude Code CLI (`claude -p`, your own
login, no API key in Steno): only the transcript *text* goes to Claude, on stdin, with all
tools disabled; audio and voiceprints never leave the machine. `[llm]` sets the model
(default the Haiku alias), effort and output language (default Swedish). To-do items must
carry a quote that occurs word for word in the transcript, otherwise they are dropped.
`--llm-fix` is off by default: Claude proposes small `{old, new}` corrections that are
applied only when `old` is in the line and the edit is small. In Claude Code the slash
commands `/sammanfatta` and `/todo` run these and show the result.

## License

MIT
