#!/bin/bash
# Hermetic smoke tests: temp HOME, fixture sessions, isolated tmux socket, no Claude calls,
# no launchd or pmset changes. Run: tests/smoke.sh
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1
# Never touch the repository this runs from: a git hook (pre-push) or another repository's tooling
# may export GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_COMMON_DIR, GIT_CONFIG*, ... and every git
# command below (git init, git log in scratch folders) would then read or write that repository.
for git_var in $(compgen -e | grep '^GIT_'); do unset "$git_var"; done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# What must not change during the run: this checkout's HEAD, the shared repository's HEAD and
# core.bare (checked at the end).
repo_state() {
  local common
  common="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 0
  printf 'head=%s main=%s bare=%s\n' "$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)" \
    "$(git --git-dir="$common" rev-parse HEAD 2>/dev/null)" "$(git --git-dir="$common" config --get core.bare 2>/dev/null)"
}
REPO_STATE_BEFORE="$(repo_state)"
BOSUN="$ROOT/bin/bosun"
WORK="$(mktemp -d)"
SOCKET="bosun-smoke-$$"
passed=0
failed=0
CONSOLE_PID="" # the console test's server, while it runs

cleanup() {
  [ -n "$CONSOLE_PID" ] && kill "$CONSOLE_PID" 2>/dev/null
  tmux -L "$SOCKET" kill-server 2>/dev/null
  rm -f "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/${SOCKET:?}"
  rm -rf "${WORK:?}"
}
trap cleanup EXIT

# check <description> <command...>: run the command; on failure, show the end of its output.
check() {
  local description="$1" output
  shift
  if output="$("$@" 2>&1)"; then
    passed=$((passed + 1))
    echo "  pass  $description"
  else
    failed=$((failed + 1))
    echo "  FAIL  $description"
    [ -n "$output" ] && tail -5 <<<"$output" | sed 's/^/          /'
  fi
}

contains() { grep -qF -- "$2" <<<"$1" || {
  echo "expected to find: $2"
  echo "in: $(tail -3 <<<"$1")"
  return 1
}; }

# fails_with <text> <command...>: the command fails and says <text>, so a crash or a usage error
# does not count as the expected refusal.
fails_with() {
  local text="$1" output
  shift
  if output="$("$@" 2>&1)"; then
    echo "succeeded: $output"
    return 1
  fi
  contains "$output" "$text"
}

# wait_for <seconds> <command...>: poll until the command succeeds, instead of a fixed sleep.
wait_for() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 0.2
  done
}

# console_started <stdout file> <stderr file>: the console printed its URL; if not, what it said.
console_started() {
  wait_for 10 grep -q '^bosun console: http://127.0.0.1:[0-9]*/?t=' "$1" && return 0
  echo "no URL; the console's stderr:"
  tail -5 "$2"
  return 1
}
has_windows() { [ "$(tmux -L "$SOCKET" list-windows -t =smoke -F '#{window_name}' 2>/dev/null | tr '\n' ' ')" = "$1" ]; }

echo "syntax"
for script in "$ROOT"/bin/bosun "$ROOT"/libexec/bosun-* "$ROOT"/install.sh "$ROOT"/uninstall.sh "$ROOT"/lib/*.sh; do
  head -1 "$script" | grep -q python && continue
  check "bash -n $(basename "$script")" bash -n "$script"
done
check "python compiles" python3 -c "import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]" "$ROOT/libexec/bosun-resolve" "$ROOT"/lib/*.py
check "the console's JavaScript passes its node check (skipped without node)" "$ROOT/tests/js_check.sh"

check "every command's --help prints its usage header and no code" bash -c '
  for cmd in "$1"/libexec/bosun-* "$1"/install.sh "$1"/uninstall.sh; do
    out="$("$cmd" --help 2>&1)" || { echo "$cmd: exit $?"; exit 1; }
    grep -qi "usage:" <<<"$(head -3 <<<"$out")" || { echo "$cmd: $(head -1 <<<"$out")"; exit 1; }
    ! grep -qE "^(set -uo|source |import )" <<<"$out" || { echo "$cmd prints code"; exit 1; }
  done' _ "$ROOT"
check "usage errors exit 2" bash -c '
  for args in "send" "send --bogus" "send window-only" "send --id" "send --no-item window-only" "send --new w" "send --start" "start --bogus" "launch --bogus" "refresh --bogus" "trust --bogus" "watchdog --bogus" "watchdog --only" "sessions --bogus" "learn --bogus" "learn apply" "events --bogus" "slack-watch --bogus" "review-watch --bogus" "console --bogus" "orphans --bogus"; do
    # shellcheck disable=SC2086  # split the argument list on purpose
    "$1" $args >/dev/null 2>&1
    [ $? -eq 2 ] || { echo "bosun $args did not exit 2"; exit 1; }
  done' _ "$BOSUN"

echo "python unit tests (tests/unit)"
unit_out="$(python3 -m unittest discover -s "$ROOT/tests/unit" 2>&1)"
check "$(grep -o 'Ran [0-9]* tests' <<<"$unit_out" || echo 'unit tests'), all passing" grep -qx OK <<<"$(tail -1 <<<"$unit_out")"
grep -qx OK <<<"$(tail -1 <<<"$unit_out")" || tail -25 <<<"$unit_out"

# Fixture HOME with two projects and named sessions.
export HOME="$WORK/home"
export BOSUN_CONFIG_DIR="$WORK/config"
export BOSUN_STATE_DIR="$WORK/state"
export BOSUN_LOG_DIR="$WORK/state/logs"
export BOSUN_SESSION_NAME="smoke"
export BOSUN_TMUX_SOCKET="$SOCKET"
export BOSUN_LAUNCH_DRY_RUN=1
export BOSUN_ORCHESTRATOR="" # orchestrator tests below enable it explicitly
REPO_A="$WORK/repos/svc-a"
REPO_B="$WORK/repos/svc-b"
mkdir -p "$REPO_A/.git" "$REPO_B/.git" "$HOME/.claude/sessions" "$BOSUN_CONFIG_DIR"
REPO_A="$(cd "$REPO_A" && pwd -P)"
REPO_B="$(cd "$REPO_B" && pwd -P)"
project_dir() { echo "$HOME/.claude/projects/${1//[^A-Za-z0-9-]/-}"; }
make_session() { # dir id title mtime
  local p
  p="$(project_dir "$1")"
  mkdir -p "$p"
  printf '{"type":"user"}\n{"type":"custom-title","customTitle":"%s","sessionId":"%s"}\n' "$3" "$2" >"$p/$2.jsonl"
  touch -t "$4" "$p/$2.jsonl"
}
make_session "$REPO_A" 11111111-0000-0000-0000-000000000001 alpha 202601010000
make_session "$REPO_A" 11111111-0000-0000-0000-000000000002 alpha 202601020000
make_session "$REPO_A" 11111111-0000-0000-0000-000000000003 alpha-extended 202601030000
make_session "$REPO_B" 22222222-0000-0000-0000-000000000001 beta 202601010000

cat >"$BOSUN_CONFIG_DIR/sessions.yml" <<EOF
sessions:
  - name: alpha
    dir: $REPO_A
  - name: beta
    dir: $REPO_B
  - name: gamma
    dir: $REPO_B
    model: sonnet
    effort: high
    autocompact: 500000
EOF

echo "resolve"
check "picks most recent exact match" test "$("$BOSUN" resolve "$REPO_A" alpha)" = 11111111-0000-0000-0000-000000000002
check "does not prefix-match" test "$("$BOSUN" resolve "$REPO_A" alpha-extended)" = 11111111-0000-0000-0000-000000000003
check "unknown name exits 1" bash -c "! '$BOSUN' resolve '$REPO_B' gamma"
printf '{"pid":%d,"sessionId":"22222222-0000-0000-0000-000000000001"}' $$ >"$HOME/.claude/sessions/$$.json"
check "--live finds a running holder" test "$("$BOSUN" resolve --live 22222222-0000-0000-0000-000000000001)" = "$$"
check "--live ignores idle sessions" bash -c "! '$BOSUN' resolve --live 11111111-0000-0000-0000-000000000002"
check "unknown names exit 3, distinct from failures" bash -c '"$1" resolve "$2" gamma 2>/dev/null; [ $? -eq 3 ]' _ "$BOSUN" "$REPO_B"
mkdir -p "$(project_dir "$REPO_A")/broken.jsonl"
check "an unreadable transcript does not break the lookup" test "$("$BOSUN" resolve "$REPO_A" alpha)" = 11111111-0000-0000-0000-000000000002
printf '{"pid":%d,"sessionId":"33333333-0000-0000-0000-000000000001","startedAt":1000}' $$ >"$HOME/.claude/sessions/stale.json"
check "--live skips a record whose pid was reused" bash -c '"$1" resolve --live 33333333-0000-0000-0000-000000000001; [ $? -eq 3 ]' _ "$BOSUN"

echo "launch (dry run)"
out="$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)"
check "resumes by UUID with name, remote control and drain prompt" contains "$out" "--name alpha --remote-control alpha --resume 11111111-0000-0000-0000-000000000002"
check "the prompt follows --, so a leading dash is not an option" contains "$out" "--resume 11111111-0000-0000-0000-000000000002 -- Bosun\\ resumed"
check "sessions start with the default model" contains "$out" "--model opus"
check "... and the default effort, as a session setting /effort can still change" contains "$out" 'effortLevel\":\"medium'
out="$(cd "$REPO_B" && "$BOSUN" launch gamma 2>&1)"
check "a sessions.yml entry overrides model and effort" bash -c 'grep -q -- "--model sonnet" <<<"$1" && grep -q "effortLevel\\\\\":\\\\\"high" <<<"$1"' _ "$out"
out="$(cd "$REPO_B" && "$BOSUN" launch --model haiku --effort low gamma 2>&1)"
check "launch --model/--effort override the entry" bash -c 'grep -q -- "--model haiku" <<<"$1" && grep -q "effortLevel\\\\\":\\\\\"low" <<<"$1"' _ "$out"
check "empty settings leave Claude Code's defaults" bash -c '! grep -qE -- "--model|effortLevel" <<<"$1"' _ "$(cd "$REPO_A" && BOSUN_MODEL='' BOSUN_EFFORT='' "$BOSUN" launch alpha 2>&1)"
check "an unknown effort is refused" fails_with "effort must be one of" "$BOSUN" launch --effort huge alpha
check "repo sessions launch with the default auto-compact window" contains "$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)" "--autocompact 300000 --name alpha"
check "a sessions.yml entry sets its own auto-compact window" contains "$(cd "$REPO_B" && "$BOSUN" launch gamma 2>&1)" "--autocompact 500000"
check "an empty auto-compact setting leaves Claude Code's default" bash -c '! grep -q -- "--autocompact" <<<"$1"' _ "$(cd "$REPO_A" && BOSUN_AUTOCOMPACT_TOKENS=0 "$BOSUN" launch alpha 2>&1)"
check "an auto-compact window out of range is refused" fails_with "must be auto or 100000-1000000" env BOSUN_AUTOCOMPACT_TOKENS=5000 "$BOSUN" launch alpha
out="$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)"
check "repo sessions get the compaction hook" bash -c 'grep -q "SessionStart" <<<"$1" && grep -qE "compact-context.{0,2} --window.{0,2} alpha" <<<"$1"' _ "$out"
check "BOSUN_COMPACT_CONTEXT=0 leaves the hook out" bash -c '! grep -q "compact-context" <<<"$1"' _ "$(cd "$REPO_A" && BOSUN_COMPACT_CONTEXT=0 "$BOSUN" launch alpha 2>&1)"
out="$(printf '{"hook_event_name":"SessionStart","source":"compact"}' | "$BOSUN" hook compact-context --window alpha)"
check "the compaction hook adds the window's items as SessionStart context" bash -c 'jq -e ".hookSpecificOutput.hookEventName == \"SessionStart\" and (.hookSpecificOutput.additionalContext | test(\"compacted\"))" <<<"$1" >/dev/null' _ "$out"
check "the compaction hook adds nothing when it is off" test -z "$(echo '{}' | BOSUN_COMPACT_CONTEXT=0 "$BOSUN" hook compact-context --window alpha)"
check "the compaction hook never fails the session start" bash -c 'echo "{}" | BOSUN_STATE_DIR=/nonexistent/x "$1" hook compact-context --window alpha' _ "$BOSUN"
check "repo sessions get the async signal hooks" bash -c 'grep -qE "hook.{0,2} signal.{0,2} --window.{0,2} alpha" <<<"$1" && grep -q "StopFailure" <<<"$1" && grep -q "PermissionRequest" <<<"$1" && grep -qE "async.{0,4}true" <<<"$1"' _ "$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)"
check "BOSUN_HOOK_SIGNALS=0 leaves the signal hooks out" bash -c '! grep -q "StopFailure" <<<"$1"' _ "$(cd "$REPO_A" && BOSUN_HOOK_SIGNALS=0 "$BOSUN" launch alpha 2>&1)"
printf '{"session_id":"s1","hook_event_name":"StopFailure","error":"rate_limit","last_assistant_message":"spend limit"}' | "$BOSUN" hook signal --window alpha
check "the signal hook records the event for the window" bash -c 'jq -e "select(.event == \"StopFailure\" and .kind == \"rate_limit\" and .session == \"s1\")" "$1" >/dev/null' _ "$BOSUN_STATE_DIR/signals/alpha.jsonl"
check "the signal hook never fails" bash -c 'echo "{broken" | BOSUN_STATE_DIR=/nonexistent/x "$1" hook signal --window alpha' _ "$BOSUN"
check "the signal hook records nothing when it is off" bash -c 'echo "{\"session_id\":\"s2\",\"hook_event_name\":\"Stop\"}" | BOSUN_HOOK_SIGNALS=0 "$1" hook signal --window alpha; ! grep -q s2 "$2"' _ "$BOSUN" "$BOSUN_STATE_DIR/signals/alpha.jsonl"
out="$(cd "$REPO_B" && "$BOSUN" launch beta 2>&1)"
check "refuses to resume a live session" contains "$out" "already running as pid $$"
out="$(cd "$REPO_B" && "$BOSUN" launch gamma 2>&1)"
check "starts a new named session when none exists" contains "$out" "starting a new one"

echo "config loading"
CFG="$WORK/cfg-test"
mkdir -p "$CFG"
cat >"$WORK/config-child.sh" <<'SH'
source "$1/lib/common.sh"; load_config; printf '%s' "${!2}"
SH
check "a child command re-reads bosun.env after its parent loaded it" env BOSUN_CONFIG_DIR="$CFG" bash -c '
  source "$1/lib/common.sh"; load_config
  echo "BOSUN_DRAIN_PROMPT=new" >"$BOSUN_CONFIG_DIR/bosun.env"
  [ "$(bash "$2" "$1" BOSUN_DRAIN_PROMPT)" = new ]' _ "$ROOT" "$WORK/config-child.sh"
check "environment overrides still beat the file" test "$(BOSUN_CONFIG_DIR="$CFG" BOSUN_DRAIN_PROMPT=from-env bash "$WORK/config-child.sh" "$ROOT" BOSUN_DRAIN_PROMPT)" = from-env
multi_line=$'line one\nline two'
check "multi-line overrides survive" test "$(BOSUN_CONFIG_DIR="$CFG" BOSUN_DRAIN_PROMPT="$multi_line" bash "$WORK/config-child.sh" "$ROOT" BOSUN_DRAIN_PROMPT)" = "$multi_line"
check "load_config exports only environment overrides" env BOSUN_CONFIG_DIR="$CFG" bash -c 'source "$1/lib/common.sh"; load_config; ! env | grep -q "^BOSUN_DRAIN_PROMPT="' _ "$ROOT"

echo "lib/common.sh functions"
fn() { bash -c 'source "$1/lib/common.sh"; shift; "$@"' _ "$ROOT" "$@"; }
check "shift_time adds minutes" test "$(fn shift_time 08:00 1)" = 08:01
check "shift_time wraps past midnight" test "$(fn shift_time 23:59 2)" = 00:01
check "shift_time goes back past midnight" test "$(fn shift_time 00:00 -1)" = 23:59
check "shift_time reads leading zeros as decimal" test "$(fn shift_time 08:09 1)" = 08:10
check "seconds_until a time already past today is 0" test "$(fn seconds_until 00:00)" = 0
check "the awake window's end at or before the start is the next day" bash -c '[ "$1" -gt 0 ] && [ "$1" -le 86400 ]' _ "$(BOSUN_START_TIME="$(date +%H:%M)" BOSUN_AWAKE_UNTIL="$(date -v-1M +%H:%M)" fn seconds_until_awake_end)"
soon_end="$(date -v+2M +%H:%M)"
if [[ "$soon_end" > "$(date +%H:%M)" ]]; then # not across midnight
  check "... or later today while yesterday's window still runs" bash -c '[ "$1" -gt 60 ] && [ "$1" -le 120 ]' _ "$(BOSUN_START_TIME=23:59 BOSUN_AWAKE_UNTIL="$soon_end" fn seconds_until_awake_end)"
fi
soon="$(date -v+2M +%H:%M)"
if [[ "$soon" > "$(date +%H:%M)" ]]; then # not across midnight
  check "seconds_until a time two minutes ahead" bash -c '[ "$1" -gt 60 ] && [ "$1" -le 120 ]' _ "$(fn seconds_until "$soon")"
fi
check "launchd: every day is one entry without a weekday" bash -c '[ "$(grep -c "<dict>" <<<"$1")" = 1 ] && ! grep -q Weekday <<<"$1"' _ "$(fn launchd_calendar_xml 08:00 MTWRFSU)"
check "launchd: weekdays are Monday 1 to Friday 5" test "$(fn launchd_calendar_xml 08:05 MTWRF | grep -oE 'Weekday</key><integer>[0-9]' | grep -oE '[0-9]$' | tr -d '\n')" = 12345
check "launchd: Sunday is 0 and Saturday 6" test "$(fn launchd_calendar_xml 08:00 SU | grep -oE 'Weekday</key><integer>[0-9]' | grep -oE '[0-9]$' | tr -d '\n')" = 60
for url in git@github.com:acme/api.git https://github.com/acme/api.git https://github.com/acme/api ssh://git@github.com/acme/api.git; do
  dir="$WORK/slug-$RANDOM"
  git init -q "$dir" && git -C "$dir" remote add origin "$url"
  check "repo_slug_of_dir reads $url" test "$(fn repo_slug_of_dir "$dir")" = acme/api
done
check "minutes since the latest client keystroke" test "$(bash -c 'source "$1/lib/common.sh"; tmux_cmd() { echo $(( $(date +%s) - 600 )); echo $(( $(date +%s) - 125 )); }; minutes_since_client_input' _ "$ROOT")" = 2
check "no attached client means no answer" bash -c 'source "$1/lib/common.sh"; tmux_cmd() { :; }; ! minutes_since_client_input' _ "$ROOT"
check "only idle, busy and shell sessions take input" bash -c 'source "$1/lib/common.sh"; claude_takes_input idle && claude_takes_input busy && claude_takes_input shell && ! claude_takes_input waiting && ! claude_takes_input ""' _ "$ROOT"
check "run_with_timeout stops a slow command" bash -c 'source "$1/lib/common.sh"; start=$SECONDS; ! run_with_timeout 1 sleep 10 && [ $((SECONDS - start)) -lt 5 ]' _ "$ROOT"
check "run_with_timeout passes a fast command's status" bash -c 'source "$1/lib/common.sh"; run_with_timeout 5 true && ! run_with_timeout 5 false' _ "$ROOT"

echo "example configs"
check "the generic example lists every setting with its default value" bash -c '
  dump() { for v in $(compgen -v BOSUN_ | grep -vx "BOSUN_ROOT\|BOSUN_PASSTHROUGH_VARS\|BOSUN_CONFIG_DIR"); do printf "%s=%s\n" "$v" "${!v}"; done; }
  defaults="$(env -i HOME="$HOME" bash -c "source \"\$1/lib/common.sh\"; set_defaults; $(declare -f dump); dump" _ "$1")"
  example="$(env -i HOME="$HOME" bash -c "BOSUN_CONFIG_DIR=\$HOME/.config/bosun; source \"\$1/examples/generic/bosun.env\"; $(declare -f dump); dump" _ "$1")"
  [ "$defaults" = "$example" ] || { diff <(echo "$defaults") <(echo "$example"); exit 1; }' _ "$ROOT"
check "the example configs only set known settings" bash -c '
  known="$(grep -oE "^  BOSUN_[A-Z_]+=" "$1/lib/common.sh" | tr -d " =")"
  for f in "$1"/examples/*/bosun.env; do
    for key in $(grep -oE "^BOSUN_[A-Z_]+=" "$f" | tr -d =); do grep -qx "$key" <<<"$known" || { echo "$f: $key"; exit 1; }; done
  done' _ "$ROOT"

echo "templates"
plist="$(bash -c "source '$ROOT/lib/common.sh'; load_config; render_launchd_plist com.example.test 08:00 MTWRF '' BOSUN_TRIGGER=launchd")"
check "launchd plist is valid" plutil -lint - <<<"$plist"
check "launchd plist has weekday schedule" contains "$plist" "<key>Weekday</key><integer>5</integer>"
plist="$(bash -c "source '$ROOT/lib/common.sh'; load_config; render_watchdog_plist")"
check "the watchdog plist is valid" plutil -lint - <<<"$plist"
check "... runs the watchdog every 5 minutes under its own label" bash -c 'grep -q "<string>com.bosun.morning.watchdog</string>" <<<"$1" && grep -q "<integer>300</integer>" <<<"$1" && grep -q "watchdog --once" <<<"$1"' _ "$plist"

plist="$(bash -c "source '$ROOT/lib/common.sh'; load_config; render_learn_plist")"
check "the learning agent's plist is valid" plutil -lint - <<<"$plist"
check "... runs learn --scheduled daily at BOSUN_LEARN_AT under its own label" bash -c 'grep -q "<string>com.bosun.morning.learn</string>" <<<"$1" && grep -q "learn --scheduled" <<<"$1" && grep -q "<key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer>" <<<"$1"' _ "$plist"

echo "window list"
specs() { bash -c 'source "$1/lib/common.sh"; load_config; window_specs' _ "$ROOT"; }
check "one window per session, without orchestrator or checkin when disabled" test "$(specs | cut -f1,3 | tr '\t\n' ': ')" = "alpha:session beta:session gamma:session "
out="$(BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A" specs | cut -f1,3 | tr '\t\n' ': ')"
check "orchestrator first, checkin last" test "$out" = "orch:orchestrator alpha:session beta:session gamma:session checkin:checkin "
check "no checkin window when check-ins are off" bash -c '! BOSUN_ORCHESTRATOR=orch BOSUN_CHECKIN_MINUTES=0 bash -c "source \"\$1/lib/common.sh\"; load_config; window_specs" _ "$1" | grep -q checkin' _ "$ROOT"
check "a checkin window for events alone when asked" bash -c 'BOSUN_ORCHESTRATOR=orch BOSUN_CHECKIN_MINUTES=0 BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_WITHOUT_CHECKINS=1 bash -c "source \"\$1/lib/common.sh\"; load_config; window_specs" _ "$1" | grep -q "^checkin"' _ "$ROOT"
check "events alone need events on" bash -c '! BOSUN_ORCHESTRATOR=orch BOSUN_CHECKIN_MINUTES=0 BOSUN_EVENTS_WITHOUT_CHECKINS=1 bash -c "source \"\$1/lib/common.sh\"; load_config; window_specs" _ "$1" | grep -q checkin' _ "$ROOT"

