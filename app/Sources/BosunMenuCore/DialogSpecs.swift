import Foundation

// The dialogs as values: their texts, buttons and checkboxes, and what each answer means to a flow. The
// AppKit shell turns a `DialogSpec` into an `NSAlert` and an answer back into a `DialogAnswer`; nothing
// here knows AppKit, so every word is tested. A spec says which button, if any, is the default (the one
// Return presses) and which is the one Escape presses: the shell sets each button's key explicitly and leaves
// the rest to nothing, so no title's heuristic decides it.

public struct DialogBox: Equatable, Sendable {
  public let id: String
  public let title: String
  public let on: Bool

  public init(id: String, title: String, on: Bool) {
    self.id = id
    self.title = title
    self.on = on
  }
}

public enum DialogStyle: Equatable, Sendable {
  case informational, warning
}

public struct DialogSpec: Equatable, Sendable {
  public let title: String
  public let message: String
  /// Text in a scrolling, read-only text view (update notes, the commits, the problems).
  public let scroll: String?
  public let boxes: [DialogBox]
  public let buttons: [String]
  /// The index of the button Escape presses.
  public let cancel: Int?
  /// The index of the button Return presses; nil for none (a destructive action has no default button).
  public let defaultButton: Int?
  public let style: DialogStyle

  public init(
    title: String, message: String, scroll: String?, boxes: [DialogBox], buttons: [String], cancel: Int?,
    defaultButton: Int? = 0, style: DialogStyle = .informational
  ) {
    self.title = title
    self.message = message
    self.scroll = scroll
    self.boxes = boxes
    self.buttons = buttons
    self.cancel = cancel
    self.defaultButton = defaultButton
    self.style = style
  }

  /// What closing the dialog without a choice answers: its cancel button, else its last, and its boxes as
  /// they were.
  public var defaultAnswer: DialogAnswer {
    DialogAnswer(
      button: cancel ?? max(buttons.count - 1, 0),
      boxes: Dictionary(uniqueKeysWithValues: boxes.map { ($0.id, $0.on) }))
  }
}

/// A button (by its index in the spec) and the state of each box (by its id).
public struct DialogAnswer: Equatable, Sendable {
  public let button: Int
  public let boxes: [String: Bool]

  public init(button: Int, boxes: [String: Bool]) {
    self.button = button
    self.boxes = boxes
  }
}

// MARK: Quit

private enum QuitBox {
  static let pause = "pauseMorning"
  static let sleep = "sleep"
  static let keepIcon = "keepIcon"
}

private func quitBoxes(sleep: Bool) -> [DialogBox] {
  let defaults = QuitChoices()
  var boxes = [
    DialogBox(
      id: QuitBox.pause, title: "Pause the morning start until I open bosun again", on: defaults.pauseMorning)
  ]
  if sleep {
    boxes.append(DialogBox(id: QuitBox.sleep, title: "Let this Mac sleep now", on: defaults.sleep))
  }
  boxes.append(DialogBox(id: QuitBox.keepIcon, title: "Keep bosun in the menu bar", on: defaults.keepIcon))
  return boxes
}

/// The first dialog: what quitting would meet, and the person's choices. The sleep box is only there while
/// the Mac is being kept awake: with no keep-awake there is nothing for it to end.
public func quitFirstSpec(_ plan: QuitPlan) -> DialogSpec {
  DialogSpec(
    title: "Quit bosun?", message: (plan.lines + [plan.note].compactMap { $0 }).joined(separator: "\n"),
    scroll: nil, boxes: quitBoxes(sleep: plan.keepAwakeUntil != nil), buttons: ["Quit bosun", "Cancel"],
    cancel: 1)
}

/// Shown when `quit --plan` could not look (or did not answer): the reason, a Stop Now, which needs no look,
/// and Quit bosun.app, which leaves bosun as it is. Stop Now ends every window, and bosun cannot tell whether
/// agents are busy: so no button is the default (Return does nothing), and Escape cancels, as Apple's
/// guidelines allow for a destructive action.
public func quitUnavailableSpec(_ why: String) -> DialogSpec {
  DialogSpec(
    title: "bosun cannot say what quitting would stop",
    message: why
      + "\n\nStop Now ends every window without waiting for any agent. Quit bosun.app leaves bosun "
      + "as it is.",
    scroll: nil, boxes: quitBoxes(sleep: true), buttons: ["Cancel", "Stop Now", "Quit bosun.app"], cancel: 0,
    defaultButton: nil, style: .warning)
}

public func quitBusySpec(_ names: [String]) -> DialogSpec {
  DialogSpec(
    title: "Some agents are in the middle of a task",
    message: "These are busy: \(names.joined(separator: ", ")).\n\n"
      + "Quit can wait for them to finish, and asks again if they take long, or stop them now.",
    scroll: nil, boxes: [], buttons: ["Let Them Finish", "Stop Them Now", "Cancel"], cancel: 2)
}

