---
title: Investigate or answer a question
kinds: any
may: write findings to a local markdown file
effort: medium
never: post anything
followup: propose the reply to the requester with the exact text.
---
Investigate (no code change):
- Investigate and write the findings to a markdown file in the repo's working tree (do not commit it).
- Do not post messages or comments anywhere.
- When done, for each ledger id: bosun ledger report <id> --summary '<the answer, one line>' --result <path of the findings file> [--open '<what is still unknown, or a decision I need to make>']
