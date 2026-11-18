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