echo "ledger"
L() { "$BOSUN" ledger "$@"; }
check "upsert creates in the initial state" contains "$(L upsert 'review:o/r#1' --kind review-request --title 'Fix X' --url https://github.com/o/r/pull/1)" "created: review:o/r#1 [review-request] state=requested"
check "upsert is idempotent" contains "$(L upsert 'review:o/r#1' --kind review-request --title 'Fix X')" "unchanged"
check "unknown kind is rejected" fails_with "unknown kind" "$BOSUN" ledger upsert x:1 --kind nope
check "invalid transition is rejected" fails_with "is not allowed" "$BOSUN" ledger transition "review:o/r#1" rereview-requested --note x --evidence y
check "valid transition" contains "$(L transition 'review:o/r#1' reviewed --note 'reviewed on GitHub' --field reviewed_sha=abc)" "requested -> reviewed"
check "re-review needs evidence" fails_with "needs --evidence" "$BOSUN" ledger transition "review:o/r#1" rereview-requested --note again
check "re-review with evidence" contains "$(L transition 'review:o/r#1' rereview-requested --note again --evidence 'GitHub re-request after review')" "reviewed -> rereview-requested"
check "unseen source" bash -c '! "$1" ledger seen https://slack/p1' _ "$BOSUN"
L record-source https://slack/p1 --class pr-review --item 'review:o/r#1' >/dev/null
check "seen source is remembered" contains "$(L seen https://slack/p1)" '"classification": "pr-review"'
check "source without an item" contains "$(L record-source https://slack/p2 --class fyi)" "recorded"
check "propose records a pending action" contains "$(L propose 'review:o/r#1' 'Post review')" "proposal pending"
check "same proposal is not duplicated" contains "$(L propose 'review:o/r#1' 'Post review')" "unchanged"
check "pending proposals are listed" contains "$(L list --proposals)" "PENDING: Post review"
check "ledger.md shows proposals" contains "$(cat "$BOSUN_STATE_DIR/ledger.md")" "Post review"
L resolve 'review:o/r#1' 'done' --note posted >/dev/null
check "resolve clears the proposal" contains "$(L list --proposals)" "(no items)"
L upsert 'mypr:o/r#2' --kind my-pr >/dev/null
L transition 'mypr:o/r#2' approved --note ok >/dev/null
L transition 'mypr:o/r#2' merged --note merged >/dev/null
check "terminal states stay terminal" fails_with "is not allowed" "$BOSUN" ledger transition "mypr:o/r#2" awaiting-review --note x
check "list --open hides finished items" bash -c '! grep -q "mypr:o/r#2" <<<"$1"' _ "$(L list --open)"
check "first mark-run reports never" contains "$(L mark-run check-in)" "previous check-in run: never"
check "next mark-run reports the previous time" bash -c '! grep -q never <<<"$1"' _ "$(L mark-run check-in)"
check "a morning prints where its Slack scan starts, from the configured lookback" contains "$(BOSUN_SLACK_LOOKBACK_HOURS=2 L mark-run morning)" "(the last 2h (the last scan was more recent))"
for i in $(seq 1 20); do L upsert "slack:C1/$i" --kind mention >/dev/null & done
wait
check "concurrent writers do not lose items" test "$(L list --kind mention | grep -c '^slack:C1/')" = 20
for i in $(seq 1 20); do
  case $((i % 4)) in
    0) L note "slack:C1/1" "note $i" >/dev/null & ;;
    1) L report "slack:C1/$i" --summary "report $i" >/dev/null & ;;
    2) L record-delegation "slack:C1/$i" --window "w$i" --task investigate >/dev/null & ;;
    3) L transition "slack:C1/$i" surfaced --note "surfaced $i" >/dev/null & ;;
  esac
done
wait
check "concurrent mixed writers lose no change" bash -c '
  [ "$("$1" ledger show slack:C1/1 | grep -cE "\"note (4|8|12|16|20)\"")" = 5 ] &&
  [ "$("$1" ledger reports | grep -c "^slack:C1/")" = 5 ] &&
  [ "$("$1" ledger list | grep -c " @w")" = 5 ] &&
  [ "$("$1" ledger list | grep -c "\[mention: surfaced\]")" = 5 ]' _ "$BOSUN"
check "upsert updates fields of an existing item without --kind" contains "$(L upsert 'slack:C1/1' --field thread=t1)" "updated: slack:C1/1 [mention]"
check "upsert needs --kind to create" fails_with "give --kind to create it" "$BOSUN" ledger upsert 'slack:C1/404' --field x=y
check "report --state makes a validated transition" contains "$(L report 'slack:C1/3' --summary 'answered' --result notes/findings.md --state 'done')" "surfaced -> done"
check "a report keeps one-line fields" fails_with "one non-empty line" "$BOSUN" ledger report 'slack:C1/7' --summary "$(printf 'a\nb')"
check "reports --since includes items the report closed" contains "$(L reports --since 2000-01-01T00:00:00Z)" "slack:C1/3  [mention: done]"
check "delegation shows the recorded window" contains "$(L delegation 'slack:C1/6')" '"window": "w6"'
check "delegation refuses an item never delegated" fails_with "never handed to a repo session" "$BOSUN" ledger delegation 'slack:C1/7'
check "every write keeps the previous version" test -s "$BOSUN_STATE_DIR/ledger.json.bak"
cp "$BOSUN_STATE_DIR/ledger.json" "$WORK/ledger.good"
echo '{"items": [' >"$BOSUN_STATE_DIR/ledger.json"
check "a corrupt ledger is refused and points at the backup" fails_with "restore it from $BOSUN_STATE_DIR/ledger.json.bak" "$BOSUN" ledger list
cp "$WORK/ledger.good" "$BOSUN_STATE_DIR/ledger.json"
check "prune drops finished items" contains "$(L prune --days 0)" "pruned 2 item(s)"

echo "outreach (waiting on other people)"
L upsert 'slack:C5/1' --kind mention --title 'template question' >/dev/null
L record-delegation 'slack:C5/1' --window exporter --task new-work >/dev/null
out="$(L reach-out 'slack:C5/1' --to 'Dana (product)' --purpose decision --subject 'Keep the template unchanged?' --option a --option b)"
check "reach-out prints the new id first" test "$(head -1 <<<"$out")" = 'out:slack:C5/1/1'
check "ask is the same command, without a parent scoped by day" contains "$(L ask --channel email --to vendor --question 'Which format?')" "out:$(date +%F)/1"
check "unknown channels are refused" fails_with "invalid choice: 'fax'" "$BOSUN" ledger reach-out --channel fax --to x --subject y
check "proposing the message moves it to proposed" contains "$(L propose 'out:slack:C5/1/1' 'Slack DM to Dana: ...')" "draft -> proposed"
check "resolve done points to sent" fails_with "record it with bosun ledger sent" "$BOSUN" ledger resolve 'out:slack:C5/1/1' 'done'
check "sent refuses a message time that is not a time" fails_with "--at takes an ISO 8601 time" "$BOSUN" ledger sent 'out:slack:C5/1/1' --ref x --at yesterday
check "sent refuses a message time in the future" fails_with "is in the future" "$BOSUN" ledger sent 'out:slack:C5/1/1' --ref x --at '2999-01-01 09:00'
check "sent records the message ref, at the Slack message's own time" contains "$(L sent 'out:slack:C5/1/1' --ref 'https://acme.slack.com/archives/C0000000001/p1700000100000100')" "proposed -> sent; at 2023-11-14T22:15:00+00:00 (slack ref)"
check "sent --at corrects the time of a message already recorded" contains "$(L sent 'out:slack:C5/1/1' --ref 'https://acme.slack.com/archives/C0000000001/p1700000100000100' --at '2023-11-14T23:00:00+01:00')" "now at 2023-11-14T22:00:00+00:00 (was 2023-11-14T22:15:00+00:00)"
check "the parent lists what it waits on" contains "$(L list --open)" "| waiting on out:slack:C5/1/1 (slack to Dana (product), "
check "a parent waiting on outreach is not finished" fails_with "is waiting on: out:slack:C5/1/1" "$BOSUN" ledger transition 'slack:C5/1' 'done' --note x
check "seen names the outreach for a reply in its thread" bash -c '
  out="$("$1" ledger seen "https://acme.slack.com/archives/C0000000001/p1700000200000200?thread_ts=1700000100.000100&cid=C0000000001")"
  [ $? = 1 ] && grep -q "in the conversation of out:slack:C5/1/1" <<<"$out"' _ "$BOSUN"
check "a reply needs evidence" fails_with "needs --evidence" "$BOSUN" ledger transition 'out:slack:C5/1/1' replied --note x --field answer=b
check "replied says which window holds the parent" contains "$(L replied 'out:slack:C5/1/1' --evidence 'user: Dana said b' --summary 'b')" "window exporter holds slack:C5/1"
out="$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" delegate --update 'out:slack:C5/1/1' 2>&1)"
check "delegate --update <out-id> hands the reply to the parent's window" contains "$out" "would deliver to smoke:exporter: Update to ledger item slack:C5/1"
check "... and marks the outreach applied" contains "$out" "replied -> applied"
check "then the parent may finish" contains "$(L transition 'slack:C5/1' 'done' --note answered)" "new -> done"
check "outreach --open lists what is still open" bash -c 'out="$("$1" ledger outreach --open)"; grep -q "draft, email to vendor; no parent item" <<<"$out" && ! grep -q "out:slack:C5/1/1" <<<"$out"' _ "$BOSUN"
L upsert 'slack:C5/2' --kind mention --title 'footer question' >/dev/null
L reach-out 'slack:C5/2' --to 'Dana (product)' --purpose decision --subject 'Keep the footer?' >/dev/null
check "reach-out warns about open outreach to the same person" contains "$(L reach-out 'slack:C5/2' --to dana --subject 'And the header?')" "warning: slack:C5/2 already has open outreach to the same person: out:slack:C5/2/1 (decision, draft: Keep the footer?)"
check "sent records one message on several items" contains "$(L sent 'out:slack:C5/2/1' 'out:slack:C5/2/2' --ref 'https://acme.slack.com/archives/C0000000001/p1700000300000300')" "ok: out:slack:C5/2/2 draft -> sent"
check "seen names every open outreach sharing the thread" contains "$(L seen 'https://acme.slack.com/archives/C0000000001/p1700000400000400?thread_ts=1700000300.000300&cid=C0000000001' || true)" "in a conversation shared by 2 open outreach items"
L reach-out 'slack:C5/2' --to Priya --purpose fyi --subject 'One more commit landed' >/dev/null
check "a one-way (fyi) message is done once sent" contains "$(L sent 'out:slack:C5/2/3' --ref 'https://acme.example.com/fyi')" "draft -> sent -> applied (fyi: one-way, no reply expected)"
check "upsert keeps an outreach's subject and title one" contains "$(L upsert 'out:slack:C5/2/2' --field 'subject=And the header row?' && L list --open)" "out:slack:C5/2/2  [outreach: sent] - And the header row?"

echo "tasks, links and tickets"
TASK="task:$(date +%F)/1"
check "spawn prints the new task's id first" test "$(L spawn 'out:slack:C5/1/1' --title 'Fix the PDF renderer' --ask 'apply the fix there' --via 'user: go ahead' | head -1)" = "$TASK"
check "a task is not handed over before I accept it" fails_with "only an accepted task is handed over" "$BOSUN" delegate new-work --id "$TASK" --ask x --window exporter
check "outreach is never the work item of a delegation" fails_with "is outreach, not a work item" "$BOSUN" delegate new-work --id 'out:slack:C5/1/1' --ask x --print
check "only my word accepts a task" fails_with "needs my approval" "$BOSUN" ledger transition "$TASK" accepted --note x --evidence 'Dana said so'
L transition "$TASK" accepted --note 'I asked' --evidence 'user: go ahead' >/dev/null
check "delegate moves an accepted task to delegated" contains "$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" delegate new-work --id "$TASK" --ask 'fix the PDF renderer' --window exporter 2>&1)" "accepted -> delegated"
L report "$TASK" --summary 'draft PR open' --result 'https://github.com/acme/pdf-renderer/pull/7' >/dev/null
check "reports name the commands that link the new PR" contains "$(L reports)" "bosun ledger link '$TASK' produces 'mypr:acme/pdf-renderer#7'"
L upsert 'mypr:acme/pdf-renderer#7' --kind my-pr --title 'PDF renderer fix' >/dev/null
check "link records produces" contains "$(L link "$TASK" produces 'mypr:acme/pdf-renderer#7')" "ok: $TASK produces mypr:acme/pdf-renderer#7"
check "links refuse cycles" fails_with "would close a cycle" "$BOSUN" ledger link 'mypr:acme/pdf-renderer#7' produces "$TASK"
check "tracks creates the ticket" contains "$(L link 'mypr:acme/pdf-renderer#7' tracks 'jira:EXP-123')" "created jira:EXP-123 [ticket: unsynced]"
check "ticket-sync maps the status and names the mismatch" contains "$(L ticket-sync 'jira:EXP-123' --status 'To Do')" "propose moving it to in-review"
check "the site's status map applies" contains "$(BOSUN_TICKET_STATUS_MAP='jira/EXP:On Hold=cancelled' L ticket-sync 'jira:EXP-123' --status 'On Hold')" "todo -> cancelled ('On Hold')"
check "tickets are never transitioned by bosun" fails_with "mirrors its tracker" "$BOSUN" ledger transition 'jira:EXP-123' 'done' --note x
check "tree shows the chain from the outreach's parent" bash -c 'out="$("$1" ledger tree "$2")"; grep -q "^- slack:C5/1 \[mention: done\]" <<<"$out" && grep -q "^    \* $2 \[task: delegated" <<<"$out"' _ "$BOSUN" "$TASK"
check "migrate-links is idempotent" bash -c '"$1" ledger migrate-links >/dev/null && [ "$("$1" ledger migrate-links)" = "nothing to migrate" ]' _ "$BOSUN"
L report "$TASK" --summary 'marked ready at my request' >/dev/null
check "reports --since lists every report in the window, not only the latest" bash -c '
  out="$("$1" ledger reports --since 2000-01-01T00:00:00Z)"
  grep -q "summary: draft PR open" <<<"$out" && grep -q "summary: marked ready at my request" <<<"$out"' _ "$BOSUN"
check "spawn warns about a task that already comes from the same source" contains "$(L spawn 'out:slack:C5/1/1' --title 'Export credit notes' --ask 'add them')" "warning: a task already comes from the same source or thread: $TASK (delegated @exporter"
FOLDED="task:$(date +%F)/2"
check "fold closes the task and prints the update for the target's window" contains "$(L fold "$FOLDED" --into "$TASK" --note 'same PR')" "next: $TASK is with window exporter; carry the work there: bosun delegate --update '$TASK' --details"
check "fold is idempotent" contains "$(L fold "$FOLDED" --into "$TASK")" "unchanged: $FOLDED already folded into $TASK (dropped)"
check "list shows the fold from both ends" bash -c 'out="$("$1" ledger list)"; grep -q "| folded into $3" <<<"$out" && grep -q "| absorbs $2" <<<"$out"' _ "$BOSUN" "$FOLDED" "$TASK"
check "fold refuses a finished target" fails_with "add --force-closed-target" "$BOSUN" ledger fold 'out:slack:C5/2/2' --into 'slack:C5/1'

echo "workstreams"
check "ws add creates a workstream with an alias" contains "$(L ws add widget-gen --title 'Automated widget generation' --epic WID-100 --alias widgets)" "created: ws:widget-gen [workstream: active]"
check "a workstream holds two levels at most" fails_with "at most 2" bash -c '"$1" ledger ws add widget-ui --title UI --parent widgets >/dev/null && "$1" ledger ws add widget-ui-dark --title Dark --parent ws:widget-ui' _ "$BOSUN"
L upsert 'mypr:acme/widgets#12' --kind my-pr --title 'Widget template cache' >/dev/null
check "link part_of takes an alias" contains "$(L link 'mypr:acme/widgets#12' part_of widgets)" "ok: mypr:acme/widgets#12 part_of ws:widget-gen"
check "an item belongs to one workstream" fails_with "belongs to one workstream" "$BOSUN" ledger link 'mypr:acme/widgets#12' part_of ws:widget-ui
L reach-out 'mypr:acme/widgets#12' --to Dana --purpose decision --subject 'Cache TTL?' >/dev/null
check "show says the outreach inherits it" contains "$(L show 'out:mypr:acme/widgets#12/1')" '"how": "inherited from mypr:acme/widgets#12 (explicit)"'
L upsert 'mypr:acme/widgets#13' --kind my-pr --title 'Preview pane' >/dev/null
L link 'mypr:acme/widgets#13' tracks 'jira:WID-101' >/dev/null
L ticket-sync 'jira:WID-101' --status 'In Progress' --parent WID-100 >/dev/null
check "a tracked ticket's epic assigns its work" contains "$(L show 'mypr:acme/widgets#13')" '"how": "epic: tracks jira:WID-101 -> jira:WID-100 (the epic of ws:widget-gen)"'
printf '{"rules": [{"ws": "widgets", "repo": "acme/widget*", "title": "sizes"}]}\n' >"$BOSUN_CONFIG_DIR/workstreams.json"
L upsert 'mypr:acme/widgets#14' --kind my-pr --title 'Widget sizes' >/dev/null
check "assign --dry-run names what it would tag and why" contains "$(L assign --dry-run)" "would assign: mypr:acme/widgets#14 -> ws:widget-gen (rule: rule 1 (repo acme/widget*; title sizes))"
check "assign --apply records the links" contains "$(L assign --apply)" "2 link(s) recorded"
check "list --ws lists its items, inherited ones too" bash -c '[ "$("$1" ledger list --ws widgets | grep -c widgets)" = 4 ]' _ "$BOSUN"
check "summary drafts the stand-up" bash -c 'out="$("$1" ledger summary --ws widgets --since 2000-01-01)"; grep -q "^In review (3)" <<<"$out" && grep -q "Cache TTL?: to send" <<<"$out"' _ "$BOSUN"
check "summary --all ends with the unassigned" contains "$(L summary --all --markdown)" "**Unassigned**"
check "lint --json is JSON" bash -c '"$1" ledger lint --json | python3 -c "import json, sys; json.load(sys.stdin)"' _ "$BOSUN"
check "lint --strict fails on a finding" fails_with "warn: done-with-open-items: ws:widget-gen" bash -c '"$1" ledger ws set-state widgets done --note x >/dev/null && "$1" ledger lint --strict' _ "$BOSUN"
L ws set-state widgets active --note reopened >/dev/null
check "ws list counts by how items were assigned" contains "$(L ws list)" "ws:widget-gen  [active] Automated widget generation | 4 item(s) (explicit 3, inherited 1), 4 open"
rm "$BOSUN_CONFIG_DIR/workstreams.json"

echo "what I say in a repo window, and what a window works on"
said_payload='{"hook_event_name":"UserPromptSubmit","session_id":"s-1","prompt":"go with option b for the suites"}'
check "the prompt hook does nothing while events are off" bash -c '"$1" hook user-prompt --window exporter <<<"$2" && [ "$("$1" ledger said)" = "(nothing I said in a repo window matches)" ]' _ "$BOSUN" "$said_payload"
check "the prompt hook records what I type on the item the window holds" bash -c '
  BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"$2" || exit 1
  for _ in $(seq 1 50); do "$1" ledger said "$3" | grep -q "go with option b" && exit 0; sleep 0.1; done; exit 1' _ "$BOSUN" "$said_payload" "$TASK"
check "... in the background: the hook returns at once" bash -c '
  start=$(python3 -c "import time; print(time.time())")
  BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"$2"
  python3 -c "import sys, time; sys.exit(time.time() - float(sys.argv[1]) > 1.0)" "$start"' _ "$BOSUN" "$said_payload"
check "... and never fails the prompt, even on garbage" bash -c 'BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"{nope"' _ "$BOSUN"
check "... it logs the error instead" wait_for 10 bash -c 'grep -q "ERROR: unreadable hook input" "$1"/hook-*.log' _ "$BOSUN_LOG_DIR"
check "a prompt bosun passes at launch is not taken for mine" bash -c '
  prompt="Bosun started this fresh conversation for this window; reply with a one-line status"
  (cd "$3" && BOSUN_EVENTS_ENABLED=1 "$1" launch exporter "$prompt" >/dev/null 2>&1) || exit 1
  payload="$(jq -nc --arg p "$prompt" "{hook_event_name: \"UserPromptSubmit\", session_id: \"s-2\", prompt: \$p}")"
  BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"$payload" || exit 1
  BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"${payload/Bosun started this fresh conversation/control typed by me}" || exit 1
  for _ in $(seq 1 50); do "$1" ledger said | grep -q "control typed by me" && break; sleep 0.1; done
  sleep 0.5; "$1" ledger said | grep -q "control typed by me" && ! "$1" ledger said | grep -q "fresh conversation"' _ "$BOSUN" "" "$REPO_A"
rm -f "$BOSUN_LOG_DIR"/hook-*.log # the ERROR line above is expected, not one for the watchdog
check "reports --since shows what I said with the reports" contains "$(L reports --since 2000-01-01T00:00:00Z)" "$TASK  [task: delegated] user said"
check "working marks the window's active item" contains "$(L working "$TASK")" "ok: exporter is working on $TASK"
check "... shown in list and delegation" bash -c '"$1" ledger list | grep -q "@exporter (reported, working)" && "$1" ledger delegation "$2" | grep -q "\"working\": true"' _ "$BOSUN" "$TASK"
check "idle takes it out" bash -c '"$1" ledger idle "$2" >/dev/null && "$1" ledger delegation "$2" | grep -q "\"working\": false"' _ "$BOSUN" "$TASK"
out="$(cd "$REPO_A" && BOSUN_EVENTS_ENABLED=1 "$BOSUN" launch alpha 2>&1)"
check "with events on, repo sessions launch with the prompt hook" contains "$out" 'hook\ user-prompt\ --window\ alpha'
check "... and without, they do not" bash -c '! grep -q "user-prompt" <<<"$1"' _ "$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)"

