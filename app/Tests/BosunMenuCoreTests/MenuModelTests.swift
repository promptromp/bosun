import Testing

@testable import BosunMenuCore

extension MenuModel {
  /// Every item of every section, in order, a submenu's after itself.
  var allItems: [MenuItem] { sections.flatMap { $0 }.flatMap { [$0] + $0.children } }
  var topIDs: [ItemID] { sections.flatMap { $0 }.map(\.id) }

  func item(_ id: ItemID) -> MenuItem { allItems.first { $0.id == id }! }
  func has(_ id: ItemID) -> Bool { allItems.contains { $0.id == id } }
}

private func loaded(_ name: String, current: Bool = true) -> StatusState {
  .loaded(Fixtures.status(name), current: current)
}

private func model(
  _ status: StatusState, update: UpdateState = .idle, flow: FlowState = FlowState(),
  login: Bool = false
) -> MenuModel {
  menu(for: status, update: update, flow: flow, login: login)
}

private let tail: [ItemID] = [.checkForUpdates, .openAtLogin, .helpMenu, .quit]

@Suite struct MenuForEachLevelTests {
  @Test func runningIsGreenWithTheUsualItems() {
    let menu = model(loaded("status-ok"))
    #expect(
      menu.header
        == StatusHeader(
          dot: .green, sentence: "bosun is running",
          detail: ["3 windows open", "1 waiting for you: acme-web", "Mac kept awake until 20:00"]))
    #expect(menu.topIDs == [.openConsole] + tail)
    #expect(!menu.has(.showProblems))
    #expect(!menu.has(.startBosun))
    #expect(menu.item(.openConsole).enabled)
    #expect(menu.item(.openConsole).key == "o")
    #expect(menu.item(.openConsole).title == "Open Console")
    #expect(menu.item(.quit).title == "Quit bosun…")
    #expect(menu.item(.quit).key == "q")
    #expect(menu.iconBadge == nil)
  }

  @Test func aWarningIsYellowAndOffersWhatIsWrong() {
    let menu = model(loaded("status-warning"))
    #expect(menu.header.dot == .yellow)
    #expect(menu.header.sentence == "bosun is running, with a warning")
    #expect(menu.topIDs == [.showProblems, .openConsole] + tail)
    #expect(menu.item(.showProblems).title == "Show What's Wrong…")
    #expect(menu.iconBadge == .warning)
  }

  @Test func anErrorIsRedWithStartFirst() {
    let menu = model(loaded("status-error"))
    #expect(menu.header.dot == .red)
    #expect(menu.header.sentence == "bosun is not running")
    #expect(menu.topIDs == [.startBosun, .showProblems, .openConsole] + tail)
    #expect(menu.item(.startBosun).title == "Start bosun")
    #expect(menu.item(.startBosun).enabled)
    #expect(menu.iconBadge == .error)
  }

  @Test func aQuitThatDidNotFinishIsAnErrorToo() {
    let menu = model(loaded("status-quit-unfinished"))
    #expect(menu.header.dot == .red)
    #expect(menu.topIDs.prefix(2) == [.startBosun, .showProblems])
    #expect(menu.item(.quit).title == "Quit bosun…")
  }

  @Test func stoppedIsGreyWithStartFirstAConsoleThatIsGreyedAndAQuitWithoutDots() {
    let menu = model(loaded("status-stopped"))
    #expect(menu.header.dot == .grey)
    #expect(menu.header.sentence == "bosun is stopped")
    #expect(menu.topIDs == [.startBosun, .openConsole] + tail)
    #expect(!menu.item(.openConsole).enabled)
    #expect(menu.item(.quit).title == "Quit bosun")
    #expect(menu.item(.quit).enabled)
    #expect(!menu.has(.showProblems))
    #expect(menu.iconBadge == nil)
  }

  @Test func restingIsGreenAndNeedsNoStart() {
    let menu = model(loaded("status-resting"))
    #expect(menu.header.dot == .green)
    #expect(menu.header.sentence == "bosun is resting until 08:00")
    #expect(!menu.has(.startBosun))
    #expect(menu.item(.openConsole).enabled)
  }

  @Test func theSectionsAreTheActionsTheConsoleTheAppsOwnItemsAndQuit() {
    let menu = model(loaded("status-error"))
    #expect(
      menu.sections.map { $0.map(\.id) } == [
        [.startBosun, .showProblems], [.openConsole], [.checkForUpdates, .openAtLogin, .helpMenu], [.quit],
      ])
  }

  @Test func aStatusThatIsNotCurrentSaysSoAndKeepsItsColour() {
    let menu = model(loaded("status-warning", current: false))
    #expect(menu.header.sentence == "bosun is running, with a warning (not current)")
    #expect(menu.header.dot == .yellow)
    #expect(menu.topIDs == [.showProblems, .openConsole] + tail)
  }

  @Test func openAtLoginShowsTheStateItWasGiven() {
    #expect(model(loaded("status-ok"), login: true).item(.openAtLogin).checked)
    #expect(!model(loaded("status-ok"), login: false).item(.openAtLogin).checked)
    #expect(model(loaded("status-ok")).item(.openAtLogin).title == "Open at Login")
  }
}

