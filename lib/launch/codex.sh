# shellcheck shell=bash
# How `bosun launch` runs a window on Codex, in the functions every agent's module has. Sourced by
# libexec/bosun-launch, which holds the flow they serve. What it relies on in Codex: lib/codex_state.py.
# Design: docs/design/harnesses.md.

# shellcheck disable=SC2034  # for run_agent, in the script that sources this
AGENT_BIN="$BOSUN_CODEX_BIN"

# What Codex's sandbox must let a session reach. It keeps a repo's .git read-only by default, so nothing
# could be committed; a task also goes through bosun's state, and the network for git and gh.
# What needs more than that (a login in the Keychain) goes past the sandbox through the reviewer.
build_agent_args() { # <window> <session name> <model> <effort>
  local git_dir roots notify
  git_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  roots="$(jq -cn --arg state "$BOSUN_STATE_DIR" --arg logs "$BOSUN_LOG_DIR" --arg git "$git_dir" \
    '[$state, $logs, $git] | map(select(. != ""))')"
  notify="$(jq -cn --arg bosun "$BOSUN" --arg window "$1" --arg name "$2" \
    '[$bosun, "hook", "codex-turn-end", "--window", $window, "--conversation", $name]')"
  # shellcheck disable=SC2206  # the flags are words, on purpose
  agent_args=($BOSUN_CODEX_FLAGS
    -c "sandbox_workspace_write.writable_roots=$roots"
    -c "sandbox_workspace_write.network_access=true"
    -c "notify=$notify")
  [ -n "$3" ] && [ "$3" != "-" ] && agent_args+=(--model "$3")
  [ -n "$4" ] && [ "$4" != "-" ] && agent_args+=(-c "model_reasoning_effort=\"$4\"")
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
