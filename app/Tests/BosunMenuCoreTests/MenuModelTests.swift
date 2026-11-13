import Testing

@testable import BosunMenuCore

extension MenuModel {
  /// Every item of every section, in order, a submenu's after itself.
  var allItems: [MenuItem] { sections.flatMap { $0 }.flatMap { [$0] + $0.children } }
  var topIDs: [ItemID] { sections.flatMap { $0 }.map(\.id) }

  func item(_ id: ItemID) -> MenuItem { allItems.first { $0.id == id }! }
  func has(_ id: ItemID) -> Bool { allItems.contains { $0.id == id } }
}

private func loaded(_ name: String, current: Bool = true) throws -> StatusState {
  .loaded(try Fixtures.status(name), current: current)
}

private func model(
  _ status: StatusState, update: UpdateState = .idle, flow: FlowState = FlowState(),
  login: Bool = false
) -> MenuModel {
  menu(for: status, update: update, flow: flow, login: login)
}

private let tail: [ItemID] = [.checkForUpdates, .openAtLogin, .helpMenu, .quit]

@Suite struct MenuForEachLevelTests {
  @Test func runningIsGreenWithTheUsualItems() throws {
    let menu = model(try loaded("status-ok"))
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

  @Test func aWarningIsYellowAndOffersWhatIsWrong() throws {
    let menu = model(try loaded("status-warning"))
    #expect(menu.header.dot == .yellow)
    #expect(menu.header.sentence == "bosun is running, with a warning")
    #expect(menu.topIDs == [.showProblems, .openConsole] + tail)
    #expect(menu.item(.showProblems).title == "Show What's Wrong…")
    #expect(menu.iconBadge == .warning)
  }

  @Test func anErrorIsRedWithStartFirst() throws {
    let menu = model(try loaded("status-error"))
    #expect(menu.header.dot == .red)
    #expect(menu.header.sentence == "bosun is not running")
    #expect(menu.topIDs == [.startBosun, .showProblems, .openConsole] + tail)
    #expect(menu.item(.startBosun).title == "Start bosun")
    #expect(menu.item(.startBosun).enabled)
    #expect(menu.iconBadge == .error)
  }

  /// A-I2: red with windows open is a bosun that runs, with an error: its problems, never Start.
  @Test func runningWithAnErrorIsRedWithItsProblemsAndNoStart() throws {
    let menu = model(try loaded("status-running-error"))
    #expect(menu.header.dot == .red)
    #expect(menu.header.sentence == "bosun is running, with an error")
    #expect(!menu.has(.startBosun))
    #expect(menu.topIDs == [.showProblems, .openConsole] + tail)
    #expect(menu.iconBadge == .error)
  }

  @Test func redWithItsWindowsUnreadOffersNoStart() throws {
    let menu = model(try loaded("status-windows-unreadable"))
    #expect(menu.header.dot == .red)
    #expect(!menu.has(.startBosun))
    #expect(menu.has(.showProblems))
  }

  /// A-2: a level this app does not know is a warning: yellow, its problems, never green and never Start.
  @Test func aLevelItDoesNotKnowIsShownAsAWarning() throws {
    let text = try Fixtures.text("status-stopped").replacingOccurrences(
      of: "\"level\": \"stopped\"", with: "\"level\": \"paused\"")
    let menu = model(.loaded(try decodeContract(StatusSnapshot.self, from: text), current: true))
    #expect(menu.header.dot == .yellow)
    #expect(menu.header.sentence == "bosun is stopped")
    #expect(menu.has(.showProblems))
    #expect(!menu.has(.startBosun))
    #expect(menu.iconBadge == .warning)
    #expect(menu.item(.quit).title == "Quit bosun…")
  }

  @Test func whatAFlowSaysIsALineUnderTheStatus() throws {
    var flow = flowRunning(.quit)
    flow.detail = "Quitting: waiting for acme-exporter to finish…"
    let menu = model(try loaded("status-ok"), flow: flow)
    #expect(menu.header.detail.last == "Quitting: waiting for acme-exporter to finish…")
    #expect(model(try loaded("status-ok")).header.detail.allSatisfy { !$0.hasPrefix("Quitting") })
  }

  @Test func aQuitThatDidNotFinishIsAnErrorToo() throws {
    let menu = model(try loaded("status-quit-unfinished"))
    #expect(menu.header.dot == .red)
    #expect(menu.topIDs.prefix(2) == [.startBosun, .showProblems])
    #expect(menu.item(.quit).title == "Quit bosun…")
  }

  @Test func stoppedIsGreyWithStartFirstAConsoleThatIsGreyedAndAQuitWithoutDots() throws {
    let menu = model(try loaded("status-stopped"))
    #expect(menu.header.dot == .grey)
    #expect(menu.header.sentence == "bosun is stopped")
    #expect(menu.topIDs == [.startBosun, .openConsole] + tail)
    #expect(!menu.item(.openConsole).enabled)
    #expect(menu.item(.quit).title == "Quit bosun")
    #expect(menu.item(.quit).enabled)
    #expect(!menu.has(.showProblems))
    #expect(menu.iconBadge == nil)
  }

  @Test func restingIsGreenAndNeedsNoStart() throws {
    let menu = model(try loaded("status-resting"))
    #expect(menu.header.dot == .green)
    #expect(menu.header.sentence == "bosun is resting until 08:00")
    #expect(!menu.has(.startBosun))
    #expect(menu.item(.openConsole).enabled)
  }

  @Test func theSectionsAreTheActionsTheConsoleTheAppsOwnItemsAndQuit() throws {
    let menu = model(try loaded("status-error"))
    #expect(
      menu.sections.map { $0.map(\.id) } == [
        [.startBosun, .showProblems], [.openConsole], [.checkForUpdates, .openAtLogin, .helpMenu], [.quit],
      ])
  }

