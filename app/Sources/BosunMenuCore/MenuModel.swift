import Foundation

// The menu each state becomes (docs/design/menu-bar.md): pure, so the AppKit shell only renders it.
// Titles and states are the design note's: the status line's dot per level, "Show What's Wrong…" only on
// a warning or an error, "Start bosun" first only when bosun is not running and is to be (stopped, or red
// with no window open: `StatusSnapshot.offersStart`), Open Console greyed while stopped, and a Quit without
// "…" when no dialog follows.

public enum Dot: Equatable, Sendable {
  case green, yellow, red, grey
}

/// The small dot on the menu bar icon.
public enum IconBadge: Equatable, Sendable {
  case warning, error, update
}

public enum HelpTopic: Hashable, Sendable {
  case guide, doctor, logs, uninstall
}

public enum ItemID: Hashable, Sendable {
  case startBosun, showProblems, openConsole, checkForUpdates, openAtLogin
  /// "Update bosun…", when bosun speaks a contract this app does not.
  case updateBosun
  case helpMenu
  case help(HelpTopic)
  case quit
}

public struct MenuItem: Equatable, Sendable {
  public let id: ItemID
  public let title: String
  public let enabled: Bool
  /// The key equivalent (with ⌘).
  public let key: String?
  /// A badge beside the title (an update is ready).
  public let badge: Bool
  public let checked: Bool
  public let children: [MenuItem]

  public init(
    id: ItemID, title: String, enabled: Bool = true, key: String? = nil, badge: Bool = false,
    checked: Bool = false, children: [MenuItem] = []
  ) {
    self.id = id
    self.title = title
    self.enabled = enabled
    self.key = key
    self.badge = badge
    self.checked = checked
    self.children = children
  }
}

/// The first lines of the menu: a coloured dot and one sentence, with grey detail lines.
public struct StatusHeader: Equatable, Sendable {
  public let dot: Dot
  public let sentence: String
  public let detail: [String]
}

public struct MenuModel: Equatable, Sendable {
  public let header: StatusHeader
  /// The items, grouped; the shell puts a separator between sections.
  public let sections: [[MenuItem]]
  public let iconBadge: IconBadge?
}

// MARK: flows

public enum Flow: Equatable, Sendable {
  case quit, update, start
  /// Open at Login being switched (`bosun app login on|off`): a second click waits for the first.
  case login
}

/// Which flow runs. One at a time: ⌘Q twice, or Quit while an update dialog is open, is ignored.
public struct FlowState: Equatable, Sendable {
  public private(set) var active: Flow?
  /// A line under the status while the flow runs (what a quit waits for); gone when it ends.
  public internal(set) var detail: String?

  public init() {}

  public var isIdle: Bool { active == nil }

  /// While the app's own quit runs it does not poll `status`: the seconds between `quit.json` and the
  /// stop would read as "bosun quit did not finish".
  public var pollsStatus: Bool { active != .quit }

  /// Starts `flow` unless another runs; false means the request is to be ignored.
  public mutating func begin(_ flow: Flow) -> Bool {
    guard active == nil else { return false }
    active = flow
    return true
  }

  public mutating func end(_ flow: Flow) {
    guard active == flow else { return }
    active = nil
    detail = nil
  }
}

// MARK: one copy at a time

public struct RunningApp: Equatable, Sendable {
  public let pid: Int32
  public let bundleID: String?
  /// When the process started, in seconds since 1970 (the shell reads `kp_proc.p_starttime` with sysctl
  /// KERN_PROC: `NSRunningApplication.launchDate` is nil for an app launchd starts directly).
  public let startedAt: Double

  public init(pid: Int32, bundleID: String?, startedAt: Double) {
    self.pid = pid
    self.bundleID = bundleID
    self.startedAt = startedAt
  }

  /// Whether this process is the older of the two: it started earlier, and with the same start (to the
  /// resolution the clock gives) the lower pid. Never by pid alone: pids wrap, at 99999 on macOS.
  fileprivate func started(before other: RunningApp) -> Bool {
    (startedAt, pid) < (other.startedAt, other.pid)
  }
}

