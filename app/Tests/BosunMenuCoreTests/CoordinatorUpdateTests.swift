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

private let available = Fixtures.decode(UpdateCheck.self, "update-check-available")

@MainActor
private func running(status: String = "status-ok", today: Today = Today()) async -> World {
  let world = await World(status: status, now: { today.now })
  // The look at launch finds no reply for a check (it fails quietly); a test then sets what a check says
  await world.coordinator.refresh(.launch)
  return world
}

private func checkCalls(_ world: World) async -> Int {
  await world.cli.calls.filter { $0 == Bosun.checkUpdates.args }.count
}

@MainActor @Suite struct CoordinatorUpdateOfferTests {
  @Test func aCheckThatFindsAnUpdateOffersItWithItsNotes() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    world.ui.answer("Remind Me Later")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.presented.map(\.title) == ["Update Available: v2026.11.11"])
    #expect(world.ui.lastSpec?.scroll == available.latest?.notes)
    // Remind Me Later: still on offer, with its badge, nothing run
    #expect(world.coordinator.update == .available(available))
    #expect(world.item(.checkForUpdates)?.title == "Update Available: v2026.11.11…")
    #expect(world.ui.lastModel?.iconBadge == .update)
    #expect(world.coordinator.flow.isIdle)
    #expect(await world.cli.started.isEmpty)
  }

  @Test func skipThisVersionRecordsItThroughBosun() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.skip(version: "v2026.11.11"), exited(0))
    world.ui.answer("Skip This Version")
    await world.coordinator.perform(.checkForUpdates)
    #expect(await world.commands.contains(["update", "--skip", "v2026.11.11"]))
    #expect(world.coordinator.update == .idle)
    #expect(world.ui.lastModel?.iconBadge == nil)
    #expect(world.item(.checkForUpdates)?.title == "Check for Updates…")
  }

  @Test func aSkipThatFailsSaysSo() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.skip(version: "v2026.11.11"), exited(1, "", err: "cannot write the state\n"))
    world.ui.answer("Skip This Version")
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "Cannot skip this version")
    #expect(world.ui.lastSpec?.message == "cannot write the state")
  }

  @Test func updateNowShowsThePlanFirstAndOnlyThenStarts() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, Fixtures.text("update-plan")))
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
    #expect(world.coordinator.update == .starting(available))
    #expect(world.item(.checkForUpdates)?.title == "Updating…")
    #expect(world.item(.checkForUpdates)?.enabled == false)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func theUpdateIsShownRunningOnceStatusSaysSo() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-update-running")))
    await world.coordinator.refresh(.timer)
    #expect(world.item(.checkForUpdates)?.title == "Updating… waiting for 2 windows")
    #expect(world.coordinator.update == .running(waitingFor: ["acme-exporter", "acme-web"]))
  }

  @Test func cancellingThePlanGoesBackToTheOfferAndStartsNothing() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Cancel")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.coordinator.update == .available(available))
    #expect(await world.cli.started.isEmpty)
  }

  @Test func aBlockedPlanSaysWhyAndStartsNothing() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, Fixtures.text("update-plan-blocked")))
    world.ui.answer("Update Now")
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "bosun cannot update now")
    #expect(world.ui.lastSpec?.message.contains("a migration of the knowledge store is missing") == true)
    #expect(world.coordinator.update == .available(available))
    #expect(await world.cli.started.isEmpty)
  }

  @Test func aPlanThatCouldNotBeMadeShowsWhy() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(
      Bosun.planUpdate, exited(1, "{\"plan\": null}", err: "bosun update: a quit is in progress\n"))
    world.ui.answer("Update Now")
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "Cannot update bosun")
    #expect(world.ui.lastSpec?.message == "bosun update: a quit is in progress")
    #expect(world.coordinator.update == .idle)
  }

  @Test func anUpdateThatCouldNotBeStartedShowsWhy() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, Fixtures.text("update-plan")))
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

  /// `starting` went on too long: look again, and if the same release is still offered, say so.
  @Test func anUpdateThatNeverShowsInStatusIsCalledAFailure() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    let before = await checkCalls(world)
    world.ui.answer("OK")
    await world.clock.waitForSleepers()
    world.clock.advance(by: .seconds(89))
    try? await Task.sleep(for: .milliseconds(30))
    #expect(await checkCalls(world) == before)
    world.clock.advance(by: .seconds(1))
    #expect(await eventually { world.ui.presented.count == 3 })
    #expect(await checkCalls(world) == before + 1)
    #expect(world.ui.lastSpec?.message == updateNeverStartedText)
  }

  @Test func theGraceMeansNothingOnceTheUpdateIsRunning() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-update-running")))
    await world.coordinator.refresh(.timer)
    let before = await checkCalls(world)
    await world.clock.waitForSleepers()
    world.clock.advance(by: .seconds(120))
    try? await Task.sleep(for: .milliseconds(30))
    #expect(await checkCalls(world) == before)
    #expect(world.ui.presented.count == 2)
  }

  @Test func anUpdateThatHasFinishedMakesANewCheck() async {
    let world = await running()
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-update-running")))
    await world.coordinator.refresh(.timer)
    #expect(world.coordinator.update == .running(waitingFor: ["acme-exporter", "acme-web"]))
    let before = await checkCalls(world)
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-ok")))
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-current")))
    await world.coordinator.refresh(.timer)
    #expect(await checkCalls(world) == before + 1)
    #expect(world.coordinator.update == .idle)
  }
}

