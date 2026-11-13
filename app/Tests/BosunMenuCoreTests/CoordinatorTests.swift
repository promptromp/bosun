import Foundation
import Testing

@testable import BosunMenuCore

@MainActor @Suite struct CoordinatorStatusTests {
  @Test func startShowsTheLoadingMenuAndThenTheStatus() async {
    let world = await World()
    await world.coordinator.start()
    #expect(world.ui.rendered.first?.model.header.sentence == "Checking bosun…")
    #expect(world.ui.rendered.first?.status == nil)
    #expect(world.ui.lastModel?.header.sentence == "bosun is running")
    await world.coordinator.stop()
  }

  @Test func aRefreshDrawsTheMenuTheStatusMakes() async {
    let world = await World(status: "status-warning")
    await world.coordinator.refresh(.timer)
    #expect(world.ui.lastModel?.header.dot == .yellow)
    #expect(world.itemIDs.contains(.showProblems))
    #expect(world.ui.rendered.last?.status != nil)
  }

  @Test func aBosunThatCannotBeReachedIsShownAndNothingElseRuns() async {
    let world = await World(status: nil)
    await world.cli.reply(Bosun.status.args, exited(1, "", err: "cannot execute\n"))
    await world.coordinator.refresh(.launch)
    #expect(world.ui.lastModel?.header.sentence == "bosun cannot be reached")
    #expect(
      world.itemIDs == [.helpMenu, .help(.guide), .help(.doctor), .help(.logs), .help(.uninstall), .quit])
  }

  /// Review focus 2: a hang keeps the last state, marked as not current.
  @Test func aHangKeepsTheLastStatusMarkedNotCurrent() async {
    let world = await World(status: nil)
    await world.reply(Bosun.status, exited(0, Fixtures.text("status-ok")))
    await world.reply(Bosun.status, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    await world.coordinator.refresh(.timer)
    await world.coordinator.refresh(.timer)
    #expect(world.ui.lastModel?.header.sentence == "bosun is running (not current)")
  }

  @Test func openAtLoginShowsWhatAppStatusSays() async {
    let on = await World(login: true)
    await on.coordinator.refresh(.launch)
    #expect(on.item(.openAtLogin)?.checked == true)
    let off = await World(login: false)
    await off.coordinator.refresh(.launch)
    #expect(off.item(.openAtLogin)?.checked == false)
  }

  @Test func theLoginIsLookedAtWhenTheMenuOpensAndNotEveryHalfMinute() async {
    let world = await World(login: false)
    await world.coordinator.refresh(.launch)
    await world.coordinator.refresh(.timer)
    await world.coordinator.refresh(.timer)
    let looks = await world.cli.calls.filter { $0 == Bosun.appStatus.args }
    #expect(looks.count == 1)
    await world.replace(Bosun.appStatus, exited(0, Fixtures.text("app-status")))
    await world.coordinator.menuWillOpen()
    #expect(world.item(.openAtLogin)?.checked == true)
  }

  @Test func anAppStatusThatCannotBeReadKeepsTheLoginAsItWas() async {
    let world = await World(login: true)
    await world.coordinator.refresh(.launch)
    await world.replace(Bosun.appStatus, exited(1, "", err: "nope\n"))
    await world.coordinator.menuWillOpen()
    #expect(world.item(.openAtLogin)?.checked == true)
  }

  /// Review focus 3: waking refreshes at once.
  @Test func aWakeRefreshesTheStatus() async {
    let world = await World()
    await world.coordinator.didWake()
    let looks = await world.cli.calls.filter { $0 == Bosun.status.args }
    #expect(looks.count == 1)
    #expect(world.ui.lastModel?.header.sentence == "bosun is running")
  }

  @Test func theMenuOpeningRefreshesTheStatus() async {
    let world = await World()
    await world.coordinator.menuWillOpen()
    #expect(world.ui.lastModel?.header.sentence == "bosun is running")
  }
}

@MainActor @Suite struct CoordinatorLaunchTests {
  @Test func openedByYouWhileStoppedItStartsBosunOnce() async {
    let world = await World(status: "status-stopped")
    await world.coordinator.refresh(.launch)
    await world.coordinator.refresh(.timer)
    #expect(await world.cli.started == [Bosun.start.args])
  }

  @Test func openedByYouInTheRedStateItStartsBosun() async {
    let world = await World(status: "status-error")
    await world.coordinator.refresh(.launch)
    #expect(await world.cli.started == [Bosun.start.args])
  }

  @Test func atLoginItOnlyShowsTheIcon() async {
    let world = await World(status: "status-stopped", atLogin: true)
    await world.coordinator.refresh(.launch)
    #expect(await world.cli.started.isEmpty)
    #expect(world.ui.lastModel?.header.dot == .grey)
  }

