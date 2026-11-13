# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, not in a public issue: use **Report a vulnerability** on
the repository's [Security tab](https://github.com/promptromp/bosun/security/advisories/new)
(GitHub's private vulnerability reporting). Reports are acknowledged as soon as possible, and
fixes are published with a GitHub security advisory that credits you, unless you prefer
otherwise.

## Supported versions

Fixes land on `main` and ship in the next dated release (a tag `v<YYYY.MM.DD>`, which a weekly workflow
or a manual run makes), so the newest release is the supported version: update with `bosun update`
(`bosun update --check` says whether there is a newer one), or run the install line again. An install on
the `main` channel (`BOSUN_UPDATE_CHANNEL="main"`) follows the head of `main` instead. In a checkout of
your own (not an install `get.sh` made), `git pull` and re-run `./install.sh`.

## What matters most

bosun runs Claude Code, Codex and Antigravity sessions with your credentials and can act in Slack, GitHub and your
tracker, so these are the reports we most want:

- a way around the approvals gate (`lib/guard.py`, `lib/approvals.py`): an outward action, such as
  a Slack message, a merge or a ticket change, that goes out without the approval that names it;
- a window that reaches an MCP server or connector its list does not name, beyond the exceptions
  `docs/design/connectors.md` lists (`lib/connectors.py`, `lib/connectors_claude.py`,
  `lib/connectors_codex.py`, `lib/connectors_agy.py`);
- Antigravity windows (`lib/agy_state.py`, `agents/agy-plugin/`): bosun installs a small plugin into `agy` when
  the first `agy` window starts, whose hooks run without approval and which answers `agy`'s permission
  question for a command. It lets a command run past `agy`'s sandbox only when every part of it is `git`, `gh`
  or `bosun` with an allow-listed subcommand and options, in a bosun window, and does nothing in a person's
  own `agy` sessions. A command outside that list that gets past the sandbox, or a bosun hook that does not
  stay quiet outside a bosun window, is a report we want. Repo git hooks do not run in these windows, for the
  same reason;
- the console (`bosun console`): anything reachable from another host or origin, or without its
  token;
- secrets or personal data that reach logs, the ledger, learning digests or the knowledge base
  without redaction (`lib/redact.py`);
- the launchd jobs and scripts: a way to make them run code that another user can write.

Problems in Claude Code itself go to Anthropic, in Codex itself to OpenAI, and in Antigravity itself to Google, through their responsible
disclosure policies.

## Bug bounties

There is no bug bounty program.
