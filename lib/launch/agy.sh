# shellcheck shell=bash
# How `bosun launch` runs a window on Google's Antigravity CLI (agy), in the functions every agent's module
# has. Sourced by libexec/bosun-launch, which holds the flow they serve. What it relies on in agy:
# lib/agy_state.py. Design: docs/design/antigravity.md.

# shellcheck disable=SC2034  # for run_agent, in the script that sources this
AGENT_BIN="$BOSUN_AGY_BIN"

# Checks, once per process, that window <name>, launched in this folder, reaches no MCP server beyond its list
# (window_connectors; docs/design/connectors.md). agy takes no limit per launch, and switching a server off in
# its shared configuration would race between windows and change the person's Antigravity app, so the window
# starts only if lib/connectors_agy.py finds each server it would start named in the list, or the list `all`. A
# refusal, and a listing that fails, stop the launch; its lines (a server the list does not name, a name agy
# has no server by) are logged as a Claude Code window's are (log_connector_lines). Call it in the shell itself,
# not in $(...), which would lose the die. A launch builds its arguments twice, and the second time reuses the
# first lookup.
check_agy_connectors() { # <name>
  [ "${agy_connector_window-}" = "$1" ] && return 0
  local list errors code=0
  list="$(window_connectors "$1")" || die "could not read the connectors of '$1' in $BOSUN_SESSIONS_FILE"
  errors="$(mktemp "${TMPDIR:-/tmp}/bosun-connectors.XXXXXX")" || die "'$1' does not start: no temporary file"
  # The list is its own argument, so one that starts with "-" is no flag.
  python3 "$BOSUN_ROOT/lib/connectors_agy.py" --agent-bin "$BOSUN_AGY_BIN" --list="$list" --cwd "$PWD" \
    2>"$errors" || code=$?
  log_connector_lines "$1" "$errors"
  rm -f "$errors"
  [ "$code" = 0 ] ||
    die "'$1' does not start: it would reach servers beyond its connectors, or they could not be listed (lib/connectors_agy.py, above)"
  agy_connector_window="$1"
}

# Exports what makes a git or gh command run by an agy window safe and unattended, for agy and whatever it
# starts (the launch's own commands after it are unaffected in effect). Git's environment config
# (GIT_CONFIG_COUNT, GIT_CONFIG_KEY_<n>, GIT_CONFIG_VALUE_<n>) outranks every repo's config file and reaches
# the processes git starts in submodules, so a repo cannot undo any of these by what it commits. They are
# appended after the pairs the environment already holds, so those stay. A launch builds its arguments twice,
# so the count is read once: the second time the same pairs are written again, never added.
#   safe.bareRepository=explicit   a bare repository inside the working tree is no repository, so a command
#                                  cannot be made to run in, and take its config from, one the agent wrote
#   core.fsmonitor=false           no program a repo names runs when git looks at its files
#   core.hooksPath=/dev/null       repo hooks are off in agy windows (my decision, 2026-11-12): an approved
#                                  commit or push would otherwise run the repo's own scripts outside agy's
#                                  sandbox, which only bosun's plugin lifts for these commands
#   core.editor, sequence.editor   a window has no person at an editor, so one that would open is an error
#   core.pager=cat                 and none at a pager, which would wait for a key
#   submodule.recurse, fetch.recurseSubmodules, diff.ignoreSubmodules
#                                  a command never reaches into a submodule, whose repository the agent could
#                                  have written
# GIT_EDITOR and GIT_PAGER say the same to git, GH_EDITOR and GH_PAGER to gh, and GH_PROMPT_DISABLED makes gh
# fail where it would ask a question.
set_agy_git_environment() {
  local pair i=0
  if [ -z "${agy_git_config_base+x}" ]; then
    agy_git_config_base="${GIT_CONFIG_COUNT:-0}"
    # Only 0, or a number with no leading zero ($((...)) would read 08 as octal): git refuses any other.
    case "$agy_git_config_base" in '' | *[!0-9]* | 0?*) agy_git_config_base=0 ;; esac
  fi
  for pair in \
    safe.bareRepository=explicit \
    core.fsmonitor=false \
    core.hooksPath=/dev/null \
    core.editor=false \
    sequence.editor=false \
    core.pager=cat \
    submodule.recurse=false \
    fetch.recurseSubmodules=false \
    diff.ignoreSubmodules=all; do
    export "GIT_CONFIG_KEY_$((agy_git_config_base + i))=${pair%%=*}"
    export "GIT_CONFIG_VALUE_$((agy_git_config_base + i))=${pair#*=}"
    i=$((i + 1))
  done
  export GIT_CONFIG_COUNT=$((agy_git_config_base + i))
  export GIT_EDITOR=false GIT_PAGER=cat GH_EDITOR=false GH_PAGER=cat GH_PROMPT_DISABLED=1
}

# What agy's environment gives bosun's plugin, for its hooks (the adapter never reads a process's environment:
# it knows a window by the `--log-file` in its command line, and by bosun's own status file, below):
# BOSUN_WINDOW, BOSUN_CONVERSATION (the name its conversation goes by, which `resolve` looks up),
# BOSUN_HOOK_CMD (what the plugin's hooks run: the install link, which outlives this version, and without
# which they do nothing), and agy's own updates off, which the morning run does at a quiet moment instead.
# agy reads its login from the Keychain only when neither SSH_CLIENT nor SSH_CONNECTION is set, which a tmux
# server started from an ssh session would carry.
set_agy_environment() { # <window> <session name>
  agy_window="$1"
  agy_conversation="$2"
  export BOSUN_WINDOW="$1" BOSUN_CONVERSATION="$2" BOSUN_HOOK_CMD="$BOSUN_CMD" AGY_CLI_DISABLE_AUTO_UPDATE=true
  unset SSH_CLIENT SSH_CONNECTION BOSUN_RESUMED # a new conversation resumes nothing
  set_agy_git_environment
}

