# File formats

Stable, plain-text formats so other tools (Claude Code, a future assistant
system) can consume Steno output without importing Steno.

## Session directory

```
<data_dir>/sessions/<session_id>/      # session_id = YYYYMMDD-HHMMSS
  transcript.jsonl     # source of truth, append-only
  transcript.md        # human-readable mirror, shows the best (refined) text, see revisions.jsonl
  revisions.jsonl      # optional, append-only: better text for lines of transcript.jsonl
  session.json         # metadata: started_at, ended_at, model, sources, stream_origins
  cursors/<consumer>.json   # read position per consumer, e.g. "claude"
  speakers.json        # speaker label layer: cluster/profile id -> display name
  participants.json    # optional: who is in the meeting (profile ids, expected speaker count)
  labels.json          # optional refine overlay: seq -> speaker id (`steno refine`)
  final.jsonl          # optional `steno finalize` result: final transcript (see below)
  final.md             # human-readable mirror of final.jsonl
  final.json           # metadata of the finalize run: speakers, counts, parameters
  clean.jsonl          # optional `steno clean`: the text with Claude's validated corrections
  clean.md, clean.json # human-readable mirror; counts, model and the edits of the run
  profile-updates.bin  # ENCRYPTED optional: voice samples a finished meeting proposes for profiles (biometric)
  profile-updates.json # plain summary of those, without embeddings (see below)
  summary.md           # optional `steno summary`
  todo.md, todo.json   # optional `steno todo`
  embeddings.bin       # ENCRYPTED speaker embeddings, seq -> vector (biometric)
  windows.bin          # ENCRYPTED embeddings of every 1.5 s window, for `steno refine` (biometric)
  diar.bin             # ENCRYPTED incremental pyannote segmentation + local-speaker embeddings
  audio-mic.flac, audio-loopback.flac   # only with `steno record --keep-audio`
```

`session.json` gets `stream_origins` (`{"mic": 0.04, ...}`) when the session ends: the time on
the session clock at which each stream's first audio sample (and so `audio-<stream>.flac`)
starts; `steno finalize` adds it to positions in the audio files. Absent = 0.

Voice profiles live next to the sessions, also encrypted:
`<data_dir>/profiles/<profile id>.bin` (see "Biometric data" below).

`<data_dir>` is `%USERPROFILE%\.steno` on Windows (override: `STENO_DATA_DIR`).

A session filed under a project (desktop app) also has `meeting.json`: `{"folder", "title"}`
when it has a meeting folder on disk, or `{"title", "project": "<project id>"}` when it is kept
in Steno only. A session that belongs to no (registered) project is in the Inbox.
An archived meeting has `archived.json` (`{"archived_at"}`) in its session dir: the app hides
it from lists and counts; nothing else changes.

## Projects and meeting folders (desktop app)

```
<data_dir>/projects.json              # {"projects": [{"id", "name", "path"?, "archived"?, "types": [{"name", "folder"}]}]}
<data_dir>/settings.json              # app settings: auto_finalize, auto_title, auto_clean, auto_summary, auto_todo, glossary, theme, transparent, mini_strip
<subfolder folder>/                   # any folder on disk, e.g. .../Meetings/One-on-One
  <YYYY-MM-DD title>/                 # one folder per meeting (or the user's own meeting folder)
    .gitignore                        # "*", only inside a git repo: meeting text is never committed
    .steno-meeting.json               # {"session": "<session_id>", "title": "...", "own_folder"?}; hidden on Windows
    transcript.md                     # best text (clean > final > refined live), rewritten while recording
    summary.md, todo.md               # copies, when made
```

A project is a label: `id` (12 hex characters), `name`, optionally a linked folder (`path`)
and `archived`. It writes no files. A **subfolder** (`types`) is a name plus an absolute folder
anywhere on disk; one folder belongs to one subfolder of one project only. Meetings without a
subfolder are kept in Steno only (no files on disk); "Save to…" later files them into a
subfolder or into an existing meeting folder inside one. Which project and subfolder a meeting
with a folder belongs to follows from the folder it lies in. A subfolder whose folder holds
meetings cannot get another folder while they are still there; renaming it is fine. A
subfolder whose folder is missing (drive offline, moved) stays listed with a warning; a
recording into it falls back to Steno's storage.

