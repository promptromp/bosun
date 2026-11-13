import Foundation

// The JSON contract with bosun's CLI (docs/design/menu-bar.md): every object the app reads, as the
// commands of part 1 print it. The app understands `"contract": 1`; a field is only ever added, so a
// field this file does not name is ignored, and a number it does not know is refused before anything else
// is read. tools/contract_samples.py writes the test fixtures from bosun's own code, so a change there
// fails a Python test until the fixtures (and, if needed, this file) follow.

public enum Contract {
  /// The one contract number this app reads.
  public static let supported = 1
}

public enum ContractError: Error, Equatable, Sendable, CustomStringConvertible {
  case unsupported(Int)
  case unreadable(String)

  var isUnsupported: Bool {
    if case .unsupported = self { return true }
    return false
  }

  public var description: String {
    switch self {
    case .unsupported(let number):
      return "bosun.app needs an update: bosun speaks version \(number) of its contract, "
        + "this app reads version \(Contract.supported)"
    case .unreadable(let why):
      return "bosun's reply could not be read: \(why)"
    }
  }
}

private struct ContractHeader: Decodable {
  let contract: Int?
}

/// Decodes bosun's JSON object of type `T`, after checking its contract number: every object bosun prints
/// carries one, and one that is missing or not ours is refused.
public func decodeContract<T: Decodable>(
  _ type: T.Type, from text: String
) throws(ContractError) -> T {
  let decoder = JSONDecoder()
  decoder.keyDecodingStrategy = .convertFromSnakeCase
  let data = Data(text.utf8)
  do {
    let header = try decoder.decode(ContractHeader.self, from: data)
    guard let number = header.contract else {
      throw ContractError.unreadable("it has no contract number")
    }
    if number != Contract.supported { throw ContractError.unsupported(number) }
    return try decoder.decode(type, from: data)
  } catch let error as ContractError {
    throw error
  } catch {
    throw ContractError.unreadable(plainReason(error))
  }
}

/// Why a reply could not be read, in a plain line: never a dump of the decoder's own error.
private func plainReason(_ error: any Error) -> String {
  guard let failure = error as? DecodingError else { return "it is not what bosun prints" }
  switch failure {
  case .keyNotFound(let key, _):
    return "it has no \"\(key.stringValue)\""
  case .typeMismatch(_, let context), .valueNotFound(_, let context), .dataCorrupted(let context):
    let path = context.codingPath.map(\.stringValue).filter { Int($0) == nil }.joined(separator: ".")
    return path.isEmpty ? "it is not the JSON bosun prints" : "\"\(path)\" is not what was expected"
  @unknown default:
    return "it is not what bosun prints"
  }
}

// MARK: bosun status --json

public enum Level: String, Decodable, Equatable, Sendable {
  case ok, warning, error, stopped
}

public struct Stopped: Decodable, Equatable, Sendable {
  public let since: String
  public let morningPaused: Bool
}

/// The update's state in `status`. A word this app does not know is `unknown`, and shows as no update.
public enum UpdateRun: Equatable, Sendable {
  case none, running
  case unknown(String)
}

extension UpdateRun: Decodable {
  public init(from decoder: any Decoder) throws {
    let word = try decoder.singleValueContainer().decode(String.self)
    switch word {
    case "none": self = .none
    case "running": self = .running
    default: self = .unknown(word)
    }
  }
}

public struct UpdateProgress: Decodable, Equatable, Sendable {
  public let state: UpdateRun
  public let waitingFor: [String]
}

public struct ConsoleStatus: Decodable, Equatable, Sendable {
  public let running: Bool
  public let url: String?
}

public struct StatusSnapshot: Decodable, Equatable, Sendable {
  public let contract: Int
  public let level: Level
  public let sentence: String
  public let detail: [String]
  public let windows: Int
  public let waiting: [String]
  public let keepAwakeUntil: String?
  public let stopped: Stopped?
  public let update: UpdateProgress
  public let console: ConsoleStatus
  public let problems: [String]
  public let logs: String

  /// What the update flow is told of the status: whether an update runs, and what it waits for.
  public var updateEvent: UpdateEvent {
    .statusShows(running: update.state == .running, waitingFor: update.waitingFor)
  }
}

