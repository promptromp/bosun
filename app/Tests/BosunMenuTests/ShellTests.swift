import AppKit
import Foundation
import Testing

@testable import BosunMenu
@testable import BosunMenuCore

private func scratchFolder() throws -> URL {
  let dir = FileManager.default.temporaryDirectory
    .appendingPathComponent("bosun-shell-test-\(UUID().uuidString)")
  try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
  return dir
}

@MainActor
private func eventually(seconds: Double = 5, _ condition: () -> Bool) async -> Bool {
  let end = Date().addingTimeInterval(seconds)
  while Date() < end {
    if condition() { return true }
    try? await Task.sleep(for: .milliseconds(5))
  }
  return condition()
}

@MainActor @Suite struct StatusIconTests {
  @Test func theIconIsAnEighteenPointTemplate() {
    let image = StatusIcon.template()
    #expect(image.isTemplate)
    #expect(image.size == NSSize(width: 18, height: 18))
  }

  @Test func theTemplateHasSomethingDrawn() throws {
    let image = StatusIcon.template()
    let tiff = try #require(image.tiffRepresentation)
    let bitmap = try #require(NSBitmapImageRep(data: tiff))
    var drawn = false
    for column in 0..<bitmap.pixelsWide {
      for row in 0..<bitmap.pixelsHigh where (bitmap.colorAt(x: column, y: row)?.alphaComponent ?? 0) > 0.5 {
        drawn = true
      }
    }
    #expect(drawn)
  }

  @Test func eachBadgeHasItsColourAndNoBadgeHasNone() {
    #expect(StatusIcon.dotColor(for: .warning) == .systemYellow)
    #expect(StatusIcon.dotColor(for: .error) == .systemRed)
    #expect(StatusIcon.dotColor(for: .update) == .systemBlue)
    #expect(StatusIcon.dotColor(for: nil) == nil)
  }

  @Test func theDotSitsInTheLowerRightCornerOfTheIcon() {
    let frame = StatusIcon.dotFrame(in: NSRect(x: 0, y: 0, width: 24, height: 22))
    #expect(frame.width == frame.height)
    #expect(frame.width <= 8)
    #expect(frame.maxX <= 24)
    #expect(frame.minY >= 0)
    #expect(frame.midX > 12)
    #expect(frame.midY < 11)
  }

  @Test func theBadgeViewShowsTheDotOnlyWhenThereIsABadge() {
    let dot = BadgeDotView()
    #expect(dot.isHidden)
    dot.show(.error)
    #expect(!dot.isHidden)
    #expect(dot.colour == .systemRed)
    dot.show(.update)
    #expect(dot.colour == .systemBlue)
    dot.show(nil)
    #expect(dot.isHidden)
  }
}

@MainActor @Suite struct WakeObserverTests {
  /// Review focus 3: the Mac wakes, the status refreshes at once.
  @Test func aWakeNotificationCallsBack() async {
    let center = NotificationCenter()
    var woke = 0
    let observer = WakeObserver(center: center) { woke += 1 }
    center.post(name: NSWorkspace.didWakeNotification, object: nil)
    #expect(await eventually { woke == 1 })
    withExtendedLifetime(observer) {}
  }

  @Test func otherNotificationsDoNot() async {
    let center = NotificationCenter()
    var woke = 0
    let observer = WakeObserver(center: center) { woke += 1 }
    center.post(name: NSWorkspace.willSleepNotification, object: nil)
    try? await Task.sleep(for: .milliseconds(50))
    #expect(woke == 0)
    withExtendedLifetime(observer) {}
  }

  @Test func aStoppedObserverHearsNothing() async {
    let center = NotificationCenter()
    var woke = 0
    let observer = WakeObserver(center: center) { woke += 1 }
    observer.stop()
    center.post(name: NSWorkspace.didWakeNotification, object: nil)
    try? await Task.sleep(for: .milliseconds(50))
    #expect(woke == 0)
  }
}

