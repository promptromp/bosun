import Foundation

@testable import BosunMenuCore

/// The JSON that `tools/contract_samples.py` writes from bosun's own code (Fixtures/*.json).
enum Fixtures {
  static func text(_ name: String) -> String {
    let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")!
    return try! String(contentsOf: url, encoding: .utf8)
  }

  static func decode<T: Decodable>(_ type: T.Type, _ name: String) -> T {
    try! decodeContract(type, from: text(name))
  }

  /// A fixture that is a plain list of strings (it has no contract number to check).
  static func strings(_ name: String) -> [String] {
    try! JSONDecoder().decode([String].self, from: Data(text(name).utf8))
  }

  static func status(_ name: String) -> StatusSnapshot {
    decode(StatusSnapshot.self, name)
  }

  static func dryRun(_ name: String) -> UpdateDryRun {
    try! decodeContract(UpdateDryRun.self, from: text(name))
  }

  static func plan(_ name: String = "update-plan") -> UpdatePlan {
    dryRun(name).plan!
  }

  static func quitResult(_ name: String) -> QuitResult {
    decode(QuitResult.self, name)
  }

  /// The text of a fixture with `"contract": 1` read as another number.
  static func text(_ name: String, contract: Int) -> String {
    text(name).replacingOccurrences(of: "\"contract\": 1", with: "\"contract\": \(contract)")
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
  lockWaitSeconds: Int? = 180
) -> QuitPlan {
  QuitPlan(
    contract: 1, windows: windows, busy: busy, waiting: waiting, keepAwakeUntil: keepAwake,
    refused: refused, note: nil, waitMinutes: waitMinutes, lockWaitSeconds: lockWaitSeconds)
}
