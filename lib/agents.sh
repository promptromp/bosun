# shellcheck shell=bash
# What bosun knows about a window's agent, whichever it is (Claude Code, Codex): sourced by lib/common.sh.
# Each agent's own files are its adapter's business (lib/claude_state.py, lib/codex_state.py), and
# lib/agent_state.py asks them. What differs in how one starts is in lib/launch/<harness>.sh, and how to
# check one in lib/harnesses/<harness>.sh. Design: docs/design/harnesses.md.

# The orchestrator stands on Claude Code's hooks, plugins and permission settings, for now.
ORCHESTRATOR_HARNESS="claude"

# What each agent says it is and can do (Profile in lib/agent_files.py), asked of lib/agent_state.py once per
# process: one tab-separated line per agent, in AGENTS order, with the columns of AGENT_PROFILE_COLUMNS.
# A command substitution has its own copy of the variable, so a function that makes many of them reads the
# profiles first (need_profiles), and what they inherit is filled in.
AGENT_PROFILE_COLUMNS="harness bin_key flags_key model_key names_sessions remote_control autocompact subagents statuses update_args"
AGENT_PROFILES=""
AGENT_PROFILES_ERROR=""

# Fills AGENT_PROFILES; on a failure returns 1 with why in AGENT_PROFILES_ERROR (run_preflight goes on
# without: it is what says python3 is missing).
load_agent_profiles() {
  [ -n "$AGENT_PROFILES" ] && return 0
  local out errors_file="" errors=""
  # Only stdout is the profiles: what a working python3 says on stderr (a warning, a shim's notice) is not.
  errors_file="$(mktemp "${TMPDIR:-/tmp}/bosun-profiles.XXXXXX" 2>/dev/null)" || errors_file=/dev/null
  if ! out="$(python3 "$BOSUN_ROOT/lib/agent_state.py" profiles 2>"$errors_file")" || [ -z "$out" ]; then
    errors="$(cat "$errors_file" 2>/dev/null)"
    [ "$errors_file" = /dev/null ] || rm -f "$errors_file"
    AGENT_PROFILES_ERROR="could not read the agents' profiles (lib/agent_state.py profiles): ${errors:-$out}"
    return 1
  fi
  [ "$errors_file" = /dev/null ] || rm -f "$errors_file"
  AGENT_PROFILES="$out"
}

need_profiles() { load_agent_profiles || die "$AGENT_PROFILES_ERROR"; }

agent_profiles() {
  need_profiles
  printf '%s\n' "$AGENT_PROFILES"
}

# One field of <harness>'s profile, by its column name; exit 1 and nothing printed for a harness with none.
profile_field() { # <harness> <field>
  need_profiles
  printf '%s\n' "$AGENT_PROFILES" | awk -F'\t' -v harness="$1" -v field="$2" -v columns="$AGENT_PROFILE_COLUMNS" '
    BEGIN { n = split(columns, names, " "); for (i = 1; i <= n; i++) if (names[i] == field) column = i }
    column && $1 == harness { print $column; found = 1; exit }
    END { exit !found }'
}

# The agents bosun knows, as one line of words, in the order of AGENTS.
known_harnesses() {
  need_profiles
  printf '%s\n' "$AGENT_PROFILES" | awk -F'\t' '{ printf "%s%s", (NR > 1 ? " " : ""), $1 } END { print "" }'
}

# Sets agent_bin_assignments to KEY=value for the setting that names each agent's command: what a process
# started with `env` gets, since load_config exports nothing. Callers declare it local.
agent_bin_env() {
  local key IFS=$' \t\n' # bash 3.2 lets `IFS=$'\t' read <<<"$(f)"` reach the words f splits
  agent_bin_assignments=()
  need_profiles
  for key in $(printf '%s\n' "$AGENT_PROFILES" | cut -f2); do
    agent_bin_assignments+=("$key=${!key:-}")
  done
}

agent_state() {
  local agent_bin_assignments
  agent_bin_env
  env BOSUN_STATE_DIR="$BOSUN_STATE_DIR" "${agent_bin_assignments[@]}" python3 "$BOSUN_ROOT/lib/agent_state.py" "$@"
}

# What only Claude Code has, such as its bypass warning.
claude_state() { python3 "$BOSUN_ROOT/lib/claude_state.py" "$@"; }

# The agent a window runs: the window list says, and a window it does not list (`send --new`) gets
# BOSUN_HARNESS. The list needs sessions.yml, so the orchestrator cannot go by it.
window_harness() { # <window>
  local harness
  if is_orchestrator "$1"; then
    echo "$ORCHESTRATOR_HARNESS"
    return 0
  fi
  harness="$(window_specs 2>/dev/null | awk -F'\t' -v name="$1" '$1 == name { print $6; exit }')"
  echo "${harness:-$BOSUN_HARNESS}"
}