# <model> without the effort agy's model names end in (gemini-3.8-flash-low), which --effort carries.
agy_base_model() {
  local model="$1" suffix
  for suffix in -low -medium -high; do
    model="${model%"$suffix"}"
    [ "$model" = "$1" ] || break
  done
  printf '%s' "$model"
}

# Sets agent_args. Every bosun flag is here, and the prompt is not: the adapter reads the marker from the
# flags before the first --prompt-interactive (run_new and run_resumed put it last).
#   --settings   auto mode, and this folder, as given, trusted for the session. `--mode auto` is dropped at
#                startup, and a trust screen would stop an unattended start. Never saved by agy.
#   --log-file   named for the conversation: the adapter's marker, which also keeps agy from adding a log of
#                its own under ~/.gemini at every start.
# No --add-dir, -c, --mode or --dangerously-skip-permissions, ever. An effort above high is passed as high:
# agy switched a model to another on xhigh.
build_agent_args() { # <window> <session name> <model> <effort>
  local settings effort="$4"
  set_agy_environment "$1" "$2"
  settings="$(jq -cn --arg dir "$PWD" '{agentMode: "auto", trustedWorkspaces: [$dir]}')" ||
    die "'$1' does not start: could not make agy's settings"
  # shellcheck disable=SC2206  # the flags are words, on purpose
  agent_args=($BOSUN_AGY_FLAGS
    --settings "$settings"
    --log-file "$BOSUN_LOG_DIR/agy-$2.log"
    --remote-control)
  [ -n "$3" ] && [ "$3" != "-" ] && agent_args+=(--model "$(agy_base_model "$3")")
  case "$effort" in xhigh | max) effort=high ;; esac
  [ -n "$effort" ] && [ "$effort" != "-" ] && agent_args+=(--effort "$effort")
  # Here, in the launch's own shell: the listing runs once, and a refusal stops the launch.
  check_agy_connectors "$1"
  return 0
}

# agy writes nothing until its first prompt, and one with no conversation yet looks like a window that never
# started. So a new conversation always gets a prompt, this one if it has nothing to do yet.
first_prompt() { # <window> <prompt given, or empty>
  if [ -n "$2" ]; then
    printf '%s' "$2"
  else
    printf "This is bosun's %s window, with nothing to do yet. Reply with just: ready" "$1"
  fi
}

# What a launch does just before it hands over to agy. In a dry run it says what it would set, as
# `dry-run: would set:` and each NAME=value: the marker, the git environment. Otherwise it records the version
# agy starts on, and forgets what the last run of the window left (lib/agy_state.py launched), so that a
# relaunch is never taken for the run before it. It also records the window's tmux pane and the server's
# socket (the path in $TMUX before its first comma), which is where the adapter looks at the window's screen;
# none outside tmux. It records the window's folder too, which names its conversation when agy's SessionStart
# hook gives none. The status is written only here, never while the arguments
# are built, which `--fingerprint` and the window list do for a window that keeps running.
note_agy_launch() {
  local var version i key tmux_socket
  if [ "$BOSUN_LAUNCH_DRY_RUN" = "1" ]; then
    printf 'dry-run: would set:'
    for var in BOSUN_WINDOW BOSUN_CONVERSATION BOSUN_HOOK_CMD BOSUN_RESUMED AGY_CLI_DISABLE_AUTO_UPDATE \
      GIT_EDITOR GIT_PAGER GH_EDITOR GH_PAGER GH_PROMPT_DISABLED GIT_CONFIG_COUNT; do
      [ -n "${!var+x}" ] && printf ' %s=%q' "$var" "${!var}"
    done
    for ((i = agy_git_config_base; i < GIT_CONFIG_COUNT; i++)); do
      for key in GIT_CONFIG_KEY_$i GIT_CONFIG_VALUE_$i; do printf ' %s=%q' "$key" "${!key}"; done
    done
    printf '\n'
    return 0
  fi
  version="$(agent_installed_version agy)"
  tmux_socket="${TMUX:-}"
  tmux_socket="${tmux_socket%%,*}"
  env BOSUN_STATE_DIR="$BOSUN_STATE_DIR" python3 "$BOSUN_ROOT/lib/agy_state.py" launched "$agy_conversation" "$version" \
    "${TMUX_PANE:-}" "$tmux_socket" "$PWD" ||
    die "'$agy_window' does not start: could not record its launch (lib/agy_state.py launched)"
}

# The prompt is the last word, and `--prompt-interactive=<prompt>` is one argument, so a prompt that starts
# with "-" is no option.
run_new() { # <prompt>
  note_agy_launch
  run_agent "${agent_args[@]}" "--prompt-interactive=$1"
}

# agy resumes only by the conversation's id, before the prompt. It starts a fresh conversation under an unknown
# id without failing, so it also says which id it was asked for: BOSUN_RESUMED, which bosun's hook checks at
# the window's first model call.
run_resumed() { # <conversation id> [prompt]
  export BOSUN_RESUMED="$1"
  note_agy_launch
  if [ -n "${2:-}" ]; then
    run_agent "${agent_args[@]}" --conversation "$1" "--prompt-interactive=$2"
  else
    run_agent "${agent_args[@]}" --conversation "$1"
  fi
}
