import BosunMenuTestSupport
import Testing

@testable import BosunMenuCore

private var available: UpdateCheck {
  get throws { try Fixtures.decode(UpdateCheck.self, "update-check-available") }
}
private var current: UpdateCheck {
  get throws { try Fixtures.decode(UpdateCheck.self, "update-check-current") }
}
private var skipped: UpdateCheck {
  get throws { try Fixtures.decode(UpdateCheck.self, "update-check-skipped") }
}
private var failedCheck: UpdateCheck {
  get throws { try Fixtures.decode(UpdateCheck.self, "update-check-failed") }
}
private var plan: UpdatePlan { get throws { try Fixtures.plan() } }
private var blocked: UpdatePlan { get throws { try Fixtures.plan("update-plan-blocked") } }

private let checkCommand = [Bosun.checkUpdates]

@Suite struct UpdateCheckTransitionTests {
  @Test func checkingStartsFromIdleAndAfterAFailure() throws {
    for state in [UpdateState.idle, .failed("earlier")] {
      let (after, commands) = next(state, on: .checkRequested(manual: true))
      #expect(after == .checking)
      #expect(commands == checkCommand)
    }
  }

  @Test func theCheckIsTheJSONCommandWithItsSixtySeconds() throws {
    #expect(Bosun.checkUpdates.args == ["update", "--check", "--json"])
    #expect(Bosun.checkUpdates.timeout == .seconds(60))
    #expect(!Bosun.checkUpdates.detached)
  }

  @Test func anAvailableUpdateIsOfferedWhetherOrNotAskedFor() throws {
    for manual in [true, false] {
      let (state, commands) = next(.checking, on: .checked(try available, manual: manual))
      #expect(try state == .available(available))
      #expect(commands.isEmpty)
    }
  }

  @Test func aSkippedVersionIsOfferedOnlyToWhoAsks() throws {
    #expect(try next(.checking, on: .checked(skipped, manual: true)).0 == .available(skipped))
    #expect(try next(.checking, on: .checked(skipped, manual: false)).0 == .idle)
  }

  @Test func nothingNewGoesBackToIdle() throws {
    #expect(try next(.checking, on: .checked(current, manual: true)).0 == .idle)
    #expect(try next(.checking, on: .checked(current, manual: false)).0 == .idle)
  }

  @Test func aCheckThatSaysItFailedFailsOnlyWhenAskedFor() throws {
    #expect(
      try next(.checking, on: .checked(failedCheck, manual: true)).0
        == .failed(failedCheck.error ?? ""))
    #expect(try next(.checking, on: .checked(failedCheck, manual: false)).0 == .idle)
  }

  @Test func aCheckThatCouldNotRunFailsOnlyWhenAskedFor() throws {
    #expect(next(.checking, on: .checkFailed("timed out", manual: true)).0 == .failed("timed out"))
    #expect(next(.checking, on: .checkFailed("timed out", manual: false)).0 == .idle)
  }

  @Test func aFailureIsAcknowledgedBackToIdle() throws {
    let (state, commands) = next(.failed("x"), on: .acknowledged)
    #expect(state == .idle)
    #expect(commands.isEmpty)
  }

  @Test func anOfferedUpdateIsNotRecheckedUnderTheMenu() throws {
    let state = UpdateState.available(try available)
    let (after, commands) = next(state, on: .checkRequested(manual: false))
    #expect(after == state)
    #expect(commands.isEmpty)
  }
}

@Suite struct UpdateOfferTransitionTests {
  @Test func updateNowAsksTheDryRunForItsPlan() throws {
    let (state, commands) = next(.available(try available), on: .updateNowChosen)
    #expect(try state == .planning(available))
    #expect(commands == [Bosun.planUpdate])
    #expect(Bosun.planUpdate.args == ["update", "--dry-run", "--json"])
    #expect(Bosun.planUpdate.timeout == .seconds(120))
  }

  @Test func skippingRecordsTheVersionAndGoesIdle() throws {
    let (state, commands) = next(.available(try available), on: .skipChosen)
    #expect(state == .idle)
    #expect(commands.map(\.args) == [["update", "--skip", "v2026.11.11"]])
  }

  @Test func remindMeLaterLeavesTheOfferWhereItIs() throws {
    let (state, commands) = next(.available(try available), on: .remindLater)
    #expect(try state == .available(available))
    #expect(commands.isEmpty)
  }
}

@Suite struct UpdatePlanTransitionTests {
  @Test func aPlanIsShownForConfirmation() throws {
    let (state, commands) = next(.planning(try available), on: .planned(try plan))
    #expect(try state == .confirming(plan, available))
    #expect(commands.isEmpty)
  }

