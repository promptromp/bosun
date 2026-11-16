# exporter

A small invoice exporter, entirely fictional. It totals invoices, applies tax by country, issues credit notes
and exports the result as CSV for the customer's accounting system. Renderings of an invoice as a PDF come
from the `pdf-renderer` repository, which calls into this one.

- `exporter/totals.py`: invoice totals. Tax is rounded per line, not per invoice.
- `exporter/tax.py`: tax rates by country, and the rounding of a tax amount.
- `exporter/credit_notes.py`: credit notes, with negative amounts, numbered in their customer's invoice sequence.
- `exporter/csv_export.py`: the CSV export. Drafts are left out.
- `scripts/integration_check.py --env <name>`: the integration check, against an environment of
  `config/environments.toml`.

Run the tests with `python3 -m unittest`. Releasing and hotfixes: `RUNBOOK.md`. Owners: `CODEOWNERS`.
