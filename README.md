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
4. Desktop app UI (first version: local web UI, `steno ui`)

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
uv run steno finalize --speakers 3          # ... with a known number of speakers (see below)
uv run steno show --final                   # print it (`steno show` prints the live transcript)
uv run steno summary                        # overview, decisions, open questions -> summary.md
uv run steno todo --person Alice            # explicit commitments only -> todo.md / todo.json
```

`finalize` re-diarizes the kept audio (pyannote segmentation-3.0 windows linked by CAM++
embeddings, Steno's spectral clustering, enrolled people matched by voice; overlapping speech
can get two speakers), transcribes the audio again and assigns each word to the speaker turn
it overlaps most. `[post] asr_mode` (or `--asr-mode`) picks how: `longform` (default) runs each
kept stream through `[post] asr_model` (large-v3-turbo, ~1.6 GB download) once as a whole,
`selective` re-transcribes only low-confidence live lines one by one (`--full-asr`: every
line; use a smaller model such as `KBLab/kb-whisper-medium`, per-clip decoding with turbo is
slow), `off` keeps the live words. Mic words that are only the remote side leaking into the
microphone stay dropped, see `docs/formats.md`. `[asr] initial_prompt` (a generic Swedish
text with English terms) is passed to the live model and to finalize. Names given with `steno name` are locked and carried over. The live transcript is never
changed; `[post] auto = true` runs finalize when `steno record` stops (Ctrl+C skips it).
The speaker count is estimated; a quiet participant who only says a few short words can be
missed (finalize then prints a hint): pass `--speakers N` when you know how many people took
part. Needs `uv sync --extra asr --extra speakers --extra audio`; the 6 MB segmentation model is
downloaded on first use. On the 3 x 10 min AMI far-field test (one distant mic, 0.25 s collar,
overlap counted) the diarization error rate falls from 31 % (live) to 18 % (finalize), 13 %
when overlapping speech is not scored; the diarization part takes about 0.4-0.9 min per
10 min of audio on a laptop CPU (`[post] seg_step_s = 1.25` is a little more accurate and twice
as slow); long-form ASR with turbo adds roughly 0.3 x the audio length per stream on a laptop
CPU, selective re-transcription of every line (`--full-asr`) several minutes more. See
[scripts/eval_diarization.py](scripts/eval_diarization.py) (`--system steno-finalize`).

`summary`, `todo` and `finalize --llm-fix` call the Claude Code CLI (`claude -p`, your own
login, no API key in Steno): only the transcript *text* goes to Claude, on stdin, with all
tools disabled; audio and voiceprints never leave the machine. `[llm]` sets the model
(default the Haiku alias), effort (default medium) and output language (default Swedish).
The CLI runs in an empty temporary directory with `--setting-sources ""`, so your own
CLAUDE.md, hooks and MCP servers are not part of the call. To-do items must
carry a quote that occurs word for word in the transcript, otherwise they are dropped.
`--llm-fix` is off by default: Claude proposes small `{old, new}` corrections that are
applied only when `old` is in the line and the edit is small. In Claude Code the slash
commands `/sammanfatta` and `/todo` run these and show the result.

## Desktop-style UI (`steno ui`)

```powershell
uv run steno ui                # starts a local server and opens your browser
uv run steno ui --no-browser   # only print the address (add --port N to fix the port)
```

Record (same sources and config as `steno record`, optional *Keep audio*), watch the live
transcript, name speakers (optionally saving the voice to a profile), open past sessions, run
Finalize / Summary / To-do as background jobs, and manage enrolled people. Same data as the CLI:
it reads and writes the session folders in `%USERPROFILE%\.steno`.

The UI is plain static HTML/CSS/JS in `src/steno/ui/static/` (no build step, no CDN, no web
fonts) served by a stdlib HTTP server with Server-Sent Events, so it can later be wrapped in a
desktop shell. It listens on 127.0.0.1 only; the address printed at start carries a random
per-launch token that every API call must send, and requests with a foreign `Host`/`Origin`
header are refused. Summary and To-do show what is sent: transcript text only, never audio or
voiceprints. Press Ctrl+C in the terminal to quit (a running recording is stopped and saved).

## License

MIT
