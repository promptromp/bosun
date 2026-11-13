import Foundation

// Quit is option B of the mockups (docs/design/menu-bar.md): a short first dialog with the person's
// choices (the morning start paused, the Mac let to sleep, the icon kept), a second only when agents are
// busy (let them finish, or stop them now), and a third when the wait ran out with some still busy (keep
// waiting, stop them now, cancel). The quit itself is one `bosun quit` command, which does the work and
// prints one object; the app only collects the choices and shows the result.

public enum BusyChoice: Equatable, Sendable {
  case letFinish
  case stopNow
}

/// How long a quit may take, as the plan said it: the minutes `--let-finish` waits for, the seconds its steps
/// may wait for the windows lock, and the most its other steps take.
public struct QuitWait: Equatable, Sendable {
  public let minutes: Int
  public let lockSeconds: Int
  public let stepsSeconds: Int

  public init(minutes: Int, lockSeconds: Int, stepsSeconds: Int = QuitWait.stepsBefore) {
    self.minutes = minutes
    self.lockSeconds = lockSeconds
    self.stepsSeconds = stepsSeconds
  }

  /// The `--let-finish` argument, which keeps the app's wait and bosun's own the same number.
  var argument: String { "--let-finish=\(minutes)" }

  /// The one place a quit's timeout is made. It must outlast what `bosun quit` itself may wait for, or the app
  /// kills it half way (its `bosun stop` among it): with `--let-finish` the minutes (BOSUN_QUIT_WAIT_MINUTES,
  /// polled to the deadline), for both modes the wait for the windows lock that a start holds
  /// (BOSUN_WINDOWS_LOCK_WAIT_SECONDS, 180 s by default, libexec/bosun-quit), and then the steps after it
  /// (`steps_seconds`: its looks at the windows, the console job's stop, `bosun stop`), plus a margin for
  /// starting bosun's own processes.
  func timeout(for busy: BusyChoice) -> Duration {
    let waited = busy == .letFinish ? minutes * 60 : 0
    return .seconds(waited + lockSeconds + stepsSeconds + Self.marginSeconds)
  }

  static let marginSeconds = 60
  /// What a bosun from before `steps_seconds` was said took for those steps (lib/quit_live.py's STEPS_SECONDS
  /// then: two looks of 30 s, launchctl's two calls of 20 s, 5 s for the console, 120 s for `bosun stop`).
  public static let stepsBefore = 225
  /// The longest the app holds a quit whose plan said nothing of its waits.
  static let unknown: Duration = .seconds(60 * 60)
}

public struct QuitChoices: Equatable, Sendable {
  public var pauseMorning: Bool
  public var sleep: Bool
  public var keepIcon: Bool
  public var busy: BusyChoice
  /// The wait a `--let-finish` quit is given, taken from the plan when the first dialog is answered, and
  /// kept for "Keep Waiting". Nil only for a plan that did not say (`QuitPlan.wait`): bosun's own default
  /// minutes then, and the longest timeout the app ever holds a quit for.
  public var wait: QuitWait?

  public init(
    pauseMorning: Bool = true, sleep: Bool = true, keepIcon: Bool = false, busy: BusyChoice = .letFinish,
    wait: QuitWait? = nil
  ) {
    self.pauseMorning = pauseMorning
    self.sleep = sleep
    self.keepIcon = keepIcon
    self.busy = busy
    self.wait = wait
  }
}

/// The `bosun quit` argv for the choices: `quit --let-finish[=<minutes>]|--stop-now [--pause-morning]
/// [--sleep] --json`. `keepIcon` is the app's own and never the command's.
public func quitCommand(_ choices: QuitChoices) -> [String] {
  var args = ["quit"]
  switch choices.busy {
  case .letFinish: args.append(choices.wait?.argument ?? "--let-finish")
  case .stopNow: args.append("--stop-now")
  }
  if choices.pauseMorning { args.append("--pause-morning") }
  if choices.sleep { args.append("--sleep") }
  return args + ["--json"]
}

public enum QuitStep: Equatable, Sendable {
  /// `bosun quit --plan --json` is being asked.
  case planning
  /// `quit --plan` said it could not look (the windows cannot be read), did not answer in time, or failed
  /// without an object: the reason, with the choices, "Stop Now", which does not need the look, and "Quit
  /// bosun.app", which leaves bosun as it is. This is how a quit that did not finish is finished. The wait is
  /// the failure object's, nil when there was none.
  case unavailable(String, QuitWait?)
  /// The first dialog: what quitting would meet, and the choices.
  case first(QuitPlan)
  /// The second dialog: these agents are busy.
  case busy([String], QuitChoices)
  /// The third: the wait ran out and these are still busy.
  case stillBusy([String], QuitChoices)
  /// `bosun quit` runs; the app does not poll `status` meanwhile.
  case running(QuitChoices)
  case failed(String)
  /// The quit happened. The app quits itself unless the icon is kept; `note` is something to tell.
  case done(keepIcon: Bool, note: String?)
  /// Only the app quits, and bosun is left as it is (chosen where bosun could not say what a quit would stop).
  case appOnly
  case cancelled

