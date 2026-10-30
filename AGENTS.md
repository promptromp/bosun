# Working on bosun

Whichever agent you are, read [CLAUDE.md](CLAUDE.md): where things live, a checklist for each kind of
change, the checks, the gotchas. It is the one set of instructions for people and agents here, so this
file only points at it.

The two rules that matter most before you run anything:

- **Never test an install on the machine you are running on.** Use `tests/sandbox.sh` (Docker, in
  seconds) or `tests/sandbox.sh mac` (a real throwaway Mac). See
  [docs/development.md](docs/development.md#which-sandbox-for-what).
- **Nothing that identifies an organization or a person goes into this repo,** also not in tests,
  fixtures or commit messages. See "Publishing" in CLAUDE.md.
