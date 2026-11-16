#!/bin/sh
# Parses the shell scripts it is given with macOS's own /bin/bash, which is 3.2 (the pre-commit hook of
# .pre-commit-config.yaml). bosun's scripts run under it on every Mac, and neither shellcheck nor a newer bash
# catches all of what 3.2 cannot parse: a script that once passed both failed on a Mac. Where /bin/bash is
# absent or is not 3.2 (CI's Linux lint job), it says so in one line and passes.
#
#   tools/bash32_hook.sh <script>...
set -eu

if [ ! -x /bin/bash ]; then
  echo "no /bin/bash here: skipping the bash 3.2 parse (macOS's /bin/bash is 3.2)"
  exit 0
fi
# shellcheck disable=SC2016  # the expansion is for /bin/bash to make, not this shell
version=$(/bin/bash -c 'echo "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"' 2>/dev/null || true)
if [ "$version" != "3.2" ]; then
  echo "/bin/bash is ${version:-unknown}, not 3.2: skipping the bash 3.2 parse (a Mac runs it)"
  exit 0
fi
failed=0
for script in "$@"; do
  if ! /bin/bash -n "$script"; then
    failed=1
  fi
done
exit "$failed"
