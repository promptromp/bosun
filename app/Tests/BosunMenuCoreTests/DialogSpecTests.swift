import Foundation
import Testing

@testable import BosunMenuCore

@Suite struct QuitDialogTests {
  private func plan(
    busy: [String] = [], waiting: [String] = [], keepAwake: String? = "20:00", note: String? = nil
  ) -> QuitPlan {
    QuitPlan(
      contract: 1, windows: ["acme-orchestrator", "acme-exporter"], busy: busy, waiting: waiting,
      keepAwakeUntil: keepAwake, refused: nil, note: note, waitMinutes: 10, lockWaitSeconds: 180,
      stepsSeconds: 225)
  }

  @Test func theFirstDialogShowsWhatQuittingWouldMeet() throws {
    let spec = quitFirstSpec(plan(busy: ["acme-exporter"], waiting: ["acme-web"]))
    #expect(spec.title == "Quit bosun?")
    #expect(spec.message.contains("2 windows open: acme-orchestrator, acme-exporter"))
    #expect(spec.message.contains("Busy: acme-exporter"))
    #expect(spec.message.contains("Waiting for you (quit stops them): acme-web"))
    #expect(spec.message.contains("The Mac is kept awake until 20:00"))
  }

  @Test func theFirstDialogsCheckboxesAndTheirDefaults() throws {
    let spec = quitFirstSpec(plan())
    #expect(spec.boxes.map(\.id) == ["pauseMorning", "sleep", "keepIcon"])
    #expect(
      spec.boxes.map(\.title) == [
        "Pause the morning start until I open bosun again", "Let this Mac sleep now",
        "Keep bosun in the menu bar",
      ])
    // The morning pause and the sleep are on, the icon is kept only when asked
    #expect(spec.boxes.map(\.on) == [true, true, false])
    #expect(spec.buttons == ["Quit bosun", "Cancel"])
    #expect(spec.cancel == 1)
  }

  /// The checkboxes start at the choices a quit has by default, the one place they are written down.
  @Test func theBoxesStartAtTheDefaultChoices() throws {
    let defaults = QuitChoices()
    let spec = quitFirstSpec(plan())
    #expect(spec.boxes.first { $0.id == "pauseMorning" }?.on == defaults.pauseMorning)
    #expect(spec.boxes.first { $0.id == "sleep" }?.on == defaults.sleep)
    #expect(spec.boxes.first { $0.id == "keepIcon" }?.on == defaults.keepIcon)
  }

  @Test func theSleepBoxIsOnlyOfferedWhileTheMacIsKeptAwake() throws {
    let spec = quitFirstSpec(plan(keepAwake: nil))
    #expect(spec.boxes.map(\.id) == ["pauseMorning", "keepIcon"])
  }

  @Test func theNoteOfAPlanThatCouldNotReadTheWindowsIsShown() throws {
    let spec = quitFirstSpec(plan(note: "the windows could not be read, so none is listed"))
    #expect(spec.message.contains("the windows could not be read"))
  }

  @Test func theBoxesAnsweredBecomeTheChoices() throws {
    let answer = DialogAnswer(button: 0, boxes: ["pauseMorning": false, "sleep": false, "keepIcon": true])
    let choices = QuitChoices(answer: answer)
    #expect(choices == QuitChoices(pauseMorning: false, sleep: false, keepIcon: true))
  }

  /// With no sleep box on the dialog, the choice is the default, never "off".
  @Test func aBoxThatWasNotShownKeepsItsDefault() throws {
    let answer = DialogAnswer(button: 0, boxes: ["pauseMorning": true, "keepIcon": false])
    #expect(QuitChoices(answer: answer) == QuitChoices())
  }

  @Test func theBusyDialogNamesTheBusyAgents() throws {
    let spec = quitBusySpec(["acme-exporter", "acme-worker"])
    #expect(spec.message.contains("acme-exporter"))
    #expect(spec.message.contains("acme-worker"))
    #expect(spec.buttons == ["Let Them Finish", "Stop Them Now", "Cancel"])
    #expect(spec.cancel == 2)
    #expect(spec.boxes.isEmpty)
  }

  @Test func theStillBusyDialogAsksAgain() throws {
    let spec = quitStillBusySpec(["acme-exporter"])
    #expect(spec.message.contains("acme-exporter"))
    #expect(spec.buttons == ["Keep Waiting", "Stop It Now", "Cancel Quit"])
    #expect(spec.cancel == 2)
  }

  @Test func whenThePlanCouldNotLookTheReasonAStopNowAndQuittingOnlyTheAppAreOffered() throws {
    let spec = quitUnavailableSpec("the windows cannot be read")
    #expect(spec.message.contains("the windows cannot be read"))
    #expect(spec.message.contains("Quit bosun.app leaves bosun as it is"))
    // Stop Now ends every window: Escape is Cancel's
    #expect(spec.buttons == ["Cancel", "Stop Now", "Quit bosun.app"])
    #expect(spec.cancel == 0)
    // ...and there is no default button: Return does nothing
    #expect(spec.defaultButton == nil)
    #expect(spec.boxes.map(\.id) == ["pauseMorning", "sleep", "keepIcon"])
  }

  @Test func eachButtonOfEachDialogIsAnEventOfTheFlow() throws {
    let choices = QuitChoices()
    let ok = DialogAnswer(button: 0, boxes: [:])
    #expect(quitEvent(for: .first(plan()), answer: ok) == .confirmed(QuitChoices()))
    #expect(quitEvent(for: .first(plan()), answer: DialogAnswer(button: 1, boxes: [:])) == .cancelled)
    let busy = QuitStep.busy(["a"], choices)
    #expect(quitEvent(for: busy, answer: ok) == .busyChosen(.letFinish))
    #expect(quitEvent(for: busy, answer: DialogAnswer(button: 1, boxes: [:])) == .busyChosen(.stopNow))
    #expect(quitEvent(for: busy, answer: DialogAnswer(button: 2, boxes: [:])) == .cancelled)
    let still = QuitStep.stillBusy(["a"], choices)
    #expect(quitEvent(for: still, answer: ok) == .busyChosen(.letFinish))
    #expect(quitEvent(for: still, answer: DialogAnswer(button: 1, boxes: [:])) == .busyChosen(.stopNow))
    #expect(quitEvent(for: still, answer: DialogAnswer(button: 2, boxes: [:])) == .cancelled)
    let gone = QuitStep.unavailable("x", nil)
    #expect(quitEvent(for: gone, answer: ok) == .cancelled)
    #expect(quitEvent(for: gone, answer: DialogAnswer(button: 1, boxes: [:])) == .confirmed(QuitChoices()))
    #expect(quitEvent(for: gone, answer: DialogAnswer(button: 2, boxes: [:])) == .leaveApp)
  }

  @Test func aStepWithNoDialogIsCancelled() throws {
    #expect(quitEvent(for: .planning, answer: DialogAnswer(button: 0, boxes: [:])) == .cancelled)
  }
}