echo "repo mask"
mask_check() { # <mask> <repo>
  bash -c 'source "$1/lib/common.sh"; BOSUN_REPO_MASK="$2"; repo_in_mask "$3"' _ "$ROOT" "$1" "$2"
}
check "owner glob matches its repos" mask_check "acme/*" acme/api
check "bare owner means all its repos" mask_check "acme" acme/api
check "matching is case-insensitive" mask_check "Acme/*" ACME/Api
check "repo globs narrow the match" bash -c '! "$@"' _ mask_check "acme/web-*" acme/api
check "other owners are excluded" bash -c '! "$@"' _ mask_check "acme/* other/one" personal/x
check "empty mask matches everything" mask_check "" personal/x
check "ledger rejects items outside the mask" fails_with "outside BOSUN_REPO_MASK" env BOSUN_REPO_MASK="acme/*" "$BOSUN" ledger upsert "mypr:personal/x#1" --kind my-pr
check "ledger accepts items inside the mask" contains "$(BOSUN_REPO_MASK="acme/*" L upsert 'mypr:acme/api#1' --kind my-pr)" "created"
check "mask does not apply to non-repo items" contains "$(BOSUN_REPO_MASK="acme/*" L upsert 'slack:C9/1' --kind mention)" "created"

echo "my-prs (fake gh)"
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\nexit 0\n' >"$HOME/.local/bin/osascript" # no real notifications from tests
chmod +x "$HOME/.local/bin/osascript"
cat >"$HOME/.local/bin/gh" <<FAKE
#!/bin/bash
printf '%s\n' "\$@" >"$WORK/fake-gh.args"
echo '{"url":"https://github.com/acme/api/pull/1","repo":"acme/api","number":1,"title":"a"}'
echo '{"url":"https://github.com/personal/x/pull/2","repo":"personal/x","number":2,"title":"b"}'
FAKE
chmod +x "$HOME/.local/bin/gh"
out="$(BOSUN_REPO_MASK="acme/*" "$BOSUN" my-prs)"
check "my-prs applies the repo mask" test "$(jq -r .repo <<<"$out")" = "acme/api"
check "my-prs succeeds when the last PR is masked out" env BOSUN_REPO_MASK="acme/*" "$BOSUN" my-prs
check "my-prs skips drafts by default" grep -qx -- "--draft=false" "$WORK/fake-gh.args"
check "default window filters by activity" grep -q -- "^--updated=>=20" "$WORK/fake-gh.args"
BOSUN_REPO_MASK="" "$BOSUN" my-prs --since 7d --by created --include-drafts >/dev/null
check "--by created filters by creation time" grep -q -- "^--created=>=$(date -u -v-7d +%Y-%m-%d)" "$WORK/fake-gh.args"
check "--include-drafts drops the draft filter" bash -c '! grep -q -- "--draft" "$1"' _ "$WORK/fake-gh.args"
"$BOSUN" my-prs --since 2026-05-01 >/dev/null
check "--since accepts a date" grep -qx -- "--updated=>=2026-05-01" "$WORK/fake-gh.args"
check "--since rejects nonsense" fails_with "bad window" "$BOSUN" my-prs --since soon
rm "$HOME/.local/bin/gh"

echo "context (fake gh)"
printf '#!/bin/sh\necho octo-me\n' >"$HOME/.local/bin/gh"
chmod +x "$HOME/.local/bin/gh"
for slug in acme/api personal/x; do
  git init -q "$WORK/context-repos/${slug#*/}" && git -C "$WORK/context-repos/${slug#*/}" remote add origin "git@github.com:$slug.git"
done
out="$(BOSUN_REPOS_DIR="$WORK/context-repos" BOSUN_REPO_MASK="acme/*" "$BOSUN" context 2>&1)"
check "context shows the chores config and my GitHub login" bash -c 'grep -qx -- "- max_nudges: 2 (outreach other than review)" <<<"$1" && grep -q -- "^- max_review_nudges: 0 " <<<"$1" && grep -qx -- "- github_login: octo-me" <<<"$1"' _ "$out"
check "context maps only the repos within the mask" bash -c 'grep -q "^- acme/api -> .*/context-repos/api$" <<<"$1" && ! grep -q "personal/x" <<<"$1"' _ "$out"
check "context says when the session is not running" contains "$out" "(session not running)"
check "context lists the open ledger items" contains "$out" "slack:C9/1"
rm "$HOME/.local/bin/gh"

echo "orchestrator"
out="$(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch "$BOSUN" launch orch 2>&1)"
check "orchestrator starts a dated conversation" contains "$out" "--name orch-$(date +%F) --remote-control orch"
check "orchestrator runs in auto mode, with its model and effort before the long policy" contains "$out" "--permission-mode auto --model opus --settings"
check "its settings keep the permission rules and guard, plus the effort" bash -c 'grep -q "PreToolUse" <<<"$1" && grep -q "effortLevel" <<<"$1"' _ "$out"
check "orchestrator loads the plugin" contains "$out" "--plugin-dir $ROOT/orchestrator/plugin"
check "orchestrator launches with its auto-compact window and the compaction hook" bash -c 'grep -q -- "--autocompact 300000" <<<"$1" && grep -qE "compact-context.{0,2} --orchestrator" <<<"$1"' _ "$out"
check "orchestrator gets the async signal hooks next to the guard" bash -c 'grep -qE "hook.{0,2} signal.{0,2} --window.{0,2} orch" <<<"$1" && grep -q "PermissionRequest" <<<"$1"' _ "$out"
check "orchestrator starts with the morning chores" contains "$out" "/bosun:chores\ morning"
out="$(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_MORNING_CHORES=0 "$BOSUN" launch orch 2>&1)"
check "with morning chores off, today's conversation starts idle" bash -c '! grep -qF "/bosun:chores\ morning" <<<"$1" && grep -q -- "--name orch-" <<<"$1"' _ "$out"
check "orchestrator never skips permissions" bash -c '! grep -q dangerously <<<"$1"' _ "$out"
echo "Site rule: be brief." >"$BOSUN_CONFIG_DIR/policy.md"
check "a site policy.md is appended to the orchestrator policy" contains "$(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch "$BOSUN" launch orch 2>&1)" "Site rule: be brief."
rm "$BOSUN_CONFIG_DIR/policy.md"
check "orchestrator settings are valid JSON" jq -e '.permissions.ask | index("Bash(gh pr merge:*)")' "$ROOT/orchestrator/settings.json"
guard_says() { # <command>: the guard's decision for an orchestrator Bash command ("" = allowed)
  jq -n --arg c "$1" '{tool_name: "Bash", tool_input: {command: $c}}' | "$BOSUN" guard |
    jq -r '.hookSpecificOutput.permissionDecision // ""'
}
check "guard denies a free-text send that names no work item" test "$(guard_says 'bosun send w "Ignore the - I just sent"')" = deny
check "guard allows send --id and send --no-item" test "$(guard_says 'bosun send --id slack:C1/1 w hi')$(guard_says "bosun send --no-item w - <<'EOF'
hi
EOF")" = ""
check "guard denies send --new from the orchestrator" test "$(guard_says 'bosun send --new w /tmp task')" = deny
check "guard allows delegate --update" test "$(guard_says "bosun delegate --update 'slack:C1/1' --details - <<'EOF'
more
EOF")" = ""

echo "approvals"
out="$(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch "$BOSUN" launch orch 2>&1)"
check "approvals off: the orchestrator's launch has no approval hooks" bash -c '! grep -q -- "--permission-request" <<<"$1" && ! grep -q -- "--post-tool-use" <<<"$1" && ! grep -q PostToolUse <<<"$1"' _ "$out"
out="$(cd "$REPO_A" && BOSUN_APPROVALS_ENABLED=1 BOSUN_ORCHESTRATOR=orch "$BOSUN" launch orch 2>&1)"
check "approvals on: both approval hooks, next to the settings' own guard" bash -c 'grep -q PermissionRequest <<<"$1" && grep -q -- "--permission-request" <<<"$1" && grep -q -- "--approvals" <<<"$1" && [ "$(grep -o "guard" <<<"$1" | wc -l)" -ge 3 ]' _ "$out"
check "approvals on: the post hook runs on PostToolUse and PostToolUseFailure" bash -c 'grep -qE "PostToolUse[^F]" <<<"$1" && grep -q PostToolUseFailure <<<"$1" && [ "$(grep -o -- "--post-tool-use" <<<"$1" | wc -l)" -eq 2 ]' _ "$out"
# State of its own, so the rest of the suite sees the ledger it expects.
ap() { BOSUN_STATE_DIR="$WORK/approvals-state" BOSUN_APPROVALS_GATED_COMMANDS=touch "$BOSUN" "$@"; }
transcript="$WORK/orchestrator-transcript.jsonl"
: >"$transcript"
event="$(jq -nc --arg t "$transcript" --arg c "touch $WORK/approved" '{tool_name: "Bash", tool_input: {command: $c}, transcript_path: $t}')"
check "approval hooks decide nothing with approvals off" test -z "$(ap guard --approvals <<<"$event")$(ap guard --permission-request <<<"$event")"
export BOSUN_APPROVALS_ENABLED=1
check "an unproposed outward call is denied, saying how to propose it" contains "$(ap guard --approvals <<<"$event")" "bosun ledger approve '<id>' --quote"
check "the plain guard is unchanged with approvals on" test -z "$(ap guard <<<"$event")"
ap ledger upsert 'mypr:acme/api#7' --kind my-pr >/dev/null
check "approve needs a proposal with an action spec" fails_with "no pending proposal with an action spec" ap ledger approve 'mypr:acme/api#7' --quote yes
ap ledger propose 'mypr:acme/api#7' --run "touch $WORK/approved" >/dev/null
printf '{"type":"user","timestamp":"%s","origin":{"kind":"human"},"message":{"role":"user","content":"1 and 3 yes"}}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$transcript"
ap ledger approve 'mypr:acme/api#7' --quote '1 and 3 yes' >/dev/null
check "an approved call whose quote I typed passes PreToolUse" test -z "$(ap guard --approvals <<<"$event")"
check "... and PermissionRequest allows it" test "$(ap guard --permission-request <<<"$event" | jq -r .hookSpecificOutput.decision.behavior)" = allow
check "... only once" test -z "$(ap guard --permission-request <<<"$event")"
check "ledger approvals lists it" contains "$(ap ledger approvals)" "approval-used (1 and 3 yes) Bash"
check "... and it is carried out, closing, until its result" contains "$(ap ledger list --proposals)" "PENDING (carried out, closing)"
post="$(jq -c '. + {hook_event_name: "PostToolUse", tool_use_id: "toolu_1", tool_response: {stdout: "", interrupted: false}}' <<<"$event")"
check "the post hook prints nothing" test -z "$(ap guard --post-tool-use <<<"$post")"
check "... and its success closed the proposal" test "$(ap ledger show 'mypr:acme/api#7' | jq -r '.proposal')" = null
check "... as carried out by the approved call" contains "$(ap ledger show 'mypr:acme/api#7' | jq -r '.history[-1].note')" "carried out by the approved call (Bash)"
failing="$(jq -nc --arg t "$transcript" --arg c "touch $WORK/no-such-dir/x" '{tool_name: "Bash", tool_input: {command: $c}, transcript_path: $t}')"
ap ledger propose 'mypr:acme/api#7' --run "touch $WORK/no-such-dir/x" >/dev/null
# The quote must come after the new proposal: a second later than now (times have whole seconds).
printf '{"type":"user","timestamp":"%s","origin":{"kind":"human"},"message":{"role":"user","content":"1 and 3 yes"}}\n' \
  "$(python3 -c 'import datetime as d; print((d.datetime.now(d.UTC) + d.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))')" >>"$transcript"
ap ledger approve 'mypr:acme/api#7' --quote '1 and 3 yes' >/dev/null
check "a second approved call is allowed" test "$(ap guard --permission-request <<<"$failing" | jq -r .hookSpecificOutput.decision.behavior)" = allow
ap guard --post-tool-use <<<"$(jq -c '. + {hook_event_name: "PostToolUseFailure", error: "Exit code 1"}' <<<"$failing")" >/dev/null
check "its failure leaves the proposal open with approval-failed" test "$(ap ledger show 'mypr:acme/api#7' | jq -r '.history[-1].event')" = approval-failed
check "... listed as needing a new approval" contains "$(ap ledger list --proposals)" "the approved call failed; needs a new approval"
check "resolve-carried-out finds nothing left to settle" contains "$(ap ledger resolve-carried-out --dry-run)" "no pending proposal has a used approval"
unset BOSUN_APPROVALS_ENABLED
check "with approvals off the post hook does nothing" test -z "$(ap guard --post-tool-use <<<"$post")"

echo "knowledge (kg, fixture acme knowledge base)"
# A config dir of its own, so doctor and the rest of the suite see no knowledge base.
kg() { BOSUN_CONFIG_DIR="$WORK/kg-config" "$BOSUN" kg "$@"; }
KG_DIR="$WORK/kg-config/knowledge"
KG_FIXTURE="$ROOT/tests/fixtures/knowledge/acme-import.jsonl"
out="$(kg import "$KG_FIXTURE" 2>&1)"
check "import is a dry run first: counts and refusals, nothing written" bash -c 'grep -q "confirmed automatically: 0; waiting for review: 20" <<<"$1" && grep -q "refused: 2 people whitelist" <<<"$1" && [ ! -e "$2" ]' _ "$out" "$KG_DIR"
check "import --apply --auto-confirm writes the knowledge base" contains "$(kg import "$KG_FIXTURE" --apply --auto-confirm 2>&1)" "confirmed automatically: 6; waiting for review: 14"
check "stats counts it" contains "$(kg stats)" "15 entities, 20 facts (14 proposed, 6 confirmed"
if command -v git >/dev/null; then
  check "every change is a commit in the knowledge directory's own history" test "$(env -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE git -C "$KG_DIR" log --format=%s | head -1)" = "kg import acme-import.jsonl"
fi
check "a person is refused anything outside the whitelist" fails_with "people whitelist" kg propose person/lee mood --value calm --source ledger:x
check "lookup resolves a strong alias and shows sources" contains "$(kg lookup releases)" "f1 Release process has_step Merge to main (step 1) [ledger:mypr:acme/invoice-exporter#102"
out="$(kg batch --cap 2)"
check "the batch lists capped groups, rework-preventing first" bash -c 'grep -q "^1\. Process: other facts: 5 facts \[prevents rework\]" <<<"$1" && grep -q "(1 more group waits for a later batch)" <<<"$1"' _ "$out"
check "a one-line decision answers it" contains "$(kg batch apply 'confirm all except 1a')" "8 confirmed, 0 rejected, 1 left proposed"
check "the context pack cites confirmed facts only" bash -c 'grep -q "\[f1; ledger:" <<<"$1" && ! grep -q "canary checks pass" <<<"$1"' _ "$(kg context --for release)"
transcript="$WORK/kg-transcript.jsonl"
printf '{"type":"user","timestamp":"%s","origin":{"kind":"human"},"message":{"role":"user","content":"note: the pdf renderer repo is archived"}}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$transcript"
check "my own words confirm a fact when the quote is in the transcript" contains "$(kg propose repo/pdf-renderer status --value archived --from-user --quote 'the pdf renderer repo is archived' --transcript "$transcript")" "confirmed (your own words"
check "... and not when it is not" contains "$(kg propose repo/pdf-renderer caveat --value slow --from-user --quote 'it is slow' --transcript "$transcript")" "stays proposed"
check "lint runs over it" kg lint
check "forget --dry-run says what it would delete" contains "$(kg forget person/lee --dry-run)" "would delete the person, 3 facts"
check "... and deletes nothing" grep -q person/lee "$KG_DIR/entities.jsonl"
check "kg usage errors exit 2" bash -c '
  for args in "" "bogus" "propose x"; do
    # shellcheck disable=SC2086  # split the argument list on purpose
    "$1" kg $args >/dev/null 2>&1
    [ $? -eq 2 ] || { echo "kg $args did not exit 2"; exit 1; }
  done' _ "$BOSUN"

# From here on `claude` is this fake (refresh asks it for the installed version), never a real
# Claude Code on this machine.
printf '#!/bin/sh\necho "2.0.0 (Claude Code)"\n' >"$WORK/fake-claude"
chmod +x "$WORK/fake-claude"
export BOSUN_CLAUDE_BIN="$WORK/fake-claude"

echo "tmux session (isolated socket, dry-run windows)"
"$BOSUN" start >/dev/null 2>&1
wait_for 10 has_windows "alpha beta gamma "
windows="$(tmux -L "$SOCKET" list-windows -t "=smoke" -F '#{window_name}' 2>/dev/null | tr '\n' ' ')"
check "start creates one window per session" test "$windows" = "alpha beta gamma "
tmux -L "$SOCKET" new-window -d -t "=smoke" -n at-a-screen "bash -c '(exec -a fake-claude sleep 60); true'"
check "the watchdog sees a Claude that has no session yet" wait_for 5 bash -c '"$1" watchdog --facts | jq -e ".windows[] | select(.name == \"at-a-screen\") | .starting_pid > 0 and .claude_pid == null" >/dev/null' _ "$BOSUN"
tmux -L "$SOCKET" kill-window -t "=smoke:at-a-screen"
check "windows are found without a UTF-8 locale too" bash -c 'LC_ALL=C "$1" sessions --json | jq -e "[.[] | select(.name == \"alpha\")] | .[0].window == true" >/dev/null' _ "$BOSUN"
# -S -200: the launch line (its --settings JSON) wraps over more rows than the window shows.
check "window runs the launcher" wait_for 10 bash -c 'tmux -L "$1" capture-pane -p -S -200 -t "=smoke:0" | grep -q "dry-run: would run"' _ "$SOCKET"
tmux -L "$SOCKET" kill-window -t "=smoke:2"
out="$("$BOSUN" start 2>&1)"
check "second start refreshes instead of duplicating" contains "$out" "already exists; mode: refresh"
check "refresh recreates missing windows" contains "$out" "gamma: create the missing window"
check "send refuses windows without Claude" fails_with "is not running Claude" "$BOSUN" send alpha hello
check "send refuses unknown windows" fails_with "no window 'no-such-window'" "$BOSUN" send no-such-window hello
check "stop rejects unknown arguments and keeps the session" bash -c '! "$1" stop --dry-run 2>/dev/null && tmux -L "$2" has-session -t =smoke' _ "$BOSUN" "$SOCKET"
check "setup rejects unknown arguments" bash -c '! "$1" setup --dryrun 2>/dev/null' _ "$BOSUN"
"$BOSUN" stop >/dev/null 2>&1
check "stop kills the session" bash -c "! tmux -L '$SOCKET' has-session -t '=smoke'"

echo "refresh (fake Claude processes and status records)"
"$BOSUN" start >/dev/null 2>&1
wait_for 10 has_windows "alpha beta gamma "
fake_claude() { # <window> <status> <version> <seconds-in-status> [name]; runs `sleep` in the pane as "Claude"
  local window_id pane_pid pid
  read -r pane_pid window_id <<<"$(tmux -L "$SOCKET" list-windows -t "=smoke" -F '#{window_name}	#{pane_pid} #{window_id}' | awk -F'\t' -v w="$1" '$1 == w { print $2 }')"
  tmux -L "$SOCKET" send-keys -t "$window_id" "sleep 600" Enter
  for _ in $(seq 1 25); do
    pid="$(pgrep -P "$pane_pid" -x sleep)" && break
    sleep 0.2
  done
  [ -n "$pid" ] || {
    echo "fake_claude: no sleep started in $1" >&2
    return 1
  }
  printf '{"pid":%d,"status":"%s","version":"%s","statusUpdatedAt":%d,"name":"%s"}' \
    "$pid" "$2" "$3" $((($(date +%s) - $4) * 1000)) "${5:-$1}" >"$HOME/.claude/sessions/$pid.json"
  echo "$pid"
}
alpha_pid="$(fake_claude alpha idle 1.0.0 3600)"
fake_claude beta busy 1.0.0 3600 >/dev/null
gamma_pid="$(fake_claude gamma idle 2.0.0 3600)"
out="$("$BOSUN" refresh --dry-run 2>&1)"
check "restarts idle outdated sessions" contains "$out" "alpha: would restart (Claude v1.0.0 -> v2.0.0"
check "leaves busy sessions alone" contains "$out" "beta: busy (v1.0.0); leaving alone"
check "leaves an idle up-to-date session with nothing in flight idle, without a prompt" contains "$out" "gamma: left idle: nothing in flight (idle, v2.0.0)"
L upsert 'slack:C77/1' --kind mention >/dev/null
L record-delegation 'slack:C77/1' --window gamma --task investigate >/dev/null
check "a window holding a hand-over without a report is in flight" test "$(L in-flight gamma)" = "slack:C77/1"
check "... so it gets the drain prompt" contains "$("$BOSUN" refresh --dry-run gamma 2>&1)" "gamma: would send the drain prompt (idle, v2.0.0)"
L report 'slack:C77/1' --summary 'looked' >/dev/null
check "... and once it reported, nothing is in flight" test -z "$(L in-flight gamma)"
out="$("$BOSUN" refresh --dry-run --drain-only 2>&1)"
check "--drain-only never restarts" bash -c 'grep -q "alpha: left idle: nothing in flight" <<<"$1" && ! grep -q "alpha: would restart" <<<"$1"' _ "$out"
# A conversation past BOSUN_FRESH_CONTEXT_TOKENS (its latest turn read 300k tokens).
big_transcript="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import claude_state; print(claude_state.transcript_path(sys.argv[2], "s-gamma"))' "$ROOT/lib" "$WORK")"
mkdir -p "$(dirname "$big_transcript")"
echo '{"type":"assistant","message":{"usage":{"input_tokens":1000,"cache_read_input_tokens":290000,"cache_creation_input_tokens":9000,"output_tokens":50}}}' >"$big_transcript"
gamma_record() { # <status> <seconds in it>
  printf '{"pid":%d,"status":"%s","version":"2.0.0","statusUpdatedAt":%d,"name":"gamma","cwd":"%s","sessionId":"s-gamma"}' \
    "$gamma_pid" "$1" $((($(date +%s) - $2) * 1000)) "$WORK" >"$HOME/.claude/sessions/$gamma_pid.json"
}
gamma_record idle 3600
check "claude_state reads a conversation's size from its latest turn" test "$(python3 "$ROOT/lib/claude_state.py" context "$gamma_pid")" = 300000
check "a dry run names a big idle window with nothing in flight, also with the flag off" contains "$("$BOSUN" refresh --dry-run gamma 2>&1)" \
  "gamma: would start a fresh conversation with BOSUN_FRESH_CONVERSATIONS=1 (context 300k > 200k tokens, nothing in flight)"
check "... with the flag on it would start one" contains "$(BOSUN_FRESH_CONVERSATIONS=1 "$BOSUN" refresh --dry-run gamma 2>&1)" \
  "gamma: would start a fresh conversation (context 300k > 200k tokens"
L upsert 'slack:C77/2' --kind mention >/dev/null
L record-delegation 'slack:C77/2' --window gamma --task investigate >/dev/null
check "... but not while it holds a hand-over without a report" bash -c '! grep -q "fresh conversation" <<<"$1"' _ "$(BOSUN_FRESH_CONVERSATIONS=1 "$BOSUN" refresh --dry-run gamma 2>&1)"
L report 'slack:C77/2' --summary 'looked' >/dev/null
gamma_record busy 3600
check "... nor while it is busy" bash -c '! grep -q "fresh conversation" <<<"$1"' _ "$(BOSUN_FRESH_CONVERSATIONS=1 "$BOSUN" refresh --dry-run gamma 2>&1)"
gamma_record idle 3600
check "... nor under a higher threshold" bash -c '! grep -q "fresh conversation" <<<"$1"' _ "$(BOSUN_FRESH_CONVERSATIONS=1 BOSUN_FRESH_CONTEXT_TOKENS=400000 "$BOSUN" refresh --dry-run gamma 2>&1)"
mkdir -p "$BOSUN_STATE_DIR/conversations"
echo "gamma-$(date +%F)-1" >"$BOSUN_STATE_DIR/conversations/gamma"
check "a fresh conversation's name is what the window resumes, and the next one counts up" bash -c '
  source "$1/lib/common.sh"; load_config
  [ "$(expected_claude_name gamma)" = "gamma-$(date +%F)-1" ] && [ "$(next_conversation_name gamma)" = "gamma-$(date +%F)-2" ]' _ "$ROOT"
rm -f "$BOSUN_STATE_DIR/conversations/gamma"
out="$("$BOSUN" refresh --dry-run --restart-idle gamma 2>&1)"
check "--restart-idle restarts up-to-date idle sessions" contains "$out" "gamma: would restart (--restart-idle"
check "window arguments limit the refresh" bash -c '! grep -q alpha <<<"$1"' _ "$out"
printf '{"pid":%d,"status":"shell","version":"1.0.0","statusUpdatedAt":%d}' "$gamma_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$gamma_pid.json"
out="$("$BOSUN" refresh --dry-run gamma 2>&1)"
check "never restarts while a background shell runs" contains "$out" "restart deferred (status shell"
printf '{"pid":%d,"status":"idle","version":"1.0.0","statusUpdatedAt":%d}' "$gamma_pid" $((($(date +%s) - 5) * 1000)) >"$HOME/.claude/sessions/$gamma_pid.json"
out="$("$BOSUN" refresh --dry-run gamma 2>&1)"
check "defers restart when only briefly idle" contains "$out" "restart deferred (status idle"
alpha_pane_pid="$(tmux -L "$SOCKET" display -p -t "=smoke:alpha" '#{pane_pid}')"
"$BOSUN" refresh alpha >/dev/null 2>&1
check "restart stops the old process" wait_for 10 bash -c "! kill -0 $alpha_pid 2>/dev/null"
check "restart relaunches in the same window, in a new process" wait_for 10 bash -c '
  [ "$(tmux -L "$1" display -p -t "=smoke:alpha" "#{pane_pid}")" != "$2" ] &&
  tmux -L "$1" capture-pane -p -S -200 -t "=smoke:alpha" | grep -q "resuming '"'"'alpha'"'"'"' _ "$SOCKET" "$alpha_pane_pid"
check "... without the drain prompt, since nothing is in flight there" wait_for 10 bash -c '
  tmux -L "$1" capture-pane -p -S -200 -t "=smoke:alpha" | grep -q "idle: no prompt"' _ "$SOCKET"
check "--unless-active is a no-op without attached clients" "$BOSUN" refresh --dry-run --unless-active
beta_window="$(tmux -L "$SOCKET" list-windows -t =smoke -F '#{window_name} #{window_id}' | awk '$1 == "beta" { print $2 }')"
beta_pane="$(tmux -L "$SOCKET" display -p -t "$beta_window" '#{pane_pid}')"
pkill -P "$beta_pane" sleep
wait_for 5 bash -c '! pgrep -P "$1" sleep' _ "$beta_pane"
cat >"$WORK/beta-claude.sh" <<'SH'
exec -a 'claude --model sonnet --settings {"effortLevel":"low"}' sleep 600
SH
tmux -L "$SOCKET" send-keys -t "$beta_window" "bash $WORK/beta-claude.sh" Enter
wait_for 5 pgrep -P "$beta_pane" # exec -a renames it, so match the pane's child, not "sleep"
beta_pid="$(pgrep -P "$beta_pane" | head -1)"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d}' "$beta_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$beta_pid.json"
check "send --list shows the model and effort a session was launched with" bash -c 'grep -E "^beta " <<<"$1" | grep -q "sonnet/low"' _ "$("$BOSUN" send --list)"
check "refresh restarts an idle session whose model/effort no longer match the config" contains "$("$BOSUN" refresh --dry-run beta 2>&1)" "beta: would restart (model/effort sonnet/low -> opus/medium"
check "... but not a Claude it did not launch" bash -c '! grep -q "model/effort" <<<"$1"' _ "$("$BOSUN" refresh --dry-run alpha 2>&1)"
cat >"$WORK/old-claude.sh" <<'SH'
exec -a 'claude --dangerously-skip-permissions --name beta --remote-control beta' sleep 600
SH
kill "$beta_pid"
wait_for 5 bash -c '! pgrep -P "$1" >/dev/null' _ "$beta_pane"
tmux -L "$SOCKET" send-keys -t "$beta_window" "bash $WORK/old-claude.sh" Enter
wait_for 5 pgrep -P "$beta_pane"
beta_pid="$(pgrep -P "$beta_pane" | head -1)"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d}' "$beta_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$beta_pid.json"
check "an bosun session from before model/effort settings is moved onto them" contains "$("$BOSUN" refresh --dry-run beta 2>&1)" "beta: would restart (model/effort -/- -> opus/medium"
printf '{"pid":%d,"status":"waiting","version":"2.0.0","statusUpdatedAt":%d}' "$gamma_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$gamma_pid.json"
check "refresh leaves a session waiting for you alone" contains "$("$BOSUN" refresh --dry-run gamma 2>&1)" "gamma: waiting (a permission prompt or question waits for you); leaving alone"
check "send refuses a session waiting for you" contains "$("$BOSUN" send gamma hello 2>&1)" "not pasting into it"

