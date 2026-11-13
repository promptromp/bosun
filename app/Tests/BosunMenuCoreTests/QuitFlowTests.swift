import Testing

@testable import BosunMenuCore

private let quiet = quitPlan(windows: ["acme-orchestrator", "acme-web"], waiting: ["acme-web"])
private let busyPlan = quitPlan(
  windows: ["acme-orchestrator", "acme-exporter", "acme-api"], busy: ["acme-exporter", "acme-api"])

@Suite struct QuitCommandTests {
  @Test func theDefaultsAreToPauseTheMorningLetTheMacSleepAndLeaveTheIcon() {
    let choices = QuitChoices()
    #expect(choices.pauseMorning)
    #expect(choices.sleep)
    #expect(!choices.keepIcon)
    #expect(choices.busy == .letFinish)
  }

  @Test func theDefaultsGiveTheCommandThePlanSays() {
    #expect(quitCommand(QuitChoices()) == ["quit", "--let-finish", "--pause-morning", "--sleep", "--json"])
  }

  @Test func everySetOfChoicesIsItsOwnArgv() {
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

  @Test func keepingTheIconIsTheAppsAndNeverTheCommands() {
    #expect(
      quitCommand(QuitChoices(keepIcon: true)) == quitCommand(QuitChoices(keepIcon: false)))
  }

  @Test func theLetFinishCommandWaitsLongerThanTheStopNowOne() {
    let finish = Bosun.quit(QuitChoices(busy: .letFinish))
    let now = Bosun.quit(QuitChoices(busy: .stopNow))
    #expect(finish.timeout > .seconds(60 * 10))
    #expect(now.timeout < finish.timeout)
    #expect(!finish.detached)
    #expect(finish.args == quitCommand(QuitChoices(busy: .letFinish)))
  }

  @Test func theQuitPlanCommand() {
    #expect(Bosun.quitPlan.args == ["quit", "--plan", "--json"])
    #expect(Bosun.quitPlan.timeout == .seconds(30))
  }
}

@Suite struct QuitStepTests {
  private func start() -> (QuitStep, [Command]) { startQuit() }

  @Test func quitStartsByAskingForThePlan() {
    let (step, commands) = start()
    #expect(step == .planning)
    #expect(commands == [Bosun.quitPlan])
  }

