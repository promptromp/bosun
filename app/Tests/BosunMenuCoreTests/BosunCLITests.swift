import Testing

@testable import BosunMenuCore

private let okStatus = Fixtures.status("status-ok")

private func result(_ outcome: CommandOutcome, out: String = "", err: String = "") -> CommandResult {
  CommandResult(outcome: outcome, stdout: out, stderr: err)
}

@Suite struct StatusFromACommandTests {
  @Test func aGoodReplyIsTheCurrentStatus() {
    let state = statusState(from: exited(0, Fixtures.text("status-ok")), previous: nil)
    #expect(state == .loaded(okStatus, current: true))
  }

  /// Review focus 1: bosun not installed, or its link broken (an update cut short).
  @Test func statusFromFailedCommand() {
    #expect(statusState(from: result(.notInstalled), previous: nil) == .notInstalled)
    #expect(statusState(from: result(.notInstalled), previous: okStatus) == .notInstalled)

    let broken = statusState(from: result(.couldNotStart("Permission denied")), previous: nil)
    #expect(broken == .unreachable("Permission denied"))

    let crashed = statusState(from: exited(1, "", err: "bosun: line 3: boom\nmore"), previous: okStatus)
    #expect(crashed == .unreachable("bosun: line 3: boom"))

    let silent = statusState(from: exited(127), previous: nil)
    #expect(silent == .unreachable("bosun exited with status 127 and said nothing"))
  }

  /// Review focus 2: a command that hangs shows the last state, "not current".
  @Test func slowCommandTimesOut() {
    let late = result(.timedOut)
    #expect(statusState(from: late, previous: okStatus) == .loaded(okStatus, current: false))
    #expect(statusState(from: late, previous: nil) == .unreachable("bosun did not answer in time"))
  }

  @Test func aReplyThatIsNotTheObjectIsUnreachableWithWhy() {
    guard case .unreachable(let why) = statusState(from: exited(0, "not json"), previous: nil) else {
      Issue.record("expected unreachable")
      return
    }
    #expect(why.contains("could not be read"))
  }

  @Test func contractTooNew() {
    let state = statusState(from: exited(0, Fixtures.text("status-ok", contract: 2)), previous: okStatus)
    #expect(state == .contractTooNew(2))
  }

  @Test func theStatusItselfIsRequestedWithItsFifteenSeconds() async {
    let cli = FakeBosunCLI()
    await cli.reply(["status", "--json"], exited(0, Fixtures.text("status-ok")))
    let state = await loadStatus(via: cli, previous: nil)
    #expect(state == .loaded(okStatus, current: true))
    #expect(await cli.timeouts == [.seconds(15)])
  }

  @Test func aStatusThatHangsKeepsTheLastOne() async {
    let cli = FakeBosunCLI()
    await cli.reply(["status", "--json"], result(.timedOut))
    #expect(await loadStatus(via: cli, previous: okStatus) == .loaded(okStatus, current: false))
  }
}

@Suite struct CommandsTests {
  @Test func eachCommandIsTheArgvPartOneDefines() {
    #expect(Bosun.status.args == ["status", "--json"])
    #expect(Bosun.start.args == ["morning", "--now"])
    #expect(Bosun.consoleURL.args == ["console", "--url"])
    #expect(Bosun.appStatus.args == ["app", "status", "--json"])
    #expect(Bosun.appLogin(on: true).args == ["app", "login", "on"])
    #expect(Bosun.appLogin(on: false).args == ["app", "login", "off"])
    #expect(Bosun.skip(version: "v2026.11.11").args == ["update", "--skip", "v2026.11.11"])
  }

  @Test func theLongOnesStartDetachedAndTheRestDoNot() {
    #expect(Bosun.start.detached)
    #expect(Bosun.update.detached)
    let others: [Command] = [
      Bosun.status, Bosun.checkUpdates, Bosun.planUpdate, Bosun.quitPlan, Bosun.consoleURL,
      Bosun.appStatus,
    ]
    #expect(others.allSatisfy { !$0.detached })
  }

  @Test func everyCallHasATimeout() {
    #expect(Bosun.status.timeout == .seconds(15))
    #expect(Bosun.checkUpdates.timeout == .seconds(60))
    #expect(Bosun.planUpdate.timeout == .seconds(30))
    #expect(Bosun.quitPlan.timeout == .seconds(30))
    #expect(Bosun.appStatus.timeout > .zero)
    #expect(Bosun.consoleURL.timeout > .zero)
    #expect(Bosun.skip(version: "v1").timeout > .zero)
  }

  @Test func theAppRunsOnlyWhatTheDesignListsAndNeverAFileOfBosuns() {
    let commands: [Command] = [
      Bosun.status, Bosun.checkUpdates, Bosun.planUpdate, Bosun.update, Bosun.skip(version: "v1"),
      Bosun.quitPlan, Bosun.start, Bosun.consoleURL, Bosun.appStatus, Bosun.appLogin(on: true),
      Bosun.quit(QuitChoices()),
    ]
    let verbs = Set(commands.map { $0.args[0] })
    #expect(verbs == ["status", "update", "quit", "morning", "console", "app"])
  }
}

