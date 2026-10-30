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

# The package that provides a tool, where the two names differ.
platform_package() {
  case "$1" in
    python3) echo python ;;
    *) echo "$1" ;;
  esac
}
