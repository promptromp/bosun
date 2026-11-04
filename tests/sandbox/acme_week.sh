# shellcheck shell=bash
# A week of work in the fictional acme world, one function a day, each running real `bosun` commands
# and checking what they did: review requests, tasks spawned and linked, a workstream, outreach and its
# reply, proposals approved in chat, what I typed in a repo window (through the hook, as Claude Code
# calls it), tickets, a history long enough to be trimmed, and prune. Each day also works the knowledge
# base (`bosun kg`, knowledge_day_<n>): the acme fixture imported, a product fact and its correction,
# people and their work facts, facts I stated (my quote, checked in a session's transcript), reviews,
# and a person deleted on request. Days 1 to 3 run on the release (from before the knowledge base's
# journal, and from before the ledger's or after it), days 4 and 5 on this checkout. Sourced by
# tests/work_e2e.sh after tests/sandbox/e2e_lib.sh (`check`, $OUT, $ROOT); `bosun` is the installed one
# on PATH. A day leaves the ids it made in variables the later days use.

ACME_SLACK="https://acme.slack.com/archives"
# The ids the days make. Tasks: the CSV export and the pagination it needs, a retired cron, docs, the
# JSON export and the retry it needs, a timezone hotfix and its follow-up; outreach to finance and to
# support.
T_CSV="" T_PAGE="" T_CRON="" T_DOCS="" T_JSON="" T_RETRY="" T_HOTFIX="" T_TIMEZONE="" OUT_DANA="" OUT_RAVI=""
REVIEW="review:acme/api#41"
MENTION="slack:C00000000/1700000000.000200"
MYPR="mypr:acme/invoice-exporter#7"
LEDGER_FILE="$HOME/.local/state/bosun/ledger.json"
OLD_TASK_ID='^task:[0-9]{4}-[0-9]{2}-[0-9]{2}/[1-9][0-9]*$'
# Minted on the UTC day of the write: `made` puts the day from before and after the command in its
# place, so the check holds across midnight UTC.
NEW_TASK_ID='^task:@UTCDAY@/[0-9a-hjkmnp-tv-z]{6}$'
# The ids the release makes: counted, or minted when it journals the ledger already
# (tests/work_e2e.sh sets it after installing it).
RELEASE_TASK_ID="$OLD_TASK_ID"
OUTREACH_ID='^out:'

# The knowledge base, and the fact ids the days make: the CSV export's caveat (from before the
# journal), Priya's review area, the first fact the new bosun mints (set by tests/work_e2e.sh) and a
# caveat from day 4. A fact id from before the journal is f<n>; one the journal mints is f- and six
# characters. My words, and the sessions I typed them in (their transcripts are made in $OUT).
KNOWLEDGE="$HOME/.config/bosun/knowledge"
K_CAVEAT="" K_PRIYA="" K_FIRST="" K_DAY4="" KG_SAID="" KG_ID=""
OLD_FACT_ID='^f[1-9][0-9]*$'
NEW_FACT_ID='^f-[0-9a-hjkmnp-tv-z]{6}$'
QUOTE_OLD="the invoice exports freeze on the last business day of the month"
QUOTE_NEW="a CSV export never includes draft invoices"
SESSION_OLD="00000000-0000-4000-8000-0000000000a1"
SESSION_NEW="00000000-0000-4000-8000-0000000000a2"

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

step() { # <description> <ledger arguments...>
  local what="$1"
  shift
  check "$what" bosun ledger "$@"
}

is_id() { # <pattern> <id> <what the command said>
  [[ "$2" =~ $1 ]] && return 0
  echo "$3"
  return 1
}

made() { # <variable> <id pattern> <description> <ledger arguments...>: the id the command printed first
  local name="$1" pattern="$2" what="$3" output before
  shift 3
  before="$(date -u +%F)"
  output="$(bosun ledger "$@" 2>&1)"
  pattern="${pattern//@UTCDAY@/($before|$(date -u +%F))}"
  printf -v "$name" '%s' "${output%%$'\n'*}"
  check "$what: ${!name}" is_id "$pattern" "${!name}" "$output"
}

