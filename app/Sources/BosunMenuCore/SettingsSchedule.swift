import Foundation

// The Schedule pane's draft: the days, the start time and until when the Mac is kept awake, as the person edits
// them before Save, the line that says them back, and the values a Save sends (only what changed, and never a
// setting the pane could not show).

/// A time of day as bosun writes it: `HH:MM`, 24-hour.
public struct TimeOfDay: Equatable, Sendable {
  public let hour: Int
  public let minute: Int

  public init(hour: Int, minute: Int) {
    self.hour = hour
    self.minute = minute
  }

  /// `HH:MM` read, two digits each; nil for anything else.
  public init?(_ text: String) {
    let parts = text.split(separator: ":", omittingEmptySubsequences: false)
    guard parts.count == 2, parts.allSatisfy({ $0.count == 2 && $0.allSatisfy(\.isASCII) }),
      let hour = Int(parts[0]), let minute = Int(parts[1]), (0..<24).contains(hour), (0..<60).contains(minute)
    else { return nil }
    self.init(hour: hour, minute: minute)
  }

  public var text: String {
    String(format: "%02d:%02d", hour, minute)
  }
}

public struct ScheduleDraft: Equatable, Sendable {
  /// The day toggles' labels, Monday first.
  public static let dayLabels = ["M", "T", "W", "T", "F", "S", "S"]
  private static let dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
  /// pmset's letters, which `BOSUN_DAYS` is written in, Monday first.
  private static let letters: [Character] = ["M", "T", "W", "R", "F", "S", "U"]
  private static let weekdays = [true, true, true, true, true, false, false]
  private static let weekends = [false, false, false, false, false, true, true]

  /// Seven, Monday first.
  public var days: [Bool]
  public var start: TimeOfDay
  public var until: TimeOfDay
  /// Which of the three the pane could show (a value bosun read and takes): only those are said back or saved.
  private let shown: Set<String>

  /// The draft of what bosun read. A setting it cannot show starts blank (no day, midnight), and its row shows
  /// it as text instead of its control (`SettingsState.row`).
  public init(view: SettingsView) {
    func value(_ key: String) -> String? {
      guard let entry = view.settings[key], entry.valid else { return nil }
      return entry.value
    }
    let days = value(SettingKey.days)
    let start = value(SettingKey.startTime).flatMap(TimeOfDay.init)
    let until = value(SettingKey.awakeUntil).flatMap(TimeOfDay.init)
    self.days = Self.letters.map { days?.contains($0) ?? false }
    self.start = start ?? TimeOfDay(hour: 0, minute: 0)
    self.until = until ?? TimeOfDay(hour: 0, minute: 0)
    var shown: Set<String> = []
    if days != nil { shown.insert(SettingKey.days) }
    if start != nil { shown.insert(SettingKey.startTime) }
    if until != nil { shown.insert(SettingKey.awakeUntil) }
    self.shown = shown
  }

  /// The days as `BOSUN_DAYS` takes them.
  public var daysValue: String {
    String(zip(Self.letters, days).filter(\.1).map(\.0))
  }

  /// Why the draft cannot be saved as it is.
  public var problem: String? {
    shown.contains(SettingKey.days) && !days.contains(true) ? "Choose at least one day" : nil
  }

  /// The schedule said back: "bosun starts at 08:00 on weekdays, and lets the Mac sleep after 20:00"; nil while
  /// a part of it cannot be shown, or no day is chosen.
  public var summary: String? {
    guard shown.count == 3, problem == nil else { return nil }
    return "bosun starts at \(start.text) \(daysPhrase), and lets the Mac sleep after \(until.text)"
  }

  private var daysPhrase: String {
    if days.allSatisfy({ $0 }) { return "every day" }
    if days == Self.weekdays { return "on weekdays" }
    if days == Self.weekends { return "on weekends" }
    let names = zip(Self.dayNames, days).filter(\.1).map(\.0)
    guard let last = names.last, names.count > 1 else { return "on " + names.joined() }
    return "on " + names.dropLast().joined(separator: ", ") + " and " + last
  }

  /// What a Save sends: each setting the pane shows whose draft differs from what bosun read (the days compared
  /// in pmset's order, so "FMTWR" read is no change from Monday to Friday).
  public func changes(from view: SettingsView) -> [String: String] {
    let read = ScheduleDraft(view: view)
    return values.filter { key, value in
      shown.contains(key) && read.shown.contains(key) && read.values[key] != value
    }
  }

  private var values: [String: String] {
    [SettingKey.days: daysValue, SettingKey.startTime: start.text, SettingKey.awakeUntil: until.text]
  }

  public func canSave(_ view: SettingsView) -> Bool {
    problem == nil && !changes(from: view).isEmpty
  }
}
