import BosunMenuCore
import Foundation

/// Writes `bosun-<command>.command` into a folder, executable, replacing the last one: what Terminal opens
/// to run `bosun doctor` or `bosun uninstall` (the text is `CommandScript.text`).
public struct CommandScriptWriter: ScriptWriting {
  public let executable: String
  public let folder: URL

  public init(executable: String, folder: URL) {
    self.executable = executable
    self.folder = folder
  }

  public func write(args: [String]) throws -> URL {
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let url = folder.appendingPathComponent("bosun-\(args.first ?? "run").command")
    try CommandScript.text(executable: executable, args: args).write(
      to: url, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
    return url
  }
}
