import Foundation

// What the app does when it starts: its arguments, whether it starts bosun, and whether it is the second
// copy. Pure, or one sysctl away from it, so the AppKit shell only asks.

public struct LaunchOptions: Equatable, Sendable {
  /// launchd's login job passes `--at-login`: only show the icon; bosun's own morning job starts bosun.
  public var atLogin: Bool
  /// `--test-hooks <dir>`: the VM suite's hooks. Without the argument they do not exist at run time.
  public var testHooks: URL?

  public init(atLogin: Bool = false, testHooks: URL? = nil) {
    self.atLogin = atLogin
    self.testHooks = testHooks
  }

  /// The options of `CommandLine.arguments` (the first is the program). What it does not know is the
  /// system's (`-psn_…`, `-NSDocumentRevisionsDebugMode`) and ignored.
  public static func parse(_ arguments: [String]) -> LaunchOptions {
    var options = LaunchOptions()
    var rest = arguments.dropFirst()
    while let argument = rest.popFirst() {
      switch argument {
      case "--at-login":
        options.atLogin = true
      case "--test-hooks":
        if let folder = rest.first, !folder.hasPrefix("--") {
          options.testHooks = URL(fileURLWithPath: folder)
          rest.removeFirst()
        }
      default:
        break
      }
    }
    return options
  }
}

/// Opened by you, with bosun stopped or not running when it should be: start it, once. At login the app
/// only shows the icon. Where bosun cannot be run there is nothing to start.
public func shouldStartAtLaunch(_ options: LaunchOptions, status: StatusState) -> Bool {
  guard !options.atLogin, case .loaded(let snapshot, _) = status else { return false }
  return snapshot.level == .stopped || snapshot.level == .error
}

// MARK: one copy at a time

/// The running applications with a start time. A copy whose start cannot be read is left out of the list:
/// whether it is older than this one is not known, and a start time made up (or 0) would decide wrongly.
public func runningApps(
  _ listed: [(pid: Int32, bundleID: String?)], startTime: (Int32) -> Double?
) -> [RunningApp] {
  listed.compactMap { app in
    startTime(app.pid).map { RunningApp(pid: app.pid, bundleID: app.bundleID, startedAt: $0) }
  }
}

/// Whether this copy exits at launch because an older copy of the app is running. `listed` is what
/// `NSRunningApplication` lists with the app's bundle identifier. A copy that cannot say when it started,
/// or has no bundle identifier (not run from the app), stays. `startTime` reads a process's start (the shell
/// gives it `processStartTime` of BosunMenuSystem; the core reads nothing itself).
public func secondCopyExits(
  running listed: [(pid: Int32, bundleID: String?)], myPid: Int32, bundleID: String?,
  startTime: (Int32) -> Double?
) -> Bool {
  guard let bundleID, let mine = startTime(myPid) else { return false }
  let me = RunningApp(pid: myPid, bundleID: bundleID, startedAt: mine)
  return shouldExitAsSecondCopy(
    running: runningApps(listed, startTime: startTime), me: me, bundleID: bundleID)
}
