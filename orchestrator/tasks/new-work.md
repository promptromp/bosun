---
title: New work for me
kinds: any
may: work in a git worktree, push a new branch, open a draft PR
effort: session
never: request reviewers, mark the PR ready, merge, post in Slack
followup: once the PR exists, upsert a `mypr:` item for it and link it to the work (`ledger link '<id>' produces 'mypr:...'`; the report's `next:` line in `ledger reports` has both commands), then propose the review request (mark ready for review, plus a Slack message with the exact text). A draft PR does not notify code owners, so nobody is asked before I approve. A `task:` item is done once that PR merged. Outreach the session recorded for this id (`ledger outreach --parent <id>`) goes through the chores' outreach step; the item is not done while it is open.
---
New work (code changes):
- Work in a git worktree on a new branch. Never commit to the default branch, and never switch branches in a checkout that other sessions share.
- Commit, push the branch and open a draft PR with a clear description.
- A choice someone else must make (a product decision, another team's contract): record each as outreach (shared rules below), list them in the PR description, and build what does not depend on them.
- Do not request reviewers, mark the PR ready, merge, or post on Slack.
- When done, wait for the PR's checks (`gh pr checks <PR URL> --watch`), then for each ledger id: bosun ledger report <id> --summary '<what the PR does, one line>' --result <draft PR URL> --ci <green|red|pending> [--open '<what is left, failing checks, or a decision I need to make>']. Always pass the PR URL as a --result: the orchestrator links the PR to this ledger id from it.
