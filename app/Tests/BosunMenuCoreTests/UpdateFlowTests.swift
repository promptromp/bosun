import Testing

@testable import BosunMenuCore

private let available = Fixtures.decode(UpdateCheck.self, "update-check-available")
private let current = Fixtures.decode(UpdateCheck.self, "update-check-current")
private let skipped = Fixtures.decode(UpdateCheck.self, "update-check-skipped")
private let failedCheck = Fixtures.decode(UpdateCheck.self, "update-check-failed")
private let plan = Fixtures.plan()
private let blocked = Fixtures.plan("update-plan-blocked")

private let checkCommand = [Bosun.checkUpdates]

@Suite struct UpdateCheckTransitionTests {
  @Test func checkingStartsFromIdleAndAfterAFailure() {
    for state in [UpdateState.idle, .failed("earlier")] {
      let (after, commands) = next(state, on: .checkRequested(manual: true))
      #expect(after == .checking)
      #expect(commands == checkCommand)
    }
  }

  @Test func theCheckIsTheJSONCommandWithItsSixtySeconds() {
    #expect(Bosun.checkUpdates.args == ["update", "--check", "--json"])
    #expect(Bosun.checkUpdates.timeout == .seconds(60))
    #expect(!Bosun.checkUpdates.detached)
  }

  @Test func anAvailableUpdateIsOfferedWhetherOrNotAskedFor() {
    for manual in [true, false] {
      let (state, commands) = next(.checking, on: .checked(available, manual: manual))
      #expect(state == .available(available))
      #expect(commands.isEmpty)
    }
  }

  @Test func aSkippedVersionIsOfferedOnlyToWhoAsks() {
    #expect(next(.checking, on: .checked(skipped, manual: true)).0 == .available(skipped))
    #expect(next(.checking, on: .checked(skipped, manual: false)).0 == .idle)
  }

  @Test func nothingNewGoesBackToIdle() {
    #expect(next(.checking, on: .checked(current, manual: true)).0 == .idle)
    #expect(next(.checking, on: .checked(current, manual: false)).0 == .idle)
  }

  @Test func aCheckThatSaysItFailedFailsOnlyWhenAskedFor() {
    #expect(
      next(.checking, on: .checked(failedCheck, manual: true)).0
        == .failed(failedCheck.error ?? ""))
    #expect(next(.checking, on: .checked(failedCheck, manual: false)).0 == .idle)
  }

  @Test func aCheckThatCouldNotRunFailsOnlyWhenAskedFor() {
    #expect(next(.checking, on: .checkFailed("timed out", manual: true)).0 == .failed("timed out"))
    #expect(next(.checking, on: .checkFailed("timed out", manual: false)).0 == .idle)
  }

  @Test func aFailureIsAcknowledgedBackToIdle() {
    let (state, commands) = next(.failed("x"), on: .acknowledged)
    #expect(state == .idle)
    #expect(commands.isEmpty)
  }

  @Test func anOfferedUpdateIsNotRecheckedUnderTheMenu() {
    let state = UpdateState.available(available)
    let (after, commands) = next(state, on: .checkRequested(manual: false))
    #expect(after == state)
    #expect(commands.isEmpty)
  }
}

@Suite struct UpdateOfferTransitionTests {
  @Test func updateNowAsksTheDryRunForItsPlan() {
    let (state, commands) = next(.available(available), on: .updateNowChosen)
    #expect(state == .planning)
    #expect(commands == [Bosun.planUpdate])
    #expect(Bosun.planUpdate.args == ["update", "--dry-run", "--json"])
    #expect(Bosun.planUpdate.timeout == .seconds(30))
  }

  @Test func skippingRecordsTheVersionAndGoesIdle() {
    let (state, commands) = next(.available(available), on: .skipChosen)
    #expect(state == .idle)
    #expect(commands.map(\.args) == [["update", "--skip", "v2026.11.11"]])
  }

  @Test func remindMeLaterLeavesTheOfferWhereItIs() {
    let (state, commands) = next(.available(available), on: .remindLater)
    #expect(state == .available(available))
    #expect(commands.isEmpty)
  }
}

@Suite struct UpdatePlanTransitionTests {
  @Test func aPlanIsShownForConfirmation() {
    let (state, commands) = next(.planning, on: .planned(plan))
    #expect(state == .confirming(plan))
    #expect(commands.isEmpty)
  }

  @Test func aBlockedPlanIsShownToo() {
    #expect(next(.planning, on: .planned(blocked)).0 == .confirming(blocked))
  }

  @Test func noPlanMeansNothingToUpdateOrARefusal() {
    let (state, _) = next(.planning, on: .planned(nil))
    guard case .failed(let why) = state else {
      Issue.record("expected a failure")
      return
    }
    #expect(why.contains("no plan"))
  }

  @Test func aPlanThatCouldNotBeMadeFails() {
    #expect(next(UpdateState.planning, on: UpdateEvent.planFailed("timed out")).0 == .failed("timed out"))
  }

  @Test func yesRunsTheUpdateDetached() {
    let (state, commands) = next(.confirming(plan), on: .confirmed)
    #expect(state == .starting)
    #expect(commands == [Bosun.update])
    #expect(Bosun.update.args == ["update", "--yes"])
    #expect(Bosun.update.detached)
  }

