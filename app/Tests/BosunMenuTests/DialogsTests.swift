import AppKit
import Testing

@testable import BosunMenu
@testable import BosunMenuCore

private func quitPlan(busy: [String] = ["acme-exporter"], keepAwake: String? = "20:00") -> QuitPlan {
  QuitPlan(
    contract: 1, windows: ["acme-orchestrator", "acme-exporter", "acme-web"], busy: busy,
    waiting: ["acme-web"],
    keepAwakeUntil: keepAwake, refused: nil, note: nil, waitMinutes: 10, lockWaitSeconds: 180,
    stepsSeconds: 225)
}

private func updatePlan() -> UpdatePlan {
  let json = """
    {"contract": 1,
     "plan": {"from_commit": "a", "to_commit": "b", "commits": ["Hardening", "Repo windows on a second agent"],
     "windows": [["acme-api", "restart"], ["acme-exporter", "take stock, then restart"], ["acme-web", "ask first"],
                 ["acme-notes", "leave"]],
     "handed": ["task:acme/exporter#7"], "user_changes": [], "blockers": []}}
    """
  return try! decodeContract(UpdateDryRun.self, from: json).plan!
}

private func check() -> UpdateCheck {
  try! decodeContract(
    UpdateCheck.self,
    from: """
      {"contract": 1, "channel": "releases", "installed": {"version": "v2026.11.04", "commit": "a"},
       "latest": {"version": "v2026.11.11", "commit": "b", "notes": "- Hardening"}, "available": true,
       "skipped": false, "ahead": false, "error": null}
      """)
}

@MainActor
private func checkboxes(_ built: BuiltAlert) -> [NSButton] {
  built.boxes.map(\.button)
}

extension NSView {
  var allSubviews: [NSView] { subviews + subviews.flatMap(\.allSubviews) }
}

@MainActor @Suite struct QuitAlertTests {
  @Test func quitAlertDefaults() {
    let built = Dialogs.build(quitFirstSpec(quitPlan()))
    let boxes = checkboxes(built)
    #expect(
      boxes.map(\.title) == [
        "Pause the morning start until I open bosun again", "Let this Mac sleep now",
        "Keep bosun in the menu bar",
      ])
    // On by default: the morning pause and the sleep; the icon is kept only when you say
    #expect(boxes.map(\.state) == [.on, .on, .off])
    #expect(built.alert.buttons.map(\.title) == ["Quit bosun", "Cancel"])
    #expect(built.alert.buttons[0].keyEquivalent == "\r")
    #expect(built.alert.buttons[1].keyEquivalent == "\u{1b}")
    #expect(built.alert.messageText == "Quit bosun?")
  }

  @Test func quitAlertNamesWhatItWouldMeet() {
    let built = Dialogs.build(quitFirstSpec(quitPlan()))
    #expect(
      built.alert.informativeText.contains("3 windows open: acme-orchestrator, acme-exporter, acme-web"))
    #expect(built.alert.informativeText.contains("The Mac is kept awake until 20:00"))
  }

  @Test func theSleepBoxIsNotThereWhenTheMacIsNotKeptAwake() {
    let built = Dialogs.build(quitFirstSpec(quitPlan(keepAwake: nil)))
    #expect(
      checkboxes(built).map(\.title) == [
        "Pause the morning start until I open bosun again", "Keep bosun in the menu bar",
      ])
  }

  @Test func quitAlertNamesBusyAgents() {
    let built = Dialogs.build(quitBusySpec(["acme-exporter", "acme-worker"]))
    #expect(built.alert.informativeText.contains("acme-exporter"))
    #expect(built.alert.informativeText.contains("acme-worker"))
    #expect(built.alert.buttons.map(\.title) == ["Let Them Finish", "Stop Them Now", "Cancel"])
    #expect(built.alert.buttons[2].keyEquivalent == "\u{1b}")
    #expect(built.alert.accessoryView == nil)
  }

  @Test func theAnswerReadsTheButtonAndTheBoxesAsTheyAre() {
    let built = Dialogs.build(quitFirstSpec(quitPlan()))
    let boxes = checkboxes(built)
    boxes[0].state = .off
    boxes[2].state = .on
    let answer = Dialogs.answer(built, response: .alertFirstButtonReturn)
    #expect(
      answer == DialogAnswer(button: 0, boxes: ["pauseMorning": false, "sleep": true, "keepIcon": true]))
    let cancel = Dialogs.answer(built, response: .alertSecondButtonReturn)
    #expect(cancel.button == 1)
  }

  @Test func aTestHookAnswerSetsTheBoxesAndGivesTheResponse() {
    let built = Dialogs.build(quitFirstSpec(quitPlan()))
    let hook = HookAnswer(button: "Quit bosun", boxes: ["sleep": false, "keepIcon": true])
    let response = Dialogs.apply(hook, to: built)
    #expect(response == .alertFirstButtonReturn)
    #expect(checkboxes(built).map(\.state) == [.on, .off, .on])
    let cancel = Dialogs.apply(HookAnswer(button: "Cancel", boxes: [:]), to: built)
    #expect(cancel == .alertSecondButtonReturn)
  }

  @Test func aTestHookAnswerForAButtonThatIsNotThereChangesNothing() {
    let built = Dialogs.build(quitFirstSpec(quitPlan()))
    let response = Dialogs.apply(HookAnswer(button: "Shred", boxes: ["sleep": false]), to: built)
    #expect(response == nil)
    #expect(checkboxes(built).map(\.state) == [.on, .on, .off])
  }
}