// MARK: bosun update --check --json

public struct InstalledVersion: Decodable, Equatable, Sendable {
  public let version: String?
  public let commit: String
}

public struct LatestVersion: Decodable, Equatable, Sendable {
  public let version: String
  public let commit: String
  public let notes: String
}

public struct UpdateCheck: Decodable, Equatable, Sendable {
  public let contract: Int
  public let channel: String
  public let installed: InstalledVersion
  public let latest: LatestVersion?
  public let available: Bool
  public let skipped: Bool
  public let ahead: Bool
  public let error: String?
}

// MARK: bosun update --dry-run --json

/// How an update treats a window (lib/update_plan.py's `Treatment`); the text is bosun's own.
public enum Treatment: Hashable, Sendable {
  case leave, reread, restart, takeStock, waitUntilIdle, askFirst, relaunch, restartLoop
  case notStartedByBosun
  case unknown(String)

  private static let known: [String: Treatment] = [
    "leave": .leave, "re-read": .reread, "restart": .restart,
    "take stock, then restart": .takeStock, "wait until idle": .waitUntilIdle,
    "ask first": .askFirst, "relaunch": .relaunch, "restart the loop": .restartLoop,
    "not started by bosun: left as it is": .notStartedByBosun,
  ]

  public init(text: String) {
    self = Self.known[text] ?? .unknown(text)
  }

  /// The window is restarted (or started again, or waited for and then restarted) by the update.
  public var restarts: Bool {
    switch self {
    case .restart, .takeStock, .waitUntilIdle, .relaunch, .restartLoop: return true
    default: return false
    }
  }

  /// The update cannot decide for it: it asks first (or the app does not know what bosun means).
  public var needsYou: Bool {
    switch self {
    case .askFirst, .unknown: return true
    default: return false
    }
  }
}

public struct PlannedWindow: Equatable, Sendable {
  public let name: String
  public let treatment: Treatment
}

extension PlannedWindow: Decodable {
  public init(from decoder: any Decoder) throws {
    var pair = try decoder.unkeyedContainer()
    let name = try pair.decode(String.self)
    let how = try pair.decode(String.self)
    self.init(name: name, treatment: Treatment(text: how))
  }
}

public struct UpdatePlan: Equatable, Sendable {
  public let fromCommit: String
  public let toCommit: String
  public let commits: [String]
  public let windows: [PlannedWindow]
  public let handed: [String]
  public let userChanges: [String]
  public let blockers: [String]

  public var isBlocked: Bool { !blockers.isEmpty }
  public var restarting: [String] { windows.filter { $0.treatment.restarts }.map(\.name) }
  public var windowsNeedingYou: [String] { windows.filter { $0.treatment.needsYou }.map(\.name) }
}

extension UpdatePlan: Decodable {
  private enum Key: String, CodingKey {
    case fromCommit, toCommit, commits, windows, handed, userChanges, blockers
  }

  public init(from decoder: any Decoder) throws {
    let values = try decoder.container(keyedBy: Key.self)
    func strings(_ key: Key) throws -> [String] {
      try values.decodeIfPresent([String].self, forKey: key) ?? []
    }
    self.init(
      fromCommit: try values.decodeIfPresent(String.self, forKey: .fromCommit) ?? "",
      toCommit: try values.decodeIfPresent(String.self, forKey: .toCommit) ?? "",
      commits: try strings(.commits),
      windows: try values.decodeIfPresent([PlannedWindow].self, forKey: .windows) ?? [],
      handed: try strings(.handed),
      userChanges: try strings(.userChanges),
      blockers: try strings(.blockers))
  }
}

/// What `bosun update --dry-run --json` prints: the plan, null when none was made.
public struct UpdateDryRun: Decodable, Equatable, Sendable {
  public let plan: UpdatePlan?
}

// MARK: bosun quit --plan --json, bosun quit ... --json