  @Test func withNoBusyAgentsThereIsOneDialogAndThenTheQuit() {
    var (step, _) = start()
    (step, _) = next(step, on: .planned(quiet))
    #expect(step == .first(quiet))
    let choices = QuitChoices(pauseMorning: false, sleep: true, keepIcon: false, busy: .stopNow)
    let (running, commands) = next(step, on: .confirmed(choices))
    // Nothing is busy, so there is nothing to stop: the safe way waits for anything that turns busy
    #expect(
      running == .running(QuitChoices(pauseMorning: false, sleep: true, keepIcon: false, busy: .letFinish)))
    #expect(commands.map(\.args) == [["quit", "--let-finish", "--sleep", "--json"]])
    let (done, none) = next(running, on: .ended(.stopped(note: nil)))
    #expect(done == .done(keepIcon: false, note: nil))
    #expect(none.isEmpty)
  }

  @Test func theIconStaysWhenTheBoxWasChecked() {
    let choices = QuitChoices(keepIcon: true)
    let (running, _) = next(.first(quiet), on: .confirmed(choices))
    #expect(next(running, on: .ended(.stopped(note: nil))).0 == .done(keepIcon: true, note: nil))
  }

  @Test func busyAgentsAskASecondQuestionBeforeAnythingRuns() {
    let choices = QuitChoices()
    let (step, commands) = next(.first(busyPlan), on: .confirmed(choices))
    #expect(step == .busy(["acme-exporter", "acme-api"], choices))
    #expect(commands.isEmpty)
  }

  @Test func letThemFinishRunsTheQuitThatWaits() {
    let choices = QuitChoices(pauseMorning: false)
    let (running, commands) = next(.busy(["acme-exporter"], choices), on: .busyChosen(.letFinish))
    #expect(running == .running(QuitChoices(pauseMorning: false, busy: .letFinish)))
    #expect(commands.map(\.args) == [["quit", "--let-finish", "--sleep", "--json"]])
  }

  @Test func stopThemNowRunsTheQuitThatDoesNotWait() {
    let (running, commands) = next(.busy(["acme-exporter"], QuitChoices()), on: .busyChosen(.stopNow))
    #expect(running == .running(QuitChoices(busy: .stopNow)))
    #expect(commands.map(\.args) == [["quit", "--stop-now", "--pause-morning", "--sleep", "--json"]])
  }

  @Test func stillBusyAfterTheWaitAsksAgain() {
    let choices = QuitChoices(busy: .letFinish)
    let (step, commands) = next(.running(choices), on: .ended(.busy(["acme-exporter"])))
    #expect(step == .stillBusy(["acme-exporter"], choices))
    #expect(commands.isEmpty)
  }

  @Test func keepWaitingRunsTheWaitingQuitAgain() {
    let choices = QuitChoices(busy: .letFinish)
    let (running, commands) = next(.stillBusy(["acme-exporter"], choices), on: .busyChosen(.letFinish))
    #expect(running == .running(choices))
    #expect(commands.map(\.args) == [quitCommand(choices)])
  }

  @Test func stopThemNowAfterTheWaitStopsThem() {
    let (running, commands) = next(
      .stillBusy(["acme-exporter"], QuitChoices(busy: .letFinish)), on: .busyChosen(.stopNow))
    #expect(running == .running(QuitChoices(busy: .stopNow)))
    #expect(commands.map(\.args) == [["quit", "--stop-now", "--pause-morning", "--sleep", "--json"]])
    #expect(next(running, on: .ended(.stopped(note: nil))).0 == .done(keepIcon: false, note: nil))
  }

  @Test func busyThenFinishThenDoneIsTheWholeWalk() {
    var step = QuitStep.planning
    step = next(step, on: .planned(busyPlan)).0
    step = next(step, on: .confirmed(QuitChoices(keepIcon: true))).0
    step = next(step, on: .busyChosen(.letFinish)).0
    step = next(step, on: .ended(.stopped(note: nil))).0
    #expect(step == .done(keepIcon: true, note: nil))
  }

  @Test func busyThenStillBusyThenStopIsTheWholeWalk() {
    var step = QuitStep.planning
    step = next(step, on: .planned(busyPlan)).0
    step = next(step, on: .confirmed(QuitChoices())).0
    step = next(step, on: .busyChosen(.letFinish)).0
    step = next(step, on: .ended(.busy(["acme-exporter"]))).0
    step = next(step, on: .busyChosen(.stopNow)).0
    step = next(step, on: .ended(.stopped(note: nil))).0
    #expect(step == .done(keepIcon: false, note: nil))
  }

  @Test func aConsoleStillRunningIsCarriedToTheEnd() {
    let note = "A console is still running on port 8765"
    let (step, _) = next(.running(QuitChoices()), on: .ended(.stopped(note: note)))
    #expect(step == .done(keepIcon: false, note: note))
  }

  @Test func cancelAtAnyDialogStopsNothing() {
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

  @Test func aPlanThatRefusesIsShownAsTheRefusalAndNoDialogOpens() {
    let refused = quitPlan(refused: "an update is running: quit when it has finished")
    let (step, commands) = next(.planning, on: .planned(refused))
    #expect(step == .failed("an update is running: quit when it has finished"))
    #expect(commands.isEmpty)
  }

  @Test func aPlanThatCouldNotBeAskedForFails() {
    #expect(
      next(QuitStep.planning, on: QuitEvent.planFailed("bosun cannot be reached")).0
        == .failed("bosun cannot be reached"))
  }

  @Test func aQuitThatFailedShowsItsLine() {
    let (step, commands) = next(.running(QuitChoices()), on: .ended(.failed("bosun stop failed")))
    #expect(step == .failed("bosun stop failed"))
    #expect(commands.isEmpty)
  }

  @Test func anEventThatMeansNothingInTheStepChangesNothing() {
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

  @Test func theFinishedStepsAreTheOnesTheFlowEndsIn() {
    #expect(QuitStep.done(keepIcon: false, note: nil).isFinished)
    #expect(QuitStep.failed("x").isFinished)
    #expect(QuitStep.cancelled.isFinished)
    #expect(!QuitStep.planning.isFinished)
    #expect(!QuitStep.running(QuitChoices()).isFinished)
    #expect(!QuitStep.first(quiet).isFinished)
  }

  @Test func onlyAFinishedQuitThatKeptNoIconQuitsTheApp() {
    #expect(QuitStep.done(keepIcon: false, note: nil).quitsTheApp)
    #expect(!QuitStep.done(keepIcon: true, note: nil).quitsTheApp)
    #expect(!QuitStep.failed("x").quitsTheApp)
    #expect(!QuitStep.cancelled.quitsTheApp)
  }
}

@Suite struct QuitOutcomeTests {
  @Test func exitZeroAndStoppedIsDone() {
    let outcome = quitOutcome(from: exited(0, Fixtures.text("quit-result-stopped")))
    #expect(outcome == .stopped(note: nil))
  }

