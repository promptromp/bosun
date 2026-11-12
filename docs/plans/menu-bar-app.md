# Menu bar app, part 2: bosun.app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** bosun.app: a menu bar app that shows bosun's state, checks for and runs updates, opens the console, quits
and starts bosun with the person's choices, built in CI, signed with the publisher's Developer ID and notarized, and
proven on a real Mac in the Tart VM.

**Architecture:** A Swift package in `app/`. `BosunMenuCore` is pure (the JSON contract's types, the menu each state
becomes, the update and quit flows as state machines, the `bosun` command each action runs) and carries every test;
`BosunMenu` is the AppKit shell (`NSStatusItem`, `NSMenu`, `NSAlert`) that renders the core's models and runs
`bosun` through one `BosunCLI` protocol. The app reads nothing of bosun's but what part 1's commands print.

**Tech Stack:** Swift 6 (Swift Testing), AppKit, SwiftPM, `codesign`/`notarytool`/`stapler`, GitHub Actions on macOS.

**Spec:** `docs/design/menu-bar.md`; part 1's plan `docs/plans/menu-bar-cli.md` (its interfaces are this plan's
inputs).

## Global Constraints

- The app runs `bosun` only through `~/.local/bin/bosun`, and only the commands part 1 defines: `status --json`,
  `update --check --json`, `update --dry-run --json`, `update --yes`, `update --skip <v>`, `quit --plan --json`, `quit
  …`, `morning --now`, `console --url`, `app status --json`, `app login on|off`. It never opens bosun's files.
- It understands `"contract": 1`; any other number shows "bosun.app needs an update" with "Update bosun…".
- No privacy permissions: no notifications, accessibility, automation, screen recording. Terminal is reached by opening
  a `.command` file with `NSWorkspace`, never by AppleScript.
- `LSUIElement`, not sandboxed, hardened runtime when signed, minimum macOS 14, Apple silicon.
- The bundle identifier comes from `app/publisher.json`, the version from `app/VERSION`; nothing else names them.
- Long-running processes are bosun's launchd jobs, never the app's children: the app starts a command and lets it run
  (`bosun morning --now` and `bosun update --yes` are started detached, their progress read back from `status`).
