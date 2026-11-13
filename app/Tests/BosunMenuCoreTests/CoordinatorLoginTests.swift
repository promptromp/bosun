import BosunMenuTestSupport
import Foundation
import Testing

@testable import BosunMenuCore

/// Open at Login switched: one switch at a time, and a reading of it that began before the switch never puts the
/// old state back.
@MainActor @Suite struct CoordinatorLoginTests {
  /// A-6: one switch at a time: the item is greyed while `app login` runs, so a second click waits.
  @Test func openAtLoginIsGreyedWhileItIsSwitched() async throws {
    let gate = Gate()
    let world = try await World(login: false, gates: [Bosun.appLogin(on: true).args: gate])
    await world.refresh(.launch)
    await world.reply(Bosun.appLogin(on: true), exited(0))
    await world.replace(Bosun.appStatus, exited(0, try Fixtures.text("app-status")))
    let toggle = Task { await world.coordinator.perform(.openAtLogin) }
    #expect(await gate.waitForWaiter())
    #expect(world.item(.openAtLogin)?.enabled == false)
    await world.coordinator.perform(.openAtLogin)
    await gate.open()
    await toggle.value
    #expect(await world.commands.filter { $0.first == "app" && $0.dropFirst().first == "login" }.count == 1)
    #expect(world.item(.openAtLogin)?.checked == true)
    #expect(world.item(.openAtLogin)?.enabled == true)
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
    await coordinator.perform(.openAtLogin)
    #expect(coordinator.login == true)
    await hold.gate.open()
    await look.value
    #expect(coordinator.login == true)
    #expect(ui.lastModel?.sections.flatMap { $0 }.first { $0.id == .openAtLogin }?.checked == true)
  }
}
