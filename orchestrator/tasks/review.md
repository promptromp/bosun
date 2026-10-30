---
title: Review someone else's PR
kinds: review-request
may: post one review with inline comments; APPROVE if nothing blocks merging, otherwise COMMENT; skip a PR that is merged, closed or already approved by someone else, unless the ask names something specific
effort: medium
never: request changes, push to the PR branch, merge, post in Slack
followup: transition the item to `delegated` before you hand it over (back to `requested`, with the error, if delegate fails); the session reports the review and moves the item to `reviewed` itself (or to `closed` / `dismissed` when it skips the review). For a re-review, put 'focus on changes since <reviewed_sha>' in --details.
---
Review someone else's PR:
- First check whether a review is still needed: `gh pr view <url> --json state,mergedAt,reviewDecision,latestReviews`.
  - Merged or closed: do not review. Run `bosun ledger report <id> --state closed --summary 'merged|closed before review'` and stop.
  - Already approved by someone else (reviewDecision APPROVED, or an APPROVED review in latestReviews when the repo requires none): do not review, unless the ask above names something specific for me to look at. Then review only that part. Otherwise run `bosun ledger report <id> --state dismissed --summary 'already approved by <login>; no specific ask'` and stop.
- Run git pull on the base branch, check out the PR and review it.
- Post the review on GitHub as one review with inline comments on the relevant lines.
- If nothing blocks merging, submit it as APPROVE. Otherwise submit it as COMMENT and lead with the blocking points.
- Do not request changes, push to the PR branch, merge, or post anywhere else (Slack, Jira).
- When done, for each ledger id: bosun ledger report <id> --state reviewed --summary '<verdict and the blocking points, one line>' --result <review URL> --field reviewed_at=<ISO time> --field reviewed_sha=<head commit>
