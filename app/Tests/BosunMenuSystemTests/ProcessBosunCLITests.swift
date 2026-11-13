import BosunMenuCore
import Foundation
import Testing

@testable import BosunMenuSystem

/// A folder of its own (the process tests run real scripts, never bosun).
private func scratchFolder() throws -> URL {
  let dir = FileManager.default.temporaryDirectory
    .appendingPathComponent("bosun-cli-test-\(UUID().uuidString)")
  try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
  return dir
}

/// A fake `bosun`: a shell script with `body`, executable, in `folder`.
private func fakeBosun(_ body: String, in folder: URL, mode: Int = 0o755) throws -> String {
  let url = folder.appendingPathComponent("bosun")
  try ("#!/bin/sh\n" + body + "\n").write(to: url, atomically: true, encoding: .utf8)
  try FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: url.path)
  return url.path
}

private func processExists(_ pid: Int32) -> Bool {
  kill(pid, 0) == 0
}

/// The pid a script wrote, or nil while the file is missing or not yet whole (the shell creates it before it
/// writes into it).
private func pid(inFile path: String) -> Int32? {
  guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return nil }
  return Int32(text.trimmingCharacters(in: .whitespacesAndNewlines))
}

/// A condition that load may delay: polled for up to `seconds` (generous: it costs nothing when met).
private func waitUntil(seconds: Double = 10, _ done: () -> Bool) async -> Bool {
  let end = Date().addingTimeInterval(seconds)
  while Date() < end {
    if done() { return true }
    try? await Task.sleep(for: .milliseconds(20))
  }
  return done()
}

/// Runs `executable` with a timeout far longer than the test, waits until the script has written its pid
/// (a bounded poll), then cancels the run, which stops what hung as a timeout does (`stop`). Cancelling after
/// the file exists, rather than racing a short timeout against the shell's start, cannot fire too early
/// under load. Nil, with an issue recorded, if the script never wrote it.
private func stoppedAfterPid(
  of path: String, in pidFile: String, sourceLocation: SourceLocation = #_sourceLocation
) async -> (result: CommandResult, pid: Int32)? {
  let task = Task { await ProcessBosunCLI(executable: path).run([], timeout: .seconds(300)) }
  var written: Int32?
  _ = await waitUntil {
    written = pid(inFile: pidFile)
    return written != nil
  }
  task.cancel()
  let result = await task.value
  guard let written else {
    Issue.record("the script never wrote \(pidFile)", sourceLocation: sourceLocation)
    return nil
  }
  return (result, written)
}