  @Test func startIsGreyedOnAStatusThatIsNotCurrent() throws {
    #expect(model(try loaded("status-stopped")).item(.startBosun).enabled)
    #expect(!model(try loaded("status-stopped", current: false)).item(.startBosun).enabled)
    #expect(!model(try loaded("status-error", current: false)).item(.startBosun).enabled)
  }

  @Test func aStatusThatIsNotCurrentSaysSoAndKeepsItsColour() throws {
    let menu = model(try loaded("status-warning", current: false))
    #expect(menu.header.sentence == "bosun is running, with a warning (not current)")
    #expect(menu.header.dot == .yellow)
    #expect(menu.topIDs == [.showProblems, .openConsole] + tail)
  }

  @Test func openAtLoginShowsTheStateItWasGiven() throws {
    #expect(try model(loaded("status-ok"), login: true).item(.openAtLogin).checked)
    #expect(try !model(loaded("status-ok"), login: false).item(.openAtLogin).checked)
    #expect(try model(loaded("status-ok")).item(.openAtLogin).title == "Open at Login")
  }
}

@Suite struct MenuWhenBosunCannotBeAskedTests {
  /// What may be chosen: every enabled leaf. None of these runs bosun.
  private func enabledLeaves(_ menu: MenuModel) -> [ItemID] {
    menu.sections.flatMap { $0 }.flatMap { $0.children.isEmpty ? [$0] : $0.children }
      .filter(\.enabled).map(\.id)
  }

  @Test func notInstalledSaysSoAndOffersNothingThatRunsBosun() throws {
    let menu = model(.notInstalled)
    #expect(menu.header.sentence == "bosun is not installed")
    #expect(menu.header.dot == .grey)
    #expect(enabledLeaves(menu) == [.help(.guide), .quit])
    #expect(menu.item(.quit).title == "Quit bosun.app")
    #expect(menu.iconBadge == nil)
  }

  /// bosun runs but its status could not be had: Run bosun doctor is what finds out why, and it is offered.
  @Test func unreachableSaysSoWithWhyAndOffersOnlyDoctor() throws {
    let menu = model(.unreachable("bosun did not answer in time"))
    #expect(menu.header.sentence == "bosun cannot be reached")
    #expect(menu.header.detail == ["bosun did not answer in time"])
    #expect(menu.header.dot == .red)
    #expect(enabledLeaves(menu) == [.help(.guide), .help(.doctor), .quit])
    #expect(menu.iconBadge == .error)
  }

  /// A link that cannot be run: doctor could not run either, so nothing that runs bosun is offered.
  @Test func cannotRunSaysSoWithWhyAndOffersNothingThatRunsBosun() throws {
    let menu = model(.cannotRun("~/.local/bin/bosun cannot be run (a broken link, or not executable)"))
    #expect(menu.header.sentence == "bosun cannot be run")
    #expect(menu.header.detail == ["~/.local/bin/bosun cannot be run (a broken link, or not executable)"])
    #expect(menu.header.dot == .red)
    #expect(enabledLeaves(menu) == [.help(.guide), .quit])
    #expect(menu.iconBadge == .error)
    #expect(menu.item(.quit).title == "Quit bosun.app")
  }

  @Test func theHelpMenuKeepsItsFourItemsButOnlyTheGuideIsEnabledThen() throws {
    let help = model(.notInstalled).item(.helpMenu).children
    #expect(help.map(\.id) == [.help(.guide), .help(.doctor), .help(.logs), .help(.uninstall)])
    #expect(help.map(\.enabled) == [true, false, false, false])
  }

