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

## Live text that improves while you record (rolling refinement)

```powershell
uv run steno record                         # draft after a few seconds, better text every ~25 s of speech
uv run steno new                            # only what is new; improved lines come back as "Corrections"
```

The live draft transcribes one utterance at a time, so it lacks context (kb-whisper-small on the
166 s Swedish + English test: WER 30 %). A background worker (`[refine]`) keeps the last 15
minutes of each stream in memory and, every ~25 s of accumulated speech, decodes that window
again as long-form audio (whole sentences, 30 s chunks, its own VAD) with `[refine] model`
(default kb-whisper-small; `deepdml/faster-whisper-large-v3-turbo-ct2` is better but 3-4x slower).
The better text replaces the draft of the lines it covers: speakers stay attached, the live
JSONL is never rewritten (revisions are appended to `revisions.jsonl`, see
[docs/formats.md](docs/formats.md)), and `transcript.md` always shows the best text. Mic speech
that the echo gate dropped does not come back (only words inside existing lines are used).
It tries not to compete with the draft: one job at a time, its own thread at below-normal
priority (the model's internal worker threads keep normal priority), and it yields to the draft
queue between model segments. If it falls behind (more than `max_backlog_windows`
of unrefined speech) it switches to `[refine] fallback_model` if you set one, and beyond
`skip_after_windows` it skips the oldest speech (the draft stays) and logs a warning.
`steno new --consumer claude` delivers each new line once, with the refined text if it exists,
and an earlier line again, under "Corrections", only when its text changed beyond case and
punctuation.

The same worker runs the accurate speaker analysis (pyannote segmentation + CAM++) on every 10 s
window that held speech, *during* the meeting (`[refine] diarize`, stored encrypted in
`diar.bin`), so stopping only has the last window left to do.

## After the meeting (layer 3)

```powershell
uv run steno record                         # (--keep-audio only for --thorough / --asr-mode longform)
uv run steno finalize                       # seconds: refined text + speakers -> final.jsonl / final.md
uv run steno finalize --speakers 3          # ... with a known number of speakers (see below)
uv run steno finalize --thorough            # slow, kept audio: pyannote over the whole recording
uv run steno finalize --asr-mode longform   # slow, kept audio: whole-file transcription with turbo
uv run steno show --final                   # print it (`steno show` prints the live transcript)
uv run steno clean                          # Claude fixes misheard words/punctuation -> clean.jsonl / clean.md
uv run steno clean --dry-run                # ... only show the proposed edits, write nothing
uv run steno summary                        # overview, decisions, open questions -> summary.md
uv run steno todo --person Alice            # explicit commitments only -> todo.md / todo.json
```

`finalize` is ready in seconds because the heavy work already happened during the meeting.
Text, `[post] asr_mode` (or `--asr-mode`): `refined` (default) is the best text of the live
session (see above), no new ASR and no kept audio needed; `longform` runs each kept stream
through `[post] asr_model` (large-v3-turbo, ~1.6 GB download) once as a whole, `selective`
re-transcribes only low-confidence live lines one by one (`--full-asr`: every line; use a smaller
model such as `KBLab/kb-whisper-medium`, per-clip decoding with turbo is slow), `off` keeps the
draft. A session without refine revisions that has kept audio falls back to `longform`.
Speakers, `[post] diarization` (or `--thorough`): `fast` (default) resolves `diar.bin`, the
incremental pyannote segmentation windows linked by CAM++ embeddings that were computed while
recording (no audio needed; a session without it clusters the live window embeddings instead, as
`steno refine` does); `thorough` runs the same analysis over the kept audio after the meeting.
Either way Steno's spectral clustering finds the speakers, enrolled people are matched by voice
(overlapping speech can get two speakers), and every word goes to the speaker turn it overlaps
most. Mic words that are only the remote side leaking into the microphone stay dropped, see
`docs/formats.md`. `[asr] initial_prompt` (a generic Swedish text with English terms) is passed to
the live and refine models and to longform finalize. Names given with `steno name` are locked and
carried over. The live transcript is never changed; `[post] auto = true` runs finalize when
`steno record` stops (Ctrl+C skips it). The speaker count is estimated; a quiet participant who
only says a few short words can be missed (finalize then prints a hint): pass `--speakers N` when
you know how many people took part. Needs `uv sync --extra asr --extra speakers --extra audio`;
the 6 MB segmentation model is downloaded on first use.

Measured on a laptop CPU (i5-1235U, 12 threads, no GPU), see
[scripts/simulate_live.py](scripts/simulate_live.py) and
[scripts/eval_diarization.py](scripts/eval_diarization.py):

| 166 s test (141 s speech), real-time replay | live draft only | + refine small | + refine turbo |
|---|---:|---:|---:|
| WER of the draft / of the refined text | 29-30 % | 30 % / 24 % | 30 % / 9 % |
| draft latency, median / p90 (s after the utterance) | 3.1 / 3.6 | 3.1 / 3.8 | 3.2 / 4.1 |
| refined text arrives (s after the last line of its window) | - | 10 | 25 |
| stop to finished transcript | 2 s | 10 s | 25 s |
| system CPU, mean / p90 | 34 / 68 % | 38 / 68 % | 46 / 89 % |
| **11 min (loop of the same audio)** | | | |
| draft latency, median / p90 | 4.5 / 5.7 | 4.8 / 6.4 | 6.5 / 8.8 |
| refine decoding per speech second / lag | - | 0.29 / 11 s | 1.10 / 34 s |
| stop to finished transcript | 5 s | 14 s | 36 s |

The stop time is the draft queue plus the last refine window plus `finalize` (0.1-0.4 s); it
does not grow with the meeting. With turbo the worker is slower than real time while the live
draft, the speaker model and the diarizer share the CPU, so the draft slows down (+2 s) and the
stop takes 25-36 s: kb-whisper-small is the default for that reason. Diarization error rate on
the 3 x 10 min AMI far-field test (pooled): live 31 %, `steno refine` 31 %, window
clustering 31 %, incremental pyannote during the meeting 20 % (about 0.05 x the audio length of
background CPU), `--thorough` 18 %. Looping one recording gives bit-identical embeddings, which
fool the speaker count (8 "speakers" for one person in the 11 min run), so those runs say
nothing about speaker accuracy.

`summary`, `todo`, `clean` and `finalize --llm-fix` call the Claude Code CLI (`claude -p`, your own
login, no API key in Steno): only the transcript *text* goes to Claude, on stdin, with all
tools disabled; audio and voiceprints never leave the machine. `[llm]` sets the model
(default the Haiku alias), effort (default medium) and output language (default Swedish).
The CLI runs in an empty temporary directory with `--setting-sources ""`, so your own
CLAUDE.md, hooks and MCP servers are not part of the call. To-do items must
carry a quote that occurs word for word in the transcript, otherwise they are dropped.
`--llm-fix` is off by default: Claude proposes small `{old, new}` corrections that are
applied only when `old` is in the line and the edit is small. `steno clean` is the same idea
as a separate step on the best text you have (final, else live), in chunks and with stricter
checks: it fixes English terms that were spelled the Swedish way ("dokker kontejner" ->
"Docker container"), obvious ASR garbage, punctuation and casing, and never paraphrases,
drops content or changes numbers, weekdays or names. The result is `clean.jsonl` / `clean.md`
(plus `clean.json` with counts and every applied and rejected edit); the original transcripts
are never touched, and `summary` / `todo` use the clean text while it matches its source.
`--dry-run` shows what would change. See `docs/formats.md` for the rules. In Claude Code the slash
commands `/sammanfatta` and `/todo` run these and show the result.

## Desktop-style UI (`steno ui`)

```powershell
uv run steno ui                # starts a local server and opens your browser
uv run steno ui --no-browser   # only print the address (add --port N to fix the port)
```

Record (same sources and config as `steno record`, optional *Keep audio*), watch the live
transcript, name speakers (optionally saving the voice to a profile), open past sessions, run
Finalize / Clean up / Summary / To-do as background jobs, and manage enrolled people. Same data as the CLI:
it reads and writes the session folders in `%USERPROFILE%\.steno`.

The UI is plain static HTML/CSS/JS in `src/steno/ui/static/` (no build step, no CDN, no web
fonts) served by a stdlib HTTP server with Server-Sent Events, so it can later be wrapped in a
desktop shell. It listens on 127.0.0.1 only; the address printed at start carries a random
per-launch token that every API call must send, and requests with a foreign `Host`/`Origin`
header are refused. Clean up, Summary and To-do show what is sent: transcript text only, never audio or
voiceprints. Press Ctrl+C in the terminal to quit (a running recording is stopped and saved).

## License

MIT
