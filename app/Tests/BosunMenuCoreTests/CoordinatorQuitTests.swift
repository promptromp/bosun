import Foundation
import Testing

@testable import BosunMenuCore

private let letFinish = ["quit", "--let-finish", "--pause-morning", "--sleep", "--json"]
private let stopNow = ["quit", "--stop-now", "--pause-morning", "--sleep", "--json"]

/// The fixture's plan, with or without its busy agent.
private func planText(busy: Bool) -> String {
  let text = Fixtures.text("quit-plan")
  return busy
    ? text : text.replacingOccurrences(of: "\"busy\": [\n    \"acme-exporter\"\n  ]", with: "\"busy\": []")
}

private func quitReply(_ fixture: String, code: Int32 = 0) -> CommandResult {
  exited(code, Fixtures.text(fixture))
}

@MainActor
private func ready(busy: Bool = false, status: String = "status-ok") async -> World {
  let world = await World(status: status, atLogin: true)
  await world.reply(Bosun.quitPlan, exited(0, planText(busy: busy)))
  await world.refresh(.launch)
  return world
}

private func statusLooks(_ world: World) async -> Int {
  await world.cli.calls.filter { $0 == Bosun.status.args }.count
}

@MainActor @Suite struct CoordinatorQuitTests {
  @Test func whileStoppedQuitJustLeavesTheApp() async {
    let world = await ready(status: "status-stopped")
    #expect(world.item(.quit)?.title == "Quit bosun")
    await world.coordinator.perform(.quit)
    #expect(world.ui.terminated == 1)
    #expect(world.ui.presented.isEmpty)
    #expect(await !world.commands.contains(Bosun.quitPlan.args))
  }

  @Test func withBosunMissingQuitJustLeavesTheApp() async {
    let world = await World(status: nil)
    await world.reply(Bosun.status, CommandResult(outcome: .notInstalled, stdout: "", stderr: ""))
    await world.refresh(.launch)
    #expect(world.item(.quit)?.title == "Quit bosun.app")
    await world.coordinator.perform(.quit)
    #expect(world.ui.terminated == 1)
    #expect(world.ui.presented.isEmpty)
  }