public func quitStillBusySpec(_ names: [String]) -> DialogSpec {
  DialogSpec(
    title: "Still busy",
    message: "These are still working after the wait: \(names.joined(separator: ", ")).",
    scroll: nil, boxes: [], buttons: ["Keep Waiting", "Stop It Now", "Cancel Quit"], cancel: 2)
}

public func quitNoteSpec(_ note: String) -> DialogSpec {
  DialogSpec(
    title: "bosun is stopped", message: note, scroll: nil, boxes: [], buttons: ["OK"], cancel: nil)
}

extension QuitChoices {
  /// The choices the boxes were answered with; a box the dialog did not have keeps its default.
  public init(answer: DialogAnswer) {
    let defaults = QuitChoices()
    self.init(
      pauseMorning: answer.boxes[QuitBox.pause] ?? defaults.pauseMorning,
      sleep: answer.boxes[QuitBox.sleep] ?? defaults.sleep,
      keepIcon: answer.boxes[QuitBox.keepIcon] ?? defaults.keepIcon)
  }
}

/// An answer to the dialog of a quit step, as the event of the flow.
public func quitEvent(for step: QuitStep, answer: DialogAnswer) -> QuitEvent {
  switch step {
  case .first:
    return answer.button == 0 ? .confirmed(QuitChoices(answer: answer)) : .cancelled
  case .unavailable:
    switch answer.button {
    case 1: return .confirmed(QuitChoices(answer: answer))
    case 2: return .leaveApp
    default: return .cancelled
    }
  case .busy, .stillBusy:
    switch answer.button {
    case 0: return .busyChosen(.letFinish)
    case 1: return .busyChosen(.stopNow)
    default: return .cancelled
    }
  default:
    return .cancelled
  }
}

// MARK: Update

private func versionLabel(_ installed: InstalledVersion) -> String {
  installed.version ?? installed.commit.map { String($0.prefix(7)) } ?? "a version bosun could not read"
}

public func updateOfferSpec(_ check: UpdateCheck) -> DialogSpec {
  let version = check.latest?.version ?? "a new version"
  let notes = (check.latest?.notes ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
  return DialogSpec(
    title: "Update Available: \(version)",
    message: "bosun \(version) is available. You have \(versionLabel(check.installed)).",
    scroll: notes.isEmpty ? "No notes came with this release." : notes, boxes: [],
    buttons: ["Update Now", "Remind Me Later", "Skip This Version"], cancel: 1)
}

public func updateOfferEvent(answer: DialogAnswer) -> UpdateEvent {
  switch answer.button {
  case 0: return .updateNowChosen
  case 2: return .skipChosen
  default: return .remindLater
  }
}

private func joined(_ label: String, _ names: [String]) -> String? {
  names.isEmpty ? nil : "\(label): " + names.joined(separator: ", ")
}

/// What the update would do (its rehearsal's plan): which windows restart, which ask first, what is left to
/// the person, and why it cannot run.
public func updatePlanSpec(_ plan: UpdatePlan, check: UpdateCheck) -> DialogSpec {
  let version = check.latest?.version ?? "the new version"
  let lines = [
    joined("These windows restart", plan.restarting) ?? "No window restarts.",
    joined("These ask you first", plan.windowsNeedingYou),
    joined("Handed tasks keep their old text", plan.handed),
    joined("Change these of your own files yourself", plan.userChanges),
    joined("Blocked", plan.blockers),
  ].compactMap { $0 }
  return DialogSpec(
    title: plan.isBlocked ? "bosun cannot update now" : "Update bosun to \(version)?",
    message: lines.joined(separator: "\n"),
    scroll: plan.commits.isEmpty
      ? nil : "What changes:\n" + plan.commits.map { "• " + $0 }.joined(separator: "\n"),
    boxes: [], buttons: plan.isBlocked ? ["OK"] : ["Update", "Cancel"], cancel: plan.isBlocked ? nil : 1,
    style: plan.isBlocked ? .warning : .informational)
}

/// Yes or no to the plan. OK on a blocked plan is only a way to close it.
public func updatePlanEvent(plan: UpdatePlan, answer: DialogAnswer) -> UpdateEvent {
  !plan.isBlocked && answer.button == 0 ? .confirmed : .cancelled
}

public func upToDateSpec(_ check: UpdateCheck) -> DialogSpec {
  DialogSpec(
    title: "bosun is up to date", message: "You have \(versionLabel(check.installed)).", scroll: nil,
    boxes: [], buttons: ["OK"], cancel: nil)
}

// MARK: plain ones

public func failureSpec(_ title: String, _ why: String) -> DialogSpec {
  DialogSpec(
    title: title, message: why, scroll: nil, boxes: [], buttons: ["OK"], cancel: nil, style: .warning)
}

public func problemsSpec(_ problems: [String]) -> DialogSpec {
  DialogSpec(
    title: "What's Wrong",
    message: problems.isEmpty ? "bosun reports nothing wrong right now." : "bosun reports:",
    scroll: problems.isEmpty ? nil : problems.joined(separator: "\n"), boxes: [], buttons: ["OK"],
    cancel: nil)
}
