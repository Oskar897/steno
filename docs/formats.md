# File formats

Stable, plain-text formats so other tools (Claude Code, a future assistant
system) can consume Steno output without importing Steno.

## Session directory

```
<data_dir>/sessions/<session_id>/      # session_id = YYYYMMDD-HHMMSS
  transcript.jsonl     # source of truth, append-only
  transcript.md        # human-readable mirror, regenerated/appended
  session.json         # metadata: started_at, ended_at, model, sources
  cursors/<consumer>.json   # read position per consumer, e.g. "claude"
  speakers.json        # speaker label layer: cluster/profile id -> display name
  labels.json          # optional refine overlay: seq -> speaker id (`steno refine`)
  embeddings.bin       # ENCRYPTED speaker embeddings, seq -> vector (biometric)
  windows.bin          # ENCRYPTED embeddings of every 1.5 s window, for `steno refine` (biometric)
  audio-mic.flac, audio-loopback.flac   # only with `steno record --keep-audio`
```

Voice profiles live next to the sessions, also encrypted:
`<data_dir>/profiles/<profile id>.bin` (see "Biometric data" below).

`<data_dir>` is `%USERPROFILE%\.steno` on Windows (override: `STENO_DATA_DIR`).

## transcript.jsonl (v1)

One JSON object per line, written only when a segment is final.

| field     | type   | notes                                          |
|-----------|--------|------------------------------------------------|
| `v`       | int    | schema version, `1`                            |
| `seq`     | int    | 1-based, strictly increasing within a session  |
| `t_start` | float  | seconds since session start                    |
| `t_end`   | float  | seconds since session start                    |
| `wall`    | string | ISO 8601 local time of segment start           |
| `source`  | string | `"mic"` or `"loopback"`                        |
| `speaker` | string | display label, e.g. `"Me"`, `"Remote"`, a name |
| `text`    | string | transcribed text                               |
| `lang`    | string | detected/forced language code, e.g. `"sv"`     |
| `speaker_id`   | string | optional, see below                       |
| `speaker_conf` | float  | optional, see below                       |
| `provisional`  | bool   | optional, see below                       |

`speaker_id`, `speaker_conf` and `provisional` are written only when speaker
recognition is active; layer-1 lines simply omit them (still schema `v: 1`,
readers must treat all three as optional).

- `speaker_id`: `"S1"`, `"S2"`, ... = anonymous cluster of this session;
  `"P:<profile id>"` = recognised enrolled person; `"U"` = unknown (short segment
  with no earlier speaker on that stream).
- `speaker_conf`: cosine similarity behind the decision, averaged over the turn's
  1.5 s windows (to the profile, or to the cluster centroid; `0.0` for a short
  segment that inherited the previous speaker of its stream and for the first window
  of a new cluster).
- `provisional`: `true` for cluster labels and inherited speakers (live labels can
  change when clusters merge or after `steno refine`), `false` for a profile match.
  A new live speaker is *tentative* until it has at least 3.5 s of speech from two
  segments; tentative speakers that never reach that (a cough, a one-off noise) are
  folded into the nearest real speaker via the `merged` map below, at the latest when
  the session ends. Until then they appear as their own `S<n>`.
- `speaker` stays the display label *at write time* ("Speaker 2", "Alice").
  The JSONL is never rewritten, so names assigned later live in the label layer.