Older registries are still read: entries with only `path` (id from the path, name from the
folder), subfolder folders relative to the project folder, and an old `<project>/Steno/` folder
with meetings, `index.md` and a generated `CLAUDE.md`. Removing such a project takes Steno's own
files out of `Steno/` once it holds no meetings.

A meeting can also be recorded into a folder that already exists (the user's own meeting folder
with an agenda): Steno's files are then written next to the user's files, which Steno never
changes or deletes. Deleting a meeting removes Steno's files only and the folder when it is
empty. Removing a subfolder or project from Steno never deletes folders; with "also delete the
meetings" it deletes their sessions and Steno's files in their folders.

The meeting folder holds only readable text. The session directory above stays the source of
truth (JSONL, revisions, embeddings, kept audio) and lives in the data dir.

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
| `avg_logprob`  | float  | optional: ASR confidence of the utterance, see below |
| `words`        | list   | optional: `[[start, end, word], ...]`, see below |
| `final`        | bool   | `true` in `final.jsonl` only              |

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

`avg_logprob` and `words` are written by the live engine whenever the ASR backend provides
them (faster-whisper does); sessions from before have neither, and every reader must treat both
as optional. `avg_logprob` is the model's average log-probability of the utterance (duration
weighted over the model's segments; closer to 0 is better, below about -1 is poor). When
one VAD segment is split into several lines, each line carries the confidence of the whole
utterance. `words` holds the line's own words with start/end in *session seconds* (the same
clock as `t_start`/`t_end`), rounded to 10 ms; the words joined by spaces are the line's
`text`. `steno finalize` uses both.

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

## revisions.jsonl (rolling refinement)

The live line is transcribed one utterance at a time (a few seconds of lag, little context).
While recording, the refine worker (`[refine]`, `steno.refine`) transcribes every ~25 s of
accumulated speech again as long-form audio, optionally with a bigger model, and appends one
**revision** per line it improved. `transcript.jsonl` is never rewritten; a file without
`revisions.jsonl` is simply a session without refinement, and old readers that only know
`transcript.jsonl` keep working (they see the drafts).

One JSON object per line, written when the revision is final:

| field          | type   | notes                                                       |
|----------------|--------|-------------------------------------------------------------|
| `v`            | int    | schema version, `1`                                         |
| `rev`          | int    | 1-based, strictly increasing within a session               |
| `seq`          | int    | the `transcript.jsonl` line this text replaces              |
| `wall`         | string | ISO 8601 local time the revision was written                |
| `text`         | string | the new text of the line (never empty)                      |
| `lang`         | string | optional                                                    |
| `words`        | list   | optional `[[start, end, word], ...]` in session seconds     |
| `avg_logprob`  | float  | optional, mean log-probability of the words' model segments |
| `model`        | string | optional, the refine model                                  |

Rules: a revision replaces only `text` (and, when present, `words` / `avg_logprob`) of the line
`seq`; the line keeps its `seq`, times, source and speaker, so speaker labels (live ids,
`labels.json`, names) stay attached. If a line has several revisions the one with the highest
`rev` is the best text. A line the worker never reached (too far behind, or the session
ended) has none: its draft is the best text. The text of a window is divided between the
live lines by the words' timestamps; words that fall in no live line (mic speech the echo
gate dropped, noise) are discarded, so refinement cannot bring echo back. A revision that
differs from the draft only in case or punctuation is not written. A trailing line without a
newline is partial and ignored; so is a corrupt line.

`transcript.read_best(session_dir)` returns the lines with the best text applied (same `seq`,
speaker and times; `Segment.rev` is the revision used, never serialized). `transcript.md` is
rewritten with the best text after every revision, and `steno show`, `steno new`, label changes
and `steno finalize` read the best text too. `session.json` gets a `refine` object when the
session ends (`model`, `windows`, `revisions`, `failed`, `skipped_speech_s`,
`fallback_windows`, `diar_windows`).

## Final transcript (`steno finalize`)

`steno finalize` writes three new files; by default (`[post] asr_mode = "refined"`,
`diarization = "fast"`) it takes seconds and needs neither kept audio nor a model: the text is
the best text of the live session and the speakers come from what the live run computed
(`diar.bin`, or else the 1.5 s window embeddings, see below).
`--thorough` / `[post] diarization = "thorough"` and `asr_mode` `longform` / `selective` need
the kept audio (`steno record --keep-audio`).
`transcript.jsonl` stays exactly as the live engine wrote it, and `steno new` and the label
layer keep working on it. `steno show --final` prints the final transcript, `steno summary` and
`steno todo` prefer it when it exists (or the clean text made from it, see below).

