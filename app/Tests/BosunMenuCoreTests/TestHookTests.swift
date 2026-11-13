import Foundation
import Testing

@testable import BosunMenuCore

private func scratchFolder() throws -> URL {
  let dir = FileManager.default.temporaryDirectory
    .appendingPathComponent("bosun-hooks-test-\(UUID().uuidString)")
  try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
  return dir
}

private func loaded(_ name: String) -> StatusState { .loaded(Fixtures.status(name), current: true) }

private func jsonObject(_ data: Data) throws -> [String: Any] {
  try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
}

@Suite struct ItemNameTests {
  private let all: [ItemID] = [
    .startBosun, .showProblems, .openConsole, .checkForUpdates, .openAtLogin, .updateBosun, .helpMenu,
    .help(.guide), .help(.doctor), .help(.logs), .help(.uninstall), .quit,
  ]

  @Test func eachItemNameReadsBackAsTheItem() {
    for id in all {
      #expect(ItemID(name: id.name) == id, "\(id.name)")
    }
  }

  @Test func theNamesAreTheDocumentedOnes() {
    #expect(
      all.map(\.name) == [
        "startBosun", "showProblems", "openConsole", "checkForUpdates", "openAtLogin", "updateBosun",
        "helpMenu",
        "help.guide", "help.doctor", "help.logs", "help.uninstall", "quit",
      ])
  }

  @Test func theNamesAreDistinct() {
    #expect(Set(all.map(\.name)).count == all.count)
  }

  @Test func aNameThatIsNotAnItemIsNil() {
    #expect(ItemID(name: "reboot") == nil)
    #expect(ItemID(name: "help.nothing") == nil)
    #expect(ItemID(name: "") == nil)
  }
}

@Suite struct MenuJSONTests {
  @Test func theMenuAndItsStatusLevelAreWritten() throws {
    let state = loaded("status-ok")
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
    let state = loaded("status-stopped")
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
    let state = loaded("status-warning")
    let model = menu(for: state, update: .idle, flow: FlowState())
    let object = try jsonObject(menuJSON(model, status: state))
    #expect(object["icon_badge"] as? String == "warning")
    #expect(object["level"] as? String == "warning")
  }