@Suite struct UpdateDialogTests {
  private var check: UpdateCheck {
    get throws { try Fixtures.decode(UpdateCheck.self, "update-check-available") }
  }

  @Test func theOfferNamesTheVersionsAndShowsTheNotes() throws {
    let spec = updateOfferSpec(try check)
    #expect(spec.title == "Update Available: v2026.11.11")
    #expect(spec.message.contains("v2026.11.11"))
    #expect(spec.message.contains("v2026.11.04"))
    #expect(try spec.scroll == check.latest?.notes)
    #expect(spec.buttons == ["Update Now", "Remind Me Later", "Skip This Version"])
    #expect(spec.cancel == 1)
  }

  @Test func anOfferWithNoNotesSaysSo() throws {
    let bare = UpdateCheck(
      contract: 1, channel: "releases", installed: InstalledVersion(version: nil, commit: "1111111abcdef"),
      latest: LatestVersion(version: "v2026.11.11", commit: "2222", notes: "  \n"), available: true,
      skipped: false,
      ahead: false, error: nil)
    let spec = updateOfferSpec(bare)
    #expect(spec.scroll == "No notes came with this release.")
    #expect(spec.message.contains("1111111"))  // an install with no version shows its commit
  }

  @Test func theAnswerIsAnEventOfTheFlow() throws {
    func event(_ button: Int) -> UpdateEvent {
      updateOfferEvent(answer: DialogAnswer(button: button, boxes: [:]))
    }
    #expect(event(0) == .updateNowChosen)
    #expect(event(1) == .remindLater)
    #expect(event(2) == .skipChosen)
  }

