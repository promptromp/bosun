import BosunMenuTestSupport
import Testing

@testable import BosunMenuCore

private let quiet = quitPlan(windows: ["acme-orchestrator", "acme-web"], waiting: ["acme-web"])
private let busyPlan = quitPlan(
  windows: ["acme-orchestrator", "acme-exporter", "acme-api"], busy: ["acme-exporter", "acme-api"])

@Suite struct QuitCommandTests {
  @Test func theDefaultsAreToPauseTheMorningLetTheMacSleepAndLeaveTheIcon() throws {
    let choices = QuitChoices()
    #expect(choices.pauseMorning)
    #expect(choices.sleep)
    #expect(!choices.keepIcon)
    #expect(choices.busy == .letFinish)
  }

  @Test func theDefaultsGiveTheCommandThePlanSays() throws {
    #expect(quitCommand(QuitChoices()) == ["quit", "--let-finish", "--pause-morning", "--sleep", "--json"])
  }

  @Test func everySetOfChoicesIsItsOwnArgv() throws {
    for pause in [true, false] {
      for sleep in [true, false] {
        for busy in [BusyChoice.letFinish, .stopNow] {
          let choices = QuitChoices(pauseMorning: pause, sleep: sleep, keepIcon: false, busy: busy)
          var expected = ["quit", busy == .letFinish ? "--let-finish" : "--stop-now"]
          if pause { expected.append("--pause-morning") }
          if sleep { expected.append("--sleep") }
          expected.append("--json")
          #expect(quitCommand(choices) == expected)
        }
      }
    }
  }

  @Test func keepingTheIconIsTheAppsAndNeverTheCommands() throws {
    #expect(
      quitCommand(QuitChoices(keepIcon: true)) == quitCommand(QuitChoices(keepIcon: false)))
  }

  @Test func theLetFinishCommandWaitsLongerThanTheStopNowOne() throws {
    let wait = QuitWait(minutes: 10, lockSeconds: 180, stepsSeconds: 225)
    let finish = Bosun.quit(QuitChoices(busy: .letFinish, wait: wait))
    let now = Bosun.quit(QuitChoices(busy: .stopNow, wait: wait))
    #expect(finish.timeout > .seconds(60 * 10))
    #expect(now.timeout < finish.timeout)
    #expect(!finish.detached)
    #expect(finish.args == quitCommand(QuitChoices(busy: .letFinish, wait: wait)))
  }

  @Test func theQuitPlanCommand() throws {
    #expect(Bosun.quitPlan.args == ["quit", "--plan", "--json"])
    #expect(Bosun.quitPlan.timeout == .seconds(30))
  }
}

@Suite struct QuitStepTests {
  private func start() -> (QuitStep, [Command]) { startQuit() }

  @Test func quitStartsByAskingForThePlan() throws {
    let (step, commands) = start()
    #expect(step == .planning)
    #expect(commands == [Bosun.quitPlan])
  }