  public var isFinished: Bool {
    switch self {
    case .failed, .done, .appOnly, .cancelled: return true
    default: return false
    }
  }

  public var quitsTheApp: Bool {
    switch self {
    case .done(let keepIcon, _): return !keepIcon
    case .appOnly: return true
    default: return false
    }
  }
}

/// How the `bosun quit` command ended.
public enum QuitOutcome: Equatable, Sendable {
  case stopped(note: String?)
  case busy([String])
  case failed(String)
}

public enum QuitEvent: Equatable, Sendable {
  case planned(QuitPlan)
  /// The plan cannot go on, and nothing the app could run would get past it (a refusal): only told.
  case planFailed(String)
  /// The plan could not be made (bosun's own failure, its `error`; no answer in time; a failure with no
  /// object), which a Stop Now can get past; with the wait its failure object said, if any.
  case planUnavailable(String, QuitWait?)
  case confirmed(QuitChoices)
  case busyChosen(BusyChoice)
  /// Leave the app, and bosun as it is.
  case leaveApp
  case cancelled
  case ended(QuitOutcome)
}

/// Quit was chosen: ask what it would meet.
public func startQuit() -> (QuitStep, [Command]) {
  (.planning, [Bosun.quitPlan])
}

/// The step after an event, and what to run. An event that means nothing in the step changes nothing.
public func next(_ step: QuitStep, on event: QuitEvent) -> (QuitStep, [Command]) {
  if event == .cancelled {
    return cancelling(step)
  }
  switch (step, event) {
  case (.planning, .planned(let plan)):
    return (plan.refused.map { .failed($0) } ?? .first(plan), [])
  case (.planning, .planFailed(let why)):
    return (.failed(why), [])
  case (.planning, .planUnavailable(let why, let wait)):
    return (.unavailable(why, wait), [])
  case (.unavailable(_, let wait), .confirmed(let choices)):
    var choices = QuitChoices(copying: choices, busy: .stopNow)
    choices.wait = wait
    return running(choices)
  case (.unavailable, .leaveApp):
    return (.appOnly, [])
  case (.first(let plan), .confirmed(let choices)):
    return confirming(plan, choices)
  case (.busy(_, let choices), .busyChosen(let busy)), (.stillBusy(_, let choices), .busyChosen(let busy)):
    return running(QuitChoices(copying: choices, busy: busy))
  case (.running(let choices), .ended(let outcome)):
    return (ended(outcome, choices), [])
  default:
    return (step, [])
  }
}

private func cancelling(_ step: QuitStep) -> (QuitStep, [Command]) {
  switch step {
  case .planning, .unavailable, .first, .busy, .stillBusy: return (.cancelled, [])
  default: return (step, [])
  }
}

/// The first dialog's answer. Busy agents get a second question; with none, the quit waits for any that
/// turns busy meanwhile, so it never stops an agent's work without a say.
private func confirming(_ plan: QuitPlan, _ choices: QuitChoices) -> (QuitStep, [Command]) {
  var choices = choices
  choices.wait = plan.wait
  if plan.busy.isEmpty { return running(QuitChoices(copying: choices, busy: .letFinish)) }
  return (.busy(plan.busy, choices), [])
}

private func running(_ choices: QuitChoices) -> (QuitStep, [Command]) {
  (.running(choices), [Bosun.quit(choices)])
}

private func ended(_ outcome: QuitOutcome, _ choices: QuitChoices) -> QuitStep {
  switch outcome {
  case .stopped(let note): return .done(keepIcon: choices.keepIcon, note: note)
  case .busy(let names): return .stillBusy(names, choices)
  case .failed(let why): return .failed(why)
  }
}

extension QuitChoices {
  fileprivate init(copying choices: QuitChoices, busy: BusyChoice) {
    self = choices
    self.busy = busy
  }
}

extension UpdateState {
  /// `update --yes` was started and `status` does not show it yet: its grace is asleep.
  var isStarting: Bool {
    if case .starting = self { return true }
    return false
  }
}

extension QuitStep {
  /// The busy agents a dialog named, for the line a running quit shows.
  var busyNames: [String]? {
    switch self {
    case .busy(let names, _), .stillBusy(let names, _): return names
    default: return nil
    }
  }
}