echo "console sessions and health (these fake Claude windows; it never starts Claude)"
printf '#!/bin/sh\ntouch "%s"\nexit 1\n' "$WORK/claude-was-started" >"$WORK/no-claude"
chmod +x "$WORK/no-claude"
touch "$WORK/before-console"
gamma_screen="$(tmux -L "$SOCKET" capture-pane -p -t "=smoke:gamma")"
BOSUN_CLAUDE_BIN="$WORK/no-claude" "$BOSUN" console --port 0 >"$WORK/live-console.out" 2>"$WORK/live-console.err" &
CONSOLE_PID=$!
check "the console starts on the live session's state" console_started "$WORK/live-console.out" "$WORK/live-console.err"
LIVE_URL="$(grep -o 'http://[^ ]*' "$WORK/live-console.out")"
LIVE_BASE="${LIVE_URL%%/\?*}"
LIVE_COOKIE="bosun_console_${LIVE_BASE##*:}=${LIVE_URL##*t=}"
check "/api/sessions has a card per window, with its Claude's status, model and attach command" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/sessions" | jq -e --arg socket "$3" "
    ([.windows[] | select(.kind == \"session\") | .name] == [\"alpha\", \"beta\", \"gamma\"]) and
    (.windows[] | select(.name == \"gamma\") | .state == \"waiting\" and .model == \"sonnet\" and .claude.seconds >= 3600
      and .attach == \"tmux -L \(\$socket) attach -t smoke:gamma\") and
    (.counts.waiting == 1)"' _ "$LIVE_BASE" "$LIVE_COOKIE" "$SOCKET"
check "/api/health has every watchdog check, and the flags as booleans" bash -c '
  names="$("$3" watchdog --list | cut -d" " -f1 | jq -R . | jq -sc .)" &&
    curl -sf -H "Cookie: $2" "$1/api/health" | jq -e --argjson names "$names" "
      ([.watchdog.checks[].name] == \$names) and ([.flags[] | type] | unique == [\"boolean\"]) and
      (.events | has(\"deliveries_today\") and has(\"max_per_day\") and has(\"pending\")) and
      (.slack_watch | has(\"spend_today\") and has(\"cap\")) and (.login.state | type == \"string\")"' \
  _ "$LIVE_BASE" "$LIVE_COOKIE" "$BOSUN"
check "/api/needs-you lists the window waiting at a prompt" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/needs-you" | jq -e "[.windows[].name] == [\"gamma\"]"' _ "$LIVE_BASE" "$LIVE_COOKIE"
check "/api/sessions and /api/health need the cookie: 401" bash -c '
  for path in /api/sessions /api/health; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" "$1$path")" = 401 ] || exit 1
  done' _ "$LIVE_BASE"
# The console's own access token (console/token, written at start) is its only file there.
check "... and reading them started no Claude, wrote no state and left the panes alone" bash -c '
  [ ! -e "$1/claude-was-started" ] && [ -z "$(find "$2" -type f -newer "$1/before-console" ! -path "$2/console/token")" ] &&
    [ "$(tmux -L "$3" capture-pane -p -t "=smoke:gamma")" = "$4" ]' _ "$WORK" "$BOSUN_STATE_DIR" "$SOCKET" "$gamma_screen"
kill "$CONSOLE_PID" 2>/dev/null
check "... and it stops" wait_for 10 bash -c '! kill -0 "$1" 2>/dev/null' _ "$CONSOLE_PID"
CONSOLE_PID=""
tmux -L "$SOCKET" send-keys -t "=smoke:alpha" "cd /" Enter
wait_for 5 bash -c '[ "$(tmux -L "$1" display -p -t =smoke:alpha "#{pane_current_path}")" = / ]' _ "$SOCKET"
"$BOSUN" refresh alpha >/dev/null 2>&1
check "relaunch starts in the manifest directory" wait_for 10 bash -c '[ "$(tmux -L "$1" display -p -t "=smoke:alpha" "#{pane_current_path}")" = "$2" ]' _ "$SOCKET" "$REPO_A"
tmux -L "$SOCKET" split-window -d -t "=smoke:alpha" "sleep 600"
check "refresh refuses to relaunch into a split window and fails" bash -c '! "$1" refresh alpha >"$2" 2>&1 && grep -q "split into several panes" "$2"' _ "$BOSUN" "$WORK/split.out"
check "... and the other pane keeps running" bash -c 'tmux -L "$1" list-panes -t =smoke:alpha -F "#{pane_current_command}" | grep -qx sleep' _ "$SOCKET"
check "refresh fails without a manifest" bash -c '! BOSUN_SESSIONS_FILE=/nonexistent "$1" refresh 2>/dev/null' _ "$BOSUN"
"$BOSUN" stop >/dev/null 2>&1

echo "restarts leave no shells behind (zsh with a prompt helper in its startup file)"
# Like a prompt theme's git-status daemon: interactive startup forks a helper off (detached, so
# launchd adopts it at once), and only the shell's first prompt lets it exit. A pane's launch shell
# (zsh -lic) never shows a prompt. The cap ends a leaked helper after 5 minutes.
HELPER_PIDS="$WORK/helper-pids"
cat >"$HOME/.zshrc" <<'ZSHRC'
if [[ -o interactive ]]; then
  zmodload zsh/system zsh/zselect zsh/datetime
  _helper_marker="$HOME/helper.$$.$EPOCHREALTIME"
  : >"$_helper_marker"
  ( {
    print $sysparams[pid] >>"$HELPER_PIDS"
    repeat 1500; do [[ -e $_helper_marker ]] || exit 0; zselect -t 20; done
  } &! )
  _helper_prompt() { rm -f "$_helper_marker"; precmd_functions=(${precmd_functions:#_helper_prompt}); }
  precmd_functions+=(_helper_prompt)
fi
ZSHRC
saved_shell="$SHELL" saved_zdotdir="${ZDOTDIR-}"
export SHELL=/bin/zsh ZDOTDIR="$HOME" HELPER_PIDS
"$BOSUN" start >/dev/null 2>&1
wait_for 10 has_windows "alpha beta gamma "
launched_in() { tmux -L "$SOCKET" capture-pane -p -S -200 -t "=smoke:$1" | grep -q "dry-run: would run"; }
for _ in 1 2 3; do
  wait_for 10 launched_in alpha
  helper_pane="$(tmux -L "$SOCKET" display -p -t "=smoke:alpha" '#{pane_pid}')"
  fake_claude alpha idle 1.0.0 3600 >/dev/null
  "$BOSUN" refresh alpha >/dev/null 2>&1
  wait_for 10 bash -c '[ "$(tmux -L "$1" display -p -t "=smoke:alpha" "#{pane_pid}")" != "$2" ]' _ "$SOCKET" "$helper_pane"
done
wait_for 10 launched_in alpha
"$BOSUN" stop >/dev/null 2>&1
helpers_left() { # the helpers still running, as "pid ppid"
  local pid
  [ -f "$HELPER_PIDS" ] || return 0
  while read -r pid; do ps -o pid=,ppid= -p "$pid"; done <"$HELPER_PIDS"
}
check "the shells' startup files started helpers (one per launch shell at least)" bash -c '[ "$(wc -l <"$1")" -ge 6 ]' _ "$HELPER_PIDS"
no_helpers_left() {
  local left
  left="$(helpers_left)"
  [ -z "$left" ] || {
    echo "still running (pid ppid): $left"
    return 1
  }
}
wait_for 5 no_helpers_left
check "after start, three restarts and stop, none is left behind" no_helpers_left
helpers_left | awk '{ print $1 }' | xargs kill 2>/dev/null
export SHELL="$saved_shell"
if [ -n "$saved_zdotdir" ]; then export ZDOTDIR="$saved_zdotdir"; else unset ZDOTDIR; fi
rm -f "$HOME/.zshrc" "$HOME"/helper.*

echo "orphans (fake processes adopted by launchd)"
FAKE_PIDS="$WORK/fake-orphan-pids"
fake_process() { # <command line> [child]: a process launchd adopts that shows <command line>; with "child", it has one
  (FAKE_PIDS="$FAKE_PIDS" perl -e 'open(my $f, ">>", $ENV{FAKE_PIDS}) or die; print $f "$$\n"; close $f;
    $0 = $ARGV[0]; if ($ARGV[1]) { if (!fork) { sleep 60; exit } } sleep 60' "$1" "${2:-}" &)
}
wrapper="$SHELL -lic $(printf '%q' "$BOSUN")"
fake_process "$wrapper launch smoke-orphan ; exec $SHELL -l"
fake_process "$wrapper launch smoke-busy ; exec $SHELL -l" child
fake_process "tmux -L $SOCKET new-session -d -s smoke-tmux -- $wrapper launch smoke-tmux"
fake_process "$SHELL -lic /Users/me/elsewhere/bin/bosun launch smoke-elsewhere ; exec $SHELL -l"
wait_for 5 bash -c '[ "$(wc -l <"$1")" -ge 4 ]' _ "$FAKE_PIDS"
out="$("$BOSUN" orphans 2>&1)"
check "orphans lists a launch shell's leftover: launchd's, this checkout's command line, no children" bash -c '
  grep -q "1 shell(s) left behind" <<<"$1" && grep -q "launch smoke-orphan" <<<"$1"' _ "$out"
check "... never one with children, the tmux server or another checkout's" bash -c '! grep -qE "smoke-(busy|tmux|elsewhere)" <<<"$1"' _ "$out"
orphan_pid="$(grep "launch smoke-orphan" <<<"$out" | awk '{ print $1 }')"
check "... and stops nothing without --apply" kill -0 "$orphan_pid"
check "--apply stops it" contains "$("$BOSUN" orphans --apply 2>&1)" "stopped 1 shell(s)"
check "... only it" bash -c 'wait_gone() { for _ in $(seq 1 25); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.2; done; return 1; }
  wait_gone "$1" || exit 1
  for pid in $(cat "$2"); do [ "$pid" = "$1" ] && continue; ps -o command= -p "$pid" | grep -q "launch smoke-orphan" && exit 1; done
  [ "$(ps -o pid= -p "$(paste -sd, "$2")" | wc -l)" -ge 3 ]' _ "$orphan_pid" "$FAKE_PIDS"
check "orphans takes no other argument" fails_with "unknown argument" "$BOSUN" orphans --bogus
while read -r pid; do
  pkill -P "$pid" 2>/dev/null
  kill "$pid" 2>/dev/null
done <"$FAKE_PIDS"

echo "orchestrator refresh and check-ins"
export BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A"
"$BOSUN" start >/dev/null 2>&1
check "start opens the orchestrator first and checkin last" wait_for 10 has_windows "orch alpha beta gamma checkin "
runs_checkin_loop() { # the checkin window of this test session runs `bosun checkin --loop`
  local pane_pid pid
  pane_pid="$(tmux -L "$SOCKET" list-panes -t "=smoke:checkin" -F '#{pane_pid}' 2>/dev/null)" || return 1
  for pid in $(bash -c 'source "$1/lib/common.sh"; descendants "$2"' _ "$ROOT" "$pane_pid"); do
    ps -o command= -p "$pid" | grep -q "bosun-checkin --loop" && return 0
  done
  return 1
}
check "the checkin window runs the check-in loop" wait_for 10 runs_checkin_loop
tmux -L "$SOCKET" kill-window -t "=smoke:checkin"
check "refresh recreates a missing checkin window" contains "$("$BOSUN" refresh 2>&1)" "checkin: create the missing window"
check "... and it runs the check-in loop again" wait_for 10 runs_checkin_loop
orch_pid="$(fake_claude orch idle 2.0.0 3600 orch-2000-01-01)"
out="$("$BOSUN" refresh --dry-run orch 2>&1)"
check "orchestrator restarts on a new day" contains "$out" "orch: would restart (new day: orch-2000-01-01 -> orch-$(date +%F)"
check "... even with --drain-only" contains "$("$BOSUN" refresh --dry-run --drain-only orch 2>&1)" "orch: would restart (new day"
check "checkin starts today's conversation instead of checking in on yesterday's" contains "$("$BOSUN" checkin --dry-run 2>&1)" "would start today's conversation"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-2000-01-01"}' "$orch_pid" $((($(date +%s) - 30) * 1000)) >"$HOME/.claude/sessions/$orch_pid.json"
out="$("$BOSUN" refresh --dry-run orch 2>&1)"
check "a deferred new-day restart sends no drain prompt into yesterday's conversation" bash -c 'grep -q "still on yesterday" <<<"$1" && ! grep -q "drain prompt" <<<"$1"' _ "$out"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-%s"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) "$(date +%F)" >"$HOME/.claude/sessions/$orch_pid.json"
check "checkin nudges an idle orchestrator" contains "$("$BOSUN" checkin --dry-run 2>&1)" "would send '/bosun:chores check-in'"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch"}' "$orch_pid" $((($(date +%s) - 30) * 1000)) >"$HOME/.claude/sessions/$orch_pid.json"
check "checkin waits while you may be talking to it" bash -c 'grep -qE "skip: orchestrator is idle \(for [0-9]+s\)" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
printf '{"pid":%d,"status":"busy","version":"2.0.0","statusUpdatedAt":%d,"name":"orch"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$orch_pid.json"
check "checkin skips a busy orchestrator" contains "$("$BOSUN" checkin --dry-run 2>&1)" "skip: orchestrator is busy"
orch_status() { # <status> <seconds in it> [name]
  printf '{"pid":%d,"status":"%s","version":"2.0.0","statusUpdatedAt":%d,"name":"%s"}' "$orch_pid" "$1" $((($(date +%s) - $2) * 1000)) "${3:-orch-$(date +%F)}" >"$HOME/.claude/sessions/$orch_pid.json"
}
orch_status shell 1440
check "a background command alone (shell) does not hold the check-in back" contains "$("$BOSUN" checkin --dry-run 2>&1)" "would send '/bosun:chores check-in'"
orch_status shell 30
check "... but its turn must have ended 5 minutes ago" bash -c 'grep -qE "skip: orchestrator is shell \(for [0-9]+s\)" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
orch_status shell 1440 orch-2000-01-01
check "... and yesterday's conversation is not restarted under it" bash -c 'grep -q "background command runs (shell); today.s starts once it is idle" <<<"$1" && ! grep -q "would start today" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
orch_status waiting 3600
check "checkin still skips an orchestrator at a prompt" contains "$("$BOSUN" checkin --dry-run 2>&1)" "skip: orchestrator is waiting"
orch_status idle 3600
gate_state="$WORK/gate-state" # its own state: no events queued by earlier checks
BOSUN_STATE_DIR="$gate_state" L mark-run check-in >/dev/null
mkdir -p "$gate_state/slack-watch"
quiet_run_at="$(python3 -c 'from datetime import UTC, datetime, timedelta; print((datetime.now(UTC) + timedelta(seconds=5)).isoformat())')"
printf '{"failures":0,"runs":[{"started":"%s","found":0}]}' "$quiet_run_at" >"$gate_state/slack-watch/state.json"
gated() { BOSUN_STATE_DIR="$gate_state" BOSUN_EVENTS_ENABLED=1 BOSUN_SLACK_WATCH_ENABLED=1 "$BOSUN" checkin --dry-run 2>&1; }
check "a check-in with nothing new since the last one is skipped" bash -c 'grep -q "skip: nothing new since" <<<"$1" && ! grep -q "would send" <<<"$1"' _ "$(gated)"
check "... unless BOSUN_CHECKIN_MAX_QUIET_HOURS=0 turns the gate off" contains "$(BOSUN_CHECKIN_MAX_QUIET_HOURS=0 gated)" "would send '/bosun:chores check-in'"
printf '{"failures":2,"runs":[]}' >"$gate_state/slack-watch/state.json"
check "... and a failing Slack watcher sends it, since the check-in scans Slack itself" contains "$(gated)" "check-in has news: the Slack watcher is failing"
orch_status busy 3600
check "a check-in held back by timing exits 3 (deferred)" bash -c '"$1" checkin --dry-run >/dev/null 2>&1; [ $? -eq 3 ]' _ "$BOSUN"
orch_status waiting 3600
check "... and so does one held back by a prompt" bash -c '"$1" checkin --dry-run >/dev/null 2>&1; [ $? -eq 3 ]' _ "$BOSUN"
orch_status busy 3600
BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_INTERVAL_SECONDS=1 BOSUN_CHECKIN_EVERY_SECONDS=1 \
  "$BOSUN" checkin --loop --dry-run >"$WORK/deferred-loop.out" 2>&1 &
deferred_loop=$!
check "the loop keeps a deferred check-in due" wait_for 10 grep -q "the check-in stays due; retrying every tick" "$WORK/deferred-loop.out"
sleep 3
orch_status idle 3600
check "... and sends it on a later tick, once the orchestrator is free" wait_for 10 grep -q "would send '/bosun:chores check-in'" "$WORK/deferred-loop.out"
pkill -P "$deferred_loop" 2>/dev/null
kill "$deferred_loop" 2>/dev/null
wait "$deferred_loop" 2>/dev/null
check "... logging the skip once, not on every retry" test "$(grep -c 'skip: orchestrator is busy' "$WORK/deferred-loop.out")" = 1
check "... while events keep flowing during the wait" grep -q "\[events\] dry-run: " "$WORK/deferred-loop.out"
orch_status busy 3600 orch

echo "expired Claude login (fake claude, fictional transcripts)"
# auth status says $FAKE_AUTH (in or out); the login check request gets $FAKE_PROBE (ok or auth).
cat >"$WORK/login-claude" <<FAKE
#!/bin/bash
case "\$1" in
  --version) echo "2.0.0 (Claude Code)" ;;
  auth)
    [ "\${FAKE_AUTH:-in}" = in ] && { echo '{"loggedIn": true, "authMethod": "claude.ai"}'; exit 0; }
    echo '{"loggedIn": false, "authMethod": "none"}'
    exit 1
    ;;
  -p)
    echo probe >>"$WORK/login-probes.log"
    if [ "\${FAKE_PROBE:-ok}" = ok ]; then
      echo '{"type": "result", "is_error": false, "result": "OK", "total_cost_usd": 0.001}'
    else
      echo '{"type": "assistant", "error": "authentication_failed", "message": {"content": []}}'
      echo '{"type": "result", "is_error": true, "result": "x", "api_error_status": 401}'
    fi
    ;;
esac
FAKE
chmod +x "$WORK/login-claude"
probes() { cat "$WORK/login-probes.log" 2>/dev/null | wc -l | tr -d ' '; }
ORCH_TRANSCRIPT="$(project_dir "$REPO_A")/0000000a-0000-0000-0000-000000000000.jsonl"
mkdir -p "$(dirname "$ORCH_TRANSCRIPT")"
login_reply() { # <seconds ago> [error fields]: a reply in the orchestrator's transcript
  local at
  at="$(date -u -r $(($(date +%s) - $1)) +%Y-%m-%dT%H:%M:%S.000Z)"
  if [ -n "${2:-}" ]; then
    printf '{"type":"assistant","timestamp":"%s","isApiErrorMessage":true,%s,"message":{"model":"<synthetic>","content":[{"type":"text","text":"Login expired"}]}}\n' "$at" "$2"
  else
    printf '{"type":"assistant","timestamp":"%s","message":{"content":[{"type":"text","text":"Checked in."}]}}\n' "$at"
  fi >>"$ORCH_TRANSCRIPT"
}
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-%s","cwd":"%s","sessionId":"0000000a-0000-0000-0000-000000000000"}' \
  "$orch_pid" $((($(date +%s) - 3600) * 1000)) "$(date +%F)" "$REPO_A" >"$HOME/.claude/sessions/$orch_pid.json"
login_reply 7200
login_reply 3700 '"error":"authentication_failed","apiErrorStatus":401'
login_reply 3600 '"error":"authentication_failed"'
LC() { BOSUN_CLAUDE_BIN="$WORK/login-claude" BOSUN_LOGIN_REPLY_WAIT_SECONDS=1 "$BOSUN" "$@" 2>&1; }
out="$(FAKE_PROBE=auth LC checkin)"
check "a check-in to an orchestrator that gets only login errors is not sent" bash -c 'grep -q "orch needs /login: every reply since" <<<"$1" && grep -q "skip: orch needs /login" <<<"$1" && ! grep -q delivered <<<"$1"' _ "$out"
check "... after one login check request, which failed" test "$(probes)" = 1
out="$(FAKE_PROBE=auth LC checkin)"
check "the next check-in only skips, quietly" bash -c 'grep -q "skip: orch needs /login" <<<"$1" && ! grep -q "needs /login:" <<<"$1"' _ "$out"
check "... and makes no new login check request yet" test "$(probes)" = 1
check "status shows the window that needs a login" bash -c 'grep -q "Claude login" <<<"$1" && grep -E "^  orch +needs-login" <<<"$1"' _ "$(LC status)"
check "the watchdog's status shows it" bash -c 'grep "FAIL  login-expired" <<<"$1" | grep -q "every prompt in orch (since"' _ "$(LC watchdog --status --only login-expired)"
login_reply 60
check "a normal reply resumes check-ins" bash -c 'grep -q "orch works again" <<<"$1" && grep -q "would send" <<<"$1"' _ "$(LC checkin --dry-run)"
check "the dry run left the record alone" contains "$(cat "$BOSUN_STATE_DIR/login.json")" '"orch"'
LC checkin >/dev/null
check "a real check-in clears it" bash -c '! grep -q "\"orch\"" "$1"' _ "$BOSUN_STATE_DIR/login.json"
login_reply 30 '"error":"rate_limit","apiError":"usage_limit_reached","apiErrorStatus":429'
out="$(LC watchdog --status --only spend-limit)"
check "a usage-limit (spend limit) stop shows in the watchdog's status" bash -c 'grep "FAIL  spend-limit" <<<"$1" | grep -q "orch: it stopped on a usage limit (spend limit)"' _ "$out"
check "... and is not read as a login error" bash -c '! grep -q "every prompt in orch" <<<"$1"' _ "$(LC watchdog --status --only login-expired)"
probes_before="$(probes)"
out="$(LC watchdog --dry-run --only spend-limit)"
check "the dry run names it and makes no login check request" bash -c 'grep -q "dry-run: spend-limit: .*orch: it stopped on a usage limit" <<<"$1" && [ "$2" = "$3" ]' _ "$out" "$probes_before" "$(probes)"
# Back to the busy orchestrator the events tests expect, and no state from the real check-ins.
rm -f "$ORCH_TRANSCRIPT" "$BOSUN_STATE_DIR/login.json" "$BOSUN_STATE_DIR/watchdog.json"
printf '{"pid":%d,"status":"busy","version":"2.0.0","statusUpdatedAt":%d,"name":"orch"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$orch_pid.json"

echo "events to the orchestrator (fake Claude, send dry-run)"
EV() { BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_DEBOUNCE_SECONDS=0 BOSUN_EVENTS_MIN_IDLE_SECONDS=60 BOSUN_SEND_DRY_RUN=1 "$BOSUN" events "$@" 2>&1; }
check "events are off by default" contains "$("$BOSUN" events 2>&1)" "events are off (BOSUN_EVENTS_ENABLED=0)"
check "the earlier user-said events are pending" contains "$("$BOSUN" events --list)" "user-said exporter"
check "nothing goes to a busy orchestrator" contains "$(EV)" "skip: the orchestrator is busy"
printf '{"pid":%d,"status":"shell","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-%s"}' "$orch_pid" $((($(date +%s) - 600) * 1000)) "$(date +%F)" >"$HOME/.claude/sessions/$orch_pid.json"
check "one whose turn is over while a background command runs (shell) would get them" contains "$(EV --dry-run)" "dry-run: would deliver:"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-%s"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) "$(date +%F)" >"$HOME/.claude/sessions/$orch_pid.json"
check "a dry run shows the one batched summary" contains "$(EV --dry-run)" "dry-run: would deliver:"
check "an idle orchestrator gets the summary" contains "$(EV)" "delivered "
check "... once" bash -c '[ "$("$1" events --list)" = "(no events)" ] && [ -z "$(BOSUN_EVENTS_ENABLED=1 "$1" events 2>&1)" ]' _ "$BOSUN"
L report 'slack:C1/6' --summary 'found the cause' >/dev/null
check "with events off the ledger queues nothing" bash -c '[ "$("$1" events --list)" = "(no events)" ]' _ "$BOSUN"
BOSUN_EVENTS_ENABLED=1 L report 'slack:C1/6' --summary 'found the cause, again' >/dev/null
check "a check-in covers what is pending" contains "$("$BOSUN" events --covered)" "1 pending event(s) covered by the check-in"
check "status shows the dispatcher's last run" contains "$("$BOSUN" events --status)" "deliveries today: 1"

echo "slack watcher (fake claude with fixture JSON)"
# The fake prints what a real call streams: the init message with the tools it could see, then the
# result with the structured output. SLACK_FAKE=write adds a write tool, SLACK_FAKE=garbage prints
# no result. Its arguments go to slack-claude.args.
cat >"$WORK/slack-claude" <<FAKE
#!/bin/bash
printf '%s\n' "\$@" >"$WORK/slack-claude.args"
cat >/dev/null
p=mcp__plugin_slack_slack__
tools="\"\${p}slack_search_public_and_private\",\"\${p}slack_read_thread\""
[ "\${SLACK_FAKE:-}" = write ] && tools="\$tools,\"\${p}slack_send_message\""
[ "\${SLACK_FAKE:-}" = garbage ] && { echo "not json"; exit 1; }
sec=$(($(date +%s) - 120))
link="https://acme.slack.com/archives/C0000000001/p\${sec}000100"
echo "{\"type\":\"system\",\"subtype\":\"init\",\"tools\":[\$tools]}"
echo "{\"type\":\"result\",\"is_error\":false,\"total_cost_usd\":0.03,\"permission_denials\":[],\"structured_output\":{\"truncated\":false,\"messages\":[{\"permalink\":\"\$link\",\"channel\":\"C0000000001\",\"ts\":\"\$sec.000100\",\"thread_ts\":\"\",\"author\":\"Dana\",\"kind\":\"mention\",\"excerpt\":\"can you check the exporter header?\"},{\"permalink\":\"https://evil.example.com/x\",\"channel\":\"C1\",\"ts\":\"1.0\",\"thread_ts\":\"\",\"author\":\"x\",\"kind\":\"dm\",\"excerpt\":\"x\"}]}}"
FAKE
chmod +x "$WORK/slack-claude"
echo '{"mcpServers": {"plugin:slack:slack": {"type": "http", "url": "https://mcp.example.com/mcp"}}}' >"$WORK/slack-mcp.json"
SW() { BOSUN_CLAUDE_BIN="$WORK/slack-claude" BOSUN_SLACK_WATCH_MCP_CONFIG="$WORK/slack-mcp.json" "$BOSUN" slack-watch "$@" 2>&1; }
SWE() { BOSUN_EVENTS_ENABLED=1 SW "$@"; }
"$BOSUN" events --covered >/dev/null
check "a scheduled watch does nothing while it is off" bash -c '[ -z "$1" ] && [ ! -e "$2/slack-watch/state.json" ]' _ "$(SW --scheduled)" "$BOSUN_STATE_DIR"
check "a watch needs events on" contains "$(SW)" "events are off (BOSUN_EVENTS_ENABLED=0)"
cp "$BOSUN_STATE_DIR/slack-watch/state.json" "$WORK/slack-state-before.json"
out="$(SWE --dry-run)"
check "a dry run shows what it would queue, and the bad entry rejected" bash -c 'grep -q "would queue: mention https://acme.slack.com/archives/C0000000001/p" <<<"$1" && grep -q "1 rejected (not a Slack permalink)" <<<"$1"' _ "$out"
check "... queues nothing and writes no state" bash -c '[ "$("$1" events --list)" = "(no events)" ] && cmp -s "$2/slack-watch/state.json" "$3"' _ "$BOSUN" "$BOSUN_STATE_DIR" "$WORK/slack-state-before.json"
check "the call gets only Slack's read tools, no built-in tools and no settings" bash -c '
  args="$(tr "\n" " " <"$1")"
  for want in "--strict-mcp-config" "--setting-sources  " "--tools  --allowedTools" "--permission-mode dontAsk" "--permission-prompts none" "--disallowedTools mcp__plugin_slack_slack__slack_send_message"; do
    grep -qF -- "$want" <<<"$args" || { echo "missing: $want"; exit 1; }
  done
  ! grep -qE "Bash|--dangerously" <<<"$args"' _ "$WORK/slack-claude.args"
check "a watch queues one slack event per new message" contains "$(SWE)" "1 message(s) (1 mention, 0 dm, 0 thread-reply), 1 new"
check "... which the event list shows with its permalink" contains "$("$BOSUN" events --list)" "slack     slack-watch    https://acme.slack.com/archives/C0000000001/p"
check "... and the dispatcher frames as untrusted, for chores step 1" contains "$(BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_FAST_DEBOUNCE_SECONDS=0 BOSUN_SEND_DRY_RUN=1 "$BOSUN" events --dry-run 2>&1)" "untrusted excerpt: \"can you check the exporter header?\""
check "the next watch does not queue it again" contains "$(SWE)" "1 already queued"
check "a scheduled watch waits for its interval" test -z "$(BOSUN_SLACK_WATCH_ENABLED=1 BOSUN_DAYS=MTWRFSU BOSUN_START_TIME=00:00 BOSUN_AWAKE_UNTIL=00:00 SWE --scheduled)"
check "a call that could see a write tool is refused" bash -c 'out="$(SLACK_FAKE=write "$@")"; [ $? -eq 1 ] && grep -q "ERROR: the Slack watch failed (1 in a row): tool restriction not in force" <<<"$out"' _ env BOSUN_EVENTS_ENABLED=1 BOSUN_CLAUDE_BIN="$WORK/slack-claude" BOSUN_SLACK_WATCH_MCP_CONFIG="$WORK/slack-mcp.json" "$BOSUN" slack-watch
check "output that is not the JSON is a failure" contains "$(SLACK_FAKE=garbage SWE)" "failed (2 in a row)"
check "status shows the failures and today's spend, a refused call's included" bash -c 'grep -q "failures in a row: 2" <<<"$1" && grep -q "spent today \$0.09" <<<"$1"' _ "$(SW --status)"
check "the watchdog checks the watcher once it is enabled" contains "$(BOSUN_EVENTS_ENABLED=1 BOSUN_SLACK_WATCH_ENABLED=1 "$BOSUN" watchdog --status 2>&1)" "slack-watch      "
check "the check-in loop runs the watcher with events on" contains "$(BOSUN_EVENTS_ENABLED=1 "$BOSUN" checkin --dry-run 2>&1)" "dry-run: would run bosun slack-watch --scheduled"
"$BOSUN" events --covered >/dev/null

echo "alerts"
echo '{"result":"https://x.slack.com/archives/D1/p1","permission_denials":[],"total_cost_usd":0.02}' >"$WORK/alert-sent.json"
cat >"$WORK/alert-claude" <<FAKE
#!/bin/bash
[ "\$1" = auth ] && { echo '{"loggedIn": true, "authMethod": "claude.ai"}'; exit 0; } # the watchdog's login check
printf '%s\n' "\$@" >"$WORK/alert-claude.args"
if [ -n "\${ALERT_FAKE_REPLY:-}" ]; then echo "\$ALERT_FAKE_REPLY"; else cat "$WORK/alert-sent.json"; fi
FAKE
chmod +x "$WORK/alert-claude"
export BOSUN_ALERT_SLACK_USER=U0TEST
# The watchdog alerts only inside the awake window: pin its clock to noon today, every day scheduled,
# and give the fake records times relative to that.
NOON="$(date -j -f '%Y-%m-%d %H:%M:%S' "$(date +%F) 12:00:00" +%s)"
export BOSUN_WATCHDOG_NOW="$NOON" BOSUN_DAYS=MTWRFSU
claude_record() { # <pid> <status> <seconds in status at noon> [name]
  printf '{"pid":%d,"status":"%s","version":"2.0.0","statusUpdatedAt":%d,"name":"%s"}' \
    "$1" "$2" $(((NOON - $3) * 1000)) "${4:-orch}" >"$HOME/.claude/sessions/$1.json"
}
claude_record "$orch_pid" waiting 600
check "no alert while the orchestrator has waited only briefly" bash -c '! grep -q "would notify" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
claude_record "$orch_pid" waiting 3600
check "a long wait at a prompt alerts on Slack" contains "$("$BOSUN" checkin --dry-run 2>&1)" "would notify and DM you on Slack: [bosun] The orch session has waited 60 min"
check "the check-in loop's dry run changes no watchdog state" test ! -e "$BOSUN_STATE_DIR/watchdog.json"
out="$(BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" checkin 2>&1)"
check "... and sends it" contains "$out" "alert sent as a Slack DM"
check "the DM can never wait for an approval" bash -c 'grep -qx dontAsk "$1" && grep -qx none "$1" && grep -qx ToolSearch "$1" && ! grep -q -- "--dangerously" "$1"' _ "$WORK/alert-claude.args"
check "... once per prompt" bash -c '! grep -q alert <<<"$1"' _ "$(BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" checkin 2>&1)"
check "... and the loop's old marker agrees, for a loop still running the old code" test "$(cat "$BOSUN_STATE_DIR/alerted-orch")" = "$orch_pid:$(((NOON - 3600) * 1000))"
mkdir -p "$HOME/Library/LaunchAgents"
touch "$HOME/Library/LaunchAgents/com.bosun.morning.watchdog.plist"
claude_record "$orch_pid" waiting 7200
check "once the watchdog agent is installed, the loop leaves alerts to it" bash -c '! grep -q "watchdog\|would notify" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
rm "$HOME/Library/LaunchAgents/com.bosun.morning.watchdog.plist"
check "a refused DM is reported" fails_with "not delivered on Slack" env BOSUN_CLAUDE_BIN="$WORK/alert-claude" ALERT_FAKE_REPLY='{"result":"blocked","permission_denials":[{"tool_name":"x"}]}' "$BOSUN" alert hi
check "a bad member id is refused" fails_with "must be a Slack member id" env BOSUN_ALERT_SLACK_USER='U1"; x' "$BOSUN" alert hi
check "without a member id, only a notification" contains "$(BOSUN_ALERT_SLACK_USER='' "$BOSUN" alert hi 2>&1)" "notification only"
check "send dry-run only logs" contains "$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" send orch hello 2>&1)" "dry-run: would deliver to smoke:orch: hello"
check "send --new dry-run opens nothing" bash -c 'BOSUN_SEND_DRY_RUN=1 "$1" send --new extra "$2" hi >/dev/null && ! tmux -L "$3" list-windows -t =smoke -F "#{window_name}" | grep -qx extra' _ "$BOSUN" "$REPO_A" "$SOCKET"

echo "watchdog (pinned clock, fake alert Claude)"
# A label with no launchd job, so `launchctl print` (read-only) finds nothing.
export BOSUN_LAUNCHD_LABEL="com.bosun.smoke-watchdog"
TODAY="$(date +%F)"
printf '%s [morning] === morning run (trigger=launchd, session=smoke, dry_run=0) ===\n%s [morning] morning run complete\n' \
  "$TODAY 08:01:00" "$TODAY 08:01:40" >"$BOSUN_LOG_DIR/morning-$TODAY.log"
WD() { BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" watchdog "$@" 2>&1; }
at_minute() { echo $((NOON + $1 * 60)); } # the pinned clock, <n> minutes after noon
check "--help describes every check --list names" bash -c '
  for name in $("$1" watchdog --list | cut -d" " -f1); do "$1" watchdog --help | grep -q "^  $name " || { echo "$name"; exit 1; }; done' _ "$BOSUN"
check "an unknown check is a usage error" bash -c '"$1" watchdog --only nope >/dev/null 2>&1; [ $? -eq 2 ]' _ "$BOSUN"
claude_record "$orch_pid" waiting 3600 # the prompt the check-in loop alerted about above
out="$(WD --status)"
check "status sees the session and its check-in loop" contains "$out" "checkin-missing  the check-in loop is running"
check "status shows a prompt it already alerted about" bash -c 'grep "WARN  waiting" <<<"$1" | grep -q "already alerted"' _ "$out"
check "status on a fresh install: the first run only baselines the logs" contains "$out" "first scan:"
cp "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before.json"
claude_record "$orch_pid" waiting 3000
check "a new prompt: the dry run would alert" contains "$(WD --dry-run)" "would notify and DM you on Slack: [bosun] The orch session has waited 50 min"
check "... and changes no state" cmp -s "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before.json"
check "a run alerts" contains "$(WD)" "alert sent as a Slack DM"
check "... once" test -z "$(BOSUN_WATCHDOG_NOW="$(at_minute 5)" WD)"
check "the first full run baselined the logs and recorded itself" bash -c 'jq -e ".logs != null and .last_run != null" "$1"' _ "$BOSUN_STATE_DIR/watchdog.json"
echo "$TODAY 12:07:00 [checkin] ERROR: something broke" >>"$BOSUN_LOG_DIR/checkin-$TODAY.log"
check "a new ERROR line in a log alerts" contains "$(BOSUN_WATCHDOG_NOW="$(at_minute 10)" WD)" "alert sent as a Slack DM (\$0.02): 1 new ERROR line(s) in the bosun logs: checkin-$TODAY.log: [checkin] ERROR: something broke"
claude_record "$orch_pid" shell 14400 # a background command since 08:00
cp "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before.json"
check "a background command keeping a window in shell for 4 awake hours warns" contains "$(WD --dry-run --only long-shell)" "long-shell: alerts now: A background command has kept orch in shell for 4h"
check "... the dry run records no episode" cmp -s "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before.json"
check "... and status shows it" bash -c 'grep "WARN  long-shell" <<<"$1" | grep -q "check it is still wanted"' _ "$(WD --status --only long-shell)"
claude_record "$orch_pid" shell 3600
check "an hour in shell is fine" contains "$(WD --status --only long-shell)" "ok    long-shell       no Claude window kept in shell for 4+ awake hours"
claude_record "$orch_pid" waiting 3000 # back to the prompt alerted about above
"$BOSUN" stop >/dev/null 2>&1
check "stop records that you stopped the session" test -f "$BOSUN_STATE_DIR/stopped-smoke"
check "a session you stopped is not missing" contains "$(BOSUN_WATCHDOG_NOW="$(at_minute 15)" WD --status)" "session-missing  you stopped it at"
check "... and nothing alerts" test -z "$(BOSUN_WATCHDOG_NOW="$(at_minute 20)" WD)"
rm "$BOSUN_STATE_DIR/stopped-smoke"
check "a session gone without a stop is confirmed on the next run" contains "$(BOSUN_WATCHDOG_NOW="$(at_minute 25)" WD --dry-run)" "session-missing: alerts if still so on the next run"
BOSUN_WATCHDOG_NOW="$(at_minute 25)" WD >/dev/null
check "... then alerts" contains "$(BOSUN_WATCHDOG_NOW="$(at_minute 30)" WD)" "tmux session 'smoke' is not running"
check "... once" test -z "$(BOSUN_WATCHDOG_NOW="$(at_minute 35)" WD)"
evening="$(date -j -f '%Y-%m-%d %H:%M:%S' "$TODAY 21:00:00" +%s)"
echo "$TODAY 21:00:00 [checkin] ERROR: late" >>"$BOSUN_LOG_DIR/checkin-$TODAY.log"
check "outside the awake window nothing is sent" contains "$(BOSUN_WATCHDOG_NOW="$evening" WD --dry-run)" "log-errors: held until the awake window"
check "status reports the watchdog" contains "$("$BOSUN" status 2>&1)" "Watchdog (com.bosun.smoke-watchdog.watchdog)"
unset BOSUN_WATCHDOG_NOW BOSUN_DAYS BOSUN_ALERT_SLACK_USER BOSUN_LAUNCHD_LABEL

echo "sessions and learn (fake analysis Claude)"
cat >"$WORK/learn-claude" <<'EOF'
#!/bin/bash
cat >/dev/null
echo '{"total_cost_usd": 0.05, "structured_output": {"summary": "quiet", "findings": [{"key": "smoke-rule", "category": "prompt-gap", "severity": "low", "title": "Smoke rule", "summary": "s", "evidence": [{"source": "session", "ref": "x", "quote": "Bosun activity digest"}], "proposal": {"target": "policy", "text": "Smoke rule text.", "rationale": "r"}}]}}'
EOF
chmod +x "$WORK/learn-claude"
LEARN() { BOSUN_CLAUDE_BIN="$WORK/learn-claude" "$BOSUN" learn "$@" 2>&1; }
check "sessions --json lists the configured windows, running or not" bash -c '"$1" sessions --json | jq -e "[.[] | select(.kind == \"session\") | .name] == [\"alpha\", \"beta\", \"gamma\"]"' _ "$BOSUN"
check "sessions prints a table" contains "$("$BOSUN" sessions)" "gamma"
check "learn --collect-only writes a digest and calls no Claude" contains "$(BOSUN_CLAUDE_BIN=false "$BOSUN" learn --collect-only --out "$WORK/learn-digest" 2>&1)" "digest of"
check "... and records no run" test ! -e "$BOSUN_STATE_DIR/learn/state.json"
check "... and says what a run would cost" contains "$(BOSUN_CLAUDE_BIN=false "$BOSUN" learn --collect-only --out "$WORK/learn-digest" 2>&1)" "a run would cost about \$"
check "a run estimated over the budget never calls Claude" contains "$(BOSUN_LEARN_MAX_BUDGET_USD=0.01 BOSUN_CLAUDE_BIN=false "$BOSUN" learn --dry-run --since 2h --out "$WORK/learn-digest" 2>&1)" "not calling Claude"
BOSUN_LEARN_MAX_DIGEST_KB=1 BOSUN_CLAUDE_BIN=false "$BOSUN" learn --collect-only --out "$WORK/learn-capped" >/dev/null 2>&1
check "a digest over its cap says at the top what it left out" grep -q "^## Trimmed to fit the digest cap (1 KB" "$WORK"/learn-capped/*/digest.md
check "a run proposes its change in the ledger" contains "$(LEARN --since 2h)" "new proposals: learn:$TODAY/1"
check "... as a pending proposal" contains "$("$BOSUN" ledger list --proposals)" "learn:$TODAY/1"
check "status lists it as waiting for you" contains "$(LEARN --status)" "learn:$TODAY/1: Smoke rule"
check "... and so do learn status and learn list" bash -c '[ "$("$1" learn status 2>&1)" = "$("$1" learn --status 2>&1)" ] && [ "$("$1" learn list 2>&1)" = "$("$1" learn --status 2>&1)" ]' _ "$BOSUN"
check "apply appends the rule to the site policy" bash -c '"$1" learn apply "learn:$2/1" >/dev/null 2>&1 && grep -q "Smoke rule text." "$3/policy.md"' _ "$BOSUN" "$TODAY" "$BOSUN_CONFIG_DIR"
check "... and closes the item" contains "$("$BOSUN" ledger show "learn:$TODAY/1")" '"state": "applied"'
check "a second run does not propose it again" contains "$(LEARN --since 2h)" "new proposals: none"
check "the watchdog checks the learning run once it is enabled" contains "$(BOSUN_LEARN_ENABLED=1 "$BOSUN" watchdog --status 2>&1)" "learn            "
rm -f "$BOSUN_CONFIG_DIR/policy.md"

echo "folder trust"
TRUST_REPO="$WORK/repos/trust-me"
git init -q "$TRUST_REPO" && git -C "$TRUST_REPO" remote add origin git@github.com:acme/api.git
TRUST_REPO="$(cd "$TRUST_REPO" && pwd -P)"
check "--check fails for an untrusted folder" bash -c '! "$1" trust --check "$2"' _ "$BOSUN" "$TRUST_REPO"
check "refuses a repo outside the mask" fails_with "outside the repo mask" env BOSUN_REPO_MASK="other/*" "$BOSUN" trust "$TRUST_REPO"
check "refuses a folder that is not a git repo" fails_with "not a git repository" "$BOSUN" trust "$WORK"
check "trusts a repo inside the mask" env BOSUN_REPO_MASK="acme/*" "$BOSUN" trust "$TRUST_REPO"
check "records it the way Claude does" test "$(jq -r --arg d "$TRUST_REPO" '.projects[$d].hasTrustDialogAccepted' "$HOME/.claude.json")" = true
check "--check passes once trusted" "$BOSUN" trust --check "$TRUST_REPO"
check "--revoke removes it" bash -c '"$1" trust --revoke "$2" && ! "$1" trust --check "$2"' _ "$BOSUN" "$TRUST_REPO"
check "--any trusts any folder" "$BOSUN" trust --any "$REPO_B"

echo "send --new and --start (fake Claude that registers itself)"
cat >"$WORK/live-claude" <<FAKE
#!/bin/bash
if [ "\$1" = "--version" ]; then echo "2.0.0 (Claude Code)"; exit 0; fi
printf '%s\n' "\${@:1:\$#-1}" >"$WORK/live-claude.\$\$.args"
printf '%s' "\${!#}" >"$WORK/live-claude.\$\$.prompt"
env | grep '^BOSUN_' | cut -d= -f1 >"$WORK/live-claude.\$\$.env"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d}' \$\$ "\$(date +%s)000" >"\$HOME/.claude/sessions/\$\$.json"
exec sleep 600
FAKE
chmod +x "$WORK/live-claude"
BOSUN_CLAUDE_BIN="$WORK/live-claude" "$BOSUN" start >/dev/null 2>&1
wait_for 10 has_windows "orch alpha beta gamma checkin "
live() { BOSUN_LAUNCH_DRY_RUN=0 BOSUN_START_TIMEOUT=5 "$BOSUN" send "$@"; }
claude_in() { # <window>: prints the pid of the fake Claude running in the window
  local pane_pid
  pane_pid="$(tmux -L "$SOCKET" list-windows -t "=smoke" -F '#{window_name} #{pane_pid}' | awk -v w="$1" '$1 == w { print $2 }')"
  bash -c 'source "$1/lib/common.sh"; claude_pid_under "$2"' _ "$ROOT" "$pane_pid"
}
# Longer than the 1024-byte terminal line limit, multi-line, and full of shell metacharacters.
long_prompt="$(
  printf 'Ledger id: review:o/r#7\nDo not post. It'"'"'s "quoted" $HOME `date` \\ %%s\n'
  head -c 3000 /dev/zero | tr '\0' x
  printf '\nlast line'
)"
out="$(BOSUN_TRUST_MASKED_REPOS=0 live --new delta "$REPO_A" "$long_prompt" 2>&1)"
check "--new stops at once in an untrusted folder" contains "$out" "does not trust $REPO_A"
check "... without opening a window" bash -c '! tmux -L "$1" list-windows -t =smoke -F "#{window_name}" | grep -qx delta' _ "$SOCKET"
"$BOSUN" trust --any "$REPO_A" >/dev/null
out="$(live --new delta "$REPO_A" "$long_prompt" 2>&1)"
check "--new reports Claude running" contains "$out" "Claude is running in smoke:delta"
delta_pid="$(claude_in delta)"
check "--new starts Claude in the new window" test -n "$delta_pid"
check "--new delivers a long prompt intact" bash -c '[ "$(cat "$1")" = "$2" ]' _ "$WORK/live-claude.$delta_pid.prompt" "$long_prompt"
check "--new names the session after the window" grep -qx delta "$WORK/live-claude.$delta_pid.args"
check "Claude does not inherit the config" bash -c '! grep -qx BOSUN_DRAIN_PROMPT "$1"' _ "$WORK/live-claude.$delta_pid.env"
check "... but keeps real environment overrides" grep -qx BOSUN_CONFIG_DIR "$WORK/live-claude.$delta_pid.env"
L upsert 'slack:C8/1' --kind mention >/dev/null
out="$(live --new --model haiku --effort low zeta "$REPO_A" "start low" 2>&1)"
check "send --new --model/--effort start the window with them" bash -c 'grep -qx -- "--model" "$1" && grep -qx haiku "$1" && grep -q "\"effortLevel\":\"low\"" "$1"' _ "$WORK/live-claude.$(claude_in zeta).args"
check "delegate --window delivers through send" contains "$("$BOSUN" delegate investigate --id slack:C8/1 --ask 'look into it' --window delta 2>&1)" "delivered to smoke:delta"
check "the prompt file is removed once read" bash -c '[ -z "$(ls -A "$1")" ]' _ "$BOSUN_STATE_DIR/prompts"
check "--new refuses an existing window" fails_with "already exists" live --new delta "$REPO_A" again
check "--start refuses a window already running Claude" fails_with "already runs Claude" live --start delta again
out="$(live --start alpha "resume with this" 2>&1)"
check "--start starts Claude in an idle window" contains "$out" "Claude is running in smoke:alpha"
alpha_pid="$(claude_in alpha)"
check "--start resumes the window's named session" grep -qx 11111111-0000-0000-0000-000000000002 "$WORK/live-claude.$alpha_pid.args"
check "--start delivers the prompt" test "$(cat "$WORK/live-claude.$alpha_pid.prompt")" = "resume with this"
out="$(live --start beta "x" 2>&1)"
check "--start fails when Claude does not start" test $? -ne 0
check "a failed start shows the window's output" contains "$out" "already running as pid $$"
out="$(live --new epsilon "$TRUST_REPO" hi 2>&1)"
check "--new trusts a repo inside the mask before starting" contains "$out" "trusted: $TRUST_REPO"
check "... and Claude starts there" contains "$out" "Claude is running in smoke:epsilon"
tmux -L "$SOCKET" send-keys -t "=smoke:gamma" "sleep 600" Enter
pane_has_children() { [ -n "$(pgrep -P "$(tmux -L "$SOCKET" display -p -t "=smoke:$1" '#{pane_pid}')")" ]; }
wait_for 5 pane_has_children gamma
check "--start never replaces a running command" fails_with "running another command" env BOSUN_RELAUNCH_SETTLE_SECONDS=1 "$BOSUN" send --start gamma x
tmux -L "$SOCKET" send-keys -t "=smoke:gamma" C-c
wait_for 5 bash -c '! pgrep -P "$(tmux -L "$1" display -p -t =smoke:gamma "#{pane_pid}")" >/dev/null' _ "$SOCKET"
tmux -L "$SOCKET" send-keys -t "=smoke:gamma" "sleep 2" Enter
wait_for 5 pane_has_children gamma
check "--start waits for a briefly busy shell to settle" contains "$(live --start gamma 'after the shell settled' 2>&1)" "Claude is running in smoke:gamma"
kill "$alpha_pid"
wait_for 10 bash -c '! pgrep -P "$(tmux -L "$1" display -p -t =smoke:alpha "#{pane_pid}")" >/dev/null' _ "$SOCKET"
drain_prompt="$(bash -c 'source "$1/lib/common.sh"; load_config; printf %s "$BOSUN_DRAIN_PROMPT"' _ "$ROOT")"
out="$(live --start alpha 2>&1)"
check "--start without a message resumes with the drain prompt" test "$(cat "$WORK/live-claude.$(claude_in alpha).prompt")" = "$drain_prompt"
cat >"$WORK/echo-claude" <<FAKE
#!/bin/bash
printf '{"pid":%d,"status":"idle"}' \$\$ >"\$HOME/.claude/sessions/\$\$.json"
exec cat >"$WORK/received.txt"
FAKE
chmod +x "$WORK/echo-claude"
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n echo "$WORK/echo-claude; sleep 1"
wait_for 10 claude_in echo
cat >"$WORK/bg-claude" <<FAKE
#!/bin/bash
printf '{"pid":%d,"status":"shell"}' \$\$ >"\$HOME/.claude/sessions/\$\$.json"
exec sleep 600
FAKE
chmod +x "$WORK/bg-claude"
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n bgcmd "$WORK/bg-claude; sleep 1"
wait_for 10 claude_in bgcmd
check "send --list shows a Claude with a background command as idle-bg, not as a shell" bash -c 'grep -E "^bgcmd " <<<"$1" | grep -q "claude:idle-bg"' _ "$("$BOSUN" send --list)"
tmux -L "$SOCKET" kill-window -t "=smoke:bgcmd"
# A fake Claude screen: an input box (rule, prompt line, rule) holding $1; with $2=reject, it
# answers whatever arrives the way Claude answers an unknown slash command.
cat >"$WORK/box-claude" <<FAKE
#!/bin/bash
printf '{"pid":%d,"status":"idle"}' \$\$ >"\$HOME/.claude/sessions/\$\$.json"
rule="\$(printf '%.0s─' {1..40})"
printf '%s\n❯ %s\n%s\n' "\$rule" "\$1" "\$rule"
read -r line
[ "\${2:-}" = reject ] && echo "Unknown command: \$line"
exec sleep 600
FAKE
chmod +x "$WORK/box-claude"
box_window() { # <name> <box text> [reject]
  tmux -L "$SOCKET" new-window -d -t "=smoke:" -n "$1" "$WORK/box-claude '$2' ${3:-}; sleep 1"
  wait_for 10 claude_in "$1"
}
box_window halftyped "half typed"
check "send waits for unsent text in the input line, then fails" fails_with "input line holds unsent text (half typed)" \
  env BOSUN_SEND_WAIT_SECONDS=1 BOSUN_SEND_RETRY_SECONDS=1 "$BOSUN" send --no-item halftyped hello
box_window emptybox ""
check "send pastes into an empty input line" contains "$("$BOSUN" send --no-item emptybox hello 2>&1)" "delivered to smoke:emptybox"
box_window clearing "" reject
check "send fails when the paste ran as an unknown command" fails_with "ran the paste as an unknown command" \
  env BOSUN_SEND_CONFIRM_SECONDS=2 "$BOSUN" send --no-item clearing "/clearLedger item x"
for w in halftyped emptybox clearing; do tmux -L "$SOCKET" kill-window -t "=smoke:$w"; done
message="$(printf 'first line\nsecond line')"
"$BOSUN" send echo "$message" >/dev/null 2>&1
check "send delivers the whole multi-line message" wait_for 5 bash -c '[ "$(cat "$1")" = "$2" ]' _ "$WORK/received.txt" "$message"
check "send leaves no paste buffer behind" bash -c '! tmux -L "$1" list-buffers -F "#{buffer_name}" 2>/dev/null | grep -q bosun-send' _ "$SOCKET"
"$BOSUN" send --no-item echo - >/dev/null 2>&1 <<'EOF'
from stdin, not a dash
EOF
check "send - delivers stdin, never a literal '-'" wait_for 5 bash -c 'grep -qx "from stdin, not a dash" "$1" && ! grep -qx -- "-" "$1"' _ "$WORK/received.txt"
check "send - refuses an empty stdin" fails_with "message on stdin is empty" bash -c '"$1" send echo - </dev/null' _ "$BOSUN"
check "send --id refuses an unknown item" fails_with "no ledger item slack:C8/404" "$BOSUN" send --id slack:C8/404 echo hi
L upsert 'slack:C8/2' --kind mention >/dev/null
"$BOSUN" send --id slack:C8/2 echo "is it done" >/dev/null 2>&1
check "send --id names the item in the message" wait_for 5 grep -q "Ledger item slack:C8/2: is it done" "$WORK/received.txt"
check "... and notes the delivery on the item" contains "$(L show slack:C8/2)" "message sent to echo"
"$BOSUN" delegate investigate --id slack:C8/2 --ask 'look into it' --window echo >/dev/null 2>&1
check "delegate records the window on the item" contains "$(L delegation slack:C8/2)" '"window": "echo"'
"$BOSUN" delegate --update slack:C8/2 --details - >/dev/null 2>&1 <<'EOF'
Also check the second case.
EOF
check "delegate --update reaches the item's window, framed with its id" wait_for 5 grep -q "Update to ledger item slack:C8/2" "$WORK/received.txt"
check "... and is kept in the item's history" contains "$(L show slack:C8/2)" "Also check the second case."
L upsert 'slack:C8/3' --kind mention >/dev/null
L record-delegation 'slack:C8/3' --window gone --task investigate >/dev/null
check "delegate --update fails when the item's window is gone" fails_with "no window 'gone'" "$BOSUN" delegate --update slack:C8/3 --details 'more'
check "... says where the item went, and records nothing" bash -c '
  "$1" delegate --update slack:C8/3 --details more 2>&1 | grep -q "handed to window gone" &&
  ! "$1" ledger show slack:C8/3 | grep -q "\"event\": \"update\""' _ "$BOSUN"
L upsert 'slack:C8/4' --kind mention >/dev/null
check "delegate --update refuses an item never delegated" fails_with "never handed to a repo session" "$BOSUN" delegate --update slack:C8/4 --details x
L transition 'slack:C8/3' 'done' --note 'answered' >/dev/null
check "delegate --update refuses a finished item" fails_with "is done; a finished item takes no updates" "$BOSUN" delegate --update slack:C8/3 --details x
L upsert 'slack:C8/5' --kind mention >/dev/null
"$BOSUN" delegate investigate --id slack:C8/5 --ask 'look' --link https://x/5 --window echo >/dev/null 2>&1
check "a second hand-over awaiting a report is refused, pointing to --update" fails_with "has not reported since. To add to or correct that task: bosun delegate --update 'slack:C8/5'" "$BOSUN" delegate investigate --id slack:C8/5 --ask 'look again' --window echo
check "... and says where and when it went" bash -c '"$1" delegate investigate --id slack:C8/5 --ask x --window echo 2>&1 | grep -q "handed to window echo at .* (investigate)"' _ "$BOSUN"
check "--again hands it over anyway" contains "$("$BOSUN" delegate investigate --id slack:C8/5 --ask 'look again' --window echo --again 2>&1)" "delivered to smoke:echo"
L report 'slack:C8/5' --summary 'first pass done' >/dev/null
check "after a report a new hand-over needs no --again" contains "$("$BOSUN" delegate investigate --id slack:C8/5 --ask 'next question' --window echo 2>&1)" "delivered to smoke:echo"
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n plain
check "a move to an unknown window changes nothing" fails_with "not delivered to nowhere (see above); slack:C8/5 stays with echo" "$BOSUN" delegate --update slack:C8/5 --window nowhere --details 'echo is stuck'
check "a move to a window without Claude changes nothing" fails_with "is not running Claude" "$BOSUN" delegate --update slack:C8/5 --window plain --details 'echo is stuck'
check "a move to the same window is refused" fails_with "already delegated to echo; drop --window" "$BOSUN" delegate --update slack:C8/5 --window echo --details x
check "--update --window moves the item and tells the old window" contains "$("$BOSUN" delegate --update slack:C8/5 --window delta --details 'echo is stuck' 2>&1)" "moved from echo to delta (the old window was told to stop)"
check "... the old window gets the stop notice by id" wait_for 5 grep -q "Ledger item slack:C8/5 has moved to window delta. Stop working on it here" "$WORK/received.txt"
check "... and the ledger has the new window and the move" bash -c '
  "$1" ledger delegation slack:C8/5 | grep -q "\"window\": \"delta\".*\"moved_from\": \"echo\"\|\"moved_from\": \"echo\".*\"window\": \"delta\"" &&
  "$1" ledger show slack:C8/5 | grep -q "\"event\": \"moved\""' _ "$BOSUN"
L upsert 'slack:C8/6' --kind mention >/dev/null
L record-delegation 'slack:C8/6' --window gone2 --task investigate --ask 'look' >/dev/null
out="$("$BOSUN" delegate --update slack:C8/6 --window echo --details 'gone2 was closed' 2>&1)"
check "a move away from a gone window still happens" contains "$out" "moved from gone2 to echo (the old window was NOT told to stop)"
check "... and says the old window was not told" contains "$out" "window gone2 was not told to stop"
check "... the new window gets the whole task" wait_for 5 grep -q "Ledger item slack:C8/6 moves to this window from window gone2" "$WORK/received.txt"
L transition 'slack:C8/6' 'done' --note 'answered' >/dev/null
check "a finished item is not moved" fails_with "is done; a finished item takes no updates" "$BOSUN" delegate --update slack:C8/6 --window delta --details x
check "--start dry-run only logs" contains "$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" send --start gamma hi 2>&1)" "dry-run: would start Claude in smoke:gamma"
"$BOSUN" stop >/dev/null 2>&1

echo "check-in loop timing"
check "the loop waits one interval before the first check-in" bash -c '
  BOSUN_CHECKIN_MINUTES=1 "$1" checkin --loop >"$2" 2>&1 & pid=$!
  sleep 2; kill $pid 2>/dev/null; pkill -P $pid 2>/dev/null; wait $pid 2>/dev/null
  [ ! -s "$2" ]' _ "$BOSUN" "$WORK/checkin-loop.out"
check "one check-in refuses while check-ins are off" fails_with "check-ins are disabled" env BOSUN_CHECKIN_MINUTES=0 "$BOSUN" checkin
check "the loop refuses with check-ins off unless events run without them" fails_with "BOSUN_EVENTS_WITHOUT_CHECKINS=1 keeps the loop" env BOSUN_CHECKIN_MINUTES=0 BOSUN_EVENTS_ENABLED=1 "$BOSUN" checkin --loop
check "with events alone the loop keeps running and sends no check-in" bash -c '
  BOSUN_CHECKIN_MINUTES=0 BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_WITHOUT_CHECKINS=1 BOSUN_EVENTS_INTERVAL_SECONDS=1 \
    "$1" checkin --loop >"$2" 2>&1 & pid=$!
  sleep 3; alive=0; kill -0 $pid 2>/dev/null && alive=1
  kill $pid 2>/dev/null; pkill -P $pid 2>/dev/null; wait $pid 2>/dev/null
  [ "$alive" = 1 ] && ! grep -q "chores check-in" "$2"' _ "$BOSUN" "$WORK/events-loop.out"

echo "delegate (task types)"
L upsert 'review:acme/api#5' --kind review-request --title 'Add X' >/dev/null
L upsert 'slack:C7/1' --kind mention >/dev/null
check "--list shows every task type" bash -c 'for t in review fix-my-pr new-work investigate; do grep -q "^$t: " <<<"$1" || exit 1; done' _ "$("$BOSUN" delegate --list)"
task="$("$BOSUN" delegate review --id 'review:acme/api#5' --link https://github.com/acme/api/pull/5 --requester Priya --ask "review the PR" --details "my main point: add a backfill" --print)"
check "a review for a running window runs in a subagent at medium effort" contains "$(head -1 <<<"$task")" 'one Agent tool call with effort "medium"'
check "the task has the facts" contains "$task" "Ask: review the PR"
check "the task carries my details" contains "$task" "Details: my main point: add a backfill"
check "the task has its type's rules" contains "$task" "submit it as APPROVE"
check "the task ends with the shared rules" contains "$(tail -4 <<<"$task")" "AWS:"
task="$("$BOSUN" delegate investigate,new-work --id 'slack:C7/1' --ask "triage and fix" --print)"
check "types combine in order" bash -c 'grep -n "^Investigate" <<<"$1" | cut -d: -f1 | { read a; [ "$a" -lt "$(grep -n "^New work" <<<"$1" | cut -d: -f1)" ]; }' _ "$task"
check "a type rejects ids of another kind" fails_with "take review-request items" "$BOSUN" delegate review --id slack:C7/1 --ask x --print
check "ids must exist in the ledger" fails_with "upsert it first" "$BOSUN" delegate investigate --id slack:C7/404 --ask x --print
check "unknown types are rejected" fails_with "unknown task type" "$BOSUN" delegate nope --id slack:C7/1 --ask x --print
check "the ask must be one line" bash -c '! "$1" delegate investigate --id slack:C7/1 --ask "$(printf "a\nb")" --print 2>/dev/null' _ "$BOSUN"
check "a target is required" fails_with "say where" "$BOSUN" delegate investigate --id slack:C7/1 --ask x
check "--new goes through send" contains "$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" delegate investigate --id slack:C7/1 --ask x --new probe "$REPO_A" 2>&1)" "dry-run: would open window smoke:probe"
check "every ledger step in a task file is a valid transition" python3 - "$ROOT" <<'PY'
import re, sys
from pathlib import Path
sys.path.insert(0, f"{sys.argv[1]}/lib")
from ledger import KINDS, allowed_targets
for path in Path(sys.argv[1], "orchestrator", "tasks").glob("[!_]*.md"):
    text = path.read_text()
    declared = re.search(r"^kinds: (.+)$", text, re.M).group(1).split()
    kinds = list(KINDS) if declared == ["any"] else declared
    assert all(kind in KINDS for kind in kinds), (path.name, declared)
    steps = re.findall(r"bosun ledger transition \S+ (\S+)", text)
    steps += re.findall(r"bosun ledger report \S+ --state (\S+)", text)
    assert "ledger note" not in text, (path.name, "report outcomes with ledger report")
    for state in steps:
        for kind in kinds:
            states = KINDS[kind]["transitions"]
            assert any(state in allowed_targets(kind, s) for s in states), (path.name, kind, state)
PY
check "the task table in docs/orchestrator.md matches the task files, row for row" bash -c '
  table="$(sed -n "/^## Delegated tasks/,/^## /p" "$2" | grep "^| " | grep -v "^| Task |")"
  diff <(sort <<<"$1") <(sort <<<"$table")' _ "$("$BOSUN" delegate --list --markdown)" "$ROOT/docs/orchestrator.md"

echo "chores dry run (fake claude)"
cat >"$WORK/fake-claude" <<FAKE
#!/bin/bash
if [ "\$1" = "--version" ]; then echo "2.0.0 (Claude Code)"; exit 0; fi
printf '%s\n' "\$@" >"$WORK/fake-claude.args"
env | grep -E '^(BOSUN_|PATH=)' >"$WORK/fake-claude.env"
"$BOSUN" ledger upsert 'review:o/r#9' --kind review-request --title 'From dry run' >/dev/null
echo '{"result":"fake plan","total_cost_usd":0.01,"num_turns":1,"permission_denials":[{"tool_name":"Bash","tool_input":{"command":"gh pr merge x"}}]}'
FAKE
out="$("$BOSUN" chores morning --dry-run 2>&1)"
check "dry run prints the plan" contains "$out" "fake plan"
check "dry run reports scratch ledger changes" contains "$out" "+ review:o/r#9"
check "dry run reports blocked tool calls" contains "$out" "- Bash: gh pr merge x"
check "dry run uses a scratch state dir" grep -q "^BOSUN_STATE_DIR=$BOSUN_STATE_DIR/dry-runs/" "$WORK/fake-claude.env"
check "dry run turns on send dry-run" grep -qx "BOSUN_SEND_DRY_RUN=1" "$WORK/fake-claude.env"
check "dry run puts this checkout's bosun first on PATH" grep -q "^PATH=$ROOT/bin:" "$WORK/fake-claude.env"
check "dry run loads orchestrator settings and plugin" bash -c 'grep -qx -- --settings "$1" && grep -qx -- --plugin-dir "$1"' _ "$WORK/fake-claude.args"
check "dry run uses the orchestrator's model and effort" bash -c 'grep -qx opus "$1" && grep -q "\"effortLevel\":\"medium\"" "$1" && grep -q PreToolUse "$1"' _ "$WORK/fake-claude.args"
check "dry run invokes the morning chores" grep -qx "/bosun:chores morning" "$WORK/fake-claude.args"
check "dry run denies the settings' ask list by name" bash -c 'grep -qx -- --disallowedTools "$1" && grep -qx "mcp__plugin_slack_slack__slack_send_message" "$1" && grep -qx "Bash(git push:\*)" "$1"' _ "$WORK/fake-claude.args"
check "dry run leaves the real ledger alone" bash -c '! "$1" ledger show "review:o/r#9" >/dev/null 2>&1' _ "$BOSUN"
sleep 1
out="$("$BOSUN" chores morning --dry-run --continue 2>&1)"
check "--continue starts from the previous rehearsal" contains "$out" "seeded from $BOSUN_STATE_DIR/dry-runs/"
check "--continue sees earlier items as existing" contains "$out" "(0 change(s)"

echo "chores options"
check "--fresh and --continue conflict" fails_with "either --fresh or --continue" "$BOSUN" chores morning --dry-run --fresh --continue
check "--fresh needs --dry-run" fails_with "only apply to --dry-run" "$BOSUN" chores morning --fresh

echo "pr-threads (fake gh)"
cat >"$WORK/threads.json" <<'JSON'
{"data": {"repository": {"pullRequest": {"url": "https://github.com/acme/api/pull/5", "author": {"login": "me"},
  "reviews": {"nodes": [{"id": "R1", "url": "https://x/r1", "author": {"login": "rev"}, "state": "CHANGES_REQUESTED", "submittedAt": "2026-01-01T00:00:00Z", "body": ""}]},
  "reviewThreads": {"pageInfo": {"hasNextPage": false, "endCursor": null}, "nodes": [
  {"isResolved": false, "isOutdated": false, "path": "a.py", "line": 3,
   "first": {"totalCount": 1, "nodes": [{"author": {"login": "rev"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/1", "body": "please   fix\nthis", "pullRequestReview": {"id": "R1"}}]},
   "last": {"nodes": [{"author": {"login": "rev"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/1"}]}},
  {"isResolved": false, "isOutdated": false, "path": "c.py", "line": 7,
   "first": {"totalCount": 2, "nodes": [{"author": {"login": "rev"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/3", "body": "rename", "pullRequestReview": {"id": "R1"}}]},
   "last": {"nodes": [{"author": {"login": "me"}, "createdAt": "2026-01-02T00:00:00Z", "url": "https://x/4"}]}},
  {"isResolved": true, "isOutdated": false, "path": "b.py", "line": 9,
   "first": {"totalCount": 1, "nodes": [{"author": {"login": "old"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/2", "body": "done"}]},
   "last": {"nodes": [{"author": {"login": "old"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/2"}]}}
]}}}}}
JSON
cat >"$HOME/.local/bin/gh" <<FAKE
#!/bin/bash
cat "$WORK/threads.json"
FAKE
chmod +x "$HOME/.local/bin/gh"
out="$("$BOSUN" pr-threads https://github.com/acme/api/pull/5 2>&1)"
check "pr-threads counts unresolved threads" contains "$out" "unresolved threads: 2 (awaiting the author, me: 1)"
check "pr-threads shows who, where and when" contains "$out" "- rev on a.py:3 2026-01-01T00:00:00Z https://x/1"
check "pr-threads folds the comment onto one line" contains "$out" "please fix this"
check "pr-threads shows replies and the last commenter" contains "$out" "round 1; 1 reply, last: me 2026-01-02T00:00:00Z (answered by the author)"
check "pr-threads counts threads per review round" contains "$out" "1. CHANGES_REQUESTED by rev 2026-01-01T00:00:00Z: 2 threads, 1 awaiting the author"
check "pr-threads skips resolved threads" bash -c '! grep -q "old on b.py" <<<"$1"' _ "$out"
out="$("$BOSUN" pr-threads https://github.com/acme/api/pull/5 --awaiting-author 2>&1)"
check "pr-threads --awaiting-author exits 3 while threads await the author" test "$?" = 3
check "pr-threads --awaiting-author lists only those" bash -c 'grep -q "a.py:3" <<<"$1" && ! grep -q "c.py:7" <<<"$1"' _ "$out"
check "pr-threads --json parses" bash -c '"$1" pr-threads https://github.com/acme/api/pull/5 --json | jq -e ".awaiting_author == 1" >/dev/null' _ "$BOSUN"
check "pr-threads rejects other URLs" fails_with "not a GitHub PR URL" "$BOSUN" pr-threads https://example.com/x

echo "review-watch (fake gh)"
RW_ID='mypr:acme/api#5'
"$BOSUN" ledger upsert "$RW_ID" --kind my-pr --title "api fix" --url https://github.com/acme/api/pull/5 >/dev/null
"$BOSUN" ledger transition "$RW_ID" changes-requested --note "round 1" >/dev/null
"$BOSUN" ledger transition "$RW_ID" addressing --note "fixing" >/dev/null
"$BOSUN" ledger record-delegation "$RW_ID" --window api --task fix-my-pr >/dev/null
out="$("$BOSUN" review-watch --id "$RW_ID" 2>&1)"
check "review-watch ignores a review from before the hand-over" contains "$out" "no new reviews on 1 PR(s)"
# a second round after the hand-over
jq '.data.repository.pullRequest.reviews.nodes[0].submittedAt = "2999-01-01T00:00:00Z"' "$WORK/threads.json" >"$WORK/threads2.json" &&
  mv "$WORK/threads2.json" "$WORK/threads.json"
out="$("$BOSUN" review-watch --id "$RW_ID" --dry-run 2>&1)"
check "review-watch --dry-run shows the new review" contains "$out" "$RW_ID [addressing] (held by api): new review CHANGES_REQUESTED by rev"
check "... and records nothing" bash -c '! "$1" ledger show "$2" | grep -q review-arrived' _ "$BOSUN" "$RW_ID"
out="$("$BOSUN" review-watch --id "$RW_ID" 2>&1)"
check "review-watch names the next step" contains "$out" "bosun delegate --update '$RW_ID'"
check "review-watch records it on the item, with no state change" bash -c '"$1" ledger show "$2" | jq -e "(.history | map(select(.event == \"review-arrived\")) | length) == 1 and .state == \"addressing\"" >/dev/null' _ "$BOSUN" "$RW_ID"
"$BOSUN" review-watch --id "$RW_ID" >/dev/null 2>&1
check "review-watch records a review once" bash -c '"$1" ledger show "$2" | jq -e "(.history | map(select(.event == \"review-arrived\")) | length) == 1" >/dev/null' _ "$BOSUN" "$RW_ID"
check "review-watch --quiet prints nothing once it is recorded" test -z "$("$BOSUN" review-watch --id "$RW_ID" --quiet 2>&1)"
rm "$HOME/.local/bin/gh"

echo "impact (fake gh)"
cat >"$HOME/.local/bin/gh" <<'FAKE'
#!/bin/sh
case "$1 $2" in
  "api user") echo octo-me ;;
  "pr list") echo '[{"number":5,"title":"api fix","url":"https://github.com/acme/api/pull/5","createdAt":"2026-01-05T09:00:00Z","mergedAt":"2026-01-06T09:00:00Z","additions":3,"deletions":1}]' ;;
  "search prs") echo '[]' ;;
  *) echo "unexpected gh $*" >&2; exit 1 ;;
esac
FAKE
chmod +x "$HOME/.local/bin/gh"
check "impact --help prints its usage" contains "$("$BOSUN" impact --help 2>&1)" "Usage: bosun impact collect"
out="$(BOSUN_IMPACT_REPOS=acme/api "$BOSUN" impact collect --since 2025-12-01 --dry-run 2>&1)"
check "impact collect --dry-run counts what it found" contains "$out" "dry-run: nothing written; impact: 1 merged PRs, 0 reviewed PRs in 1 repos since 2025-12-01"
check "... and writes nothing" test ! -e "$BOSUN_STATE_DIR/impact/snapshot.json"
out="$(BOSUN_IMPACT_REPOS=acme/api BOSUN_IMPACT_OVERHEAD_FIXED_AT=2025-12-15 "$BOSUN" impact collect --since 2025-12-01 2>&1)"
check "impact collect writes the snapshot" bash -c 'jq -e ".prs[0].url == \"https://github.com/acme/api/pull/5\" and (.repos | index(\"acme/api\"))" "$1" >/dev/null' _ "$BOSUN_STATE_DIR/impact/snapshot.json"
check "... with the guardrails and the overhead fix date" bash -c 'jq -e "(.guardrails | type == \"array\") and .settings.overhead_fixed_at == \"2025-12-15\"" "$1" >/dev/null' _ "$BOSUN_STATE_DIR/impact/snapshot.json"
check "impact collect refuses a bad --since" fails_with "--since takes a date" "$BOSUN" impact collect --since yesterday
rm "$HOME/.local/bin/gh"

echo "setup steps (fake aws)"
SETUP_CFG="$WORK/setup-config"
mkdir -p "$SETUP_CFG"
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$SETUP_CFG/"
cat >"$HOME/.local/bin/aws" <<FAKE
#!/bin/bash
echo "\$*" >>"$WORK/aws.log"
case "\$*" in
  "sts get-caller-identity --profile good") exit 0 ;;
  "sts get-caller-identity --profile stale") [ -f "$WORK/aws.logged-in" ] ;;
  "sso login --profile stale") touch "$WORK/aws.logged-in" ;;
  "configure get sso_session --profile cached") echo acme ;;
  "sts get-caller-identity --profile cached") [ -f "\$HOME/.aws/cli/cache/role.json" ] || [ -f "$WORK/aws.cached-login" ] ;;
  "sso login --sso-session acme") touch "$WORK/aws.cached-login" ;;
  "configure get sso_session --profile books-"*) echo corp ;;
  "sts get-caller-identity --profile books-"*) [ -f "$WORK/aws.corp-login" ] ;;
  "sso login --sso-session corp")
    echo "open https://oidc.example.com/authorize?state=secret-state&code_challenge=secret"
    sleep "\${FAKE_SSO_WAIT:-0}"
    [ -n "\${FAKE_SSO_APPROVE:-}" ] && touch "$WORK/aws.corp-login"
    exit 0
    ;;
  *) exit 255 ;;
esac
FAKE
chmod +x "$HOME/.local/bin/aws"
cat >"$SETUP_CFG/setup.sh" <<'SH'
setup_steps() {
  ensure_aws_sso good
  ensure_aws_sso stale
  ensure_aws_sso broken
  ensure_aws_sso cached
  run_step "slow step" sleep 10
  run_step "quick step" true
}
SH
# "cached": its SSO session token expired, but 12-hour role credentials are still cached.
SSO_TOKEN="$HOME/.aws/sso/cache/$(printf '%s' acme | shasum -a 1 | cut -d' ' -f1).json"
mkdir -p "$HOME/.aws/sso/cache" "$HOME/.aws/cli/cache"
echo '{"expiresAt": "2000-01-01T00:00:00Z"}' >"$SSO_TOKEN"
echo '{"ProviderType": "sso", "Credentials": {}}' >"$HOME/.aws/cli/cache/role.json"
out="$(BOSUN_CONFIG_DIR="$SETUP_CFG" "$BOSUN" setup --dry-run 2>&1)"
check "setup --dry-run keeps cached role credentials" test -f "$HOME/.aws/cli/cache/role.json"
check "setup --dry-run only checks" bash -c 'grep -q "would run: aws sso login --profile stale" <<<"$1" && ! grep -q "sso login" "$2"' _ "$out" "$WORK/aws.log"
out="$(BOSUN_CONFIG_DIR="$SETUP_CFG" BOSUN_LOGIN_TIMEOUT=1 "$BOSUN" setup 2>&1)"
code=$?
check "setup counts failed steps and exits 1" bash -c '[ "$1" = 1 ] && grep -q "setup finished with 2 failed step(s)" <<<"$2"' _ "$code" "$out"
check "a valid profile needs no login" contains "$out" "aws profile good is valid, no login needed"
check "an expired profile logs in" contains "$out" "aws profile stale logged in"
check "a login that does not help is a failure" contains "$out" "aws profile broken still invalid after sso login"
check "an expired SSO session is not hidden by cached role credentials" bash -c 'grep -q "aws profile cached logged in" <<<"$1" && [ ! -f "$2" ]' _ "$out" "$HOME/.aws/cli/cache/role.json"
echo '{"expiresAt": "2999-01-01T00:00:00Z"}' >"$SSO_TOKEN"
echo '{"ProviderType": "sso", "Credentials": {}}' >"$HOME/.aws/cli/cache/role.json"
rm -f "$WORK/aws.cached-login"
out="$(BOSUN_CONFIG_DIR="$SETUP_CFG" BOSUN_LOGIN_TIMEOUT=1 "$BOSUN" setup 2>&1)"
check "a live SSO session keeps its cached role credentials" bash -c 'grep -q "aws profile cached is valid, no login needed" <<<"$1" && [ -f "$2" ]' _ "$out" "$HOME/.aws/cli/cache/role.json"
rm -f "$SSO_TOKEN" "$HOME/.aws/cli/cache/role.json"
check "a step that runs too long is stopped" contains "$out" "FAIL: slow step"
check "the other steps still run" contains "$out" "ok: quick step"
SSO_CFG="$WORK/sso-config"
mkdir -p "$SSO_CFG"
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$SSO_CFG/"
printf 'setup_steps() {\n  ensure_aws_sso books-dev\n  ensure_aws_sso books-prod\n}\n' >"$SSO_CFG/setup.sh"
SSO() { BOSUN_CONFIG_DIR="$SSO_CFG" BOSUN_LOGIN_TIMEOUT=5 "$BOSUN" setup "$@" 2>&1; }
logins() { grep -c "sso login --sso-session corp" "$WORK/aws.log"; }
: >"$WORK/aws.log"
out="$(SSO --dry-run)"
check "setup --dry-run plans one login per SSO session" bash -c 'grep -q "would run: aws sso login --sso-session corp" <<<"$1" && grep -q "books-prod is not valid; the login for SSO session corp above would cover it" <<<"$1"' _ "$out"
out="$(FAKE_SSO_APPROVE=1 SSO)"
check "one login covers every profile of its SSO session" bash -c 'grep -q "aws profile books-dev logged in" <<<"$1" && grep -q "aws profile books-prod is valid, no login needed" <<<"$1" && [ "$2" = 1 ]' _ "$out" "$(logins)"
check "... and the login's OAuth parameters stay out of the log" bash -c 'grep -q "authorize?<masked>" <<<"$1" && ! grep -q secret <<<"$1"' _ "$out"
rm -f "$WORK/aws.corp-login"
: >"$WORK/aws.log"
out="$(SSO)"
check "a session whose login failed is not logged in again for its next profile" bash -c '[ "$2" = 1 ] && grep -q "FAIL: aws profile books-prod: its SSO session corp still needs a login (see above); run: aws sso login --sso-session corp" <<<"$1"' _ "$out" "$(logins)"
check "... the failure says what to run" contains "$out" "no browser approval within 5s. Run: aws sso login --sso-session corp"
: >"$WORK/aws.log"
out="$(FAKE_SSO_WAIT=3 BOSUN_AWS_SSO_ALERT_AFTER=1 SSO)"
check "a login still waiting for its browser approval alerts you" bash -c 'grep -q "still waits for a browser approval after 1s; alerting you" <<<"$1" && grep -q "alert (notification only; BOSUN_ALERT_SLACK_USER is empty): AWS SSO login for SSO session corp waits for your approval" <<<"$1"' _ "$out"
out="$(FAKE_SSO_APPROVE=1 BOSUN_AWS_SSO_ALERT_AFTER=60 SSO)"
check "... but a login the browser approves at once does not" bash -c '! grep -q "alerting you" <<<"$1"' _ "$out"
rm -f "$WORK/aws.corp-login"
LOGIN_CFG="$WORK/login-config"
mkdir -p "$LOGIN_CFG"
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$LOGIN_CFG/"
echo 'setup_steps() { ensure_claude_login; }' >"$LOGIN_CFG/setup.sh"
LS() { BOSUN_CONFIG_DIR="$LOGIN_CFG" BOSUN_CLAUDE_BIN="$WORK/login-claude" "$BOSUN" setup "$@" 2>&1; }
rm -f "$WORK/login-probes.log"
check "setup --dry-run makes no request" bash -c 'grep -q "dry-run: would make one login check request" <<<"$1" && grep -q "ok: Claude Code login works" <<<"$1" && [ ! -e "$2" ]' _ "$(LS --dry-run)" "$WORK/login-probes.log"
check "ensure_claude_login passes when the login check request works" bash -c 'grep -q "ok: Claude Code login works: logged in (claude.ai)" <<<"$1"' _ "$(LS)"
check "... after one request" test "$(probes)" = 1
out="$(FAKE_PROBE=auth LS)"
check "a revoked login fails the step loudly, for the morning-failed alert" bash -c 'grep -q "FAIL: Claude Code login: the login check at .* got a login error (authentication_failed/401); run /login" <<<"$1" && grep -q "setup finished with 1 failed step" <<<"$1"' _ "$out"
out="$(FAKE_AUTH=out LS)"
check "logged out fails without a request" bash -c 'grep -q "FAIL: Claude Code login: claude auth status: not logged in" <<<"$1" && [ "$2" = 2 ]' _ "$out" "$(probes)"

echo "morning (dry run)"
out="$(BOSUN_CONFIG_DIR="$SETUP_CFG" BOSUN_NETWORK_PROBE_URL=file:///dev/null "$BOSUN" morning --dry-run --awake-minutes 5 2>&1)"
check "morning --dry-run would hold the keep-awake" contains "$out" "dry-run: would hold keep-awake for 5 minutes"
check "... but starts no caffeinate" test ! -f "$BOSUN_STATE_DIR/keep-awake-smoke.pid"
check "it waits for the network" contains "$out" "network is up"
check "it previews the setup steps" contains "$out" "aws profile good is valid, no login needed"
check "it lists the windows it would start, with model and effort" contains "$out" "alpha (session, opus/medium) -> $REPO_A"

echo "start modes"
"$BOSUN" start >/dev/null 2>&1
wait_for 10 has_windows "orch alpha beta gamma checkin "
check "drain mode refreshes without restarts" contains "$(BOSUN_ON_EXISTING=drain "$BOSUN" start 2>&1)" "mode: drain"
first_pane="$(tmux -L "$SOCKET" display -p -t "=smoke:alpha" '#{pane_pid}')"
out="$("$BOSUN" start --restart 2>&1)"
check "--restart starts the session afresh" bash -c 'grep -q "restarting existing session" <<<"$1" && [ "$(tmux -L "$2" display -p -t =smoke:alpha "#{pane_pid}")" != "$3" ]' _ "$out" "$SOCKET" "$first_pane"
check "an unknown mode is an error" fails_with "must be refresh, drain or restart" env BOSUN_ON_EXISTING=bogus "$BOSUN" start
"$BOSUN" stop >/dev/null 2>&1

echo "console (local server on a free port, fixture ledger)"
CONSOLE_STATE="$WORK/console-state"
mkdir -p "$CONSOLE_STATE"
cp "$ROOT/tests/fixtures/console/acme-ledger.json" "$CONSOLE_STATE/ledger.json"
# A fictional site for the Library (a fake secret in bosun.env, a site task type, files it must
# never list, a memory folder with a symlink out of it) and learning runs (one report outside).
CONSOLE_CONFIG="$WORK/console-config"
CONSOLE_MEMORY="$WORK/console-memory"
mkdir -p "$CONSOLE_CONFIG/tasks" "$CONSOLE_MEMORY" "$CONSOLE_STATE/learn/runs/20261020-183000" "$WORK/elsewhere"
printf 'BOSUN_SESSION_NAME="smoke"\nACME_API_TOKEN="fake-token-value-0000"\nBOSUN_ALERT_URL=https://hooks.example.com/services/T0/B0/X0\n' >"$CONSOLE_CONFIG/bosun.env"
printf -- '---\ntitle: Site review\nkinds: review-request\nmay: comment\nnever: approve\nfollowup: none\n---\nSite rules.\n' >"$CONSOLE_CONFIG/tasks/review.md"
echo "acme" >"$CONSOLE_CONFIG/forbidden-terms.txt"
echo "old" >"$CONSOLE_CONFIG/policy.md.bak-1"
echo "# Deploys" >"$CONSOLE_MEMORY/deploys.md"
echo "outside" >"$WORK/elsewhere/secret.md"
ln -s "$WORK/elsewhere/secret.md" "$CONSOLE_MEMORY/escape.md"
echo "# Learning report" >"$CONSOLE_STATE/learn/runs/20261020-183000/report.md"
echo "# Not ours" >"$WORK/elsewhere/report.md"
printf '{"runs": [{"started": "2026-10-20T18:30:00+00:00", "outcome": "ok", "findings": 0, "run_dir": "%s"}, {"started": "2026-10-19T18:30:00+00:00", "outcome": "ok", "run_dir": "%s"}]}' \
  "$CONSOLE_STATE/learn/runs/20261020-183000" "$WORK/elsewhere" >"$CONSOLE_STATE/learn/state.json"
# A scratch knowledge base for the Knowledge view (the fixture, with its own git history), and a
# restricted identifier the console must never show.
BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" "$BOSUN" kg import "$KG_FIXTURE" --apply --auto-confirm >/dev/null 2>&1
BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" "$BOSUN" kg identify person/dana slack U0000000077 >/dev/null 2>&1
BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" BOSUN_CONSOLE_MEMORY_DIRS="$CONSOLE_MEMORY" \
  "$BOSUN" console --port 0 >"$WORK/console.out" 2>"$WORK/console.err" &
CONSOLE_PID=$! # the cleanup trap stops it if a check below aborts the run
check "it prints its URL with a token" console_started "$WORK/console.out" "$WORK/console.err"
CONSOLE_URL="$(grep -o 'http://[^ ]*' "$WORK/console.out")"
CONSOLE_BASE="${CONSOLE_URL%%/\?*}"
CONSOLE_PORT="${CONSOLE_BASE##*:}"
CONSOLE_COOKIE="bosun_console_$CONSOLE_PORT=${CONSOLE_URL##*t=}"
http_status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
check "the shell page is served with the CSP header" bash -c '
  curl -sfi "$1/" | grep -qi "^content-security-policy: default-src .self.;"' _ "$CONSOLE_BASE"
check "the token becomes an HttpOnly, SameSite=Strict cookie, and the redirect drops it" bash -c '
  out="$(curl -si "$1")"
  grep -q "^HTTP/1.[01] 303" <<<"$out" && grep -qi "^location: /.$" <<<"$out" &&
    grep -qi "^set-cookie: .*; HttpOnly; SameSite=Strict" <<<"$out"' _ "$CONSOLE_URL"
check "/api/version is valid JSON with the item count" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/version" | jq -e ".ledger.items == 19 and .ledger.version == 1"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/items lists every item, and its open ones as ledger list --open does" bash -c '
  open="$(BOSUN_STATE_DIR="$3" "$4" ledger list --open | wc -l | tr -d " ")" &&
    curl -sf -H "Cookie: $2" "$1/api/items?group=all" | jq -e ".count == 19 and .total == 19" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/items?group=active" | jq -e --argjson open "$open" ".count == \$open"' \
  _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$CONSOLE_STATE" "$BOSUN"
check "/api/items filters and searches" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/items?kind=outreach&group=all&person=dana&q=credit+notes" |
    jq -e "[.rows[].id] == [\"out:task:2026-10-20/1/1\"]"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/items/<id> takes the URL-encoded id" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/items/mypr%3Aacme%2Fexporter%23102" |
    jq -e ".id == \"mypr:acme/exporter#102\" and (.links | map(.label) == [\"blocks\"])"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/needs-you lists the proposals, proposed outreach and mismatches" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/needs-you" |
    jq -e "(.proposals | length) == 2 and (.outreach | length) == 1 and (.mismatches | length) == 3"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/sessions and /api/health answer JSON with no session running" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/sessions" | jq -e "[.windows[] | select(.window)] == [] and .error == null" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/health" | jq -e ".watchdog.checks | length > 0"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
touch "$WORK/before-stats"
check "/api/stats has every metric for the period asked, 7 days without one" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/stats?days=30" | jq -e "
    .days == 30 and (.throughput.days | length) == 30 and .periods == [7, 30, 90] and
    (.prs | has(\"median_open_to_merge_seconds\")) and (.reviews | has(\"median_turnaround_seconds\")) and
    (.tasks | has(\"per_window\")) and (.outreach | has(\"nudges_per_reply\")) and
    (.people.recipients | has(\"top\")) and (.bosun.spend | length) == 30 and (.odd_times | type) == \"number\"" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/stats" | jq -e ".days == 7"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/stats refuses another period: 400" bash -c '
  for query in days=8 days=365 "days=7&days=30" days=week; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" -H "Cookie: $2" "$1/api/stats?$query")" = 400 ] || exit 1
  done' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/learn lists the runs, a report key only for the one under the learn folder, and the learn: items" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/learn" | jq -e "
    (.runs | length) == 2 and (.runs[0].report | test(\"^[0-9a-f]{16}\$\")) and .runs[1].report == null and
    ([.items[].id] == [\"learn:2026-10-20/1\"]) and (.items[0].pending == true)" >/dev/null &&
    key="$(curl -sf -H "Cookie: $2" "$1/api/learn" | jq -r ".runs[0].report")" &&
    curl -sf -H "Cookie: $2" "$1/api/learn/report/$key" | jq -e ".body == \"# Learning report\n\""' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/library lists the repo, the site overlay with its effective task type, and memory" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/library" | jq -e "
    [.groups[].entries[]] as \$all | (\$all | map(.name)) as \$names |
    (\$names | index(\"orchestrator/policy.md\")) and (\$names | index(\"bosun.env\")) and
    (\$names | index(\"deploys.md\")) and (\$names | index(\"escape.md\") | not) and
    (\$names | map(select(test(\"forbidden|bak\"))) == []) and
    (\$all | map(select(.name == \"tasks/review.md\"))[0].badge == \"effective\") and
    (\$all | map(select(.name == \"orchestrator/tasks/review.md\"))[0].badge == \"replaced by site\")"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/library/<key> masks the site config: no token, no webhook URL" bash -c '
  key="$(curl -sf -H "Cookie: $2" "$1/api/library" | jq -r "[.groups[].entries[] | select(.name == \"bosun.env\")][0].key")" &&
    body="$(curl -sf -H "Cookie: $2" "$1/api/library/$key")" &&
    jq -e ".redacted == true and (.body | contains(\"BOSUN_SESSION_NAME\"))" <<<"$body" >/dev/null &&
    ! grep -q "fake-token-value\|hooks.example.com" <<<"$body"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "files only by a known key: anything else is 404" bash -c '
  for path in /api/library/0123456789abcdef /api/library/..%2F..%2Fetc%2Fpasswd /api/library/bosun.env /api/learn/report/0123456789abcdef; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" -H "Cookie: $2" "$1$path")" = 404 ] || exit 1
  done' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "... and reading the stats wrote nothing in the state directory" bash -c '[ -z "$(find "$1" -newer "$2")" ]' _ "$CONSOLE_STATE" "$WORK/before-stats"
check "XSS strings come back as text, and a javascript: URL is no link" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/items/mypr%3Aacme%2Fexporter%23103" | jq -e ".title == \"Header fix <script>alert(1)</script>\"" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/items/slack%3AC0000000004%2F1700000004.000004" |
    jq -e ".url == null and .url_text == \"javascript:alert(document.cookie)\" and .anchors == []"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/knowledge: the four axis cards, the counts and today's review batch" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/knowledge" | jq -e "
    (.cards | map(.title)) == [\"People\", \"Process\", \"Product\", \"Systems\"] and
    .totals.entities == 15 and .totals.facts == 20 and .review.groups == 3"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
kg_key() { # <entity id>: its opaque key, from the console's search
  curl -sf -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/knowledge?q=${1#*/}" |
    jq -r --arg id "$1" '[.search.entities[] | select(.id == $id)][0].key'
}
check "an entity page by its opaque key: a process with its ordered flow and its graph" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/knowledge/entity/$3" | jq -e "
    .entity.name == \"Release process\" and ([.flow.steps[].step.name] == [\"Merge to main\", \"Smoke tests\", \"Gradual rollout\"]) and
    (.graph.nodes | length) > 1"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$(kg_key process/release)"
check "a person page says how to delete them, and no knowledge view shows a restricted identifier" bash -c '
  page="$(curl -sf -H "Cookie: $2" "$1/api/knowledge/entity/$3")" &&
    jq -e ".privacy | contains(\"bosun kg forget person/dana\")" <<<"$page" >/dev/null &&
    ! grep -q U0000000077 <<<"$page$(curl -sf -H "Cookie: $2" "$1/api/knowledge?q=dana")$(curl -sf -H "Cookie: $2" "$1/api/knowledge/review")"' \
  _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$(kg_key person/dana)"
check "/api/knowledge/briefing and /constellation: the headline, coverage, a placeholder for rediscovery, every entity" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/knowledge/briefing" | jq -e "
    .totals.facts == 20 and (.coverage | map(.card)) == [\"people\", \"process\", \"product\", \"systems\"] and
    (.captured.new | length) == 30 and .rediscovery.measured == false and (.connected | length) > 0" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/knowledge/constellation" | jq -e "
      (.nodes | length) == 15 and .more == 0 and ([.edges[] | select(.from >= 15 or .to >= 15)] == [])" >/dev/null &&
    for path in briefing constellation; do
      [ "$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Cookie: $2" "$1/api/knowledge/$path")" = 405 ] &&
        [ "$(curl -s -o /dev/null -w "%{http_code}" "$1/api/knowledge/$path")" = 401 ] || exit 1
    done' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "entities only by a known key: an id, a traversal or an unknown key is 404" bash -c '
  for path in 0123456789abcdef process/release process%2Frelease ..%2Fentities.jsonl; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" -H "Cookie: $2" "$1/api/knowledge/entity/$path")" = 404 ] || exit 1
  done' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
KG_REVIEW="$(curl -sf -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/knowledge/review")"
KG_FACT="$(jq -r '[.groups[].facts[] | select(.decidable)][0].id' <<<"$KG_REVIEW")"
KG_POST=(env BASE="$CONSOLE_BASE" COOKIE="$CONSOLE_COOKIE" CSRF="$(jq -r .csrf <<<"$KG_REVIEW")" OUT="$WORK/kg-post.json"
LOG="$CONSOLE_CONFIG/knowledge/facts.jsonl" KGDIR="$CONSOLE_CONFIG/knowledge" FACT="$KG_FACT"
OTHER="$(jq -r '[.groups[].facts[] | select(.decidable)][1].id' <<<"$KG_REVIEW")"
BODY="{\"action\": \"confirm\", \"facts\": [\"$KG_FACT\"]}")
# post [curl options...] inside these checks: POST the decision $BODY (confirm $FACT), print the status
KG_POST_FN='post() { curl -s -o "$OUT" -w "%{http_code}" -X POST --data "$BODY" "$@" "$BASE/api/knowledge/review"; }'
check "/api/knowledge/review gives the batch, lettered like kg batch, and a CSRF token" bash -c '
  jq -e "(.csrf | length) > 20 and .groups[0].facts[0].ref == \"1a\" and .summary.facts == 14" <<<"$1"' _ "$KG_REVIEW"
check "a GET with a forged decision in its query writes nothing" "${KG_POST[@]}" bash -c '
  before="$(cksum <"$LOG")" && curl -sf -H "Cookie: $COOKIE" "$BASE/api/knowledge/review?action=confirm&facts=$FACT" >/dev/null &&
    [ "$(cksum <"$LOG")" = "$before" ]'
check "a decision without the cookie, the CSRF token, this Origin or JSON is refused, and changes nothing" "${KG_POST[@]}" bash -c "$KG_POST_FN"'
  before="$(cksum <"$LOG")" json="Content-Type: application/json" token="X-Bosun-CSRF: $CSRF"
  [ "$(post -H "Origin: $BASE" -H "$json" -H "$token")" = 401 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "$json")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "$json" -H "X-Bosun-CSRF: guess")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "$json" -H "$token")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: http://evil.example.com" -H "$json" -H "$token")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "Sec-Fetch-Site: cross-site" -H "$json" -H "$token")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "Content-Type: text/plain" -H "$token")" = 415 ] &&
    BODY="{\"action\": \"confirm\", \"facts\": [\"f1\"]}" &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "$json" -H "$token")" = 409 ] &&
    [ "$(cksum <"$LOG")" = "$before" ]'
KG_STAMP="$(curl -sf -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/version" | jq -r .knowledge.stamp)"
check "a confirm round trip: confirmed, recorded as reviewed in the console, one commit" "${KG_POST[@]}" bash -c "$KG_POST_FN"'
  [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "Content-Type: application/json" -H "X-Bosun-CSRF: $CSRF")" = 200 ] &&
    jq -e ".confirmed == 1 and .left_proposed == 0" "$OUT" >/dev/null &&
    tail -1 "$LOG" | jq -e --arg f "$FACT" ".id == \$f and .event == \"confirmed\" and .set.reviewed_by.ref == \"console\"" >/dev/null &&
    curl -sf -H "Cookie: $COOKIE" "$BASE/api/knowledge/review" | jq -e --arg f "$FACT" "[.groups[].facts[] | select(.id == \$f and .decidable)] == []" >/dev/null &&
    [ "$(env -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE git -C "$KGDIR" log -1 --format=%s)" = "kg console review: confirm $FACT" ]'
check "/api/version carries the knowledge base's own stamp, changed by the decision" bash -c '
  [ "$(curl -sf -H "Cookie: $2" "$1/api/version" | jq -r .knowledge.stamp)" != "$3" ]' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$KG_STAMP"
check "defer leaves the fact proposed and writes nothing" "${KG_POST[@]}" bash -c "$KG_POST_FN"'
  before="$(cksum <"$LOG")" BODY="{\"action\": \"defer\", \"facts\": [\"$OTHER\"]}"
  [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "Content-Type: application/json" -H "X-Bosun-CSRF: $CSRF")" = 200 ] &&
    jq -e ".left_proposed == 1" "$OUT" >/dev/null && [ "$(cksum <"$LOG")" = "$before" ]'
check "an unknown item: 404" test "$(http_status -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/items/mypr%3Aacme%2Fexporter%23999")" = 404
check "a query it cannot use: 400" test "$(http_status -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/items?group=mine")" = 400
check "the ledger endpoints need the cookie: 401" bash -c '
  for path in /api/items /api/items/mypr%3Aacme%2Fexporter%23102 /api/needs-you /api/sessions /api/health /api/stats /api/learn /api/library; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" "$1$path")" = 401 ] || exit 1
  done' _ "$CONSOLE_BASE"