  @Test func whileRunningItStartsNothing() async {
    let world = await World(status: "status-ok")
    await world.coordinator.refresh(.launch)
    #expect(await world.cli.started.isEmpty)
  }

  /// The decision is of the first status only: a bosun that stops later is not started behind your back.
  @Test func aStopLaterOnIsNotStartedAgain() async {
    let world = await World(status: "status-ok")
    await world.coordinator.refresh(.launch)
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-stopped")))
    await world.coordinator.refresh(.timer)
    #expect(await world.cli.started.isEmpty)
  }

  @Test func ifTheFirstStatusIsNotThereTheDecisionWaitsForTheNext() async {
    let world = await World(status: nil)
    await world.cli.reply(Bosun.status.args, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    await world.reply(Bosun.status, exited(0, Fixtures.text("status-stopped")))
    await world.coordinator.refresh(.launch)
    #expect(await world.cli.started.isEmpty)
    await world.coordinator.refresh(.timer)
    #expect(await world.cli.started == [Bosun.start.args])
  }
}

@MainActor @Suite struct CoordinatorActionTests {
  @Test func openConsoleOpensTheAddressBosunGives() async {
    let world = await World()
    await world.coordinator.refresh(.launch)
    await world.reply(Bosun.consoleURL, exited(0, "http://127.0.0.1:8123/?t=abc\n"))
    await world.coordinator.perform(.openConsole)
    #expect(world.ui.opened == [URL(string: "http://127.0.0.1:8123/?t=abc")!])
  }

  @Test func openConsoleThatFailsShowsWhy() async {
    let world = await World()
    await world.coordinator.refresh(.launch)
    await world.reply(Bosun.consoleURL, exited(1, "", err: "the console is not running\n"))
    world.ui.answer("OK")
    await world.coordinator.perform(.openConsole)
    #expect(world.ui.opened.isEmpty)
    #expect(world.ui.lastSpec?.title == "Cannot open the console")
    #expect(world.ui.lastSpec?.message == "the console is not running")
  }

  @Test func openConsoleThatAnswersWithAnythingElseOpensNothing() async {
    let world = await World()
    await world.coordinator.refresh(.launch)
    await world.reply(Bosun.consoleURL, exited(0, "file:///etc/passwd\n"))
    world.ui.answer("OK")
    await world.coordinator.perform(.openConsole)
    #expect(world.ui.opened.isEmpty)
    #expect(world.ui.presented.count == 1)
  }

  @Test func startBosunStartsTheMorningRunAndLooksAgain() async {
    let world = await World(status: "status-stopped")
    await world.coordinator.refresh(.launch)
    let started = await world.cli.started.count  // the start at launch
    let looksBefore = await world.cli.calls.filter { $0 == Bosun.status.args }.count
    await world.coordinator.perform(.startBosun)
    #expect(await world.cli.started.count == started + 1)
    #expect(await world.cli.started.last == ["morning", "--now"])
    #expect(world.coordinator.flow.isIdle)
    // It looks at the status once it is started
    let looks = await world.cli.calls.filter { $0 == Bosun.status.args }
    #expect(looks.count == looksBefore + 1)
  }