@MainActor @Suite struct CoordinatorUpdateCheckTests {
  @Test func nothingNewSaysBosunIsUpToDate() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-current")))
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "bosun is up to date")
    #expect(world.coordinator.update == .idle)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func aCheckThatFailedShowsWhy() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-failed")))
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.title == "Cannot check for updates")
    #expect(world.ui.lastSpec?.message.contains("could not reach the origin") == true)
    #expect(world.coordinator.update == .idle)
  }

  @Test func aCheckThatHungShowsThat() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    #expect(world.ui.lastSpec?.message == "bosun did not answer in time")
  }

  @Test func theChecksGreyedItemShowsWhileItRuns() async {
    let world = await running()
    await world.replace(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-current")))
    world.ui.answer("OK")
    await world.coordinator.perform(.checkForUpdates)
    let titles = world.ui.rendered.compactMap { entry in
      entry.model.sections.flatMap { $0 }.first { $0.id == .checkForUpdates }?.title
    }
    #expect(titles.contains("Checking for Updates…"))
  }

  /// The launch look is the first check, quietly: a badge and no dialog.
  @Test func theCheckAtLaunchOnlyShowsABadge() async {
    let world = await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.coordinator.refresh(.launch)
    #expect(world.coordinator.update == .available(available))
    #expect(world.ui.lastModel?.iconBadge == .update)
    #expect(world.ui.presented.isEmpty)
  }

  @Test func aCheckThatFailsInTheBackgroundSaysNothing() async {
    let world = await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-failed")))
    await world.coordinator.refresh(.launch)
    #expect(world.coordinator.update == .idle)
    #expect(world.ui.presented.isEmpty)
  }

  @Test func aSkippedReleaseIsNotOfferedInTheBackground() async {
    let world = await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-skipped")))
    await world.coordinator.refresh(.launch)
    #expect(world.coordinator.update == .idle)
    #expect(world.ui.lastModel?.iconBadge == nil)
  }

  @Test func checksAreSixHoursAndAMinuteApart() async {
    let today = Today()
    let world = await running(today: today)
    // The launch look was the first check
    #expect(await checkCalls(world) == 1)
    await world.coordinator.refresh(.timer)
    await world.coordinator.menuWillOpen()
    #expect(await checkCalls(world) == 1)
    today.advance(by: 61)
    await world.coordinator.menuWillOpen()
    #expect(await checkCalls(world) == 2)
    today.advance(by: 6 * 3600)
    await world.coordinator.refresh(.timer)
    #expect(await checkCalls(world) == 3)
  }

  @Test func clickingAnOfferedUpdateOffersItAgainWithoutAskingBosun() async {
    let world = await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.coordinator.refresh(.launch)
    let before = await checkCalls(world)
    world.ui.answer("Remind Me Later")
    await world.coordinator.perform(.checkForUpdates)
    #expect(await checkCalls(world) == before)
    #expect(world.ui.presented.map(\.title) == ["Update Available: v2026.11.11"])
  }

  @Test func whileADialogIsOpenQuitAndTheRestAreIgnored() async {
    let world = await World(status: "status-ok")
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.coordinator.refresh(.launch)
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
  @Test func updateBosunRunsTheUpdateFlow() async {
    let world = await World(status: nil)
    await world.reply(Bosun.status, exited(0, Fixtures.text("status-ok", contract: 2)))
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.coordinator.refresh(.launch)
    #expect(world.item(.updateBosun) != nil)
    world.ui.answer("Remind Me Later")
    await world.coordinator.perform(.updateBosun)
    #expect(world.ui.presented.map(\.title) == ["Update Available: v2026.11.11"])
    #expect(world.coordinator.update == .available(available))
  }

  @Test func updateBosunWithNothingToOfferSaysSo() async {
    let world = await World(status: nil)
    await world.reply(Bosun.status, exited(0, Fixtures.text("status-ok", contract: 2)))
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-current")))
    await world.coordinator.refresh(.launch)
    world.ui.answer("OK")
    await world.coordinator.perform(.updateBosun)
    #expect(world.ui.lastSpec?.title == "bosun is up to date")
  }
}