`final.jsonl` is schema v1 with these differences from the live file:

- `final` is `true`; `seq` is a fresh 1-based numbering (it is **not** the live `seq`).
- `speaker` is the display name at the time finalize ran; `speaker_id` is in a **new
  namespace**: `S1`, `S2`, ... by first appearance, `P:<profile id>` for an enrolled or
  user-named person, `U` for words that no diarization turn covered (then `speaker` is the
  live label). There is no `provisional`, `speaker_conf` or overlay: the final labels are
  fixed, so rename someone with `steno name` on the live transcript and run finalize again
  (names set that way are locked and carried over, see below).
- Lines are cut where the speaker changes and consecutive lines of one speaker with a pause
  shorter than `[post] merge_gap_s` are joined, so a line is a speaker's stretch of speech,
  at most 20 s. Overlapping speech gives overlapping `t_start`/`t_end` of different
  speakers; the file is ordered by `t_start`.
- `words` and `avg_logprob` are as in the live file (lines that were transcribed again carry
  the new values; a joined line carries the mean confidence). Lines from sessions without word
  timestamps have no `words`.

`final.json`: plain JSON with `created`, `segments`, `live_segments`, `speakers`
(`id`, `name`, `seconds`, `locked`), `asr_mode` (`refined`, `longform`, `selective` or
`off`), `asr_model` (`null` when `off` or `refined`), `diarization_mode` (`fast`, `thorough`
or `none` when the session has no window embeddings: the live labels are kept),
`diarization_source` (fast: `incremental` = `diar.bin`, `windows` = `windows.bin`), `revised_lines`
(refined: live lines whose text is a revision), `asr_fell_back_to_longform` (`refined` was
asked for, but the session had no revisions and has kept audio), `asr_redone` / `asr_flagged` (selective: lines transcribed
again / considered; longform: `asr_redone` = long-form lines before speaker splitting),
`full_asr` (selective mode only), `longform` (`null` unless longform: `lines`, `words`,
`echo_words_dropped`, `streams_kept_live`, `seconds`), `num_speakers`, `llm_fix` (counts and
the rejected edits, which contain transcript text) and timings. No biometric data.

Where the words come from (`[post] asr_mode`): in `refined` (default) the best text of the live
session (`revisions.jsonl`, see above) with its word timestamps, no new ASR; a line without
revision keeps its draft, and a session without any revision falls back to `longform` when it
has kept audio. In `longform` every kept stream
(`audio-mic.flac`, `audio-loopback.flac`, positions shifted by `stream_origins`) is
transcribed once as a whole with `[post] asr_model`, `[asr] language` and `[asr]
initial_prompt`; words replace the live text and nothing of the live words survives, only
the live *lines* are used for two things: (1) the label of words that no diarization turn
covers (`speaker_id` `U`), and (2) the echo rule below. A stream with no kept audio, one whose
long-form pass fails, or one in which the model finds no speech although the live pass did
keeps its live lines (`streams_kept_live`). In `selective` only low-confidence live lines are
transcribed again (`--full-asr`: all); in `off` the live draft words are kept.

Echo rule (longform, sessions with both streams and `[echo] enabled`): the live engine drops
mic utterances that are the loopback audio leaking into the mic and records nothing about
them, so a whole-file transcription of the mic stream would write the remote side's words a
second time as "Me". A mic word is therefore kept when its midpoint lies within 0.4 s of a
live mic line (the live pass judged that speech real, even if the remote was talking) or when
no loopback speech is going on at that time (the union of the live loopback lines and the new
loopback words); otherwise it is dropped and the line is cut around it. Consequence: speech
the live pass dropped as echo stays dropped, and mic speech that overlaps the remote side but
that the live VAD missed completely is lost too.