typed_in() { # <window> <prompt>: what I type in a repo window, as Claude Code passes it to the hook
  jq -n --arg prompt "$2" --arg cwd "$HOME/Development/$1" '{hook_event_name: "UserPromptSubmit",
    session_id: "00000000-0000-4000-8000-000000000001", cwd: $cwd, prompt: $prompt}' |
    BOSUN_EVENTS_ENABLED=1 BOSUN_HOOK_SYNC=1 bosun hook user-prompt --window "$1"
}
said_on() { bosun ledger said "$1" | grep -qF "$2"; }

notes() { # <id> <count>: one note after another, as CI runs come in
  local n=1
  while [ "$n" -le "$2" ]; do
    bosun ledger note "$1" "CI run $n on the streaming branch: green" >/dev/null || return 1
    n=$((n + 1))
  done
}
trimmed() { # <id> [<entries dropped>]: 200 entries, the second the marker counting what went
  bosun ledger show "$1" | jq -e --argjson count "${2:-0}" \
    '(.history | length) == 200 and .history[1].event == "history-trimmed" and ($count == 0 or .history[1].count == $count)'
}

item_ids() { jq -r '.items | keys[]' "$LEDGER_FILE" | sort; }
prune_drops() { # <days> <id...>: an ordinary prune drops exactly these items, no more and no fewer
  local days="$1" before dropped expected
  shift
  before="$(item_ids)"
  bosun ledger prune --days "$days" || return 1
  dropped="$(comm -23 <(echo "$before") <(item_ids))"
  expected="$(printf '%s\n' "$@" | sort)"
  [ "$dropped" = "$expected" ] && return 0
  printf 'dropped:\n%s\nexpected:\n%s\n' "$dropped" "$expected"
  return 1
}
is_ticket() { bosun ledger show "$1" | jq -e '.kind == "ticket"'; }
both_kept() { bosun ledger show "$1" >/dev/null && is_ticket "$2"; } # <item> <ticket>

said() { # <exit code> <output> <text>: the command succeeded and said <text>
  [ "$1" = 0 ] && [[ "$2" == *"$3"* ]] && return 0
  echo "exit $1: $2"
  return 1
}
says() { # <text> <command...>: it succeeds, saying <text>
  local text="$1" output code=0
  shift
  output="$("$@" 2>&1)" || code=$?
  said "$code" "$output" "$text"
}
kg_says() { # <description> <text> <kg arguments...>: what it said stays in KG_SAID
  local what="$1" text="$2" code=0
  shift 2
  KG_SAID="$(bosun kg "$@" 2>&1)" || code=$?
  check "$what" said "$code" "$KG_SAID" "$text"
}
kg_mints() { # <id pattern> <description> <kg arguments...>: the fact id it printed ("ok: <id> ..."), in KG_ID
  local pattern="$1" what="$2"
  shift 2
  KG_SAID="$(bosun kg "$@" 2>&1)"
  KG_ID="$(grep -m 1 '^ok: ' <<<"$KG_SAID")"
  KG_ID="${KG_ID#ok: }"
  KG_ID="${KG_ID%% *}"
  check "$what: $KG_ID" is_id "$pattern" "$KG_ID" "$KG_SAID"
}
kg_made() { # <variable> <id pattern> <description> <kg arguments...>: that fact id, in <variable> too
  local name="$1"
  shift
  kg_mints "$@"
  printf -v "$name" '%s' "$KG_ID"
}
refuses() { # <text> <command...>: it fails, saying <text>
  local text="$1" output
  shift
  if output="$("$@" 2>&1)"; then
    echo "it did not refuse: $output"
    return 1
  fi
  [[ "$output" == *"$text"* ]] && return 0
  echo "$output"
  return 1
}
fact_of() { # <entity> <predicate>: the id of its first fact with that predicate, whatever its status
  bosun kg facts "$1" --predicate "$2" --status all --closed --json | jq -r '.[0].id // empty'
}
a_transcript() { # <session id> <prompt>: a session in which I typed <prompt>, as Claude Code writes it
  jq -cn --arg at "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)" --arg id "$1" --arg prompt "$2" \
    '{type: "user", timestamp: $at, sessionId: $id, message: {role: "user", content: $prompt}}' >"$OUT/$1.jsonl"
  echo "$OUT/$1.jsonl"
}
last_forgotten() { # <jq test>: on the newest record of the people deleted on request
  jq -se "last | $1" "$KNOWLEDGE/forgotten.jsonl"
}

