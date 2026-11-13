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

/// Writes no files: remembers what it was asked to write, and says where it would have.
final class FakeScripts: ScriptWriting, @unchecked Sendable {
  private let lock = NSLock()
  private var asked: [[String]] = []
  private var failing = false

  var written: [[String]] { lock.withLock { asked } }

  var fails: Bool {
    get { lock.withLock { failing } }
    set { lock.withLock { failing = newValue } }
  }

  struct Refused: Error, LocalizedError {
    var errorDescription: String? { "the folder cannot be written" }
  }

  func write(args: [String]) throws -> URL {
    if fails { throw Refused() }
    lock.withLock { asked.append(args) }
    return URL(fileURLWithPath: "/tmp/bosun-app/bosun-\(args[0]).command")
  }
}

/// A door a test holds shut: what waits at it goes on when the test opens it.
actor Gate {
  private var isOpen = false
  private var waiting: [CheckedContinuation<Void, Never>] = []
  private var arrivals: [UUID: CheckedContinuation<Bool, Never>] = [:]

  func wait() async {
    if isOpen { return }
    await withCheckedContinuation { continuation in
      waiting.append(continuation)
      let told = arrivals
      arrivals = [:]
      for (_, arrival) in told { arrival.resume(returning: true) }
    }
  }

  /// Returns once something waits at the gate, shut (at once if it already does): the signal a test awaits
  /// instead of sleeping and hoping the look got there. False if nothing came within `limit`, so a test
  /// that never gets there fails instead of hanging.
  func waitForWaiter(within limit: Duration = .seconds(10)) async -> Bool {
    if !waiting.isEmpty { return true }
    let id = UUID()
    Task {
      try? await Task.sleep(for: limit)
      self.giveUpWaiting(id)
    }
    return await withCheckedContinuation { arrivals[id] = $0 }
  }

  private func giveUpWaiting(_ id: UUID) {
    arrivals.removeValue(forKey: id)?.resume(returning: false)
  }

  /// Shuts it again, for the next command to wait at.
  func arm() {
    isOpen = false
  }

  func open() {
    isOpen = true
    for continuation in waiting { continuation.resume() }
    waiting = []
  }
}

/// A bosun some of whose commands wait at a gate before they are answered (a command that hangs).
struct GatedCLI: BosunCLI {
  let inner: FakeBosunCLI
  let gates: [[String]: Gate]

  func run(_ args: [String], timeout: Duration) async -> CommandResult {
    if let gate = gates[args] { await gate.wait() }
    return await inner.run(args, timeout: timeout)
  }

  func start(_ args: [String]) async -> CommandResult {
    await inner.start(args)
  }
}

/// A coordinator over a fake bosun and a fake shell, with a clock the test moves.
@MainActor
struct World {
  let coordinator: AppCoordinator
  let cli: FakeBosunCLI
  let ui: FakeUI
  let clock: ManualClock
  let scripts: FakeScripts

  init(
    status: String? = "status-ok", login: Bool = true, atLogin: Bool = false,
    now: @escaping @Sendable () -> Date = { Date(timeIntervalSince1970: 1_800_000_000) },
    gates: [[String]: Gate] = [:]
  ) async {
    cli = FakeBosunCLI()
    ui = FakeUI()
    clock = ManualClock()
    scripts = FakeScripts()
    if let status { await cli.reply(Bosun.status.args, exited(0, Fixtures.text(status))) }
    let app = Fixtures.text("app-status").replacingOccurrences(
      of: "\"login\": true", with: "\"login\": \(login)")
    await cli.reply(Bosun.appStatus.args, exited(0, app))
    await cli.reply(Bosun.start.args, exited(0))
    coordinator = AppCoordinator(
      cli: GatedCLI(inner: cli, gates: gates), ui: ui,
      options: LaunchOptions(atLogin: atLogin, testHooks: nil), clock: clock, now: now,
      scripts: scripts)
  }

  /// A look at the status, and then the work it set going outside the look (an update check).
  func refresh(_ reason: RefreshReason) async {
    await coordinator.refresh(reason)
    await coordinator.settle()
  }

  func menuWillOpen() async {
    await coordinator.menuWillOpen()
    await coordinator.settle()
  }

  func didWake() async {
    await coordinator.didWake()
    await coordinator.settle()
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

  /// How many times Start was run.
  var starts: Int {
    get async { await cli.calls.filter { $0 == Bosun.start.args }.count }
  }

  /// What bosun was asked to run (waited for), apart from the status and login looks.
  var commands: [[String]] {
    get async {
      await cli.calls.filter { $0 != Bosun.status.args && $0 != Bosun.appStatus.args }
    }
  }
}
