# Migration fixtures

One fixture per migration in `lib/migrations.py`, in the format it migrates from, named
`<store>-<version>` (`ledger-1.json`: a `ledger.json` in format 1). Its test runs the migration on
a copy, twice, and then the store's own check (`validate_unlocked`). Fictional data only.

A fixture with a journal is a folder of the store's files. A migration of a store that has a
journal never edits its files in place: it rebuilds them from the journal and stamps them, as a
write saves them (an edit in place is a change outside the journal, which validate reports). Only a
store from before journals, such as `ledger-1.json`, is converted in place.
