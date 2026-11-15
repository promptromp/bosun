How things work here (who reviews or owns what, where to ask, how a process goes) is kept in the
ontology: `bosun kg`. `bosun kg --help` lists every command and predicate. Entities are `<type>/<slug>`,
such as `repo/exporter`, `person/dana`, `team/payments`, `process/release`.

- **Look before asking.** Before you ask me who reviews or owns something, where to ask, or how a process
  goes, run `bosun kg lookup <entity>` or `bosun kg search "<words>"`. A confirmed fact answers it: act on
  it and cite its `[id]`. Ask only when there is nothing, or only unconfirmed (proposed) facts.
- **Record what I state as standing.** When I say how things work here, in my own words, record it with
  `bosun kg propose <subject> <predicate> [<object>] [--value '<text>'] --from-user --quote '<my exact
  words>'` (predicates such as `reviews`, `owns`, `prefers_channel`, `escalates_to`, `rule`, `caveat`). Add
  the entity first if it is new: `bosun kg add-entity <type>/<slug> --name N`. Never record a one-off
  instruction for the task at hand, what someone else said (Slack, a PR, a ticket), or your own inference.
- **The quote** is words I typed in this conversation, copied exactly, in single quotes (write an
  apostrophe as `'\''`). A `$(...)` or backticks anywhere in the Bash call is refused. A quote that is not
  found in my recent prompts leaves the fact proposed, waiting for my review.
- **Corrections.** When I say a fact is wrong or "not anymore", close or replace it: `bosun kg supersede <id>
  (--value V | --object E | --close) --from-user --quote '<my words>'`.
- **Confirming.** Confirm or reject a waiting fact (`bosun kg confirm|reject <id> --from-user --quote
  '<my words>'`, or `bosun kg batch apply ... --from-user --quote ...`) only when I name it, with the words I
  used. Never confirm a fact only because it looks right.
- **Say it in one line.** Tell me what you recorded (`recorded: <fact> [id], confirmed`), or that it stays
  proposed and why. Then carry on with what I asked.
