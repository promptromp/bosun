import Foundation

// The update flow's side of the coordinator: its events one after the other (each makes the next state, which
// is drawn, and the commands it asks for are run, their outcomes being events), the checks that run quietly in
// the background, and the dialogs of a check the person asked for: the offer, the plan, the failures.

extension AppCoordinator {
  // MARK: events and commands

  /// The next state for an event, drawn; the commands that state asks for are returned, not run.
  func apply(_ event: UpdateEvent) -> [Command] {
    if case .checkRequested(let manual) = event { checkIsManual = manual }
    let (state, commands) = next(update, on: event)
    update = state
    render()
    return commands
  }

  /// The events, one after the other, with the commands each asks for run and their outcomes taken as events.
  /// Returns the events it took.
  func drain(_ first: [UpdateEvent]) async -> [UpdateEvent] {
    var queue = first
    var taken: [UpdateEvent] = []
    while !queue.isEmpty {
      let event = queue.removeFirst()
      taken.append(event)
      for command in apply(event) { queue += await execute(command) }
    }
    return taken
  }

  func execute(_ command: Command) async -> [UpdateEvent] {
    switch command {
    case Bosun.checkUpdates:
      let manual = checkIsManual
      checkIsManual = false
      return [await checkEvent(via: cli, manual: manual)]
    case Bosun.planUpdate:
      return [await planEvent(via: cli)]
    case Bosun.update:
      return await startUpdate(command)
    case _ where command.args.starts(with: ["update", "--skip"]):
      let result = await cli.run(command)
      if result.outcome != .exited(0) { await fail("Cannot skip this version", failureText(result)) }
      return []
    default:
      // The flow asks for no other command
      return []
    }
  }

  /// `update --yes`, started and left to run; `status` shows it from then on. If it never shows, the grace
  /// runs out and it is looked into.
  private func startUpdate(_ command: Command) async -> [UpdateEvent] {
    let result = await cli.start(command)
    guard result.outcome == .started else { return [.startFailed(failureText(result))] }
    Task { [clock] in
      try? await clock.sleep(for: Self.startGrace)
      await self.graceElapsed()
    }
    return []
  }

  private func graceElapsed() async {
    guard case .starting = update else { return }
    _ = await drain([.startGaveUp])
    surfaceFailureInBackground()
  }

  // MARK: outside a look

  /// Commands the status asked for (the check after an update has finished), run outside the look: one at
  /// a time after the last, so a `git ls-remote` that hangs never holds a look.
  func runInBackground(_ commands: [Command]) {
    guard !commands.isEmpty else { return }
    let before = backgroundWork
    backgroundWork = Task {
      await before?.value
      var events: [UpdateEvent] = []
      for command in commands { events += await self.execute(command) }
      _ = await self.drain(events)
      self.surfaceFailureInBackground()
    }
  }

  /// The update check that is due (see `UpdateCheckThrottle`), quietly: a badge, never a dialog.
  func checkInBackground(_ reason: RefreshReason) {
    guard case .idle = update, throttle.allows(reason, at: now()) else { return }
    runInBackground(apply(.checkRequested(manual: false)))
  }

  /// A failure the person did not ask for (an update that never started): told outside the look in progress.
  func surfaceFailureInBackground() {
    guard case .failed = update, !failureShowing else { return }
    failureWork = Task { await self.showFailure("Cannot update bosun") }
  }

  /// Tells the update flow's failure and lets it go: the state is `.failed` until it is.
  func showFailure(_ title: String) async {
    guard case .failed(let why) = update, !failureShowing else { return }
    failureShowing = true
    await fail(title, why)
    failureShowing = false
    _ = await drain([.acknowledged])
  }

  // MARK: the person's checks

  /// "Check for Updates…" (or the offer it became, when an update is waiting): the flow with its dialogs.
  func checkForUpdates() async {
    guard flow.begin(.update) else { return }
    render()
    if case .available(let check) = update {
      await offer(check)
    } else {
      await afterManualCheck(await drain([.checkRequested(manual: true)]))
    }
    flow.end(.update)
    render()
  }

  /// "Update bosun…", when bosun speaks a contract this app does not: the same flow, from its check.
  func updateBosun() async {
    let start = updateBosunStart()
    guard flow.begin(start.flow) else { return }
    render()
    if case .available(let check) = update {
      await offer(check)
    } else {
      update = start.state
      checkIsManual = true
      render()
      var events: [UpdateEvent] = []
      for command in start.commands { events += await execute(command) }
      await afterManualCheck(await drain(events))
    }
    flow.end(.update)
    render()
  }

  /// What a check the person asked for came to: an offer, a failure, or "up to date".
  private func afterManualCheck(_ events: [UpdateEvent]) async {
    switch update {
    case .available(let check):
      await offer(check)
    case .failed:
      await showFailure("Cannot check for updates")
    default:
      for case .checked(let check, manual: true) in events where check.error == nil {
        _ = await ui.present(upToDateSpec(check))
      }
    }
  }

  private func offer(_ check: UpdateCheck) async {
    let answer = await ui.present(updateOfferSpec(check))
    _ = await drain([updateOfferEvent(answer: answer)])
    if case .confirming(let plan, let check) = update {
      await confirm(plan, check)
    } else {
      await showFailure("Cannot update bosun")
    }
  }

  private func confirm(_ plan: UpdatePlan, _ check: UpdateCheck) async {
    let answer = await ui.present(updatePlanSpec(plan, check: check))
    _ = await drain([updatePlanEvent(plan: plan, answer: answer)])
    await showFailure("Cannot update bosun")
  }
}