  @Test func withNoBusyAgentsThereIsOneDialogAndThenTheQuit() throws {
    var (step, _) = start()
    (step, _) = next(step, on: .planned(quiet))
    #expect(step == .first(quiet))
    let choices = QuitChoices(pauseMorning: false, sleep: true, keepIcon: false, busy: .stopNow)
    let (running, commands) = next(step, on: .confirmed(choices))
    // Nothing is busy, so there is nothing to stop: the safe way waits for anything that turns busy
    let wait = QuitWait(minutes: 10, lockSeconds: 180)  // what the plan said
    #expect(
      running
        == .running(
          QuitChoices(pauseMorning: false, sleep: true, keepIcon: false, busy: .letFinish, wait: wait)))
    #expect(commands.map(\.args) == [["quit", "--let-finish=10", "--sleep", "--json"]])
    let (done, none) = next(running, on: .ended(.stopped(note: nil)))
    #expect(done == .done(keepIcon: false, note: nil))
    #expect(none.isEmpty)
  }

  @Test func theIconStaysWhenTheBoxWasChecked() throws {
    let choices = QuitChoices(keepIcon: true)
    let (running, _) = next(.first(quiet), on: .confirmed(choices))
    #expect(next(running, on: .ended(.stopped(note: nil))).0 == .done(keepIcon: true, note: nil))
  }

  @Test func busyAgentsAskASecondQuestionBeforeAnythingRuns() throws {
    let choices = QuitChoices()
    let (step, commands) = next(.first(busyPlan), on: .confirmed(choices))
    #expect(
      step
        == .busy(["acme-exporter", "acme-api"], QuitChoices(wait: QuitWait(minutes: 10, lockSeconds: 180))))
    #expect(commands.isEmpty)
  }

  @Test func letThemFinishRunsTheQuitThatWaits() throws {
    let choices = QuitChoices(pauseMorning: false)
    let (running, commands) = next(.busy(["acme-exporter"], choices), on: .busyChosen(.letFinish))
    #expect(running == .running(QuitChoices(pauseMorning: false, busy: .letFinish)))
    #expect(commands.map(\.args) == [["quit", "--let-finish", "--sleep", "--json"]])
  }

  @Test func stopThemNowRunsTheQuitThatDoesNotWait() throws {
    let (running, commands) = next(.busy(["acme-exporter"], QuitChoices()), on: .busyChosen(.stopNow))
    #expect(running == .running(QuitChoices(busy: .stopNow)))
    #expect(commands.map(\.args) == [["quit", "--stop-now", "--pause-morning", "--sleep", "--json"]])
  }

  @Test func stillBusyAfterTheWaitAsksAgain() throws {
    let choices = QuitChoices(busy: .letFinish)
    let (step, commands) = next(.running(choices), on: .ended(.busy(["acme-exporter"])))
    #expect(step == .stillBusy(["acme-exporter"], choices))
    #expect(commands.isEmpty)
  }

  @Test func keepWaitingRunsTheWaitingQuitAgain() throws {
    let choices = QuitChoices(busy: .letFinish)
    let (running, commands) = next(.stillBusy(["acme-exporter"], choices), on: .busyChosen(.letFinish))
    #expect(running == .running(choices))
    #expect(commands.map(\.args) == [quitCommand(choices)])
  }

  @Test func stopThemNowAfterTheWaitStopsThem() throws {
    let (running, commands) = next(
      .stillBusy(["acme-exporter"], QuitChoices(busy: .letFinish)), on: .busyChosen(.stopNow))
    #expect(running == .running(QuitChoices(busy: .stopNow)))
    #expect(commands.map(\.args) == [["quit", "--stop-now", "--pause-morning", "--sleep", "--json"]])
    #expect(next(running, on: .ended(.stopped(note: nil))).0 == .done(keepIcon: false, note: nil))
  }

  @Test func busyThenFinishThenDoneIsTheWholeWalk() throws {
    var step = QuitStep.planning
    step = next(step, on: .planned(busyPlan)).0
    step = next(step, on: .confirmed(QuitChoices(keepIcon: true))).0
    step = next(step, on: .busyChosen(.letFinish)).0
    step = next(step, on: .ended(.stopped(note: nil))).0
    #expect(step == .done(keepIcon: true, note: nil))
  }

  @Test func busyThenStillBusyThenStopIsTheWholeWalk() throws {
    var step = QuitStep.planning
    step = next(step, on: .planned(busyPlan)).0
    step = next(step, on: .confirmed(QuitChoices())).0
    step = next(step, on: .busyChosen(.letFinish)).0
    step = next(step, on: .ended(.busy(["acme-exporter"]))).0
    step = next(step, on: .busyChosen(.stopNow)).0
    step = next(step, on: .ended(.stopped(note: nil))).0
    #expect(step == .done(keepIcon: false, note: nil))
  }

  @Test func aConsoleStillRunningIsCarriedToTheEnd() throws {
    let note = "A console is still running on port 8765"
    let (step, _) = next(.running(QuitChoices()), on: .ended(.stopped(note: note)))
    #expect(step == .done(keepIcon: false, note: note))
  }

  @Test func cancelAtAnyDialogStopsNothing() throws {
    let choices = QuitChoices()
    let steps: [QuitStep] = [
      .planning, .first(quiet), .first(busyPlan), .busy(["a"], choices), .stillBusy(["a"], choices),
    ]
    for step in steps {
      let (after, commands) = next(step, on: .cancelled)
      #expect(after == .cancelled, "\(step)")
      #expect(commands.isEmpty, "\(step)")
    }
  }

  @Test func aPlanThatRefusesIsShownAsTheRefusalAndNoDialogOpens() throws {
    let refused = quitPlan(refused: "an update is running: quit when it has finished")
    let (step, commands) = next(.planning, on: .planned(refused))
    #expect(step == .failed("an update is running: quit when it has finished"))
    #expect(commands.isEmpty)
  }

  @Test func aPlanThatCouldNotBeAskedForFails() throws {
    #expect(
      next(QuitStep.planning, on: QuitEvent.planFailed("bosun cannot be reached")).0
        == .failed("bosun cannot be reached"))
  }

  @Test func aQuitThatFailedShowsItsLine() throws {
    let (step, commands) = next(.running(QuitChoices()), on: .ended(.failed("bosun stop failed")))
    #expect(step == .failed("bosun stop failed"))
    #expect(commands.isEmpty)
  }

  @Test func anEventThatMeansNothingInTheStepChangesNothing() throws {
    let nothing: [(QuitStep, QuitEvent)] = [
      (.done(keepIcon: false, note: nil), .confirmed(QuitChoices())),
      (.cancelled, .busyChosen(.stopNow)),
      (.failed("x"), .ended(.stopped(note: nil))),
      (.planning, .confirmed(QuitChoices())),
      (.first(quiet), .busyChosen(.stopNow)),
      (.running(QuitChoices()), .cancelled),
      (.busy(["a"], QuitChoices()), .ended(.stopped(note: nil))),
    ]
    for (step, event) in nothing {
      let (after, commands) = next(step, on: event)
      #expect(after == step, "\(step) on \(event)")
      #expect(commands.isEmpty)
    }
  }

  @Test func theFinishedStepsAreTheOnesTheFlowEndsIn() throws {
    #expect(QuitStep.done(keepIcon: false, note: nil).isFinished)
    #expect(QuitStep.failed("x").isFinished)
    #expect(QuitStep.cancelled.isFinished)
    #expect(!QuitStep.planning.isFinished)
    #expect(!QuitStep.running(QuitChoices()).isFinished)
    #expect(!QuitStep.first(quiet).isFinished)
  }

  @Test func onlyAFinishedQuitThatKeptNoIconQuitsTheApp() throws {
    #expect(QuitStep.done(keepIcon: false, note: nil).quitsTheApp)
    #expect(!QuitStep.done(keepIcon: true, note: nil).quitsTheApp)
    #expect(!QuitStep.failed("x").quitsTheApp)
    #expect(!QuitStep.cancelled.quitsTheApp)
  }
}