public struct QuitPlan: Decodable, Equatable, Sendable {
  public let contract: Int
  public let windows: [String]
  public let busy: [String]
  public let waiting: [String]
  public let keepAwakeUntil: String?
  public let refused: String?
  public let note: String?
  /// How long a `--let-finish` quit may wait (BOSUN_QUIT_WAIT_MINUTES) and how long its steps wait for the
  /// windows lock (BOSUN_WINDOWS_LOCK_WAIT_SECONDS): what the quit's timeout is made of. Part 1 prints both
  /// in every plan; they are optional here only so a plan object without them still reads (see `wait`).
  public let waitMinutes: Int?
  public let lockWaitSeconds: Int?

  /// The quit's wait as bosun said it, or nil for a plan object without the fields.
  public var wait: QuitWait? {
    guard let minutes = waitMinutes, let lock = lockWaitSeconds else { return nil }
    return QuitWait(minutes: minutes, lockSeconds: lock)
  }

  /// What quitting would meet, one line each, for the first dialog.
  public var lines: [String] {
    var lines = [Self.windowsLine(windows)]
    if !busy.isEmpty { lines.append("Busy: " + busy.joined(separator: ", ")) }
    if !waiting.isEmpty {
      lines.append("Waiting for you (quit stops them): " + waiting.joined(separator: ", "))
    }
    if let until = keepAwakeUntil { lines.append("The Mac is kept awake until \(until)") }
    return lines
  }

  private static func windowsLine(_ names: [String]) -> String {
    switch names.count {
    case 0: return "No windows are open"
    case 1: return "1 window open: " + names[0]
    default: return "\(names.count) windows open: " + names.joined(separator: ", ")
    }
  }
}

/// How a quit ended. A word this app does not know is `unknown`, and shows as a failure with the word.
public enum QuitResultKind: Equatable, Sendable {
  case stopped, busy, refused, failed
  case unknown(String)

  public init(text: String) {
    switch text {
    case "stopped": self = .stopped
    case "busy": self = .busy
    case "refused": self = .refused
    case "failed": self = .failed
    default: self = .unknown(text)
    }
  }
}

extension QuitResultKind: Decodable {
  public init(from decoder: any Decoder) throws {
    self.init(text: try decoder.singleValueContainer().decode(String.self))
  }
}

/// What the console was left as (lib/quit.py's `CONSOLE_*`). A word this app does not know is `unknown`
/// and is said nothing of.
public enum ConsoleOutcome: Equatable, Sendable {
  case stopped, notRunning, stillRunning
  case unknown(String)

  public init(text: String) {
    switch text {
    case "stopped": self = .stopped
    case "not running": self = .notRunning
    case "still running": self = .stillRunning
    default: self = .unknown(text)
    }
  }
}

extension ConsoleOutcome: Decodable {
  public init(from decoder: any Decoder) throws {
    self.init(text: try decoder.singleValueContainer().decode(String.self))
  }
}

public struct QuitResult: Equatable, Sendable {
  public let plan: QuitPlan
  public let result: QuitResultKind
  public let error: String?
  public let morningPaused: Bool?
  public let keepAwake: String?
  public let console: ConsoleOutcome?
  public let consolePort: Int?

  /// The line to show when the quit did not happen: its error, or the reason it was refused.
  public var message: String? { error ?? plan.refused }
}

extension QuitResult: Decodable {
  private enum Key: String, CodingKey {
    case result, error, morningPaused, keepAwake, console, consolePort
  }

  public init(from decoder: any Decoder) throws {
    let values = try decoder.container(keyedBy: Key.self)
    self.init(
      plan: try QuitPlan(from: decoder),
      result: try values.decode(QuitResultKind.self, forKey: .result),
      error: try values.decodeIfPresent(String.self, forKey: .error),
      morningPaused: try values.decodeIfPresent(Bool.self, forKey: .morningPaused),
      keepAwake: try values.decodeIfPresent(String.self, forKey: .keepAwake),
      console: try values.decodeIfPresent(ConsoleOutcome.self, forKey: .console),
      consolePort: try values.decodeIfPresent(Int.self, forKey: .consolePort))
  }
}

// MARK: bosun app status --json

public struct AppStatus: Decodable, Equatable, Sendable {
  public let contract: Int
  public let installed: String?
  public let wanted: String
  public let path: String
  public let login: Bool
}