@Suite struct MenuWhenBosunCannotBeAskedTests {
  /// What may be chosen: every enabled leaf. None of these runs bosun.
  private func enabledLeaves(_ menu: MenuModel) -> [ItemID] {
    menu.sections.flatMap { $0 }.flatMap { $0.children.isEmpty ? [$0] : $0.children }
      .filter(\.enabled).map(\.id)
  }

  @Test func notInstalledSaysSoAndOffersNothingThatRunsBosun() {
    let menu = model(.notInstalled)
    #expect(menu.header.sentence == "bosun is not installed")
    #expect(menu.header.dot == .grey)
    #expect(enabledLeaves(menu) == [.help(.guide), .quit])
    #expect(menu.item(.quit).title == "Quit bosun.app")
    #expect(menu.iconBadge == nil)
  }

  @Test func unreachableSaysSoWithWhyAndOffersNothingThatRunsBosun() {
    let menu = model(.unreachable("bosun: No such file or directory"))
    #expect(menu.header.sentence == "bosun cannot be reached")
    #expect(menu.header.detail == ["bosun: No such file or directory"])
    #expect(menu.header.dot == .red)
    #expect(enabledLeaves(menu) == [.help(.guide), .quit])
    #expect(menu.iconBadge == .error)
  }

  @Test func theHelpMenuKeepsItsFourItemsButOnlyTheGuideIsEnabledThen() {
    let help = model(.notInstalled).item(.helpMenu).children
    #expect(help.map(\.id) == [.help(.guide), .help(.doctor), .help(.logs), .help(.uninstall)])
    #expect(help.map(\.enabled) == [true, false, false, false])
  }

  @Test func contractTooNewOffersToUpdateBosunAndNothingElseThatRunsIt() {
    let menu = model(.contractTooNew(2))
    #expect(menu.header.sentence == "bosun.app needs an update")
    #expect(menu.header.detail == ["bosun reports version 2 of its status; this app reads version 1."])
    #expect(menu.header.dot == .yellow)
    #expect(menu.topIDs == [.updateBosun, .helpMenu, .quit])
    #expect(menu.item(.updateBosun).title == "Update bosun…")
    #expect(menu.item(.updateBosun).enabled)
    #expect(enabledLeaves(menu) == [.updateBosun, .help(.guide), .help(.doctor), .help(.uninstall), .quit])
    #expect(menu.iconBadge == .warning)
  }

  @Test func updateBosunWaitsForTheFlowInProgress() {
    let flow = flowRunning(.update)
    #expect(!model(.contractTooNew(2), flow: flow).item(.updateBosun).enabled)
  }
}

