import Foundation

// The update flow of docs/design/menu-bar.md, as a pure state machine: a state and an event make the
// next state and the `bosun` commands to run. The shell runs the commands and feeds their outcome back as
// events (`checkEvent`, `planEvent`); it reads the update's progress back from `status` (`updateEvent`).
// `bosun update` stays the one updater: the app only asks (--check), shows the plan (--dry-run), and
// starts `update --yes` detached (Start is `morning --now --background`, a short command).

public enum UpdateState: Equatable, Sendable {
  case idle
  case checking
  case available(UpdateCheck)
  /// The plan is being made for the offered update (kept, so a cancel goes back to the offer).
  case planning(UpdateCheck)
  case confirming(UpdatePlan, UpdateCheck)
  /// `update --yes` was started and `status` does not yet show it running.
  case starting(UpdateCheck)
  /// `starting` went on too long: a fresh check, to tell an update that never began from one that
  /// has already finished.
  case checkingStart(UpdateCheck)
  case running(waitingFor: [String])
  case failed(String)
}

public enum UpdateEvent: Equatable, Sendable {
  /// `manual`: the person asked (the menu item), rather than the six-hourly or menu-open check.
  case checkRequested(manual: Bool)
  case checked(UpdateCheck, manual: Bool)
  case checkFailed(String, manual: Bool)
  case updateNowChosen
  case skipChosen
  case remindLater
  /// The dry run's plan; nil when it made none (up to date).
  case planned(UpdatePlan?)
  case planFailed(String)
  case confirmed
  case cancelled
  case statusShows(running: Bool, waitingFor: [String])
  /// `starting` went on too long without `status` showing the update: the shell's grace has run out.
  /// The app looks again before it calls that a failure.
  case startGaveUp
  /// `update --yes` could not be started at all (no bosun, no permission): why.
  case startFailed(String)
  /// The grace ran out while the app could not read bosun's status (its contract is newer): the update was
  /// started and cannot be watched, so it is let go without a verdict.
  case startUnwatched
  case acknowledged
}

public let noPlanText =
  "bosun made no plan: it is up to date, or the update was refused. Run bosun update in a terminal to see which."
public let updateNeverStartedText =
  "The update did not start. Run bosun update in a terminal to see why."

/// Choosing "Update bosun…" (bosun speaks a contract this app does not): the update flow, from its
/// check, as for "Check for Updates…". The shell begins `Flow.update` and runs the commands.
public func updateBosunStart() -> FlowStart {
  let (state, commands) = next(UpdateState.idle, on: UpdateEvent.checkRequested(manual: true))
  return FlowStart(flow: .update, state: state, commands: commands)
}

public struct FlowStart: Equatable, Sendable {
  public let flow: Flow
  public let state: UpdateState
  public let commands: [Command]
}

/// The state after an event, and what to run. An event that means nothing in the state changes nothing.
public func next(_ state: UpdateState, on event: UpdateEvent) -> (UpdateState, [Command]) {
  nextChecking(state, on: event) ?? nextPlanning(state, on: event)
    ?? nextWhileRunning(state, on: event) ?? (state, [])
}

private typealias Step = (UpdateState, [Command])

/// Checking for an update and the offer of one.
private func nextChecking(_ state: UpdateState, on event: UpdateEvent) -> Step? {
  switch (state, event) {
  case (.idle, .checkRequested), (.failed, .checkRequested):
    return (.checking, [Bosun.checkUpdates])
  case (.checking, .checked(let check, let manual)):
    return (afterCheck(check, manual: manual), [])
  case (.checking, .checkFailed(let why, let manual)):
    return (manual ? .failed(why) : .idle, [])
  case (.failed, .acknowledged):
    return (.idle, [])
  case (.checkingStart(let before), .checked(let check, _)):
    return (stillOffered(check, like: before) ? .failed(updateNeverStartedText) : .idle, [])
  case (.checkingStart, .checkFailed):
    return (.idle, [])
  case (.available(let check), .updateNowChosen):
    return (.planning(check), [Bosun.planUpdate])
  case (.available(let check), .skipChosen):
    return (.idle, check.latest.map { [Bosun.skip(version: $0.version)] } ?? [])
  default:
    return nil
  }
}

