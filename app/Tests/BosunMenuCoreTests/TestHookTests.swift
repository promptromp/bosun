import Foundation
import Testing

@testable import BosunMenuCore

private func loaded(_ name: String) throws -> StatusState {
  .loaded(try Fixtures.status(name), current: true)
}

private func jsonObject(_ data: Data) throws -> [String: Any] {
  try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
}

@Suite struct ItemNameTests {
  private let all: [ItemID] = [
    .startBosun, .showProblems, .openConsole, .checkForUpdates, .settings, .updateBosun, .helpMenu,
    .help(.guide), .help(.doctor), .help(.logs), .help(.uninstall), .stopBosun, .quitApp,
  ]

  @Test func eachItemNameReadsBackAsTheItem() throws {
    for id in all {
      #expect(ItemID(name: id.name) == id, "\(id.name)")
    }
  }

  @Test func theNamesAreTheDocumentedOnes() throws {
    #expect(
      all.map(\.name) == [
        "startBosun", "showProblems", "openConsole", "checkForUpdates", "settings", "updateBosun",
        "helpMenu",
        "help.guide", "help.doctor", "help.logs", "help.uninstall", "stopBosun", "quitApp",
      ])
  }

  @Test func theNamesAreDistinct() throws {
    #expect(Set(all.map(\.name)).count == all.count)
  }

  @Test func aNameThatIsNotAnItemIsNil() throws {
    #expect(ItemID(name: "reboot") == nil)
    // the items 0.1.3 took out of the menu
    #expect(ItemID(name: "quit") == nil)
    #expect(ItemID(name: "openAtLogin") == nil)
    #expect(ItemID(name: "help.nothing") == nil)
    #expect(ItemID(name: "") == nil)
  }
}

@Suite struct MenuJSONTests {
  @Test func theMenuAndItsStatusLevelAreWritten() throws {
    let state = try loaded("status-ok")
    let model = menu(for: state, update: .idle, flow: FlowState())
    let object = try jsonObject(menuJSON(model, status: state))
    #expect(object["level"] as? String == "ok")
    let header = try #require(object["header"] as? [String: Any])
    #expect(header["dot"] as? String == "green")
    #expect(header["sentence"] as? String == "bosun is running")
    #expect((header["detail"] as? [String])?.count == 3)
    #expect(object["icon_badge"] is NSNull)
    let sections = try #require(object["sections"] as? [[[String: Any]]])
    let ids = sections.flatMap { $0 }.compactMap { $0["id"] as? String }
    #expect(ids == ["openConsole", "checkForUpdates", "settings", "helpMenu", "stopBosun", "quitApp"])
  }

  @Test func anItemCarriesItsStateAndItsChildren() throws {
    let state = try loaded("status-stopped")
    let model = menu(for: state, update: .idle, flow: FlowState())
    let object = try jsonObject(menuJSON(model, status: state))
    #expect(object["level"] as? String == "stopped")
    let items = try #require(object["sections"] as? [[[String: Any]]]).flatMap { $0 }
    let console = try #require(items.first { $0["id"] as? String == "openConsole" })
    #expect(console["enabled"] as? Bool == false)
    #expect(console["key"] as? String == "o")
    let settings = try #require(items.first { $0["id"] as? String == "settings" })
    #expect(settings["key"] as? String == ",")
    #expect(settings["checked"] as? Bool == false)
    let help = try #require(items.first { $0["id"] as? String == "helpMenu" })
    let children = try #require(help["children"] as? [[String: Any]])
    #expect(
      children.compactMap { $0["id"] as? String } == [
        "help.guide", "help.doctor", "help.logs", "help.uninstall",
      ])
  }

  @Test func theBadgeIsNamed() throws {
    let state = try loaded("status-warning")
    let model = menu(for: state, update: .idle, flow: FlowState())
    let object = try jsonObject(menuJSON(model, status: state))
    #expect(object["icon_badge"] as? String == "warning")
    #expect(object["level"] as? String == "warning")
  }

  @Test func theLevelNamesWhatIsKnownWhenThereIsNoStatus() throws {
    #expect(statusLevelName(.notInstalled) == "not_installed")
    #expect(statusLevelName(.unreachable("x")) == "unreachable")
    #expect(statusLevelName(.cannotRun("x")) == "cannot_run")
    #expect(statusLevelName(.contractTooNew(2)) == "contract_too_new")
    #expect(statusLevelName(nil) == "loading")
    #expect(try statusLevelName(loaded("status-error")) == "error")
  }

  @Test func theLoadingMenuIsWritten() throws {
    let object = try jsonObject(menuJSON(loadingMenu(flow: FlowState()), status: nil))
    #expect(object["level"] as? String == "loading")
  }
}

@Suite struct HookActionTests {
  private func action(_ text: String) throws -> HookAction {
    try HookAction.decode(Data(text.utf8))
  }

  @Test func anItemIsReadByItsName() throws {
    #expect(try action(#"{"item": "quitApp"}"#) == .item(.quitApp))
    #expect(try action(#"{"item": "stopBosun"}"#) == .item(.stopBosun))
    #expect(try action(#"{"item": "help.doctor"}"#) == .item(.help(.doctor)))
  }

