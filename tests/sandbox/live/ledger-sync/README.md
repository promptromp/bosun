# ledger-sync

Posts the exporter's invoices and credit notes to the ledger, entirely fictional.

- `sync/client.py`: the ledger API client. It retries with backoff when the ledger answers 429.
- `sync/mapping.py`: how an invoice becomes a ledger entry (whole cents, credit notes negative).

It depends on the exporter's output (invoices and credit notes in its format). Run the tests with
`python3 -m unittest`. What to do when a night fails, and the month-end pause: `RUNBOOK.md`. Owners: `CODEOWNERS`.
