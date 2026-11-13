import BosunMenuCore
import Foundation

// The test hooks' files (see BosunMenuCore/TestHooks.swift for the formats): the folder the app writes its menu
// to, takes actions from and logs into, and the `BosunCLI` that logs what it runs. Foundation only.

public enum HookLogEntry: Equatable, Sendable {
  case command([String], detached: Bool, outcome: String)
  case open(String)
  case error(String)

  var object: [String: Any] {
    switch self {
    case .command(let args, let detached, let outcome):
      return ["command": args, "detached": detached, "outcome": outcome]
    case .open(let target): return ["open": target]
    case .error(let why): return ["error": why]
    }
  }
}

/// How a command ended, in a word for the log.
func describe(_ outcome: CommandOutcome) -> String {
  switch outcome {
  case .exited(let code): return "exited \(code)"
  case .started: return "started"
  case .timedOut: return "timed out"
  case .notInstalled: return "not installed"
  case .couldNotStart(let why): return "could not start: \(why)"
  }
}

/// A `BosunCLI` that logs each command it passes on and how it ended.
public struct LoggingBosunCLI: BosunCLI {
  private let inner: any BosunCLI
  private let log: @Sendable (HookLogEntry) -> Void

  public init(wrapping inner: any BosunCLI, log: @escaping @Sendable (HookLogEntry) -> Void) {
    self.inner = inner
    self.log = log
  }

  public func run(_ args: [String], timeout: Duration) async -> CommandResult {
    let result = await inner.run(args, timeout: timeout)
    log(.command(args, detached: false, outcome: describe(result.outcome)))
    return result
  }

  public func start(_ args: [String]) async -> CommandResult {
    let result = await inner.start(args)
    log(.command(args, detached: true, outcome: describe(result.outcome)))
    return result
  }
}

// MARK: the files

public struct TestHookFiles: Sendable {
  public let directory: URL

  public init(directory: URL) {
    self.directory = directory
  }

  private var actionsFolder: URL { directory.appendingPathComponent("actions") }

  public func prepare() throws {
    try FileManager.default.createDirectory(at: actionsFolder, withIntermediateDirectories: true)
  }

  public func writeMenu(_ model: MenuModel, status: StatusState?) throws {
    try menuJSON(model, status: status).write(
      to: directory.appendingPathComponent("menu.json"), options: .atomic)
  }

  /// The actions waiting, in the order of their numbers, taken (their files removed). An answer is for the
  /// open dialog: with none open it waits, and so does everything after it; and one answer is taken at a
  /// time (the next is for the dialog that follows, which is not open yet). A file that is not an action is
  /// logged and removed.
  public func takeActions(dialogOpen: Bool) -> [HookAction] {
    var taken: [HookAction] = []
    for file in pendingFiles() {
      guard let data = try? Data(contentsOf: file) else { continue }
      var answered = false
      do {
        let action = try HookAction.decode(data)
        if case .answer = action {
          if !dialogOpen { break }
          answered = true
        }
        taken.append(action)
      } catch {
        log(.error("\(file.lastPathComponent): \(error)"))
      }
      try? FileManager.default.removeItem(at: file)
      if answered { break }
    }
    return taken
  }

  /// `<n>.json` files, by n.
  private func pendingFiles() -> [URL] {
    let names = (try? FileManager.default.contentsOfDirectory(atPath: actionsFolder.path)) ?? []
    return names.compactMap { name -> (Int, URL)? in
      guard name.hasSuffix(".json"), let number = Int(name.dropLast(".json".count)) else { return nil }
      return (number, actionsFolder.appendingPathComponent(name))
    }
    .sorted { $0.0 < $1.0 }.map(\.1)
  }

  public func log(_ entry: HookLogEntry) {
    let options: JSONSerialization.WritingOptions = [.sortedKeys, .withoutEscapingSlashes]
    guard var line = try? JSONSerialization.data(withJSONObject: entry.object, options: options) else {
      return
    }
    line.append(0x0A)
    let url = directory.appendingPathComponent("log.jsonl")
    if !FileManager.default.fileExists(atPath: url.path) {
      FileManager.default.createFile(atPath: url.path, contents: nil)
    }
    guard let handle = try? FileHandle(forWritingTo: url) else { return }
    defer { try? handle.close() }
    _ = try? handle.seekToEnd()
    try? handle.write(contentsOf: line)
  }
}
