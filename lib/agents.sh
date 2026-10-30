# shellcheck shell=bash
# What bosun knows about a window's agent, whichever it is (Claude Code, Codex): sourced by lib/common.sh.
# Each agent's own files are its adapter's business (lib/claude_state.py, lib/codex_state.py), and
# lib/agent_state.py asks them. What differs in how one starts is in lib/launch/<harness>.sh, and how to
# check one in lib/harnesses/<harness>.sh. Design: docs/design/harnesses.md.

# The orchestrator stands on Claude Code's hooks, plugins and permission settings, for now.
ORCHESTRATOR_HARNESS="claude"

agent_state() {
  BOSUN_STATE_DIR="$BOSUN_STATE_DIR" BOSUN_CLAUDE_BIN="$BOSUN_CLAUDE_BIN" BOSUN_CODEX_BIN="$BOSUN_CODEX_BIN" \
    python3 "$BOSUN_ROOT/lib/agent_state.py" "$@"
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
  case "$1" in
    codex) echo "${BOSUN_CODEX_MODEL:--}" ;;
    *) echo "${BOSUN_MODEL:--}" ;;
  esac
}

# Every agent this setup uses, as one line of words: what preflight and `bosun trust` go through.
harnesses_in_use() {
  {
    [ -n "$BOSUN_ORCHESTRATOR" ] && echo "$ORCHESTRATOR_HARNESS"
    echo "$BOSUN_HARNESS"
    [ -f "$BOSUN_SESSIONS_FILE" ] && window_specs 2>/dev/null | cut -f6 | grep -v '^-$'
  } | awk '!seen[$0]++' | tr '\n' ' ' | sed 's/ $//'
}

# The check `bosun preflight`, `init` and `doctor` all start with. The agents' commands go along
# explicitly, since load_config does not export them.
run_preflight() { # [--quiet]
  BOSUN_HARNESSES="$(harnesses_in_use)" BOSUN_CLAUDE_BIN="$BOSUN_CLAUDE_BIN" BOSUN_CODEX_BIN="$BOSUN_CODEX_BIN" \
    sh "$BOSUN_ROOT/lib/preflight.sh" "$@"
}

# Ask an agent's profile (lib/harnesses/<harness>.sh) something.
from_profile() { # <harness> <function>
  BOSUN_CLAUDE_BIN="$BOSUN_CLAUDE_BIN" BOSUN_CODEX_BIN="$BOSUN_CODEX_BIN" \
    sh -c '. "$1"; "$2"' _ "$BOSUN_ROOT/lib/harnesses/$1.sh" "$2"
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
agent_pid_starting() { # <pane_pid>
  local pid command agents
  agents=" $(basename "$BOSUN_CLAUDE_BIN") $(basename "$BOSUN_CODEX_BIN") "
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
