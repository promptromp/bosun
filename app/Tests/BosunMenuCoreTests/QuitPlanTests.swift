import BosunMenuTestSupport
import Testing

@testable import BosunMenuCore

@Suite struct QuitPlanUnavailableTests {
  private let reason = "the windows could not be listed: tmux did not answer"
  private let wait = QuitWait(minutes: 10, lockSeconds: 180, stepsSeconds: 225)

  @Test func aPlanThatCouldNotLookKeepsItsErrorItsWaitAndOffersStopNow() throws {
    let event = quitPlanEvent(from: try Fixtures.reply("quit-plan-failed"))
    guard case .planUnavailable(let why, let said) = event else {
      Issue.record("expected planUnavailable")
      return
    }
    #expect(why.contains("tmux did not answer"))
    #expect(!why.contains("said nothing"))
    #expect(said == wait)
  }

  @Test func theStepOffersTheChoicesAndStopNowIsGivenTheFailuresWait() throws {
    let (step, none) = next(QuitStep.planning, on: QuitEvent.planUnavailable(reason, wait))
    #expect(step == .unavailable(reason, wait))
    #expect(none.isEmpty)
    let choices = QuitChoices(pauseMorning: false, sleep: true, keepIcon: true)
    let (running, commands) = next(step, on: .confirmed(choices))
    #expect(
      running
        == .running(QuitChoices(pauseMorning: false, sleep: true, keepIcon: true, busy: .stopNow, wait: wait))
    )
    #expect(commands.map(\.args) == [["quit", "--stop-now", "--sleep", "--json"]])
    // the lock wait, the steps and the margin: longer than bosun's own lock wait and its stop
    #expect(commands.map(\.timeout) == [.seconds(465)])  // 180 + 225 + 60
    #expect(next(running, on: .ended(.stopped(note: nil))).0 == .done(keepIcon: true, note: nil))
  }

  @Test func withNoWaitSaidStopNowGetsTheLongestTimeout() throws {
    let (step, _) = next(
      QuitStep.planning, on: QuitEvent.planUnavailable("bosun did not answer in time", nil))
    let (_, commands) = next(step, on: .confirmed(QuitChoices()))
    #expect(commands.map(\.timeout) == [.seconds(60 * 60)])
  }

  @Test func theStepCanBeCancelledOrLeftAndIgnoresTheBusyAnswers() throws {
    let step = QuitStep.unavailable(reason, wait)
    #expect(next(step, on: QuitEvent.cancelled).0 == .cancelled)
    #expect(next(step, on: QuitEvent.busyChosen(.stopNow)).0 == step)
    #expect(!step.isFinished)
    let (left, commands) = next(step, on: .leaveApp)
    #expect(left == .appOnly)
    #expect(commands.isEmpty)
    #expect(left.isFinished && left.quitsTheApp)
  }

  @Test func leavingTheAppMeansNothingElsewhere() throws {
    #expect(next(QuitStep.first(quitPlan()), on: .leaveApp).0 == .first(quitPlan()))
    #expect(next(QuitStep.planning, on: .leaveApp).0 == .planning)
  }

  @Test func aFailureWithNoErrorStillSaysStopNowWorks() throws {
    let text = try Fixtures.text("quit-plan-failed").replacingOccurrences(
      of: "\"error\": \"", with: "\"error\": null, \"was\": \"")
    guard case .planUnavailable(let why, _) = quitPlanEvent(from: exited(1, text)) else {
      Issue.record("expected planUnavailable")
      return
    }
    #expect(why.contains("Stop Now still works"))
  }

  @Test func aPlanThatRanIsThePlan() throws {
    let plan = try Fixtures.decode(QuitPlan.self, "quit-plan")
    #expect(quitPlanEvent(from: try Fixtures.reply("quit-plan")) == .planned(plan))
  }

  /// P1: only `failed` offers Stop Now. A refusal (an update runs) on a non-zero exit would refuse a Stop Now
  /// too, so it is only told.
  @Test func aRefusedOrBusyObjectOnAFailedExitOnlyExplains() throws {
    let refused = try Fixtures.text("quit-result-refused")
    guard case .planFailed(let why) = quitPlanEvent(from: exited(1, refused)) else {
      Issue.record("expected planFailed")
      return
    }
    #expect(why.contains("update is running"))
    let busy = try Fixtures.text("quit-result-busy")
    guard case .planFailed = quitPlanEvent(from: exited(3, busy)) else {
      Issue.record("expected planFailed")
      return
    }
    let unknown = try Fixtures.text("quit-plan-failed")
      .replacingOccurrences(of: "\"result\": \"failed\"", with: "\"result\": \"vanished\"")
    guard case .planFailed = quitPlanEvent(from: exited(1, unknown)) else {
      Issue.record("expected planFailed")
      return
    }
  }

  /// A plan that never answered, or failed with no object, is no dead end: Stop Now, or leave only the app.
  @Test func aPlanThatTimedOutOrSaidOnlyALineOffersStopNow() throws {
    let hung = CommandResult(outcome: .timedOut, stdout: "", stderr: "")
    #expect(quitPlanEvent(from: hung) == .planUnavailable("bosun did not answer in time", nil))
    let line = quitPlanEvent(from: exited(1, "", err: "bosun quit: tmux hung\nmore"))
    #expect(line == .planUnavailable("bosun quit: tmux hung", nil))
  }

  @Test func whatCannotRunBosunOrIsNotUnderstoodIsOnlyTold() throws {
    let missing = CommandResult(outcome: .notInstalled, stdout: "", stderr: "")
    guard case .planFailed = quitPlanEvent(from: missing) else {
      Issue.record("expected planFailed")
      return
    }
    guard case .planFailed = quitPlanEvent(from: exited(0, "not json")) else {
      Issue.record("expected planFailed")
      return
    }
  }

  @Test func theFakeCLIGivesTheSameThroughTheCommand() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(["quit", "--plan", "--json"], try Fixtures.reply("quit-plan-failed"))
    guard case .planUnavailable = await quitPlanEvent(via: cli) else {
      Issue.record("expected planUnavailable")
      return
    }
  }
}

