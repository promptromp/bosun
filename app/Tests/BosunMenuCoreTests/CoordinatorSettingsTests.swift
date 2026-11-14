import BosunMenuTestSupport
import Foundation
import Testing

@testable import BosunMenuCore

/// A change bosun made, with nothing to note.
private let changedReply =
  #"{"contract": 1, "changed": {"BOSUN_UPDATE_CHANNEL": ["main", "releases"]}, "notes": []}"#

/// The settings fixture with the update channel read as `channel`.
private func settingsWith(channel: String) throws -> String {
  let text = try Fixtures.text("settings-get")
  let changed = text.replacingOccurrences(
    of: "\"set\": true,\n      \"valid\": true,\n      \"value\": \"main\"",
    with: "\"set\": true,\n      \"valid\": true,\n      \"value\": \"\(channel)\"")
  guard changed != text else { throw Fixtures.Missing(name: "settings-get (with its channel main)") }
  return changed
}

/// What bosun was asked of its settings (the look at launch also checks for updates).
@MainActor
private func settingsCommands(_ world: World) async -> [[String]] {
  await world.commands.filter { $0.first == "settings" }
}

/// Settings… : the window shown over `settings get` and `app status`, and what each edit runs.
@MainActor @Suite struct CoordinatorSettingsTests {
  /// A world whose status is loaded and whose Settings window is open over the settings fixture.
  private func opened(login: Bool = true) async throws -> World {
    let world = try await World(login: login)
    await world.refresh(.launch)
    try await world.reply(Bosun.settingsGet, fixture: "settings-get")
    await world.coordinator.perform(.settings)
    return world
  }

  private func apply(_ world: World, _ edit: SettingsEdit) async throws -> SettingsState {
    let apply = try #require(world.ui.settingsApply)
    return await apply(edit)
  }

  @Test func settingsShowsWhatBosunReadsAndTheLogin() async throws {
    let world = try await opened(login: false)
    let shown = try #require(world.ui.settingsShown.last)
    #expect(world.ui.settingsShown.count == 1)
    #expect(shown.view == (try Fixtures.decode(SettingsView.self, "settings-get")))
    #expect(!shown.openAtLogin)
    #expect(shown.message == nil)
    #expect(!shown.busy)
    #expect(world.coordinator.settings == shown)
    #expect(await settingsCommands(world) == [Bosun.settingsGet.args])
  }

  @Test func testSecondOpenRefreshes() async throws {
    let world = try await opened()
    await world.replace(Bosun.settingsGet, exited(0, try settingsWith(channel: "releases")))
    await world.coordinator.perform(.settings)
    #expect(world.ui.settingsShown.count == 2)
    let fresh = try #require(world.ui.settingsShown.last?.view)
    #expect(fresh.settings[SettingKey.updateChannel]?.value == "releases")
  }

