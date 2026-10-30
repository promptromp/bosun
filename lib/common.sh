# shellcheck shell=bash
# Settings defined here are used by the scripts that source this file.
# shellcheck disable=SC2034
# Shared helpers for every bosun command. Source this file; do not execute it.

BOSUN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOSUN="$BOSUN_ROOT/bin/bosun"

# launchd starts jobs with a bare PATH; make the usual tool locations visible.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.rbenv/shims:$HOME/.rbenv/bin:/usr/bin:/bin:/usr/sbin:/sbin${PATH:+:$PATH}"
export LANG="${LANG:-en_US.UTF-8}"

# Defaults. Every value can be overridden in $BOSUN_CONFIG_DIR/bosun.env,
# and an BOSUN_* variable already set in the environment beats both.
set_harness_defaults() {
  BOSUN_HARNESS="claude"
  BOSUN_CODEX_BIN="codex"
  BOSUN_CODEX_FLAGS="--no-daemon --approve-for-me"
  BOSUN_CODEX_MODEL=""
}

set_defaults() {
  BOSUN_CONFIG_DIR="${BOSUN_CONFIG_DIR:-$HOME/.config/bosun}"
  BOSUN_SESSION_NAME="bosun"
  BOSUN_SESSIONS_FILE="$BOSUN_CONFIG_DIR/sessions.yml"
  BOSUN_SETUP_FILE="$BOSUN_CONFIG_DIR/setup.sh"
  BOSUN_STATE_DIR="$HOME/.local/state/bosun"
  BOSUN_LOG_DIR="$BOSUN_STATE_DIR/logs"
  # tmux server (socket name) for the session. A dedicated server keeps it apart from other tmux
  # sessions: windows inherit the environment of whatever started the server, and other tools can
  # kill a shared server. Empty: the default server. Attach with `bosun attach`.
  BOSUN_TMUX_SOCKET="bosun"

  BOSUN_DAYS="MTWRF"
  BOSUN_START_TIME="08:00"
  BOSUN_WAKE_DELAY_MINUTES="1"
  BOSUN_AWAKE_UNTIL="20:00"
  BOSUN_LAUNCHD_LABEL="com.bosun.morning"

  BOSUN_NETWORK_PROBE_URL="https://www.apple.com/library/test/success.html"
  BOSUN_NETWORK_TIMEOUT="120"
  BOSUN_LOGIN_TIMEOUT="300"
  # An AWS SSO login still waiting for its browser approval after this many seconds alerts you
  # (`bosun alert`); one the browser approves on its own finishes well before.
  BOSUN_AWS_SSO_ALERT_AFTER="60"
  BOSUN_START_TIMEOUT="30"
  # Let `bosun send --new|--start` trust a repo's folder for Claude Code (see `bosun trust`)
  # when the repo is inside BOSUN_REPO_MASK. 0: fail instead and ask you to trust it.
  BOSUN_TRUST_MASKED_REPOS="1"

  BOSUN_ON_EXISTING="refresh"
  BOSUN_ACTIVE_CLIENT_MINUTES="10"
  BOSUN_RESTART_MIN_IDLE_SECONDS="120"
  # With BOSUN_FRESH_CONVERSATIONS=1, refresh starts a fresh conversation for a repo window
  # that is idle, holds nothing in flight, and whose conversation grew past this many tokens (each
  # prompt re-reads all of it). The ledger and its memory carry what it needs.
  BOSUN_FRESH_CONVERSATIONS="0"
  BOSUN_FRESH_CONTEXT_TOKENS="200000"
  # Claude Code compacts a conversation by itself once its context passes this many tokens
  # (`claude --autocompact`, 100000 to 1000000, or auto), mid-task, keeping a summary. Every prompt
  # re-reads the whole conversation, so a window left to grow to 300k-900k tokens costs that much
  # per turn; fresh conversations above only start at the next refresh. A sessions.yml entry can set
  # its own `autocompact`. Empty or 0: Claude Code's own default. Read at launch (restart a window).
  BOSUN_AUTOCOMPACT_TOKENS="300000"
  BOSUN_ORCHESTRATOR_AUTOCOMPACT_TOKENS="300000"
  # After a compaction a SessionStart hook adds back, in at most this many characters, the ledger
  # items the window holds (the orchestrator: what waits for my approval). 0: no hook.
  BOSUN_COMPACT_CONTEXT="1"
  BOSUN_COMPACT_CONTEXT_MAX_CHARS="1500"
  BOSUN_CLAUDE_BIN="claude"
  set_harness_defaults
  BOSUN_CLAUDE_FLAGS="--permission-mode auto --chrome"
  # Model and effort for the repo sessions; sessions.yml entries can override them per window.
  # Model: an alias (opus, sonnet, haiku, fable) or a full model name. Effort: low, medium, high,
  # xhigh or max. Empty: Claude Code's own default, i.e. your last /model and /effort choice.
  BOSUN_MODEL="opus"
  BOSUN_EFFORT="medium"
  BOSUN_DRAIN_PROMPT="Bosun resumed or restarted this session (for example after sleep, a Claude Code update or a settings change). This is not a new day or a new task. If work in this session was cut off, resume it and finish. Otherwise reply with a one-line status and stay idle for my next command."

  BOSUN_REPOS_DIR="$HOME/Development"
  # Space-separated owner/repo glob patterns the orchestrator works on; a bare owner means all of
  # its repos. Empty: every repo. Example: "my-org/*" or "my-org other-org/one-repo".
  BOSUN_REPO_MASK=""

  # Orchestrator window (empty name disables it). Its directory must be a folder Claude Code
  # already trusts, because a trust prompt would block an unattended start.
  BOSUN_ORCHESTRATOR="orchestrator"
  BOSUN_ORCHESTRATOR_DIR=""
  BOSUN_ORCHESTRATOR_FLAGS="--chrome --permission-mode auto"
  BOSUN_ORCHESTRATOR_MODEL="opus"
  BOSUN_ORCHESTRATOR_EFFORT="medium"
  BOSUN_ORCHESTRATOR_SETTINGS=""
  BOSUN_CHECKIN_MINUTES="60"
  # 1: each day's new orchestrator conversation starts with /bosun:chores morning; 0: it starts
  # idle (launch without a prompt), and the chores run only when you or a check-in ask for them.
  BOSUN_ORCHESTRATOR_MORNING_CHORES="1"
  # A check-in goes out only when something is new since the last one (lib/checkin_gate.py), or
  # after this many hours without one, so the chores still run; 0 sends every check-in.
  BOSUN_CHECKIN_MAX_QUIET_HOURS="4"
  # When a Claude window (the orchestrator: check-ins pause meanwhile) has waited this long at a
  # permission prompt or question, the watchdog alerts you with `bosun alert`. 0: never.
  BOSUN_ALERT_WAITING_MINUTES="30"
  # Your Slack member id: alerts also DM you there (see `bosun alert --help`). Empty: macOS
  # notification only.
  BOSUN_ALERT_SLACK_USER=""
  BOSUN_ALERT_MODEL="haiku"
  # The watchdog (`bosun watchdog`, run every 5 minutes by its own launchd agent) alerts when
  # there is no morning run this many minutes after it was due (start time + wake delay).
  BOSUN_WATCHDOG_GRACE_MINUTES="15"
  # ... and when a ledger item has been delegated to a repo session for this many awake hours
  # (inside the awake window on scheduled days, so nights and weekends do not count). 0: never.
  BOSUN_WATCHDOG_DELEGATED_HOURS="8"
  # ... and when a background command has kept a Claude window in "shell" (turn over, the command
  # still running) for this many awake hours: is it still wanted? 0: never.
  BOSUN_LONG_SHELL_HOURS="4"
  BOSUN_MY_PR_WINDOW="48h"
  BOSUN_NUDGE_AFTER_BUSINESS_DAYS="1"
  BOSUN_MAX_NUDGES="2"
  BOSUN_MAX_REVIEW_NUDGES="0"
  BOSUN_REVIEW_ESCALATE_AFTER_NUDGES="3"
  set_feature_defaults

  BOSUN_AWS_BIN="aws"
  BOSUN_LAUNCH_DRY_RUN="0"
  BOSUN_SEND_DRY_RUN="0"
  BOSUN_SETUP_DRY_RUN="0"
}