# The model a window on <harness> runs unless it says otherwise; each agent names its models differently.
harness_default_model() { # <harness>
  local key
  key="$(profile_field "$1" model_key)" || return 1
  echo "${!key:--}"
}

# The command that runs <harness>, as the config names it.
harness_bin() { # <harness>
  local key
  key="$(profile_field "$1" bin_key)" || return 1
  echo "${!key:-}"
}

# Every agent this setup uses, as one line of words: what preflight and `bosun trust` go through.
harnesses_in_use() {
  {
    [ -n "$BOSUN_ORCHESTRATOR" ] && echo "$ORCHESTRATOR_HARNESS"
    echo "$BOSUN_HARNESS"
    [ -f "$BOSUN_SESSIONS_FILE" ] && window_specs 2>/dev/null | cut -f6 | grep -v '^-$'
  } | awk '!seen[$0]++' | tr '\n' ' ' | sed 's/ $//'
}

# Makes <harness>'s one-time setup, if its profile has one and it is still to be made (agent_state.py setup),
# and puts it on record for `bosun uninstall`. It holds agent-setup.lock, waiting up to 60 seconds, so two
# windows starting at once on an agent that has not been set up do it once. Says why on stderr when it
# cannot; the caller stops the launch. The setup may install from this install (BOSUN_INSTALL), and
# load_config exports nothing, so what it needs goes along explicitly.
agent_setup() { # <harness>
  local agent_bin_assignments
  agent_bin_env
  python3 "$BOSUN_ROOT/lib/run_locked.py" "$BOSUN_STATE_DIR/agent-setup.lock" 60 \
    "another bosun is setting up $1 and did not finish within a minute" -- \
    env BOSUN_STATE_DIR="$BOSUN_STATE_DIR" BOSUN_INSTALL="$BOSUN_INSTALL" "${agent_bin_assignments[@]}" \
    python3 "$BOSUN_ROOT/lib/agent_state.py" setup "$1"
}

# Updates one agent with its own command (its profile's update_args), as the morning run does for an agent
# bosun starts with its auto-update off. Logs what came of it on stderr; prints the problem word
# (update-<agent>) on stdout when it failed. With --dry-run it only says what it would run.
update_one_agent() { # [--dry-run] <harness> <update args>
  local dry_run=0 harness bin name out_file rc=0 update_words
  [ "$1" = "--dry-run" ] && dry_run=1 && shift
  harness="$1"
  read -r -a update_words <<<"$2"
  bin="$(harness_bin "$harness")"
  name="$(agent_display_name "$harness")"
  if [ "$dry_run" = "1" ]; then
    log "dry-run: would run $bin ${update_words[*]}" >&2
    return 0
  fi
  out_file="$(mktemp "${TMPDIR:-/tmp}/bosun-update-agent.XXXXXX")" || out_file=/dev/null
  run_with_timeout 300 "$bin" "${update_words[@]}" >"$out_file" 2>&1 || rc=$?
  if [ "$rc" = "0" ]; then
    log "updated $name" >&2
  else
    log "could not update $name: $(grep -v '^[[:space:]]*$' "$out_file" 2>/dev/null | tail -n 1)" >&2
    echo "update-$harness"
  fi
  [ "$out_file" = /dev/null ] || rm -f "$out_file"
}

# Updates each agent in use whose profile says how (update_args), once, at a quiet moment: bosun starts
# such an agent with its own auto-update off, so a running window is never changed under it. A failure is
# a problem word on stdout, for the morning's notification, and stops nothing.
update_agents() { # [--dry-run]
  local harness update
  need_profiles
  for harness in $(harnesses_in_use); do
    update="$(profile_field "$harness" update_args)" || continue
    [ "$update" = "-" ] && continue
    update_one_agent "$@" "$harness" "$update"
  done
}

# The check `bosun preflight`, `init` and `doctor` all start with. The agents' commands go along
# explicitly, since load_config does not export them.
run_preflight() { # [--quiet]
  local agent_bin_assignments=()
  # Without python3 the commands cannot be named; preflight then says that python3 is what is missing.
  if load_agent_profiles; then agent_bin_env; fi
  env BOSUN_HARNESSES="$(harnesses_in_use)" ${agent_bin_assignments[@]+"${agent_bin_assignments[@]}"} \
    sh "$BOSUN_ROOT/lib/preflight.sh" "$@"
}

