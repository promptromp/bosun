import Foundation
import Testing

@testable import BosunMenuCore

/// The JSON that `tools/contract_samples.py` writes from bosun's own code (Fixtures/*.json). Each read throws:
/// a fixture that is missing or does not decode fails the test that reads it, not the whole run.
enum Fixtures {
  struct Missing: Error, CustomStringConvertible {
    let name: String
    var description: String {
      "no fixture \(name).json: write them with `uv run python tools/contract_samples.py`"
    }
  }

  static func text(_ name: String) throws -> String {
    guard let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
    else {
      throw Missing(name: name)
    }
    return try String(contentsOf: url, encoding: .utf8)
  }

  static func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
    try decodeContract(type, from: text(name))
  }

  /// A fixture that is a plain list of strings (it has no contract number to check).
  static func strings(_ name: String) throws -> [String] {
    try JSONDecoder().decode([String].self, from: Data(text(name).utf8))
  }

  static func status(_ name: String) throws -> StatusSnapshot {
    try decode(StatusSnapshot.self, name)
  }

  static func dryRun(_ name: String) throws -> UpdateDryRun {
    try decodeContract(UpdateDryRun.self, from: text(name))
  }

  static func plan(_ name: String = "update-plan") throws -> UpdatePlan {
    try #require(dryRun(name).plan)
  }

  static func quitResult(_ name: String) throws -> QuitResult {
    try decode(QuitResult.self, name)
  }

  /// A status as a bosun from before `attention` printed it: the fixture without that field.
  static func withoutAttention(_ name: String) throws -> String {
    var object = try #require(
      try JSONSerialization.jsonObject(with: Data(text(name).utf8)) as? [String: Any])
    object.removeValue(forKey: "attention")
    return try #require(String(bytes: JSONSerialization.data(withJSONObject: object), encoding: .utf8))
  }

  /// The text of a fixture with `"contract": 1` read as another number.
  static func text(_ name: String, contract: Int) throws -> String {
    try text(name).replacingOccurrences(of: "\"contract\": 1", with: "\"contract\": \(contract)")
  }

  /// What a command sample's run returned (`exits.json`, written beside the samples): 0 for one not listed.
  static func exitCode(_ name: String) throws -> Int32 {
    let codes = try JSONDecoder().decode([String: Int32].self, from: Data(text("exits").utf8))
    return codes[name] ?? 0
  }

  /// A command sample as bosun gave it: its object on stdout, with the exit status its run returned.
  static func reply(_ name: String) throws -> CommandResult {
    exited(try exitCode(name), try text(name))
  }
}

/// A window listing of a command that ran: `exitCode` and what it printed.
func exited(_ code: Int32, _ out: String = "", err: String = "") -> CommandResult {
  CommandResult(outcome: .exited(code), stdout: out, stderr: err)
}

func quitPlan(
  windows: [String] = ["acme-orchestrator", "acme-api"],
  busy: [String] = [],
  waiting: [String] = [],
  keepAwake: String? = nil,
  refused: String? = nil,
  waitMinutes: Int? = 10,
  lockWaitSeconds: Int? = 180,
  stepsSeconds: Int? = 225
) -> QuitPlan {
  QuitPlan(
    contract: 1, windows: windows, busy: busy, waiting: waiting, keepAwakeUntil: keepAwake,
    refused: refused, note: nil, waitMinutes: waitMinutes, lockWaitSeconds: lockWaitSeconds,
    stepsSeconds: stepsSeconds)
}