  @Test func startBosunThatCannotStartShowsWhy() async {
    let world = await World(status: "status-stopped", atLogin: true)
    await world.coordinator.refresh(.launch)
    await world.cli.reply(
      Bosun.start.args, CommandResult(outcome: .couldNotStart("no permission"), stdout: "", stderr: ""))
    world.ui.answer("OK")
    await world.coordinator.perform(.startBosun)
    #expect(world.ui.lastSpec?.title == "bosun did not start")
    #expect(world.ui.lastSpec?.message == "no permission")
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func startBosunWithBosunMissingSaysSo() async {
    let world = await World(status: "status-stopped", atLogin: true)
    await world.coordinator.refresh(.launch)
    await world.cli.reply(
      Bosun.start.args, CommandResult(outcome: .notInstalled, stdout: "", stderr: ""))
    world.ui.answer("OK")
    await world.coordinator.perform(.startBosun)
    #expect(world.ui.lastSpec?.message == "bosun is not installed")
  }

  @Test func openAtLoginTurnsItOnAndShowsItChecked() async {
    let world = await World(login: false)
    await world.coordinator.refresh(.launch)
    #expect(world.item(.openAtLogin)?.checked == false)
    await world.reply(Bosun.appLogin(on: true), exited(0))
    await world.replace(Bosun.appStatus, exited(0, Fixtures.text("app-status")))
    await world.coordinator.perform(.openAtLogin)
    #expect(await world.commands.contains(["app", "login", "on"]))
    #expect(world.item(.openAtLogin)?.checked == true)
  }

  @Test func openAtLoginTurnsItOffWhenItIsOn() async {
    let world = await World(login: true)
    await world.coordinator.refresh(.launch)
    await world.reply(Bosun.appLogin(on: false), exited(0))
    let off = Fixtures.text("app-status").replacingOccurrences(
      of: "\"login\": true", with: "\"login\": false")
    await world.replace(Bosun.appStatus, exited(0, off))
    await world.coordinator.perform(.openAtLogin)
    #expect(await world.commands.contains(["app", "login", "off"]))
    #expect(world.item(.openAtLogin)?.checked == false)
  }

  @Test func openAtLoginThatFailsShowsWhyAndChangesNothing() async {
    let world = await World(login: false)
    await world.coordinator.refresh(.launch)
    await world.reply(Bosun.appLogin(on: true), exited(1, "", err: "bosun.app is not installed\n"))
    world.ui.answer("OK")
    await world.coordinator.perform(.openAtLogin)
    #expect(world.ui.lastSpec?.title == "Cannot change Open at Login")
    #expect(world.ui.lastSpec?.message == "bosun.app is not installed")
    #expect(world.item(.openAtLogin)?.checked == false)
  }

  @Test func showWhatsWrongListsTheProblems() async {
    let world = await World(status: "status-warning")
    await world.coordinator.refresh(.launch)
    world.ui.answer("OK")
    await world.coordinator.perform(.showProblems)
    let problems = Fixtures.status("status-warning").problems
    #expect(!problems.isEmpty)
    #expect(world.ui.lastSpec?.scroll == problems.joined(separator: "\n"))
  }

  @Test func theGuideIsTheDocsSite() async {
    let world = await World()
    await world.coordinator.refresh(.launch)
    await world.coordinator.perform(.help(.guide))
    #expect(world.ui.opened == [Links.guide])
  }

  @Test func showLogsOpensTheFolderStatusGave() async {
    let world = await World()
    await world.coordinator.refresh(.launch)
    await world.coordinator.perform(.help(.logs))
    #expect(world.ui.opened == [URL(fileURLWithPath: "/Users/me/.local/state/bosun/logs")])
  }

  @Test func runDoctorOpensAScriptThatRunsIt() async throws {
    let world = await World()
    await world.coordinator.refresh(.launch)
    await world.coordinator.perform(.help(.doctor))
    let url = try #require(world.ui.opened.first)
    #expect(url.lastPathComponent == "bosun-doctor.command")
    let text = try String(contentsOf: url, encoding: .utf8)
    #expect(text.contains("'/Users/me/.local/bin/bosun' 'doctor'"))
  }

  @Test func uninstallOpensAScriptThatRunsIt() async throws {
    let world = await World()
    await world.coordinator.refresh(.launch)
    await world.coordinator.perform(.help(.uninstall))
    let url = try #require(world.ui.opened.first)
    #expect(url.lastPathComponent == "bosun-uninstall.command")
    #expect(try String(contentsOf: url, encoding: .utf8).contains("'uninstall'"))
  }

  @Test func aScriptThatCannotBeWrittenShowsWhy() async {
    let world = await World()
    await world.coordinator.refresh(.launch)
    // A file where the folder should be
    try? Data().write(to: world.scripts)
    world.ui.answer("OK")
    await world.coordinator.perform(.help(.doctor))
    #expect(world.ui.opened.isEmpty)
    #expect(world.ui.lastSpec?.title == "Cannot open Terminal")
  }

  /// A greyed item cannot be chosen: a click that comes in anyway (the test hooks') is ignored.
  @Test func anItemThatIsGreyedIsIgnored() async {
    let world = await World(status: "status-stopped", atLogin: true)
    await world.coordinator.refresh(.launch)
    #expect(world.item(.openConsole)?.enabled == false)
    await world.coordinator.perform(.openConsole)
    #expect(await !world.commands.contains(Bosun.consoleURL.args))
    #expect(world.ui.opened.isEmpty)
  }

  @Test func anItemThatIsNotInTheMenuIsIgnored() async {
    let world = await World()
    await world.coordinator.refresh(.launch)
    await world.coordinator.perform(.startBosun)
    #expect(await world.cli.started.isEmpty)
  }

  @Test func theHelpMenuItselfDoesNothing() async {
    let world = await World()
    await world.coordinator.refresh(.launch)
    let drawn = world.ui.rendered.count
    await world.coordinator.perform(.helpMenu)
    #expect(world.ui.opened.isEmpty)
    #expect(world.ui.presented.isEmpty)
    #expect(world.ui.rendered.count == drawn)
  }
}
