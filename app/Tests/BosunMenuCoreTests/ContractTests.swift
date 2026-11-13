import Testing

@testable import BosunMenuCore

@Suite struct StatusContractTests {
  static let names = [
    "status-ok", "status-warning", "status-error", "status-stopped", "status-quit-unfinished",
    "status-resting", "status-update-running",
  ]

  @Test(arguments: names) func everyStatusFixtureDecodes(name: String) throws {
    let status = try Fixtures.status(name)
    #expect(status.contract == 1)
    #expect(!status.sentence.isEmpty)
    #expect(status.logs == "/Users/me/.local/state/bosun/logs")
  }

  @Test func theLevelsAreTheOnesBosunSays() throws {
    let levels = try Self.names.map { try Fixtures.status($0).level }
    #expect(levels == [.ok, .warning, .error, .stopped, .error, .ok, .ok])
  }

  @Test func aRunningBosunWithAWindowWaitingAndAKeepAwake() throws {
    let status = try Fixtures.status("status-ok")
    #expect(status.sentence == "bosun is running")
    #expect(status.windows == 3)
    #expect(status.waiting == ["acme-web"])
    #expect(status.keepAwakeUntil == "20:00")
    #expect(status.stopped == nil)
    #expect(status.update == UpdateProgress(state: .none, waitingFor: []))
    #expect(status.console == ConsoleStatus(running: false, url: nil))
    #expect(status.detail == ["3 windows open", "1 waiting for you: acme-web", "Mac kept awake until 20:00"])
  }

  @Test func aWarningListsItsProblems() throws {
    let status = try Fixtures.status("status-warning")
    #expect(status.sentence == "bosun is running, with a warning")
    #expect(status.problems == ["The acme-web window has waited at a prompt for 45 min."])
  }

  @Test func aQuitLeavesTheStoppedFields() throws {
    let status = try Fixtures.status("status-stopped")
    #expect(status.stopped == Stopped(since: "2026-11-11T09:30:00+00:00", morningPaused: true))
    #expect(status.keepAwakeUntil == nil)
  }

  @Test func aQuitThatDidNotFinishIsAnErrorWithTheWayOut() throws {
    let status = try Fixtures.status("status-quit-unfinished")
    #expect(status.sentence == "bosun quit did not finish")
    #expect(status.problems.first?.contains("bosun quit --stop-now") == true)
    #expect(status.stopped?.morningPaused == false)
  }

  @Test func anUpdateInProgressNamesTheWindowsItWaitsFor() throws {
    let status = try Fixtures.status("status-update-running")
    #expect(status.update == UpdateProgress(state: .running, waitingFor: ["acme-exporter", "acme-web"]))
    #expect(status.updateEvent == .statusShows(running: true, waitingFor: ["acme-exporter", "acme-web"]))
    #expect(try Fixtures.status("status-ok").updateEvent == .statusShows(running: false, waitingFor: []))
  }