/// launchd's login job starts `Contents/MacOS/bosun` directly, which skips LaunchServices' one-copy rule,
/// so a copy that finds another running application with its bundle identifier, started before it, exits
/// at launch: the copy that started earliest stays. Two started together each see the other, and one
/// always stays.
public func shouldExitAsSecondCopy(running: [RunningApp], me: RunningApp, bundleID: String) -> Bool {
  running.contains { $0.bundleID == bundleID && $0.pid != me.pid && $0.started(before: me) }
}

// MARK: the menu

/// The menu for a state. `login` is whether Open at Login is on (`bosun app status`).
public func menu(
  for status: StatusState, update: UpdateState, flow: FlowState, login: Bool = false
) -> MenuModel {
  switch status {
  case .loaded(let snapshot, let current):
    return loadedMenu(snapshot, current: current, update: update, flow: flow, login: login)
  case .notInstalled:
    return offlineMenu(
      StatusHeader(
        dot: .grey, sentence: "bosun is not installed",
        detail: ["Install bosun (see its README), then open this app again."]),
      badge: nil, flow: flow)
  case .cannotRun(let why):
    return offlineMenu(
      StatusHeader(dot: .red, sentence: "bosun cannot be run", detail: [why]), badge: .error, flow: flow)
  case .unreachable(let why):
    // bosun runs, only its status could not be had: doctor is what finds out why
    return offlineMenu(
      StatusHeader(dot: .red, sentence: "bosun cannot be reached", detail: [why]),
      badge: .error, flow: flow, doctor: true)
  case .contractTooNew(let number):
    return tooNewMenu(number, update: update, flow: flow)
  }
}

private enum Title {
  static let start = "Start bosun"
  static let problems = "Show What's Wrong…"
  static let console = "Open Console"
  static let login = "Open at Login"
  static let help = "Help"
}

private func loadedMenu(
  _ status: StatusSnapshot, current: Bool, update: UpdateState, flow: FlowState, login: Bool
) -> MenuModel {
  let idle = flow.isIdle
  let stopped = status.level == .stopped
  var actions: [MenuItem] = []
  if status.offersStart {
    actions.append(MenuItem(id: .startBosun, title: Title.start, enabled: idle))
  }
  if showsProblems(status.level) {
    actions.append(MenuItem(id: .showProblems, title: Title.problems))
  }
  let console = MenuItem(id: .openConsole, title: Title.console, enabled: !stopped, key: "o")
  let own = [
    updateItem(update, enabled: idle),
    MenuItem(id: .openAtLogin, title: Title.login, enabled: idle, checked: login),
    helpItem(runsBosun: true, doctor: true, logs: !status.logs.isEmpty, idle: idle),
  ]
  let quit = MenuItem(
    id: .quit, title: stopped ? "Quit bosun" : "Quit bosun…", enabled: idle, key: "q")
  let header = StatusHeader(
    dot: dot(of: status.level),
    sentence: current ? status.sentence : status.sentence + " (not current)",
    detail: status.detail + [flow.detail].compactMap { $0 })
  return MenuModel(
    header: header,
    sections: [actions, [console], own, [quit]].filter { !$0.isEmpty },
    iconBadge: badge(status.level, update))
}

/// A level this app does not know is shown as a warning: never all well.
private func dot(of level: Level) -> Dot {
  switch level {
  case .ok: return .green
  case .warning, .unknown: return .yellow
  case .error: return .red
  case .stopped: return .grey
  }
}

private func showsProblems(_ level: Level) -> Bool {
  switch level {
  case .warning, .error, .unknown: return true
  case .ok, .stopped: return false
  }
}

private func badge(_ level: Level, _ update: UpdateState) -> IconBadge? {
  switch level {
  case .error: return .error
  case .warning, .unknown: return .warning
  case .ok, .stopped:
    switch update {
    case .available, .confirming: return .update
    default: return nil
    }
  }
}