check "no token: 401" test "$(http_status "$CONSOLE_BASE/api/version")" = 401
check "a wrong Host: 403" test "$(http_status -H 'Host: evil.example.com' "$CONSOLE_BASE/")" = 403
check "POST: 405" test "$(http_status -X POST -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/version")" = 405
check "path traversal: 404" test "$(http_status --path-as-is "$CONSOLE_BASE/../lib/ledger.py")" = 404
check "a ledger write shows in /api/version" bash -c '
  BOSUN_STATE_DIR="$3" "$4" ledger upsert "mypr:acme/exporter#105" --kind my-pr --title "Credit notes" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/version" | jq -e ".ledger.items == 20"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$CONSOLE_STATE" "$BOSUN"
check "the log has the refusals, but no token, query, item id or ledger text" bash -c '
  grep -q "POST /api/version 405" "$1" && grep -q "GET /api/items/<id> 404" "$1" &&
    ! grep -qE "t=|acme|Credit|mine" "$1"' _ "$WORK/console.err"
check "a port in use is a clear error" fails_with "use --port N, or --port 0 for a free one" \
  env BOSUN_STATE_DIR="$CONSOLE_STATE" "$BOSUN" console --port "$CONSOLE_PORT"
check "it refuses a non-loopback address" fails_with "only listens on 127.0.0.1, not 0.0.0.0" \
  env BOSUN_STATE_DIR="$CONSOLE_STATE" "$BOSUN" console --port 0 --host 0.0.0.0