  @Test func aContractOtherThanOneIsRefusedBeforeAnythingElseIsRead() throws {
    #expect(throws: ContractError.unsupported(2)) {
      try decodeContract(StatusSnapshot.self, from: try Fixtures.text("status-ok", contract: 2))
    }
    #expect(throws: ContractError.unsupported(7)) {
      try decodeContract(StatusSnapshot.self, from: #"{"contract": 7, "future": ["shape"]}"#)
    }
  }

  @Test func textThatIsNotTheObjectIsUnreadable() throws {
    for text in ["", "not json", "[]", #"{"contract": 1}"#, #"{"contract": "1"}"#] {
      #expect(throws: ContractError.self) { try decodeContract(StatusSnapshot.self, from: text) }
    }
    do {
      _ = try decodeContract(StatusSnapshot.self, from: "not json")
    } catch {
      #expect(error.description.contains("could not be read"))
    }
  }

  @Test func aMinorWordItDoesNotKnowDoesNotFailTheRest() throws {
    let status = try Fixtures.text("status-update-running")
      .replacingOccurrences(of: "\"state\": \"running\"", with: "\"state\": \"paused\"")
    let decoded = try decodeContract(StatusSnapshot.self, from: status)
    #expect(decoded.update.state == .unknown("paused"))
    #expect(decoded.updateEvent == .statusShows(running: false, waitingFor: ["acme-exporter", "acme-web"]))
    #expect(decoded.level == .ok)

    let quit = try Fixtures.text("quit-result-stopped-console-running")
      .replacingOccurrences(of: "still running", with: "hiding")
    #expect(try decodeContract(QuitResult.self, from: quit).console == .unknown("hiding"))
    #expect(QuitResultKind(text: "vanished") == .unknown("vanished"))
    let vanished = try Fixtures.text("quit-result-stopped")
      .replacingOccurrences(of: "\"result\": \"stopped\"", with: "\"result\": \"vanished\"")
    #expect(try decodeContract(QuitResult.self, from: vanished).result == .unknown("vanished"))
  }

  @Test func aReplyThatCannotBeReadSaysSoInOnePlainLineNeverTheDecodersDump() throws {
    let cases = [
      ("not json", "it is not the JSON bosun prints"),
      (#"{"contract": 1}"#, "it has no \"level\""),
      (
        try Fixtures.text("status-ok").replacingOccurrences(of: "\"windows\": 3", with: "\"windows\": \"3\""),
        "\"windows\" is not what was expected"
      ),
      (
        try Fixtures.text("status-ok").replacingOccurrences(of: "\"level\": \"ok\"", with: "\"level\": 3"),
        "\"level\" is not what was expected"
      ),
    ]
    for (text, line) in cases {
      do {
        _ = try decodeContract(StatusSnapshot.self, from: text)
        Issue.record("expected a refusal")
      } catch {
        #expect(error.description == "bosun's reply could not be read: " + line)
        #expect(!error.description.contains("DecodingError"))
        #expect(!error.description.contains("\n"))
      }
    }
  }

  /// A CLI that added a level is read, not refused: the level is `unknown` (shown as a warning, never green),
  /// and the rest of the status is bosun's.
  @Test func aLevelItDoesNotKnowIsReadAsUnknownNotRefused() throws {
    let text = try Fixtures.text("status-ok").replacingOccurrences(
      of: "\"level\": \"ok\"", with: "\"level\": \"purple\"")
    let status = try decodeContract(StatusSnapshot.self, from: text)
    #expect(status.level == .unknown("purple"))
    #expect(status.level.name == "purple")
    #expect(status.sentence == "bosun is running")
    #expect(!status.offersStart)
  }

  @Test func eachKnownLevelHasItsName() throws {
    for name in ["ok", "warning", "error", "stopped"] {
      #expect(Level(name: name).name == name)
      #expect(Level(name: name) != .unknown(name))
    }
  }

  @Test func runningIsReadAndMayBeNullOrAbsent() throws {
    #expect(try Fixtures.status("status-running-error").running == true)
    #expect(try Fixtures.status("status-error").running == false)
    #expect(try Fixtures.status("status-windows-unreadable").running == nil)
    let absent = try Fixtures.text("status-ok").replacingOccurrences(of: "\"running\": true,", with: "")
    #expect(try decodeContract(StatusSnapshot.self, from: absent).running == nil)
  }

  @Test func aCheckWhoseCommitCouldNotBeReadIsReadWithItsError() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-failed-no-commit")
    #expect(check.installed.commit == nil)
    #expect(check.error?.contains("cannot read the commit") == true)
  }

  /// A-15: a fixture that is not there fails the test that reads it, by a thrown error, not the run.
  @Test func aMissingFixtureIsAnErrorNotACrash() throws {
    #expect(throws: Fixtures.Missing.self) { try Fixtures.text("no-such-fixture") }
    #expect(throws: (any Error).self) { try Fixtures.status("no-such-fixture") }
  }

  @Test func theExitStatusOfACommandSampleIsTheOneItsRunReturned() throws {
    #expect(try Fixtures.exitCode("quit-result-busy") == 3)
    #expect(try Fixtures.exitCode("quit-plan-failed") == 1)
    #expect(try Fixtures.exitCode("quit-result-stopped") == 0)
    #expect(try Fixtures.reply("quit-result-busy") == exited(3, try Fixtures.text("quit-result-busy")))
  }

  @Test func theContractErrorsSayWhatIsWrong() throws {
    #expect(ContractError.unsupported(3).description.contains("needs an update"))
    #expect(ContractError.unreadable("x").description.contains("x"))
  }
}

