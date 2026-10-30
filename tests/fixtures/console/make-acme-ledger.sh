#!/bin/bash
# Usage: tests/fixtures/console/make-acme-ledger.sh [<out.json>]
#
# Rebuild the console's fixture ledger (default: acme-ledger.json next to this script) with the
# real `bosun ledger` commands, in a scratch state and config dir, so every item, link and
# history entry is one the ledger itself writes. Fictional data only: acme's invoice exporter.
# It covers every kind, the outreach states, the lineage relations, a pending proposal, said
# entries, ticket mismatches and the XSS strings the console must show as text.
#
# Task and outreach ids carry the day they were made, and the tests name them, so the build day is
# pinned: every date of the day it runs (local and UTC, at its start and end) is written as
# FIXTURE_DAY. Times of day stay as they ran.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
OUT="${1:-$HERE/acme-ledger.json}"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
export BOSUN_CONFIG_DIR="$SCRATCH/config" BOSUN_STATE_DIR="$SCRATCH/state"
export BOSUN_LOG_DIR="$SCRATCH/logs" BOSUN_EVENTS_ENABLED=0 BOSUN_REPO_MASK=""
mkdir -p "$BOSUN_CONFIG_DIR"
FIXTURE_DAY="2026-10-20"
TODAY="$(date +%F)"
BUILD_DAYS="$TODAY $(date -u +%F)"
L() { "$ROOT/bin/bosun" ledger "$@" >/dev/null; }
SLACK="https://acme.slack.com/archives"
PR="https://github.com/acme/exporter/pull"
MENTION="slack:C0000000001/1700000001.000001"
TASK="task:$TODAY/1"
DUP="task:$TODAY/2"
FOLDED="task:$TODAY/3"
RELEASE="task:$TODAY/4"

# My PRs, a mention and the task spawned from it.
L upsert "mypr:acme/exporter#102" --kind my-pr --title "VAT breakdown on invoice exports" --url "$PR/102"
L upsert "mypr:acme/exporter#103" --kind my-pr --title "Header fix <script>alert(1)</script>" --url "$PR/103"
L upsert "$MENTION" --kind mention --title "Priya: could you also export credit notes?" \
  --url "$SLACK/C0000000001/p1700000001000001"
L record-source "$SLACK/C0000000001/p1700000001000001" --class mention --item "$MENTION"
L spawn "$MENTION" --title "Export credit notes" --ask "Add credit notes to the invoice export" \
  --via "$SLACK/C0000000001/p1700000001000001" --requester "Priya" --repo "acme/exporter" \
  --done-when "credit notes appear in the monthly export"
L transition "$TASK" accepted --note "go ahead" --evidence "user: yes, do the credit notes"
L record-delegation "$TASK" --window exporter --task new-work --ask "Add credit notes to the export" \
  --requester "Priya" --link "$SLACK/C0000000001/p1700000001000001"
L upsert "mypr:acme/exporter#104" --kind my-pr --title "Credit notes in the invoice export" --url "$PR/104"
L link "$TASK" produces "mypr:acme/exporter#104"
L report "$TASK" --summary "Opened the credit notes PR; numbering still open" --result "$PR/104" \
  --open "whether credit notes reuse the invoice sequence"
L note "$TASK" "Odd note <img src=x onerror=alert(1)> kept as text"

# Tickets: the task tracks a ticket that is already done (a mismatch), #103 an open issue.
L ticket-sync "jira:EXP-12" --status "Done" --title "Credit notes in exports" \
  --url "https://acme.atlassian.net/browse/EXP-12"
L link "$TASK" tracks "jira:EXP-12"
L ticket-sync "ghissue:acme/exporter#7" --status "open" --title "Header shows the wrong date" \
  --url "https://github.com/acme/exporter/issues/7"
L link "mypr:acme/exporter#103" tracks "ghissue:acme/exporter#7"

# Outreach: sent and waited on, proposed (a pending message), replied, one-way (applied).
L reach-out "$TASK" --to "Dana (finance)" --channel slack --purpose question \
  --subject "Do credit notes reuse the invoice number sequence?" --option "reuse" --option "own sequence"