/// A-14: the line a running quit shows under the status.
@Suite struct QuittingLineTests {
  @Test func aQuitThatLetsAgentsFinishNamesThem() throws {
    let running = QuitStep.running(QuitChoices(busy: .letFinish))
    #expect(
      quittingLine(running, busy: ["acme-exporter"]) == "Quitting: waiting for acme-exporter to finish…")
    #expect(quittingLine(running, busy: ["a", "b"]) == "Quitting: waiting for a, b to finish…")
    #expect(quittingLine(running, busy: []) == "Quitting…")
  }

  @Test func stoppingNowSaysSo() throws {
    #expect(quittingLine(.running(QuitChoices(busy: .stopNow)), busy: ["a"]) == "Quitting: stopping bosun…")
  }

  @Test func aDialogStepHasNoLine() throws {
    #expect(quittingLine(.planning, busy: []) == nil)
    #expect(quittingLine(.busy(["a"], QuitChoices()), busy: ["a"]) == nil)
    #expect(quittingLine(.appOnly, busy: []) == nil)
  }

  @Test func theBusyNamesComeFromTheDialogsThatNamedThem() throws {
    #expect(QuitStep.busy(["a"], QuitChoices()).busyNames == ["a"])
    #expect(QuitStep.stillBusy(["b"], QuitChoices()).busyNames == ["b"])
    #expect(QuitStep.planning.busyNames == nil)
  }
}

@Suite struct QuitOutcomeTests {
  @Test func exitZeroAndStoppedIsDone() throws {
    let outcome = quitOutcome(from: try Fixtures.reply("quit-result-stopped"))
    #expect(outcome == .stopped(note: nil))
  }

  @Test func aConsoleStillRunningIsANoteWithItsPort() throws {
    let outcome = quitOutcome(from: try Fixtures.reply("quit-result-stopped-console-running"))
    guard case .stopped(let note?) = outcome else {
      Issue.record("expected a note")
      return
    }
    #expect(note.contains("8765"))
    #expect(note.contains("still running"))
  }

  @Test func aConsoleStillRunningWithNoPortSaysSo() throws {
    let text = try Fixtures.text("quit-result-stopped-console-running")
      .replacingOccurrences(of: "8765", with: "null")
    guard case .stopped(let note?) = quitOutcome(from: exited(0, text)) else {
      Issue.record("expected a note")
      return
    }
    #expect(!note.contains("port"))
  }