@Suite struct FakeBosunCLITests {
  @Test func itRecordsWhatWasRunAndAnswersWhatItWasToldTo() async {
    let cli = FakeBosunCLI()
    await cli.reply(["a"], exited(0, "one"))
    let first = await cli.run(["a"], timeout: .seconds(1))
    #expect(first.stdout == "one")
    #expect(await cli.calls == [["a"]])
    #expect(await cli.timeouts == [.seconds(1)])
  }

  @Test func repliesAreGivenInTheOrderTheyWereToldAndTheLastOneStays() async {
    let cli = FakeBosunCLI()
    await cli.reply(["a"], exited(0, "one"))
    await cli.reply(["a"], exited(0, "two"))
    let outs = [
      await cli.run(["a"], timeout: .seconds(1)).stdout,
      await cli.run(["a"], timeout: .seconds(1)).stdout,
      await cli.run(["a"], timeout: .seconds(1)).stdout,
    ]
    #expect(outs == ["one", "two", "two"])
  }

  @Test func aCommandItWasNotToldAboutFailsLoudly() async {
    let cli = FakeBosunCLI()
    let reply = await cli.run(["surprise", "--now"], timeout: .seconds(1))
    #expect(reply.outcome == .exited(1))
    #expect(reply.stderr.contains("surprise --now"))
  }

  @Test func aCommandRunsThroughItsOwnTimeout() async {
    let cli = FakeBosunCLI()
    await cli.reply(["status", "--json"], exited(0, "x"))
    _ = await cli.run(Bosun.status)
    #expect(await cli.timeouts == [Bosun.status.timeout])
  }
}

@Suite struct FetchTests {
  struct Thing: Decodable, Equatable, Sendable { let contract: Int }

  @Test func aReplyThatDecodesIsTheThing() async {
    let cli = FakeBosunCLI()
    await cli.reply(["x"], exited(0, #"{"contract": 1}"#))
    let command = Command(args: ["x"], timeout: .seconds(1))
    let got = await fetch(Thing.self, command, via: cli)
    #expect(got == .success(Thing(contract: 1)))
  }

  @Test func theFailuresEachHaveTheirWords() {
    #expect(FetchFailure.notInstalled.message == "bosun is not installed")
    #expect(FetchFailure.timedOut.message == "bosun did not answer in time")
    #expect(FetchFailure.couldNotStart("denied").message == "denied")
    #expect(FetchFailure.exited(4, "boom").message == "boom")
    #expect(FetchFailure.exited(4, "").message == "bosun exited with status 4 and said nothing")
    #expect(FetchFailure.contract(.unsupported(2)).message.contains("needs an update"))
  }

  @Test func theCommandsOwnStderrLineIsWhatIsShown() async {
    let cli = FakeBosunCLI()
    await cli.reply(["x"], exited(2, "", err: "\n  \nbosun x: usage\nmore\n"))
    let got = await fetch(Thing.self, Command(args: ["x"], timeout: .seconds(1)), via: cli)
    #expect(got == .failure(.exited(2, "bosun x: usage")))
  }

  @Test func aNonZeroExitWithTheObjectOnStdoutStillDecodesWhenAskedTo() async {
    let cli = FakeBosunCLI()
    await cli.reply(["x"], exited(3, #"{"contract": 1}"#))
    let command = Command(args: ["x"], timeout: .seconds(1))
    #expect(await fetch(Thing.self, command, via: cli, trustingStdout: true) == .success(Thing(contract: 1)))
    #expect(await fetch(Thing.self, command, via: cli) == .failure(.exited(3, "")))
  }
}

@Suite struct DetachedCommandTests {
  @Test func aDetachedCommandIsStartedAndLeftRunning() async {
    let cli = FakeBosunCLI()
    let result = await cli.start(Bosun.update)
    #expect(result.outcome == .started)
    #expect(await cli.started == [["update", "--yes"]])
    #expect(await cli.calls.isEmpty)
  }

  @Test func runRefusesADetachedCommandAndStartRefusesAWaitedOne() async {
    let cli = FakeBosunCLI()
    guard case .couldNotStart(let ran) = await cli.run(Bosun.update).outcome else {
      Issue.record("expected a refusal")
      return
    }
    #expect(ran.contains("start it"))
    guard case .couldNotStart(let started) = await cli.start(Bosun.status).outcome else {
      Issue.record("expected a refusal")
      return
    }
    #expect(started.contains("run it"))
    // Neither reached bosun
    #expect(await cli.calls.isEmpty)
    #expect(await cli.started.isEmpty)
  }

  @Test func startBosunIsTheMorningRunDetached() async {
    let cli = FakeBosunCLI()
    _ = await cli.start(Bosun.start)
    #expect(await cli.started == [["morning", "--now"]])
  }

  @Test func theFakeAnswersAStartWhenToldTo() async {
    let cli = FakeBosunCLI()
    await cli.reply(["morning", "--now"], result(.notInstalled))
    #expect(await cli.start(Bosun.start).outcome == .notInstalled)
  }

  @Test func aStartedCommandHasNoReplyToRead() {
    let started = CommandResult(outcome: .started, stdout: "", stderr: "")
    guard case .failure(.couldNotStart(let why)) = decodeReply(StatusSnapshot.self, started) else {
      Issue.record("expected a failure")
      return
    }
    #expect(why.contains("no reply"))
  }
}