  @Test func withNothingBusyOneDialogAndTheQuitRunsWithTheDefaultChoices() async {
    let world = await ready()
    await world.reply(Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    world.ui.answer("Quit bosun")
    await world.coordinator.perform(.quit)
    #expect(world.ui.presented.map(\.title) == ["Quit bosun?"])
    #expect(await world.commands.contains(letFinish))
    #expect(world.ui.terminated == 1)
  }

  @Test func theBoxesTheAnswerSetBecomeTheCommand() async {
    let world = await ready()
    let command = ["quit", "--let-finish", "--json"]
    await world.reply(Command(args: command, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    world.ui.answer("Quit bosun", boxes: ["pauseMorning": false, "sleep": false])
    await world.coordinator.perform(.quit)
    #expect(await world.commands.contains(command))
    #expect(world.ui.terminated == 1)
  }

  @Test func theQuitWaitsAsLongAsAnAgentMayTake() async {
    let world = await ready()
    await world.reply(Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    world.ui.answer("Quit bosun")
    await world.coordinator.perform(.quit)
    let timeouts = await world.cli.timeouts
    #expect(timeouts.last == Bosun.quit(QuitChoices()).timeout)
  }

  @Test func keepingTheIconLeavesTheAppRunningAndLooksOnceMore() async {
    let world = await ready()
    await world.reply(Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    let before = await statusLooks(world)
    world.ui.answer("Quit bosun", boxes: ["keepIcon": true])
    await world.coordinator.perform(.quit)
    #expect(world.ui.terminated == 0)
    #expect(await statusLooks(world) == before + 1)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func aConsoleThatIsStillRunningIsToldAfterwards() async {
    let world = await ready()
    await world.reply(
      Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-stopped-console-running"))
    world.ui.answer("Quit bosun")
    world.ui.answer("OK")
    await world.coordinator.perform(.quit)
    #expect(world.ui.presented.map(\.title) == ["Quit bosun?", "bosun is stopped"])
    #expect(world.ui.lastSpec?.message.contains("8765") == true)
    #expect(world.ui.terminated == 1)
  }

  @Test func cancellingTheFirstDialogRunsNothingAndPollsAgain() async {
    let world = await ready()
    let before = await statusLooks(world)
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quit)
    #expect(await world.commands.filter { $0.first == "quit" && $0 != Bosun.quitPlan.args }.isEmpty)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
    #expect(await statusLooks(world) == before + 1)
  }

  @Test func aQuitThatIsRefusedSaysWhy() async {
    let world = await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, quitReply("quit-plan-refused"))
    await world.refresh(.launch)
    world.ui.answer("OK")
    await world.coordinator.perform(.quit)
    #expect(world.ui.lastSpec?.title == "Quit did not finish")
    #expect(world.ui.lastSpec?.message.contains("update is running") == true)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func aQuitThatFailedSaysWhyAndTheAppStays() async {
    let world = await ready()
    await world.reply(
      Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-failed", code: 1))
    world.ui.answer("Quit bosun")
    world.ui.answer("OK")
    await world.coordinator.perform(.quit)
    #expect(world.ui.lastSpec?.title == "Quit did not finish")
    #expect(world.ui.lastSpec?.message.contains("bosun quit --stop-now") == true)
    #expect(world.ui.terminated == 0)
  }

  @Test func aPlanThatCouldNotLookOffersStopNow() async {
    let world = await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, quitReply("quit-plan-failed", code: 1))
    await world.reply(Command(args: stopNow, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    await world.refresh(.launch)
    world.ui.answer("Stop Now")
    await world.coordinator.perform(.quit)
    #expect(world.ui.presented.first?.buttons == ["Cancel", "Stop Now"])
    #expect(await world.commands.contains(stopNow))
    #expect(world.ui.terminated == 1)
  }

  @Test func aPlanThatCouldNotLookCanBeCancelled() async {
    let world = await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, quitReply("quit-plan-failed", code: 1))
    await world.refresh(.launch)
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quit)
    #expect(world.ui.terminated == 0)
    #expect(await world.commands.filter { $0.first == "quit" }.count == 1)
  }

  @Test func aPlanThatHangsFailsWithThat() async {
    let world = await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    await world.refresh(.launch)
    world.ui.answer("OK")
    await world.coordinator.perform(.quit)
    #expect(world.ui.lastSpec?.message == "bosun did not answer in time")
  }
}

@MainActor @Suite struct CoordinatorBusyQuitTests {
  @Test func busyAgentsGetASecondQuestionNamingThem() async {
    let world = await ready(busy: true)
    await world.reply(Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    world.ui.answer("Quit bosun")
    world.ui.answer("Let Them Finish")
    await world.coordinator.perform(.quit)
    #expect(world.ui.presented.map(\.title).count == 2)
    #expect(world.ui.lastSpec?.message.contains("acme-exporter") == true)
    #expect(await world.commands.contains(letFinish))
    #expect(world.ui.terminated == 1)
  }

  @Test func stoppingThemNowIsTheOtherCommand() async {
    let world = await ready(busy: true)
    await world.reply(Command(args: stopNow, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    world.ui.answer("Quit bosun")
    world.ui.answer("Stop Them Now")
    await world.coordinator.perform(.quit)
    #expect(await world.commands.contains(stopNow))
    #expect(await !world.commands.contains(letFinish))
  }

  @Test func cancellingTheSecondQuestionRunsNoQuit() async {
    let world = await ready(busy: true)
    world.ui.answer("Quit bosun")
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quit)
    #expect(await world.commands.filter { $0.first == "quit" && $0 != Bosun.quitPlan.args }.isEmpty)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
  }

  /// The wait ran out with an agent still busy: ask again, and keep waiting is the default.
  @Test func anAgentStillBusyAfterTheWaitIsAskedAboutAgain() async {
    let world = await ready(busy: true)
    let command = Command(args: letFinish, timeout: .seconds(1))
    await world.reply(command, quitReply("quit-result-busy", code: 3))
    await world.reply(command, quitReply("quit-result-stopped"))
    world.ui.answer("Quit bosun")
    world.ui.answer("Let Them Finish")
    world.ui.answer("Keep Waiting")
    await world.coordinator.perform(.quit)
    #expect(world.ui.presented.map(\.title).count == 3)
    #expect(world.ui.lastSpec?.buttons == ["Keep Waiting", "Stop It Now", "Cancel Quit"])
    #expect(await world.commands.filter { $0 == letFinish }.count == 2)
    #expect(world.ui.terminated == 1)
  }

  @Test func stopItNowAfterTheWaitStopsThem() async {
    let world = await ready(busy: true)
    await world.reply(Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-busy", code: 3))
    await world.reply(Command(args: stopNow, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    world.ui.answer("Quit bosun")
    world.ui.answer("Let Them Finish")
    world.ui.answer("Stop It Now")
    await world.coordinator.perform(.quit)
    #expect(await world.commands.contains(stopNow))
    #expect(world.ui.terminated == 1)
  }

  @Test func cancelQuitAfterTheWaitLeavesBosunAsItIs() async {
    let world = await ready(busy: true)
    await world.reply(Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-busy", code: 3))
    world.ui.answer("Quit bosun")
    world.ui.answer("Let Them Finish")
    world.ui.answer("Cancel Quit")
    await world.coordinator.perform(.quit)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
  }
}

/// Review focus 4, and the rule that the app does not poll while its own quit runs.
@MainActor @Suite struct CoordinatorQuitFlowRulesTests {
  @Test func nothingIsPolledWhileTheQuitRunsAndOneLookFollowsIt() async {
    let world = await ready(busy: true)
    await world.reply(Command(args: letFinish, timeout: .seconds(1)), quitReply("quit-result-stopped"))
    let before = await statusLooks(world)
    var duringDialogs: [Int] = []
    world.ui.whileOpen = { _ in
      // A timer tick, the menu opening and a wake, while the dialog is up
      await world.refresh(.timer)
      await world.menuWillOpen()
      await world.didWake()
      duringDialogs.append(await statusLooks(world))
    }
    world.ui.answer("Quit bosun", boxes: ["keepIcon": true])
    world.ui.answer("Let Them Finish")
    await world.coordinator.perform(.quit)
    #expect(duringDialogs == [before, before])
    #expect(await statusLooks(world) == before + 1)
  }

  @Test func theMenuIsGreyedWhileTheQuitRuns() async {
    let world = await ready()
    var greyed: [Bool] = []
    world.ui.whileOpen = { _ in
      greyed.append(
        world.coordinator.menuModel.sections.flatMap { $0 }.filter { $0.id == .quit }.allSatisfy {
          !$0.enabled
        })
    }
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quit)
    #expect(greyed == [true])
  }

  @Test func aSecondQuitWhileOneRunsIsIgnored() async {
    let world = await ready()
    world.ui.whileOpen = { _ in
      await world.coordinator.perform(.quit)
      await world.coordinator.perform(.checkForUpdates)
    }
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quit)
    #expect(world.ui.presented.count == 1)
    #expect(await world.commands.filter { $0 == Bosun.quitPlan.args }.count == 1)
  }
}
