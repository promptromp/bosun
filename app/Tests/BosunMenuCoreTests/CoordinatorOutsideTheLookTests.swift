import Foundation
import Testing

@testable import BosunMenuCore

/// What goes on outside a look, so that a command that hangs never holds the next look, and a look that
/// lands during the app's own quit is never drawn.
@MainActor @Suite struct CoordinatorOutsideTheLookTests {
  @Test func theStatusIsDrawnBeforeALoginThatHangsIsAnswered() async {
    let hold = Gate()
    let world = await World(gates: [Bosun.appStatus.args: hold])
    let look = Task { await world.coordinator.refresh(.launch) }
    // "(not current)" and the rest of the status do not wait for `app status`
    #expect(await eventually { world.ui.lastModel?.header.sentence == "bosun is running" })
    await hold.open()
    await look.value
    #expect(world.item(.openAtLogin)?.checked == true)
  }

  @Test func aUpdateCheckThatHangsDoesNotHoldTheLooks() async {
    let hold = Gate()
    let world = await World(gates: [Bosun.checkUpdates.args: hold])
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-available")))
    await world.coordinator.refresh(.launch)  // returns with the check still out
    #expect(world.coordinator.update == .checking)
    #expect(world.item(.checkForUpdates)?.title == "Checking for Updates…")
    // Looks go on meanwhile
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-warning")))
    await world.coordinator.refresh(.timer)
    #expect(world.ui.lastModel?.header.dot == .yellow)
    await hold.open()
    await world.coordinator.settle()
    #expect(world.coordinator.update != .checking)
    #expect(world.ui.lastModel?.iconBadge == .warning)
  }

  @Test func theCheckAfterAFinishedUpdateIsOutsideTheLookToo() async {
    let hold = Gate()
    let world = await World(status: "status-update-running", gates: [Bosun.checkUpdates.args: hold])
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-current")))
    await world.refresh(.launch)
    #expect(world.coordinator.update == .running(waitingFor: ["acme-exporter", "acme-web"]))
    // The update has finished: its check is asked for, and does not hold the look that found it so
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-ok")))
    await world.coordinator.refresh(.timer)
    #expect(world.coordinator.update == .checking)
    await hold.open()
    await world.coordinator.settle()
    #expect(world.coordinator.update == .idle)
  }

  @Test func checksRunOneAtATime() async {
    let hold = Gate()
    let world = await World(gates: [Bosun.checkUpdates.args: hold])
    await world.reply(Bosun.checkUpdates, exited(0, Fixtures.text("update-check-current")))
    await world.coordinator.refresh(.launch)
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-update-running")))
    await world.coordinator.refresh(.timer)
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-ok")))
    await world.coordinator.refresh(.timer)
    await hold.open()
    await world.coordinator.settle()
    let checks = await world.cli.calls.filter { $0 == Bosun.checkUpdates.args }.count
    #expect(checks == 2)  // the check at launch, and the one after the update that ran in between
    #expect(world.coordinator.update == .idle)
  }

  /// A Start that fails at launch tells it in a dialog of its own: the look that found bosun stopped is over.
  @Test func aStartThatFailsAtLaunchDoesNotHoldTheLook() async {
    let world = await World(status: "status-stopped")
    await world.replace(
      Bosun.start, CommandResult(outcome: .couldNotStart("no permission"), stdout: "", stderr: ""))
    let hold = Gate()
    world.ui.whileOpen = { _ in await hold.wait() }
    world.ui.answer("OK")
    await world.coordinator.refresh(.launch)  // returns although the dialog is still open
    #expect(await eventually { world.ui.presented.count == 1 })
    #expect(world.ui.lastSpec?.title == "bosun did not start")
    // and the poller is free: the next look goes through
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-ok")))
    await world.coordinator.refresh(.timer)
    #expect(world.ui.lastModel?.header.sentence == "bosun is running")
    await hold.open()
    await world.coordinator.settle()
  }

  /// `quit.json` is written before the windows go: a look that lands in between would say "bosun quit did
  /// not finish". Pausing the poller does not stop a look already out, so its result is dropped.
  @Test func aLookThatLandsDuringTheAppsOwnQuitIsNotDrawn() async {
    let hold = Gate()
    let world = await World(atLogin: true, gates: [Bosun.status.args: hold])
    await hold.open()
    await world.reply(Bosun.quitPlan, exited(0, Fixtures.text("quit-plan")))
    await world.refresh(.launch)
    #expect(world.ui.lastModel?.header.sentence == "bosun is running")
    // A look goes out, and waits at the gate with a status that says the quit did not finish
    await world.replace(Bosun.status, exited(0, Fixtures.text("status-quit-unfinished")))
    await hold.arm()
    let look = Task { await world.coordinator.refresh(.timer) }
    #expect(await hold.waitForWaiter())  // the look is out, and waits at the gate
    let seen = Seen()
    world.ui.whileOpen = { _ in
      // The quit is under way (its dialog is open); the look lands now
      await hold.open()
      await look.value
      seen.sentence = world.coordinator.menuModel.header.sentence
    }
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quit)
    #expect(seen.sentence == "bosun is running")
  }
}

@MainActor
final class Seen {
  var sentence = ""
}
