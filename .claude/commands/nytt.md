---
description: Answer using only the transcript text that is new since last time
argument-hint: [question about what was just said]
allowed-tools: Bash(uv run steno new:*)
---

New meeting transcript since the last time you looked (empty means nothing new):

!`uv run steno new --consumer claude`

The user's request: $ARGUMENTS

Use ONLY the new transcript text above to answer the request. Do not rely on
earlier transcript text, and do not read transcript files yourself. If the new
text is empty, reply just "inget nytt". Answer in the user's language and keep it short.