day_1() {
  echo "day 1: a review request, a CSV export and what it needs, a cron retired"
  step "a workstream for the invoice exports" ws add invoice-exports --title "Invoice exports" --repo acme/api
  step "a review request" upsert "$REVIEW" --kind review-request --title "Retry failed invoice exports" \
    --url https://github.com/acme/api/pull/41
  step "... the Slack message that asked for it" record-source "$ACME_SLACK/C00000000/p1700000000000100" \
    --class pr-review --item "$REVIEW"
  step "a message to read, no more" record-source "$ACME_SLACK/C00000000/p1700000000000150" --class fyi
  step "a mention" upsert "$MENTION" --kind mention --title "Is the March export stuck?"
  step "... answered in its thread" transition "$MENTION" "done" --note "answered in the thread: it ran at 02:00"
  made T_CSV "$RELEASE_TASK_ID" "a task from what I said" spawn --title "Add a CSV export for invoices" \
    --ask "Customers want their invoices as CSV" --via "user: add a CSV export to the invoice exporter" --repo acme-api
  made T_PAGE "$RELEASE_TASK_ID" "... and one it needs first" spawn "$T_CSV" --title "Paginate the invoice list endpoint" \
    --ask "The CSV export reads every invoice" --blocks
  step "... part of the workstream" link "$T_CSV" part_of invoice-exports
  step "I accept the CSV export" transition "$T_CSV" accepted --note "accepted in chat" --evidence "user: yes, do the CSV export"
  step "... and the pagination" transition "$T_PAGE" accepted --note "accepted in chat" --evidence "user: paginate it first"
  step "... handed to the API's window" record-delegation "$T_CSV" --window acme-api --task new-work \
    --ask "Add a CSV export for invoices"
  step "... which works on it" working "$T_CSV" --window acme-api
  check "what I typed there is recorded" typed_in acme-api "use ISO 8601 dates in the CSV and keep the header row"
  check "... on the task" said_on "$T_CSV" "ISO 8601 dates"
  made OUT_DANA "$OUTREACH_ID" "a question for finance" reach-out "$T_CSV" --to "Dana (finance)" \
    --subject "Which date format should the CSV use?"
  step "... sent" sent "$OUT_DANA" --ref "$ACME_SLACK/D00000000/p1700000000000300" --at "$(now_utc)"
  step "a reply to the review, proposed" propose "$REVIEW" --send slack --channel C00000000 \
    --thread 1700000000.000100 --text "Reviewed: two small comments on the retry backoff."
  step "... and approved in chat" approve "$REVIEW" --quote "yes, send it"
  made T_CRON "$RELEASE_TASK_ID" "a task for a ticket" spawn --title "Retire the nightly export cron" \
    --ask "The queue replaced it" --via "user: retire the old cron"
  step "... which tracks it" link "$T_CRON" tracks jira:ACME-9
  step "... as the tracker shows it" ticket-sync jira:ACME-9 --status Done --title "Retire the export cron"
  step "... accepted" transition "$T_CRON" accepted --note "accepted in chat" --evidence "user: go ahead"
  step "... and done" transition "$T_CRON" "done" --note "the cron is gone"
  knowledge_day_1
}

