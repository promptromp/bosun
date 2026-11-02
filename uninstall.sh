#!/bin/bash
# Take bosun off this Mac, from the checkout: the same as `bosun uninstall`, whose --help says how it works.
exec "$(dirname "$0")/bin/bosun" uninstall "$@"
