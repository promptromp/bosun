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
    .startBosun, .showProblems, .openConsole, .checkForUpdates, .openAtLogin, .updateBosun, .helpMenu,
    .help(.guide), .help(.doctor), .help(.logs), .help(.uninstall), .quit,
  ]

  @Test func eachItemNameReadsBackAsTheItem() throws {
    for id in all {
      #expect(ItemID(name: id.name) == id, "\(id.name)")
    }
  }

  @Test func theNamesAreTheDocumentedOnes() throws {
    #expect(
      all.map(\.name) == [
        "startBosun", "showProblems", "openConsole", "checkForUpdates", "openAtLogin", "updateBosun",
        "helpMenu",
        "help.guide", "help.doctor", "help.logs", "help.uninstall", "quit",
      ])
  }

  @Test func theNamesAreDistinct() throws {
    #expect(Set(all.map(\.name)).count == all.count)
  }

  @Test func aNameThatIsNotAnItemIsNil() throws {
    #expect(ItemID(name: "reboot") == nil)
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
    #expect(ids == ["openConsole", "checkForUpdates", "openAtLogin", "helpMenu", "quit"])
  }

  @Test func anItemCarriesItsStateAndItsChildren() throws {
    let state = try loaded("status-stopped")
    let model = menu(for: state, update: .idle, flow: FlowState(), login: true)
    let object = try jsonObject(menuJSON(model, status: state))
    #expect(object["level"] as? String == "stopped")
    let items = try #require(object["sections"] as? [[[String: Any]]]).flatMap { $0 }
    let console = try #require(items.first { $0["id"] as? String == "openConsole" })
    #expect(console["enabled"] as? Bool == false)
    #expect(console["key"] as? String == "o")
    let login = try #require(items.first { $0["id"] as? String == "openAtLogin" })
    #expect(login["checked"] as? Bool == true)
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
    #expect(try action(#"{"item": "quit"}"#) == .item(.quit))
    #expect(try action(#"{"item": "help.doctor"}"#) == .item(.help(.doctor)))
  }

  @Test func anAnswerNamesTheButtonAndTheBoxes() throws {
    let read = try action(#"{"answer": {"button": "Quit bosun", "boxes": {"sleep": false}}}"#)
    #expect(read == .answer(HookAnswer(button: "Quit bosun", boxes: ["sleep": false])))
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
      DialogBox(id: "keepIcon", title: "Keep", on: false),
    ],
    buttons: ["Quit bosun", "Cancel"], cancel: 1)

  @Test func theButtonIsFoundByItsTitle() throws {
    let answer = HookAnswer(button: "Cancel", boxes: [:]).resolve(for: spec)
    #expect(answer?.button == 1)
  }

  @Test func theBoxesStartAtTheDialogsDefaultsAndTakeTheAnswers() throws {
    let answer = HookAnswer(button: "Quit bosun", boxes: ["keepIcon": true]).resolve(for: spec)
    #expect(answer == DialogAnswer(button: 0, boxes: ["pauseMorning": true, "keepIcon": true]))
  }

  @Test func aButtonThatIsNotThereIsNoAnswer() throws {
    #expect(HookAnswer(button: "Shred", boxes: [:]).resolve(for: spec) == nil)
  }

  @Test func aBoxThatIsNotThereIsNoAnswer() throws {
    #expect(HookAnswer(button: "Cancel", boxes: ["nothing": true]).resolve(for: spec) == nil)
  }
}
