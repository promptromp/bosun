import BosunMenuCore

// What the tests share and the app never ships: a target only the test targets depend on (the app's executable
// does not, so none of this is in bosun.app).

/// A `BosunCLI` that answers what it was told to and records what it was asked. A reply told twice for
/// one command is given in turn, the last one staying; a command it was not told about fails loudly.
public actor FakeBosunCLI: BosunCLI {
  private var replies: [[String]: [CommandResult]] = [:]
  public private(set) var calls: [[String]] = []
  public private(set) var timeouts: [Duration] = []
  /// What was `start`ed (detached), apart from what was `run`.
  public private(set) var started: [[String]] = []

  public init() {}

  public func reply(_ args: [String], _ result: CommandResult) {
    replies[args, default: []].append(result)
  }

  /// From now on `args` answers `result` alone, whatever was told for it before.
  public func replaceReply(_ args: [String], _ result: CommandResult) {
    replies[args] = [result]
  }

  public func run(_ args: [String], timeout: Duration) async -> CommandResult {
    calls.append(args)
    timeouts.append(timeout)
    guard let queue = replies[args], let first = queue.first else {
      let said = "FakeBosunCLI: no reply for " + args.joined(separator: " ")
      return CommandResult(outcome: .exited(1), stdout: "", stderr: said)
    }
    if queue.count > 1 { replies[args] = Array(queue.dropFirst()) }
    return first
  }

  /// A command started and left running is `.started`, unless it was told otherwise.
  public func start(_ args: [String]) async -> CommandResult {
    started.append(args)
    return replies[args]?.first ?? CommandResult(outcome: .started, stdout: "", stderr: "")
  }
}
