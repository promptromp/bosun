import Darwin
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

/// When a process started, in seconds since 1970, from the kernel's process table (`kp_proc.p_starttime`).
/// `NSRunningApplication.launchDate` is nil for an app launchd starts directly, which is how the login job
/// starts this one. Nil when the process is not there or the kernel will not say; never 0.
public func processStartTime(pid: Int32) -> Double? {
  var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
  var info = kinfo_proc()
  var size = MemoryLayout<kinfo_proc>.stride
  guard sysctl(&mib, UInt32(mib.count), &info, &size, nil, 0) == 0, size == MemoryLayout<kinfo_proc>.stride,
    info.kp_proc.p_pid == pid
  else { return nil }
  let started = info.kp_proc.p_starttime
  let seconds = Double(started.tv_sec) + Double(started.tv_usec) / 1_000_000
  return seconds > 0 ? seconds : nil
}

/// The running applications with a start time. A copy whose start cannot be read is left out of the list:
/// whether it is older than this one is not known, and a start time made up (or 0) would decide wrongly.
public func runningApps(
  _ listed: [(pid: Int32, bundleID: String?)], startTime: (Int32) -> Double? = processStartTime(pid:)
) -> [RunningApp] {
  listed.compactMap { app in
    startTime(app.pid).map { RunningApp(pid: app.pid, bundleID: app.bundleID, startedAt: $0) }
  }
}

/// Whether this copy exits at launch because an older copy of the app is running. `listed` is what
/// `NSRunningApplication` lists with the app's bundle identifier. A copy that cannot say when it started,
/// or has no bundle identifier (not run from the app), stays.
public func secondCopyExits(
  running listed: [(pid: Int32, bundleID: String?)], myPid: Int32, bundleID: String?,
  startTime: (Int32) -> Double? = processStartTime(pid:)
) -> Bool {
  guard let bundleID, let mine = startTime(myPid) else { return false }
  let me = RunningApp(pid: myPid, bundleID: bundleID, startedAt: mine)
  return shouldExitAsSecondCopy(
    running: runningApps(listed, startTime: startTime), me: me, bundleID: bundleID)
}