@MainActor @Suite struct RecordingOpenerTests {
  @Test func theUrlIsLoggedAndNothingIsOpened() throws {
    let folder = try scratchFolder()
    let files = TestHookFiles(directory: folder)
    try files.prepare()
    RecordingOpener(files: files).open(URL(string: "http://127.0.0.1:8123/?t=x")!)
    let text = try String(contentsOf: folder.appendingPathComponent("log.jsonl"), encoding: .utf8)
    #expect(text.contains("\"open\":\"http://127.0.0.1:8123/?t=x\""))
  }

  @Test func aFileIsLoggedByItsPath() throws {
    let folder = try scratchFolder()
    let files = TestHookFiles(directory: folder)
    try files.prepare()
    RecordingOpener(files: files).open(URL(fileURLWithPath: "/tmp/bosun-app/bosun-doctor.command"))
    let text = try String(contentsOf: folder.appendingPathComponent("log.jsonl"), encoding: .utf8)
    #expect(text.contains("\"open\":\"/tmp/bosun-app/bosun-doctor.command\""))
  }
}

/// A switch a test turns while a closure it gave away reads it.
@MainActor
final class Flag {
  var on = false
}

@MainActor @Suite struct HookRunnerTests {
  private func setUp() throws -> (TestHookFiles, URL) {
    let folder = try scratchFolder()
    let files = TestHookFiles(directory: folder)
    try files.prepare()
    return (files, folder)
  }

  private func drop(_ text: String, _ name: String, _ folder: URL) throws {
    try text.write(to: folder.appendingPathComponent("actions/\(name)"), atomically: true, encoding: .utf8)
  }

  @Test func anItemIsPerformedAsIfClicked() throws {
    let (files, folder) = try setUp()
    var performed: [ItemID] = []
    let runner = HookRunner(
      files: files, perform: { performed.append($0) }, answer: { _ in true }, dialogOpen: { false })
    try drop(#"{"item": "openConsole"}"#, "1.json", folder)
    runner.tick()
    #expect(performed == [.openConsole])
  }

  @Test func anAnswerGoesToTheOpenDialog() throws {
    let (files, folder) = try setUp()
    var answered: [HookAnswer] = []
    let open = Flag()
    let runner = HookRunner(
      files: files, perform: { _ in },
      answer: {
        answered.append($0)
        return true
      }, dialogOpen: { open.on })
    try drop(#"{"answer": {"button": "Cancel"}}"#, "1.json", folder)
    runner.tick()
    #expect(answered.isEmpty)
    open.on = true
    runner.tick()
    #expect(answered == [HookAnswer(button: "Cancel", boxes: [:])])
  }

  @Test func anAnswerTheDialogCannotTakeIsLogged() throws {
    let (files, folder) = try setUp()
    let runner = HookRunner(files: files, perform: { _ in }, answer: { _ in false }, dialogOpen: { true })
    try drop(#"{"answer": {"button": "Shred"}}"#, "1.json", folder)
    runner.tick()
    let text = try String(contentsOf: folder.appendingPathComponent("log.jsonl"), encoding: .utf8)
    #expect(text.contains("Shred"))
  }

  @Test func theTimerTicksOnItsOwn() async throws {
    let (files, folder) = try setUp()
    var performed: [ItemID] = []
    let runner = HookRunner(
      files: files, perform: { performed.append($0) }, answer: { _ in true }, dialogOpen: { false },
      interval: 0.02)
    runner.start()
    try drop(#"{"item": "quit"}"#, "1.json", folder)
    #expect(await eventually { performed == [.quit] })
    runner.stop()
  }
}

@MainActor @Suite struct LaunchOptionsFromTheProcessTests {
  @Test func theHooksAreOffWithoutTheirArgument() {
    #expect(LaunchOptions.parse(CommandLine.arguments).testHooks == nil)
  }
}