@Suite struct QuitUnknownResultTests {
  @Test func aResultWordItDoesNotKnowIsAFailureThatNamesIt() throws {
    let text = try Fixtures.text("quit-result-stopped")
      .replacingOccurrences(of: "\"result\": \"stopped\"", with: "\"result\": \"vanished\"")
    guard case .failed(let line) = quitOutcome(from: exited(0, text)) else {
      Issue.record("expected a failure")
      return
    }
    #expect(line.contains("\"vanished\""))
    #expect(line.contains("bosun quit --stop-now"))
  }

  @Test func anUnknownConsoleWordIsSaidNothingOf() throws {
    let text = try Fixtures.text("quit-result-stopped-console-running")
      .replacingOccurrences(of: "still running", with: "hiding")
    #expect(quitOutcome(from: exited(0, text)) == .stopped(note: nil))
  }
}

@Suite struct QuitActionTests {
  private func loaded(_ name: String) throws -> StatusState {
    .loaded(try Fixtures.status(name), current: true)
  }

  @Test func aRunningBosunRunsTheQuitFlowWhateverItsLevel() throws {
    let names = ["status-ok", "status-warning", "status-error", "status-quit-unfinished", "status-resting"]
    for name in names {
      #expect(try quitAction(for: loaded(name)) == .runFlow, "\(name)")
    }
    #expect(quitAction(for: .loaded(try Fixtures.status("status-ok"), current: false)) == .runFlow)
  }

  @Test func whileStoppedThereIsNoDialogAndTheAppQuits() throws {
    #expect(try quitAction(for: loaded("status-stopped")) == .exitApp)
  }

  @Test func whenBosunCannotBeAskedTheAppOnlyQuits() throws {
    #expect(quitAction(for: .notInstalled) == .exitApp)
    #expect(quitAction(for: .unreachable("x")) == .exitApp)
    #expect(quitAction(for: .contractTooNew(2)) == .exitApp)
  }
}