How the final labels come about, `[post] diarization = "fast"` (default). If the session has
`diar.bin` (incremental diarization during recording, `[refine] diarize`), it is resolved the
way `thorough` does it below (clustering, linking, stitching) without touching audio; the
result is the thorough one except at the edges of windows that held no live speech
(`diarization_source` `incremental`). Otherwise the 1.5 s window
embeddings of the live run (`windows.bin`) are clustered all at once (spectral clustering,
eigengap speaker count or `--speakers N`, the 2-way split test below, minor clusters
reassigned), every stream's window labels are median-filtered and cut into speaker turns, and
the clusters are named and matched to profiles as below. It takes seconds whatever the
meeting length and has the accuracy of `steno refine` (AMI far-field DER ~30 %, overlapping
speech gets one speaker, only live-VAD speech has windows). A session without `windows.bin`
keeps the live labels (`diarization_mode` `none`). `--thorough` (`"thorough"`): pyannote
segmentation-3.0 finds up to three local speakers
(two at a time) in every 10 s window; CAM++ embeddings of each local speaker's solo speech are
clustered with Steno's spectral clustering (speaker count from the eigengap, or
`--speakers N`). When the eigengap says one speaker, a 2-way split is still tested: it is
kept if the smaller cluster has >= `post.split_min_s` seconds of solo speech and the cosine
between the two centroids is <= `post.split_cos` (one dominant speaker plus a quiet one is
otherwise counted as one). Local speakers are linked to the clusters (two that overlap in time never
share one), stitched over the overlapping windows, and the clusters are matched to enrolled
profiles. Each ASR word goes to the speaker turn it overlaps most. A cluster whose time
coincides with live lines named by the user (`steno name`) takes that name and keeps it
(locked); live profile matches also keep their names.

## Clean-up (`steno clean`)

`steno clean [session] [--dry-run]` sends the session's best text (`final.jsonl` if it exists,
else the live transcript with current speaker names) to the Claude Code CLI as
`[id] Speaker: text` lines in chunks, and asks for minimal diffs `{id, old, new}`
(`src/steno/prompts/clean.md`): misrecognised words (above all English terms spelled the way
they sound in Swedish), obvious ASR garbage, punctuation and casing, and immediate stutter
duplicates ("jag jag" -> "Jag"; emphasis such as "nej nej" is kept). It must not paraphrase,
summarise, translate or remove content; fillers stay. Only text is sent, never audio.
`transcript.jsonl` and `final.jsonl` are never changed. `--dry-run` shows the edits and which
ones pass validation and writes nothing.

Every edit is validated before it is applied (the reason of a rejection is recorded):
the id must be a line of that chunk; `old` must occur in the line as whole words (first
occurrence is replaced); case/punctuation-only edits are always fine, any other edit may
change at most `[llm] fix_max_edit` of the line (Levenshtein on case-folded text, line length
counted as at least 30, summed over the edits of a line) and must not turn a word into
something unrelated; digits, number words, weekdays, months and yes/no/negation words must be
identical in `old` and `new`; a capitalised word that is not at the start of a sentence
(a name) in `old` must come back as a similar word, and a capitalised word in `new` must be
similar to a word in `old` or already occur in the transcript (so a mis-hearing can be
corrected towards a name that is in the text, but no name is invented); words may only vanish
when a word directly repeats itself or when words are merged into a similar-looking one
("over fit" -> "overfit"), and at most two words may be added. These checks are heuristics: a
swap of two similar-looking ordinary words can pass, so the clean text is a suggestion and the
original stays the record.

`clean.jsonl` has the line format of `transcript.jsonl` (schema v1); `seq` and the speaker
fields are those of the source text (`final.jsonl` or the live file, **not** renumbered), a
changed line loses its `words`. `clean.md` is its Markdown mirror. `clean.json` holds
`created`, `model`, `effort`, `source` (`final` or `live`), `source_sha256` (of the source
lines' `seq` and text), `lines`, `changed_lines`, `chunks`, `failed_chunks` (a failed chunk
keeps its original text), `proposed`, `applied`, `rejected`, `rejected_by_reason`, `seconds`,
`edits` (`seq`, `old`, `new`) and `rejected_edits` (with `reason`); both lists contain
transcript text. Running `steno clean` again starts from the source text, not from the
previous clean text, and replaces the three files.

`steno summary` and `steno todo` use `clean.jsonl` while `source_sha256` still matches the
current source text (for example not after a new `steno finalize`), with the current speaker
names; otherwise they use `final.jsonl`, else the live transcript. When there is no fresh
`clean.jsonl`, `steno summary`, `steno todo` and the app run the clean-up first (a failed
clean-up is reported and the uncleaned text is used). Their output files end with a line
`_Text used: clean transcript (clean.jsonl), model sonnet._` (or `final` / `live`);
`todo.json` stays a plain array.