/// What `stopModal` was told, in a test.
@MainActor
final class StoppedWith {
  var responses: [NSApplication.ModalResponse] = []
}

@MainActor @Suite struct KeyEquivalentTests {
  private func keys(_ built: BuiltAlert) -> [String] { built.alert.buttons.map(\.keyEquivalent) }

  /// A destructive action has no default button: Return does nothing, Escape cancels, Stop Now and Quit
  /// bosun.app have no key.
  @Test func theStopNowDialogHasNoDefaultButton() {
    let built = Dialogs.build(quitUnavailableSpec("the windows cannot be read"))
    #expect(built.alert.buttons.map(\.title) == ["Cancel", "Stop Now", "Quit bosun.app"])
    #expect(keys(built) == ["\u{1b}", "", ""])
    #expect(!keys(built).contains("\r"))
  }

  @Test func theFirstButtonIsTheDefaultWhenTheSpecSaysSo() {
    let built = Dialogs.build(quitFirstSpec(quitPlan()))
    #expect(keys(built) == ["\r", "\u{1b}"])
    #expect(keys(Dialogs.build(failureSpec("t", "m"))) == ["\r"])
  }

  /// A button titled Cancel that comes first is not Return's unless the spec says so, and not the default
  /// by its place: the spec names each key.
  @Test func aCancelTitledFirstButtonLosesNothingAndGainsNothing() {
    let kept = DialogSpec(
      title: "t", message: "m", scroll: nil, boxes: [], buttons: ["Cancel", "Go"], cancel: 0, defaultButton: 0
    )
    #expect(keys(Dialogs.build(kept)) == ["\r", ""])
    let none = DialogSpec(
      title: "t", message: "m", scroll: nil, boxes: [], buttons: ["Cancel", "Go"], cancel: nil,
      defaultButton: nil)
    #expect(keys(Dialogs.build(none)) == ["", ""])
    let second = DialogSpec(
      title: "t", message: "m", scroll: nil, boxes: [], buttons: ["Go", "Cancel"], cancel: nil,
      defaultButton: 1)
    #expect(keys(Dialogs.build(second)) == ["", "\r"])
  }
}

@MainActor @Suite struct AnswerWakeTests {
  private func presenterAnswering(_ codes: StoppedWith) -> AlertPresenter {
    let presenter = AlertPresenter { codes.responses.append($0) }
    presenter.current = Dialogs.build(quitFirstSpec(quitPlan()))
    return presenter
  }

  /// The loop may not have woken by the next tick: the dialog was answered, a second answer must find none,
  /// not stop the loop again with another code.
  @Test func anAnsweredDialogTakesNoSecondAnswer() {
    let codes = StoppedWith()
    let presenter = presenterAnswering(codes)
    #expect(presenter.answerOpenDialog(HookAnswer(button: "Quit bosun", boxes: [:])))
    #expect(!presenter.hasOpenDialog)
    #expect(!presenter.answerOpenDialog(HookAnswer(button: "Cancel", boxes: [:])))
    #expect(codes.responses == [.alertFirstButtonReturn])
  }