L propose "out:$TASK/1" "Slack DM to Dana: do credit notes reuse the invoice number sequence?"
L sent "out:$TASK/1" --ref "$SLACK/D0000000001/p1792483200000100"
L reach-out "mypr:acme/exporter#102" --to "Marco" --channel slack --purpose review \
  --subject "Could you review the template PR?"
L propose "out:mypr:acme/exporter#102/1" "Slack DM to Marco: could you review $PR/102 when you have a moment?"
L reach-out --to "Lena" --channel email --purpose decision --subject "Ship exporter 2.4 this week?"
L sent "out:$TODAY/1" --ref "<msg-0001@example.com>"
L replied "out:$TODAY/1" --evidence "$SLACK/D0000000002/p1792486800000200" --summary "Yes, Thursday"
L reach-out --to "exporter channel" --channel slack --purpose fyi --subject "Exporter 2.4 goes out Thursday"
L sent "out:$TODAY/2" --ref "$SLACK/C0000000002/p1792490400000300"

# Lineage: a duplicate, a fold, a release blocked by #102 with a pending proposal.
L spawn "$MENTION" --title "Credit notes in CSV" --ask "Export credit notes as CSV too" --via "same thread"
L link "$DUP" duplicate_of "$TASK"
L transition "$DUP" dropped --note "duplicate of $TASK"
L spawn "$MENTION" --title "Credit note totals in the summary sheet" --ask "Show credit note totals" \
  --via "same thread"
L fold "$FOLDED" --into "$TASK" --note "same PR"
L spawn --title "Release exporter 2.4" --ask "Tag and release 2.4" --via "user: release once 102 is in"
L link "$RELEASE" blocked_by "mypr:acme/exporter#102"
L propose "$RELEASE" "accept $RELEASE and bosun delegate '$RELEASE' --window exporter --task new-work"

# Reviews, a learning proposal, a dismissed mention, a mention with a javascript: URL.
L upsert "review:acme/ledger-sync#57" --kind review-request --title "Retry on 429 from the ledger API" \
  --url "https://github.com/acme/ledger-sync/pull/57"
L record-delegation "review:acme/ledger-sync#57" --window ledger-sync --task review --requester "Omar"
L transition "review:acme/ledger-sync#57" delegated --note "handed to ledger-sync"
L report "review:acme/ledger-sync#57" --summary "Reviewed: two comments on the backoff" \
  --result "https://github.com/acme/ledger-sync/pull/57#pullrequestreview-1" --state reviewed
L upsert "td:000000001" --kind td-review --title "Exporter v2 tech design" \
  --url "https://acme.atlassian.net/wiki/spaces/ENG/pages/000000001"
L upsert "learn:$TODAY/1" --kind learning --title "Ask before reusing a number sequence"
L propose "learn:$TODAY/1" "Add to policy.md: ask the finance owner before reusing a number sequence"
L upsert "slack:C0000000003/1700000003.000003" --kind mention --title "Lunch <img src=x onerror=alert(2)>"
L transition "slack:C0000000003/1700000003.000003" dismissed --note "not for me <b>bold</b>"
L upsert "slack:C0000000004/1700000004.000004" --kind mention --title "A link that is not a web link" \
  --url "javascript:alert(document.cookie)"

# What I said in the exporter window: its hook (on only with events) records it on the task the
# window holds; the events it queues stay in the scratch dir.
printf '%s' '{"hook_event_name":"UserPromptSubmit","prompt":"keep the numbering per customer, as Dana said","session_id":"s-0001"}' |
  BOSUN_EVENTS_ENABLED=1 BOSUN_HOOK_SYNC=1 "$ROOT/bin/bosun" hook user-prompt --window exporter

# Pin the build day (see the top), then write it sorted and indented.
BUILD_DAYS="$BUILD_DAYS $(date +%F) $(date -u +%F)"
# shellcheck disable=SC2086  # one argument per day, on purpose
python3 - "$BOSUN_STATE_DIR/ledger.json" "$FIXTURE_DAY" $BUILD_DAYS <<'PY' >"$OUT"
import json, sys
path, pinned, *days = sys.argv[1:]
text = open(path).read()
for day in set(days) - {pinned}:
    text = text.replace(day, pinned)
print(json.dumps(json.loads(text), sort_keys=True, indent=2))
PY
echo "wrote $OUT ($(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["items"]))' "$OUT") items)"
