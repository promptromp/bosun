import Foundation
import Testing

@testable import BosunMenuCore

/// A date the test moves.
final class Today: @unchecked Sendable {
  private let lock = NSLock()
  private var date = Date(timeIntervalSince1970: 1_800_000_000)
  var now: Date { lock.withLock { date } }
  func advance(by seconds: TimeInterval) { lock.withLock { date = date.addingTimeInterval(seconds) } }
}

private var available: UpdateCheck {
  get throws { try Fixtures.decode(UpdateCheck.self, "update-check-available") }
}

@MainActor
private func running(status: String = "status-ok", today: Today = Today()) async throws -> World {
  let world = try await World(status: status, now: { today.now })
  // The look at launch finds no reply for a check (it fails quietly); a test then sets what a check says
  await world.refresh(.launch)
  return world
}

private func checkCalls(_ world: World) async -> Int {
  await world.cli.calls.filter { $0 == Bosun.checkUpdates.args }.count
}

@MainActor @Suite struct CoordinatorUpdateOfferTests {
  @Test func aCheckThatFindsAnUpdateOffersItWithItsNotes() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    world.ui.answer("Remind Me Later")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.presented.map(\.title) == ["Update Available: v2026.11.11"])
    #expect(try world.ui.lastSpec?.scroll == available.latest?.notes)
    // Remind Me Later: still on offer, with its badge, nothing run
    #expect(try world.coordinator.update == .available(available))
    #expect(world.item(.checkForUpdates)?.title == "Update Available: v2026.11.11…")
    #expect(world.ui.lastModel?.iconBadge == .update)
    #expect(world.coordinator.flow.isIdle)
    #expect(await world.cli.started.isEmpty)
  }

  @Test func skipThisVersionRecordsItThroughBosun() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.skip(version: "v2026.11.11"), exited(0))
    world.ui.answer("Skip This Version")
    await world.coordinator.perform(.checkForUpdates)
    #expect(await world.commands.contains(["update", "--skip", "v2026.11.11"]))
    #expect(world.coordinator.update == .idle)
    #expect(world.ui.lastModel?.iconBadge == nil)
    #expect(world.item(.checkForUpdates)?.title == "Check for Updates…")
  }

  @Test func aSkipThatFailsSaysSo() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.skip(version: "v2026.11.11"), exited(1, "", err: "cannot write the state\n"))
    world.ui.answer("Skip This Version")
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "Cannot skip this version")
    #expect(world.ui.lastSpec?.message == "cannot write the state")
  }

  @Test func updateNowShowsThePlanFirstAndOnlyThenStarts() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    #expect(
      world.ui.presented.map(\.title) == ["Update Available: v2026.11.11", "Update bosun to v2026.11.11?"])
    #expect(world.ui.lastSpec?.message.contains("These windows restart: acme-api") == true)
    // The plan came from the rehearsal, and `update --yes` was started detached, never waited for
    #expect(await world.commands.contains(Bosun.planUpdate.args))
    #expect(await world.cli.started == [Bosun.update.args])
    #expect(await !world.commands.contains(Bosun.update.args))
    #expect(try world.coordinator.update == .starting(available))
    #expect(world.item(.checkForUpdates)?.title == "Updating…")
    #expect(world.item(.checkForUpdates)?.enabled == false)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func theUpdateIsShownRunningOnceStatusSaysSo() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-running")))
    await world.refresh(.timer)
    #expect(world.item(.checkForUpdates)?.title == "Updating… waiting for 2 windows")
    #expect(world.coordinator.update == .running(waitingFor: ["acme-exporter", "acme-web"]))
  }

  @Test func cancellingThePlanGoesBackToTheOfferAndStartsNothing() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Cancel")
    await world.coordinator.perform(.checkForUpdates)
    #expect(try world.coordinator.update == .available(available))
    #expect(await world.cli.started.isEmpty)
  }

  @Test func aBlockedPlanSaysWhyAndStartsNothing() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan-blocked")))
    world.ui.answer("Update Now")
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "bosun cannot update now")
    #expect(world.ui.lastSpec?.message.contains("a migration of the knowledge store is missing") == true)
    #expect(try world.coordinator.update == .available(available))
    #expect(await world.cli.started.isEmpty)
  }

  @Test func aPlanThatCouldNotBeMadeShowsWhy() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(
      Bosun.planUpdate, exited(1, "{\"plan\": null}", err: "bosun update: a quit is in progress\n"))
    world.ui.answer("Update Now")
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "Cannot update bosun")
    #expect(world.ui.lastSpec?.message == "bosun update: a quit is in progress")
    #expect(world.coordinator.update == .idle)
  }

  @Test func anUpdateThatCouldNotBeStartedShowsWhy() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    await world.cli.reply(
      Bosun.update.args, CommandResult(outcome: .couldNotStart("no permission"), stdout: "", stderr: ""))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "Cannot update bosun")
    #expect(world.ui.lastSpec?.message == "no permission")
    #expect(world.coordinator.update == .idle)
  }

  @Test func anUpdateThatHasFinishedMakesANewCheck() async throws {
    let world = try await running()
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-running")))
    await world.refresh(.timer)
    #expect(world.coordinator.update == .running(waitingFor: ["acme-exporter", "acme-web"]))
    let before = await checkCalls(world)
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-ok")))
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-current")))
    await world.refresh(.timer)
    #expect(await checkCalls(world) == before + 1)
    #expect(world.coordinator.update == .idle)
  }
}

