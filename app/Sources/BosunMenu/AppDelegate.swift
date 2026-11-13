import AppKit
import BosunMenuCore
import BosunMenuSystem

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
  private let options: LaunchOptions
  private var shell: AppShell?

  init(options: LaunchOptions) {
    self.options = options
  }

  /// How long after launch the app looks for an older copy once more.
  private static let secondLook: Duration = .seconds(3)

  func applicationDidFinishLaunching(_ notification: Notification) {
    // launchd's login job starts this app directly, which skips LaunchServices' one-copy rule: a copy that
    // finds an older one running exits, starting nothing.
    if isSecondCopy() {
      NSApp.terminate(nil)
      return
    }
    let shell = AppShell(options: options)
    self.shell = shell
    Task { await shell.start() }
    // Two copies started together may each look before the other is listed, and both stay. A second look
    // a few seconds on lists both, and the younger one exits (the order of starts is total).
    Task { [weak self] in
      try? await Task.sleep(for: Self.secondLook)
      if self?.isSecondCopy() == true { NSApp.terminate(nil) }
    }
  }

  func applicationWillTerminate(_ notification: Notification) {
    shell?.stop()
  }

  private func isSecondCopy() -> Bool {
    let bundleID = Bundle.main.bundleIdentifier
    let listed =
      bundleID.map { id in
        NSRunningApplication.runningApplications(withBundleIdentifier: id).map {
          (pid: $0.processIdentifier, bundleID: $0.bundleIdentifier)
        }
      } ?? []
    return secondCopyExits(
      running: listed, myPid: ProcessInfo.processInfo.processIdentifier, bundleID: bundleID,
      startTime: processStartTime(pid:))
  }
}