@Suite struct ProcessRunTests {
  @Test func repliesWithStdoutStderrAndTheExitCode() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("echo '{\"a\": 1}'; echo oops >&2; exit 3", in: folder)
    let result = await ProcessBosunCLI(executable: path).run(["status"], timeout: .seconds(10))
    #expect(result.outcome == .exited(3))
    #expect(result.stdout == "{\"a\": 1}\n")
    #expect(result.stderr == "oops\n")
  }

  @Test func passesTheArgumentsAsTheyAre() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("printf '%s|' \"$@\"", in: folder)
    let result = await ProcessBosunCLI(executable: path).run(
      ["quit", "--let-finish", "a b"], timeout: .seconds(10))
    #expect(result.stdout == "quit|--let-finish|a b|")
  }

  @Test func readsWhatTheEnvironmentGave() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("printf '%s' \"$BOSUN_TEST_FOO\"", in: folder)
    let cli = ProcessBosunCLI(executable: path, environment: ["BOSUN_TEST_FOO": "bar"])
    let result = await cli.run([], timeout: .seconds(10))
    #expect(result.stdout == "bar")
  }

  /// Review focus 2: a `bosun` command that hangs has a timeout, after which the reply is `timedOut`. The
  /// timer is the real one (never early; the upper bound only says it did fire, and is far looser than
  /// load can stretch a one-second timer).
  @Test func slowCommandTimesOut() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("exec sleep 60", in: folder)
    let started = ContinuousClock.now
    let result = await ProcessBosunCLI(executable: path).run(["status"], timeout: .seconds(1))
    let took = ContinuousClock.now - started
    #expect(result.outcome == .timedOut)
    #expect(took >= .seconds(1))
    #expect(took < .seconds(50))  // the script sleeps 60: it was not waited for
  }

  /// And what hung is stopped: the script's own pid is gone (stopped after its pid file exists, see
  /// `stoppedAfterPid`).
  @Test func aHungCommandIsStopped() async throws {
    let folder = try scratchFolder()
    let pidFile = folder.appendingPathComponent("pid").path
    let path = try fakeBosun("echo $$ > '\(pidFile)'\nexec sleep 60", in: folder)
    guard let (result, stopped) = await stoppedAfterPid(of: path, in: pidFile) else { return }
    #expect(result.outcome == .timedOut)
    #expect(await waitUntil { !processExists(stopped) })
  }

  /// TERM is ignored, so only the KILL after the grace ends it; `run` returns once it is gone.
  @Test func aCommandThatIgnoresTerminateIsKilled() async throws {
    let folder = try scratchFolder()
    let pidFile = folder.appendingPathComponent("pid").path
    let path = try fakeBosun(
      "trap '' TERM\necho $$ > '\(pidFile)'\nwhile true; do sleep 1; done", in: folder)
    guard let (result, stopped) = await stoppedAfterPid(of: path, in: pidFile) else { return }
    #expect(result.outcome == .timedOut)
    #expect(await waitUntil { !processExists(stopped) })
  }

  /// The child leads its own process group: what a timeout signals, so a script's own children go with it.
  @Test func theCommandLeadsItsOwnProcessGroup() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("echo \"$$ $(ps -o pgid= -p $$)\"", in: folder)
    let result = await ProcessBosunCLI(executable: path).run([], timeout: .seconds(10))
    let words = result.stdout.split(separator: " ").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
    #expect(words.count == 2)
    #expect(words.first == words.last)
    #expect(words.first != String(getpgrp()))
  }

  @Test func aTimeoutStopsWhatTheCommandStartedToo() async throws {
    let folder = try scratchFolder()
    let childFile = folder.appendingPathComponent("child").path
    let path = try fakeBosun("sleep 60 &\necho $! > '\(childFile)'\nwait", in: folder)
    guard let (result, child) = await stoppedAfterPid(of: path, in: childFile) else { return }
    #expect(result.outcome == .timedOut)
    #expect(await waitUntil { !processExists(child) })
  }

  /// Many commands at once, each read to its end and let go: nothing reads a pipe that was closed.
  @Test func manyCommandsAtOnceAllAnswer() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("echo hello; echo there >&2", in: folder)
    let cli = ProcessBosunCLI(executable: path)
    let results = await withTaskGroup(of: CommandResult.self) { group in
      for _ in 0..<40 { group.addTask { await cli.run([], timeout: .seconds(20)) } }
      var all: [CommandResult] = []
      for await result in group { all.append(result) }
      return all
    }
    #expect(results.count == 40)
    #expect(
      results.allSatisfy { $0.outcome == .exited(0) && $0.stdout == "hello\n" && $0.stderr == "there\n" })
  }

  @Test func aCancelledRunIsATimeout() async throws {
    let folder = try scratchFolder()
    let pidFile = folder.appendingPathComponent("pid").path
    let path = try fakeBosun("echo $$ > '\(pidFile)'\nexec sleep 60", in: folder)
    let task = Task { await ProcessBosunCLI(executable: path).run([], timeout: .seconds(120)) }
    _ = await waitUntil { pid(inFile: pidFile) != nil }
    task.cancel()
    let result = await task.value
    #expect(result.outcome == .timedOut)
  }

  @Test func bigOutputDoesNotFillThePipeAndHang() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("head -c 400000 /dev/zero | tr '\\0' x", in: folder)
    let result = await ProcessBosunCLI(executable: path).run([], timeout: .seconds(20))
    #expect(result.outcome == .exited(0))
    #expect(result.stdout.count == 400_000)
  }

  /// A helper the command left running holds the pipe open; the reply is what was said by then. Said by
  /// behaviour, not by the clock: the reply is the command's own exit (not a timeout) although the helper
  /// would run for half a minute, and the helper is still there when it arrives.
  @Test func aHelperLeftRunningDoesNotHoldTheReply() async throws {
    let folder = try scratchFolder()
    let helperFile = folder.appendingPathComponent("helper").path
    let path = try fakeBosun("sleep 30 &\necho $! > '\(helperFile)'\necho done", in: folder)
    let result = await ProcessBosunCLI(executable: path).run([], timeout: .seconds(20))
    #expect(result.outcome == .exited(0))
    #expect(result.stdout == "done\n")
    let helper = try #require(pid(inFile: helperFile))
    #expect(processExists(helper))
    kill(helper, SIGKILL)
  }

  @Test func aCommandKilledBySignalExitsAs128PlusIt() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("kill -9 $$", in: folder)
    let result = await ProcessBosunCLI(executable: path).run([], timeout: .seconds(10))
    #expect(result.outcome == .exited(137))
  }

  @Test func nothingAtTheLinkIsNotInstalled() async throws {
    let folder = try scratchFolder()
    let cli = ProcessBosunCLI(executable: folder.appendingPathComponent("bosun").path)
    #expect(await cli.run(["status"], timeout: .seconds(5)).outcome == .notInstalled)
    #expect(await cli.start(["morning", "--now"]).outcome == .notInstalled)
  }

  /// An update cut short leaves a link to a folder that is gone: bosun is there but cannot be reached.
  @Test func aBrokenLinkCannotBeRun() async throws {
    let folder = try scratchFolder()
    let link = folder.appendingPathComponent("bosun")
    try FileManager.default.createSymbolicLink(
      atPath: link.path, withDestinationPath: folder.appendingPathComponent("gone/bosun").path)
    let cli = ProcessBosunCLI(executable: link.path)
    for result in [await cli.run([], timeout: .seconds(5)), await cli.start([])] {
      guard case .couldNotStart(let why) = result.outcome else {
        Issue.record("expected couldNotStart, got \(result.outcome)")
        continue
      }
      #expect(why.contains(link.path))
    }
  }

  @Test func aFileThatIsNotExecutableCannotBeRun() async throws {
    let folder = try scratchFolder()
    let path = try fakeBosun("echo hi", in: folder, mode: 0o644)
    let result = await ProcessBosunCLI(executable: path).run([], timeout: .seconds(5))
    guard case .couldNotStart = result.outcome else {
      Issue.record("expected couldNotStart, got \(result.outcome)")
      return
    }
  }
}

