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

public struct QuitChoices: Equatable, Sendable {
  public var pauseMorning: Bool
  public var sleep: Bool
  public var keepIcon: Bool
  public var busy: BusyChoice

  public init(
    pauseMorning: Bool = true, sleep: Bool = true, keepIcon: Bool = false, busy: BusyChoice = .letFinish
  ) {
    self.pauseMorning = pauseMorning
    self.sleep = sleep
    self.keepIcon = keepIcon
    self.busy = busy
  }
}

/// The `bosun quit` argv for the choices: `quit --let-finish|--stop-now [--pause-morning] [--sleep] --json`.
/// `keepIcon` is the app's own and never the command's.
public func quitCommand(_ choices: QuitChoices) -> [String] {
  var args = ["quit", choices.busy == .letFinish ? "--let-finish" : "--stop-now"]
  if choices.pauseMorning { args.append("--pause-morning") }
  if choices.sleep { args.append("--sleep") }
  return args + ["--json"]
}

public enum QuitStep: Equatable, Sendable {
  /// `bosun quit --plan --json` is being asked.
  case planning
  /// `quit --plan` said it could not look (the windows cannot be read): the reason, with the choices and
  /// "Stop Now", which does not need the look. This is how a quit that did not finish is finished.
  case unavailable(String)
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
  case cancelled

  public var isFinished: Bool {
    switch self {
    case .failed, .done, .cancelled: return true
    default: return false
    }
  }

  public var quitsTheApp: Bool {
    if case .done(let keepIcon, _) = self { return !keepIcon }
    return false
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
  case planFailed(String)
  /// bosun answered the plan with a failure of its own (its `error`), which a Stop Now can get past.
  case planUnavailable(String)
  case confirmed(QuitChoices)
  case busyChosen(BusyChoice)
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
  case (.planning, .planUnavailable(let why)):
    return (.unavailable(why), [])
  case (.unavailable, .confirmed(let choices)):
    return running(QuitChoices(copying: choices, busy: .stopNow))
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

// MARK: what a quit's reply means

let quitFailedText =
  "bosun quit did not complete. If bosun was meant to be stopped, run `bosun quit --stop-now` "
  + "again to finish, or `bosun start` to start bosun again."
let quitPlanFailedText =
  "bosun could not say what quitting would stop. Stop Now still works: it does not need to."
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
/// what is shown, and a Stop Now is offered, which `--stop-now` makes possible with unreadable windows.
func quitPlanEvent(from result: CommandResult) -> QuitEvent {
  if let failure = planFailureObject(result) {
    return .planUnavailable(failure.message ?? quitPlanFailedText)
  }
  switch decodeReply(QuitPlan.self, result) {
  case .success(let plan): return .planned(plan)
  case .failure(let failure): return .planFailed(failure.message)
  }
}

/// The failure object a plan that could not look printed (on a non-zero exit), if that is what it printed.
private func planFailureObject(_ result: CommandResult) -> QuitResult? {
  guard case .exited(let code) = result.outcome, code != 0 else { return nil }
  guard case .success(let failure) = decodeReply(QuitResult.self, result, trustingStdout: true) else {
    return nil
  }
  return failure
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
