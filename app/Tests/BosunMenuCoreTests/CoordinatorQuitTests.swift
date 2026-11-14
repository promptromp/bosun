import Foundation
import Testing

@testable import BosunMenuCore

@MainActor @Suite struct CoordinatorQuitTests {
  @Test func whileStoppedQuitJustLeavesTheApp() async throws {
    let world = try await readyToQuit(status: "status-stopped")
    #expect(world.item(.quitApp)?.title == "Quit bosun.app")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.terminated == 1)
    #expect(world.ui.presented.isEmpty)
    #expect(await !world.commands.contains(Bosun.quitPlan.args))
  }

  @Test func withBosunMissingQuitJustLeavesTheApp() async throws {
    let world = try await World(status: nil)
    await world.reply(Bosun.status, CommandResult(outcome: .notInstalled, stdout: "", stderr: ""))
    await world.refresh(.launch)
    #expect(world.item(.quitApp)?.title == "Quit bosun.app")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.terminated == 1)
    #expect(world.ui.presented.isEmpty)
  }

  @Test func withNothingBusyOneDialogAndTheQuitRunsWithTheDefaultChoices() async throws {
    let world = try await readyToQuit()
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Stop bosun")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.map(\.title) == ["Quit bosun.app?", "Stop bosun?"])
    #expect(await world.commands.contains(letFinishCommand))
    #expect(world.ui.terminated == 1)
  }