@Suite struct ProcessStartTests {
  /// Returns at once, by behaviour: the script waits for a gate the test opens only after `start` came
  /// back, so `start` cannot have waited for it, and it goes on running to leave its mark.
  @Test func startLeavesItRunningAndReturnsAtOnce() async throws {
    let folder = try scratchFolder()
    let mark = folder.appendingPathComponent("mark").path
    let gate = folder.appendingPathComponent("gate").path
    let path = try fakeBosun(
      "while [ ! -e '\(gate)' ]; do sleep 0.05; done\ntouch '\(mark)'", in: folder)
    let result = await ProcessBosunCLI(executable: path).start(["morning", "--now"])
    #expect(result.outcome == .started)
    #expect(!FileManager.default.fileExists(atPath: mark))
    #expect(FileManager.default.createFile(atPath: gate, contents: nil))
    #expect(await waitUntil { FileManager.default.fileExists(atPath: mark) })
  }

  @Test func aStartedCommandIsGivenNoInput() async throws {
    let folder = try scratchFolder()
    let mark = folder.appendingPathComponent("mark").path
    let path = try fakeBosun(
      "if read -r line; then echo got > '\(mark)'; else echo eof > '\(mark)'; fi", in: folder)
    _ = await ProcessBosunCLI(executable: path).start([])
    #expect(await waitUntil { FileManager.default.fileExists(atPath: mark) })
    let said = try String(contentsOfFile: mark, encoding: .utf8)
    #expect(said == "eof\n")
  }
}

@Suite struct InstallLinkTests {
  @Test func theLinkIsInTheHomeFolder() {
    #expect(ProcessBosunCLI.installLink(home: "/Users/me") == "/Users/me/.local/bin/bosun")
  }

  /// A Mac app's PATH is bare, as launchd's is: bosun's own tools (tmux, git) are where Homebrew puts them.
  @Test func theEnvironmentGetsTheUsualToolFolders() {
    let environment = ProcessBosunCLI.environment(
      base: ["PATH": "/usr/bin:/bin", "LANG": "C"], home: "/Users/me")
    #expect(environment["PATH"] == "/Users/me/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    #expect(environment["LANG"] == "C")
  }

  @Test func withNoPathItStillHasTheSystemOnes() {
    let environment = ProcessBosunCLI.environment(base: [:], home: "/Users/me")
    #expect(
      environment["PATH"]
        == "/Users/me/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
  }

  @Test func theStandardCLIRunsTheInstallLink() {
    let cli = ProcessBosunCLI.standard(home: "/Users/me", environment: ["PATH": "/usr/bin"])
    #expect(cli.executable == "/Users/me/.local/bin/bosun")
    #expect(cli.environment["PATH"]?.hasPrefix("/Users/me/.local/bin:") == true)
  }
}