set_feature_defaults() {
  set_learn_defaults
  set_ticket_defaults
  set_event_defaults
  set_slack_defaults
  set_console_defaults
  set_impact_defaults
  set_knowledge_defaults
  set_approval_defaults
  set_reviewer_bot_defaults
  set_delivery_defaults
  set_signal_defaults
}

# Claude Code's own signals, recorded by async hooks (`bosun hook signal`, lib/signals.py;
# design: docs/design/signals.md): what a waiting window was asked, what a finished turn left
# running, a turn that failed on a usage limit. The watchdog and events read them next to the
# session records and transcripts. 0: no hooks, and readers ignore what was recorded. Read at
# launch (restart a window). Each window's file keeps its newest half past BOSUN_SIGNALS_MAX_KB.
set_signal_defaults() {
  BOSUN_HOOK_SIGNALS="1"
  BOSUN_SIGNALS_MAX_KB="256"
}

# Getting prompts to Claude windows: pasting safely, and the orchestrator being there to read them.
set_delivery_defaults() {
  # `bosun send` pastes only into a window nobody typed in (a client showing it) for this many
  # minutes and whose Claude input line is empty, waiting up to BOSUN_SEND_WAIT_SECONDS
  # (checking every BOSUN_SEND_RETRY_SECONDS), then fails so the caller can resend. After the
  # Enter it waits up to BOSUN_SEND_CONFIRM_SECONDS for Claude to take the prompt (the line
  # empties, no "Unknown command"), else it fails. 0 minutes: never wait for typing.
  BOSUN_SEND_TYPING_MINUTES="2"
  BOSUN_SEND_WAIT_SECONDS="90"
  BOSUN_SEND_RETRY_SECONDS="10"
  BOSUN_SEND_CONFIRM_SECONDS="5"
  # The watchdog also alerts when the orchestrator's Claude has not run for this many awake minutes
  # after the morning run (0: never). With BOSUN_WATCHDOG_ORCHESTRATOR_RESTART=1 it then
  # relaunches it once per outage (`bosun refresh --unless-active <orchestrator>`).
  BOSUN_WATCHDOG_ORCHESTRATOR_DOWN_MINUTES="15"
  BOSUN_WATCHDOG_ORCHESTRATOR_RESTART="1"
}

set_learn_defaults() {
  # Learning (`bosun learn`): once a day at BOSUN_LEARN_AT on BOSUN_DAYS, a headless
  # Claude reads a digest of the sessions, the ledger and the logs since the last run (at most
  # BOSUN_LEARN_HOURS back) and proposes memory notes, rules and setup changes for your
  # approval. BOSUN_LEARN_ENABLED=1 makes install.sh load its launchd agent and the watchdog
  # check it; `bosun learn` by hand works either way.
  BOSUN_LEARN_ENABLED="0"
  BOSUN_LEARN_AT="18:30"
  BOSUN_LEARN_HOURS="24"
  BOSUN_LEARN_MODEL="opus"
  BOSUN_LEARN_EFFORT="medium"
  BOSUN_LEARN_MAX_BUDGET_USD="2"
  BOSUN_LEARN_MAX_PROPOSALS="5"
  BOSUN_LEARN_MAX_EVENTS="250"
  BOSUN_LEARN_MAX_DIGEST_KB="400"
  BOSUN_LEARN_TIMEOUT_MINUTES="30"
  # Claude session ids (space-separated) learn never reads, e.g. a private conversation.
  BOSUN_LEARN_EXCLUDE_SESSIONS=""
}

set_ticket_defaults() {
  # External tickets (`bosun ledger ticket-sync`): your tracker's status names that the
  # built-in mapping does not know, as '<system>[/<project>]:<status name>=<category>' entries
  # separated by ';' (categories: todo, in-progress, in-review, done, cancelled), e.g.
  # "jira:Ready for Review=in-review; jira/EXP:On Hold=todo". And when a ticket of open work
  # counts as read too long ago (hours).
  BOSUN_TICKET_STATUS_MAP=""
  BOSUN_TICKET_STALE_HOURS="24"
  # The tracker projects you own (Jira project keys like EXP, GitHub owner/repo), separated by
  # spaces or commas. A ticket in another project is another team's to move: `ledger tickets
  # --mismatch` shows its mismatches as FYI and the console does not count them as needing you.
  # Empty: every project counts as yours.
  BOSUN_TICKET_OWNED_PROJECTS=""
}

set_event_defaults() {
  # Events (docs/design/events.md). 1: repo sessions launched from then on get a UserPromptSubmit
  # hook that records what you type there (`ledger said`); the ledger queues events (reports, what
  # you said, outreach drafts, follow-up tasks, windows waiting at a prompt); and the check-in loop
  # (restart it after changing this) pushes them to the orchestrator as one summary. A repo window
  # waiting WAITING seconds at a prompt is an event. TRANSPORT: send (tmux paste).
  BOSUN_EVENTS_ENABLED="0"
  BOSUN_EVENTS_WAITING_SECONDS="120"
  BOSUN_EVENTS_TRANSPORT="send"
  # How often the loop runs the dispatcher (seconds; a shell check, no Claude call).
  BOSUN_EVENTS_INTERVAL_SECONDS="30"
  # When pending events go (lib/event_lanes.py). Each delivery is one orchestrator turn that re-reads
  # its whole conversation, so longer waits batch more events into one. Routine lane (reports, what
  # you said, outreach, tasks, reviews): once its newest event is DEBOUNCE seconds old, or its oldest
  # MAX_WAIT. Fast lane, the event types in FAST_KINDS (space- or comma-separated; empty: none): the
  # FAST_ timings. While nobody typed in tmux for AWAY_MINUTES (0: never away), the routine lane uses
  # the AWAY_ timings. A delivery takes every pending event, whichever lane made it due.
  BOSUN_EVENTS_DEBOUNCE_SECONDS="45"
  BOSUN_EVENTS_MAX_WAIT_SECONDS="300"
  BOSUN_EVENTS_FAST_KINDS="slack waiting"
  BOSUN_EVENTS_FAST_DEBOUNCE_SECONDS="45"
  BOSUN_EVENTS_FAST_MAX_WAIT_SECONDS="300"
  BOSUN_EVENTS_AWAY_MINUTES="0"
  BOSUN_EVENTS_AWAY_DEBOUNCE_SECONDS="1200"
  BOSUN_EVENTS_AWAY_MAX_WAIT_SECONDS="3600"
  # Only to an orchestrator whose turn ended MIN_IDLE seconds ago, while you are not typing, and at
  # most MAX_PER_DAY deliveries a day (the next check-in covers the rest).
  BOSUN_EVENTS_MIN_IDLE_SECONDS="60"
  BOSUN_EVENTS_MAX_PER_DAY="30"
  # The summary (lib/event_summary.py): at most MAX_LINES events, then a pointer to the ledger; each
  # line shows up to INLINE_CHARS (at least 80) of what was recorded (a report's summary, open
  # points, results and CI; what you typed; the outreach or task asked for), so the orchestrator
  # can act without reading the item first. A longer one is cut, with the command for the rest.
  BOSUN_EVENTS_MAX_LINES="10"
  BOSUN_EVENTS_INLINE_CHARS="600"
  # 1: with check-ins off (BOSUN_CHECKIN_MINUTES=0), the checkin window still runs its loop for
  # events, the Slack and review watchers and waiting alerts, without ever sending a check-in.
  BOSUN_EVENTS_WITHOUT_CHECKINS="0"
  # With events on, the check-in loop also runs `bosun review-watch` every MINUTES (0: never):
  # a new review on one of your PRs after its last hand-over (or report) is recorded on the item and
  # becomes an event, so it reaches the orchestrator while the PR's window may still be working.
  BOSUN_REVIEW_WATCH_MINUTES="15"
}

