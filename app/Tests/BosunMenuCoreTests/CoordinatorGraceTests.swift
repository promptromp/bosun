import BosunMenuTestSupport
import Foundation
import Testing

@testable import BosunMenuCore

// The grace an `update --yes` is given to show in `status` (AppCoordinator.startGrace): what comes of it when
// it runs out, and that it belongs to its own start.

private var available: UpdateCheck {
  get throws { try Fixtures.decode(UpdateCheck.self, "update-check-available") }
}

@MainActor
private func running() async throws -> World {
  let world = try await World(status: "status-ok")
  await world.refresh(.launch)
  return world
}

private func checkCalls(_ world: World) async -> Int {
  await world.cli.calls.filter { $0 == Bosun.checkUpdates.args }.count
}

@MainActor @Suite struct CoordinatorGraceTests {
  /// `starting` went on too long: look again, and if the same release is still offered, say so.
  @Test func anUpdateThatNeverShowsInStatusIsCalledAFailure() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    let before = await checkCalls(world)
    world.ui.answer("OK")
    // The grace the update was started with: 90 seconds from now on this clock
    #expect(await world.clock.waitForSleeper(until: world.clock.now.advanced(by: .seconds(90))))
    world.clock.advance(by: .seconds(89))
    #expect(world.clock.sleeperCount == 1)  // it did not wake, so nothing could have looked
    #expect(await checkCalls(world) == before)
    world.clock.advance(by: .seconds(1))
    #expect(await eventually { world.ui.presented.count == 3 })
    #expect(await checkCalls(world) == before + 1)
    #expect(world.ui.lastSpec?.message == updateNeverStartedText)
  }

  @Test func theGraceMeansNothingOnceTheUpdateIsRunning() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    #expect(await world.clock.waitForSleeper(until: world.clock.now.advanced(by: .seconds(90))))
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-running")))
    await world.refresh(.timer)
    // The update shows running: its grace is let go (no sleeper is left), and settle waits for it to end
    #expect(world.clock.sleeperCount == 0)
    let before = await checkCalls(world)
    world.clock.advance(by: .seconds(120))
    await world.coordinator.settle()
    #expect(await checkCalls(world) == before)
    #expect(world.ui.presented.count == 2)
  }

  /// A-7: a grace belongs to its own start: one let go never fires for a later start.
  @Test func anEarlierGraceNeverFiresForALaterStart() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    #expect(await world.clock.waitForSleeper(until: world.clock.now.advanced(by: .seconds(90))))
    // It shows running and finishes; a check says the same release is still offered
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-running")))
    await world.refresh(.timer)
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-ok")))
    await world.refresh(.timer)
    #expect(world.coordinator.update == .available(try available))
    #expect(world.clock.sleeperCount == 0)
    // A minute on, a second start: its grace is 90 s from then, not what is left of the first one's
    world.clock.advance(by: .seconds(60))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    #expect(await world.clock.waitForSleeper(until: world.clock.now.advanced(by: .seconds(90))))
    world.clock.advance(by: .seconds(40))
    await world.coordinator.settle()
    #expect(world.coordinator.update == .starting(try available))
  }

  /// A-4: with bosun's status unreadable (it speaks a newer contract), an update that ran out its grace is
  /// let go quietly: it may well be running, and no check can tell.
  @Test func anUpdateThatCannotBeWatchedIsLetGoWithoutAVerdict() async throws {
    let world = try await World(status: nil)
    await world.reply(Bosun.status, exited(0, try Fixtures.text("status-ok", contract: 2)))
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    await world.refresh(.launch)
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.updateBosun)
    #expect(world.coordinator.update == .starting(try available))
    #expect(world.item(.updateBosun)?.title == "Updating…")
    #expect(world.item(.updateBosun)?.enabled == false)
    // a second click while it starts is greyed, and does nothing
    await world.coordinator.perform(.updateBosun)
    #expect(await world.cli.started == [Bosun.update.args])
    let checks = await checkCalls(world)
    #expect(await world.clock.waitForSleeper(until: world.clock.now.advanced(by: .seconds(90))))
    world.clock.advance(by: .seconds(90))
    #expect(await eventually { world.coordinator.update == .idle })
    #expect(await checkCalls(world) == checks)
    #expect(world.ui.presented.count == 2)
    #expect(world.item(.updateBosun)?.enabled == true)
  }
}
