---
description: Summarise a meeting (steno summary) and show the result
argument-hint: [session id, default latest]
allowed-tools: Bash(uv run steno summary:*)
---

Summary of the meeting (written to `summary.md` in the session folder; the text is sent
to Claude through the Claude Code CLI, audio never leaves the machine):

!`uv run steno summary $ARGUMENTS`

Show the summary above to the user as it is (overview, decisions, open questions). If the
output is an error message instead, report it in one line and say what to do (for example
run `steno finalize` first for better speaker names, or log in to Claude Code). Answer in
the user's language. Do not read transcript files yourself.