day_2() {
  echo "day 2: a reply, a review done, a task finished, a long history"
  step "finance replied" replied "$OUT_DANA" --evidence "$ACME_SLACK/D00000000/p1700000000000400" \
    --summary "ISO 8601 dates, in UTC" --at "$(now_utc)"
  step "... which the task takes" transition "$OUT_DANA" applied --note "the CSV uses ISO 8601 dates in UTC"
  step "the review's reply went out" resolve "$REVIEW" "done" --note "sent in the PR thread"
  step "... and the review is done" transition "$REVIEW" reviewed --note "two comments on the backoff"
  step "the pagination, handed over" record-delegation "$T_PAGE" --window acme-api --task new-work \
    --ask "Paginate the invoice list endpoint"
  step "... and reported done" report "$T_PAGE" --summary "Pagination merged" \
    --result https://github.com/acme/api/pull/42 --ci green --state "done"
  made T_DOCS "$RELEASE_TASK_ID" "a task for later" spawn --title "Document the CSV columns" \
    --ask "Support asks what each column means" --via "user: write down the CSV columns"
  step "my own PR" upsert "$MYPR" --kind my-pr --title "Stream large exports" \
    --url https://github.com/acme/invoice-exporter/pull/7
  check "205 CI runs noted on it" notes "$MYPR" 205
  check "... its history trimmed to 200, with a marker" trimmed "$MYPR" 7
  knowledge_day_2
}

day_3() {
  echo "day 3: a prune, a ticket, a proposal left waiting"
  check "prune drops what finished before yesterday, and only that" prune_drops 1 "$MENTION" "$T_CRON" jira:ACME-9
  check "... and keeps what open work holds" bosun ledger show "$T_PAGE"
  check "what I typed today is recorded" typed_in acme-api "keep the CSV header row in English"
  step "the CSV export tracks its ticket" link "$T_CSV" tracks jira:ACME-12
  step "... as the tracker shows it" ticket-sync jira:ACME-12 --status "In Progress" --title "CSV export for invoices"
  step "the docs task, accepted" transition "$T_DOCS" accepted --note "accepted in chat" --evidence "user: yes, document them"
  step "opening the PR, proposed" propose "$T_CSV" --run "gh pr create --repo acme/api --title 'Add a CSV export for invoices'"
  step "... and approved in chat, not yet used" approve "$T_CSV" --quote "go ahead and open the PR"
  knowledge_day_3
}

day_4() {
  echo "day 4: the CSV export in review, a JSON export starts, a hotfix"
  step "the CSV export's report" report "$T_CSV" --summary "CSV export ready for review" \
    --result https://github.com/acme/api/pull/43 --ci pending
  step "... the approved PR, opened" resolve "$T_CSV" "done" --note "the PR is open"
  step "... my PR" upsert mypr:acme/api#43 --kind my-pr --title "Add a CSV export for invoices" \
    --url https://github.com/acme/api/pull/43
  step "... which the task produced, in review" link "$T_CSV" produces mypr:acme/api#43
  step "... the task done" transition "$T_CSV" "done" --note "the CSV export is in review"
  step "... and its ticket" ticket-sync jira:ACME-12 --status Done
  step "the review request, closed" transition "$REVIEW" closed --note "merged upstream"
  step "a message to read, no more" record-source "$ACME_SLACK/C00000000/p1700000000000500" --class fyi
  made T_JSON "$NEW_TASK_ID" "a new task" spawn --title "Export invoices as JSON" --ask "The ERP import wants JSON" \
    --via "user: add a JSON export too" --repo acme-web
  made T_RETRY "$NEW_TASK_ID" "... and one it needs first" spawn "$T_JSON" --title "Retry an export that timed out" \
    --ask "Large JSON exports time out" --blocks
  step "... part of the workstream" link "$T_JSON" part_of invoice-exports
  step "I accept the JSON export" transition "$T_JSON" accepted --note "accepted in chat" --evidence "user: yes, the JSON export"
  step "... handed to the web's window" record-delegation "$T_JSON" --window acme-web --task new-work \
    --ask "Export invoices as JSON"
  step "... which works on it" working "$T_JSON" --window acme-web
  check "what I typed there is recorded" typed_in acme-web "name the JSON fields like the CSV columns"
  check "... on the task" said_on "$T_JSON" "like the CSV columns"
  made OUT_RAVI "$OUTREACH_ID" "a question for support" reach-out "$T_JSON" --to "Ravi (support)" \
    --subject "Do support agents need the JSON export too?"
  step "... sent" sent "$OUT_RAVI" --ref "$ACME_SLACK/D00000001/p1700000000000600" --at "$(now_utc)"
  step "... replied" replied "$OUT_RAVI" --evidence "$ACME_SLACK/D00000001/p1700000000000700" \
    --summary "No, only the ERP import" --at "$(now_utc)"
  step "opening the PR, proposed" propose "$T_JSON" --run "gh pr create --repo acme/web --title 'Export invoices as JSON'"
  step "... and approved in chat" approve "$T_JSON" --quote "yes, open the JSON PR"
  step "one more CI run on my PR" note "$MYPR" "CI run 206 on the streaming branch: green"
  check "... still 200 entries, one more counted" trimmed "$MYPR" 8
  made T_HOTFIX "$NEW_TASK_ID" "a hotfix" spawn --title "Fix the export timezone" --ask "Exports show local time" \
    --via "user: the exports must be in UTC"
  step "... which tracks its ticket" link "$T_HOTFIX" tracks jira:ACME-14
  step "... as the tracker shows it" ticket-sync jira:ACME-14 --status Done --title "Exports in UTC"
  step "... accepted" transition "$T_HOTFIX" accepted --note "accepted in chat" --evidence "user: fix it now"
  step "... and done" transition "$T_HOTFIX" "done" --note "exports are in UTC"
  knowledge_day_4
}

