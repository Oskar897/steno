---
description: Extract explicit commitments from a meeting (steno todo) and show them
argument-hint: [--person NAME | --all] [session id, default latest]
allowed-tools: Bash(uv run steno todo:*)
---

To-do list from the meeting (only explicit commitments with a verbatim quote from the
transcript; written to `todo.md` and `todo.json` in the session folder):

!`uv run steno todo $ARGUMENTS`

Show the list above to the user as it is, grouped by person. If it is empty, say that no
explicit commitments were found. If the output is an error message instead, report it in
one line. Answer in the user's language. Do not read transcript files yourself.