## Summary and to-do (`steno summary`, `steno todo`)

Both send transcript *text* (never audio) to the Claude Code CLI (`claude -p`, the user's
own login, no API key) and are described by prompts in `src/steno/prompts/`. They use
`[llm] summary_model` (default `sonnet`; `model`, default `haiku`, is for clean-up, fix and
the title). Before the transcript they send a short context block: the meeting date, its
title, the speaker labels and the glossary from the app's settings.

`summary.md`: Markdown with an overview, key points, decisions, tentative items and open
questions, headings and text in `[llm] language` (Swedish: Översikt, Huvudpunkter, Beslut,
Preliminärt, Öppna frågor), each point with its `[mm:ss]`. Hedged or conditional plans go
under Preliminärt, never under Beslut. Personal follow-up tasks belong in the to-do list, not
under decisions.

`todo.json` (and the same content as `todo.md`, grouped by owner): a JSON array of

```json
{"owner": "Alice", "task": "Send the report", "due": "Friday", "confidence": 0.9,
 "evidence_quote": "I'll send the report on Friday", "t": 65.0}
```

`owner` is a speaker name or `"TBD"`, `due` a string or `null`, `t` seconds since session start
(taken from the line that contains the quote). Only explicit commitments are listed, extracted
from the transcript and never from the summary; an item whose `evidence_quote` does not
occur verbatim in the transcript (whole words, compared case-, punctuation- and
whitespace-insensitively) is discarded, and so are items below `[llm] todo_min_confidence`
(default 0.5) and a second item with the same owner and the same task or quote. `steno todo --person NAME` writes only that person's items (and so replaces a full list; run
it without `--person` to get everyone's again).

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
- `merged`: live clusters merged into another (`S3` now means `S1`); follow the chain. The
  target can be a person: when the live engine finds that a cluster's centroid matches an
  enrolled profile (`[speakers] cluster_match_*`), the cluster is bound to it
  (`"S1": "P:1a2b3c4d"`), so lines written earlier as `S1` now mean that person. Readers that
  follow the chain (`steno.labels`) get this for free.
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

## Participants (`participants.json`)

Plain JSON in the session directory, no biometric data: which enrolled people are expected in
the meeting and how many speakers there should be. The desktop app writes it when a recording
starts with people chosen, and the user can change it during and after the meeting. It is
optional: a missing file, an empty list or a damaged file all mean "anybody".

```json
{"v": 1, "profiles": ["1a2b3c4d", "5e6f7a8b"], "count": 4}
```

- `profiles`: voice profile ids (8 hex characters, the id of `profiles/<id>.bin`, up to 50).
  Names come from the profile store / `speakers.json`, never from this file.
- `count`: expected number of speakers, guests included (1-20), or `null`. It may be larger than
  the list.
- Live recognition (`SpeakerTracker`) reads the file every few seconds (`reload_interval_s`, and
  only reloads profiles when its modification time or size changed). With a non-empty list,
  windows and clusters are matched against those profiles only; other enrolled people can only
  appear as anonymous `S<n>` speakers. `count` replaces `[speakers] max_speakers` as the cap on
  session clusters (without `count` the config value stays the upper bound).
- `steno finalize` uses `count` as the number of speakers when none is given (an explicit
  number wins) and matches the profile store restricted to the list. `final.json` records the
  number actually used in `num_speakers`.
- Naming a speaker as an enrolled person adds that person to a non-empty list.
- Writes are atomic (temp file + replace) and read-modify-write inside the app.

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
  at least 30 s of speech is recommended. What a finished meeting learns is set by
  `[profiles] update`: `ask` (default) keeps the candidates as pending updates until the user
  confirms (`profile-updates.*` below), `auto` adds them at once, `off` never. Either way it is
  at most 5 diverse window embeddings of an already enrolled person who was recognised for
  at least `auto_update_min_s` (tag `auto:<session id>`); it never creates a profile or
  touches anyone who was not recognised. (An older `auto_update = false` reads as `off`.)
