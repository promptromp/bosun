## What and why


## How it was tested

- [ ] The commit and push hooks ran (no `--no-verify`): the lint, the unit tests and the smoke sections the change
  reaches (`tests/smoke.sh --changed origin/main --list` shows which); the merge queue runs the rest
- [ ] Wording Claude acts on changed (policy, task files, prompts): say which `tests/llm` cases cover it

## Checklist

- [ ] The docs follow the change, in this pull request: the README, `docs/` (install steps, what runs where,
  known limits), the lists in `CLAUDE.md`, the design note, `ROADMAP.md`
- [ ] Placeholders only: no real names, ids, emails or tokens
