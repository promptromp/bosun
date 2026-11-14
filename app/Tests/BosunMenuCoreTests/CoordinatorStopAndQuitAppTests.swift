import Foundation
import Testing

@testable import BosunMenuCore

/// Stop bosun… and Quit bosun.app apart (docs/design/control-panel.md, "Stop and Quit").
@MainActor @Suite struct CoordinatorStopAndQuitAppTests {
  @Test func testStopKeepsTheApp() async throws {
    let world = try await readyToQuit()
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    let before = await statusLooks(world)
    world.ui.answer("Stop bosun")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.presented.map(\.title) == ["Stop bosun?"])
    #expect(await world.commands.contains(letFinishCommand))
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
    // the poller is let go and looks once, for the grey stopped status
    #expect(await statusLooks(world) == before + 1)
  }

  @Test func testQuitAppLeaveRunning() async throws {
    let world = try await readyToQuit()
    world.ui.answer("Leave bosun Running")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.map(\.title) == ["Quit bosun.app?"])
    #expect(world.ui.terminated == 1)
    #expect(await world.commands.filter { $0.first == "quit" }.isEmpty)
  }

  @Test func testQuitAppStopAndQuit() async throws {
    let world = try await readyToQuit()
    await world.reply(
      Command(args: letFinishCommand, timeout: .seconds(1)), try quitReply("quit-result-stopped"))
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Stop bosun")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.map(\.title) == ["Quit bosun.app?", "Stop bosun?"])
    #expect(await world.commands.contains(Bosun.quitPlan.args))
    #expect(await world.commands.contains(letFinishCommand))
    #expect(world.ui.terminated == 1)
  }

  @Test func quitAppCancelledLeavesEverythingAsItWas() async throws {
    let world = try await readyToQuit()
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
    #expect(await world.commands.filter { $0.first == "quit" }.isEmpty)
  }

  @Test func stopBosunAndQuitThatIsCancelledKeepsTheApp() async throws {
    let world = try await readyToQuit()
    world.ui.answer("Stop bosun and Quit…")
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.terminated == 0)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func testQuitWithUnreadableStatusExitsAtOnce() async throws {
    let unreachable = try await World(status: nil)
    await unreachable.reply(Bosun.status, CommandResult(outcome: .exited(1), stdout: "", stderr: "boom"))
    let missing = try await World(status: nil)
    await missing.reply(Bosun.status, CommandResult(outcome: .notInstalled, stdout: "", stderr: ""))
    let stale = try await World(status: "status-ok")
    await stale.refresh(.launch)
    await stale.replace(Bosun.status, CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    for world in [unreachable, missing, stale] {
      await world.refresh(.timer)
      await world.coordinator.perform(.quitApp)
      #expect(world.ui.terminated == 1, "\(String(describing: world.coordinator.status))")
      #expect(world.ui.presented.isEmpty)
      #expect(await world.commands.filter { $0.first == "quit" }.isEmpty)
    }
    guard case .unreachable? = unreachable.coordinator.status else {
      Issue.record("not unreachable: \(String(describing: unreachable.coordinator.status))")
      return
    }
    guard case .loaded(_, current: false)? = stale.coordinator.status else {
      Issue.record("not a status that is not current: \(String(describing: stale.coordinator.status))")
      return
    }
  }

  @Test func whileStoppedQuitAppJustLeaves() async throws {
    let world = try await readyToQuit(status: "status-stopped")
    #expect(world.item(.stopBosun) == nil)
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.terminated == 1)
    #expect(world.ui.presented.isEmpty)
  }

  @Test func aStopWhoseLookFailedOffersStopNowButNotQuittingTheApp() async throws {
    let world = try await World(status: "status-ok", atLogin: true)
    await world.reply(Bosun.quitPlan, try Fixtures.reply("quit-plan-failed"))
    await world.refresh(.launch)
    world.ui.answer("Cancel")
    await world.coordinator.perform(.stopBosun)
    #expect(world.ui.presented.first?.buttons == ["Cancel", "Stop Now"])
    #expect(world.ui.terminated == 0)
  }

  @Test func aSecondQuitAppWhileItsDialogIsOpenIsIgnored() async throws {
    let world = try await readyToQuit()
    world.ui.whileOpen = { _ in
      await world.coordinator.perform(.quitApp)
      await world.coordinator.perform(.stopBosun)
    }
    world.ui.answer("Cancel")
    await world.coordinator.perform(.quitApp)
    #expect(world.ui.presented.count == 1)
  }

  /// Settings… opens the Settings window (CoordinatorSettingsTests), never a dialog, and reads only bosun's settings.
  @Test func settingsOpensTheWindowAndNoDialog() async throws {
    let world = try await readyToQuit()
    let before = await world.commands
    try await world.reply(Bosun.settingsGet, fixture: "settings-get")
    await world.coordinator.perform(.settings)
    #expect(world.ui.presented.isEmpty)
    #expect(world.ui.settingsShown.count == 1)
    #expect(await world.commands == before + [Bosun.settingsGet.args])
  }
}