  @Test func thePlanShowsWhatRestarts() throws {
    let plan = try Fixtures.plan()
    let spec = updatePlanSpec(plan, check: try check)
    #expect(spec.title == "Update bosun to v2026.11.11?")
    #expect(
      spec.message.contains("These windows restart: acme-api, acme-exporter, acme-worker, acme-docs, checkin")
    )
    #expect(spec.message.contains("These ask you first: acme-web"))
    #expect(spec.message.contains("Handed tasks keep their old text: task:acme/exporter#7"))
    #expect(
      spec.message.contains(
        "Change these of your own files yourself: sessions.yml: add connectors: to the exporter entry"))
    #expect(!spec.message.contains("Blocked"))
    #expect(spec.scroll?.contains("Repo windows on a second agent") == true)
    #expect(spec.buttons == ["Update", "Cancel"])
    #expect(spec.cancel == 1)
  }

  @Test func aPlanThatRestartsNothingSaysSo() throws {
    let quiet = UpdatePlan(
      fromCommit: "a", toCommit: "b", commits: [],
      windows: [PlannedWindow(name: "acme-notes", treatment: .leave)],
      handed: [], userChanges: [], blockers: [])
    let spec = updatePlanSpec(quiet, check: try check)
    #expect(spec.message.contains("No window restarts."))
    #expect(spec.scroll == nil)
  }

  @Test func aBlockedPlanSaysWhyAndOnlyCloses() throws {
    let spec = updatePlanSpec(try Fixtures.plan("update-plan-blocked"), check: try check)
    #expect(spec.title == "bosun cannot update now")
    #expect(spec.message.contains("a migration of the knowledge store is missing"))
    #expect(spec.buttons == ["OK"])
  }

  @Test func yesAndNoOnThePlan() throws {
    let plan = try Fixtures.plan()
    #expect(updatePlanEvent(plan: plan, answer: DialogAnswer(button: 0, boxes: [:])) == .confirmed)
    #expect(updatePlanEvent(plan: plan, answer: DialogAnswer(button: 1, boxes: [:])) == .cancelled)
  }

  @Test func okOnABlockedPlanIsACancel() throws {
    let blocked = try Fixtures.plan("update-plan-blocked")
    #expect(updatePlanEvent(plan: blocked, answer: DialogAnswer(button: 0, boxes: [:])) == .cancelled)
  }

  @Test func upToDateSaysWhichVersion() throws {
    let current = try Fixtures.decode(UpdateCheck.self, "update-check-current")
    let spec = upToDateSpec(current)
    #expect(spec.title == "bosun is up to date")
    #expect(spec.message.contains(try #require(current.installed.version)))
    #expect(spec.buttons == ["OK"])
  }

  @Test func aVersionWithNoTagIsItsShortCommitAndOneWithNeitherSaysSo() throws {
    let check = try Fixtures.decode(UpdateCheck.self, "update-check-current")
    let untagged = UpdateCheck(
      contract: 1, channel: "main", installed: InstalledVersion(version: nil, commit: check.installed.commit),
      latest: check.latest, available: false, skipped: false, ahead: false, error: nil)
    #expect(upToDateSpec(untagged).message == "You have 2222222.")
    let unread = try Fixtures.decode(UpdateCheck.self, "update-check-failed-no-commit")
    #expect(unread.installed.commit == nil)
    #expect(upToDateSpec(unread).message == "You have a version bosun could not read.")
  }
}

@Suite struct DefaultButtonTests {
  @Test func everyOtherDialogsFirstButtonIsItsDefault() throws {
    let plan = QuitPlan(
      contract: 1, windows: [], busy: [], waiting: [], keepAwakeUntil: nil, refused: nil, note: nil,
      waitMinutes: nil, lockWaitSeconds: nil, stepsSeconds: nil)
    let specs = [
      quitFirstSpec(plan), quitBusySpec(["a"]), quitStillBusySpec(["a"]), quitNoteSpec("n"),
      failureSpec("t", "m"), problemsSpec([]),
    ]
    for spec in specs { #expect(spec.defaultButton == 0, "\(spec.title)") }
  }

  @Test func aSpecSaysItsDefaultWithoutDefaultingTheCancelButton() throws {
    let spec = DialogSpec(
      title: "t", message: "m", scroll: nil, boxes: [], buttons: ["Cancel", "Go"], cancel: 0)
    #expect(spec.defaultButton == 0)
    let none = DialogSpec(
      title: "t", message: "m", scroll: nil, boxes: [], buttons: ["Cancel", "Go"], cancel: 0,
      defaultButton: nil)
    #expect(none.defaultButton == nil)
  }
}

@Suite struct OtherDialogTests {
  @Test func aFailureShowsItsReason() throws {
    let spec = failureSpec("Cannot open the console", "bosun did not answer in time")
    #expect(spec.title == "Cannot open the console")
    #expect(spec.message == "bosun did not answer in time")
    #expect(spec.buttons == ["OK"])
    #expect(spec.style == .warning)
  }

  @Test func theProblemsAreListedInTheScrollingText() throws {
    let spec = problemsSpec(["watchdog has not run for 40 minutes", "acme-api could not start"])
    #expect(spec.title == "What's Wrong")
    #expect(spec.scroll == "watchdog has not run for 40 minutes\nacme-api could not start")
    #expect(spec.buttons == ["OK"])
  }

  @Test func noProblemsSaysSo() throws {
    let spec = problemsSpec([])
    #expect(spec.scroll == nil)
    #expect(spec.message.contains("nothing"))
  }

  @Test func aQuitNoteIsShownAsItIs() throws {
    let spec = quitNoteSpec("A console is still running on port 8765.")
    #expect(spec.title == "bosun is stopped")
    #expect(spec.message == "A console is still running on port 8765.")
    #expect(spec.buttons == ["OK"])
  }

  @Test func aDialogsDefaultAnswerIsItsCancelButtonOrItsLast() throws {
    let spec = quitFirstSpec(quitPlan(windows: []))
    #expect(spec.defaultAnswer == DialogAnswer(button: 1, boxes: ["pauseMorning": true, "keepIcon": false]))
    #expect(failureSpec("t", "m").defaultAnswer == DialogAnswer(button: 0, boxes: [:]))
  }
}