@Suite struct UpdateContractTests {
  @Test func anAvailableUpdateHasItsVersionAndNotes() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-available")
    #expect(check.available)
    #expect(!check.skipped)
    #expect(check.latest?.version == "v2026.11.11")
    #expect(check.latest?.notes.contains("Hardening") == true)
    #expect(check.installed.version == "v2026.11.04")
    #expect(check.channel == "releases")
    #expect(check.error == nil)
  }

  @Test func aCurrentBosunHasNothingToOffer() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-current")
    #expect(!check.available)
    #expect(check.latest?.version == "v2026.11.11")
  }

  @Test func aSkippedVersionIsStillAvailableToWhoeverAsks() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-skipped")
    #expect(check.skipped)
    #expect(check.available)
  }

  @Test func aCheckThatCouldNotBeMadeSaysWhy() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-failed")
    #expect(check.error?.contains("could not reach") == true)
    #expect(check.latest == nil)
    #expect(!check.available)
  }

  @Test func theDryRunsPlanHasWhatTheDialogShows() throws {
    let plan = try Fixtures.plan()
    #expect(plan.fromCommit == String(repeating: "1", count: 40))
    #expect(plan.toCommit == String(repeating: "2", count: 40))
    #expect(plan.commits.count == 2)
    #expect(plan.isBlocked == false)
    #expect(plan.userChanges == ["sessions.yml: add connectors: to the exporter entry"])
    #expect(plan.handed == ["task:acme/exporter#7"])
    #expect(plan.windows.count == 9)
  }

  @Test func theWindowsThatRestartAreTheOnesTheUpdateTouches() throws {
    let plan = try Fixtures.plan()
    #expect(
      plan.restarting == [
        "acme-api", "acme-exporter", "acme-worker", "acme-docs", "checkin",
      ])
  }

  @Test func theWindowsThatNeedYouAreTheOnesItMustAskFirst() throws {
    #expect(try Fixtures.plan().windowsNeedingYou == ["acme-web"])
  }

  @Test func aBlockedPlanSaysWhy() throws {
    let plan = try Fixtures.plan("update-plan-blocked")
    #expect(plan.isBlocked)
    #expect(plan.blockers == ["a migration of the knowledge store is missing"])
  }

  @Test func aDryRunWithNoPlanIsNil() throws {
    #expect(try Fixtures.dryRun("update-plan-none").plan == nil)
  }

  @Test func theDryRunCarriesTheContractNumberLikeEveryOtherObject() throws {
    #expect(throws: ContractError.unsupported(2)) {
      try decodeContract(UpdateDryRun.self, from: #"{"contract": 2, "plan": null}"#)
    }
    #expect(throws: ContractError.unreadable("it has no contract number")) {
      try decodeContract(UpdateDryRun.self, from: #"{"plan": null}"#)
    }
    #expect(throws: ContractError.self) {
      try decodeContract(UpdateDryRun.self, from: "{")
    }
  }

  @Test func everyTreatmentBosunHasIsOneTheAppKnows() throws {
    let all = try Fixtures.strings("update-treatments")
    #expect(all.count == 9)
    for text in all {
      if case .unknown = Treatment(text: text) { Issue.record("the app does not know \(text)") }
    }
    #expect(Set(all.map { Treatment(text: $0) }).count == 9)
  }

  @Test func aTreatmentItDoesNotKnowNeedsYouAndIsNotARestart() throws {
    let treatment = Treatment(text: "something new")
    #expect(treatment == .unknown("something new"))
    #expect(treatment.needsYou)
    #expect(!treatment.restarts)
  }

  @Test func aWindowIsANameAndHowItIsTreated() throws {
    let text = #"{"contract": 1, "plan": {"windows": [["acme-api", "restart"]]}}"#
    let run = try decodeContract(UpdateDryRun.self, from: text)
    #expect(run.plan?.windows == [PlannedWindow(name: "acme-api", treatment: .restart)])
    #expect(run.plan?.commits == [])
    #expect(throws: ContractError.self) {
      try decodeContract(
        UpdateDryRun.self, from: #"{"contract": 1, "plan": {"windows": [["only a name"]]}}"#)
    }
  }
}

