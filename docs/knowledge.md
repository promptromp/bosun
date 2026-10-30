# Knowledge

`bosun kg` keeps what bosun should know about your organization: people (work facts
only), processes and the product, as atomic facts with sources, so a fact stated once is not lost
when the conversation ends (design: [docs/design/knowledge.md](design/knowledge.md)). It
lives in `$BOSUN_CONFIG_DIR/knowledge/`, never in this repo, with its own git history (one
commit per change; `BOSUN_KNOWLEDGE_GIT=0` turns it off, and it is skipped if git is missing).

```sh
bosun kg add-entity process/release --name "Release process" --alias releases
bosun kg add-entity gate/canary-checks --name "Canary checks"
bosun kg propose releases has_step canary-checks --order 3 --source ledger:mypr:acme/api#7 --source report:task:2026-10-01/1
bosun kg propose releases rule --value "a deploy rolls out to production gradually once the canary checks pass" \
  --from-user --quote "it rolls out gradually once the canary checks pass" --transcript <session.jsonl>
bosun kg batch                          # the day's proposals, grouped and numbered
bosun kg batch apply 'confirm all except 2b'
bosun kg context --for releases         # a small cited pack of confirmed facts
```

- **Entities** are `<type>/<slug>` on three axes: people (`person`, `team`), process (`process`,
  `step`, `gate`, `channel`, `system`) and product (`repo`, `service`, `environment`, `feature`,
  `decision`, `principle`, `lesson`). A name or strong alias works anywhere an id does; weak
  aliases are related terms for search. Near-identical names are reported as merge suggestions.
- **Facts** use a closed set of predicates (`bosun kg schema`); a site adds its own in
  `knowledge/schema.json`. About a person only work facts are kept (role, team, title, timezone,
  stated working hours, preferred channels, what they review, own or approve, stated work
  preferences); anything else is refused, as is anything inferred about a person.
- **Confirmation without the spam:** a fact is confirmed at once when the quote of your words is
  in a prompt you typed in that session's transcript (tool results and pasted text never count),
  when two trusted sources of different kinds back a low-risk fact that names no person, or when a
  profile source gives a title or timezone. Slack, PR, ticket and document text never confirms
  anything. The rest waits for `kg batch`: one list, grouped by entity and axis, rework-preventing
  groups first, at most 10 groups, 8 facts a group and 40 facts a day
  (`BOSUN_KNOWLEDGE_BATCH_CAP`, `_GROUP_FACTS`, `_BATCH_FACTS`), answered in one line;
  "confirm all except 2b" leaves 2b proposed, only "reject 2b" rejects it.
  Unreviewed proposals expire after 30 days unless something observes them again.
- **Two timelines:** facts are never edited or deleted when they change: `kg supersede` closes
  one (`valid_to`, `superseded_by`), and `kg facts <entity> --as-of <date>` shows what held then.
- **Maintenance:** `kg lint` reports contradictions, stale facts (`BOSUN_KNOWLEDGE_STALE_DAYS`),
  orphans, alias collisions and expired proposals; person facts not re-observed for 12 months
  are deleted by `kg expire`, and `kg forget person/<slug>` deletes a person on request (with the
  git history, which restarts).
- **Seeding:** `kg import <file>` reads entities and facts as JSON lines, records every fact as a
  proposal (`--auto-confirm` applies the rules above), is a dry run unless `--apply`, and says how
  many daily batches the review takes; `tools/survey_to_kg.py` converts a survey's markdown tables, with a mapping file kept
  next to the survey, into such a file. `bosun doctor` shows the counts.