private func updateItem(_ state: UpdateState, enabled: Bool) -> MenuItem {
  let id = ItemID.checkForUpdates
  switch state {
  case .idle, .failed:
    return MenuItem(id: id, title: "Check for Updates…", enabled: enabled)
  case .checking, .checkingStart:
    return MenuItem(id: id, title: "Checking for Updates…", enabled: false)
  case .available(let check):
    return MenuItem(id: id, title: availableTitle(check), enabled: enabled, badge: true)
  case .planning:
    return MenuItem(id: id, title: "Preparing Update…", enabled: false)
  case .confirming(_, let check):
    return MenuItem(id: id, title: availableTitle(check), enabled: false, badge: true)
  case .starting:
    return MenuItem(id: id, title: "Updating…", enabled: false)
  case .running(let waiting):
    return MenuItem(id: id, title: updatingTitle(waiting.count), enabled: false)
  }
}

private func availableTitle(_ check: UpdateCheck) -> String {
  "Update Available\(check.latest.map { ": \($0.version)" } ?? "")…"
}

private func updatingTitle(_ waiting: Int) -> String {
  switch waiting {
  case 0: return "Updating…"
  case 1: return "Updating… waiting for 1 window"
  default: return "Updating… waiting for \(waiting) windows"
  }
}

/// The Help submenu. The guide is a web page; the rest run bosun (or show what it logged), so they are
/// there only while bosun can be run: Run bosun doctor also while its status could not be had (which is what
/// doctor is for), the logs once a status named their folder, and Uninstall only with a status.
private func helpItem(runsBosun: Bool, doctor: Bool, logs: Bool, idle: Bool) -> MenuItem {
  MenuItem(
    id: .helpMenu, title: Title.help,
    children: [
      MenuItem(id: .help(.guide), title: "bosun Guide"),
      MenuItem(id: .help(.doctor), title: "Run bosun doctor", enabled: doctor),
      MenuItem(id: .help(.logs), title: "Show Logs", enabled: runsBosun && logs),
      MenuItem(id: .help(.uninstall), title: "Uninstall bosun…", enabled: runsBosun && idle),
    ])
}

/// bosun is missing, cannot be run or cannot be asked: nothing is offered that acts on it (Run bosun doctor
/// only where bosun runs and its status could not be had), and Quit only leaves the app.
private func offlineMenu(
  _ header: StatusHeader, badge: IconBadge?, flow: FlowState, doctor: Bool = false
) -> MenuModel {
  MenuModel(
    header: header,
    sections: [
      [helpItem(runsBosun: false, doctor: doctor, logs: false, idle: flow.isIdle)],
      [MenuItem(id: .quit, title: "Quit bosun.app", enabled: flow.isIdle, key: "q")],
    ],
    iconBadge: badge)
}

/// The menu before the first status has come: it says it is looking, and offers nothing that runs bosun.
public func loadingMenu(flow: FlowState) -> MenuModel {
  offlineMenu(
    StatusHeader(dot: .grey, sentence: "Checking bosun…", detail: []), badge: nil, flow: flow)
}

/// bosun speaks a newer contract: "Update bosun…" (bosun's update, which brings the app bosun expects), and
/// the way on when bosun is current and the app's own release lags behind it.
private func tooNewMenu(_ number: Int, update: UpdateState, flow: FlowState) -> MenuModel {
  let detail = [
    "bosun reports version \(number) of its status; this app reads version \(Contract.supported).",
    "If bosun is up to date, run `bosun app update` in a terminal.",
  ]
  return MenuModel(
    header: StatusHeader(dot: .yellow, sentence: "bosun.app needs an update", detail: detail),
    sections: [
      [updateBosunItem(update, idle: flow.isIdle)],
      [helpItem(runsBosun: true, doctor: true, logs: false, idle: flow.isIdle)],
      [MenuItem(id: .quit, title: "Quit bosun.app", enabled: flow.isIdle, key: "q")],
    ],
    iconBadge: .warning)
}

/// "Update bosun…" while nothing is under way; otherwise what is (greyed), so a second click cannot start
/// a second update.
private func updateBosunItem(_ state: UpdateState, idle: Bool) -> MenuItem {
  switch state {
  case .idle, .failed, .available:
    return MenuItem(id: .updateBosun, title: "Update bosun…", enabled: idle)
  default:
    return MenuItem(id: .updateBosun, title: updateItem(state, enabled: false).title, enabled: false)
  }
}
