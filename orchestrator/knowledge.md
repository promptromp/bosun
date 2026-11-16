The ontology (`bosun kg --help`): entities are `<type>/<slug>`.

- **Look before asking**: `bosun kg lookup "<name>"` also shows who reviews, owns or approves it and where to ask
  each: one lookup usually answers who and where; `kg search "<words>"` if the name doesn't resolve. Look up
  only what a brief's ontology section lacks. Act on a confirmed fact and cite its `[id]`; ask only if there is
  none. Read the text, never `--json`.
- **Record what I state as standing**, in my exact words, on the entity `kg search` finds (new only if none: `kg add-entity <type>/<slug> --name N`): `bosun kg propose <subject> <predicate> [<object>] [--value '<text>']
  --from-user --quote '<my words>'` (an apostrophe in it: `'\''`). Never a one-off instruction, someone else's words or your inference. No `$(...)` or backticks.
- **Corrections:** `bosun kg supersede <id> (--value V | --object E | --close) --from-user --quote '<words>'`.
- **Confirm or reject** a waiting fact (`kg confirm|reject <id> --from-user --quote '<words>'`) only when
  I name it.
- **Say in one line** what you recorded (`recorded: <fact> [id]`) or why it stays proposed (also when the quote is not in my recent prompts).
