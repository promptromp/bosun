import Foundation

// How the app talks to bosun: one protocol (the AppKit shell implements it over `Process`, tests over
// `FakeBosunCLI`), the commands of part 1 as values, and what each reply means. The app runs bosun only
// through `~/.local/bin/bosun`, only these commands, and never opens a file of bosun's.

/// A `bosun` command: its argv, how long to wait for it, and whether it is started and left running
/// (`bosun update --yes` is: long-running work belongs to bosun's own jobs, never to the app, whose progress is
/// read back from `status`). Start is not: `bosun morning --now --background` hands the work to a launchd job
/// and returns at once, because a process the app starts has the app as its parent, and so does everything that
/// process starts (the tmux server, the agents): the Mac's privacy prompts would name the app, and the grants
/// would follow its signature.
public struct Command: Equatable, Sendable {
  public let args: [String]
  public let timeout: Duration
  public let detached: Bool

  public init(args: [String], timeout: Duration, detached: Bool = false) {
    self.args = args
    self.timeout = timeout
    self.detached = detached
  }
}

public enum CommandOutcome: Equatable, Sendable {
  case exited(Int32)
  /// A detached command was started and left running: it has no reply to read.
  case started
  case timedOut
  /// There is nothing at `~/.local/bin/bosun`.
  case notInstalled
  /// There is something, but it could not be run (a broken link, no permission).
  case couldNotStart(String)
}

public struct CommandResult: Equatable, Sendable {
  public let outcome: CommandOutcome
  public let stdout: String
  public let stderr: String

  public init(outcome: CommandOutcome, stdout: String, stderr: String) {
    self.outcome = outcome
    self.stdout = stdout
    self.stderr = stderr
  }
}

public protocol BosunCLI: Sendable {
  /// Runs `bosun <args>` and waits for it, up to `timeout`.
  func run(_ args: [String], timeout: Duration) async -> CommandResult
  /// Starts `bosun <args>` and leaves it running; returns `.started` once it is, or why it could not be.
  func start(_ args: [String]) async -> CommandResult
}

extension BosunCLI {
  /// Runs a command that is waited for; a detached one is refused (it is to be `start`ed).
  public func run(_ command: Command) async -> CommandResult {
    if command.detached { return Self.misused(command, "is a detached command: start it, do not run it") }
    return await run(command.args, timeout: command.timeout)
  }

  /// Starts a detached command; one that is waited for is refused (it is to be `run`).
  public func start(_ command: Command) async -> CommandResult {
    if !command.detached {
      return Self.misused(command, "is not a detached command: run it, do not start it")
    }
    return await start(command.args)
  }

  private static func misused(_ command: Command, _ what: String) -> CommandResult {
    let why = "bosun \(command.args.joined(separator: " ")) \(what)"
    return CommandResult(outcome: .couldNotStart(why), stdout: "", stderr: "")
  }
}

/// The commands the app runs, with the timeouts of the review focus (status 15 s, check 60 s, plan 30 s).
public enum Bosun {
  public static let status = Command(args: ["status", "--json"], timeout: .seconds(15))
  public static let checkUpdates = Command(args: ["update", "--check", "--json"], timeout: .seconds(60))
  public static let planUpdate = Command(args: ["update", "--dry-run", "--json"], timeout: .seconds(30))
  public static let update = Command(args: ["update", "--yes"], timeout: .seconds(15), detached: true)
  public static let quitPlan = Command(args: ["quit", "--plan", "--json"], timeout: .seconds(30))
  /// Start: the morning run, as a launchd job, started and returned from (a short command, waited for).
  public static let start = Command(args: ["morning", "--now", "--background"], timeout: .seconds(15))
  public static let consoleURL = Command(args: ["console", "--url"], timeout: .seconds(15))
  public static let appStatus = Command(args: ["app", "status", "--json"], timeout: .seconds(15))

  public static func skip(version: String) -> Command {
    Command(args: ["update", "--skip", version], timeout: .seconds(15))
  }

  public static func appLogin(on: Bool) -> Command {
    Command(args: ["app", "login", on ? "on" : "off"], timeout: .seconds(15))
  }