  @Test func aConsoleStillRunningIsANoteWithItsPort() {
    let outcome = quitOutcome(from: exited(0, Fixtures.text("quit-result-stopped-console-running")))
    guard case .stopped(let note?) = outcome else {
      Issue.record("expected a note")
      return
    }
    #expect(note.contains("8765"))
    #expect(note.contains("still running"))
  }

  @Test func aConsoleStillRunningWithNoPortSaysSo() {
    let text = Fixtures.text("quit-result-stopped-console-running")
      .replacingOccurrences(of: "8765", with: "null")
    guard case .stopped(let note?) = quitOutcome(from: exited(0, text)) else {
      Issue.record("expected a note")
      return
    }
    #expect(!note.contains("port"))
  }

  @Test func exitThreeIsStillBusyWithTheNames() {
    let outcome = quitOutcome(from: exited(3, Fixtures.text("quit-result-busy")))
    #expect(outcome == .busy(["acme-exporter"]))
  }

  @Test func aRefusalIsItsReasonAndAFailureItsError() {
    guard case .failed(let refused) = quitOutcome(from: exited(1, Fixtures.text("quit-result-refused")))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(refused.contains("update is running"))
    guard case .failed(let failed) = quitOutcome(from: exited(1, Fixtures.text("quit-result-failed")))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(failed.contains("the quit record stays"))
    #expect(failed.contains("bosun quit --stop-now"))
    guard case .failed(let early) = quitOutcome(from: exited(1, Fixtures.text("quit-result-failed-early")))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(early.contains("nothing was changed"))
  }

  @Test func aFailureWithNoLineStillSaysHowToFinish() {
    let text = Fixtures.text("quit-result-failed").replacingOccurrences(
      of: "\"error\": \"", with: "\"error\": null, \"was\": \"")
    guard case .failed(let line) = quitOutcome(from: exited(1, text)) else {
      Issue.record("expected a failure")
      return
    }
    #expect(line.contains("bosun quit --stop-now"))
  }

  @Test func theWordsOfTheResultDecideNotTheExitCode() {
    #expect(quitOutcome(from: exited(1, Fixtures.text("quit-result-stopped"))) == .stopped(note: nil))
    guard case .failed = quitOutcome(from: exited(0, Fixtures.text("quit-result-refused"))) else {
      Issue.record("expected a failure")
      return
    }
  }

  @Test func aQuitThatTimedOutSaysTheRecordMayStayAndHowToFinish() {
    guard
      case .failed(let line) = quitOutcome(from: CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(line.contains("bosun quit --stop-now"))
    #expect(line.contains("did not finish"))
  }

  @Test func aQuitThatCouldNotStartIsAFailure() {
    for outcome in [CommandOutcome.notInstalled, .couldNotStart("denied")] {
      guard case .failed = quitOutcome(from: CommandResult(outcome: outcome, stdout: "", stderr: "")) else {
        Issue.record("expected a failure")
        return
      }
    }
  }

  @Test func aReplyThatIsNotTheObjectIsItsStderrLine() {
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

  @Test func aNewerContractIsTheAppNeedingAnUpdate() {
    guard
      case .failed(let line) = quitOutcome(from: exited(0, Fixtures.text("quit-result-stopped", contract: 2)))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(line.contains("needs an update"))
  }
}

@Suite struct QuitGlueTests {
  @Test func thePlanThatRanBecomesTheEvent() async {
    let cli = FakeBosunCLI()
    await cli.reply(["quit", "--plan", "--json"], exited(0, Fixtures.text("quit-plan")))
    let event = await quitPlanEvent(via: cli)
    #expect(event == .planned(Fixtures.decode(QuitPlan.self, "quit-plan")))
    #expect(await cli.timeouts == [.seconds(30)])
  }

  @Test func aPlanThatDidNotRunBecomesAFailureEvent() async {
    let cli = FakeBosunCLI()
    await cli.reply(
      ["quit", "--plan", "--json"], CommandResult(outcome: .notInstalled, stdout: "", stderr: ""))
    guard case .planFailed(let why) = await quitPlanEvent(via: cli) else {
      Issue.record("expected planFailed")
      return
    }
    #expect(why.contains("not installed"))
  }

  @Test func theQuitThatRanBecomesTheEnding() async {
    let cli = FakeBosunCLI()
    let choices = QuitChoices(busy: .stopNow)
    await cli.reply(quitCommand(choices), exited(0, Fixtures.text("quit-result-stopped")))
    #expect(await runQuit(choices, via: cli) == .ended(.stopped(note: nil)))
    #expect(await cli.calls == [quitCommand(choices)])
    #expect(await cli.timeouts == [Bosun.quit(choices).timeout])
  }
}