- `profile-updates.bin` / `profile-updates.json`: written when a session ends with
  `update = "ask"` and at least one person has candidates (else neither file exists). The
  `.bin` is encrypted like a profile (`npz`: `emb_<profile id>` float32 rows, JSON `meta` with
  per person `name` and `seconds`); the `.json` is plain text for the app's notice and holds no
  voiceprint: `{"v": 1, "profiles": [{"id", "name", "samples", "seconds"}]}` (`seconds` = speech
  recognised as that person). `POST /api/sessions/<id>/profile-updates` adds the stored rows to
  the profile (`ProfileStore.add_embeddings`, tag `auto:<session id>`) or discards them, and
  deletes the data of everyone it handled; data of a deleted person is dropped, and deleting the
  meeting deletes both files with the session folder.
- `embeddings.bin`: seq -> embedding for each line that was long enough to embed
  (`min_embed_s`; the normalised mean of the line's window embeddings), flushed every
  20 lines and at the end of the session. `steno name` learns from these.
- `windows.bin`: one embedding per 1.5 s window (every 0.75 s) of every embedded
  segment, with stream name and session start/end time of the window (`npz`: `stream`
  codes, `t0`, `t1`, `embeddings`, JSON `meta` with the stream names), flushed every
  100 windows and at the end. About 1 KB per second of speech. Sessions recorded
  before windows existed have no such file; `steno refine` then clusters the
  per-line embeddings instead.
- `diar.bin`: written when the session ends if `[refine] diarize = true`: what
  `steno finalize --thorough` computes from the kept audio, computed during the meeting
  instead (pyannote segmentation-3.0 probabilities of every 10 s window that held live speech,
  as float16, and one CAM++ embedding per local speaker of a window, with window index, local
  speaker, seconds of audio and whether it was taken from solo speech; `npz`, compressed, JSON
  `meta` with stream names, step and sample counts). `steno finalize` (default
  `diarization = "fast"`) clusters and stitches it without audio. About 1 MB per 10 min of
  speech.
- Profiles are changed only by explicit commands: `steno enroll`, `steno name`,
  `steno people`. The live stream never enrols anyone.

## Cursors ("only what is new")

`cursors/<consumer>.json` = `{"last_seq": <int>, "last_rev": <int>}` (`last_rev` is left out
while it is 0; a cursor with only `last_seq` is valid).
`steno new --consumer claude` prints segments with `seq > last_seq` and then
advances the cursor. `steno new --peek` prints without advancing.

With refinement (`revisions.jsonl`) the rule is:

- New lines (`seq > last_seq`) are delivered once, with the best text known at that moment: a
  line that was already refined is *not* delivered as a draft first.
- A line delivered earlier is delivered again, under the heading `Corrections: ...`, only if a
  revision newer than `last_rev` changes its text beyond case and punctuation (compared with
  the text the consumer was last shown). The corrected text replaces the old one. Each revision
  is delivered at most once; a revision that only changes case, punctuation or spacing is
  skipped silently.
- The cursor moves to the newest line and the newest revision read. `--peek` changes nothing.
  Cursors are per consumer.

### Stateless mode: `steno new --since`

```
steno new --since start
steno new --since <session>:<last_seq>:<last_rev>
```

Same delivery rule as above, but the position is a **token** passed back and forth instead of a
cursor file; nothing is written (no `cursors/` entry), so any number of readers can follow the
same meeting. The token is `<session id>:<last_seq>:<last_rev>`, e.g. `20261121-195539:42:7`:
the session id (`[A-Za-z0-9_-]`, up to 64 characters), the newest line delivered and the newest
revision read, both non-negative integers (`start` = `session:0:0` of the target meeting). A token
that does not match gives exit code 2 and an error on stderr.

Output (stdout, Markdown): a header, a blank line, the new lines, an optional
`Corrections: ...` block (as above), a blank line and the last line `next: <token>`, always last
and always present; use that value for the next call. Without anything new the body is
`(nothing new)` and the token is unchanged.

```
# Meeting: <title>
project: <name or (none)> | type: <type, if any> | state: recording live | finished | <time range>
session: <session id>
note: ...            (optional: new meeting since the token; summary.md / todo.md exist)
```

The target meeting is `--session ID`, else the latest meeting of the project whose linked folder or
subfolder folder contains the current folder (or `--project PATH`), else the latest meeting overall. If it differs from the
session in the token, a `note:` line says so and it is delivered from its start. With no meeting
at all the output is `(no meeting found ...)` and `next: start`. `--since` cannot be combined with
`--consumer` or `--peek`.