# Ask an agent's profile (lib/harnesses/<harness>.sh) something.
from_profile() { # <harness> <function>
  local agent_bin_assignments
  agent_bin_env
  # shellcheck disable=SC2016  # the dot-script's own $1 and $2, in the sh that env starts
  env "${agent_bin_assignments[@]}" sh -c '. "$1"; "$2"' _ "$BOSUN_ROOT/lib/harnesses/$1.sh" "$2"
}
agent_display_name() { from_profile "$1" harness_name; }         # <harness>
agent_installed_version() { from_profile "$1" harness_version; } # <harness>

# Whether <harness> has accepted its folder trust screen for <dir>. In an untrusted folder an agent shows
# that screen at startup and waits, which stalls an unattended start. `bosun trust` sets it.
agent_trusts() { [ -d "$2" ] && agent_state trusted "$1" "$2"; } # <harness> <dir>

# PID of the agent with a session running anywhere under a tmux pane, if any.
agent_pid_under() {
  local pids
  pids="$(descendants "$1")" || return 1
  [ -n "$pids" ] || return 1
  # shellcheck disable=SC2086
  agent_state registered $pids
}

# PID of an agent's process under a tmux pane, session or not. Every screen before a session (first run,
# trust) runs without one, so agent_pid_under cannot see it. Goes by the first word of its command line,
# which is the name however it was installed: the npm one's process is called node.
agent_pid_starting() {                          # <pane_pid>
  local pid command agents harness IFS=$' \t\n' # as in agent_bin_env
  agents=" "
  for harness in $(known_harnesses); do
    agents="$agents$(basename "$(harness_bin "$harness")") "
  done
  for pid in $(descendants "$1"); do
    command="$(ps -o command= -p "$pid" 2>/dev/null)"
    case "$agents" in
      *" $(basename "${command%% *}") "*)
        echo "$pid"
        return 0
        ;;
    esac
  done
  return 1
}

# One line per window of the session: its name, the pid of the agent with a session in it, if any, and
# the pid of one that has none yet.
window_agent_facts() {
  local name pane_pid pid starting
  need_profiles # once, before the substitutions below
  tmux_cmd list-windows -t "=$BOSUN_SESSION_NAME" -F '#{window_name}	#{pane_pid}' 2>/dev/null |
    while IFS=$'\t' read -r name pane_pid; do
      starting=""
      pid="$(agent_pid_under "$pane_pid")" || {
        pid=""
        starting="$(agent_pid_starting "$pane_pid")"
      }
      printf '%s\t%s\t%s\n' "$name" "$pid" "$starting"
    done
}

# One line per window of the session: its name and the pid of the Claude running in it, if any.
window_agent_pids() { window_agent_facts | cut -f1,2; }

# The model and effort a running agent was launched with, as "model<TAB>effort<TAB>bosun" ("-" when not set at
# launch; bosun: "yes" if bosun launched it). Each agent's adapter reads its own command line.
agent_launch_model_effort() { # <pid>
  agent_state launch "$1" 2>/dev/null || printf -- '-\t-\tno\n'
}

# A field (status, version, sessionId, harness, ...) of a running agent's record.
agent_session_field() { agent_state field "$1" "$2" 2>/dev/null; }

# Whether an agent with <status> can take a pasted message: idle, busy (it queues the message) or
# shell (turn over, a background shell still running). Never "waiting": that is a permission
# prompt or a question to you, where a pasted Enter would answer it.
agent_takes_input() { case "$1" in idle | busy | shell) return 0 ;; *) return 1 ;; esac }

# Whether an agent with <status> has finished its turn: idle, or shell (only a background command
# still runs; its completion notice queues behind a prompt pasted meanwhile). Check-ins and events
# wait for this. In both, statusUpdatedAt is when the turn ended (agent_seconds_in_status).
agent_turn_over() { case "$1" in idle | shell) return 0 ;; *) return 1 ;; esac }

# How lists show a running agent's <status>: "shell" (its turn is over and a background command
# it started still runs) reads as no agent at all, so it shows as "idle-bg". The status names
# the scripts compare stay as Claude Code writes them.
agent_status_label() { if [ "$1" = shell ]; then echo idle-bg; else echo "${1:-?}"; fi; }

# Seconds since a running agent last changed status (busy/idle/shell); empty if unknown.
agent_seconds_in_status() {
  local updated_ms
  updated_ms="$(agent_session_field "$1" statusUpdatedAt)"
  [ -n "$updated_ms" ] && echo $(($(date +%s) - updated_ms / 1000))
}
