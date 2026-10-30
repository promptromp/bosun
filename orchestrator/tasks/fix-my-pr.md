---
title: Address review comments on my PR
kinds: my-pr
may: push new commits to the PR branch; reply in the review threads it addressed (every thread awaiting the author in a fresh `bosun pr-threads --awaiting-author`, any round)
effort: session
never: resolve threads, re-request review, merge, post in Slack, reuse an earlier draft or file for a "review addressed" message
followup: transition the item to `addressing` before you hand it over (back to `changes-requested` if delegate fails). When the session's report says it pushed (its `ci:` line: red or pending, say so in the proposal), run `bosun pr-threads <url> --awaiting-author` first: if it lists a thread, or a review is newer than the report, send those to the window with `delegate --update` instead. Otherwise propose re-requesting review (GitHub, and the `review_thread` Slack thread if known) with the exact message, written from that output (never from an earlier draft) and with the thread counts per review round in the proposal; once that is done, transition to `awaiting-review` with --field last_push_at=<time of the pushed commit>.
---
Address the review comments on my PR:
- Work from the live PR, not from an earlier message, report or file: run `bosun pr-threads <PR URL> --awaiting-author`. It lists every review thread whose last comment is not mine, from every review round, and the threads per round. Address each one it lists, including rounds that came after this task was written.
- Fix them as new commits on the PR branch and push them (no force push).
- Reply in each review thread you addressed with one line on what changed and the commit. A thread you leave open (a question for me, a disagreement): say why in your report's --open.
- Do not resolve threads, re-request review, merge, or post on Slack.
- When done, run `bosun pr-threads <PR URL> --awaiting-author` again: it should list nothing but the threads you name in --open. If you suggest or draft any message saying the review is addressed, write it from that output (thread counts per round), never from an earlier draft or file.
- Wait for the PR's checks on your last push (`gh pr checks <PR URL> --watch`), then for each ledger id: bosun ledger report <id> --summary '<what changed, one line; N threads addressed (round 1: a, round 2: b)>' --result <PR URL> --result <each pushed commit> --result <each thread URL you replied in, one per thread> --ci <green|red|pending> [--open '<threads left open, failing checks, or a decision I need to make>']
