import Foundation

// The update flow of docs/design/menu-bar.md, as a pure state machine: a state and an event make the
// next state and the `bosun` commands to run. The shell runs the commands and feeds their outcome back as
// events (`checkEvent`, `planEvent`); it reads the update's progress back from `status` (`updateEvent`).
// `bosun update` stays the one updater: the app only asks (--check), shows the plan (--dry-run), and
// starts `update --yes` detached.

public enum UpdateState: Equatable, Sendable {
  case idle
  case checking
  case available(UpdateCheck)
  case planning
  case confirming(UpdatePlan)
  /// `update --yes` was started and `status` does not yet show it running.
  case starting
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
  case startGaveUp
  case acknowledged
}

public let noPlanText =
  "bosun made no plan: it is up to date, or the update was refused. Run bosun update in a terminal to see which."
public let updateNeverStartedText =
  "The update did not start. Run bosun update in a terminal to see why."

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
  case (.available, .updateNowChosen):
    return (.planning, [Bosun.planUpdate])
  case (.available(let check), .skipChosen):
    return (.idle, check.latest.map { [Bosun.skip(version: $0.version)] } ?? [])
  default:
    return nil
  }
}

/// The plan, and the person's yes or no to it.
private func nextPlanning(_ state: UpdateState, on event: UpdateEvent) -> Step? {
  switch (state, event) {
  case (.planning, .planned(let plan)):
    return (plan.map { .confirming($0) } ?? .failed(noPlanText), [])
  case (.planning, .planFailed(let why)):
    return (.failed(why), [])
  case (.confirming(let plan), .confirmed):
    return plan.isBlocked ? (state, []) : (.starting, [Bosun.update])
  case (.confirming, .cancelled):
    return (.checking, [Bosun.checkUpdates])
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
  case (.starting, .startGaveUp):
    return (.failed(updateNeverStartedText), [])
  default:
    return nil
  }
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
  switch decodeReply(UpdateDryRun.self, result, contractRequired: false, trustingStdout: true) {
  case .failure(let failure): return .planFailed(failure.message)
  case .success(let run):
    if let plan = run.plan { return .planned(plan) }
    if case .exited(let code) = result.outcome, code != 0, !firstLine(result.stderr).isEmpty {
      return .planFailed(firstLine(result.stderr))
    }
    return .planned(nil)
  }
}