@Suite struct MenuUpdateItemTests {
  @Test func eachUpdateStateHasItsTitleAndState() {
    let check = Fixtures.decode(UpdateCheck.self, "update-check-available")
    let plan = Fixtures.plan()
    func item(_ title: String, enabled: Bool = false, badge: Bool = false) -> MenuItem {
      MenuItem(id: .checkForUpdates, title: title, enabled: enabled, badge: badge)
    }
    let cases: [(UpdateState, MenuItem)] = [
      (.idle, item("Check for Updates…", enabled: true)),
      (.checking, item("Checking for Updates…")),
      (.available(check), item("Update Available: v2026.11.11…", enabled: true, badge: true)),
      (.planning(check), item("Preparing Update…")),
      (.confirming(plan, check), item("Update Available: v2026.11.11…", badge: true)),
      (.checkingStart(check), item("Checking for Updates…")),
      (.starting(check), item("Updating…")),
      (.running(waitingFor: []), item("Updating…")),
      (.running(waitingFor: ["acme-web"]), item("Updating… waiting for 1 window")),
      (.running(waitingFor: ["a", "b", "c"]), item("Updating… waiting for 3 windows")),
      (.failed("no network"), item("Check for Updates…", enabled: true)),
    ]
    for (state, expected) in cases {
      #expect(model(loaded("status-ok"), update: state).item(.checkForUpdates) == expected, "\(state)")
    }
  }

  @Test func anUpdateReadyPutsADotOnTheIconUnlessSomethingWorseIsWrong() {
    let check = Fixtures.decode(UpdateCheck.self, "update-check-available")
    #expect(model(loaded("status-ok"), update: .available(check)).iconBadge == .update)
    #expect(model(loaded("status-stopped"), update: .available(check)).iconBadge == .update)
    #expect(model(loaded("status-warning"), update: .available(check)).iconBadge == .warning)
    #expect(model(loaded("status-error"), update: .available(check)).iconBadge == .error)
  }

  @Test func whileAFlowRunsTheUpdateItemWaits() {
    let flow = flowRunning(.quit)
    #expect(!model(loaded("status-ok"), flow: flow).item(.checkForUpdates).enabled)
  }
}

@Suite struct MenuWhileAFlowRunsTests {
  @Test func startQuitAndUninstallWaitButTheConsoleAndTheProblemsDoNot() {
    let menu = model(loaded("status-error"), flow: flowRunning(.quit))
    #expect(!menu.item(.startBosun).enabled)
    #expect(!menu.item(.quit).enabled)
    #expect(!menu.item(.help(.uninstall)).enabled)
    #expect(menu.item(.openConsole).enabled)
    #expect(menu.item(.showProblems).enabled)
    #expect(menu.item(.help(.doctor)).enabled)
  }

  @Test func withNoFlowTheyAreAllThere() {
    let menu = model(loaded("status-error"))
    #expect(menu.item(.startBosun).enabled)
    #expect(menu.item(.quit).enabled)
    #expect(menu.item(.help(.uninstall)).enabled)
  }
}

@Suite struct HelpMenuTests {
  @Test func theHelpMenuHasTheGuideDoctorLogsAndUninstall() {
    let help = model(loaded("status-ok")).item(.helpMenu)
    #expect(help.title == "Help")
    #expect(
      help.children.map(\.title) == [
        "bosun Guide", "Run bosun doctor", "Show Logs", "Uninstall bosun…",
      ])
    #expect(help.children.allSatisfy { $0.enabled })
  }

  @Test func theLogsWaitForAFolderToShow() {
    let json = Fixtures.text("status-ok").replacingOccurrences(
      of: "/Users/me/.local/state/bosun/logs", with: "")
    let status = try! decodeContract(StatusSnapshot.self, from: json)
    #expect(!model(.loaded(status, current: true)).item(.help(.logs)).enabled)
  }
}