set_slack_defaults() {
  # The chores' Slack scan (`bosun ledger mark-run` prints where it starts): a check-in scans
  # from the last run; a morning from the last LOOKBACK hours, or from the last run if that was
  # longer ago (a failed morning, days away), but never more than MAX_LOOKBACK hours back.
  BOSUN_SLACK_LOOKBACK_HOURS="24"
  BOSUN_SLACK_MAX_LOOKBACK_HOURS="96"
  # The Slack watcher (`bosun slack-watch`; needs BOSUN_EVENTS_ENABLED=1). 1: the check-in
  # loop runs it every MINUTES inside the awake window: one headless Claude (MODEL) that can only
  # read Slack finds new mentions, DMs and replies in your threads, and each new one becomes an
  # event for the orchestrator. At most MAX_USD_PER_CALL a call and MAX_USD_PER_DAY a day.
  # MCP_CONFIG: an MCP config file with only your Slack server; empty: the Slack plugin's.
  # QUIET_AUTHORS: comma-separated display names (bots posting status updates) never pushed as
  # events; the check-in's Slack scan still reads their messages.
  BOSUN_SLACK_WATCH_ENABLED="0"
  BOSUN_SLACK_WATCH_MINUTES="15"
  BOSUN_SLACK_WATCH_MODEL="haiku"
  BOSUN_SLACK_WATCH_MAX_USD_PER_CALL="0.25"
  BOSUN_SLACK_WATCH_MAX_USD_PER_DAY="1.5"
  BOSUN_SLACK_WATCH_MCP_CONFIG=""
  BOSUN_SLACK_WATCH_QUIET_AUTHORS=""
}

set_approval_defaults() {
  # Approvals (docs/design/approvals.md). 1: the orchestrator, from its next start, gets two more
  # hooks: an outward call (Slack, Jira/Confluence writes, gh pr merge/ready/edit/comment/review/
  # close, gh api, git push) that carries out a proposal you approved in chat (`ledger approve
  # --quote`) runs without a second Claude Code prompt; one that is neither approved nor covered by
  # a standing class is denied at once with how to propose it, instead of waiting at a prompt.
  # DENY_UNAPPROVED=0: such calls get the old prompt instead (takes effect at once).
  BOSUN_APPROVALS_ENABLED="0"
  BOSUN_APPROVALS_DENY_UNAPPROVED="1"
  # More outward commands to gate the same way, as command prefixes separated by ',' (e.g. "glab mr
  # merge, my-deploy"); add a matching `ask` rule to the orchestrator settings as the backstop.
  BOSUN_APPROVALS_GATED_COMMANDS=""
  # Standing approvals: classes allowed without a chat approval, space-separated (empty: none):
  # pr-review-traffic (Slack review requests, re-review bumps and merged notes for your own PRs,
  # in SLACK_CHANNELS only, mentioning only SLACK_MENTIONS) and pr-housekeeping (gh pr ready, and
  # gh pr edit --title/--body/--add-reviewer with GH_REVIEWERS, on your own ready PRs). Channel
  # and Slack member ids, GitHub logins: space-separated. DM channels are never standing.
  BOSUN_STANDING_CLASSES=""
  BOSUN_STANDING_SLACK_CHANNELS=""
  BOSUN_STANDING_SLACK_MENTIONS=""
  BOSUN_STANDING_GH_REVIEWERS=""
  # pr-housekeeping also allows `gh pr comment <url> --body '<text>'` when <text> is exactly one of
  # these comment bodies, separated by '|' (empty: none), e.g. the command that asks an automated
  # reviewer for another round. At most MAX_PER_PR such comments per body and PR go out under the
  # standing approval (0: no cap); past that, each needs your word. Like adding a reviewer, it is
  # a re-review request, so BOSUN_APPROVALS_CHECK_REVIEW_THREADS checks the live PR first.
  BOSUN_STANDING_GH_COMMENTS=""
  BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR="3"
  # 1: before an approved (or standing) call that asks for a re-review on one of your PRs (outreach
  # with purpose review, or gh pr edit --add-reviewer on a my-pr item) goes out, the guard reads the
  # live PR (`bosun pr-threads`) and denies the call while a review thread awaits the author or
  # a review is newer than the proposed message. If GitHub cannot be read, Claude Code asks you.
  BOSUN_APPROVALS_CHECK_REVIEW_THREADS="1"
}

# Automated reviewers (lib/pr_threads.py): GitHub logins, space-separated (empty: none), of bots
# whose PR reviews count like a person's in `bosun pr-threads`, `review-watch` and the guard's
# re-review check: their unresolved threads await the PR author and their reviews are rounds. Every
# other bot (GraphQL type Bot, or a login ending in [bot]) still never counts. Their latest verdict
# is read from the review body with VERDICT_PATTERN (a multi-line regular expression; its first
# group is the verdict) and shown; a reviewer bot's review is never an approval.
set_reviewer_bot_defaults() {
  BOSUN_REVIEWER_BOTS=""
  BOSUN_REVIEWER_BOT_VERDICT_PATTERN='^#+\s*Verdict:\s*(.+?)\s*$'
}

set_knowledge_defaults() {
  # `bosun kg` (docs/design/knowledge.md, in $BOSUN_CONFIG_DIR/knowledge/): one commit per
  # change in its own history (1; skipped if git is missing).
  BOSUN_KNOWLEDGE_GIT="1"
  # A review batch holds at most this many groups, facts in all and facts a group (at most 26).
  BOSUN_KNOWLEDGE_BATCH_CAP="10"
  BOSUN_KNOWLEDGE_BATCH_FACTS="40"
  BOSUN_KNOWLEDGE_GROUP_FACTS="8"
  # `kg lint` reports confirmed facts not observed for this many days as stale.
  BOSUN_KNOWLEDGE_STALE_DAYS="180"
}

set_console_defaults() {
  # `bosun console` (docs/design/console.md): its port on 127.0.0.1 (--port overrides it).
  BOSUN_CONSOLE_PORT="8765"
  # 1: keep the console's access token across restarts ($BOSUN_STATE_DIR/console/token, 0600),
  # so an open tab stays signed in; `bosun console --new-token` rotates it. 0: new each start.
  BOSUN_CONSOLE_PERSIST_TOKEN="1"
  # Claude Code memory folders the console's Library may show (read-only, .md files only),
  # separated by ':'. Empty: none.
  BOSUN_CONSOLE_MEMORY_DIRS=""
}

