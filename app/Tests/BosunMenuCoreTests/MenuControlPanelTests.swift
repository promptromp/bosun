import Testing

@testable import BosunMenuCore

private func loaded(_ name: String) throws -> StatusState {
  .loaded(try Fixtures.status(name), current: true)
}

private func model(_ status: StatusState) -> MenuModel {
  menu(for: status, update: .idle, flow: FlowState())
}

/// The control panel's menu (docs/design/control-panel.md): Start bosun now outside your hours, Settings… (⌘,),
/// Stop bosun… while the session runs, and Quit bosun.app, always last.
@Suite struct MenuControlPanelTests {
  /// Every state a menu is drawn for.
  private func everyMenu() throws -> [MenuModel] {
    let fixtures = [
      "status-ok", "status-warning", "status-error", "status-stopped", "status-resting",
      "status-running-outside-hours", "status-quit-unfinished", "status-windows-unreadable",
    ]
    let loadedMenus = try fixtures.map { model(try loaded($0)) }
    let others: [StatusState] = [
      .notInstalled, .unreachable("no answer"), .cannotRun("broken link"), .contractTooNew(2),
    ]
    return loadedMenus + others.map { model($0) } + [loadingMenu(flow: FlowState())]
  }

  @Test func testRestingOffersStartNow() throws {
    let menu = model(try loaded("status-resting"))
    #expect(menu.topIDs.first == .startBosun)
    #expect(menu.item(.startBosun).title == "Start bosun now")
    #expect(menu.item(.startBosun).enabled)
    #expect(!menu.has(.stopBosun))
  }

  @Test func testRunningOutsideHours() throws {
    let menu = model(try loaded("status-running-outside-hours"))
    #expect(menu.header.sentence == "bosun is running · outside your hours")
    #expect(menu.has(.stopBosun))
    #expect(menu.item(.stopBosun).title == "Stop bosun…")
    #expect(!menu.has(.startBosun))
  }

  @Test func startIsStartBosunInYourHours() throws {
    #expect(model(try loaded("status-stopped")).item(.startBosun).title == "Start bosun")
    #expect(model(try loaded("status-error")).item(.startBosun).title == "Start bosun")
  }

  @Test func testSettingsItemHasCommaKey() throws {
    let menu = model(try loaded("status-ok"))
    #expect(menu.item(.settings).title == "Settings…")
    #expect(menu.item(.settings).key == ",")
    #expect(menu.item(.settings).enabled)
  }

  @Test func testOpenConsoleFollowsConsoleRunning() throws {
    let text = try Fixtures.consoleRunning("status-ok")
    let serving = StatusState.loaded(try decodeContract(StatusSnapshot.self, from: text), current: true)
    #expect(model(serving).item(.openConsole).enabled)
    #expect(!model(try loaded("status-ok")).item(.openConsole).enabled)
  }

  @Test func testQuitAppAlwaysLast() throws {
    for menu in try everyMenu() {
      let last = try #require(menu.sections.last?.last)
      #expect(last.id == .quitApp, "\(menu.header.sentence)")
      #expect(last.title == "Quit bosun.app")
      #expect(last.key == "q")
    }
  }

  @Test func stopIsOnlyThereWhileTheSessionRuns() throws {
    #expect(model(try loaded("status-ok")).has(.stopBosun))
    #expect(model(try loaded("status-warning")).has(.stopBosun))
    // a quit that did not finish is finished with Stop
    #expect(model(try loaded("status-quit-unfinished")).has(.stopBosun))
    #expect(!model(try loaded("status-stopped")).has(.stopBosun))
    #expect(!model(try loaded("status-error")).has(.stopBosun))
    #expect(!model(.notInstalled).has(.stopBosun))
  }

  @Test func theLastSectionIsStopThenQuit() throws {
    #expect(model(try loaded("status-ok")).sections.last?.map(\.id) == [.stopBosun, .quitApp])
    #expect(model(try loaded("status-stopped")).sections.last?.map(\.id) == [.quitApp])
  }
}
