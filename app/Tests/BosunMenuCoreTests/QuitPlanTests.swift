import Testing

@testable import BosunMenuCore

@Suite struct QuitPlanUnavailableTests {
  private let reason = "the windows could not be listed: tmux did not answer"

  @Test func aPlanThatCouldNotLookKeepsItsErrorAndOffersStopNow() {
    let event = quitPlanEvent(from: exited(1, Fixtures.text("quit-plan-failed")))
    guard case .planUnavailable(let why) = event else {
      Issue.record("expected planUnavailable")
      return
    }
    #expect(why.contains("tmux did not answer"))
    #expect(!why.contains("said nothing"))
  }

  @Test func theStepOffersTheChoicesAndStopNowIsTheOnlyWayOn() {
    let (step, none) = next(QuitStep.planning, on: QuitEvent.planUnavailable(reason))
    #expect(step == .unavailable(reason))
    #expect(none.isEmpty)
    let choices = QuitChoices(pauseMorning: false, sleep: true, keepIcon: true)
    let (running, commands) = next(step, on: .confirmed(choices))
    #expect(
      running == .running(QuitChoices(pauseMorning: false, sleep: true, keepIcon: true, busy: .stopNow)))
    #expect(commands.map(\.args) == [["quit", "--stop-now", "--sleep", "--json"]])
    #expect(next(running, on: .ended(.stopped(note: nil))).0 == .done(keepIcon: true, note: nil))
  }

  @Test func theStepCanBeCancelledAndIgnoresTheBusyAnswers() {
    #expect(next(QuitStep.unavailable(reason), on: QuitEvent.cancelled).0 == .cancelled)
    #expect(next(QuitStep.unavailable(reason), on: QuitEvent.busyChosen(.stopNow)).0 == .unavailable(reason))
    #expect(!QuitStep.unavailable(reason).isFinished)
  }

  @Test func aFailureWithNoErrorStillSaysStopNowWorks() {
    let text = Fixtures.text("quit-plan-failed").replacingOccurrences(
      of: "\"error\": \"", with: "\"error\": null, \"was\": \"")
    guard case .planUnavailable(let why) = quitPlanEvent(from: exited(1, text)) else {
      Issue.record("expected planUnavailable")
      return
    }
    #expect(why.contains("Stop Now still works"))
  }

  @Test func aPlanThatRanIsThePlan() {
    let plan = Fixtures.decode(QuitPlan.self, "quit-plan")
    #expect(quitPlanEvent(from: exited(0, Fixtures.text("quit-plan"))) == .planned(plan))
  }

  @Test func aReplyThatIsNotTheFailureObjectIsAnOrdinaryFailure() {
    guard case .planFailed(let line) = quitPlanEvent(from: exited(1, "", err: "bosun quit: usage\nmore"))
    else {
      Issue.record("expected planFailed")
      return
    }
    #expect(line == "bosun quit: usage")
    let missing = CommandResult(outcome: .notInstalled, stdout: "", stderr: "")
    guard case .planFailed = quitPlanEvent(from: missing) else {
      Issue.record("expected planFailed")
      return
    }
  }

  @Test func theFakeCLIGivesTheSameThroughTheCommand() async {
    let cli = FakeBosunCLI()
    await cli.reply(["quit", "--plan", "--json"], exited(1, Fixtures.text("quit-plan-failed")))
    guard case .planUnavailable = await quitPlanEvent(via: cli) else {
      Issue.record("expected planUnavailable")
      return
    }
  }
}

@Suite struct QuitUnknownResultTests {
  @Test func aResultWordItDoesNotKnowIsAFailureThatNamesIt() {
    let text = Fixtures.text("quit-result-stopped")
      .replacingOccurrences(of: "\"result\": \"stopped\"", with: "\"result\": \"vanished\"")
    guard case .failed(let line) = quitOutcome(from: exited(0, text)) else {
      Issue.record("expected a failure")
      return
    }
    #expect(line.contains("\"vanished\""))
    #expect(line.contains("bosun quit --stop-now"))
  }

  @Test func anUnknownConsoleWordIsSaidNothingOf() {
    let text = Fixtures.text("quit-result-stopped-console-running")
      .replacingOccurrences(of: "still running", with: "hiding")
    #expect(quitOutcome(from: exited(0, text)) == .stopped(note: nil))
  }
}

@Suite struct QuitActionTests {
  private func loaded(_ name: String) -> StatusState { .loaded(Fixtures.status(name), current: true) }

  @Test func aRunningBosunRunsTheQuitFlowWhateverItsLevel() {
    let names = ["status-ok", "status-warning", "status-error", "status-quit-unfinished", "status-resting"]
    for name in names {
      #expect(quitAction(for: loaded(name)) == .runFlow, "\(name)")
    }
    #expect(quitAction(for: .loaded(Fixtures.status("status-ok"), current: false)) == .runFlow)
  }

  @Test func whileStoppedThereIsNoDialogAndTheAppQuits() {
    #expect(quitAction(for: loaded("status-stopped")) == .exitApp)
  }

  @Test func whenBosunCannotBeAskedTheAppOnlyQuits() {
    #expect(quitAction(for: .notInstalled) == .exitApp)
    #expect(quitAction(for: .unreachable("x")) == .exitApp)
    #expect(quitAction(for: .contractTooNew(2)) == .exitApp)
  }
}
