import Foundation

// The quit's part of the JSON contract with bosun's CLI (Contract.swift has the rest and the rules).

// MARK: bosun quit --plan --json, bosun quit ... --json

public struct QuitPlan: Decodable, Equatable, Sendable {
  public let contract: Int
  public let windows: [String]
  public let busy: [String]
  public let waiting: [String]
  public let keepAwakeUntil: String?
  public let refused: String?
  public let note: String?
  /// How long a `--let-finish` quit may wait (BOSUN_QUIT_WAIT_MINUTES), how long its steps wait for the
  /// windows lock (BOSUN_WINDOWS_LOCK_WAIT_SECONDS) and the most its other steps take (its looks, the console's
  /// job, `bosun stop`): what the quit's timeout is made of. Part 1 prints the first two in every plan, and a
  /// later bosun the third; they are optional here only so a plan object without them still reads (`wait`).
  public let waitMinutes: Int?
  public let lockWaitSeconds: Int?
  public let stepsSeconds: Int?

  /// The quit's wait as bosun said it, or nil for a plan object without the minutes and the lock wait. One
  /// without the steps' seconds (a bosun from before they were said) gets what they were then.
  public var wait: QuitWait? {
    guard let minutes = waitMinutes, let lock = lockWaitSeconds else { return nil }
    return QuitWait(minutes: minutes, lockSeconds: lock, stepsSeconds: stepsSeconds ?? QuitWait.stepsBefore)
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