@MainActor @Suite struct CoordinatorUpdateCheckTests {
  @Test func nothingNewSaysBosunIsUpToDate() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-current")))
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "bosun is up to date")
    #expect(world.coordinator.update == .idle)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func aCheckThatFailedShowsWhy() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-failed")))
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "Cannot check for updates")
    #expect(world.ui.lastSpec?.message.contains("could not reach the origin") == true)
    #expect(world.coordinator.update == .idle)
  }

  @Test func aCheckThatHungShowsThat() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.message == "bosun did not answer in time")
  }

  @Test func theChecksGreyedItemShowsWhileItRuns() async throws {
    let world = try await running()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-current")))
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    let titles = world.ui.rendered.compactMap { entry in
      entry.model.sections.flatMap { $0 }.first { $0.id == .checkForUpdates }?.title
    }
    #expect(titles.contains("Checking for Updates…"))
  }

  /// The launch look is the first check, quietly: a badge and no dialog.
  @Test func theCheckAtLaunchOnlyShowsABadge() async throws {
    let world = try await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.refresh(.launch)
    #expect(try world.coordinator.update == .available(available))
    #expect(world.ui.lastModel?.iconBadge == .update)
    #expect(world.ui.presented.isEmpty)
  }

  @Test func aCheckThatFailsInTheBackgroundSaysNothing() async throws {
    let world = try await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-failed")))
    await world.refresh(.launch)
    #expect(world.coordinator.update == .idle)
    #expect(world.ui.presented.isEmpty)
  }

  @Test func aSkippedReleaseIsNotOfferedInTheBackground() async throws {
    let world = try await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-skipped")))
    await world.refresh(.launch)
    #expect(world.coordinator.update == .idle)
    #expect(world.ui.lastModel?.iconBadge == nil)
  }

  @Test func checksAreSixHoursAndAMinuteApart() async throws {
    let today = Today()
    let world = try await running(today: today)
    // The launch look was the first check
    #expect(await checkCalls(world) == 1)
    await world.refresh(.timer)
    await world.menuWillOpen()
    #expect(await checkCalls(world) == 1)
    today.advance(by: 61)
    await world.menuWillOpen()
    #expect(await checkCalls(world) == 2)
    today.advance(by: 6 * 3600)
    await world.refresh(.timer)
    #expect(await checkCalls(world) == 3)
  }

  @Test func clickingAnOfferedUpdateOffersItAgainWithoutAskingBosun() async throws {
    let world = try await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.refresh(.launch)
    let before = await checkCalls(world)
    world.ui.answer("Remind Me Later")
    await world.coordinator.perform(.checkForUpdates)
    #expect(await checkCalls(world) == before)
    #expect(world.ui.presented.map(\.title) == ["Update Available: v2026.11.11"])
  }

  @Test func whileADialogIsOpenQuitAndTheRestAreIgnored() async throws {
    let world = try await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.refresh(.launch)
    world.ui.whileOpen = { _ in
      await world.coordinator.perform(.quit)
      await world.coordinator.perform(.checkForUpdates)
      await world.coordinator.perform(.startBosun)
    }
    world.ui.answer("Remind Me Later")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.presented.count == 1)
    #expect(world.ui.terminated == 0)
    #expect(await !world.commands.contains(Bosun.quitPlan.args))
  }

  /// bosun speaks a contract this app does not: "Update bosun…" is the update flow from its check.
  @Test func updateBosunRunsTheUpdateFlow() async throws {
    let world = try await World(status: nil)
    await world.reply(Bosun.status, exited(0, try Fixtures.text("status-ok", contract: 2)))
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.refresh(.launch)
    #expect(world.item(.updateBosun) != nil)
    world.ui.answer("Remind Me Later")
    await world.coordinator.perform(.updateBosun)
    #expect(world.ui.presented.map(\.title) == ["Update Available: v2026.11.11"])
    #expect(try world.coordinator.update == .available(available))
  }

  /// A-8: a check the person asked for that found an update is never "up to date", even when the state moved
  /// on before its dialog (a status showed the update running meanwhile).
  @Test func aCheckThatFoundAnUpdateIsNeverUpToDate() async throws {
    let gate = Gate()
    await gate.open()  // the launch's own check goes through
    let world = try await World(status: "status-ok", gates: [Bosun.checkUpdates.args: gate])
    await world.refresh(.launch)
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-running")))
    await gate.arm()
    let check = Task { await world.coordinator.perform(.checkForUpdates) }
    #expect(await gate.waitForWaiter())
    // while the check waits, a status shows the update running
    await world.coordinator.refresh(.timer)
    await gate.open()
    await check.value
    #expect(world.coordinator.update == .running(waitingFor: ["acme-exporter", "acme-web"]))
    #expect(!world.ui.presented.contains { $0.title == "bosun is up to date" })
  }

  @Test func updateBosunWithNothingToOfferSaysSo() async throws {
    let world = try await World(status: nil)
    await world.reply(Bosun.status, exited(0, try Fixtures.text("status-ok", contract: 2)))
    await world.reply(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-current")))
    await world.refresh(.launch)
    world.ui.answer("OK")
    await world.coordinator.perform(.updateBosun)
    #expect(world.ui.lastSpec?.title == "bosun is up to date")
  }
}