set_impact_defaults() {
  # `bosun impact collect` and the console's Impact view (docs/design/impact.md): the repos
  # (owner/name, comma-separated; empty: those of the ledger's mypr: and review: items), the day
  # bosun started (YYYY-MM-DD; empty: the ledger's first item) and the baseline's first day
  # (empty: 13 weeks before the start).
  BOSUN_IMPACT_REPOS=""
  BOSUN_IMPACT_START=""
  BOSUN_IMPACT_BASELINE_FROM=""
  # Minutes of my time a prompt I type stands for (reading, answering): my touch time estimate.
  BOSUN_IMPACT_PROMPT_MINUTES="2"
  # 1: the morning run collects the snapshot each day.
  BOSUN_IMPACT_COLLECT="0"
  # The Overhead panel: prompt openings ('|'-separated) a site sends on a schedule, counted as
  # overhead, and the day overhead fixes went in (YYYY-MM-DD; the panel compares the week on each
  # side). The watchdog warns when a window's conversation re-reads more than
  # BOSUN_CONTEXT_WARN_TOKENS a turn for over a day (0: off), and when overhead is more than
  # BOSUN_OVERHEAD_WARN_SHARE of the last 7 days' cost (0: off).
  BOSUN_IMPACT_OVERHEAD_PREFIXES=""
  BOSUN_IMPACT_OVERHEAD_FIXED_AT=""
  BOSUN_CONTEXT_WARN_TOKENS="300000"
  BOSUN_OVERHEAD_WARN_SHARE="0.5"
  # Cost per task: how many hours after a window's last ledger event (hand-over, update, report,
  # `ledger working`, something I said there), or before its next report, a turn there still counts
  # for that item; and how many of the most expensive items the panel lists.
  BOSUN_IMPACT_ATTRIBUTION_HOURS="8"
  BOSUN_IMPACT_TASKS_TOP="15"
}