  @Test func yesToABlockedPlanDoesNothing() {
    let state = UpdateState.confirming(blocked)
    let (after, commands) = next(state, on: .confirmed)
    #expect(after == state)
    #expect(commands.isEmpty)
  }

  @Test func cancellingLooksAgainSoTheOfferComesBack() {
    let (state, commands) = next(.confirming(plan), on: .cancelled)
    #expect(state == .checking)
    #expect(commands == checkCommand)
  }
}

@Suite struct UpdateRunningTransitionTests {
  @Test func theStatusShowingTheUpdateMakesItRunningWithWhatItWaitsFor() {
    let (state, _) = next(.starting, on: .statusShows(running: true, waitingFor: ["acme-web"]))
    #expect(state == .running(waitingFor: ["acme-web"]))
    let (later, _) = next(state, on: .statusShows(running: true, waitingFor: []))
    #expect(later == .running(waitingFor: []))
  }

  @Test func anUpdateStartedElsewhereIsShownToo() {
    for state in [UpdateState.idle, .checking, .failed("x"), .available(available)] {
      let (after, commands) = next(state, on: .statusShows(running: true, waitingFor: ["a"]))
      #expect(after == .running(waitingFor: ["a"]))
      #expect(commands.isEmpty)
    }
  }

  @Test func anUpdateBeingPlannedIsNotInterruptedByTheStatus() {
    for state in [UpdateState.planning, .confirming(plan)] {
      #expect(next(state, on: .statusShows(running: true, waitingFor: [])).0 == state)
    }
  }

  @Test func whenTheUpdateEndsTheAppLooksAgain() {
    let (state, commands) = next(
      .running(waitingFor: ["a"]), on: .statusShows(running: false, waitingFor: []))
    #expect(state == .checking)
    #expect(commands == checkCommand)
  }

  @Test func theGapBeforeTheStatusShowsItIsNotTheEndOfTheUpdate() {
    let (state, commands) = next(.starting, on: .statusShows(running: false, waitingFor: []))
    #expect(state == .starting)
    #expect(commands.isEmpty)
  }

  @Test func anUpdateThatNeverShowsFails() {
    let (state, _) = next(.starting, on: .startGaveUp)
    guard case .failed(let why) = state else {
      Issue.record("expected a failure")
      return
    }
    #expect(why.contains("bosun update"))
    #expect(next(.running(waitingFor: []), on: .startGaveUp).0 == .running(waitingFor: []))
  }

  @Test func theStatusNotShowingAnUpdateChangesNothingElse() {
    for state in [UpdateState.idle, .checking, .planning, .available(available), .failed("x")] {
      #expect(next(state, on: .statusShows(running: false, waitingFor: [])).0 == state)
    }
  }
}

@Suite struct UpdateIgnoredEventTests {
  @Test func anEventThatMeansNothingInTheStateChangesNothing() {
    let nothing: [(UpdateState, UpdateEvent)] = [
      (.idle, .checked(available, manual: true)),
      (.idle, .updateNowChosen),
      (.checking, .confirmed),
      (.planning, .skipChosen),
      (.starting, .cancelled),
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
  @Test func aCheckThatRanBecomesTheEventForTheMachine() async {
    let cli = FakeBosunCLI()
    await cli.reply(["update", "--check", "--json"], exited(0, Fixtures.text("update-check-available")))
    #expect(await checkEvent(via: cli, manual: true) == .checked(available, manual: true))
    #expect(await cli.calls == [["update", "--check", "--json"]])
    #expect(await cli.timeouts == [.seconds(60)])
  }

  @Test func aCheckThatFailedBecomesAFailureEvent() async {
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

  @Test func aCheckFromANewerContractSaysTheAppNeedsAnUpdate() async {
    let cli = FakeBosunCLI()
    await cli.reply(
      ["update", "--check", "--json"], exited(0, Fixtures.text("update-check-available", contract: 2)))
    guard case .checkFailed(let why, _) = await checkEvent(via: cli, manual: true) else {
      Issue.record("expected checkFailed")
      return
    }
    #expect(why.contains("needs an update"))
  }

  @Test func aPlanThatRanBecomesTheEvent() async {
    let cli = FakeBosunCLI()
    await cli.reply(["update", "--dry-run", "--json"], exited(0, Fixtures.text("update-plan")))
    #expect(await planEvent(via: cli) == .planned(plan))
    #expect(await cli.timeouts == [.seconds(30)])
  }

  @Test func aDryRunThatMadeNoPlanIsNoPlan() async {
    let cli = FakeBosunCLI()
    await cli.reply(["update", "--dry-run", "--json"], exited(1, Fixtures.text("update-plan-none")))
    #expect(await planEvent(via: cli) == .planned(nil))
  }

  @Test func aRefusedDryRunSaysWhy() async {
    let cli = FakeBosunCLI()
    await cli.reply(
      ["update", "--dry-run", "--json"],
      exited(1, Fixtures.text("update-plan-none"), err: "bosun update: an update is running"))
    #expect(await planEvent(via: cli) == .planFailed("bosun update: an update is running"))
  }

  @Test func aPlanThatCouldNotBeMadeIsAFailureEvent() async {
    let cli = FakeBosunCLI()
    await cli.reply(["update", "--dry-run", "--json"], exited(1, "", err: "bosun update: refused\nmore"))
    #expect(await planEvent(via: cli) == .planFailed("bosun update: refused"))
  }
}
