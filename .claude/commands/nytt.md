---
description: Answer using only the transcript text that is new since last time
argument-hint: [question about what was just said]
allowed-tools: Bash(uv run steno new:*)
---

New meeting transcript since the last time you looked (empty means nothing new):

!`uv run steno new --consumer claude`

The user's request: $ARGUMENTS

Answer using the new text above together with transcript excerpts already in
this conversation. Do not read transcript files yourself (that would resend
everything). If the new text is empty and there is no request, reply just
"inget nytt". Answer in the user's language and keep it short.
