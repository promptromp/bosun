# Exporter runbook

## Releasing

1. Merge to `main` (one reviewer from billing; `exporter/tax.py` needs Dana's, see CODEOWNERS).
2. The smoke tests run on the merge commit. A red smoke run stops the release.
3. Deploy to staging and run `python3 scripts/integration_check.py --env staging`.
4. Canary: 5% of exports for an hour. Lena signs off from the dashboards.
5. The deploy bot does the gradual rollout and announces it in #deploys.

Nothing is rolled out on the last business day of the month: finance closes the books that day.

## Hotfix

A hotfix skips the canary hour but never the smoke tests. Post the change and its notes in #deploys; the
release owner approves it there, and the deploy bot rolls it out.

## Rolling back

Ask the deploy bot in #deploys to roll back to the previous version, then open an incident note.
