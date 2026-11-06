# Migration fixtures

One fixture per migration in `lib/migrations.py`, in the format it migrates from, named
`<store>-<version>` (`ledger-1.json`: a `ledger.json` in format 1). Its test runs the migration on
a copy, twice, and then the store's own check (`validate_unlocked`). Fictional data only.