/// The plan, and the person's yes or no to it.
private func nextPlanning(_ state: UpdateState, on event: UpdateEvent) -> Step? {
  switch (state, event) {
  case (.planning(let check), .planned(let plan)):
    return (plan.map { .confirming($0, check) } ?? .failed(noPlanText), [])
  case (.planning, .planFailed(let why)):
    return (.failed(why), [])
  case (.confirming(let plan, let check), .confirmed):
    return plan.isBlocked ? (state, []) : (.starting(check), [Bosun.update])
  case (.confirming(_, let check), .cancelled):
    return (.available(check), [])
  default:
    return nil
  }
}

/// What the status and the grace period say, which can happen in most states.
private func nextWhileRunning(_ state: UpdateState, on event: UpdateEvent) -> Step? {
  switch (state, event) {
  case (.planning, .statusShows), (.confirming, .statusShows):
    return (state, [])
  case (.running, .statusShows(false, _)):
    return (.checking, [Bosun.checkUpdates])
  case (_, .statusShows(true, let waiting)):
    return (.running(waitingFor: waiting), [])
  case (.starting(let check), .startGaveUp):
    return (.checkingStart(check), [Bosun.checkUpdates])
  case (.starting, .startFailed(let why)):
    return (.failed(why), [])
  case (.starting, .startUnwatched):
    return (.idle, [])
  default:
    return nil
  }
}

/// Whether the status is to be looked at promptly (every few seconds, not every 30): while an update `update
/// --yes` was started for has yet to show in the status (its grace is bounded), and while a status that is
/// current shows one running, so that its end is drawn within seconds. A status that could not be read, or a
/// kept one, is no reason to ask more often.
public func wantsPromptLooks(_ update: UpdateState, status: StatusState?) -> Bool {
  switch (update, status) {
  case (.starting, _), (.running, .loaded(_, current: true)?): return true
  default: return false
  }
}

/// The same release is still on offer: what `update --yes` was to install has not been installed.
private func stillOffered(_ check: UpdateCheck, like before: UpdateCheck) -> Bool {
  check.error == nil && check.available && check.latest?.version == before.latest?.version
}

private func afterCheck(_ check: UpdateCheck, manual: Bool) -> UpdateState {
  if let why = check.error { return manual ? .failed(why) : .idle }
  guard check.available, check.latest != nil, manual || !check.skipped else { return .idle }
  return .available(check)
}

// MARK: running the commands

public func checkEvent(via cli: any BosunCLI, manual: Bool) async -> UpdateEvent {
  switch await fetch(UpdateCheck.self, Bosun.checkUpdates, via: cli) {
  case .success(let check): return .checked(check, manual: manual)
  case .failure(let failure): return .checkFailed(failure.message, manual: manual)
  }
}

public func planEvent(via cli: any BosunCLI) async -> UpdateEvent {
  planEvent(from: await cli.run(Bosun.planUpdate))
}

/// A dry run's reply as an event. It prints its object on every path, so a refusal has no plan and a
/// line on stderr saying why, which is shown rather than "no plan".
func planEvent(from result: CommandResult) -> UpdateEvent {
  switch decodeReply(UpdateDryRun.self, result, trustingStdout: true) {
  case .failure(let failure): return .planFailed(failure.message)
  case .success(let run):
    if let plan = run.plan { return .planned(plan) }
    if case .exited(let code) = result.outcome, code != 0, !firstLine(result.stderr).isEmpty {
      return .planFailed(firstLine(result.stderr))
    }
    return .planned(nil)
  }
}