kill "$CONSOLE_PID" 2>/dev/null
check "a TERM stops it cleanly" wait_for 10 grep -q "bosun console: stopped" "$WORK/console.out"
check "... and it exits" wait_for 10 bash -c '! kill -0 "$1" 2>/dev/null' _ "$CONSOLE_PID"
CONSOLE_PID=""
# console_token_of_start [args...]: start the console on the same state, print its URL's token, stop it.
console_token_of_start() {
  local out="$WORK/console-again.out" pid
  BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" \
    "$BOSUN" console --port 0 "$@" >"$out" 2>"$WORK/console-again.err" &
  pid=$!
  console_started "$out" "$WORK/console-again.err" >/dev/null && grep -o '?t=[^ ]*' "$out" | cut -c4-
  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
}
FIRST_TOKEN="${CONSOLE_URL##*\?t=}"
check "a restart keeps the token, so an open tab stays signed in" test "$(console_token_of_start)" = "$FIRST_TOKEN"
check "... stored 0600 in a 0700 folder" bash -c '
  [ "$(stat -c %a "$1/console/token" 2>/dev/null || stat -f %Lp "$1/console/token")" = 600 ] &&
    [ "$(stat -c %a "$1/console" 2>/dev/null || stat -f %Lp "$1/console")" = 700 ]' _ "$CONSOLE_STATE"
