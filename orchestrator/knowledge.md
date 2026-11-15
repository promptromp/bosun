The ontology (`bosun kg --help`): who reviews or owns what, where to ask, how a process goes;
entities are `<type>/<slug>`, like `repo/exporter`.

- **Look before asking**: `bosun kg lookup <entity>` or `bosun kg search "<words>"`. A brief's ontology section already holds what is known
  for its repo: look up only what it lacks. Act on a confirmed fact and cite its `[id]`; ask only when
  there is nothing or only unconfirmed facts. Read the text output; never `--json`.
- **Record what I state as standing**, in my exact words: `bosun kg propose <subject> <predicate> [<object>]
  [--value '<text>'] --from-user --quote '<my words>'` (an apostrophe in it: `'\''`; `kg add-entity <type>/<slug> --name N` first if new). Never a one-off instruction, someone else's words, or your inference. No `$(...)` or backticks.
- **Corrections:** `bosun kg supersede <id> (--value V | --object E | --close) --from-user --quote '<words>'`.
- **Confirm or reject** a waiting fact (`kg confirm|reject <id> --from-user --quote '<words>'`) only when
  I name it.
- **Say in one line** what you recorded (`recorded: <fact> [id]`) or why it stays proposed (also when the quote is not in my recent prompts).