A VAD segment in which the speaker changes (a short interjection inside someone
else's long turn) is written as **one line per speaker turn**: consecutive `seq`
values, `t_start`/`t_end` of the turns are disjoint parts of the segment, and the
words of the segment are divided between the lines by their timestamps (a turn that
received no word gets no line). Readers should therefore not assume that one line is
one breath of speech; consecutive lines by the same speaker are normal.

Example (fake data):

```json
{"v":1,"seq":3,"t_start":12.4,"t_end":15.9,"wall":"2026-01-01T10:00:12","source":"loopback","speaker":"Remote","text":"Ska vi ta det på fredag?","lang":"sv"}
```

## Speaker label layer

Plain JSON, no biometric data. Readers (`steno new`, the Markdown mirror, a future
UI) show the *current* name for a segment, resolved in this order: `labels.json`
overlay, then the segment's own `speaker_id`, then its written `speaker`.

`speakers.json`:

```json
{"v": 1,
 "clusters": {"S1": {"name": "Alice", "profile_id": "1a2b3c4d"}, "S2": {}},
 "merged":   {"S3": "S1"},
 "profiles": {"1a2b3c4d": "Alice"}}
```

- `clusters`: names given to clusters (`steno name`). An unnamed `S<n>` shows as
  `Speaker <n>`.
- `merged`: live clusters merged into another (`S3` now means `S1`); follow the chain.
- `profiles`: display names of recognised/enrolled people (`P:<id>` -> name), kept
  here so readers never need the encrypted profile store.

`labels.json` (written only by `steno refine`):

```json
{"v": 1, "created": "2026-01-01T10:30:00",
 "segments": {"1": {"speaker_id": "P:1a2b3c4d", "name": "Alice", "conf": 0.94},
              "2": {"speaker_id": "S1", "name": "Speaker 1", "conf": 0.91}}}
```

Refine clusters the window embeddings of the whole session (spectral clustering,
speaker count from the eigengap, or `--speakers N`) and gives each line the majority
cluster of the windows inside it. It replaces the cluster namespace in `speakers.json` (unmatched clusters are
renumbered `S1`, `S2`, ... by first appearance and `merged` is cleared), so ids in
the overlay and in the live JSONL lines of the same session are different
namespaces; always resolve through the overlay first. `name` in the overlay is a
snapshot, the current name comes from `speakers.json`. `transcript.md` is
regenerated by `steno refine`, `steno name` and `steno people rename` (skipped
while a recording still holds the file).

## Biometric data

Voice profiles and session embeddings are voiceprints (GDPR special-category
data). Both are written as encrypted files: Windows DPAPI in *current user*
scope with an application-specific entropy, so they open only for the same
Windows account on the same machine and cannot be copied to another PC. Files
start with the header `STENO-DPAPI1` plus a newline; payload is an `npz` archive (float32
embeddings plus a JSON metadata string, no pickles). On non-Windows platforms
encryption is unavailable and Steno refuses to store them, unless
`STENO_INSECURE_TEST=1` (plaintext, header `STENO-PLAIN1`, for tests only).

- Profile: up to 30 embeddings per person (most redundant ones dropped first),
  name, created/updated timestamps, and a per-embedding origin tag (session id or
  `enroll:<timestamp>`). `steno enroll` stores 2.5 s windows (1 s hop) of the
  recording, without outliers (cosine to the mean more than 2 sigma below the average);
  at least 30 s of speech is recommended.
- `embeddings.bin`: seq -> embedding for each line that was long enough to embed
  (`min_embed_s`; the normalised mean of the line's window embeddings), flushed every
  20 lines and at the end of the session. `steno name` learns from these.
- `windows.bin`: one embedding per 1.5 s window (every 0.75 s) of every embedded
  segment, with stream name and session start/end time of the window (`npz`: `stream`
  codes, `t0`, `t1`, `embeddings`, JSON `meta` with the stream names), flushed every
  100 windows and at the end. About 1 KB per second of speech. Sessions recorded
  before windows existed have no such file; `steno refine` then clusters the
  per-line embeddings instead.
- Profiles are changed only by explicit commands: `steno enroll`, `steno name`,
  `steno people`. The live stream never enrols anyone.

## Cursors ("only what is new")

`cursors/<consumer>.json` = `{"last_seq": <int>}`.
`steno new --consumer claude` prints segments with `seq > last_seq` and then
advances the cursor. `steno new --peek` prints without advancing.
