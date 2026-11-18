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
uv run python scripts/benchmark_asr.py --dataset fleurs --n 40 --n-en 20 --models kb-whisper-small,kb-whisper-small@5
```

Models (`--models`, comma-separated; `name@N` sets the Whisper beam size, default 1):
`kb-whisper-base`, `kb-whisper-small`, `kb-whisper-medium`, `kb-whisper-large`,
`whisper-small`, `whisper-large-v3-turbo`, `parakeet-tdt-0.6b-v3` (sherpa-onnx int8),
`qwen3-asr-0.6b` (sherpa-onnx int8, 30 languages incl. Swedish). Downloads go to the Steno
models dir (`steno.paths.models_dir()`); sizes: kb-whisper-small ~0.5 GB, medium ~1.5 GB, large
~3 GB, turbo ~1.6 GB, parakeet ~0.5 GB, qwen3 ~0.9 GB.

Output goes to `<data_dir>/benchmarks/<timestamp>/` (override with `--out`): a
markdown table on stdout, `results.json` (rewritten after every model) and one hypothesis
`.txt` per run (one line per utterance in dataset mode). Nothing is written inside the
repository, and audio/transcripts must never be committed.

The recommendation is the lowest WER among runs with RTF < 0.5, which leaves CPU
headroom for live use next to a call app (in dataset mode: on the Swedish set). Whisper
runs use `vad_filter=True`, int8; `[auto]` runs use language detection to judge
Swedish/English code-switching.

### Dataset mode (`--dataset fleurs|fleurs-sv|fleurs-en`)

Scores each model on a reproducible subset of [Google FLEURS](https://huggingface.co/datasets/google/fleurs)
test data (CC-BY-4.0): `--n` utterances of Swedish (`sv_se`, default 60) and `--n-en` of
English (`en_us`, default 30). Utterances with distinct sentences and 3-29 s length are
picked with a fixed seed (a smaller `--n` is a prefix of a larger one). The audio archive
(~440 MB sv, ~290 MB en) is streamed once and only the selected wavs are kept, under
`<data_dir>/eval/asr/<dataset>/`; later runs are offline.

* Whisper models get the dataset's language forced (`sv` / `en`); parakeet and qwen3 detect it.
  The English set shows how much a Swedish fine-tune degrades on English speech.
  `--language auto` lets Whisper detect the language instead (run label gets `:auto`, the
  note column shows how often it detected the set's language) to judge code-switch handling.
* Each utterance is transcribed separately; WER/CER are corpus-level (errors summed over
  utterances / reference length) after `normalize_text`. RTF = summed transcription time
  (incl. VAD) / summed audio duration; model load and a 5 s warm-up are excluded.
  Peak RSS is the maximum over the timed sets and includes the model.
* The normaliser does not convert between digits and spelled-out numbers, so a model that
  writes "2000" against a reference with "tvåtusen" (or the reverse) is penalised.
* FLEURS is clean read speech: it ranks models but does not predict WER on meetings
  (overlap, room noise, spontaneous speech, code-switching).
* No public Swedish-English code-switching test set with audio was found; check your own
  recordings with `--audio`/`--reference` for that.

### Code-switching strategies (`--dataset local:<name>`)

Compares ways to handle Swedish speech with English terms on your own recordings
(`scripts/record_eval.py` writes `<data_dir>/eval/<name>/refs.tsv` and `NN.wav`; recordings that
are missing are skipped).

```
uv run python scripts/benchmark_asr.py --dataset local:codeswitch --llm-fix \
    --models kb-whisper-small,kb-whisper-small+prompt,kb-whisper-medium+prompt,whisper-large-v3-turbo:auto,whisper-large-v3-turbo:sv+prompt
```

* Strategy grammar: `key[@beam][:lang][+prompt][+hot]`. `:lang` forces sv/en/auto (turbo's
  language detection); `+prompt` passes a generic Swedish meeting text with English terms
  (`INITIAL_PROMPT`, not copied from the eval sentences; override with `--prompt-text`);
  `+hot` passes faster-whisper `hotwords` (`--hotwords-text`). The texts used are stored in
  `results.json`.
* `--llm-fix [STRATEGY...]` adds a `<strategy>+fix` row: that strategy's hypotheses go through
  Steno's `llm_fix` (prompts/fix.md, haiku, effort medium) via the real `claude` CLI. Default
  strategy = the first in `--models`. Only hypothesis text is sent. Its RTF includes the CLI call,
  so it is a post-pass figure, excluded from the live recommendation.
* Columns: WER, CER, **EN-term acc** (share of English units in the references found in the
  hypothesis; split into terms inside Swedish sentences and whole English sentences), RTF.
  Heuristic (see the comment above `ENGLISH_TERMS`): a curated term list where Swedish suffixes
  are accepted ("branchen", "API:et", "testerna"); a sentence with >= 3 English function words is
  English and all its tokens must match exactly.

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