  @Test func exitThreeIsStillBusyWithTheNames() throws {
    let outcome = quitOutcome(from: try Fixtures.reply("quit-result-busy"))
    #expect(outcome == .busy(["acme-exporter"]))
  }

  @Test func aRefusalIsItsReasonAndAFailureItsError() throws {
    guard case .failed(let refused) = quitOutcome(from: try Fixtures.reply("quit-result-refused"))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(refused.contains("update is running"))
    guard case .failed(let failed) = quitOutcome(from: try Fixtures.reply("quit-result-failed"))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(failed.contains("the quit record stays"))
    #expect(failed.contains("bosun quit --stop-now"))
    guard case .failed(let early) = quitOutcome(from: try Fixtures.reply("quit-result-failed-early"))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(early.contains("nothing was changed"))
  }

  @Test func aFailureWithNoLineStillSaysHowToFinish() throws {
    let text = try Fixtures.text("quit-result-failed").replacingOccurrences(
      of: "\"error\": \"", with: "\"error\": null, \"was\": \"")
    guard case .failed(let line) = quitOutcome(from: exited(1, text)) else {
      Issue.record("expected a failure")
      return
    }
    #expect(line.contains("bosun quit --stop-now"))
  }

  /// Each object with an exit status it was never given: the words decide.
  @Test func theWordsOfTheResultDecideNotTheExitCode() throws {
    #expect(quitOutcome(from: exited(1, try Fixtures.text("quit-result-stopped"))) == .stopped(note: nil))
    guard case .failed = quitOutcome(from: exited(0, try Fixtures.text("quit-result-refused"))) else {
      Issue.record("expected a failure")
      return
    }
  }

  @Test func aQuitThatTimedOutSaysTheRecordMayStayAndHowToFinish() throws {
    guard
      case .failed(let line) = quitOutcome(from: CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(line.contains("bosun quit --stop-now"))
    #expect(line.contains("did not finish"))
  }

  @Test func aQuitThatCouldNotStartIsAFailure() throws {
    for outcome in [CommandOutcome.notInstalled, .couldNotStart("denied")] {
      guard case .failed = quitOutcome(from: CommandResult(outcome: outcome, stdout: "", stderr: "")) else {
        Issue.record("expected a failure")
        return
      }
    }
  }

  @Test func aReplyThatIsNotTheObjectIsItsStderrLine() throws {
    guard case .failed(let line) = quitOutcome(from: exited(1, "oops", err: "bosun quit: usage\nmore")) else {
      Issue.record("expected a failure")
      return
    }
    #expect(line == "bosun quit: usage")
    guard case .failed(let silent) = quitOutcome(from: exited(2)) else {
      Issue.record("expected a failure")
      return
    }
    #expect(silent.contains("status 2"))
  }

  @Test func aNewerContractIsTheAppNeedingAnUpdate() throws {
    guard
      case .failed(let line) = quitOutcome(
        from: exited(0, try Fixtures.text("quit-result-stopped", contract: 2)))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(line.contains("needs an update"))
  }
}

@Suite struct QuitGlueTests {
  @Test func thePlanThatRanBecomesTheEvent() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(["quit", "--plan", "--json"], try Fixtures.reply("quit-plan"))
    let event = await quitPlanEvent(via: cli)
    #expect(event == .planned(try Fixtures.decode(QuitPlan.self, "quit-plan")))
    #expect(await cli.timeouts == [.seconds(30)])
  }

  @Test func aPlanThatDidNotRunBecomesAFailureEvent() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(
      ["quit", "--plan", "--json"], CommandResult(outcome: .notInstalled, stdout: "", stderr: ""))
    guard case .planFailed(let why) = await quitPlanEvent(via: cli) else {
      Issue.record("expected planFailed")
      return
    }
    #expect(why.contains("not installed"))
  }

  @Test func theQuitThatRanBecomesTheEnding() async throws {
    let cli = FakeBosunCLI()
    let choices = QuitChoices(busy: .stopNow)
    await cli.reply(quitCommand(choices), try Fixtures.reply("quit-result-stopped"))
    #expect(await runQuit(choices, via: cli) == .ended(.stopped(note: nil)))
    #expect(await cli.calls == [quitCommand(choices)])
    #expect(await cli.timeouts == [Bosun.quit(choices).timeout])
  }
}
