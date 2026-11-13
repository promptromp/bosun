import Foundation
import Testing

@testable import BosunMenuSystem

@Suite struct CommandScriptWriterTests {
  private func scratchFolder() throws -> URL {
    let dir = FileManager.default.temporaryDirectory
      .appendingPathComponent("bosun-script-test-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
  }

  @Test func theFileIsExecutableAndIsNamedForTheCommand() throws {
    let folder = try scratchFolder()
    let url = try CommandScriptWriter(
      executable: "/Users/me/.local/bin/bosun", folder: folder
    ).write(args: ["uninstall"])
    #expect(url.lastPathComponent == "bosun-uninstall.command")
    #expect(url.deletingLastPathComponent().standardizedFileURL == folder.standardizedFileURL)
    let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int
    #expect(mode == 0o755)
  }

  @Test func writingItAgainReplacesIt() throws {
    let folder = try scratchFolder()
    _ = try CommandScriptWriter(executable: "/one", folder: folder).write(args: ["doctor"])
    let url = try CommandScriptWriter(executable: "/two", folder: folder).write(args: ["doctor"])
    let text = try String(contentsOf: url, encoding: .utf8)
    #expect(text.contains("'/two'"))
    #expect(!text.contains("'/one'"))
  }

  @Test func theFolderIsMadeWhenItIsNotThere() throws {
    let folder = try scratchFolder().appendingPathComponent("not/yet")
    let url = try CommandScriptWriter(executable: "/x", folder: folder).write(args: ["doctor"])
    #expect(FileManager.default.fileExists(atPath: url.path))
  }

  /// Run for real, with a stand-in for bosun: it prints, the script waits for Return, and passes its status on.
  @Test func theScriptRunsAndKeepsTheExitStatus() throws {
    let folder = try scratchFolder()
    let fake = folder.appendingPathComponent("fake-bosun")
    try "#!/bin/sh\necho \"ran: $*\"\nexit 4\n".write(to: fake, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: fake.path)
    let url = try CommandScriptWriter(executable: fake.path, folder: folder).write(args: ["doctor"])
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
