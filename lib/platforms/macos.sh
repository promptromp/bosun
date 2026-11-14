# shellcheck shell=sh
# The macOS profile for lib/preflight.sh. A platform is one file like this, named for its BOSUN_PLATFORM,
# and only says how this OS does things. The interface is in docs/design/init.md.

platform_name() { echo "macOS"; }

# What must be in place before anything else can even be asked. Without Apple's command line tools,
# the first `git` or `python3` opens a dialog instead of answering.
platform_base_ready() { xcode-select -p >/dev/null 2>&1; }
platform_base_name() { echo "Apple's command line tools"; }
platform_base_help() { echo "Install Homebrew, which brings Apple's command line tools: https://brew.sh"; }

platform_manager_name() { echo "Homebrew"; }
platform_manager_ready() { command -v brew >/dev/null 2>&1; }
platform_manager_help() { echo "Install Homebrew: https://brew.sh"; }
platform_install_command() { echo "brew install $*"; }

# Whether the login keychain is locked for this session: an ssh session starts with it locked, whatever the
# desktop unlocked, and an agent that keeps its login there (Claude Code) then reads as logged out.
# Asked without reading anything in it; no `security`, or any other answer, is not locked.
platform_keychain_locked() {
  command -v security >/dev/null 2>&1 || return 1
  case "$(security show-keychain-info "$HOME/Library/Keychains/login.keychain-db" 2>&1 >/dev/null)" in
    *"User interaction is not allowed"*) return 0 ;;
  esac
  return 1
}

# The package that provides a tool, where the two names differ.
platform_package() {
  case "$1" in
    python3) echo python ;;
    *) echo "$1" ;;
  esac
}