/// The line under the status while `bosun quit` runs: what it waits for (the agents named busy, which it lets
/// finish), or that it stops bosun now. Nil in any other step.
public func quittingLine(_ step: QuitStep, busy: [String]) -> String? {
  guard case .running(let choices) = step else { return nil }
  switch choices.busy {
  case .stopNow: return "Quitting: stopping bosun…"
  case .letFinish:
    return busy.isEmpty ? "Quitting…" : "Quitting: waiting for \(busy.joined(separator: ", ")) to finish…"
  }
}

// MARK: what a quit's reply means

let quitFailedText =
  "bosun quit did not complete. If bosun was meant to be stopped, run `bosun quit --stop-now` "
  + "again to finish, or `bosun start` to start bosun again."
let quitPlanFailedText =
  "bosun could not say what quitting would stop. Stop Now still works: it does not need to."
let quitPlanRefusedText = "bosun refused to quit and did not say why. Run `bosun quit --plan` in a terminal."
let quitTimedOutText =
  "bosun quit did not finish in time. Its record may stay, so bosun stays stopped: run "
  + "`bosun quit --stop-now` again to finish, or `bosun start` to start bosun again."

/// The outcome of a `bosun quit ... --json` run. The object's own `result` decides (exit 0 stopped,
/// 3 busy, 1 refused or failed say the same); a reply with no object is its stderr line.
public func quitOutcome(from result: CommandResult) -> QuitOutcome {
  switch decodeReply(QuitResult.self, result, trustingStdout: true) {
  case .failure(.timedOut): return .failed(quitTimedOutText)
  case .failure(let failure): return .failed(failure.message)
  case .success(let reply):
    switch reply.result {
    case .stopped: return .stopped(note: consoleNote(reply))
    case .busy: return .busy(reply.plan.busy)
    case .refused, .failed: return .failed(reply.message ?? quitFailedText)
    case .unknown(let word):
      return .failed("bosun quit answered \"\(word)\", which this app does not know. \(quitFailedText)")
    }
  }
}

private func consoleNote(_ reply: QuitResult) -> String? {
  guard reply.console == .stillRunning else { return nil }
  let port = reply.consolePort.map { " on port \($0)" } ?? ""
  return "A console is still running\(port). Its job may still be exiting; if it stays, or you "
    + "started it by hand, stop it where you started it."
}

// MARK: running the commands

public func quitPlanEvent(via cli: any BosunCLI) async -> QuitEvent {
  quitPlanEvent(from: await cli.run(Bosun.quitPlan))
}

/// `quit --plan --json`'s reply as an event. When it cannot look it exits 1 with its failure object on
/// stdout (the plan's keys, `result: failed` and its `error`) and nothing on stderr: that `error` is
/// what is shown, and a Stop Now is offered, which `--stop-now` makes possible with unreadable windows. Only
/// `failed` offers it: an object with another result on a non-zero exit (refused: an update runs) would be
/// refused again, so it is only told. A plan that did not answer in time, or failed with no object, offers
/// Stop Now too (with the longest wait, since bosun did not say its own), and a way to leave only the app.
func quitPlanEvent(from result: CommandResult) -> QuitEvent {
  if let reply = planFailureObject(result) {
    guard reply.result == .failed else { return .planFailed(reply.message ?? quitPlanRefusedText) }
    return .planUnavailable(reply.message ?? quitPlanFailedText, reply.plan.wait)
  }
  switch decodeReply(QuitPlan.self, result) {
  case .success(let plan): return .planned(plan)
  case .failure(let failure):
    switch failure {
    case .timedOut, .exited: return .planUnavailable(failure.message, nil)
    case .notInstalled, .couldNotStart, .contract: return .planFailed(failure.message)
    }
  }
}

/// The object a plan printed on a non-zero exit, if it printed one.
private func planFailureObject(_ result: CommandResult) -> QuitResult? {
  guard case .exited(let code) = result.outcome, code != 0 else { return nil }
  guard case .success(let reply) = decodeReply(QuitResult.self, result, trustingStdout: true) else {
    return nil
  }
  return reply
}

// MARK: what Quit does

public enum QuitAction: Equatable, Sendable {
  /// No dialog: the app only quits, since there is no running bosun to stop (it is stopped, missing, or
  /// cannot be asked).
  case exitApp
  /// The quit flow, from `startQuit()`.
  case runFlow
}

/// What choosing Quit does in a state.
public func quitAction(for status: StatusState) -> QuitAction {
  if case .loaded(let snapshot, _) = status, snapshot.level != .stopped { return .runFlow }
  return .exitApp
}

public func runQuit(_ choices: QuitChoices, via cli: any BosunCLI) async -> QuitEvent {
  .ended(quitOutcome(from: await cli.run(Bosun.quit(choices))))
}
