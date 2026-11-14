import BosunMenuTestSupport
import Foundation
import Testing

@testable import BosunMenuCore

// While an update is under way the status is looked at every few seconds, so its end is drawn at once; the
// 30 seconds come back when it is over.

private var available: UpdateCheck {
  get throws { try Fixtures.decode(UpdateCheck.self, "update-check-available") }
}

private func statusCalls(_ world: World) async -> Int {
  await world.cli.calls.filter { $0 == Bosun.status.args }.count
}

@Suite struct PromptLookRuleTests {
  @Test func anUpdateStartedOrRunningAsksForPromptLooks() throws {
    let snapshot = try Fixtures.decode(StatusSnapshot.self, "status-update-running")
    let current = StatusState.loaded(snapshot, current: true)
    #expect(wantsPromptLooks(.starting(try available), status: current))
    // Before any status was read too: the grace bounds it
    #expect(wantsPromptLooks(.starting(try available), status: nil))
    #expect(wantsPromptLooks(.running(waitingFor: []), status: current))
  }

  @Test func aStatusThatCannotBeReadIsNoReasonToAskMoreOften() throws {
    let snapshot = try Fixtures.decode(StatusSnapshot.self, "status-update-running")
    #expect(!wantsPromptLooks(.running(waitingFor: []), status: .loaded(snapshot, current: false)))
    #expect(!wantsPromptLooks(.running(waitingFor: []), status: .unreachable("hung")))
    #expect(!wantsPromptLooks(.running(waitingFor: []), status: .contractTooNew(9)))
    #expect(!wantsPromptLooks(.running(waitingFor: []), status: nil))
  }

  @Test func nothingUnderWayLooksAtTheNormalPace() throws {
    let snapshot = try Fixtures.decode(StatusSnapshot.self, "status-ok")
    let current = StatusState.loaded(snapshot, current: true)
    for state: UpdateState in [
      .idle, .checking, .available(try available), .planning(try available), .checkingStart(try available),
      .failed("no"),
    ] {
      #expect(!wantsPromptLooks(state, status: current), "\(state)")
    }
  }
}

@MainActor @Suite struct CoordinatorPromptLookTests {
  @Test func anUpdateTheAppStartsIsLookedAtPromptlyUntilItIsOver() async throws {
    let world = try await World(status: "status-ok")
    await world.refresh(.launch)
    #expect(!world.coordinator.looksPromptly)
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    // Started, not yet in the status
    #expect(try world.coordinator.update == .starting(available))
    #expect(world.coordinator.looksPromptly)
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-running")))
    await world.refresh(.timer)
    #expect(world.coordinator.looksPromptly)
    // It has finished: the check after it runs, and the looks go back to every 30 seconds
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-current")))
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-ok")))
    await world.refresh(.timer)
    #expect(world.coordinator.update == .idle)
    #expect(!world.coordinator.looksPromptly)
  }

  @Test func aStatusThatHangsDuringAnUpdateGoesBackToTheNormalPace() async throws {
    let world = try await World(status: "status-update-running")
    await world.refresh(.launch)
    #expect(world.coordinator.looksPromptly)
    await world.replace(Bosun.status, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    await world.refresh(.timer)
    #expect(!world.coordinator.looksPromptly)
  }

  /// The poller itself: every 2 seconds while the update runs, so its end is drawn 2 seconds after it, not 30.
  @Test func thePollerLooksEveryTwoSecondsWhileAnUpdateRuns() async throws {
    let world = try await World(status: "status-update-running")
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-current")))
    await world.coordinator.start()
    #expect(world.coordinator.update == .running(waitingFor: ["acme-exporter", "acme-web"]))
    #expect(world.coordinator.looksPromptly)
    #expect(await world.clock.waitForSleeper(until: world.clock.now.advanced(by: .seconds(2))))
    let before = await statusCalls(world)
    world.clock.advance(by: .seconds(2))
    #expect(await eventually { await statusCalls(world) == before + 1 })
    // The update ends: the next look, 2 seconds on, draws it over
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-ok")))
    #expect(await world.clock.waitForSleeper(until: world.clock.now.advanced(by: .seconds(2))))
    world.clock.advance(by: .seconds(2))
    let waiting = UpdateState.running(waitingFor: ["acme-exporter", "acme-web"])
    #expect(await eventually { world.coordinator.update != waiting })
    #expect(await eventually { !world.coordinator.looksPromptly })
    await world.coordinator.settle()
    #expect(world.coordinator.update == .idle)
    // and the timer waits 30 seconds again
    #expect(await world.clock.waitForSleeper(until: world.clock.now.advanced(by: .seconds(30))))
    await world.coordinator.stop()
  }
}