  @Test func aBlockedPlanIsShownToo() throws {
    #expect(try next(.planning(available), on: .planned(blocked)).0 == .confirming(blocked, available))
  }

  @Test func noPlanMeansNothingToUpdateOrARefusal() throws {
    let (state, _) = next(.planning(try available), on: .planned(nil))
    guard case .failed(let why) = state else {
      Issue.record("expected a failure")
      return
    }
    #expect(why.contains("no plan"))
  }

  @Test func aPlanThatCouldNotBeMadeFails() throws {
    let state = next(UpdateState.planning(try available), on: UpdateEvent.planFailed("timed out")).0
    #expect(state == .failed("timed out"))
  }

  @Test func yesRunsTheUpdateDetached() throws {
    let (state, commands) = next(.confirming(try plan, try available), on: .confirmed)
    #expect(try state == .starting(available))
    #expect(commands == [Bosun.update])
    #expect(Bosun.update.args == ["update", "--yes"])
    #expect(Bosun.update.detached)
  }

  @Test func yesToABlockedPlanDoesNothing() throws {
    let state = UpdateState.confirming(try blocked, try available)
    let (after, commands) = next(state, on: .confirmed)
    #expect(after == state)
    #expect(commands.isEmpty)
  }

  @Test func cancellingGoesBackToTheOfferWithoutAskingAgain() throws {
    let (state, commands) = next(.confirming(try plan, try available), on: .cancelled)
    #expect(try state == .available(available))
    #expect(commands.isEmpty)
  }
}

@Suite struct UpdateRunningTransitionTests {
  @Test func theStatusShowingTheUpdateMakesItRunningWithWhatItWaitsFor() throws {
    let (state, _) = next(
      .starting(try available), on: .statusShows(running: true, waitingFor: ["acme-web"]))
    #expect(state == .running(waitingFor: ["acme-web"]))
    let (later, _) = next(state, on: .statusShows(running: true, waitingFor: []))
    #expect(later == .running(waitingFor: []))
  }

  @Test func anUpdateStartedElsewhereIsShownToo() throws {
    let states: [UpdateState] = [
      .idle, .checking, .failed("x"), .available(try available), .checkingStart(try available),
    ]
    for state in states {
      let (after, commands) = next(state, on: .statusShows(running: true, waitingFor: ["a"]))
      #expect(after == .running(waitingFor: ["a"]))
      #expect(commands.isEmpty)
    }
  }

  @Test func anUpdateBeingPlannedIsNotInterruptedByTheStatus() throws {
    for state in [UpdateState.planning(try available), .confirming(try plan, try available)] {
      #expect(next(state, on: .statusShows(running: true, waitingFor: [])).0 == state)
    }
  }

  @Test func whenTheUpdateEndsTheAppLooksAgain() throws {
    let (state, commands) = next(
      .running(waitingFor: ["a"]), on: .statusShows(running: false, waitingFor: []))
    #expect(state == .checking)
    #expect(commands == checkCommand)
  }

  @Test func theGapBeforeTheStatusShowsItIsNotTheEndOfTheUpdate() throws {
    let (state, commands) = next(
      .starting(try available), on: .statusShows(running: false, waitingFor: []))
    #expect(try state == .starting(available))
    #expect(commands.isEmpty)
  }

  @Test func givingUpOnTheStartLooksAgainBeforeCallingItAFailure() throws {
    let (state, commands) = next(.starting(try available), on: .startGaveUp)
    #expect(try state == .checkingStart(available))
    #expect(commands == checkCommand)
    #expect(next(.running(waitingFor: []), on: .startGaveUp).0 == .running(waitingFor: []))
  }

  /// The grace ran out where the status cannot be read: the update is let go, with no verdict and nothing run.
  @Test func anUnwatchedStartIsLetGoWithoutAVerdict() throws {
    let (state, commands) = next(.starting(try available), on: .startUnwatched)
    #expect(state == .idle)
    #expect(commands.isEmpty)
    #expect(next(.running(waitingFor: []), on: .startUnwatched).0 == .running(waitingFor: []))
    #expect(next(UpdateState.idle, on: .startUnwatched).0 == .idle)
  }

  @Test func theSameVersionStillOfferedMeansTheUpdateNeverStarted() throws {
    let (state, commands) = next(.checkingStart(try available), on: .checked(try available, manual: false))
    #expect(state == .failed(updateNeverStartedText))
    #expect(commands.isEmpty)
  }

