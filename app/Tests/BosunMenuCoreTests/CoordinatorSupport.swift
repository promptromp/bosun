import Foundation
import Testing

@testable import BosunMenuCore

/// The shell's side as the tests have it: what was drawn, asked, opened; answers are queued by button title.
@MainActor
final class FakeUI: AppUI {
  private(set) var rendered: [(model: MenuModel, status: StatusState?)] = []
  private(set) var presented: [DialogSpec] = []
  private(set) var opened: [URL] = []
  private(set) var terminated = 0
  private var answers: [(title: String, boxes: [String: Bool])] = []
  /// Runs while a dialog is open (what happens when a key is pressed or a click comes in meanwhile).
  var whileOpen: (@MainActor (DialogSpec) async -> Void)?

  func answer(_ title: String, boxes: [String: Bool] = [:]) {
    answers.append((title, boxes))
  }

  var lastModel: MenuModel? { rendered.last?.model }
  var lastSpec: DialogSpec? { presented.last }

  func render(_ model: MenuModel, status: StatusState?) {
    rendered.append((model, status))
  }

  func present(_ spec: DialogSpec) async -> DialogAnswer {
    presented.append(spec)
    await whileOpen?(spec)
    guard !answers.isEmpty else {
      Issue.record("no answer queued for the dialog \"\(spec.title)\"")
      return spec.defaultAnswer
    }
    let next = answers.removeFirst()
    guard let resolved = HookAnswer(button: next.title, boxes: next.boxes).resolve(for: spec) else {
      Issue.record("the dialog \"\(spec.title)\" has no button \"\(next.title)\" (\(spec.buttons))")
      return spec.defaultAnswer
    }
    return resolved
  }

  func open(_ url: URL) { opened.append(url) }

  func terminate() { terminated += 1 }
}

/// A coordinator over a fake bosun and a fake shell, with a clock the test moves.
@MainActor
struct World {
  let coordinator: AppCoordinator
  let cli: FakeBosunCLI
  let ui: FakeUI
  let clock: ManualClock
  let scripts: URL

  init(
    status: String? = "status-ok", login: Bool = true, atLogin: Bool = false,
    now: @escaping @Sendable () -> Date = { Date(timeIntervalSince1970: 1_800_000_000) }
  ) async {
    cli = FakeBosunCLI()
    ui = FakeUI()
    clock = ManualClock()
    scripts = FileManager.default.temporaryDirectory
      .appendingPathComponent("bosun-coordinator-test-\(UUID().uuidString)")
    if let status { await cli.reply(Bosun.status.args, exited(0, Fixtures.text(status))) }
    let app = Fixtures.text("app-status").replacingOccurrences(
      of: "\"login\": true", with: "\"login\": \(login)")
    await cli.reply(Bosun.appStatus.args, exited(0, app))
    coordinator = AppCoordinator(
      cli: cli, ui: ui, options: LaunchOptions(atLogin: atLogin, testHooks: nil), clock: clock, now: now,
      script: ScriptSettings(executable: "/Users/me/.local/bin/bosun", folder: scripts))
  }

  func reply(_ command: Command, _ result: CommandResult) async {
    await cli.reply(command.args, result)
  }

  func reply(_ command: Command, fixture: String, code: Int32 = 0) async {
    await cli.reply(command.args, exited(code, Fixtures.text(fixture)))
  }

  /// From now on `command` answers `result` (a reply told with `reply` after calls waits its turn).
  func replace(_ command: Command, _ result: CommandResult) async {
    await cli.replaceReply(command.args, result)
  }

  /// The ids of the items in the last menu drawn, in order, with the submenus' items after their parent.
  var itemIDs: [ItemID] {
    func ids(_ items: [MenuItem]) -> [ItemID] { items.flatMap { [$0.id] + ids($0.children) } }
    return ids((ui.lastModel?.sections ?? []).flatMap { $0 })
  }

  func item(_ id: ItemID) -> MenuItem? {
    func find(_ items: [MenuItem]) -> MenuItem? {
      for item in items {
        if item.id == id { return item }
        if let found = find(item.children) { return found }
      }
      return nil
    }
    return find((ui.lastModel?.sections ?? []).flatMap { $0 })
  }

  /// What bosun was asked to run (waited for), apart from the status and login looks.
  var commands: [[String]] {
    get async {
      await cli.calls.filter { $0 != Bosun.status.args && $0 != Bosun.appStatus.args }
    }
  }
}
