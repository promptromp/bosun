import Foundation
import Testing

@testable import BosunMenuCore

@Suite struct LoadingMenuTests {
  @Test func beforeTheFirstStatusTheMenuOnlySaysItIsLooking() {
    let model = loadingMenu(flow: FlowState())
    #expect(model.header.dot == .grey)
    #expect(model.header.sentence == "Checking bosun…")
    #expect(model.iconBadge == nil)
    let items = model.sections.flatMap { $0 }
    #expect(items.map(\.id) == [.helpMenu, .quit])
    #expect(items.last?.title == "Quit bosun.app")
    // Nothing that runs bosun is offered before it is known to be there
    let help = items.first?.children ?? []
    #expect(help.first { $0.id == .help(.guide) }?.enabled == true)
    #expect(help.filter { $0.id != .help(.guide) }.allSatisfy { !$0.enabled })
  }
}

@Suite struct StartFailedTests {
  private let check = Fixtures.decode(UpdateCheck.self, "update-check-available")

  @Test func anUpdateThatCouldNotBeStartedFailsWithItsReason() {
    let (state, commands) = next(UpdateState.starting(check), on: UpdateEvent.startFailed("no permission"))
    #expect(state == .failed("no permission"))
    #expect(commands.isEmpty)
  }

  @Test func inAnyOtherStateItMeansNothing() {
    let (state, commands) = next(UpdateState.idle, on: UpdateEvent.startFailed("no permission"))
    #expect(state == .idle)
    #expect(commands.isEmpty)
  }
}

@Suite struct ConsoleURLTests {
  @Test func theAddressIsTheFirstLine() throws {
    let url = try consoleURL(from: exited(0, "http://127.0.0.1:8123/?t=abc\n")).get()
    #expect(url == URL(string: "http://127.0.0.1:8123/?t=abc"))
  }

  @Test func localhostIsAlsoLocal() throws {
    let url = try consoleURL(from: exited(0, "\n  http://localhost:8123/?t=abc  \n")).get()
    #expect(url.host == "localhost")
  }

  /// The answer is opened in the browser: only the console on this Mac may be.
  @Test func anythingButTheLocalConsoleIsRefused() {
    for text in [
      "file:///etc/passwd", "https://example.com/?t=x", "http://192.168.1.4:8123/", "javascript:alert(1)",
      "not a url", "",
    ] {
      let result = consoleURL(from: exited(0, text + "\n"))
      guard case .failure(let error) = result else {
        Issue.record("\(text) should be refused")
        continue
      }
      #expect(!error.message.isEmpty)
    }
  }

  @Test func aFailureSaysWhatBosunSaid() {
    guard
      case .failure(let error) = consoleURL(from: exited(1, "", err: "bosun: the console is not running\n"))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(error.message == "bosun: the console is not running")
  }

  @Test func aFailureThatSaidNothingSaysTheStatus() {
    guard case .failure(let error) = consoleURL(from: exited(2)) else {
      Issue.record("expected a failure")
      return
    }
    #expect(error.message == "bosun exited with status 2 and said nothing")
  }

  @Test func aHangAndAMissingBosunSayWhat() {
    let hung = CommandResult(outcome: .timedOut, stdout: "", stderr: "")
    let missing = CommandResult(outcome: .notInstalled, stdout: "", stderr: "")
    guard case .failure(let first) = consoleURL(from: hung),
      case .failure(let second) = consoleURL(from: missing)
    else {
      Issue.record("expected failures")
      return
    }
    #expect(first.message == "bosun did not answer in time")
    #expect(second.message == "bosun is not installed")
  }
}

@Suite struct CommandScriptTests {
  private func scratchFolder() throws -> URL {
    let dir = FileManager.default.temporaryDirectory
      .appendingPathComponent("bosun-script-test-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
  }

  @Test func theScriptRunsBosunAndWaitsForAKeyBeforeTheWindowCloses() {
    let text = CommandScript.text(executable: "/Users/me/.local/bin/bosun", args: ["doctor"])
    #expect(text.hasPrefix("#!/bin/sh\n"))
    #expect(text.contains("'/Users/me/.local/bin/bosun' 'doctor'\n"))
    #expect(text.contains("read -r"))
    #expect(text.contains("exit $status"))
  }

  @Test func whatIsNotPlainIsQuoted() {
    #expect(CommandScript.quote("plain") == "'plain'")
    #expect(CommandScript.quote("a b") == "'a b'")
    #expect(CommandScript.quote("it's") == "'it'\\''s'")
    #expect(CommandScript.quote("$(rm -rf ~)") == "'$(rm -rf ~)'")
  }

  @Test func theFileIsExecutableAndIsNamedForTheCommand() throws {
    let folder = try scratchFolder()
    let url = try CommandScript.write(
      executable: "/Users/me/.local/bin/bosun", args: ["uninstall"], folder: folder)
    #expect(url.lastPathComponent == "bosun-uninstall.command")
    #expect(url.deletingLastPathComponent().standardizedFileURL == folder.standardizedFileURL)
    let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int
    #expect(mode == 0o755)
  }

  @Test func writingItAgainReplacesIt() throws {
    let folder = try scratchFolder()
    _ = try CommandScript.write(executable: "/one", args: ["doctor"], folder: folder)
    let url = try CommandScript.write(executable: "/two", args: ["doctor"], folder: folder)
    let text = try String(contentsOf: url, encoding: .utf8)
    #expect(text.contains("'/two'"))
    #expect(!text.contains("'/one'"))
  }

  @Test func theFolderIsMadeWhenItIsNotThere() throws {
    let folder = try scratchFolder().appendingPathComponent("not/yet")
    let url = try CommandScript.write(executable: "/x", args: ["doctor"], folder: folder)
    #expect(FileManager.default.fileExists(atPath: url.path))
  }

  /// Run for real, with a stand-in for bosun: it prints, the script waits for Return, and passes its status on.
  @Test func theScriptRunsAndKeepsTheExitStatus() throws {
    let folder = try scratchFolder()
    let fake = folder.appendingPathComponent("fake-bosun")
    try "#!/bin/sh\necho \"ran: $*\"\nexit 4\n".write(to: fake, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: fake.path)
    let url = try CommandScript.write(executable: fake.path, args: ["doctor"], folder: folder)
    let process = Process()
    process.executableURL = url
    let input = Pipe()
    let output = Pipe()
    process.standardInput = input
    process.standardOutput = output
    try process.run()
    input.fileHandleForWriting.write(Data("\n".utf8))
    try input.fileHandleForWriting.close()
    let data = output.fileHandleForReading.readDataToEndOfFile()
    let said = try #require(String(bytes: data, encoding: .utf8))
    process.waitUntilExit()
    #expect(said.contains("ran: doctor"))
    #expect(said.contains("Press Return"))
    #expect(process.terminationStatus == 4)
  }
}

@Suite struct LinkTests {
  @Test func theGuideIsTheDocsSite() {
    #expect(Links.guide.scheme == "https")
    #expect(Links.guide.absoluteString == "https://promptromp.github.io/bosun/")
  }
}
