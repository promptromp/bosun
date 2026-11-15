# Ledger sync runbook

## The nightly sync

The scheduler runs `sync` at 02:00 UTC and posts every exported invoice and credit note to the ledger API.
The ledger answers 429 when it is busy: the client waits and retries, so a slow night is not a failed one.

## Month-end freeze

Sync is paused during the month-end freeze: finance closes the books and nothing may post to them. Platform
pauses the scheduler's job on the last business day of the month and resumes it the next morning. A sync that
ran into the freeze is not retried by hand; ask #platform.

## When a night fails

Look at the scheduler's log for the first non-429 error. A mapping error (a country without an account) is
fixed in `sync/mapping.py`; the next night's run picks up the invoices that were left.