day_5() {
  echo "day 5: an ordinary prune, and a pruned ticket tracked again"
  # The CSV task is done but its PR is still in review: prune keeps it, and what it links to (the
  # pagination, its closed ticket), so no kept link points at a pruned item. Nothing open reaches the
  # hotfix or its ticket: those go.
  check "prune drops what finished before yesterday, and only that" prune_drops 1 "$REVIEW" "$OUT_DANA" \
    "$T_HOTFIX" jira:ACME-14
  check "... ledger validate --journal passes right after it" bosun ledger validate --journal
  check "... the CSV task stays with its PR in review" bosun ledger show "$T_CSV"
  check "... and with it the pagination and the closed ticket it links to" both_kept "$T_PAGE" jira:ACME-12
  made T_TIMEZONE "$NEW_TASK_ID" "a task for a pruned ticket" spawn --title "Fix the timezone in the March exports too" \
    --ask "March ran before the fix" --via "user: March is still in local time"
  step "... tracks it again" link "$T_TIMEZONE" tracks jira:ACME-14
  check "... so it is back" is_ticket jira:ACME-14
  step "... as the tracker shows it now" ticket-sync jira:ACME-14 --status "In Progress" --title "Exports in UTC"
  step "... and the one pruned before the upgrade" link "$T_TIMEZONE" tracks jira:ACME-9
  check "... which is back too" is_ticket jira:ACME-9
  step "the JSON export's report" report "$T_JSON" --summary "JSON export ready for review" \
    --result https://github.com/acme/web/pull/12 --ci green
  step "... the approved PR, opened" resolve "$T_JSON" "done" --note "the PR is open"
  step "the docs task, done" transition "$T_DOCS" "done" --note "the columns are in the README"
  step "the retry, accepted" transition "$T_RETRY" accepted --note "accepted in chat" --evidence "user: yes, retry them"
  knowledge_day_5
}