# Settings come from the defaults, then bosun.env, then BOSUN_* variables in the environment.
# The result is not exported: child commands load the config themselves, so a long-running process
# (a Claude session, the tmux server) never carries a frozen copy that would mask later edits to
# bosun.env. Only real environment overrides stay exported, and pass on to children.
load_config() {
  local name i=0
  local -a override_names=() override_values=()
  for name in $(compgen -e | grep '^BOSUN_' | grep -vx BOSUN_COMMAND); do
    override_names+=("$name")
    override_values+=("${!name}")
  done
  set_defaults
  if [ -f "$BOSUN_CONFIG_DIR/bosun.env" ]; then
    # shellcheck disable=SC1091
    source "$BOSUN_CONFIG_DIR/bosun.env"
  fi
  while [ "$i" -lt ${#override_names[@]} ]; do
    printf -v "${override_names[$i]}" '%s' "${override_values[$i]}"
    i=$((i + 1))
  done
  : "${BOSUN_ORCHESTRATOR_DIR:=$BOSUN_REPOS_DIR}"
  : "${BOSUN_ORCHESTRATOR_SETTINGS:=$BOSUN_ROOT/orchestrator/settings.json}"
  mkdir -p "$BOSUN_STATE_DIR" "$BOSUN_LOG_DIR"
}

# Whether <name> is an override from the environment (exported), rather than a default or a value
# from bosun.env.
is_env_override() { declare -p "$1" 2>/dev/null | grep -q '^declare -[a-zA-Z]*x'; }

log() { printf '%s [%s] %s\n' "$(date '+%F %T')" "${BOSUN_COMMAND:-bosun}" "$*"; }
die() {
  log "ERROR: $*" >&2
  exit 1
}

# Print the calling script's leading comment block (its `# Usage:` header) for --help.
usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; }

# A usage error: exit 2, pointing at --help.
die_usage() {
  log "ERROR: $* (see --help)" >&2
  exit 2
}

notify() {
  osascript -e "display notification \"${2//\"/\\\"}\" with title \"${1//\"/\\\"}\"" >/dev/null 2>&1 || true
}

tmux_cmd() {
  # -u: without a UTF-8 locale (LC_ALL=C), tmux prints the tabs in its -F formats as "_", and every
  # window would look missing.
  if [ -n "$BOSUN_TMUX_SOCKET" ]; then
    tmux -u -L "$BOSUN_TMUX_SOCKET" "$@"
  else
    tmux -u "$@"
  fi
}

# The orchestrator's policy: orchestrator/policy.md, then the site's $BOSUN_CONFIG_DIR/policy.md
# if there is one (additions for this machine or team, without forking the repo).
orchestrator_policy() {
  cat "$BOSUN_ROOT/orchestrator/policy.md"
  if [ -f "$BOSUN_CONFIG_DIR/policy.md" ]; then
    printf '\n\n# Site policy (%s)\n\n' "$BOSUN_CONFIG_DIR/policy.md"
    cat "$BOSUN_CONFIG_DIR/policy.md"
  fi
}

# The keep-awake (caffeinate) pid file that `morning` writes and `stop` releases.
keep_awake_pidfile() { echo "$BOSUN_STATE_DIR/keep-awake-$BOSUN_SESSION_NAME.pid"; }

# When you stopped the session (`stop` writes it, `start` removes it). The watchdog does not
# expect the session on a day you stopped it.
stop_marker() { echo "$BOSUN_STATE_DIR/stopped-$BOSUN_SESSION_NAME"; }

session_exists() { tmux_cmd has-session -t "=$BOSUN_SESSION_NAME" 2>/dev/null; }

# Command for attaching to the session, for messages.
attach_hint() {
  if [ -n "$BOSUN_TMUX_SOCKET" ]; then
    echo "tmux -L $BOSUN_TMUX_SOCKET attach -t $BOSUN_SESSION_NAME"
  else
    echo "tmux attach -t $BOSUN_SESSION_NAME"
  fi
}

# Environment overrides a launched window needs, since tmux panes get the tmux server's
# environment, not the caller's.
BOSUN_PASSTHROUGH_VARS="BOSUN_CONFIG_DIR BOSUN_SESSION_NAME BOSUN_TMUX_SOCKET BOSUN_LAUNCH_DRY_RUN"

# Sets launch_cmd to the tmux arguments (for new-window or respawn-pane) that run
# `bosun launch <window> [--prompt-file <file>]` as the pane's own command. The command is
# never typed into a shell: a typed line longer than the terminal's input limit (1024 bytes on
# macOS) is cut off and its Enter dropped, and a shell that has been running for a while carries
# whatever environment it had. tmux refuses commands over about 16 KB, hence the prompt file.
# It runs in an interactive login shell, like a window you open by hand, so Claude gets your usual
# environment, and that shell stays in the pane after Claude exits. Without a prompt, launch sends
# the drain prompt. That launch shell never shows a prompt, so helpers your startup files fork off
# would outlive it; launch stops them (reap_shell_helpers).
build_launch_command() { # <window> [prompt] [kind] [model] [effort] [quiet]
  local window="$1" prompt="${2:-}" prompt_dir prompt_file shell var
  local launch=("$BOSUN" launch)
  [ -n "${4:-}" ] && launch+=(--model "$4")
  [ -n "${5:-}" ] && launch+=(--effort "$5")
  # quiet: resume without the drain prompt (launch --no-prompt)
  [ "${6:-}" = "quiet" ] && launch+=(--no-prompt)
  launch+=("$window")
  # The checkin window runs the check-in loop instead of Claude.
  [ "${3:-}" = "checkin" ] && launch=("$BOSUN" checkin --loop)
  if [ -n "$prompt" ]; then
    prompt_dir="$BOSUN_STATE_DIR/prompts"
    mkdir -p "$prompt_dir"
    prompt_file="$(mktemp "$prompt_dir/$window.XXXXXX")" || die "cannot create a prompt file in $prompt_dir"
    printf '%s' "$prompt" >"$prompt_file"
    launch+=(--prompt-file "$prompt_file")
  fi
  shell="$(tmux_cmd show-options -gv default-shell 2>/dev/null)" # fails before the server runs
  [ -n "$shell" ] || shell="$(login_shell)"
  launch_cmd=()
  for var in $BOSUN_PASSTHROUGH_VARS; do
    if is_env_override "$var"; then launch_cmd+=(-e "$var=${!var}"); fi
  done
  launch_cmd+=(-- "$shell" -lic "$(printf '%q ' "${launch[@]}"); exec $(printf '%q' "$shell") -l")
}

# The user's login shell, as tmux would pick it: $SHELL, else the user record.
login_shell() {
  local shell="${SHELL:-}"
  [ -n "$shell" ] || shell="$(dscl . -read "/Users/$(id -un)" UserShell 2>/dev/null | awk '{ print $2 }')"
  echo "${shell:-/bin/zsh}"
}

RELAUNCH_SETTLE_SECONDS="${BOSUN_RELAUNCH_SETTLE_SECONDS:-8}" # the variable is for tests

# Replace the process in a window's pane with a fresh `bosun launch`, in <dir> (default: the
# pane's current directory). Refuses split windows and panes that run anything besides their
# shell, so it never kills your work.
relaunch_in_window() { # <window_id> <window> [prompt] [dir] [quiet]
  local window_id="$1" panes pane_id pane_pid children workdir
  panes="$(tmux_cmd list-panes -t "$window_id" -F '#{pane_id} #{pane_pid}')" || return 1
  if [ "$(grep -c . <<<"$panes")" -ne 1 ]; then
    log "$2: the window is split into several panes; not replacing it"
    return 1
  fi
  read -r pane_id pane_pid <<<"$panes"
  # Right after Claude exits, the pane's shell restarts as a login shell and briefly runs helpers
  # (prompt and git-status workers); give it a few seconds to settle before calling it busy.
  local deadline=$((SECONDS + RELAUNCH_SETTLE_SECONDS))
  while :; do
    children="$(descendants "$pane_pid")" || {
      log "$2: could not list the pane's processes; not replacing it"
      return 1
    }
    [ -z "$children" ] && break
    if [ "$SECONDS" -ge "$deadline" ]; then
      log "$2: the window is running another command; not replacing it"
      return 1
    fi
    sleep 0.5
  done
  workdir="${4:-$(tmux_cmd display-message -p -t "$pane_id" '#{pane_current_path}')}"
  build_launch_command "$2" "${3:-}" "" "" "" "${5:-}"
  log_reaped "$2" "$(reap_shell_helpers "$pane_pid")"
  tmux_cmd respawn-pane -k -t "$pane_id" -c "$workdir" "${launch_cmd[@]}"
}

# lib/orphans.py: the helpers a pane's shell forked off while reading your startup files, which
# only that shell's first prompt would stop (see its docstring).
orphans() { python3 "$BOSUN_ROOT/lib/orphans.py" "$@"; }

# Sets wrapper_args to lib/orphans.py's --wrapper arguments: how the command line of a pane that
# build_launch_command made starts, for each shell it may have used.
set_wrapper_args() {
  local shell bosun
  bosun="$(printf '%q' "$BOSUN")"
  wrapper_args=()
  while IFS= read -r shell; do
    [ -n "$shell" ] || continue
    wrapper_args+=(--wrapper "$shell -lic $bosun launch " --wrapper "$shell -lic $bosun checkin --loop ")
  done < <({
    tmux_cmd show-options -gv default-shell 2>/dev/null
    login_shell
  } | awk '!seen[$0]++')
}

# Stop the helpers shell <pid> forked off (SIGTERM); prints them, one "<pid><TAB><command>" each.
# --wrapper-only: only if that shell is a pane's launch wrapper, which never shows a prompt (so a
# shell you work in keeps its helpers). `launch` and `checkin --loop` call it for their own shell,
# `refresh` and `stop` for a pane they replace or close, whose shell goes anyway.
reap_shell_helpers() { # <pid> [--wrapper-only]
  local -a helper_args=("$1" --kill)
  if [ "${2:-}" = "--wrapper-only" ]; then
    set_wrapper_args
    helper_args+=(${wrapper_args[@]+"${wrapper_args[@]}"})
  fi
  orphans helpers "${helper_args[@]}" 2>/dev/null || true
}

log_reaped() { # <window> <reap_shell_helpers output>
  [ -n "$2" ] || return 0
  log "$1: stopped $(grep -c . <<<"$2") helper(s) its shell's startup files had left (pid $(cut -f1 <<<"$2" | paste -sd, -))"
}

# Stop the helpers of every pane's shell in the session, before it is killed.
reap_session_helpers() {
  local window pane_pid
  while IFS=$'\t' read -r window pane_pid; do
    log_reaped "$window" "$(reap_shell_helpers "$pane_pid")"
  done < <(tmux_cmd list-panes -s -t "=$BOSUN_SESSION_NAME" -F '#{window_name}	#{pane_pid}' 2>/dev/null)
}

# Every window of the session, in order, as "name<TAB>dir<TAB>kind<TAB>model<TAB>effort<TAB>harness": the
# orchestrator (kind orchestrator, if enabled), one per sessions.yml entry (kind session), and
# checkin (kind checkin, when the orchestrator and check-ins are on). Model and effort come from
# the sessions.yml entry or the defaults; "-" means the agent's own default. The one list that
# start, refresh, launch, morning --dry-run and doctor work from.
window_specs() {
  [ -f "$BOSUN_SESSIONS_FILE" ] || die "sessions manifest not found: $BOSUN_SESSIONS_FILE"
  if [ -n "$BOSUN_ORCHESTRATOR" ]; then
    printf '%s\t%s\torchestrator\t%s\t%s\t%s\n' "$BOSUN_ORCHESTRATOR" "$BOSUN_ORCHESTRATOR_DIR" \
      "${BOSUN_ORCHESTRATOR_MODEL:--}" "${BOSUN_ORCHESTRATOR_EFFORT:--}" "$ORCHESTRATOR_HARNESS"
  fi
  ruby -ryaml -e '
    pick = ->(value, default) { value.to_s.empty? ? default : value.to_s }
    default_model = { "claude" => ARGV[1], "codex" => ARGV[4] }  # as harness_default_model says
    (YAML.safe_load(File.read(ARGV[0])) || {}).fetch("sessions", []).each do |s|
      harness = pick.(s["harness"], ARGV[3])
      puts [s.fetch("name"), File.expand_path(s.fetch("dir")), "session",
            pick.(s["model"], default_model.fetch(harness, "-")), pick.(s["effort"], ARGV[2]), harness].join("\t")
    end' "$BOSUN_SESSIONS_FILE" "$(harness_default_model claude)" "${BOSUN_EFFORT:--}" "$BOSUN_HARNESS" "$(harness_default_model codex)" ||
    die "could not read $BOSUN_SESSIONS_FILE"
  if checkin_loop_wanted; then
    printf 'checkin\t%s\tcheckin\t-\t-\t-\n' "$HOME"
  fi
}

# Whether the checkin window runs its loop: check-ins are on, or events run without them
# (BOSUN_EVENTS_WITHOUT_CHECKINS=1). lib/watchdog.py's loop_wanted says the same.
checkin_loop_wanted() {
  [ -n "$BOSUN_ORCHESTRATOR" ] || return 1
  [ "$BOSUN_CHECKIN_MINUTES" -gt 0 ] && return 0
  [ "$BOSUN_EVENTS_ENABLED" = "1" ] && [ "$BOSUN_EVENTS_WITHOUT_CHECKINS" = "1" ]
}

EFFORT_LEVELS="low medium high xhigh max"

# Whether <effort> is a Claude Code effort level, or "-" / empty for its default.
valid_effort() { [ -z "$1" ] || [ "$1" = "-" ] || [[ " $EFFORT_LEVELS " == *" $1 "* ]]; }

# The configured "model<TAB>effort" for window <name>: its window_specs entry, else the defaults.
window_model_effort() {
  if is_orchestrator "$1"; then
    printf '%s\t%s\n' "${BOSUN_ORCHESTRATOR_MODEL:--}" "${BOSUN_ORCHESTRATOR_EFFORT:--}"
    return
  fi
  { window_specs 2>/dev/null | awk -F'\t' -v name="$1" '$1 == name { print $4 "\t" $5; found = 1; exit } END { exit !found }'; } ||
    printf '%s\t%s\n' "$(harness_default_model "$(window_harness "$1")")" "${BOSUN_EFFORT:--}"
}

# Sets model_effort_args to the Claude flags for <model> and <effort> ("-": Claude Code's default).
# Effort goes in as a session-level setting (--settings), not --effort: that way /effort inside
# the session can still change it, while your saved defaults (from /model and /effort, which
# write ~/.claude/settings.json) no longer decide it. [settings-file] is merged in (orchestrator).
build_model_effort_args() { # <model> <effort> [settings-file] [extra settings JSON]
  local model="$1" effort="$2" settings_file="${3:-}" extra="${4:-}" settings='{}'
  model_effort_args=()
  [ -n "$model" ] && [ "$model" != "-" ] && model_effort_args+=(--model "$model")
  [ "$effort" = "-" ] && effort=""
  if [ -z "$effort" ] && [ -z "$extra" ]; then
    [ -n "$settings_file" ] && model_effort_args+=(--settings "$settings_file")
    return 0
  fi
  if [ -n "$settings_file" ]; then
    settings="$(jq -c . "$settings_file")" || die "could not read $settings_file"
  fi
  [ -n "$effort" ] && settings="$(jq -c --arg effort "$effort" '. + {effortLevel: $effort}' <<<"$settings")"
  # The extra hooks are added to the file's own (for the same event too), never replace them.
  # shellcheck disable=SC2016  # a jq program, not shell
  [ -n "$extra" ] && settings="$(jq -c --argjson extra "$extra" '
    reduce (($extra.hooks // {}) | to_entries[]) as $hook (. * ($extra | del(.hooks));
      .hooks[$hook.key] = ((.hooks[$hook.key] // []) + $hook.value))' <<<"$settings")"
  model_effort_args+=(--settings "$settings")
}

# The settings the orchestrator gets on top of its settings file: with BOSUN_APPROVALS_ENABLED=1,
# the approvals hooks (`bosun guard --approvals`, `--permission-request` and, after the call,
# `--post-tool-use` for both PostToolUse and PostToolUseFailure; lib/guard.py); and the compaction
# hook (compact_hook_settings). Nothing when neither is on, so its launch is unchanged.
orchestrator_extra_settings() {
  {
    approvals_hook_settings
    compact_hook_settings --orchestrator
    signal_hook_settings "$BOSUN_ORCHESTRATOR"
  } | merge_settings
}

approvals_hook_settings() {
  [ "$BOSUN_APPROVALS_ENABLED" = "1" ] || return 0
  jq -cn --arg guard "$(printf '%q ' "$BOSUN" guard)" '
    def hook($mode; $timeout): [{matcher: "Bash|mcp__.*", hooks: [{type: "command", command: ($guard + $mode), timeout: $timeout}]}];
    {hooks: {
      PreToolUse: hook("--approvals"; 30),
      PermissionRequest: hook("--permission-request"; 30),
      PostToolUse: hook("--post-tool-use"; 10),
      PostToolUseFailure: hook("--post-tool-use"; 10)
    }}'
}

# The settings a repo session gets on top of its model and effort: with BOSUN_EVENTS_ENABLED=1,
# the UserPromptSubmit hook that records what you type in <window> (`bosun hook`); and the
# compaction hook (compact_hook_settings). Nothing when neither is on.
repo_session_settings() { # <window>
  {
    if [ "$BOSUN_EVENTS_ENABLED" = "1" ]; then
      jq -cn --arg command "$(printf '%q ' "$BOSUN" hook user-prompt --window "$1")" \
        '{hooks: {UserPromptSubmit: [{hooks: [{type: "command", command: $command, timeout: 10}]}]}}'
    fi
    compact_hook_settings --window "$1"
    signal_hook_settings "$1"
  } | merge_settings
}

# With BOSUN_HOOK_SIGNALS=1, async hooks that record Claude Code's own signals for <window>
# (`bosun hook signal`): they never block or change anything, so the session never waits on them.
signal_hook_settings() { # <window>
  [ "$BOSUN_HOOK_SIGNALS" = "1" ] || return 0
  jq -cn --arg command "$(printf '%q ' "$BOSUN" hook signal --window "$1")" '
    [{hooks: [{type: "command", command: $command, async: true, timeout: 10}]}] as $hook
    | {hooks: {Notification: $hook, PermissionRequest: $hook, Stop: $hook, StopFailure: $hook,
        SessionStart: $hook, SessionEnd: $hook}}'
}

# With BOSUN_COMPACT_CONTEXT=1, the SessionStart hook (matcher compact) that adds back what the
# window holds once Claude Code compacts its conversation (`bosun hook compact-context`).
compact_hook_settings() { # --window <window> | --orchestrator
  [ "$BOSUN_COMPACT_CONTEXT" = "1" ] || return 0
  jq -cn --arg command "$(printf '%q ' "$BOSUN" hook compact-context "$@")" \
    '{hooks: {SessionStart: [{matcher: "compact", hooks: [{type: "command", command: $command, timeout: 15}]}]}}'
}

# Settings objects on stdin (one per line) merged into one: keys from later ones win, hooks for the
# same event are concatenated. Prints nothing when there is nothing to merge.
merge_settings() {
  jq -cs '
    reduce .[] as $s ({}; (. * ($s | del(.hooks))) as $merged
      | $merged | .hooks = (reduce (($s.hooks // {}) | to_entries[]) as $e ($merged.hooks // {};
          .[$e.key] = ((.[$e.key] // []) + $e.value))))
    | if .hooks == {} then del(.hooks) else . end
    | select(. != {})'
}

# Sets autocompact_flag to `--autocompact <n>` for window <name>: the orchestrator's setting, else
# its sessions.yml `autocompact`, else BOSUN_AUTOCOMPACT_TOKENS; empty when that is empty or 0.
set_autocompact_flag() { # <name>
  local tokens
  if is_orchestrator "$1"; then
    tokens="$BOSUN_ORCHESTRATOR_AUTOCOMPACT_TOKENS"
  else
    tokens="$(window_autocompact "$1")"
  fi
  autocompact_flag=()
  if [ -z "$tokens" ] || [ "$tokens" = "0" ]; then return 0; fi
  valid_autocompact "$tokens" || die "autocompact for $1 must be auto or 100000-1000000 tokens (got '$tokens')"
  autocompact_flag=(--autocompact "$tokens")
}

valid_autocompact() { [ "$1" = "auto" ] || { [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 100000 ] && [ "$1" -le 1000000 ]; }; }

# Window <name>'s sessions.yml `autocompact`, else BOSUN_AUTOCOMPACT_TOKENS.
window_autocompact() { # <name>
  [ -f "$BOSUN_SESSIONS_FILE" ] || {
    printf '%s\n' "$BOSUN_AUTOCOMPACT_TOKENS"
    return 0
  }
  ruby -ryaml -e '
    entry = (YAML.safe_load(File.read(ARGV[0])) || {}).fetch("sessions", []).find { |s| s["name"] == ARGV[1] } || {}
    puts entry.key?("autocompact") ? entry["autocompact"].to_s : ARGV[2]' \
    "$BOSUN_SESSIONS_FILE" "$1" "$BOSUN_AUTOCOMPACT_TOKENS" 2>/dev/null ||
    printf '%s\n' "$BOSUN_AUTOCOMPACT_TOKENS"
}

# The bosun window this command runs in (from tmux's TMUX_PANE, when the pane belongs to the
# bosun tmux server), or nothing.
caller_window() {
  [ -n "${TMUX_PANE:-}" ] && [ -n "${TMUX:-}" ] || return 0
  [ "$(basename "${TMUX%%,*}")" = "${BOSUN_TMUX_SOCKET:-default}" ] || return 0
  tmux_cmd display-message -p -t "$TMUX_PANE" '#{window_name}' 2>/dev/null
}

source "$BOSUN_ROOT/lib/agents.sh"

# lib/login.py: whether Claude Code is still logged in, and whether a window's session gets real
# replies or only login errors.
claude_login() {
  BOSUN_STATE_DIR="$BOSUN_STATE_DIR" BOSUN_CLAUDE_BIN="$BOSUN_CLAUDE_BIN" \
    python3 "$BOSUN_ROOT/lib/login.py" "$@"
}

# owner/repo of the git repository in <dir>, from its origin remote URL.
repo_slug_of_dir() {
  local remote
  remote="$(git -C "$1" remote get-url origin 2>/dev/null)" || return 1
  sed -E 's#(\.git)?$##; s#^.*[:/]([^/:]+/[^/]+)$#\1#' <<<"$remote"
}

is_orchestrator() { [ -n "$BOSUN_ORCHESTRATOR" ] && [ "$1" = "$BOSUN_ORCHESTRATOR" ]; }

# Claude session name a window should be running: the orchestrator starts a fresh, dated
# conversation each day; every other window keeps its name, or the name of the fresh conversation
# `bosun refresh` last started for it (BOSUN_FRESH_CONVERSATIONS).
expected_session_name() {
  local recorded
  if is_orchestrator "$1"; then
    echo "$1-$(date +%F)"
  elif recorded="$(cat "$(conversation_file "$1")" 2>/dev/null)" && [ -n "$recorded" ]; then
    echo "$recorded"
  else
    echo "$1"
  fi
}

# Where a repo window's current conversation name is kept once refresh started a fresh one.
conversation_file() { echo "$BOSUN_STATE_DIR/conversations/$1"; }

# The next fresh conversation name for window <name>: <name>-<date>-<n>.
next_conversation_name() { # <name>
  echo "$1-$(date +%F)-$(($(conversations_today "$1") + 1))"
}

conversations_today() { # <name>: how many fresh conversations it started today
  local recorded
  recorded="$(cat "$(conversation_file "$1")" 2>/dev/null)"
  case "$recorded" in
    "$1-$(date +%F)-"*) echo "${recorded##*-}" ;;
    *) echo 0 ;;
  esac
}

# Seconds from now until HH:MM today (0 if already past).
# Seconds from now until BOSUN_AWAKE_UNTIL, the end of the current awake window. An end at or
# before BOSUN_START_TIME is past midnight (01:00 = 1 am): the coming one, so tonight's when it
# has passed today, or today's when yesterday's window is still running.
seconds_until_awake_end() {
  local -r seconds_per_day=86400
  local target now
  target="$(date -j -f '%Y-%m-%d %H:%M:%S' "$(date +%F) $BOSUN_AWAKE_UNTIL:00" +%s)" || return 1
  now="$(date +%s)"
  if [[ ! "$BOSUN_AWAKE_UNTIL" > "$BOSUN_START_TIME" ]] && [ "$target" -le "$now" ]; then
    target=$((target + seconds_per_day))
  fi
  echo $((target > now ? target - now : 0))
}

seconds_until() {
  local target now
  target="$(date -j -f '%Y-%m-%d %H:%M:%S' "$(date +%F) $1:00" +%s)" || return 1
  now="$(date +%s)"
  echo $((target > now ? target - now : 0))
}

# Run a command, killing it after N seconds. Returns the command's exit code (143 on timeout).
run_with_timeout() {
  local seconds="$1" pid watcher rc
  shift
  "$@" &
  pid=$!
  (sleep "$seconds" && kill "$pid") 2>/dev/null &
  watcher=$!
  wait "$pid"
  rc=$?
  pkill -P "$watcher" 2>/dev/null
  kill "$watcher" 2>/dev/null
  return "$rc"
}

# Whether the check-in loop (`bosun checkin --loop`) runs under tmux pane <pane_pid>.
checkin_loop_running() {
  local pid
  for pid in $(descendants "$1"); do
    ps -o command= -p "$pid" 2>/dev/null | grep -q "bosun-checkin --loop" && return 0
  done
  return 1
}

descendants() {
  ps -Ao pid=,ppid= | awk -v root="$1" '
    { parent[$1] = $2 }
    END {
      for (pid in parent) {
        p = pid
        while (p in parent && p != root && p > 1) p = parent[p]
        if (p == root && pid != root) print pid
      }
    }'
}

# StartCalendarInterval XML for HH:MM on the given pmset-style days (e.g. MTWRF).
launchd_calendar_xml() {
  local hour=$((10#${1%%:*})) minute=$((10#${1##*:})) days="$2" i letter
  local weekday_letters="UMTWRFS"
  if [ "$days" = "MTWRFSU" ]; then
    printf '  <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>\n' "$hour" "$minute"
    return
  fi
  printf '  <array>\n'
  for ((i = 0; i < ${#days}; i++)); do
    letter="${days:i:1}"
    local prefix="${weekday_letters%%"$letter"*}"
    printf '    <dict><key>Weekday</key><integer>%d</integer><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>\n' \
      "${#prefix}" "$hour" "$minute"
  done
  printf '  </array>\n'
}

# The EnvironmentVariables entries of a launchd plist: BOSUN_CONFIG_DIR plus KEY=VALUE pairs.
launchd_env_xml() {
  local pair env_xml=""
  for pair in "BOSUN_CONFIG_DIR=$BOSUN_CONFIG_DIR" "$@"; do
    env_xml+="    <key>${pair%%=*}</key><string>${pair#*=}</string>"$'\n'
  done
  printf '%s' "${env_xml%$'\n'}"
}

# The watchdog's launchd agent runs `bosun watchdog` every WATCHDOG_INTERVAL_SECONDS,
# independent of the tmux session. Its label is the morning job's plus ".watchdog".
WATCHDOG_INTERVAL_SECONDS=300
watchdog_label() { echo "$BOSUN_LAUNCHD_LABEL.watchdog"; }
watchdog_plist() { echo "$HOME/Library/LaunchAgents/$(watchdog_label).plist"; }

# The settings lib/watchdog.py reads. load_config does not export them, so `watchdog` and `console`
# pass them explicitly, from settings_lines.
WATCHDOG_SETTINGS="BOSUN_STATE_DIR BOSUN_LOG_DIR BOSUN_SESSION_NAME BOSUN_ORCHESTRATOR
  BOSUN_DAYS BOSUN_START_TIME BOSUN_WAKE_DELAY_MINUTES BOSUN_AWAKE_UNTIL
  BOSUN_ALERT_WAITING_MINUTES BOSUN_CHECKIN_MINUTES BOSUN_EVENTS_WITHOUT_CHECKINS
  BOSUN_WATCHDOG_GRACE_MINUTES
  BOSUN_WATCHDOG_DELEGATED_HOURS BOSUN_LONG_SHELL_HOURS BOSUN_WATCHDOG_ORCHESTRATOR_DOWN_MINUTES
  BOSUN_WATCHDOG_ORCHESTRATOR_RESTART BOSUN_HOOK_SIGNALS BOSUN_LEARN_ENABLED BOSUN_LEARN_AT BOSUN_LEARN_TIMEOUT_MINUTES
  BOSUN_EVENTS_ENABLED BOSUN_EVENTS_INTERVAL_SECONDS BOSUN_SLACK_WATCH_ENABLED
  BOSUN_SLACK_WATCH_MINUTES BOSUN_CLAUDE_BIN BOSUN_CONTEXT_WARN_TOKENS
  BOSUN_OVERHEAD_WARN_SHARE"

# "NAME=value" for each setting named, one per line (for `env`).
settings_lines() { # <setting names...>
  local name
  for name in "$@"; do printf '%s=%s\n' "$name" "${!name}"; done
}

render_watchdog_plist() {
  ENV_XML="$(launchd_env_xml)" LABEL="$(watchdog_label)" INTERVAL="$WATCHDOG_INTERVAL_SECONDS" ROOT="$BOSUN_ROOT" LOGS="$BOSUN_LOG_DIR" \
    ruby -e 'print STDIN.read
      .gsub("__LABEL__", ENV["LABEL"]).gsub("__BOSUN_ROOT__", ENV["ROOT"]).gsub("__INTERVAL__", ENV["INTERVAL"])
      .gsub("__ENV__", ENV["ENV_XML"]).gsub("__LOG_DIR__", ENV["LOGS"])' \
    <"$BOSUN_ROOT/templates/launchd-watchdog.plist"
}

# The learning agent runs `bosun learn --scheduled` daily at BOSUN_LEARN_AT on
# BOSUN_DAYS; install.sh loads it only with BOSUN_LEARN_ENABLED=1.
learn_label() { echo "$BOSUN_LAUNCHD_LABEL.learn"; }
learn_plist() { echo "$HOME/Library/LaunchAgents/$(learn_label).plist"; }

render_learn_plist() {
  ENV_XML="$(launchd_env_xml)" CALENDAR="$(launchd_calendar_xml "$BOSUN_LEARN_AT" "$BOSUN_DAYS")" \
  LABEL="$(learn_label)" ROOT="$BOSUN_ROOT" LOGS="$BOSUN_LOG_DIR" \
    ruby -e 'print STDIN.read
      .gsub("__LABEL__", ENV["LABEL"]).gsub("__BOSUN_ROOT__", ENV["ROOT"]).gsub("__CALENDAR__", ENV["CALENDAR"])
      .gsub("__ENV__", ENV["ENV_XML"]).gsub("__LOG_DIR__", ENV["LOGS"])' \
    <"$BOSUN_ROOT/templates/launchd-learn.plist"
}

# render_launchd_plist <label> <HH:MM> <days> <morning-args> [KEY=VALUE ...]
render_launchd_plist() {
  local label="$1" time="$2" days="$3" args="$4" env_xml calendar
  shift 4
  env_xml="$(launchd_env_xml "$@")"
  calendar="$(launchd_calendar_xml "$time" "$days")"
  ENV_XML="$env_xml" CALENDAR="$calendar" LABEL="$label" ARGS="$args" ROOT="$BOSUN_ROOT" LOGS="$BOSUN_LOG_DIR" \
    ruby -e 'print STDIN.read
      .gsub("__LABEL__", ENV["LABEL"]).gsub("__BOSUN_ROOT__", ENV["ROOT"]).gsub("__ARGS__", ENV["ARGS"])
      .gsub("__ENV__", ENV["ENV_XML"]).gsub("__CALENDAR__", ENV["CALENDAR"]).gsub("__LOG_DIR__", ENV["LOGS"])' \
    <"$BOSUN_ROOT/templates/launchd.plist"
}

# HH:MM shifted by N minutes (negative allowed), wrapping around midnight.
shift_time() {
  local total=$(((10#${1%%:*} * 60 + 10#${1##*:} + $2 + 1440) % 1440))
  printf '%02d:%02d' $((total / 60)) $((total % 60))
}

# Print "<pane_pid> <window_id>" for the window named exactly $1 in the bosun session.
# (tmux target lookups fall back to the current window for unknown names, so match by hand.)
find_window() {
  tmux_cmd list-windows -t "=$BOSUN_SESSION_NAME" -F '#{window_name}	#{pane_pid} #{window_id}' 2>/dev/null |
    awk -F'\t' -v name="$1" '$1 == name { print $2; found = 1; exit } END { exit !found }'
}

# With events on, tell <window>'s prompt hook that <text> is bosun's, not yours, so it is not
# recorded as something you said (`ledger said`, user-said events): every prompt bosun gives a
# window, pasted (`send`) or passed at launch (refresh's fresh conversation, `send --new`).
note_bosun_prompt() { # <window> <text>
  [ "$BOSUN_EVENTS_ENABLED" = "1" ] && [ -n "$2" ] || return 0
  BOSUN_STATE_DIR="$BOSUN_STATE_DIR" python3 "$BOSUN_ROOT/lib/events.py" pasted "$1" <<<"$2" ||
    log "warning: could not note bosun's prompt for $1's prompt hook"
}

# Minutes since the last keystroke from any client attached to the session, or with a window id
# only from clients showing that window; fails if no such client is attached.
minutes_since_client_input() { # [window_id]
  local latest
  latest="$(tmux_cmd list-clients -t "=$BOSUN_SESSION_NAME" -F '#{client_activity} #{window_id}' 2>/dev/null |
    awk -v window="${1:-}" 'window == "" || $2 == window { print $1 }' | sort -n | tail -1)"
  [ -n "$latest" ] || return 1
  echo $((($(date +%s) - latest) / 60))
}

# Succeeds if owner/repo matches BOSUN_REPO_MASK (see lib/repo_mask.py; empty mask matches all).
repo_in_mask() { [ -n "$(printf '%s\n' "$1" | repo_mask_filter "")" ]; }

# Print the lines on stdin whose repository matches BOSUN_REPO_MASK: the first field of each
# line, or with a key, that key of each JSON line.
repo_mask_filter() { # <json-key, or "" for the first field>
  python3 "$BOSUN_ROOT/lib/repo_mask.py" "$BOSUN_REPO_MASK" ${1:+--json-key "$1"}
}
