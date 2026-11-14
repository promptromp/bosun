import Foundation

// The Settings window's state and what it decides (docs/design/control-panel.md, "The Settings window"): a view
// over `bosun settings get|set --json`, so it shows what bosun reads, a value it cannot show as such (never as a
// default), and changes settings only through bosun. The SwiftUI window (BosunMenu) binds this state and calls
// back with a `SettingsEdit`; the coordinator carries the edit out (AppCoordinator+Settings.swift).

/// The keys the window shows, as bosun names them.
public enum SettingKey {
  public static let startAtLogin = "BOSUN_START_AT_LOGIN"
  public static let updateChannel = "BOSUN_UPDATE_CHANNEL"
  public static let days = "BOSUN_DAYS"
  public static let startTime = "BOSUN_START_TIME"
  public static let awakeUntil = "BOSUN_AWAKE_UNTIL"
  /// What moves the Mac's repeating wake (`sudo pmset`) while the wake is bosun's.
  public static let wake: Set<String> = [days, startTime]
}

/// The line a value bosun does not take shows, with the wizard as the way to fix it.
func invalidLine(_ value: String) -> String {
  "bosun.env says \"\(value)\": fix it with Change windows and connectors…"
}

/// How a setting's row shows: by its control (`editable`, `text` nil), or as text when the control cannot show it
/// (a value bosun does not take, or one it could not read: "unknown"), with a line under it.
public struct SettingRow: Equatable, Sendable {
  public let editable: Bool
  public let text: String?
  public let note: String?

  public init(editable: Bool, text: String?, note: String?) {
    self.editable = editable
    self.text = text
    self.note = note
  }
}

public struct SettingsState: Equatable, Sendable {
  /// What `settings get` read; nil until read, or when it could not be.
  public let view: SettingsView?
  public let openAtLogin: Bool
  /// The last failure or note, shown under the form.
  public let message: String?
  /// A set is running: the form is disabled.
  public let busy: Bool

  public init(view: SettingsView?, openAtLogin: Bool, message: String?, busy: Bool) {
    self.view = view
    self.openAtLogin = openAtLogin
    self.message = message
    self.busy = busy
  }

  /// Whether a change is to be made in Terminal: it moves the wake (the days or the start time) while the wake
  /// is bosun's, which `pmset` needs an administrator for.
  public func needsTerminal(_ values: [String: String]) -> Bool {
    view?.wake == "ours" && values.keys.contains { SettingKey.wake.contains($0) }
  }

  /// The state while an edit runs: the form disabled, the last message gone.
  public func starting() -> SettingsState {
    SettingsState(view: view, openAtLogin: openAtLogin, message: nil, busy: true)
  }

  /// How the row of `key` shows.
  public func row(_ key: String) -> SettingRow {
    guard let entry = view?.settings[key], let value = entry.value else {
      return SettingRow(editable: false, text: "unknown", note: nil)
    }
    guard entry.valid else { return SettingRow(editable: false, text: value, note: invalidLine(value)) }
    return SettingRow(editable: !busy, text: nil, note: nil)
  }

  /// The line at the top of the form when `bosun.env` could not be read.
  public var problem: String? {
    guard let view, !view.readable else { return nil }
    let why = view.error.map { " (\($0))" } ?? ""
    return
      "bosun.env cannot be read\(why), so its settings are unknown: fix it with Change windows and connectors…"
  }
}

/// What the window asks for.
public enum SettingsEdit: Equatable, Sendable {
  case openAtLogin(Bool)
  /// One Save of a pane (or one switch of General's): only what changed.
  case values([String: String])
  /// Terminal: `bosun init`.
  case changeWindows
  case openConfigFolder, showLogs
}

/// `settings set` with the values, as a person runs it in Terminal (no `--json`), in the order of their keys.
func settingsSetArgs(_ values: [String: String]) -> [String] {
  ["settings", "set"] + values.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }
}

/// What a `settings set --json` left for the window to say: bosun's refusal or failure, its notes, or nothing.
func settingsSetMessage(_ result: CommandResult) -> String? {
  switch decodeReply(SettingsSetReply.self, result, trustingStdout: true) {
  case .success(let reply):
    if let error = reply.error { return error }
    if result.outcome != .exited(0) { return failureText(result) }
    let notes = reply.notes ?? []
    return notes.isEmpty ? nil : notes.joined(separator: "\n")
  case .failure(let failure):
    return failure.message
  }
}
