import BosunMenuTestSupport
import Foundation
import Testing

@testable import BosunMenuCore

@MainActor @Suite struct CoordinatorStatusTests {
  @Test func startShowsTheLoadingMenuAndThenTheStatus() async throws {
    let world = try await World()
    await world.coordinator.start()
    #expect(world.ui.rendered.first?.model.header.sentence == "Checking bosun…")
    #expect(world.ui.rendered.first?.status == nil)
    #expect(world.ui.lastModel?.header.sentence == "bosun is running")
    await world.coordinator.stop()
  }

  @Test func aRefreshDrawsTheMenuTheStatusMakes() async throws {
    let world = try await World(status: "status-warning")
    await world.refresh(.timer)
    #expect(world.ui.lastModel?.header.dot == .yellow)
    #expect(world.itemIDs.contains(.showProblems))
    #expect(world.ui.rendered.last?.status != nil)
  }

  @Test func aBosunThatCannotBeReachedIsShownAndNothingElseRuns() async throws {
    let world = try await World(status: nil)
    await world.cli.reply(Bosun.status.args, exited(1, "", err: "cannot execute\n"))
    await world.refresh(.launch)
    #expect(world.ui.lastModel?.header.sentence == "bosun cannot be reached")
    #expect(
      world.itemIDs == [.helpMenu, .help(.guide), .help(.doctor), .help(.logs), .help(.uninstall), .quit])
  }

  /// Review focus 2: a hang keeps the last state, marked as not current.
  @Test func aHangKeepsTheLastStatusMarkedNotCurrent() async throws {
    let world = try await World(status: nil)
    await world.reply(Bosun.status, exited(0, try Fixtures.text("status-ok")))
    await world.reply(Bosun.status, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    await world.refresh(.timer)
    await world.refresh(.timer)
    #expect(world.ui.lastModel?.header.sentence == "bosun is running (not current)")
  }

  @Test func openAtLoginShowsWhatAppStatusSays() async throws {
    let on = try await World(login: true)
    await on.refresh(.launch)
    #expect(on.item(.openAtLogin)?.checked == true)
    let off = try await World(login: false)
    await off.refresh(.launch)
    #expect(off.item(.openAtLogin)?.checked == false)
  }

  @Test func theLoginIsLookedAtWhenTheMenuOpensAndNotEveryHalfMinute() async throws {
    let world = try await World(login: false)
    await world.refresh(.launch)
    await world.refresh(.timer)
    await world.refresh(.timer)
    let looks = await world.cli.calls.filter { $0 == Bosun.appStatus.args }
    #expect(looks.count == 1)
    await world.replace(Bosun.appStatus, exited(0, try Fixtures.text("app-status")))
    await world.menuWillOpen()
    #expect(world.item(.openAtLogin)?.checked == true)
  }

  @Test func anAppStatusThatCannotBeReadKeepsTheLoginAsItWas() async throws {
    let world = try await World(login: true)
    await world.refresh(.launch)
    await world.replace(Bosun.appStatus, exited(1, "", err: "nope\n"))
    await world.menuWillOpen()
    #expect(world.item(.openAtLogin)?.checked == true)
  }

  /// Review focus 3: waking refreshes at once.
  @Test func aWakeRefreshesTheStatus() async throws {
    let world = try await World()
    await world.didWake()
    let looks = await world.cli.calls.filter { $0 == Bosun.status.args }
    #expect(looks.count == 1)
    #expect(world.ui.lastModel?.header.sentence == "bosun is running")
  }

  @Test func theMenuOpeningRefreshesTheStatus() async throws {
    let world = try await World()
    await world.menuWillOpen()
    #expect(world.ui.lastModel?.header.sentence == "bosun is running")
  }
}

@MainActor @Suite struct CoordinatorLaunchTests {
  @Test func openedByYouWhileStoppedItStartsBosunOnce() async throws {
    let world = try await World(status: "status-stopped")
    await world.refresh(.launch)
    await world.refresh(.timer)
    #expect(await world.starts == 1)
  }

  @Test func openedByYouInTheRedStateItStartsBosun() async throws {
    let world = try await World(status: "status-error")
    await world.refresh(.launch)
    #expect(await world.starts == 1)
  }

  /// A-I2: opening the app never runs the morning on a bosun that runs, whatever its level: one with an error
  /// (an ERROR line logged, a finding), or one whose windows could not be read.
  @Test func openedByYouOnABosunThatRunsWithAnErrorItStartsNothing() async throws {
    for status in ["status-running-error", "status-windows-unreadable"] {
      let world = try await World(status: status)
      await world.refresh(.launch)
      #expect(await world.starts == 0, "\(status)")
      #expect(world.item(.startBosun) == nil, "\(status)")
      #expect(world.item(.showProblems) != nil, "\(status)")
    }
  }

  /// Windows still open after a quit that did not finish: opening the app starts nothing, and the menu offers
  /// Start for the person to choose.
  @Test func openedByYouAfterAQuitThatDidNotFinishItStartsNothingButOffersStart() async throws {
    let world = try await World(status: "status-quit-unfinished")
    await world.refresh(.launch)
    #expect(await world.starts == 0)
    #expect(world.item(.startBosun)?.enabled == true)
  }

