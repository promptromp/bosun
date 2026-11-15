#!/bin/bash
# Usage: history.sh <exporter|pdf-renderer|ledger-sync> <destination>
#
# Builds the fictional acme repository <name> (tests/sandbox/live/<name>/, its files as they are today) in
# <destination> as a few commits by different people, dated in 2026, so that `git log` and `git blame` read
# like a team's work. The dates and authors are fixed: two runs make the same commits. The last commit takes
# whatever the folder holds that the earlier ones did not, so that the repository ends as the folder is. It
# makes no remote and pushes nothing: tests/sandbox/live.sh does that.
set -euo pipefail

repo="${1:?exporter, pdf-renderer or ledger-sync}"
dest="${2:?a folder to make the repository in}"
src="$(cd "$(dirname "$0")" && pwd)/$repo"
[ -d "$src" ] || {
  echo "no such repository: $repo" >&2
  exit 2
}

# The people, as `Name <mail>`: made up, with example.com addresses.
DANA="Dana Example|dana@example.com"
LEE="Lee Sample|lee@example.com"
MARCO="Marco Placeholder|marco@example.com"
LENA="Lena Placeholder|lena@example.com"
PRIYA="Priya Placeholder|priya@example.com"
SAM="Sam Placeholder|sam@example.com"

# stub <path> <text>: a first, shorter version of a file, which a later commit replaces with the real one.
stub() {
  mkdir -p "$dest/$(dirname "$1")"
  printf '%s\n' "$2" >"$dest/$1"
}

# commit <person> <date> <message> <path>...: copy the paths from the folder (`.`: all of it) and commit them.
commit() {
  local person="$1" date="$2" message="$3" path
  shift 3
  for path in "$@"; do
    if [ "$path" = . ]; then
      cp -R "$src/." "$dest/"
    else
      mkdir -p "$dest/$(dirname "$path")"
      cp -R "$src/$path" "$dest/$path"
    fi
  done
  find "$dest" -name __pycache__ -prune -exec rm -rf {} +
  git -C "$dest" add -A
  GIT_AUTHOR_NAME="${person%|*}" GIT_AUTHOR_EMAIL="${person#*|}" GIT_AUTHOR_DATE="${date}T10:15:00+0000" \
    GIT_COMMITTER_NAME="${person%|*}" GIT_COMMITTER_EMAIL="${person#*|}" GIT_COMMITTER_DATE="${date}T10:15:00+0000" \
    git -C "$dest" -c commit.gpgsign=false commit -q -m "$message"
}

exporter() {
  stub README.md "# exporter"
  stub CODEOWNERS "* @acme/billing"
  stub exporter/__init__.py '"""A small invoice exporter."""

from exporter.totals import invoice_total

__all__ = ["invoice_total"]'
  commit "$LEE" 2026-02-09 "Invoice totals, with tax rounded per line" \
    .gitignore exporter/totals.py tests/__init__.py tests/test_totals.py
  commit "$DANA" 2026-03-03 "Tax rates by country, rounded half up" exporter/tax.py tests/test_tax.py
  commit "$LEE" 2026-04-14 "CSV export of issued invoices" \
    exporter/__init__.py exporter/csv_export.py tests/test_csv_export.py
  commit "$PRIYA" 2026-05-20 "Credit notes: negative lines, numbered in the customer's invoice sequence" \
    exporter/credit_notes.py tests/test_credit_notes.py
  commit "$MARCO" 2026-06-30 "An integration check, and the environments it may run against" \
    config/environments.toml scripts/integration_check.py tests/test_integration_check.py
  commit "$LENA" 2026-08-11 "Runbook: release steps, the canary sign-off, hotfixes" RUNBOOK.md README.md
  commit "$DANA" 2026-09-22 "CODEOWNERS: tax rules are reviewed by Dana" CODEOWNERS .
}

pdf_renderer() {
  stub README.md "# pdf-renderer"
  stub renderer/__init__.py '"""Renders invoices for print."""'
  commit "$MARCO" 2026-01-12 "VAT breakdown per rate, rounded per line" \
    .gitignore renderer/vat_breakdown.py tests/__init__.py tests/test_vat_breakdown.py
  commit "$SAM" 2026-02-24 "Page layout, and render(invoice) for the exporter" \
    renderer/__init__.py renderer/layout.py tests/test_layout.py
  commit "$MARCO" 2026-05-05 "CODEOWNERS" CODEOWNERS
  commit "$MARCO" 2026-07-14 "README: the exporter calls render(invoice)" README.md .
}

ledger_sync() {
  stub README.md "# ledger-sync"
  commit "$SAM" 2026-03-16 "Map invoices to ledger entries" \
    .gitignore sync/__init__.py sync/mapping.py tests/__init__.py tests/test_mapping.py
  commit "$PRIYA" 2026-04-27 "Client: retry with backoff when the ledger answers 429" sync/client.py tests/test_client.py
  commit "$LENA" 2026-09-14 "Runbook: sync is paused during the month-end freeze" RUNBOOK.md
  commit "$SAM" 2026-09-15 "CODEOWNERS: platform owns the sync; README" CODEOWNERS README.md .
}

[ ! -e "$dest/.git" ] || {
  echo "$dest is a repository already" >&2
  exit 2
}
mkdir -p "$dest"
git -C "$dest" init -q -b main
case "$repo" in
  exporter) exporter ;;
  pdf-renderer) pdf_renderer ;;
  ledger-sync) ledger_sync ;;
  *)
    echo "no such repository: $repo" >&2
    exit 2
    ;;
esac
