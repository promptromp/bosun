import BosunMenuCore
import Foundation
import Testing

@testable import BosunMenuSystem

private func scratchFolder() throws -> URL {
  let dir = FileManager.default.temporaryDirectory
    .appendingPathComponent("bosun-hooks-test-\(UUID().uuidString)")
  try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
  return dir
}

/// A status as `bosun status --json` prints it, for a level.
private func loaded(_ level: String) -> StatusState {
  let json = """
    {"contract": 1, "level": "\(level)", "sentence": "bosun is running", "detail": ["3 windows open"],
     "windows": 3, "waiting": [], "keep_awake_until": null, "stopped": null,
     "update": {"state": "none", "waiting_for": []}, "console": {"running": false, "url": null},
     "problems": [], "logs": "/logs"}
    """
  guard let snapshot = try? decodeContract(StatusSnapshot.self, from: json) else {
    fatalError("the sample status does not decode")
  }
  return .loaded(snapshot, current: true)
}

private func exited(_ code: Int32, _ out: String = "") -> CommandResult {
  CommandResult(outcome: .exited(code), stdout: out, stderr: "")
}

private func eventually(seconds: Double = 5, _ condition: () async -> Bool) async -> Bool {
  let end = Date().addingTimeInterval(seconds)
  while Date() < end {
    if await condition() { return true }
    try? await Task.sleep(for: .milliseconds(5))
  }
  return await condition()
}

private func jsonObject(_ data: Data) throws -> [String: Any] {
  try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
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
    let state = loaded("ok")
    try hooks.writeMenu(menu(for: state, update: .idle, flow: FlowState()), status: state)
    let data = try Data(contentsOf: folder.appendingPathComponent("menu.json"))
    #expect(try jsonObject(data)["level"] as? String == "ok")
  }

  @Test func aNewMenuReplacesTheOldOne() throws {
    let (hooks, folder) = try files()
    for name in ["ok", "error"] {
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
    // One answer a tick: what follows it is for the dialog after it, which is not open yet
    let later = hooks.takeActions(dialogOpen: true)
    #expect(later == [.answer(HookAnswer(button: "Cancel", boxes: [:]))])
    #expect(hooks.takeActions(dialogOpen: false) == [.item(.startBosun)])
  }

  @Test func twoAnswersAreTakenOnTwoTicks() throws {
    let (hooks, folder) = try files()
    try drop(#"{"answer": {"button": "Quit bosun"}}"#, as: "1.json", in: folder)
    try drop(#"{"answer": {"button": "Let Them Finish"}}"#, as: "2.json", in: folder)
    #expect(hooks.takeActions(dialogOpen: true) == [.answer(HookAnswer(button: "Quit bosun", boxes: [:]))])
    #expect(
      hooks.takeActions(dialogOpen: true) == [.answer(HookAnswer(button: "Let Them Finish", boxes: [:]))])
    #expect(hooks.takeActions(dialogOpen: true).isEmpty)
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
