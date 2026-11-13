import BosunMenuCore
import Foundation

// Runs `bosun` for real: a `BosunCLI` over `Process`. Foundation only, so it is tested (against shell scripts
// that stand in for bosun) apart from AppKit. Every call waits at most its timeout; what hung is stopped
// with its whole process group, TERM then KILL, so the git and tmux a bosun script started go too. Output is
// read as it comes, so a reply bigger than a pipe cannot hang the command, and a helper the command left
// running (holding the pipe open) cannot hold the reply. Nothing waits by polling: the end of the command
// and of each pipe are signals.

public struct ProcessBosunCLI: BosunCLI {
  public let executable: String
  public let environment: [String: String]

  /// How long, after the command ended, a pipe may stay open (a helper left running) before its reply is
  /// taken as it is.
  static let drainGrace: Duration = .seconds(1)
  /// How long a command that was told to stop (TERM) has to do so before it is killed.
  static let killGrace: Duration = .seconds(1)

  public init(executable: String, environment: [String: String] = ProcessInfo.processInfo.environment) {
    self.executable = executable
    self.environment = environment
  }

  /// The app talks to bosun only through the install link, which `bosun update` points at the current version.
  public static func installLink(home: String = NSHomeDirectory()) -> String {
    home + "/.local/bin/bosun"
  }

  /// A Mac app's PATH is bare, as launchd's is: put bosun's tool folders (where bosun's own scripts put
  /// them, lib/common.sh) in front, so tmux and git are found.
  public static func environment(base: [String: String], home: String) -> [String: String] {
    var environment = base
    let system = base["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
    environment["PATH"] = ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", system]
      .joined(separator: ":")
    return environment
  }

  public static func standard(
    home: String = NSHomeDirectory(), environment base: [String: String] = ProcessInfo.processInfo.environment
  ) -> ProcessBosunCLI {
    ProcessBosunCLI(executable: installLink(home: home), environment: environment(base: base, home: home))
  }

  // MARK: BosunCLI

  public func run(_ args: [String], timeout: Duration) async -> CommandResult {
    if let refusal = launchRefusal() { return refusal }
    let process = makeProcess(args)
    let output = OutputCollector()
    let errors = OutputCollector()
    let exited = Signal()
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = output.pipe
    process.standardError = errors.pipe
    process.terminationHandler = { _ in exited.fire() }
    do {
      try process.run()
    } catch {
      output.release()
      errors.release()
      return Self.couldNotStart("\(executable) could not be run: \(error.localizedDescription)")
    }
    guard await exited.wait(timeout: timeout) else {
      await Self.stop(process, exited: exited)
      output.release()
      errors.release()
      return CommandResult(outcome: .timedOut, stdout: "", stderr: "")
    }
    await Self.drain(output, errors)
    output.release()
    errors.release()
    return CommandResult(
      outcome: .exited(Self.exitCode(of: process)), stdout: output.text, stderr: errors.text)
  }

  /// Started and left running: no input, its output goes nowhere. The child outlives the app if need be.
  /// (The app does not use this for Start: a child of the app is a child for the Mac's privacy rules.)
  public func start(_ args: [String]) async -> CommandResult {
    if let refusal = launchRefusal() { return refusal }
    let process = makeProcess(args)
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
      try process.run()
    } catch {
      return Self.couldNotStart("\(executable) could not be run: \(error.localizedDescription)")
    }
    return CommandResult(outcome: .started, stdout: "", stderr: "")
  }

  // MARK: pieces

  /// Nothing at the link: bosun is not installed. Something there that cannot be run (a link into a folder
  /// an update cut short removed): bosun is there but cannot be reached.
  private func launchRefusal() -> CommandResult? {
    guard (try? FileManager.default.attributesOfItem(atPath: executable)) != nil else {
      return CommandResult(outcome: .notInstalled, stdout: "", stderr: "")
    }
    guard FileManager.default.isExecutableFile(atPath: executable) else {
      return Self.couldNotStart("\(executable) cannot be run (a broken link, or not executable)")
    }
    return nil
  }

  private static func couldNotStart(_ why: String) -> CommandResult {
    CommandResult(outcome: .couldNotStart(why), stdout: "", stderr: "")
  }

  private func makeProcess(_ args: [String]) -> Process {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = args
    process.environment = environment
    process.currentDirectoryURL = URL(fileURLWithPath: environment["HOME"] ?? NSHomeDirectory())
    return process
  }