  @Test func anAnswerTheDialogCannotTakeLeavesItOpen() {
    let codes = StoppedWith()
    let presenter = presenterAnswering(codes)
    #expect(!presenter.answerOpenDialog(HookAnswer(button: "Shred", boxes: [:])))
    #expect(presenter.hasOpenDialog)
    #expect(codes.responses.isEmpty)
  }

  /// `stopModal` from a timer ends the modal loop only once an event is taken; this is the event posted so a
  /// VM that nobody touches has one. (Whether the loop then ends is Task 5's to see: showing a dialog is not
  /// something a headless test can do.)
  @Test func theEventThatWakesTheModalLoopIsAnApplicationDefinedOne() {
    #expect(Dialogs.wakeEvent()?.type == .applicationDefined)
  }

  @Test func withNoDialogOpenAnAnswerHasNoTakerAndPostsNothing() {
    let presenter = AlertPresenter()
    #expect(!presenter.hasOpenDialog)
    #expect(!presenter.answerOpenDialog(HookAnswer(button: "OK", boxes: [:])))
  }
}

@MainActor @Suite struct UpdateAlertTests {
  @Test func updateAlertShowsWhatRestarts() {
    let built = Dialogs.build(updatePlanSpec(updatePlan(), check: check()))
    #expect(built.alert.messageText == "Update bosun to v2026.11.11?")
    #expect(built.alert.informativeText.contains("These windows restart: acme-api, acme-exporter"))
    #expect(built.alert.informativeText.contains("These ask you first: acme-web"))
    #expect(!built.alert.informativeText.contains("acme-notes"))
    #expect(built.alert.buttons.map(\.title) == ["Update", "Cancel"])
    // What is new, in a scrolling text view
    let text = built.scrollText
    #expect(text?.string.contains("Repo windows on a second agent") == true)
    #expect(text?.isEditable == false)
    #expect(built.alert.accessoryView?.allSubviews.contains { $0 is NSScrollView } == true)
  }

  @Test func theOfferShowsTheNotesInAScrollingTextView() {
    let built = Dialogs.build(updateOfferSpec(check()))
    #expect(built.alert.messageText == "Update Available: v2026.11.11")
    #expect(built.alert.buttons.map(\.title) == ["Update Now", "Remind Me Later", "Skip This Version"])
    #expect(built.scrollText?.string == "- Hardening")
    #expect(built.scrollText?.isSelectable == true)
  }

  @Test func theBlockedPlanOnlyHasOK() {
    let json = """
      {"contract": 1, "plan": {"from_commit": "a", "to_commit": "b", "commits": [], "windows": [], "handed": [],
       "user_changes": [], "blockers": ["a migration is missing"]}}
      """
    let plan = try! decodeContract(UpdateDryRun.self, from: json).plan!
    let built = Dialogs.build(updatePlanSpec(plan, check: check()))
    #expect(built.alert.buttons.map(\.title) == ["OK"])
    #expect(built.alert.informativeText.contains("a migration is missing"))
  }
}

@MainActor @Suite struct PlainAlertTests {
  @Test func aFailureIsAWarningWithOK() {
    let built = Dialogs.build(failureSpec("Cannot open the console", "bosun is not installed"))
    #expect(built.alert.alertStyle == .warning)
    #expect(built.alert.buttons.map(\.title) == ["OK"])
    #expect(built.alert.accessoryView == nil)
  }

  @Test func theProblemsAreInAScrollingTextView() {
    let built = Dialogs.build(problemsSpec(["one", "two"]))
    #expect(built.scrollText?.string == "one\ntwo")
  }

  @Test func theAlertFitsItsAccessory() {
    let built = Dialogs.build(quitFirstSpec(quitPlan()))
    let size = built.alert.accessoryView?.frame.size
    #expect((size?.width ?? 0) > 100)
    #expect((size?.height ?? 0) > 20)
  }
}
