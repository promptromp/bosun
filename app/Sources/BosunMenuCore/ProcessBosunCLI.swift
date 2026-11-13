import Foundation

// Runs `bosun` for real: a `BosunCLI` over `Process`. Foundation only, so it is tested here (against shell
// scripts that stand in for bosun) and not in the AppKit shell. Every call waits at most its timeout; what
// hung is terminated, then killed. Output is read as it comes, so a reply bigger than a pipe cannot hang
// the command, and a helper the command left running (holding the pipe open) cannot hold the reply.

public struct ProcessBosunCLI: BosunCLI {
  public let executable: String
  public let environment: [String: String]

  /// How long, after the command ended, a pipe may stay open (a helper left running) before its reply is
  /// taken as it is.
  static let drainGrace: Duration = .seconds(1)
  /// How long a command that was told to stop (SIGTERM) has to do so before it is killed.
  static let killGrace: Duration = .seconds(1)
  private static let pollEvery: Duration = .milliseconds(10)

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
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = output.pipe
    process.standardError = errors.pipe
    do {
      try process.run()
    } catch {
      output.close()
      errors.close()
      return Self.couldNotStart("\(executable) could not be run: \(error.localizedDescription)")
    }
    let deadline = ContinuousClock.now.advanced(by: timeout)
    guard await Self.wait(for: process, until: deadline) else {
      await Self.stop(process)
      output.close()
      errors.close()
      return CommandResult(outcome: .timedOut, stdout: "", stderr: "")
    }
    await Self.drain(output, errors)
    return CommandResult(
      outcome: .exited(Self.exitCode(of: process)), stdout: output.text, stderr: errors.text)
  }

  /// Started and left running: no input, its output goes nowhere. The child outlives the app if need be.
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

  /// True when the process ended before the deadline; false at the deadline, or when the caller was cancelled.
  private static func wait(for process: Process, until deadline: ContinuousClock.Instant) async -> Bool {
    while process.isRunning {
      if ContinuousClock.now >= deadline { return false }
      do {
        try await Task.sleep(for: pollEvery)
      } catch {
        return false
      }
    }
    return true
  }

  /// SIGTERM, and SIGKILL if it does not go.
  private static func stop(_ process: Process) async {
    process.terminate()
    let grace = ContinuousClock.now.advanced(by: killGrace)
    if await !waitIgnoringCancellation(for: process, until: grace) {
      kill(process.processIdentifier, SIGKILL)
      _ = await waitIgnoringCancellation(for: process, until: ContinuousClock.now.advanced(by: killGrace))
    }
  }

  /// `wait`, but a cancelled caller does not cut it short: stopping what was started must finish.
  private static func waitIgnoringCancellation(
    for process: Process, until deadline: ContinuousClock.Instant
  ) async -> Bool {
    while process.isRunning {
      if ContinuousClock.now >= deadline { return false }
      await nap()
    }
    return true
  }

  /// Waits for both pipes to reach their end, for at most the grace.
  private static func drain(_ first: OutputCollector, _ second: OutputCollector) async {
    let end = ContinuousClock.now.advanced(by: drainGrace)
    while !(first.isDone && second.isDone) && ContinuousClock.now < end {
      await nap()
    }
    first.close()
    second.close()
  }

  /// A short pause that a cancelled caller does not cut short (an unstructured task is not cancelled with it),
  /// so a loop that waits on one does not spin.
  private static func nap() async {
    await Task { try? await Task.sleep(for: pollEvery) }.value
  }

  /// A command ended by a signal exits as the shell reports it: 128 plus the signal.
  private static func exitCode(of process: Process) -> Int32 {
    process.terminationReason == .uncaughtSignal ? 128 + process.terminationStatus : process.terminationStatus
  }
}

/// Reads one of a command's pipes as it fills.
private final class OutputCollector: @unchecked Sendable {
  let pipe = Pipe()
  private let lock = NSLock()
  private var data = Data()
  private var finished = false

  init() {
    pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
      self?.receive(handle.availableData)
    }
  }

  private func receive(_ chunk: Data) {
    let ended = lock.withLock { () -> Bool in
      if chunk.isEmpty {
        finished = true
      } else {
        data.append(chunk)
      }
      return finished
    }
    if ended { pipe.fileHandleForReading.readabilityHandler = nil }
  }

  var isDone: Bool { lock.withLock { finished } }

  /// What was said; bytes that are not UTF-8 become the replacement character, so a reply is never lost to them.
  var text: String {
    // swiftlint:disable:next optional_data_string_conversion
    lock.withLock { String(decoding: data, as: UTF8.self) }
  }

  func close() {
    pipe.fileHandleForReading.readabilityHandler = nil
    try? pipe.fileHandleForReading.close()
  }
}