  /// TERM, and KILL if it does not go. A cancelled caller does not cut it short: stopping must finish.
  private static func stop(_ process: Process, exited: Signal) async {
    send(SIGTERM, to: process)
    if await !waitUnstoppable(exited) {
      send(SIGKILL, to: process)
      _ = await waitUnstoppable(exited)
    }
  }

  /// The wait for the end of a command that was told to stop, in a task of its own, which cancelling the
  /// caller does not cancel.
  private static func waitUnstoppable(_ exited: Signal) async -> Bool {
    let grace = killGrace
    return await Task { await exited.wait(timeout: grace) }.value
  }

  /// To the child's process group when it leads one (so the subprocesses of a bosun script go with it), else
  /// to the child alone: never to the app's own group.
  private static func send(_ signal: Int32, to process: Process) {
    let pid = process.processIdentifier
    kill(getpgid(pid) == pid ? -pid : pid, signal)
  }

  /// Waits for both pipes to reach their end, for at most the grace.
  private static func drain(_ first: OutputCollector, _ second: OutputCollector) async {
    let end = ContinuousClock.now.advanced(by: drainGrace)
    _ = await first.ended.wait(timeout: drainGrace)
    _ = await second.ended.wait(timeout: max(ContinuousClock.now.duration(to: end), .zero))
  }

  /// A command ended by a signal exits as the shell reports it: 128 plus the signal.
  private static func exitCode(of process: Process) -> Int32 {
    process.terminationReason == .uncaughtSignal ? 128 + process.terminationStatus : process.terminationStatus
  }
}

// MARK: waiting without polling

/// One answer, given once, to one waiter: whoever resolves it first wins.
private final class Race: @unchecked Sendable {
  private let lock = NSLock()
  private var value: Bool?
  private var waiter: CheckedContinuation<Bool, Never>?

  func resolve(_ answer: Bool) {
    let waiting = lock.withLock { () -> CheckedContinuation<Bool, Never>? in
      guard value == nil else { return nil }
      value = answer
      defer { waiter = nil }
      return waiter
    }
    waiting?.resume(returning: answer)
  }

  func result() async -> Bool {
    await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
      lock.lock()
      if let value {
        lock.unlock()
        continuation.resume(returning: value)
      } else {
        waiter = continuation
        lock.unlock()
      }
    }
  }
}

/// Something that happens once (a command ends, a pipe is read to its end): `wait` says whether it did,
/// within a time. A cancelled waiter is told no.
final class Signal: @unchecked Sendable {
  private let lock = NSLock()
  private var happened = false
  private var listeners: [UUID: @Sendable () -> Void] = [:]

  func fire() {
    let calls = lock.withLock { () -> [UUID: @Sendable () -> Void] in
      happened = true
      defer { listeners = [:] }
      return listeners
    }
    for call in calls.values { call() }
  }

  func wait(timeout: Duration) async -> Bool {
    let race = Race()
    let id = UUID()
    let already = lock.withLock { () -> Bool in
      if !happened { listeners[id] = { race.resolve(true) } }
      return happened
    }
    if already { return true }
    let timer = Task {
      try? await Task.sleep(for: timeout)
      race.resolve(false)
    }
    let answer = await withTaskCancellationHandler {
      await race.result()
    } onCancel: {
      race.resolve(false)
    }
    timer.cancel()
    lock.withLock { listeners[id] = nil }
    return answer
  }
}

/// Reads one of a command's pipes as it fills. The read end is not closed by hand (a read in flight would be
/// reading a closed file): the handler is taken off and the pipe closes when it is let go.
private final class OutputCollector: @unchecked Sendable {
  let pipe = Pipe()
  let ended = Signal()
  private let lock = NSLock()
  private var data = Data()

  init() {
    pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
      self?.receive(handle.availableData)
    }
  }

  private func receive(_ chunk: Data) {
    guard chunk.isEmpty else {
      lock.withLock { data.append(chunk) }
      return
    }
    pipe.fileHandleForReading.readabilityHandler = nil
    ended.fire()
  }

  /// What was said; bytes that are not UTF-8 become the replacement character, so a reply is never lost to them.
  var text: String {
    // swiftlint:disable:next optional_data_string_conversion
    lock.withLock { String(decoding: data, as: UTF8.self) }
  }

  func release() {
    pipe.fileHandleForReading.readabilityHandler = nil
  }
}
