import Foundation

// The Settings window's side of the coordinator (docs/design/control-panel.md, "The Settings window"): Settings…
// reads `bosun settings get` and `bosun app status` and shows the window; each edit is carried out through bosun
// (`settings set`, `app login`, Terminal for what needs an administrator or the wizard), and the window then
// shows what bosun reads, never the edit.

extension AppCoordinator {
  /// Settings…: the window over fresh values (a second Settings… brings the same window to the front).
  func openSettings() async {
    let state = await readSettings()
    ui.showSettings(state) { [weak self] edit in
      await self?.applySettings(edit) ?? state
    }
  }

  /// Carries out what the window asked for, and returns the state it shows next. A Save while another runs is
  /// not started: the answer is the state, busy.
  public func applySettings(_ edit: SettingsEdit) async -> SettingsState {
    let current = await currentSettings()
    guard !settingsBusy else { return current.starting() }
    switch edit {
    case .openAtLogin(let on):
      // from unknown (nil) too: `app login on|off` makes it so whatever it was
      if on != login { await switchLogin(to: on) }
      return shown(current.with(openAtLogin: login))
    case .values(let values):
      return await save(values, current)
    case .changeWindows:
      await openTerminal(running: ["init"])
    case .openConfigFolder:
      if let folder = current.view?.configDir { ui.open(URL(fileURLWithPath: folder)) }
    case .showLogs:
      if let logs = snapshot?.logs, !logs.isEmpty { ui.open(URL(fileURLWithPath: logs)) }
    }
    return current
  }

  /// A Save: in Terminal when it moves the wake bosun set (`sudo pmset`), else `settings set` and a fresh read,
  /// whose values the window shows with what the set said.
  private func save(_ values: [String: String], _ current: SettingsState) async -> SettingsState {
    if values.isEmpty { return current }
    if current.needsTerminal(values) {
      guard await openTerminal(running: settingsSetArgs(values)) else {
        return shown(current.with(message: nil))
      }
      // a new reading, of what bosun read before: the draft that went to Terminal is not shown as taken
      settingsReads += 1
      return shown(current.handedOff(read: settingsReads))
    }
    settingsBusy = true
    shown(current.starting())
    let said = settingsSetMessage(await cli.run(Bosun.settingsSet(values)))
    let fresh = await readSettings(after: said)
    settingsBusy = false
    return fresh
  }

  /// What the window shows now: what it was last shown, or a first read.
  private func currentSettings() async -> SettingsState {
    if let settings { return settings }
    return await readSettings()
  }

  /// The window came to the front again (from Terminal, or another app): bosun is read again, and the window shows
  /// what `SettingsState.refreshed` makes of it. Not while a Save runs, and a read that a Save overtook is dropped.
  public func refreshSettings() async {
    guard let current = settings, !settingsBusy else { return }
    let fresh = await fetchSettings()
    guard settings == current, !settingsBusy else { return }
    shown(current.refreshed(by: fresh))
  }

  /// `settings get` and Open at Login read, with the line an edit left: what the window shows (and the hooks'
  /// menu says).
  private func readSettings(after said: String? = nil) async -> SettingsState {
    shown(await fetchSettings(after: said))
  }

  /// A new reading of bosun's settings and of Open at Login. A read that fails shows no values (unknown), and says
  /// why.
  private func fetchSettings(after said: String? = nil) async -> SettingsState {
    await readLogin()
    settingsReads += 1
    let state: SettingsState
    switch await fetch(SettingsView.self, Bosun.settingsGet, via: cli) {
    case .success(let view):
      state = SettingsState(
        view: view, openAtLogin: login, message: said, busy: false, read: settingsReads)
    case .failure(let failure):
      let lines = [said, failure.message].compactMap { $0 }
      state = SettingsState(
        view: nil, openAtLogin: login, message: lines.joined(separator: "\n"), busy: false,
        read: settingsReads)
    }
    return state
  }

  /// Keeps `state` as what the window shows, and draws (the hooks' menu carries it).
  @discardableResult
  private func shown(_ state: SettingsState) -> SettingsState {
    settings = state
    render()
    return state
  }
}

extension SettingsState {
  func with(openAtLogin: Bool?) -> SettingsState {
    SettingsState(view: view, openAtLogin: openAtLogin, message: message, busy: busy, read: read)
  }

  func with(message: String?) -> SettingsState {
    SettingsState(view: view, openAtLogin: openAtLogin, message: message, busy: busy, read: read)
  }

  /// A change handed to Terminal: what bosun read before, as reading `read`, with the line to finish it there.
  func handedOff(read: Int) -> SettingsState {
    SettingsState(
      view: view, openAtLogin: openAtLogin, message: Self.finishInTerminal, busy: false, read: read)
  }
}