  /// A status kept after a look that timed out may be stale: Start is greyed, and a click does nothing.
  @Test func startIsOnlyOfferedFromACurrentStatus() async throws {
    let world = try await World(status: "status-stopped", atLogin: true)
    await world.refresh(.launch)
    #expect(world.item(.startBosun)?.enabled == true)
    await world.replace(Bosun.status, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    await world.refresh(.timer)
    #expect(world.ui.lastModel?.header.sentence == "bosun is stopped (not current)")
    #expect(world.item(.startBosun)?.enabled == false)
    await world.coordinator.perform(.startBosun)
    #expect(await world.starts == 0)
  }

  @Test func atLoginItOnlyShowsTheIcon() async throws {
    let world = try await World(status: "status-stopped", atLogin: true)
    await world.refresh(.launch)
    #expect(await world.starts == 0)
    #expect(world.ui.lastModel?.header.dot == .grey)
  }

  @Test func whileRunningItStartsNothing() async throws {
    let world = try await World(status: "status-ok")
    await world.refresh(.launch)
    #expect(await world.starts == 0)
  }

  /// The decision is of the first status only: a bosun that stops later is not started behind your back.
  @Test func aStopLaterOnIsNotStartedAgain() async throws {
    let world = try await World(status: "status-ok")
    await world.refresh(.launch)
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-stopped")))
    await world.refresh(.timer)
    #expect(await world.starts == 0)
  }

  @Test func ifTheFirstStatusIsNotThereTheDecisionWaitsForTheNext() async throws {
    let world = try await World(status: nil)
    await world.cli.reply(Bosun.status.args, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    await world.reply(Bosun.status, exited(0, try Fixtures.text("status-stopped")))
    await world.refresh(.launch)
    #expect(await world.starts == 0)
    await world.refresh(.timer)
    #expect(await world.starts == 1)
  }
}

@MainActor @Suite struct CoordinatorActionTests {
  @Test func openConsoleOpensTheAddressBosunGives() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.reply(Bosun.consoleURL, exited(0, "http://127.0.0.1:8123/?t=abc\n"))
    await world.coordinator.perform(.openConsole)
    #expect(world.ui.opened == [URL(string: "http://127.0.0.1:8123/?t=abc")!])
  }

  @Test func openConsoleThatFailsShowsWhy() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.reply(Bosun.consoleURL, exited(1, "", err: "the console is not running\n"))
    world.ui.answer("OK")
    await world.coordinator.perform(.openConsole)
    #expect(world.ui.opened.isEmpty)
    #expect(world.ui.lastSpec?.title == "Cannot open the console")
    #expect(world.ui.lastSpec?.message == "the console is not running")
  }