  @Test func anAnswerNamesTheButtonAndTheBoxes() throws {
    let read = try action(#"{"answer": {"button": "Stop bosun", "boxes": {"sleep": false}}}"#)
    #expect(read == .answer(HookAnswer(button: "Stop bosun", boxes: ["sleep": false])))
  }

  @Test func anAnswerNeedsNoBoxes() throws {
    #expect(try action(#"{"answer": {"button": "OK"}}"#) == .answer(HookAnswer(button: "OK", boxes: [:])))
  }

  @Test func whatIsNotAnActionIsRefusedWithAReason() throws {
    for text in [#"{"item": "reboot"}"#, "{}", "not json", #"{"item": 3}"#, #"{"answer": {}}"#] {
      #expect(throws: HookError.self) { try action(text) }
    }
  }

  @Test func theReasonSaysWhatWasWrong() throws {
    #expect(throws: HookError.unknownItem("reboot")) { try action(#"{"item": "reboot"}"#) }
  }
}

@Suite struct HookAnswerTests {
  private let spec = DialogSpec(
    title: "t", message: "m", scroll: nil,
    boxes: [
      DialogBox(id: "pauseMorning", title: "Pause", on: true),
      DialogBox(id: "sleep", title: "Sleep", on: false),
    ],
    buttons: ["Stop bosun", "Cancel"], cancel: 1)

  @Test func theButtonIsFoundByItsTitle() throws {
    let answer = HookAnswer(button: "Cancel", boxes: [:]).resolve(for: spec)
    #expect(answer?.button == 1)
  }

  @Test func theBoxesStartAtTheDialogsDefaultsAndTakeTheAnswers() throws {
    let answer = HookAnswer(button: "Stop bosun", boxes: ["sleep": true]).resolve(for: spec)
    #expect(answer == DialogAnswer(button: 0, boxes: ["pauseMorning": true, "sleep": true]))
  }

  @Test func aButtonThatIsNotThereIsNoAnswer() throws {
    #expect(HookAnswer(button: "Shred", boxes: [:]).resolve(for: spec) == nil)
  }

  @Test func aBoxThatIsNotThereIsNoAnswer() throws {
    #expect(HookAnswer(button: "Cancel", boxes: ["nothing": true]).resolve(for: spec) == nil)
  }
}

@Suite struct HookSettingsTests {
  private func action(_ text: String) throws -> HookAction {
    try HookAction.decode(Data(text.utf8))
  }

  @Test func testHookSettingsAction() throws {
    #expect(
      try action(#"{"settings": {"BOSUN_START_TIME": "09:00"}}"#)
        == .settings(.values(["BOSUN_START_TIME": "09:00"])))
    #expect(
      try action(#"{"settings": {"BOSUN_DAYS": "MTWRFS", "BOSUN_AWAKE_UNTIL": "18:00"}}"#)
        == .settings(.values(["BOSUN_DAYS": "MTWRFS", "BOSUN_AWAKE_UNTIL": "18:00"])))
    #expect(try action(#"{"settings": {"openAtLogin": false}}"#) == .settings(.openAtLogin(false)))
    #expect(try action(#"{"settings": {"openAtLogin": true}}"#) == .settings(.openAtLogin(true)))
  }

  @Test func settingsThatAreNotAnEditAreRefusedWithAReason() throws {
    let refused = [
      #"{"settings": {}}"#,
      #"{"settings": {"openAtLogin": "no"}}"#,
      #"{"settings": {"openAtLogin": true, "BOSUN_DAYS": "M"}}"#,
      #"{"settings": {"BOSUN_DAYS": true}}"#,
      #"{"settings": {"BOSUN_DAYS": 3}}"#,
      #"{"settings": "BOSUN_DAYS=M"}"#,
    ]
    for text in refused {
      #expect(throws: HookError.self, "\(text)") { try action(text) }
    }
    #expect(throws: HookError.badSettings) { try action(#"{"settings": {}}"#) }
  }
}

@Suite struct MenuJSONSettingsTests {
  private func settingsObject(_ settings: SettingsState?) throws -> Any? {
    let state = try loaded("status-ok")
    let model = menu(for: state, update: .idle, flow: FlowState())
    return try jsonObject(menuJSON(model, status: state, settings: settings))["settings"]
  }

  @Test func noSettingsStateBeforeTheWindowWasOpened() throws {
    #expect(try settingsObject(nil) is NSNull)
  }

  @Test func theSettingsStateIsWrittenWithTheValuesBosunRead() throws {
    let view = try Fixtures.decode(SettingsView.self, "settings-get")
    let object = try #require(
      try settingsObject(SettingsState(view: view, openAtLogin: true, message: "a note", busy: false))
        as? [String: Any])
    #expect(object["open_at_login"] as? Bool == true)
    #expect(object["message"] as? String == "a note")
    #expect(object["busy"] as? Bool == false)
    #expect(object["readable"] as? Bool == true)
    #expect(object["wake"] as? String == "ours")
    let values = try #require(object["values"] as? [String: Any])
    #expect(values["BOSUN_START_TIME"] as? String == "08:30")
    #expect(values.count == 5)
  }

  @Test func whatCouldNotBeReadIsNull() throws {
    let unreadable = try Fixtures.decode(SettingsView.self, "settings-get-unreadable")
    let object = try #require(
      try settingsObject(SettingsState(view: unreadable, openAtLogin: false, message: nil, busy: true))
        as? [String: Any])
    #expect(object["message"] is NSNull)
    #expect(object["readable"] as? Bool == false)
    #expect((object["values"] as? [String: Any])?["BOSUN_DAYS"] is NSNull)
    let none = try #require(
      try settingsObject(SettingsState(view: nil, openAtLogin: false, message: "no", busy: false))
        as? [String: Any])
    #expect(none["values"] is NSNull)
    #expect(none["readable"] is NSNull)
    #expect(none["wake"] is NSNull)
    #expect(none["open_at_login"] as? Bool == false)
    let unknown = try #require(
      try settingsObject(SettingsState(view: nil, openAtLogin: nil, message: nil, busy: false))
        as? [String: Any])
    #expect(unknown["open_at_login"] is NSNull)
  }
}