- Test hooks (`--test-hooks <dir>`) exist only for the VM suite, and only when the app is launched with that argument.
- Swift files pass `swift format lint --strict` (the toolchain's swift-format) with a committed `.swift-format`.
- The repo's other limits as usual; docs in the same pull request.

## Review Focus

1. bosun not installed, or `~/.local/bin/bosun` broken (an update cut short): the menu says so ("bosun is not
   installed" / "bosun cannot be reached") and offers nothing that runs it, never a crash or a frozen menu. Pinned in
   Task 1 (`statusFromFailedCommand`).
2. A `bosun` command that hangs: every call has a timeout (status 15 s, check 60 s, plan 30 s), after which the menu
   shows the last state with "(not current)". Pinned in Task 2 (`slowCommandTimesOut`).
3. The Mac waking from sleep: the status refreshes at once (`NSWorkspace.didWakeNotification`), not after 30 s. Pinned in
   Task 2.
4. Two Quits at once (⌘Q twice, or Quit while an update dialog is open): one flow at a time; the second is ignored.
   Pinned in Task 1 (`oneFlowAtATime`).
5. Launched from Spotlight while already running: the running copy shows nothing new and starts nothing twice. Pinned in
   Task 5 (VM).

---

### Task 1: The core

**Files:**

- Create: `app/Package.swift`, `app/.swift-format`, `app/Sources/BosunMenuCore/{Contract.swift, MenuModel.swift,
  UpdateFlow.swift, QuitFlow.swift, BosunCLI.swift}`, `app/Tests/BosunMenuCoreTests/*.swift`
- Create: `tools/contract_samples.py` (writes `app/Tests/BosunMenuCoreTests/Fixtures/*.json` from part 1's pure
  functions), `tests/unit/test_contract_samples.py` (the committed fixtures equal what the tool writes)

**Interfaces:**

- Produces: `struct StatusSnapshot: Decodable` (part 1's `bosun status --json`), `UpdateCheck`, `UpdatePlan` (the
  fields of `update --dry-run --json` the dialog shows: windows that restart, what needs you, blocked and why),
  `QuitPlan`, `AppStatus`.
- Produces: `func menu(for status: StatusState, update: UpdateState, flow: FlowState) -> MenuModel` where
  `StatusState` is `.loaded(StatusSnapshot, current: Bool)`, `.unreachable(String)`, `.notInstalled`,
  `.contractTooNew(Int)`; `MenuModel` is sections of `MenuItem(id: ItemID, title: String, enabled: Bool, key:
  String?, badge: Bool, children: [MenuItem])`; item ids: `startBosun, showProblems, openConsole, checkForUpdates,
  openAtLogin, help(guide|doctor|logs|uninstall), quit`. Titles and states exactly as `docs/design/menu-bar.md` and the
  mockups: the status line's dot colour per level, "Show What's Wrong…" only on warning/error, "Start bosun" first on
  error/stopped, Open Console disabled while stopped, Quit without "…" while stopped.
- Produces: `enum UpdateState { idle, checking, available(UpdateCheck), planning, confirming(UpdatePlan), running(waitingFor:
  [String]), failed(String) }` and `func next(_ state: UpdateState, on event: UpdateEvent) -> (UpdateState,
  [Command])` (pure transitions; commands are the `bosun` argv to run).
- Produces: `struct QuitChoices { pauseMorning = true, sleep = true, keepIcon = false, busy: BusyChoice }` and
  `func quitCommand(_ c: QuitChoices) -> [String]` (`["quit", "--let-finish" | "--stop-now", "--pause-morning"?,
  "--sleep"?, "--json"]`); `enum QuitStep { first(QuitPlan), busy([String]), stillBusy([String]), done, cancelled }`
  with the flow of option B: the first dialog's choices; a second only when the plan has busy agents; on exit 3 the
  "still busy" dialog (Keep Waiting / Stop Them Now / Cancel Quit).
- Produces: `protocol BosunCLI { func run(_ args: [String], timeout: Duration) async -> CommandResult }` and a
  `FakeBosunCLI` for tests.

- [ ] **Step 1: Tests** (Swift Testing): each fixture level → its menu (title, dot, item order, enabled states);
  `statusFromFailedCommand`; `contractTooNew`; each update transition; each quit path (no busy; busy then finish; busy
  then still busy then stop; cancel at each step); `oneFlowAtATime`; the quit argv for each choice set.
- [ ] **Step 2: Run** `cd app && swift test` to see them fail.
- [ ] **Step 3: Implement** the core; `tools/contract_samples.py` and its Python test keep the fixtures true to part 1.
- [ ] **Step 4: Run** `swift test`, `swift format lint --strict -r Sources Tests`, the Python test.
- [ ] **Step 5: Commit** ("bosun.app: the core — the contract, the menu, the update and quit flows").

### Task 2: The AppKit app

**Files:**

- Create: `app/Sources/BosunMenu/{main.swift, AppDelegate.swift, StatusItemController.swift, MenuRenderer.swift,
  Poller.swift, Dialogs.swift, ProcessBosunCLI.swift, Actions.swift, TestHooks.swift}`
- Test: `app/Tests/BosunMenuCoreTests` (what the shell's pieces can be tested without AppKit: the poller's timing with a
  clock, the CLI's timeout against a fake process)

**Interfaces:**

- Consumes: Task 1's models.
- Produces: the status item (the template icon; a small dot drawn over it for warning, error or an update available;
  none when ok or stopped); the menu rendered from `MenuModel`, rebuilt in place on each refresh, refreshed on
  `menuWillOpen` and every 30 s and on wake; the dialogs as `NSAlert`s with accessory views (the quit options as
  checkboxes, the update notes and plan in a scrolling text view), shown with the app briefly made a regular app so the
  alert comes to the front, then back to accessory.
- Produces: actions — Open Console (`console --url` → `NSWorkspace.open`); Start bosun (`morning --now`, detached);
  Check for Updates (the update flow); Open at Login (`app login on|off`); Help: the guide (the docs site URL), Run
  Doctor and Uninstall bosun… (a `.command` file in the app's temporary folder running `bosun doctor` or `bosun
  uninstall`, opened with `NSWorkspace`), Show Logs (the log folder, from `bosun status --json`'s `logs` path — part 1
  adds that field); Quit (the quit flow; after it, the app terminates unless "Keep bosun in the menu bar").
- Produces: launch behaviour — with `--at-login`: show the icon only; otherwise, when the status is `stopped` or
  `error`, run Start once; a second launch hands over to the running copy (macOS does this for an app bundle).
- Produces: `--test-hooks <dir>`: after each render, write `<dir>/menu.json` (the `MenuModel` as JSON, with the status
  level); poll `<dir>/actions/` every second for `<n>.json` files (`{"item": "<id>"}`, `{"answer": {...}}` for the
  open dialog: which button, which boxes) and perform them as if clicked; log each URL it would open and each command
  it runs to `<dir>/log.jsonl`. Absent the argument, none of this exists at run time.

- [ ] **Step 1: Tests** for the poller and the CLI timeout (`slowCommandTimesOut`), and the wake refresh.
- [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `swift test`, `swift build -c release`, the format lint.
- [ ] **Step 5: Commit** ("bosun.app: the menu bar app over the core").

### Task 3: The bundle, its icons, and `bosun app install --build`

**Files:**

- Create: `app/build.sh`, `app/Resources/Info.plist.in`, `tools/app_icons.py` (from `docs/assets/logo.png`: the app
  icon set for `iconutil`, and the menu bar template image — black on clear, 18×18 and 36×36 — as committed PNG files),
  `app/Resources/AppIcon.iconset/*`, `app/Resources/MenuBarIcon.png`, `app/Resources/MenuBarIcon@2x.png`
- Modify: `libexec/bosun-app` and `lib/app_install.py` (part 1: `--build` now builds), `.gitignore` (`app/.build`,
  `app/dist`)
- Test: `tests/unit/test_app_icons.py` (the template is single-colour with alpha), `tests/smoke.sh` (with a fake `swift`)

**Interfaces:**

- Produces: `app/build.sh [--identity <name>|-] [--out <dir>]`: `swift build -c release --arch arm64`; assembles
  `<out>/bosun.app` (`Contents/MacOS/bosun`, `Contents/Resources/{AppIcon.icns, MenuBarIcon*.png}`, `Info.plist` from
  the template with `CFBundleIdentifier` from `app/publisher.json`, `CFBundleShortVersionString`/`CFBundleVersion`
  from `app/VERSION`, `LSUIElement`, `LSMinimumSystemVersion` 14.0); signs the whole bundle (`codesign --force --sign
  <identity>`, plus `--options runtime --timestamp` when not ad hoc); verifies (`codesign --verify --strict`); writes
  `<out>/bosun-app-<version>.zip` with `ditto -c -k --keepParent`.
- Produces: `bosun app install --build`: runs `app/build.sh` ad hoc into a temporary folder and installs it without the
  team check, saying it is a development build.

- [ ] **Step 1: Tests.**  - [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement**; generate the icons
  once with the tool and commit them.
- [ ] **Step 4: Run** `app/build.sh` on this Mac (it builds; nothing is installed or launched here), the tests.
- [ ] **Step 5: Commit** ("bosun.app: its bundle, its icons, and a development build").

### Task 4: CI, and the signed release

**Files:**

- Modify: `.github/workflows/ci.yml` (job `app`), `.github/rulesets/main.json` (`app` required), `.pre-commit-config.yaml`
  (`swift format lint` when `swift` is present; the `app` job runs it in CI)
- Create: `.github/workflows/release-app.yml`, `docs/development.md` section "Releasing bosun.app"

**Interfaces:**

- Produces: CI job `app` (macos-latest): `swift test`, `swift format lint --strict`, `app/build.sh` ad hoc, and
  `codesign --verify` of the result, on every pull request.
- Produces: `release-app.yml` on a tag `app-v<semver>` matching `app/VERSION`: the certificate imported into a throwaway
  keychain (never the login keychain; deleted at the end), `app/build.sh --identity "$MACOS_SIGN_IDENTITY"`,
  `xcrun notarytool submit --wait` with the App Store Connect key (written to a 0600 file in `$RUNNER_TEMP`, removed
  after), the status read from `notarytool info` (never inferred), `xcrun stapler staple`, `spctl --assess --type
  execute`, the zip re-made after stapling, and a GitHub release with the zip. Secrets: `MACOS_CERTIFICATE`, <!-- pragma: allowlist secret -->
  `MACOS_CERTIFICATE_PASSWORD`, `MACOS_SIGN_IDENTITY`, `APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER_ID`; the <!-- pragma: allowlist secret -->
  team id from `app/publisher.json`. The job runs in a GitHub environment `release` so the person can require approval.
- Produces: the development guide's steps for the person: the `gh secret set` commands (values from the person's own
  files; never pasted into a chat), the environment, filling `team_id` in `app/publisher.json`, and cutting `app-v0.1.0`.

- [ ] **Step 1: Write the workflows** (actionlint and zizmor pass; actions pinned by commit).
- [ ] **Step 2: Apply the ruleset change** with the documented `gh api -X PUT …` once the job exists on main (after
  merge).
- [ ] **Step 3: Commit** ("bosun.app: built and tested on every pull request; signed and notarized on release").

### Task 5: The app proven on a real Mac

**Files:**

- Create: `tests/app_e2e.sh` (run in the VM; uses `tests/sandbox/e2e_lib.sh`)
- Modify: `tests/sandbox/mac.sh` (`app [--from release|build]`: copies the built app or fetches the release),
  `tests/sandbox.sh` (usage), `copy_checkout`'s file list, `docs/development.md` ("Which sandbox for what")

**Interfaces:**

- Produces: `tests/sandbox.sh mac app [--from build|release]` (`build`, the default: `app/build.sh` on the host, ad hoc,
  copied in; `release`: the published notarized zip downloaded in the VM with `curl`, as a person would), running
  `tests/app_e2e.sh` in a fresh clone of the developer's Mac, which checks, through the test hooks' `menu.json`,
  `actions/` and `log.jsonl` and through `bosun status --json`, `ps` and `launchctl`:
  1. install with the app (`bosun init` answers with `menu_app`), the app launched, its menu green with the windows;
  2. the watchdog job booted out → yellow, "Show What's Wrong…" lists it; back → green;
  3. Open Console → the URL logged, and `curl` with its token gets the console page;
  4. Check for Updates against a newer `v*` tag made in the VM's origin bundle → "Update Available"; Update Now with
     the plan dialog answered → the update runs, the menu shows it waiting, then green on the new commit;
  5. Quit with no busy agent, all defaults → no tmux server, no caffeinate, no console process, the app gone; the
     watchdog kickstarted logs that it skipped; the morning job kickstarted starts nothing;
  6. the app opened again → it starts bosun (green); a stand-in agent kept busy → Quit → the busy dialog → "Let Them
     Finish" with a 0-minute wait → the still-busy dialog → Stop Them Now → stopped, the app gone; then Quit with "Keep
     bosun in the menu bar" → the grey stopped menu, Start → green;
  7. Open at Login on, the VM rebooted → after login the app runs with `--at-login`, and bosun is not started outside
     its schedule;
  8. launched again while running → still one app process;
  9. the app moved to the Trash → the watchdog's `app-missing` alert line; `bosun uninstall --yes` → no app, no login
     job, nothing named bosun left (`named_bosun`).
  With `--from release`, step 1 also proves the notarized download: `spctl --assess` accepts it and it launches with no
  quarantine prompt.

- [ ] **Step 1: Write** the suite and the `mac.sh` subcommand.
- [ ] **Step 2: Run** `tests/sandbox.sh mac app` (about 25 minutes). Expected: `N passed, 0 failed`; then `tests/sandbox.sh
  mac run` and `mac live claude` still pass.
- [ ] **Step 3: Commit** ("Tests: bosun.app driven through every state and choice on a real Mac").

### Task 6: The docs

**Files:**

- Modify: `docs/setup.md` (the app: installing, the menu, quitting and starting, uninstalling), `docs/commands.md`,
  `docs/development.md` (building and releasing the app), `README.md`, `ROADMAP.md` (the DMG and Homebrew cask next),
  `CLAUDE.md` (`app/`: the layout, the contract rule, Swift's lint; Common changes: "A menu item"), `mkdocs.yml`,
  `docs/design/menu-bar.md` (status)

- [ ] **Step 1: Update** the docs; grep for "menu bar", "bosun stop", "Applications".
- [ ] **Step 2: Run** `uv run pre-commit run --all-files`.
- [ ] **Step 3: Commit** and open the pull request.

### Task 7: The first signed release (needs the person)

- [ ] **Step 1: The person** sets the secrets and the `release` environment, and fills `team_id` (a commit on main
  through a pull request).
- [ ] **Step 2:** Tag `app-v0.1.0`; the release workflow signs, notarizes and publishes it.
- [ ] **Step 3: Run** `tests/sandbox.sh mac app --from release` on a fresh clone. Expected: all pass, the download
  accepted by Gatekeeper with no prompt.