  @Test func theLevelNamesWhatIsKnownWhenThereIsNoStatus() {
    #expect(statusLevelName(.notInstalled) == "not_installed")
    #expect(statusLevelName(.unreachable("x")) == "unreachable")
    #expect(statusLevelName(.contractTooNew(2)) == "contract_too_new")
    #expect(statusLevelName(nil) == "loading")
    #expect(statusLevelName(loaded("status-error")) == "error")
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

  @Test func whatIsNotAnActionIsRefusedWithAReason() {
    for text in [#"{"item": "reboot"}"#, "{}", "not json", #"{"item": 3}"#, #"{"answer": {}}"#] {
      #expect(throws: HookError.self) { try action(text) }
    }
  }

  @Test func theReasonSaysWhatWasWrong() {
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

  @Test func theButtonIsFoundByItsTitle() {
    let answer = HookAnswer(button: "Cancel", boxes: [:]).resolve(for: spec)
    #expect(answer?.button == 1)
  }

  @Test func theBoxesStartAtTheDialogsDefaultsAndTakeTheAnswers() {
    let answer = HookAnswer(button: "Quit bosun", boxes: ["keepIcon": true]).resolve(for: spec)
    #expect(answer == DialogAnswer(button: 0, boxes: ["pauseMorning": true, "keepIcon": true]))
  }

  @Test func aButtonThatIsNotThereIsNoAnswer() {
    #expect(HookAnswer(button: "Shred", boxes: [:]).resolve(for: spec) == nil)
  }

  @Test func aBoxThatIsNotThereIsNoAnswer() {
    #expect(HookAnswer(button: "Cancel", boxes: ["nothing": true]).resolve(for: spec) == nil)
  }
}

@Suite struct TestHookFilesTests {
  private func files() throws -> (TestHookFiles, URL) {
    let folder = try scratchFolder()
    let hooks = TestHookFiles(directory: folder)
    try hooks.prepare()
    return (hooks, folder)
  }

  private func drop(_ text: String, as name: String, in folder: URL) throws {
    try text.write(to: folder.appendingPathComponent("actions/\(name)"), atomically: true, encoding: .utf8)
  }

  @Test func prepareMakesTheActionsFolder() throws {
    let (_, folder) = try files()
    var isDirectory: ObjCBool = false
    #expect(
      FileManager.default.fileExists(
        atPath: folder.appendingPathComponent("actions").path, isDirectory: &isDirectory))
    #expect(isDirectory.boolValue)
  }

  @Test func prepareMakesTheFolderItself() throws {
    let folder = try scratchFolder().appendingPathComponent("not/yet/here")
    try TestHookFiles(directory: folder).prepare()
    #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("actions").path))
  }

  @Test func theMenuIsWrittenToMenuJSON() throws {
    let (hooks, folder) = try files()
    let state = loaded("status-ok")
    try hooks.writeMenu(menu(for: state, update: .idle, flow: FlowState()), status: state)
    let data = try Data(contentsOf: folder.appendingPathComponent("menu.json"))
    #expect(try jsonObject(data)["level"] as? String == "ok")
  }

  @Test func aNewMenuReplacesTheOldOne() throws {
    let (hooks, folder) = try files()
    for name in ["status-ok", "status-error"] {
      let state = loaded(name)
      try hooks.writeMenu(menu(for: state, update: .idle, flow: FlowState()), status: state)
    }
    let data = try Data(contentsOf: folder.appendingPathComponent("menu.json"))
    #expect(try jsonObject(data)["level"] as? String == "error")
  }

  @Test func actionsAreTakenInTheOrderOfTheirNumbers() throws {
    let (hooks, folder) = try files()
    try drop(#"{"item": "quit"}"#, as: "10.json", in: folder)
    try drop(#"{"item": "startBosun"}"#, as: "9.json", in: folder)
    try drop(#"{"item": "openConsole"}"#, as: "2.json", in: folder)
    let taken = hooks.takeActions(dialogOpen: false)
    #expect(taken == [.item(.openConsole), .item(.startBosun), .item(.quit)])
  }

  /// The harness waits for its file to go: that is how it knows the action was taken.
  @Test func aTakenActionsFileIsRemoved() throws {
    let (hooks, folder) = try files()
    try drop(#"{"item": "quit"}"#, as: "1.json", in: folder)
    _ = hooks.takeActions(dialogOpen: false)
    #expect(!FileManager.default.fileExists(atPath: folder.appendingPathComponent("actions/1.json").path))
    #expect(hooks.takeActions(dialogOpen: false).isEmpty)
  }

  /// An answer is for the open dialog; with none open it waits, and so does what follows it.
  @Test func anAnswerWaitsForADialog() throws {
    let (hooks, folder) = try files()
    try drop(#"{"item": "quit"}"#, as: "1.json", in: folder)
    try drop(#"{"answer": {"button": "Cancel"}}"#, as: "2.json", in: folder)
    try drop(#"{"item": "startBosun"}"#, as: "3.json", in: folder)
    #expect(hooks.takeActions(dialogOpen: false) == [.item(.quit)])
    #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("actions/2.json").path))
    let later = hooks.takeActions(dialogOpen: true)
    #expect(later == [.answer(HookAnswer(button: "Cancel", boxes: [:])), .item(.startBosun)])
  }

  @Test func onlyJSONFilesWithANumberAreActions() throws {
    let (hooks, folder) = try files()
    try drop(#"{"item": "quit"}"#, as: "notes.txt", in: folder)
    try drop(#"{"item": "quit"}"#, as: "abc.json", in: folder)
    try drop(#"{"item": "quit"}"#, as: "1.json.tmp", in: folder)
    #expect(hooks.takeActions(dialogOpen: false).isEmpty)
  }

  @Test func aFileThatIsNotAnActionIsLoggedAndRemoved() throws {
    let (hooks, folder) = try files()
    try drop(#"{"item": "reboot"}"#, as: "1.json", in: folder)
    try drop(#"{"item": "quit"}"#, as: "2.json", in: folder)
    #expect(hooks.takeActions(dialogOpen: false) == [.item(.quit)])
    #expect(!FileManager.default.fileExists(atPath: folder.appendingPathComponent("actions/1.json").path))
    let lines = try logLines(folder)
    #expect(lines.count == 1)
    #expect((lines[0]["error"] as? String)?.contains("reboot") == true)
  }

  @Test func theLogIsOneJSONObjectPerLine() throws {
    let (hooks, folder) = try files()
    hooks.log(.command(["status", "--json"], detached: false, outcome: "exited 0"))
    hooks.log(.command(["morning", "--now"], detached: true, outcome: "started"))
    hooks.log(.open("http://127.0.0.1:8123/?t=x"))
    hooks.log(.error("no dialog is open"))
    let lines = try logLines(folder)
    #expect(lines.count == 4)
    #expect(lines[0]["command"] as? [String] == ["status", "--json"])
    #expect(lines[0]["detached"] as? Bool == false)
    #expect(lines[0]["outcome"] as? String == "exited 0")
    #expect(lines[1]["detached"] as? Bool == true)
    #expect(lines[2]["open"] as? String == "http://127.0.0.1:8123/?t=x")
    #expect(lines[3]["error"] as? String == "no dialog is open")
  }

  @Test func theLogGrowsAcrossInstances() throws {
    let (hooks, folder) = try files()
    hooks.log(.open("a"))
    TestHookFiles(directory: folder).log(.open("b"))
    #expect(try logLines(folder).count == 2)
  }

  private func logLines(_ folder: URL) throws -> [[String: Any]] {
    let text = try String(contentsOf: folder.appendingPathComponent("log.jsonl"), encoding: .utf8)
    return try text.split(separator: "\n").map { try jsonObject(Data($0.utf8)) }
  }
}

@Suite struct LoggingCLITests {
  actor Lines {
    var entries: [HookLogEntry] = []
    func add(_ entry: HookLogEntry) { entries.append(entry) }
  }

  @Test func eachCommandIsLoggedWithHowItEnded() async {
    let fake = FakeBosunCLI()
    await fake.reply(["status", "--json"], exited(0, "{}"))
    await fake.reply(["quit", "--json"], CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    let lines = Lines()
    let cli = LoggingBosunCLI(wrapping: fake) { entry in Task { await lines.add(entry) } }
    let first = await cli.run(["status", "--json"], timeout: .seconds(1))
    let second = await cli.run(["quit", "--json"], timeout: .seconds(1))
    let third = await cli.start(["morning", "--now"])
    #expect(first.stdout == "{}")
    #expect(second.outcome == .timedOut)
    #expect(third.outcome == .started)
    #expect(await eventually { await lines.entries.count == 3 })
    let entries = await lines.entries
    #expect(entries.contains(.command(["status", "--json"], detached: false, outcome: "exited 0")))
    #expect(entries.contains(.command(["quit", "--json"], detached: false, outcome: "timed out")))
    #expect(entries.contains(.command(["morning", "--now"], detached: true, outcome: "started")))
  }

  @Test func eachKindOfOutcomeHasAWord() {
    #expect(describe(.exited(3)) == "exited 3")
    #expect(describe(.started) == "started")
    #expect(describe(.timedOut) == "timed out")
    #expect(describe(.notInstalled) == "not installed")
    #expect(describe(.couldNotStart("no")) == "could not start: no")
  }
}