  /// The quit the choices make. Letting agents finish waits for them (bosun's own wait is ten minutes
  /// by default, and a setting): the timeout is longer than any wait the app expects, and an hour is
  /// the most it holds a quit for.
  public static func quit(_ choices: QuitChoices) -> Command {
    let wait: Duration = choices.busy == .letFinish ? .seconds(60 * 60) : .seconds(120)
    return Command(args: quitCommand(choices), timeout: wait)
  }
}

// MARK: what a reply means

public enum FetchFailure: Error, Equatable, Sendable {
  case notInstalled
  case timedOut
  case couldNotStart(String)
  case exited(Int32, String)
  case contract(ContractError)

  public var message: String {
    switch self {
    case .notInstalled: return "bosun is not installed"
    case .timedOut: return "bosun did not answer in time"
    case .couldNotStart(let why): return why
    case .exited(let code, let line):
      return line.isEmpty ? "bosun exited with status \(code) and said nothing" : line
    case .contract(let error): return error.description
    }
  }
}

/// The first line of what a command said on stderr.
func firstLine(_ text: String) -> String {
  text.split(separator: "\n").lazy
    .map { $0.trimmingCharacters(in: .whitespaces) }
    .first { !$0.isEmpty } ?? ""
}

/// A reply read as a `T`: an object printed on a zero exit; with `trustingStdout` also one printed on
/// another exit (`update --dry-run` and `quit` print their object on every path, and say what went
/// wrong in it). A contract number that is not ours is always `contract(.unsupported)`.
func decodeReply<T: Decodable>(
  _ type: T.Type, _ result: CommandResult, contractRequired: Bool = true, trustingStdout: Bool = false
) -> Result<T, FetchFailure> {
  switch result.outcome {
  case .notInstalled: return .failure(.notInstalled)
  case .timedOut: return .failure(.timedOut)
  case .couldNotStart(let why): return .failure(.couldNotStart(why))
  case .started: return .failure(.couldNotStart("bosun was started and left running: it has no reply"))
  case .exited(let code):
    let said = FetchFailure.exited(code, firstLine(result.stderr))
    guard code == 0 || trustingStdout else { return .failure(said) }
    do {
      return .success(
        try decodeContract(type, from: result.stdout, contractRequired: contractRequired))
    } catch {
      return .failure(code == 0 || error.isUnsupported ? .contract(error) : said)
    }
  }
}

/// Runs `command` and reads its reply as a `T`.
public func fetch<T: Decodable & Sendable>(
  _ type: T.Type, _ command: Command, via cli: any BosunCLI, contractRequired: Bool = true,
  trustingStdout: Bool = false
) async -> Result<T, FetchFailure> {
  decodeReply(
    type, await cli.run(command), contractRequired: contractRequired, trustingStdout: trustingStdout)
}

// MARK: the status

/// What the app knows of bosun's state, which is all the menu is drawn from.
public enum StatusState: Equatable, Sendable {
  case loaded(StatusSnapshot, current: Bool)
  case unreachable(String)
  case notInstalled
  case contractTooNew(Int)
}

/// The state a `status --json` reply makes. A hang keeps the last status, "not current" (the menu says
/// so); a missing bosun, a broken link or a reply that is not understood show no stale status at all.
public func statusState(from result: CommandResult, previous: StatusSnapshot?) -> StatusState {
  switch decodeReply(StatusSnapshot.self, result) {
  case .success(let status): return .loaded(status, current: true)
  case .failure(.notInstalled): return .notInstalled
  case .failure(.contract(.unsupported(let number))): return .contractTooNew(number)
  case .failure(.timedOut):
    return previous.map { .loaded($0, current: false) } ?? .unreachable(FetchFailure.timedOut.message)
  case .failure(let failure): return .unreachable(failure.message)
  }
}

public func loadStatus(via cli: any BosunCLI, previous: StatusSnapshot?) async -> StatusState {
  statusState(from: await cli.run(Bosun.status), previous: previous)
}

// MARK: a stand-in for tests

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