check "--new-token rotates it" bash -c '[ -n "$1" ] && [ "$1" != "$2" ]' _ "$(console_token_of_start --new-token)" "$FIRST_TOKEN"

echo "install and uninstall (fake launchctl and pmset, test label)"
for tool in launchctl pmset; do
  printf '#!/bin/bash\necho "%s $*" >>"%s"\n' "$tool" "$WORK/system.log" >"$HOME/.local/bin/$tool"
  chmod +x "$HOME/.local/bin/$tool"
done
# install.sh refuses to run without Claude Code on PATH: the fake, not one this machine may have.
ln -sf "$WORK/fake-claude" "$HOME/.local/bin/claude"
export BOSUN_LAUNCHD_LABEL="com.bosun.smoke-test"
INSTALL_CFG="$WORK/install-config"
PLIST="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.plist"
# Never let a test touch the real launchd or pmset: only run if the fakes come first on PATH.
if [ "$(bash -c 'source "$1/lib/common.sh"; command -v launchctl; command -v pmset' _ "$ROOT" | tr '\n' ' ')" = "$HOME/.local/bin/launchctl $HOME/.local/bin/pmset " ]; then
  BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" --example generic >/dev/null 2>&1
  check "install copies the example config" bash -c '[ -f "$1/bosun.env" ] && [ -f "$1/sessions.yml" ] && [ -f "$1/setup.sh" ]' _ "$INSTALL_CFG"
  check "install links bosun into ~/.local/bin" test "$(readlink "$HOME/.local/bin/bosun")" = "$BOSUN"
  check "install writes a valid launchd job" plutil -lint -s "$PLIST"
  check "install loads it with launchctl" grep -q "launchctl bootstrap gui/$(id -u) $PLIST" "$WORK/system.log"
  check "install never changes pmset without --with-wake" bash -c '! grep -q "pmset repeat" "$1"' _ "$WORK/system.log"
  WATCHDOG_PLIST="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.watchdog.plist"
  check "install writes the watchdog agent" plutil -lint -s "$WATCHDOG_PLIST"
  check "... and loads it" grep -q "launchctl bootstrap gui/$(id -u) $WATCHDOG_PLIST" "$WORK/system.log"
  echo "# kept" >>"$INSTALL_CFG/bosun.env"
  BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" >/dev/null 2>&1
  check "a second install keeps the config" grep -qx "# kept" "$INSTALL_CFG/bosun.env"
  LEARN_PLIST="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.learn.plist"
  check "install writes no learning agent by default" test ! -e "$LEARN_PLIST"
  BOSUN_LEARN_ENABLED=1 BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" >/dev/null 2>&1
  check "with BOSUN_LEARN_ENABLED=1 it writes and loads one" bash -c 'plutil -lint -s "$1" && grep -q "launchctl bootstrap gui/$(id -u) $1" "$2"' _ "$LEARN_PLIST" "$WORK/system.log"
  check "doctor checks the learning agent once it is enabled" contains "$(BOSUN_LEARN_ENABLED=1 "$BOSUN" doctor 2>&1)" "ok    learning agent $BOSUN_LAUNCHD_LABEL.learn loaded"
  BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" >/dev/null 2>&1
  check "... and removes it again once disabled" test ! -e "$LEARN_PLIST"
  BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/uninstall.sh" >/dev/null 2>&1
  check "uninstall unloads and removes the job" bash -c 'grep -q "launchctl bootout gui/$(id -u)/$2" "$1" && [ ! -e "$3" ]' _ "$WORK/system.log" "$BOSUN_LAUNCHD_LABEL" "$PLIST"
  check "uninstall unloads and removes the watchdog agent" bash -c 'grep -q "launchctl bootout gui/$(id -u)/$2.watchdog" "$1" && [ ! -e "$3" ]' _ "$WORK/system.log" "$BOSUN_LAUNCHD_LABEL" "$WATCHDOG_PLIST"
  check "uninstall unloads the learning agent" grep -q "launchctl bootout gui/$(id -u)/$BOSUN_LAUNCHD_LABEL.learn" "$WORK/system.log"
  check "uninstall removes the link" test ! -L "$HOME/.local/bin/bosun"
  check "uninstall keeps the config without --purge" test -f "$INSTALL_CFG/bosun.env"

  echo "init (fake launchctl and pmset, test label)"
  export BOSUN_SKIP_PREFLIGHT=1 # what bosun needs has its own tests, on a made-up machine
  INIT_CFG="$WORK/init-config"
  INIT_REPO="$HOME/Development/init-api"
  git init -q "$INIT_REPO"
  git -C "$INIT_REPO" remote add origin git@github.com:acme/init-api.git
  # No wake in these answers: `sudo pmset` would find the real pmset, whatever PATH says here.
  INIT_ANSWERS="$WORK/init-answers.json"
  printf '{"repos": [{"dir": "%s"}], "repos_dir": "%s", "days": "MWF", "start": "09:00", "until": "18:00", "wake": false}' \
    "$INIT_REPO" "$HOME/Development" >"$INIT_ANSWERS"
  INIT_PLAN="$(BOSUN_CONFIG_DIR="$INIT_CFG" "$BOSUN" init 2>&1)"
  check "init on its own prints the plan" contains "$INIT_PLAN" "Nothing has changed yet"
  check "... and changes nothing" test ! -e "$INIT_CFG"
  check "init --dry-run changes nothing" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c '"$1" init --answers "$2" --dry-run >/dev/null && [ ! -e "$BOSUN_CONFIG_DIR" ]' _ "$BOSUN" "$INIT_ANSWERS"
  check "init --detect prints JSON" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c '"$1" init --detect | jq -e .home >/dev/null' _ "$BOSUN"
  check "init --print-answers prints JSON" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c '"$1" init --print-answers | jq -e .days >/dev/null' _ "$BOSUN"
  check "init refuses bad answers" fails_with "unknown answers" env BOSUN_CONFIG_DIR="$INIT_CFG" "$BOSUN" init --answers <(echo '{"typo": 1}')
  BOSUN_CONFIG_DIR="$INIT_CFG" "$BOSUN" init --answers "$INIT_ANSWERS" >"$WORK/init.out" 2>&1
  check "init writes the config" bash -c 'grep -q "BOSUN_DAYS=\"MWF\"" "$1/bosun.env" && grep -q "name: init-api" "$1/sessions.yml" && [ -f "$1/setup.sh" ]' _ "$INIT_CFG"
  check "what it writes loads as config" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c 'source "$1/lib/common.sh"; load_config; [ "$BOSUN_START_TIME" = 09:00 ] && [ "$BOSUN_EVENTS_ENABLED" = 1 ] && [ "$BOSUN_REPOS_DIR" = "$HOME/Development" ]' _ "$ROOT"
  check "... and as windows" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c 'source "$1/lib/common.sh"; load_config; window_specs | grep -q "^init-api	$HOME/Development/init-api	session"' _ "$ROOT"
  check "init trusts the folders" python3 "$ROOT/lib/claude_state.py" trusted "$INIT_REPO"
  check "init loads the jobs through install.sh, with valid plists" bash -c 'plutil -lint -s "$1" && plutil -lint -s "$2" && grep -q "launchctl bootstrap gui/$(id -u) $1" "$3"' _ "$PLIST" "$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.learn.plist" "$WORK/system.log"
  check "the learning agent is valid XML, not only what plutil lets through" python3 -c 'import plistlib, sys; plistlib.loads(open(sys.argv[1], "rb").read())' "$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.learn.plist"
  check "... at the time the answers imply" grep -q "<key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer>" "$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.learn.plist"
  BOSUN_CONFIG_DIR="$INIT_CFG" "$ROOT/uninstall.sh" >/dev/null 2>&1
  unset BOSUN_SKIP_PREFLIGHT

  echo "get.sh (a dry run: it changes nothing)"
  check "it is plain sh" sh -n "$ROOT/get.sh"
  check "it says how to use it" bash -c 'sh "$1/get.sh" --help | grep -q "Usage: get.sh"' _ "$ROOT"
  check "it refuses what it does not know" fails_with "unknown option" sh "$ROOT/get.sh" --typo
  check "a dry run says what it would do" bash -c 'BOSUN_DIR="$2" sh "$1/get.sh" --dry-run | grep -q "git clone"' _ "$ROOT" "$WORK/get-bosun"
  check "... and does none of it" test ! -e "$WORK/get-bosun"
  check "everything in it is in functions, so a download cut short does nothing" bash -c '[ "$(tail -1 "$1/get.sh")" = "main \"\$@\"" ]' _ "$ROOT"
else
  failed=$((failed + 1))
  echo "  FAIL  fake launchctl/pmset are not first on PATH; skipped install tests"
fi
unset BOSUN_LAUNCHD_LABEL

echo "the repository this ran from"
repo_unchanged() {
  local after
  after="$(repo_state)"
  [ "$after" = "$REPO_STATE_BEFORE" ] && return 0
  echo "!!! THE REAL REPOSITORY CHANGED DURING THE SMOKE RUN !!!" >&2
  echo "before: $REPO_STATE_BEFORE" >&2
  echo "after:  $after" >&2
  return 1
}
check "its HEAD and core.bare are what they were before the run" repo_unchanged

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
