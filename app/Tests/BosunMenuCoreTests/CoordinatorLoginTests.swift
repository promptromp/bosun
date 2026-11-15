import BosunMenuTestSupport
import Foundation
import Testing

@testable import BosunMenuCore

/// Open at Login switched: one switch at a time, and a reading of it that began before the switch never puts the
/// old state back.
@MainActor @Suite struct CoordinatorLoginTests {
  /// A-6: one switch at a time: while `app login` runs a second switch is ignored.
  @Test func aSecondSwitchWhileOneRunsIsIgnored() async throws {
    let gate = Gate()
    let world = try await World(login: false, gates: [Bosun.appLogin(on: true).args: gate])
    await world.refresh(.launch)
    await world.reply(Bosun.appLogin(on: true), exited(0))
    await world.replace(Bosun.appStatus, exited(0, try Fixtures.text("app-status")))
    let toggle = Task { await world.coordinator.switchLogin(to: true) }
    #expect(await gate.waitForWaiter())
    #expect(world.coordinator.flow.active == .login)
    await world.coordinator.switchLogin(to: true)
    await gate.open()
    await toggle.value
    #expect(await world.commands.filter { $0.first == "app" && $0.dropFirst().first == "login" }.count == 1)
    #expect(world.coordinator.login == true)
    #expect(world.coordinator.flow.isIdle)
  }

  /// A-6: a look's reading of Open at Login that began before a switch (it read the plist as it was) and lands
  /// after the switch is done is dropped: it would put the old state back.
  @Test func aLoginReadingBegunBeforeTheSwitchIsDropped() async throws {
    let fake = FakeBosunCLI()
    let hold = HeldReading(inner: fake, args: Bosun.appStatus.args)
    let ui = FakeUI()
    let off = try Fixtures.text("app-status").replacingOccurrences(
      of: "\"login\": true", with: "\"login\": false")
    await fake.reply(Bosun.status.args, exited(0, try Fixtures.text("status-ok")))
    await fake.reply(Bosun.appStatus.args, exited(0, off))  // the launch's reading
    await fake.reply(Bosun.appStatus.args, exited(0, off))  // the menu open's, before the switch
    await fake.reply(Bosun.appStatus.args, exited(0, try Fixtures.text("app-status")))  // the switch's own
    await fake.reply(Bosun.appLogin(on: true).args, exited(0))
    let coordinator = AppCoordinator(
      cli: hold, ui: ui, options: LaunchOptions(atLogin: true, testHooks: nil), clock: ManualClock(),
      scripts: FakeScripts())
    await coordinator.refresh(.launch)
    await coordinator.settle()
    #expect(coordinator.login == false)
    await hold.arm()
    let look = Task { await coordinator.menuWillOpen() }
    #expect(await hold.gate.waitForWaiter())
    await coordinator.switchLogin(to: true)
    #expect(coordinator.login == true)
    await hold.gate.open()
    await look.value
    #expect(coordinator.login == true)
  }

  @Test func openAtLoginTurnsItOnAndShowsItChecked() async throws {
    let world = try await World(login: false)
    await world.refresh(.launch)
    #expect(world.coordinator.login == false)
    await world.reply(Bosun.appLogin(on: true), exited(0))
    await world.replace(Bosun.appStatus, exited(0, try Fixtures.text("app-status")))
    await world.coordinator.switchLogin(to: true)
    #expect(await world.commands.contains(["app", "login", "on"]))
    #expect(world.coordinator.login == true)
  }

  @Test func openAtLoginTurnsItOffWhenItIsOn() async throws {
    let world = try await World(login: true)
    await world.refresh(.launch)
    await world.reply(Bosun.appLogin(on: false), exited(0))
    let off = try Fixtures.text("app-status").replacingOccurrences(
      of: "\"login\": true", with: "\"login\": false")
    await world.replace(Bosun.appStatus, exited(0, off))
    await world.coordinator.switchLogin(to: false)
    #expect(await world.commands.contains(["app", "login", "off"]))
    #expect(world.coordinator.login == false)
  }

  @Test func openAtLoginThatFailsShowsWhyAndChangesNothing() async throws {
    let world = try await World(login: false)
    await world.refresh(.launch)
    await world.reply(Bosun.appLogin(on: true), exited(1, "", err: "bosun.app is not installed\n"))
    world.ui.answer("OK")
    await world.coordinator.switchLogin(to: true)
    #expect(world.ui.lastSpec?.title == "Cannot change Open at Login")
    #expect(world.ui.lastSpec?.message == "bosun.app is not installed")
    #expect(world.coordinator.login == false)
  }

  /// `bosun app status` that cannot be read leaves Open at Login unknown (nil), never off.
  @Test func aLoginThatCannotBeReadIsUnknown() async throws {
    let world = try await World(login: nil)
    await world.refresh(.launch)
    #expect(world.coordinator.login == nil)
  }

  /// From unknown, a switch runs the one chosen (`app login on|off` makes it so, whatever it was).
  @Test func fromUnknownASwitchRunsTheOneChosen() async throws {
    for on in [true, false] {
      let world = try await World(login: nil)
      await world.refresh(.launch)
      await world.reply(Bosun.appLogin(on: on), exited(0))
      await world.coordinator.switchLogin(to: on)
      #expect(await world.commands.contains(Bosun.appLogin(on: on).args))
      // the read after it still fails: what the switch made stands
      #expect(world.coordinator.login == on)
    }
  }

  /// A switch from unknown that fails leaves it unknown.
  @Test func fromUnknownASwitchThatFailsLeavesItUnknown() async throws {
    let world = try await World(login: nil)
    await world.refresh(.launch)
    await world.reply(Bosun.appLogin(on: true), exited(1, "", err: "bosun.app is not installed\n"))
    world.ui.answer("OK")
    await world.coordinator.switchLogin(to: true)
    #expect(world.ui.lastSpec?.title == "Cannot change Open at Login")
    #expect(world.coordinator.login == nil)
  }

  /// `bosun app status` could not be read: the window shows Open at Login as unknown, with its line, and a choice
  /// (Turn On or Turn Off) runs that one.
  @Test func openAtLoginUnknownIsShownSoAndAChoiceRunsIt() async throws {
    let world = try await World(login: nil)
    await world.refresh(.launch)
    try await world.reply(Bosun.settingsGet, fixture: "settings-get")
    await world.coordinator.perform(.settings)
    let shown = try #require(world.ui.settingsShown.last)
    #expect(shown.openAtLogin == nil)
    #expect(
      shown.openAtLoginRow
        == SettingRow(editable: true, text: "unknown", note: SettingsState.openAtLoginUnknown))
    await world.reply(Bosun.appLogin(on: false), exited(0))
    let apply = try #require(world.ui.settingsApply)
    let state = await apply(.openAtLogin(false))
    #expect(await world.commands.contains(["app", "login", "off"]))
    #expect(state.openAtLogin == false)
    #expect(state.openAtLoginRow == SettingRow(editable: true, text: nil, note: nil))
  }
}
