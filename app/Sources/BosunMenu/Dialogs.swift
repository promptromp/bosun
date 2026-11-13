import AppKit
import BosunMenuCore

// `DialogSpec`s as `NSAlert`s: the buttons in the spec's order (the first is the default, Return), Escape on
// the cancel button, an accessory view with a scrolling read-only text view (the notes, the plan, the
// problems) and the check boxes. `build` makes the alert without showing it, so it is tested as built;
// `AlertPresenter` shows it, with the app briefly a regular app so the alert comes to the front.

/// An alert that is built and not yet shown, with what is needed to read the answer back.
@MainActor
struct BuiltAlert {
  let spec: DialogSpec
  let alert: NSAlert
  let boxes: [(id: String, button: NSButton)]
  let scroll: NSScrollView?

  var scrollText: NSTextView? { scroll?.documentView as? NSTextView }
}

@MainActor
enum Dialogs {
  private static let accessoryWidth: CGFloat = 440
  private static let scrollHeight: CGFloat = 150

  static func build(_ spec: DialogSpec) -> BuiltAlert {
    let alert = NSAlert()
    alert.messageText = spec.title
    alert.informativeText = spec.message
    alert.alertStyle = spec.style == .warning ? .warning : .informational
    for (index, title) in spec.buttons.enumerated() {
      let button = alert.addButton(withTitle: title)
      if index == spec.cancel { button.keyEquivalent = "\u{1b}" }
    }
    let scroll = spec.scroll.map(scrollView(showing:))
    let boxes = spec.boxes.map { (id: $0.id, button: checkbox($0)) }
    var views: [NSView] = []
    if let scroll { views.append(scroll) }
    views += boxes.map(\.button)
    if !views.isEmpty { alert.accessoryView = stack(views) }
    return BuiltAlert(spec: spec, alert: alert, boxes: boxes, scroll: scroll)
  }

  /// The answer to a response of `runModal`: the button's index and each box as it stands. A response that
  /// is no button's (the dialog was closed some other way) is the spec's default answer.
  static func answer(_ built: BuiltAlert, response: NSApplication.ModalResponse) -> DialogAnswer {
    let index = response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
    var boxes: [String: Bool] = [:]
    for (id, button) in built.boxes { boxes[id] = button.state == .on }
    let button = built.spec.buttons.indices.contains(index) ? index : built.spec.defaultAnswer.button
    return DialogAnswer(button: button, boxes: boxes)
  }

  /// A test hook's answer given to a dialog: sets the boxes, and returns the response that presses the button
  /// it names. Nil, changing nothing, when the dialog has no such button or box.
  static func apply(_ hook: HookAnswer, to built: BuiltAlert) -> NSApplication.ModalResponse? {
    guard let answer = hook.resolve(for: built.spec) else { return nil }
    for (id, button) in built.boxes { button.state = answer.boxes[id] == true ? .on : .off }
    return NSApplication.ModalResponse(
      rawValue: NSApplication.ModalResponse.alertFirstButtonReturn.rawValue + answer.button)
  }

  // MARK: views

  private static func checkbox(_ box: DialogBox) -> NSButton {
    let button = NSButton(checkboxWithTitle: box.title, target: nil, action: nil)
    button.state = box.on ? .on : .off
    return button
  }

  private static func scrollView(showing text: String) -> NSScrollView {
    let scroll = NSTextView.scrollableTextView()
    scroll.hasVerticalScroller = true
    scroll.borderType = .bezelBorder
    scroll.translatesAutoresizingMaskIntoConstraints = false
    scroll.widthAnchor.constraint(equalToConstant: accessoryWidth).isActive = true
    scroll.heightAnchor.constraint(equalToConstant: scrollHeight).isActive = true
    if let view = scroll.documentView as? NSTextView {
      view.isEditable = false
      view.isSelectable = true
      view.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
      view.string = text
    }
    return scroll
  }

  private static func stack(_ views: [NSView]) -> NSStackView {
    let stack = NSStackView(views: views)
    stack.orientation = .vertical
    stack.alignment = .leading
    stack.spacing = 8
    stack.frame.size = stack.fittingSize
    return stack
  }
}

/// Shows dialogs one at a time. The app is a regular app (it has a Dock icon) only while one is open, so
/// the alert comes to the front, and an accessory again after.
@MainActor
final class AlertPresenter {
  private var showing = false
  private var waiting: [CheckedContinuation<Void, Never>] = []
  private(set) var current: BuiltAlert?

  var hasOpenDialog: Bool { current != nil }

  func present(_ spec: DialogSpec) async -> DialogAnswer {
    // One at a time: a dialog asked for while one is open waits its turn (and is handed the turn)
    if showing {
      await withCheckedContinuation { waiting.append($0) }
    } else {
      showing = true
    }
    let built = Dialogs.build(spec)
    current = built
    NSApp.setActivationPolicy(.regular)
    NSApp.activate()
    let response = built.alert.runModal()
    NSApp.setActivationPolicy(.accessory)
    current = nil
    if waiting.isEmpty {
      showing = false
    } else {
      waiting.removeFirst().resume()
    }
    return Dialogs.answer(built, response: response)
  }

  /// A test hook's answer for the dialog that is open: false when none is, or it cannot take that answer.
  func answerOpenDialog(_ hook: HookAnswer) -> Bool {
    guard let built = current, let response = Dialogs.apply(hook, to: built) else { return false }
    NSApp.stopModal(withCode: response)
    return true
  }
}
