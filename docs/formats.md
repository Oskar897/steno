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
```

`<data_dir>` is `%APPDATA%\Steno` on Windows (override: `STENO_DATA_DIR`).

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

Example (fake data):

```json
{"v":1,"seq":3,"t_start":12.4,"t_end":15.9,"wall":"2026-01-01T10:00:12","source":"loopback","speaker":"Remote","text":"Ska vi ta det på fredag?","lang":"sv"}
```

## Cursors ("only what is new")

`cursors/<consumer>.json` = `{"last_seq": <int>}`.
`steno new --consumer claude` prints segments with `seq > last_seq` and then
advances the cursor. `steno new --peek` prints without advancing.
