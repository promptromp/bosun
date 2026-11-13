import AppKit
import BosunMenuCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
  private let options: LaunchOptions
  private var shell: AppShell?

  init(options: LaunchOptions) {
    self.options = options
  }

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
      running: listed, myPid: ProcessInfo.processInfo.processIdentifier, bundleID: bundleID)
  }
}
