# shellcheck shell=bash
# How `bosun launch` runs a window on Claude Code, in the functions every agent's module has. Sourced by
# libexec/bosun-launch, which holds the flow they serve.

# shellcheck disable=SC2034  # for run_agent, in the script that sources this
AGENT_BIN="$BOSUN_CLAUDE_BIN"

# The orchestrator has its own flags, settings, plugin and policy. With BOSUN_APPROVALS_ENABLED=1 also the
# approvals hooks, and for a repo session with BOSUN_EVENTS_ENABLED=1 the hook that records what you type.
# shellcheck disable=SC2154  # build_model_effort_args and set_autocompact_flag (lib/common.sh) set their arrays
build_agent_args() { # <window> <session name> <model> <effort>
  if is_orchestrator "$1"; then
    build_model_effort_args "$3" "$4" "$BOSUN_ORCHESTRATOR_SETTINGS" "$(orchestrator_extra_settings)"
    # shellcheck disable=SC2206  # the flags are words, on purpose
    agent_args=($BOSUN_ORCHESTRATOR_FLAGS "${model_effort_args[@]}"
      --plugin-dir "$BOSUN_ROOT/orchestrator/plugin"
      --append-system-prompt "$(orchestrator_policy)")
  else
    build_model_effort_args "$3" "$4" "" "$(repo_session_settings "$1")"
    # shellcheck disable=SC2206  # the flags are words, on purpose
    agent_args=($BOSUN_CLAUDE_FLAGS ${model_effort_args[@]+"${model_effort_args[@]}"})
  fi
  # BOSUN_AUTOCOMPACT_TOKENS: Claude Code compacts the conversation past that size by itself.
  set_autocompact_flag "$1"
  agent_args+=(${autocompact_flag[@]+"${autocompact_flag[@]}"} --name "$2" --remote-control "$1")
}

# What a new session starts with. BOSUN_ORCHESTRATOR_MORNING_CHORES=0: today's conversation starts idle.
first_prompt() { # <window> <prompt given, or empty>
  if [ -z "$2" ] && is_orchestrator "$1" && [ "$BOSUN_ORCHESTRATOR_MORNING_CHORES" = "1" ]; then
    echo "/bosun:chores morning"
  else
    printf '%s' "$2"
  fi
}

# Prompts go after `--`, so one that starts with "-" is not read as an option.
run_new() { # <prompt, or empty>
  if [ -n "$1" ]; then run_agent "${agent_args[@]}" -- "$1"; else run_agent "${agent_args[@]}"; fi
}

run_resumed() { # <session id> [prompt]
  if [ -n "${2:-}" ]; then
    run_agent "${agent_args[@]}" --resume "$1" -- "$2"
  else
    run_agent "${agent_args[@]}" --resume "$1"
  fi
}
