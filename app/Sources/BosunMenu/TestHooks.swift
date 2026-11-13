import AppKit
import BosunMenuCore
import BosunMenuSystem

// The VM suite's hooks, wired up (their files and formats are `TestHookFiles` in the core). Made only when
// the app was launched with `--test-hooks <dir>`: without the argument none of this exists at run time.

/// Takes the actions a script drops in the hooks folder, once a second, and does them as if clicked.
@MainActor
final class HookRunner {
  private let files: TestHookFiles
  private let perform: @MainActor (ItemID) -> Void
  private let answer: @MainActor (HookAnswer) -> Bool
  private let dialogOpen: @MainActor () -> Bool
  private let interval: TimeInterval
  private var timer: Timer?

  init(
    files: TestHookFiles, perform: @escaping @MainActor (ItemID) -> Void,
    answer: @escaping @MainActor (HookAnswer) -> Bool, dialogOpen: @escaping @MainActor () -> Bool,
    interval: TimeInterval = 1
  ) {
    self.files = files
    self.perform = perform
    self.answer = answer
    self.dialogOpen = dialogOpen
    self.interval = interval
  }

  /// Every `interval`, in the common run loop modes: a dialog that is open runs the loop in its modal mode,
  /// and the hooks must answer it.
  func start() {
    let timer = Timer(timeInterval: interval, repeats: true) { [weak self] _ in
      MainActor.assumeIsolated { self?.tick() }
    }
    RunLoop.main.add(timer, forMode: .common)
    self.timer = timer
  }

  func stop() {
    timer?.invalidate()
    timer = nil
  }

  func tick() {
    for action in files.takeActions(dialogOpen: dialogOpen()) {
      switch action {
      case .item(let id):
        perform(id)
      case .answer(let hook):
        if !answer(hook) { files.log(.error("no open dialog could take the answer for \"\(hook.button)\"")) }
      }
    }
  }
}

/// Opens nothing, and logs what it would have: the hooks' stand-in for the workspace, so a suite run in a VM
/// opens no browser, no Finder window and no Terminal.
@MainActor
struct RecordingOpener: URLOpening {
  let files: TestHookFiles

  func open(_ url: URL) {
    files.log(.open(url.isFileURL ? url.path : url.absoluteString))
  }
}