  @Test func anUpdateThatFinishedBetweenPollsIsNoAlarm() throws {
    // Nothing newer is on offer now: the update did what it was to
    #expect(try next(.checkingStart(available), on: .checked(current, manual: false)).0 == .idle)
    // Another release is on offer: not the one that was started, which is therefore done
    var newer = try Fixtures.text("update-check-available")
    newer = newer.replacingOccurrences(of: "v2026.11.11", with: "v2026.11.18")
    let other = try! decodeContract(UpdateCheck.self, from: newer)
    #expect(try next(.checkingStart(available), on: .checked(other, manual: false)).0 == .idle)
  }

  @Test func aLookThatCannotBeMadeCallsNothingAFailure() throws {
    // Only the same version still on offer is a failure: no look, or one with an error, is not
    #expect(try next(.checkingStart(available), on: .checkFailed("offline", manual: false)).0 == .idle)
    #expect(try next(.checkingStart(available), on: .checked(failedCheck, manual: false)).0 == .idle)
  }

  @Test func theStatusNotShowingAnUpdateChangesNothingElse() throws {
    let states: [UpdateState] = [
      .idle, .checking, .planning(try available), .available(try available), .failed("x"),
      .checkingStart(try available),
    ]
    for state in states {
      #expect(next(state, on: .statusShows(running: false, waitingFor: [])).0 == state)
    }
  }
}

@Suite struct UpdateBosunTests {
  @Test func updateBosunBeginsTheUpdateFlowFromItsCheck() throws {
    #expect(
      updateBosunStart() == FlowStart(flow: .update, state: .checking, commands: checkCommand))
  }
}

@Suite struct UpdateIgnoredEventTests {
  @Test func anEventThatMeansNothingInTheStateChangesNothing() throws {
    let nothing: [(UpdateState, UpdateEvent)] = [
      (.idle, .checked(try available, manual: true)),
      (.idle, .updateNowChosen),
      (.checking, .confirmed),
      (.planning(try available), .skipChosen),
      (.starting(try available), .cancelled),
      (.running(waitingFor: []), .checkRequested(manual: true)),
      (.failed("x"), .updateNowChosen),
    ]
    for (state, event) in nothing {
      let (after, commands) = next(state, on: event)
      #expect(after == state, "\(state) on \(event)")
      #expect(commands.isEmpty, "\(state) on \(event)")
    }
  }
}

@Suite struct UpdateGlueTests {
  @Test func aCheckThatRanBecomesTheEventForTheMachine() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(["update", "--check", "--json"], exited(0, try Fixtures.text("update-check-available")))
    #expect(try await checkEvent(via: cli, manual: true) == .checked(available, manual: true))
    #expect(await cli.calls == [["update", "--check", "--json"]])
    #expect(await cli.timeouts == [.seconds(60)])
  }

  @Test func aCheckThatFailedBecomesAFailureEvent() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(
      ["update", "--check", "--json"], CommandResult(outcome: .timedOut, stdout: "", stderr: ""))
    guard case .checkFailed(let why, let manual) = await checkEvent(via: cli, manual: false) else {
      Issue.record("expected checkFailed")
      return
    }
    #expect(!manual)
    #expect(why.contains("did not answer"))
  }

  @Test func aCheckFromANewerContractSaysTheAppNeedsAnUpdate() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(
      ["update", "--check", "--json"], exited(0, try Fixtures.text("update-check-available", contract: 2)))
    guard case .checkFailed(let why, _) = await checkEvent(via: cli, manual: true) else {
      Issue.record("expected checkFailed")
      return
    }
    #expect(why.contains("needs an update"))
  }

  @Test func aPlanThatRanBecomesTheEvent() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(["update", "--dry-run", "--json"], exited(0, try Fixtures.text("update-plan")))
    #expect(try await planEvent(via: cli) == .planned(plan))
    #expect(await cli.timeouts == [.seconds(120)])
  }

  @Test func aDryRunThatMadeNoPlanIsNoPlan() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(["update", "--dry-run", "--json"], exited(1, try Fixtures.text("update-plan-none")))
    #expect(await planEvent(via: cli) == .planned(nil))
  }

  @Test func aRefusedDryRunSaysWhy() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(
      ["update", "--dry-run", "--json"],
      exited(1, try Fixtures.text("update-plan-none"), err: "bosun update: an update is running"))
    #expect(await planEvent(via: cli) == .planFailed("bosun update: an update is running"))
  }

  @Test func aPlanThatCouldNotBeMadeIsAFailureEvent() async throws {
    let cli = FakeBosunCLI()
    await cli.reply(["update", "--dry-run", "--json"], exited(1, "", err: "bosun update: refused\nmore"))
    #expect(await planEvent(via: cli) == .planFailed("bosun update: refused"))
  }
}