@Suite struct QuitContractTests {
  @Test func theQuitPlanTellsApartBusyAndWaiting() throws {
    let plan = try Fixtures.decode(QuitPlan.self, "quit-plan")
    #expect(plan.windows.count == 4)
    #expect(plan.busy == ["acme-exporter"])
    #expect(plan.waiting == ["acme-web"])
    #expect(plan.keepAwakeUntil == "20:00")
    #expect(plan.refused == nil)
  }

  @Test func aRefusedPlanCarriesTheReason() throws {
    let plan = try Fixtures.decode(QuitPlan.self, "quit-plan-refused")
    #expect(plan.refused?.contains("update is running") == true)
  }

  @Test func theQuitPlansLinesSayWhatQuittingWouldMeet() throws {
    let plan = try Fixtures.decode(QuitPlan.self, "quit-plan")
    #expect(
      plan.lines == [
        "4 windows open: acme-orchestrator, acme-exporter, acme-web, acme-api",
        "Busy: acme-exporter",
        "Waiting for you (quit stops them): acme-web",
        "The Mac is kept awake until 20:00",
      ])
    #expect(quitPlan(windows: ["acme-api"]).lines == ["1 window open: acme-api"])
    #expect(quitPlan(windows: []).lines == ["No windows are open"])
  }

  @Test func eachResultDecodesWithItsOwnFields() throws {
    let stopped = try Fixtures.quitResult("quit-result-stopped")
    #expect(stopped.result == .stopped)
    #expect(stopped.morningPaused == true)
    #expect(stopped.keepAwake == "released")
    #expect(stopped.console == .stopped)
    #expect(stopped.consolePort == nil)
    #expect(stopped.error == nil)
    #expect(stopped.plan.waiting == ["acme-web"])

    let still = try Fixtures.quitResult("quit-result-stopped-console-running")
    #expect(still.console == .stillRunning)
    #expect(still.consolePort == 8765)
    #expect(still.morningPaused == false)
    #expect(still.keepAwake == "kept until 20:00")

    let busy = try Fixtures.quitResult("quit-result-busy")
    #expect(busy.result == .busy)
    #expect(busy.plan.busy == ["acme-exporter"])
    #expect(busy.error == nil)
    #expect(busy.console == nil)

    let refused = try Fixtures.quitResult("quit-result-refused")
    #expect(refused.result == .refused)
    #expect(refused.error == nil)
    #expect(refused.message?.contains("update is running") == true)

    let failed = try Fixtures.quitResult("quit-result-failed")
    #expect(failed.result == .failed)
    #expect(failed.message?.contains("quit --stop-now") == true)

    let early = try Fixtures.quitResult("quit-result-failed-early")
    #expect(early.result == .failed)
    #expect(early.plan.windows == [])
    #expect(early.message?.contains("nothing was changed") == true)
  }

  @Test func aResultWithNeitherAnErrorNorARefusalHasNoMessage() throws {
    #expect(try Fixtures.quitResult("quit-result-busy").message == nil)
    #expect(try Fixtures.quitResult("quit-result-stopped").message == nil)
  }

  @Test func theConsoleOutcomeIsOneOfThreeWordsAndAnyOtherIsUnknown() throws {
    #expect(ConsoleOutcome(text: "stopped") == .stopped)
    #expect(ConsoleOutcome(text: "not running") == .notRunning)
    #expect(ConsoleOutcome(text: "still running") == .stillRunning)
    #expect(ConsoleOutcome(text: "hiding") == .unknown("hiding"))
  }

  @Test func aPlanThatCouldNotLookIsTheFailedObjectWithItsErrorAndThePlansKeys() throws {
    let failed = try Fixtures.quitResult("quit-plan-failed")
    #expect(failed.result == .failed)
    #expect(failed.message?.contains("tmux did not answer") == true)
    #expect(failed.plan.windows == [])
  }
}

@Suite struct AppContractTests {
  @Test func theInstalledApp() throws {
    let app = try Fixtures.decode(AppStatus.self, "app-status")
    #expect(app.installed == "0.1.0")
    #expect(app.wanted == "0.1.0")
    #expect(app.login)
    #expect(app.path == "/Users/me/Applications/bosun.app")
  }

  @Test func noAppInstalled() throws {
    let app = try Fixtures.decode(AppStatus.self, "app-status-not-installed")
    #expect(app.installed == nil)
    #expect(!app.login)
  }
}
