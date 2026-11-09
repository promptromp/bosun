# shellcheck shell=bash
# How `bosun launch` runs a window on Codex, in the functions every agent's module has. Sourced by
# libexec/bosun-launch, which holds the flow they serve. What it relies on in Codex: lib/codex_state.py.
# Design: docs/design/harnesses.md.

# shellcheck disable=SC2034  # for run_agent, in the script that sources this
AGENT_BIN="$BOSUN_CODEX_BIN"

# Sets codex_connector_args to the launch arguments that limit window <name>, launched in this folder, to
# its list (window_connectors; docs/design/connectors.md), as lib/connectors.py prints them: one
# `-c mcp_servers={<key>={enabled=false},...}` for the configured servers it does not name (none when there
# are none), `--disable plugins` when it does not name one of the plugins' servers, and `--disable apps`.
# None for `all`. Once per process: a launch builds its arguments twice, and the second time reuses the
# first lookup. A listing that fails, or arguments not of that shape, stop the launch: the window never
# starts with a server its list does not name. Its lines (a name Codex has no server by, a listing that
# failed) are logged as a Claude Code window's are (log_connector_lines). Call it in the shell itself, not
# in $(...), which would lose both the result and the die.
set_codex_connector_args() { # <name>
  [ "${codex_connector_window-}" = "$1" ] && return 0
  local list output line errors code=0
  list="$(window_connectors "$1")" || die "could not read the connectors of '$1' in $BOSUN_SESSIONS_FILE"
  errors="$(mktemp "${TMPDIR:-/tmp}/bosun-connectors.XXXXXX")" || die "'$1' does not start: no temporary file"
  # The list is its own argument, so one that starts with "-" is no flag.
  output="$(python3 "$BOSUN_ROOT/lib/connectors.py" codex --agent-bin "$BOSUN_CODEX_BIN" --list="$list" 2>"$errors")" ||
    code=$?
  log_connector_lines "$1" "$errors"
  rm -f "$errors"
  [ "$code" = 0 ] ||
    die "'$1' does not start: could not tell which servers its connectors switch off (lib/connectors.py, above)"
  codex_connector_args=()
  if [ -n "$output" ]; then
    while IFS= read -r line; do codex_connector_args+=("$line"); done <<<"$output"
  fi
  codex_connector_args_valid "$list" ||
    die "'$1' does not start: lib/connectors.py gave arguments not of the expected shape"
  codex_connector_window="$1"
}

# Whether codex_connector_args have the shape of what lib/connectors.py codex prints for <list>: nothing,
# for `all`; otherwise, in this order, an optional `-c` with an `mcp_servers={...}` table, an optional
# `--disable plugins`, then `--disable apps`, and nothing after. A shape check only, so that a missing or
# garbled answer is never taken for "no limit"; what the table holds is lib/connectors.py's (its tests read
# it back as TOML).
codex_connector_args_valid() { # <list>
  local n=${#codex_connector_args[@]} i=0
  [ "$n" -gt 0 ] || lists_everything "$1" || return 1
  [ "$n" -gt 0 ] || return 0
  if [ "${codex_connector_args[0]}" = -c ]; then
    case "${codex_connector_args[1]-}" in
      "mcp_servers={"*"}") i=2 ;;
      *) return 1 ;;
    esac
  fi
  codex_disables "$i" plugins && i=$((i + 2))
  codex_disables "$i" apps && [ "$n" = $((i + 2)) ]
}
codex_disables() { # <index> <feature>: codex_connector_args from <index> are `--disable <feature>`
  [ "${codex_connector_args[$1]-}" = --disable ] && [ "${codex_connector_args[$(($1 + 1))]-}" = "$2" ]
}

# What Codex's sandbox must let a session reach. It keeps a repo's .git read-only by default, so nothing
# could be committed; a task also goes through bosun's state, and the network for git and gh.
# What needs more than that (a login in the Keychain) goes past the sandbox through the reviewer.
# No update screen: once Codex has seen a newer version of itself, a start waits at one for a key, which
# stalls an unattended start and which the Enter `bosun send` presses would answer.
build_agent_args() { # <window> <session name> <model> <effort>
  local git_dir roots notify
  git_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  roots="$(jq -cn --arg state "$BOSUN_STATE_DIR" --arg logs "$BOSUN_LOG_DIR" --arg git "$git_dir" \
    '[$state, $logs, $git] | map(select(. != ""))')"
  notify="$(jq -cn --arg bosun "$BOSUN_CMD" --arg window "$1" --arg name "$2" \
    '[$bosun, "hook", "codex-turn-end", "--window", $window, "--conversation", $name]')"
  # shellcheck disable=SC2206  # the flags are words, on purpose
  agent_args=($BOSUN_CODEX_FLAGS
    -c "sandbox_workspace_write.writable_roots=$roots"
    -c "sandbox_workspace_write.network_access=true"
    -c "check_for_update_on_startup=false"
    -c "notify=$notify")
  [ -n "$3" ] && [ "$3" != "-" ] && agent_args+=(--model "$3")
  [ -n "$4" ] && [ "$4" != "-" ] && agent_args+=(-c "model_reasoning_effort=\"$4\"")
  # Here, in the launch's own shell: the listing runs once, and a failure stops the launch.
  set_codex_connector_args "$1"
  agent_args+=(${codex_connector_args[@]+"${codex_connector_args[@]}"})
  return 0
}

# Codex writes nothing until its first prompt, and a Codex with no session file looks like one stopped
# at a screen. So a new session always gets a prompt, this one if it has nothing to do yet.
first_prompt() { # <window> <prompt given, or empty>
  if [ -n "$2" ]; then
    printf '%s' "$2"
  else
    printf "This is bosun's %s window, with nothing to do yet. Reply with just: ready" "$1"
  fi
}

# Prompts go after `--`, so one that starts with "-" is not read as an option.
run_new() { run_agent "${agent_args[@]}" -- "$1"; } # <prompt>

run_resumed() { # <session id> [prompt]
  if [ -n "${2:-}" ]; then
    run_agent resume "${agent_args[@]}" "$1" -- "$2"
  else
    run_agent resume "${agent_args[@]}" "$1"
  fi
}