# The knowledge base, on the release: the acme fixture, then what a user adds by hand.
knowledge_day_1() {
  echo "  the knowledge base"
  kg_says "the acme knowledge, imported" "imported:" import "$ROOT/tests/fixtures/knowledge/acme-import.jsonl" \
    --apply --auto-confirm
  kg_says "the CSV export, as a feature" "ok: added" add-entity feature/csv-export --name "CSV export" --alias "csv export"
  kg_says "a reviewer of the exports" "ok: added" add-entity person/priya --name "Priya Placeholder"
  kg_says "... and another" "ok: added" add-entity person/marco --name "Marco Placeholder"
  kg_made K_CAVEAT "$OLD_FACT_ID" "a product fact, from the task and its report" propose feature/csv-export caveat \
    --value "CSV exports quote every field" --source "ledger:$T_CSV" --source "report:$T_CSV"
  check "... confirmed by its two sources" said 0 "$KG_SAID" "confirmed (two independent"
  kg_made K_PRIYA "$OLD_FACT_ID" "a person fact" propose person/priya reviews repo/invoice-exporter --source "ledger:$REVIEW"
  kg_says "... what she said she prefers" "added, proposed" propose person/priya stated_preference \
    --value "Wants export questions batched into one thread" --authority stakeholder_said --source "ledger:$OUT_DANA"
  kg_says "... and where he likes to be asked" "added, proposed" propose person/marco prefers_channel \
    channel/acme-prs --source "ledger:$REVIEW"
  kg_mints "$OLD_FACT_ID" "a rule I stated, my quote checked in the session" propose process/release rule \
    --value "Invoice exports freeze on the last business day of the month" --from-user --quote "$QUOTE_OLD" \
    --transcript "$(a_transcript "$SESSION_OLD" "$QUOTE_OLD, so nothing ships then")"
  check "... confirmed by my own words" said 0 "$KG_SAID" "your own words"
}

knowledge_day_2() {
  echo "  the knowledge base"
  kg_says "her review area, confirmed" "$K_PRIYA confirmed" confirm "$K_PRIYA"
  kg_says "the product fact, corrected" "closed, superseded by" supersede "$K_CAVEAT" \
    --value "CSV exports quote every field and write dates in UTC" --source "report:$T_PAGE"
  kg_says "a rule from a PR thread, rejected" "rejected" reject "$(fact_of repo/invoice-exporter rule)"
}

knowledge_day_3() {
  echo "  the knowledge base"
  kg_says "Marco asked to be forgotten" "deleted the person" forget person/marco
  check "... kept as a plain hash, as the release keeps it" last_forgotten 'has("hash") and (has("hash_kind") | not)'
  check "... and not added again without --allow-forgotten" refuses "deleted on request" \
    bosun kg add-entity person/marco --name "Marco Placeholder"
}

# On the new bosun: every write goes through the knowledge journal.
knowledge_day_4() {
  echo "  the knowledge base"
  kg_made K_DAY4 "$NEW_FACT_ID" "a product fact, from the task and its report" propose feature/csv-export caveat \
    --value "Large CSV exports stream page by page" --source "ledger:$T_JSON" --source "report:$T_CSV"
  kg_says "... corrected" "closed, superseded by" supersede "$K_DAY4" \
    --value "Large CSV exports stream 500 rows at a time" --source "report:$T_CSV"
  kg_mints "$NEW_FACT_ID" "a rule I stated, my quote checked in the session" propose feature/csv-export rule \
    --value "A CSV export never includes draft invoices" --from-user --quote "$QUOTE_NEW" \
    --transcript "$(a_transcript "$SESSION_NEW" "and remember: $QUOTE_NEW")"
  check "... confirmed by my own words" said 0 "$KG_SAID" "your own words"
  kg_says "a proposal from the release, confirmed by its old id" "confirmed" confirm "$(fact_of system/deploy-bot posts_to)"
  kg_says "Dana's chat id, for mentions" "recorded a slack id" identify person/dana slack U00000000
  kg_says "the day's review batch" "Knowledge to review" batch
  kg_says "... its first group confirmed in one line" "confirmed," batch apply "confirm 1"
}

knowledge_day_5() {
  echo "  the knowledge base"
  local claim=""
  kg_made claim "$NEW_FACT_ID" "a claim from a Slack thread" propose feature/csv-export caveat \
    --value "CSV exports break on commas" --source slack:C00000000/1700000000.000900
  kg_says "... waits for review, and is rejected" "$claim rejected" reject "$claim"
  kg_says "the new bosun's first fact, closed" "$K_FIRST closed" supersede "$K_FIRST" --close
  kg_says "Dana's title, from the directory" "confirmed (a structured profile source)" propose person/dana title \
    --value "Finance lead" --source profile:directory
  kg_says "the evening's expiry: nothing is old enough yet" "recorded 0 expired proposals" expire
  kg_says "a context pack for the release process" "Knowledge for Release process" context --for releases
  check "kg lint reads it all" bosun kg lint
}