  @Test func openConsoleThatAnswersWithAnythingElseOpensNothing() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.reply(Bosun.consoleURL, exited(0, "file:///etc/passwd\n"))
    world.ui.answer("OK")
    await world.coordinator.perform(.openConsole)
    #expect(world.ui.opened.isEmpty)
    #expect(world.ui.presented.count == 1)
  }

  @Test func startBosunStartsTheMorningRunAndLooksAgain() async throws {
    let world = try await World(status: "status-stopped")
    await world.refresh(.launch)
    await world.coordinator.settle()
    let starts = await world.starts  // the start at launch
    let looksBefore = await world.cli.calls.filter { $0 == Bosun.status.args }.count
    await world.coordinator.perform(.startBosun)
    #expect(await world.starts == starts + 1)
    // A short command, waited for: never a child the app leaves running
    #expect(
      await world.commands.last(where: { $0.first == "morning" }) == ["morning", "--now", "--background"])
    #expect(await world.cli.started.isEmpty)
    #expect(world.coordinator.flow.isIdle)
    // It looks at the status once it is started
    let looks = await world.cli.calls.filter { $0 == Bosun.status.args }
    #expect(looks.count == looksBefore + 1)
  }

  @Test func startBosunThatCannotStartShowsWhy() async throws {
    let world = try await World(status: "status-stopped", atLogin: true)
    await world.refresh(.launch)
    await world.replace(
      Bosun.start, CommandResult(outcome: .couldNotStart("no permission"), stdout: "", stderr: ""))
    world.ui.answer("OK")
    await world.coordinator.perform(.startBosun)
    #expect(world.ui.lastSpec?.title == "bosun did not start")
    #expect(world.ui.lastSpec?.message == "no permission")
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func startBosunThatExitsNonZeroShowsItsLine() async throws {
    let world = try await World(status: "status-stopped", atLogin: true)
    await world.refresh(.launch)
    await world.replace(Bosun.start, exited(1, "", err: "bosun: the morning job is not loaded\n"))
    world.ui.answer("OK")
    await world.coordinator.perform(.startBosun)
    #expect(world.ui.lastSpec?.title == "bosun did not start")
    #expect(world.ui.lastSpec?.message == "bosun: the morning job is not loaded")
  }

  @Test func startBosunWithBosunMissingSaysSo() async throws {
    let world = try await World(status: "status-stopped", atLogin: true)
    await world.refresh(.launch)
    await world.replace(Bosun.start, CommandResult(outcome: .notInstalled, stdout: "", stderr: ""))
    world.ui.answer("OK")
    await world.coordinator.perform(.startBosun)
    #expect(world.ui.lastSpec?.message == "bosun is not installed")
  }

  @Test func openAtLoginTurnsItOnAndShowsItChecked() async throws {
    let world = try await World(login: false)
    await world.refresh(.launch)
    #expect(world.item(.openAtLogin)?.checked == false)
    await world.reply(Bosun.appLogin(on: true), exited(0))
    await world.replace(Bosun.appStatus, exited(0, try Fixtures.text("app-status")))
    await world.coordinator.perform(.openAtLogin)
    #expect(await world.commands.contains(["app", "login", "on"]))
    #expect(world.item(.openAtLogin)?.checked == true)
  }

  @Test func openAtLoginTurnsItOffWhenItIsOn() async throws {
    let world = try await World(login: true)
    await world.refresh(.launch)
    await world.reply(Bosun.appLogin(on: false), exited(0))
    let off = try Fixtures.text("app-status").replacingOccurrences(
      of: "\"login\": true", with: "\"login\": false")
    await world.replace(Bosun.appStatus, exited(0, off))
    await world.coordinator.perform(.openAtLogin)
    #expect(await world.commands.contains(["app", "login", "off"]))
    #expect(world.item(.openAtLogin)?.checked == false)
  }

  @Test func openAtLoginThatFailsShowsWhyAndChangesNothing() async throws {
    let world = try await World(login: false)
    await world.refresh(.launch)
    await world.reply(Bosun.appLogin(on: true), exited(1, "", err: "bosun.app is not installed\n"))
    world.ui.answer("OK")
    await world.coordinator.perform(.openAtLogin)
    #expect(world.ui.lastSpec?.title == "Cannot change Open at Login")
    #expect(world.ui.lastSpec?.message == "bosun.app is not installed")
    #expect(world.item(.openAtLogin)?.checked == false)
  }

  @Test func showWhatsWrongListsTheProblems() async throws {
    let world = try await World(status: "status-warning")
    await world.refresh(.launch)
    world.ui.answer("OK")
    await world.coordinator.perform(.showProblems)
    let problems = try Fixtures.status("status-warning").problems
    #expect(!problems.isEmpty)
    #expect(world.ui.lastSpec?.scroll == problems.joined(separator: "\n"))
  }

  @Test func theGuideIsTheDocsSite() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.coordinator.perform(.help(.guide))
    #expect(world.ui.opened == [Links.guide])
  }

  @Test func showLogsOpensTheFolderStatusGave() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.coordinator.perform(.help(.logs))
    #expect(world.ui.opened == [URL(fileURLWithPath: "/Users/me/.local/state/bosun/logs")])
  }

  @Test func runDoctorOpensAScriptThatRunsIt() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.coordinator.perform(.help(.doctor))
    #expect(world.scripts.written == [["doctor"]])
    #expect(world.ui.opened == [URL(fileURLWithPath: "/tmp/bosun-app/bosun-doctor.command")])
  }

  @Test func uninstallOpensAScriptThatRunsIt() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.coordinator.perform(.help(.uninstall))
    #expect(world.scripts.written == [["uninstall"]])
    #expect(world.ui.opened == [URL(fileURLWithPath: "/tmp/bosun-app/bosun-uninstall.command")])
  }

  @Test func aScriptThatCannotBeWrittenShowsWhy() async throws {
    let world = try await World()
    await world.refresh(.launch)
    world.scripts.fails = true
    world.ui.answer("OK")
    await world.coordinator.perform(.help(.doctor))
    #expect(world.ui.opened.isEmpty)
    #expect(world.ui.lastSpec?.title == "Cannot open Terminal")
  }

  /// A greyed item cannot be chosen: a click that comes in anyway (the test hooks') is ignored.
  @Test func anItemThatIsGreyedIsIgnored() async throws {
    let world = try await World(status: "status-stopped", atLogin: true)
    await world.refresh(.launch)
    #expect(world.item(.openConsole)?.enabled == false)
    await world.coordinator.perform(.openConsole)
    #expect(await !world.commands.contains(Bosun.consoleURL.args))
    #expect(world.ui.opened.isEmpty)
  }

  @Test func anItemThatIsNotInTheMenuIsIgnored() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.coordinator.perform(.startBosun)
    #expect(await world.starts == 0)
  }

  @Test func theHelpMenuItselfDoesNothing() async throws {
    let world = try await World()
    await world.refresh(.launch)
    let drawn = world.ui.rendered.count
    await world.coordinator.perform(.helpMenu)
    #expect(world.ui.opened.isEmpty)
    #expect(world.ui.presented.isEmpty)
    #expect(world.ui.rendered.count == drawn)
  }
}