  @Test func contractTooNewOffersToUpdateBosunAndNothingElseThatRunsIt() throws {
    let menu = model(.contractTooNew(2))
    #expect(menu.header.sentence == "bosun.app needs an update")
    #expect(
      menu.header.detail == [
        "bosun reports version 2 of its status; this app reads version 1.",
        "If bosun is up to date, run `bosun app update` in a terminal.",
      ])
    #expect(menu.header.dot == .yellow)
    #expect(menu.topIDs == [.updateBosun, .helpMenu, .quit])
    #expect(menu.item(.updateBosun).title == "Update bosun…")
    #expect(menu.item(.updateBosun).enabled)
    #expect(enabledLeaves(menu) == [.updateBosun, .help(.guide), .help(.doctor), .help(.uninstall), .quit])
    #expect(menu.iconBadge == .warning)
  }

  @Test func updateBosunWaitsForTheFlowInProgress() throws {
    let flow = flowRunning(.update)
    #expect(!model(.contractTooNew(2), flow: flow).item(.updateBosun).enabled)
  }

  /// A-4: once an update is under way the item says so, greyed, so a second click cannot start another.
  @Test func updateBosunShowsTheUpdateUnderWay() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-available")
    func item(_ title: String, enabled: Bool = false) -> MenuItem {
      MenuItem(id: .updateBosun, title: title, enabled: enabled)
    }
    let cases: [(UpdateState, MenuItem)] = [
      (.idle, item("Update bosun…", enabled: true)),
      (.failed("x"), item("Update bosun…", enabled: true)),
      (.available(check), item("Update bosun…", enabled: true)),
      (.checking, item("Checking for Updates…")),
      (.planning(check), item("Preparing Update…")),
      (.starting(check), item("Updating…")),
      (.running(waitingFor: ["acme-web"]), item("Updating… waiting for 1 window")),
    ]
    for (state, expected) in cases {
      #expect(model(.contractTooNew(2), update: state).item(.updateBosun) == expected, "\(state)")
    }
  }
}

@Suite struct MenuUpdateItemTests {
  @Test func eachUpdateStateHasItsTitleAndState() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-available")
    let plan = try Fixtures.plan()
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
      #expect(try model(loaded("status-ok"), update: state).item(.checkForUpdates) == expected, "\(state)")
    }
  }

  @Test func anUpdateReadyPutsADotOnTheIconUnlessSomethingWorseIsWrong() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-available")
    #expect(try model(loaded("status-ok"), update: .available(check)).iconBadge == .update)
    #expect(try model(loaded("status-stopped"), update: .available(check)).iconBadge == .update)
    #expect(try model(loaded("status-warning"), update: .available(check)).iconBadge == .warning)
    #expect(try model(loaded("status-error"), update: .available(check)).iconBadge == .error)
  }

  @Test func whileAFlowRunsTheUpdateItemWaits() throws {
    let flow = flowRunning(.quit)
    #expect(try !model(loaded("status-ok"), flow: flow).item(.checkForUpdates).enabled)
  }
}

@Suite struct MenuWhileAFlowRunsTests {
  @Test func startQuitAndUninstallWaitButTheConsoleAndTheProblemsDoNot() throws {
    let menu = model(try loaded("status-error"), flow: flowRunning(.quit))
    #expect(!menu.item(.startBosun).enabled)
    #expect(!menu.item(.quit).enabled)
    #expect(!menu.item(.help(.uninstall)).enabled)
    #expect(menu.item(.openConsole).enabled)
    #expect(menu.item(.showProblems).enabled)
    #expect(menu.item(.help(.doctor)).enabled)
  }

  @Test func openAtLoginWaitsForAFlowAndForItsOwnSwitch() throws {
    #expect(!model(try loaded("status-ok"), flow: flowRunning(.login)).item(.openAtLogin).enabled)
    #expect(!model(try loaded("status-ok"), flow: flowRunning(.quit)).item(.openAtLogin).enabled)
    #expect(model(try loaded("status-ok")).item(.openAtLogin).enabled)
  }

  @Test func withNoFlowTheyAreAllThere() throws {
    let menu = model(try loaded("status-error"))
    #expect(menu.item(.startBosun).enabled)
    #expect(menu.item(.quit).enabled)
    #expect(menu.item(.help(.uninstall)).enabled)
  }
}

@Suite struct HelpMenuTests {
  @Test func theHelpMenuHasTheGuideDoctorLogsAndUninstall() throws {
    let help = model(try loaded("status-ok")).item(.helpMenu)
    #expect(help.title == "Help")
    #expect(
      help.children.map(\.title) == [
        "bosun Guide", "Run bosun doctor", "Show Logs", "Uninstall bosun…",
      ])
    #expect(help.children.allSatisfy { $0.enabled })
  }

  @Test func theLogsWaitForAFolderToShow() throws {
    let json = try Fixtures.text("status-ok").replacingOccurrences(
      of: "/Users/me/.local/state/bosun/logs", with: "")
    let status = try! decodeContract(StatusSnapshot.self, from: json)
    #expect(!model(.loaded(status, current: true)).item(.help(.logs)).enabled)
  }
}
