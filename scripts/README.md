# scripts

## benchmark_asr.py

Finds the best local speech-to-text model for a CPU-only laptop. Each model runs
in its own subprocess and reports load time, transcription time, real-time factor
(RTF = processing time / audio duration), peak RSS and, with a reference
transcript, WER/CER (lowercased, punctuation stripped, å/ä/ö kept).

```
uv sync --all-extras
uv run python scripts/benchmark_asr.py --self-test
uv run python scripts/benchmark_asr.py --audio talk.wav --reference talk.txt
uv run python scripts/benchmark_asr.py --audio talk.wav --models kb-whisper-small,whisper-small --threads 6
```

Models (`--models`, comma-separated): `kb-whisper-base`, `kb-whisper-small`,
`whisper-small`, `parakeet-tdt-0.6b-v3`, `whisper-large-v3-turbo`. Downloads go to
the Steno models dir (`steno.paths.models_dir()`); the turbo model is ~1.6 GB and
parakeet ~490 MB.

Output goes to `<data_dir>/benchmarks/<timestamp>/` (override with `--out`): a
markdown table on stdout, `results.json` and one hypothesis `.txt` per run. Nothing
is written inside the repository, and audio/transcripts must never be committed.

The recommendation is the lowest WER among runs with RTF < 0.5, which leaves CPU
headroom for live use next to a call app. Whisper runs use greedy decoding
(`beam_size=1`), `vad_filter=True`, int8; `[auto]` runs use language detection to
judge Swedish/English code-switching.

## eval_diarization.py

Objective, repeatable diarization evaluation (who spoke when) for a single laptop
microphone in a room, CPU only. It scores hypothesis speaker turns against reference
RTTMs with the diarization error rate from `steno.der` (10 ms frames, optimal
speaker mapping, 0.25 s collar on each side of reference boundaries, overlapped
speech scored unless `--skip-overlap`). DER = (miss + false alarm + confusion) /
reference speech; the table also shows hypothesis vs reference speaker counts and the
real-time factor.

```
uv sync --all-extras
uv run python scripts/eval_diarization.py --download --minutes 10     # fetch data once
uv run python scripts/eval_diarization.py                              # all systems
uv run python scripts/eval_diarization.py --system steno-live,sherpa-offline --save-rttm
uv run python scripts/eval_diarization.py --session 20260101-100000 --rttm mine.rttm
uv run python scripts/eval_diarization.py --system steno-live --set speakers.window_s=1.0   # try a setting
```

Data (`--download`, all outside the repo in `<data_dir>/eval/`): three AMI test
meetings (EN2002a, ES2004a, IS1009a), the first `--minutes` of the distant
`Array1-01` channel (one far-field mic, 16 kHz; fetched with an HTTP range request),
plus the word-level reference RTTMs of the pyannote
[AMI-diarization-setup](https://github.com/pyannote/AMI-diarization-setup) repo
(`only_words`). AMI is CC BY 4.0 and needs no registration; do not commit any of it.
The pyannote segmentation-3.0 model for `sherpa-offline` goes to the models dir.

Local mode: `--session <id> --rttm <file>` scores one of your own sessions (needs
`audio-mic.flac`, so record with `--keep-audio`) against an RTTM you annotated
(`SPEAKER <file> 1 <start> <dur> <NA> <NA> <name> <NA> <NA>`). The whole recording is
used unless `--minutes` is given. Keep the audio and RTTM out of the repository.

Systems (`--system`, comma-separated):

| system | what it runs |
|---|---|
| `steno-live` | Steno's live engine replayed offline: Silero VAD, CAM++ embeddings of 1.5 s windows, speaker turns inside each segment, gated online clustering with merges resolved; the transcriber is a fake (one "x" per 0.25 s) so only speaker turns matter |
| `steno-refine` | the same session, then `refine_session` (spectral clustering of all windows, eigengap speaker count) and its labels overlay; RTF includes the live run |
| `sherpa-offline` | sherpa-onnx offline diarization (pyannote segmentation-3.0 + CAM++), speaker count estimated with `--cluster-threshold` (or fixed with `--num-speakers`) |
| `sherpa-offline-oracle` | the same, given the true number of speakers |

The `steno-*` systems run against a temporary `STENO_DATA_DIR` (ONNX models linked in,
no profiles, no sessions), so your real profiles are never read or changed. They use
the built-in default config unless `--config` is given; `--set section.key=value`
(repeatable) overrides single values on top of it. Use `--skip-overlap` to score only
regions where one reference speaker talks (a single-label system cannot get the rest). Segments labelled unknown
(`U`) are dropped from the hypothesis and so count as missed speech.

Output: markdown table per system (one row per file, then mean over files and pooled)
and a summary table; `<data_dir>/eval/results/<timestamp>.json` holds every number
plus the settings (and hypothesis RTTMs with `--save-rttm`). Numbers are only
comparable with the same `--minutes`, `--collar`, `--skip-overlap` and meetings.
