import Foundation

// The settings' part of the JSON contract with bosun's CLI (Contract.swift has the rest and the rules): what
// `bosun settings get --json` and `bosun settings set ... --json` print (docs/commands.md, "Changing settings").

// MARK: bosun settings get --json

/// One setting: what bosun reads for it (null when `bosun.env` cannot be read: unknown, never its default), the
/// default, whether `bosun.env` sets it, whether bosun can use the value, and what it means.
public struct SettingEntry: Decodable, Equatable, Sendable {
  public let value: String?
  public let defaultValue: String
  public let set: Bool
  public let valid: Bool
  public let meaning: String

  private enum CodingKeys: String, CodingKey {
    case value, set, valid, meaning
    case defaultValue = "default"
  }

  public init(value: String?, defaultValue: String, set: Bool, valid: Bool, meaning: String) {
    self.value = value
    self.defaultValue = defaultValue
    self.set = set
    self.valid = valid
    self.meaning = meaning
  }
}

/// What `settings get --json` prints: whether `bosun.env` could be read (and why not), its folder, whose the
/// Mac's repeating wake is (`ours`, `none` or `other`), and each setting by its key (`BOSUN_START_TIME`).
public struct SettingsView: Decodable, Equatable, Sendable {
  public let contract: Int
  public let readable: Bool
  public let error: String?
  public let configDir: String
  public let wake: String
  public let settings: [String: SettingEntry]
}

// MARK: bosun settings set ... --json

/// What `settings set ... --json` prints: what changed (`[before, after]` per key) and notes, or on a refusal or a
/// failed step only `error`.
public struct SettingsSetReply: Decodable, Equatable, Sendable {
  public let contract: Int
  public let changed: [String: [String?]]?
  public let notes: [String]?
  public let error: String?
}