  @Test func theBoxesTheAnswerSetBecomeTheCommand() async throws {
    let world = try await readyToQuit()
    let command = ["quit", "--let-finish=10", "--json"]
    await world.reply(Command(args: command, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun", boxes: ["pauseMorning": false, "sleep": false])
    await world.coordinator.perform(.stopBosun)
    #expect(await world.commands.contains(command))
    #expect(world.ui.terminated == 0)
  }

  @Test func theQuitWaitsAsLongAsAnAgentMayTake() async throws {
    let world = try await readyToQuit()
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun")
    await world.coordinator.perform(.stopBosun)
    // The plan's ten minutes, its 180 s of lock wait, the 225 s of its other steps and a minute of margin
    #expect(await timeouts(of: letFinishCommand, in: world) == [.seconds(1065)])  // 600 + 180 + 225 + 60
  }

  /// A-I1: stopping them now can wait the plan's whole lock wait for a start that holds it, then the console
  /// and `bosun stop`: the app's timeout outlasts all of it.
  @Test func stoppingThemNowWaitsAsLongAsTheLockAndTheStopMayTake() async throws {
    let world = try await readyToQuit(busy: true)
    await world.reply(
      Command(args: stopNowCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun")
    world.ui.answer("Stop Them Now")
    await world.coordinator.perform(.stopBosun)
    #expect(await timeouts(of: stopNowCommand, in: world) == [.seconds(465)])  // 180 + 225 + 60
  }

  /// A-14: while bosun quit runs, the menu says what it waits for.
  @Test func whileTheQuitRunsTheMenuSaysWhatItWaitsFor() async throws {
    let gate = Gate()
    let world = try await World(status: "status-ok", atLogin: true, gates: [letFinishCommand: gate])
    await world.reply(Bosun.quitPlan, exited(0, try planText(busy: true)))
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    await world.refresh(.launch)
    world.ui.answer("Stop bosun")
    world.ui.answer("Let Them Finish")
    let quitting = Task { await world.coordinator.perform(.stopBosun) }
    #expect(await gate.waitForWaiter())
    #expect(world.ui.lastModel?.header.detail.last == "Quitting: waiting for acme-exporter to finish…")
    await gate.open()
    await quitting.value
    #expect(world.coordinator.flow.detail == nil)
    #expect(world.ui.lastModel?.header.detail.contains { $0.hasPrefix("Quitting") } == false)
  }

  @Test func stopLeavesTheAppRunningAndLooksOnceMore() async throws {
    let world = try await readyToQuit()
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    let before = await statusLooks(world)
    world.ui.answer("Stop bosun")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.terminated == 0)
    #expect(await statusLooks(world) == before + 1)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func aConsoleThatIsStillRunningIsToldAfterwards() async throws {
    let world = try await readyToQuit()
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)),
      try quitReply("quit-result-stopped-console-running"))
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Stop bosun")
    world.ui.answer("OK")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.map(\.title) == ["Quit bosun.app?", "Stop bosun?", "bosun is stopped"])
    #expect(world.ui.lastSpec?.message.contains("8765") == true)
    #expect(world.ui.terminated == 1)
  }

  @Test func cancellingTheFirstDialogRunsNothingAndPollsAgain() async throws {
    let world = try await readyToQuit()
    let before = await statusLooks(world)
    world.ui.answer("Cancel")
    await world.coordinator.perform(.stopBosun)
    #expect(await world.commands.filter { $0.first == "quit" && $0 != Bosun.quitPlan.args }.isEmpty)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
    #expect(await statusLooks(world) == before + 1)
  }

  @Test func aQuitThatIsRefusedSaysWhy() async throws {
    let world = try await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, try quitReply("quit-plan-refused"))
    await world.refresh(.launch)
    world.ui.answer("OK")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.lastSpec?.title == "Quit did not finish")
    #expect(world.ui.lastSpec?.message.contains("update is running") == true)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func aQuitThatFailedSaysWhyAndTheAppStays() async throws {
    let world = try await readyToQuit()
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-failed"))
    world.ui.answer("Stop bosun")
    world.ui.answer("OK")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.lastSpec?.title == "Quit did not finish")
    #expect(world.ui.lastSpec?.message.contains("bosun quit --stop-now") == true)
    #expect(world.ui.terminated == 0)
  }

  @Test func aPlanThatCouldNotLookOffersStopNow() async throws {
    let world = try await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, try Fixtures.reply("quit-plan-failed"))
    await world.reply(
      Command(args: stopNowCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    await world.refresh(.launch)
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Stop Now")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.dropFirst().first?.buttons == ["Cancel", "Stop Now", "Quit bosun.app"])
    #expect(await world.commands.contains(stopNowCommand))
    // the failure object's own wait: its lock wait and steps, not a fixed two minutes
    #expect(await world.cli.timeouts.last == .seconds(465))  // 180 + 225 + 60
    #expect(world.ui.terminated == 1)
  }

  @Test func aPlanThatCouldNotLookCanLeaveOnlyTheApp() async throws {
    let world = try await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, try Fixtures.reply("quit-plan-failed"))
    await world.refresh(.launch)
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Quit bosun.app")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.terminated == 1)
    #expect(await world.commands.filter { $0.first == "quit" } == [Bosun.quitPlan.args])
  }

  /// P1: a refusal on a non-zero exit is only told: a Stop Now would be refused too.
  @Test func aPlanRefusedOnAFailedExitOffersNothingThatWouldBeRefusedAgain() async throws {
    let world = try await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, exited(1, try Fixtures.text("quit-result-refused")))
    await world.refresh(.launch)
    world.ui.answer("OK")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.presented.map(\.title) == ["Quit did not finish"])
    #expect(world.ui.lastSpec?.buttons == ["OK"])
    #expect(world.ui.lastSpec?.message.contains("update is running") == true)
    #expect(world.ui.terminated == 0)
  }

  @Test func aPlanThatCouldNotLookCanBeCancelled() async throws {
    let world = try await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, try quitReply("quit-plan-failed"))
    await world.refresh(.launch)
    world.ui.answer("Cancel")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.terminated == 0)
    #expect(await world.commands.filter { $0.first == "quit" }.count == 1)
  }

  /// A-5: a plan that hangs is no dead end: it says so, and offers Stop Now (with the longest timeout, since
  /// bosun said nothing of its waits) and a way to leave only the app.
  @Test func aPlanThatHangsOffersStopNowAndLeavingTheApp() async throws {
    let world = try await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    await world.reply(
      Command(args: stopNowCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    await world.refresh(.launch)
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Stop Now")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.dropFirst().first?.title == "bosun cannot say what quitting would stop")
    #expect(world.ui.presented.dropFirst().first?.message.hasPrefix("bosun did not answer in time") == true)
    #expect(await world.commands.contains(stopNowCommand))
    #expect(await world.cli.timeouts.last == .seconds(60 * 60))
    #expect(world.ui.terminated == 1)
  }
}

@MainActor @Suite struct CoordinatorBusyQuitTests {
  @Test func busyAgentsGetASecondQuestionNamingThem() async throws {
    let world = try await readyToQuit(busy: true)
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Stop bosun")
    world.ui.answer("Let Them Finish")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.map(\.title).count == 3)
    #expect(world.ui.lastSpec?.message.contains("acme-exporter") == true)
    #expect(await world.commands.contains(letFinishCommand))
    #expect(world.ui.terminated == 1)
  }

  @Test func stoppingThemNowIsTheOtherCommand() async throws {
    let world = try await readyToQuit(busy: true)
    await world.reply(
      Command(args: stopNowCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun")
    world.ui.answer("Stop Them Now")
    await world.coordinator.perform(.stopBosun)
    #expect(await world.commands.contains(stopNowCommand))
    #expect(await !world.commands.contains(letFinishCommand))
  }

  @Test func cancellingTheSecondQuestionRunsNoQuit() async throws {
    let world = try await readyToQuit(busy: true)
    world.ui.answer("Stop bosun")
    world.ui.answer("Cancel")
    await world.coordinator.perform(.stopBosun)
    #expect(await world.commands.filter { $0.first == "quit" && $0 != Bosun.quitPlan.args }.isEmpty)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
  }

  /// The wait ran out with an agent still busy: ask again, and keep waiting is the default.
  @Test func anAgentStillBusyAfterTheWaitIsAskedAboutAgain() async throws {
    let world = try await readyToQuit(busy: true)
    let command = Command(args: letFinishCommand, timeout: .seconds(1))
    await world.reply(command, try quitReply("quit-result-busy"))
    await world.reply(command, try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Stop bosun")
    world.ui.answer("Let Them Finish")
    world.ui.answer("Keep Waiting")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.map(\.title).count == 4)
    #expect(world.ui.lastSpec?.buttons == ["Keep Waiting", "Stop It Now", "Cancel Quit"])
    #expect(await world.commands.filter { $0 == letFinishCommand }.count == 2)
    #expect(world.ui.terminated == 1)
  }

  @Test func stopItNowAfterTheWaitStopsThem() async throws {
    let world = try await readyToQuit(busy: true)
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-busy"))
    await world.reply(
      Command(args: stopNowCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Stop bosun")
    world.ui.answer("Let Them Finish")
    world.ui.answer("Stop It Now")
    await world.coordinator.perform(.quitApp)
    #expect(await world.commands.contains(stopNowCommand))
    #expect(world.ui.terminated == 1)
  }

  @Test func cancelQuitAfterTheWaitLeavesBosunAsItIs() async throws {
    let world = try await readyToQuit(busy: true)
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-busy"))
    world.ui.answer("Stop bosun")
    world.ui.answer("Let Them Finish")
    world.ui.answer("Cancel Quit")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
  }
}

/// Review focus 4, and the rule that the app does not poll while its own quit runs.
@MainActor @Suite struct CoordinatorQuitFlowRulesTests {
  @Test func nothingIsPolledWhileTheQuitRunsAndOneLookFollowsIt() async throws {
    let world = try await readyToQuit(busy: true)
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    let before = await statusLooks(world)
    var duringDialogs: [Int] = []
    world.ui.whileOpen = { _ in
      // A timer tick, the menu opening and a wake, while the dialog is up
      await world.refresh(.timer)
      await world.menuWillOpen()
      await world.didWake()
      duringDialogs.append(await statusLooks(world))
    }
    world.ui.answer("Stop bosun")
    world.ui.answer("Let Them Finish")
    await world.coordinator.perform(.stopBosun)
    #expect(duringDialogs == [before, before])
    #expect(await statusLooks(world) == before + 1)
  }

  @Test func theMenuIsGreyedWhileTheQuitRuns() async throws {
    let world = try await readyToQuit()
    var greyed: [Bool] = []
    world.ui.whileOpen = { _ in
      greyed.append(
        world.coordinator.menuModel.sections.flatMap { $0 }.filter {
          $0.id == .stopBosun || $0.id == .quitApp
        }.allSatisfy {
          !$0.enabled
        })
    }
    world.ui.answer("Cancel")
    await world.coordinator.perform(.stopBosun)
    #expect(greyed == [true])
  }

  @Test func aSecondQuitWhileOneRunsIsIgnored() async throws {
    let world = try await readyToQuit()
    world.ui.whileOpen = { _ in
      await world.coordinator.perform(.stopBosun)
      await world.coordinator.perform(.quitApp)
      await world.coordinator.perform(.checkForUpdates)
    }
    world.ui.answer("Cancel")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.presented.count == 1)
    #expect(await world.commands.filter { $0 == Bosun.quitPlan.args }.count == 1)
  }
}