  @Test func settingsThatCannotBeReadSayWhyAndShowNoValues() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.reply(Bosun.settingsGet, exited(1, "", err: "bosun.env could not be loaded\n"))
    await world.coordinator.perform(.settings)
    let shown = try #require(world.ui.settingsShown.last)
    #expect(shown.view == nil)
    #expect(shown.message == "bosun.env could not be loaded")
  }

  @Test func noSettingsWindowBeforeThereIsAStatus() async throws {
    let world = try await World(status: nil)
    await world.coordinator.perform(.settings)
    #expect(world.ui.settingsShown.isEmpty)
  }

  @Test func testApplyRunsSetThenRereads() async throws {
    let world = try await opened()
    let values = [SettingKey.updateChannel: "main"]
    await world.reply(Bosun.settingsSet(values), exited(0, changedReply))
    let state = try await apply(world, .values(values))
    #expect(
      Array(await settingsCommands(world).suffix(2)) == [
        ["settings", "set", "BOSUN_UPDATE_CHANNEL=main", "--json"], ["settings", "get", "--json"],
      ])
    #expect(state.message == nil)
    #expect(!state.busy)
    #expect(world.coordinator.settings == state)
  }

  @Test func testApplyFailureKeepsReadValues() async throws {
    let world = try await opened()
    let values = [SettingKey.awakeUntil: "25:00"]
    await world.reply(Bosun.settingsSet(values), exited(2, try Fixtures.text("settings-set-refused")))
    let state = try await apply(world, .values(values))
    #expect(state.message == "BOSUN_START_TIME: '25:00' must be HH:MM, 24-hour")
    #expect(state.view?.settings[SettingKey.awakeUntil]?.value == "20:00")
    #expect(await settingsCommands(world).last == Bosun.settingsGet.args)
  }

  @Test func aNoteOfTheChangeIsTheMessage() async throws {
    let world = try await opened()
    let values = [SettingKey.awakeUntil: "18:00"]
    await world.reply(Bosun.settingsSet(values), exited(0, try Fixtures.text("settings-set")))
    let state = try await apply(world, .values(values))
    #expect(state.message?.hasPrefix("another app's repeating wake was left as it is") == true)
  }

  @Test func aRereadThatFailsShowsNoValuesNotTheEdit() async throws {
    let world = try await opened()
    let values = [SettingKey.updateChannel: "releases"]
    await world.reply(Bosun.settingsSet(values), exited(0, changedReply))
    await world.replace(Bosun.settingsGet, exited(1, "", err: "Permission denied\n"))
    let state = try await apply(world, .values(values))
    #expect(state.view == nil)
    #expect(state.message == "Permission denied")
  }

  @Test func testWakeMoveOpensTerminal() async throws {
    let world = try await opened()
    let state = try await apply(world, .values([SettingKey.startTime: "09:00"]))
    #expect(world.scripts.written == [["settings", "set", "BOSUN_START_TIME=09:00"]])
    #expect(world.ui.opened.last == URL(fileURLWithPath: "/tmp/bosun-app/bosun-settings.command"))
    #expect(!(await world.commands.contains { $0.starts(with: ["settings", "set"]) }))
    #expect(
      state.message == "Finish the change in Terminal: it asks for your password to move the Mac's wake")
    #expect(state.view?.settings[SettingKey.startTime]?.value == "08:30")
  }

  @Test func aTerminalThatCannotBeOpenedIsToldAndPromisesNothing() async throws {
    let world = try await opened()
    world.scripts.fails = true
    world.ui.answer("OK")
    let state = try await apply(world, .values([SettingKey.days: "MTWRFS"]))
    #expect(world.ui.lastSpec?.title == "Cannot open Terminal")
    #expect(state.message == nil)
  }

  @Test func nothingToChangeRunsNothing() async throws {
    let world = try await opened()
    let state = try await apply(world, .values([:]))
    #expect(await settingsCommands(world) == [Bosun.settingsGet.args])
    #expect(state == world.ui.settingsShown.last)
  }

  @Test func aSecondSaveWhileOneRunsIsIgnored() async throws {
    let gate = Gate()
    let values = [SettingKey.updateChannel: "releases"]
    let world = try await World(gates: [Bosun.settingsSet(values).args: gate])
    await world.refresh(.launch)
    try await world.reply(Bosun.settingsGet, fixture: "settings-get")
    await world.coordinator.perform(.settings)
    await world.reply(Bosun.settingsSet(values), exited(0, changedReply))
    let first = Task { try await apply(world, .values(values)) }
    #expect(await gate.waitForWaiter())
    let second = try await apply(world, .values(values))
    #expect(second.busy)
    await gate.open()
    #expect(!(try await first.value).busy)
    #expect(await world.commands.filter { $0.starts(with: ["settings", "set"]) }.count == 1)
  }

  @Test func aSaveBeforeTheWindowWasOpenedReadsFirst() async throws {
    let world = try await World()
    await world.refresh(.launch)
    try await world.reply(Bosun.settingsGet, fixture: "settings-get")
    let values = [SettingKey.startTime: "09:00"]
    _ = await world.coordinator.applySettings(.values(values))
    // the read says the wake is bosun's, so the move goes to Terminal
    #expect(world.scripts.written == [["settings", "set", "BOSUN_START_TIME=09:00"]])
    #expect(await settingsCommands(world) == [Bosun.settingsGet.args])
  }

  @Test func openAtLoginSwitchesThroughAppLogin() async throws {
    let world = try await opened(login: true)
    await world.reply(Bosun.appLogin(on: false), exited(0))
    let off = try Fixtures.text("app-status").replacingOccurrences(
      of: "\"login\": true", with: "\"login\": false")
    await world.replace(Bosun.appStatus, exited(0, off))
    let state = try await apply(world, .openAtLogin(false))
    #expect(await world.commands.contains(["app", "login", "off"]))
    #expect(!state.openAtLogin)
    #expect(world.coordinator.flow.isIdle)
  }

  @Test func openAtLoginAsItIsRunsNothing() async throws {
    let world = try await opened(login: true)
    let state = try await apply(world, .openAtLogin(true))
    #expect(!(await world.commands.contains { $0.starts(with: ["app", "login"]) }))
    #expect(state.openAtLogin)
  }

  @Test func changeWindowsOpensTheWizardInTerminal() async throws {
    let world = try await opened()
    _ = try await apply(world, .changeWindows)
    #expect(world.scripts.written == [["init"]])
  }

  @Test func theConfigFolderAndTheLogsAreOpened() async throws {
    let world = try await opened()
    _ = try await apply(world, .openConfigFolder)
    #expect(world.ui.opened.last == URL(fileURLWithPath: "/Users/me/.config/bosun"))
    _ = try await apply(world, .showLogs)
    #expect(world.ui.opened.last == URL(fileURLWithPath: "/Users/me/.local/state/bosun/logs"))
  }

  @Test func noConfigFolderIsOpenedWhenItIsNotKnown() async throws {
    let world = try await World()
    await world.refresh(.launch)
    await world.reply(Bosun.settingsGet, exited(1, "", err: "no\n"))
    await world.coordinator.perform(.settings)
    _ = try await apply(world, .openConfigFolder)
    #expect(world.ui.opened.isEmpty)
  }
}
