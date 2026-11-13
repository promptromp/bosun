import Foundation

// The app's behaviour, with no AppKit in it: it holds what the app knows (the status, the update's state,
// which flow runs, whether Open at Login is on), draws the menu through `AppUI`, and carries out what is
// chosen by running `bosun` commands and asking the person through dialogs. The shell implements `AppUI`
// (the status item, the alerts, the workspace); everything the flows decide is here, tested with a fake
// bosun and a fake shell.

@MainActor
public protocol AppUI: AnyObject {
  /// Draws the menu (and, for the test hooks, writes it out). `status` is nil before the first look.
  func render(_ model: MenuModel, status: StatusState?)
  /// Shows a dialog and waits for the person's answer.
  func present(_ spec: DialogSpec) async -> DialogAnswer
  /// Opens a web address, a file or a folder with whatever the Mac opens it with.
  func open(_ url: URL)
  func terminate()
}

@MainActor
public final class AppCoordinator {
  /// How long `update --yes` has to show in `status` before it is looked into.
  static let startGrace: Duration = .seconds(90)

  private let cli: any BosunCLI
  private let ui: any AppUI
  private let options: LaunchOptions
  private let clock: any Clock<Duration>
  private let now: @Sendable () -> Date
  private let script: ScriptSettings

  public private(set) var status: StatusState?
  public private(set) var update: UpdateState = .idle
  public private(set) var flow = FlowState()
  public private(set) var login = false

  private var throttle = UpdateCheckThrottle()
  private var launchDecided = false
  private var checkIsManual = false
  private var failureShowing = false
  private lazy var poller = Poller(clock: clock) { [weak self] reason in
    await self?.look(reason)
  }

  public init(
    cli: any BosunCLI, ui: any AppUI, options: LaunchOptions, clock: any Clock<Duration> = ContinuousClock(),
    now: @escaping @Sendable () -> Date = { Date() }, script: ScriptSettings
  ) {
    self.cli = cli
    self.ui = ui
    self.options = options
    self.clock = clock
    self.now = now
    self.script = script
  }

  public var menuModel: MenuModel {
    guard let status else { return loadingMenu(flow: flow) }
    return menu(for: status, update: update, flow: flow, login: login)
  }

  private func render() {
    ui.render(menuModel, status: status)
  }

  private var snapshot: StatusSnapshot? {
    if case .loaded(let snapshot, _)? = status { return snapshot }
    return nil
  }

  // MARK: looking

  /// Draws what is known, and starts looking: now, then every 30 seconds.
  public func start() async {
    render()
    await poller.start()
  }

  public func stop() async {
    await poller.stop()
  }

  /// Asks bosun for its status now (not while the app's own quit runs, and not twice at once).
  public func refresh(_ reason: RefreshReason) async {
    await poller.refreshNow(reason)
  }

  public func menuWillOpen() async { await refresh(.menuOpened) }

  public func didWake() async { await refresh(.wake) }

  /// One look: the status, the login when it is likely to have changed, and the update check when it is due.
  private func look(_ reason: RefreshReason) async {
    let state = await loadStatus(via: cli, previous: snapshot)
    status = state
    if case .loaded(let snapshot, current: true) = state {
      _ = await drain([snapshot.updateEvent])
      surfaceFailureInBackground()
    }
    if [.launch, .menuOpened, .resumed].contains(reason) { await readLogin() }
    render()
    await startAtLaunch(state)
    await checkInBackground(reason)
  }

  private func readLogin() async {
    if case .success(let app) = await fetch(AppStatus.self, Bosun.appStatus, via: cli) {
      login = app.login
    }
  }

  /// Opened by you with bosun stopped (or red): start it, once, on the first status that says so.
  private func startAtLaunch(_ state: StatusState) async {
    if case .unreachable = state { return }
    guard !launchDecided else { return }
    launchDecided = true
    if shouldStartAtLaunch(options, status: state) { await startBosun() }
  }

  // MARK: choosing

  /// An item chosen, by a click or a test hook. A greyed item, or one the menu has not, does nothing.
  public func perform(_ id: ItemID) async {
    guard menuModel.find(id)?.enabled == true else { return }
    switch id {
    case .startBosun: await startBosun()
    case .showProblems: await showProblems()
    case .openConsole: await openConsole()
    case .checkForUpdates: await checkForUpdates()
    case .updateBosun: await updateBosun()
    case .openAtLogin: await toggleLogin()
    case .helpMenu: break
    case .help(let topic): await help(topic)
    case .quit: await quit()
    }
  }

  private func fail(_ title: String, _ why: String) async {
    _ = await ui.present(failureSpec(title, why))
  }

  private func startBosun() async {
    guard flow.begin(.start) else { return }
    render()
    let result = await cli.start(Bosun.start)
    if result.outcome != .started { await fail("bosun did not start", failureText(result)) }
    flow.end(.start)
    render()
    await refresh(.afterAction)
  }

  private func showProblems() async {
    guard let snapshot else { return }
    _ = await ui.present(problemsSpec(snapshot.problems))
  }

  private func openConsole() async {
    switch consoleURL(from: await cli.run(Bosun.consoleURL)) {
    case .success(let url): ui.open(url)
    case .failure(let error): await fail("Cannot open the console", error.message)
    }
  }

  private func toggleLogin() async {
    let result = await cli.run(Bosun.appLogin(on: !login))
    guard result.outcome == .exited(0) else {
      await fail("Cannot change Open at Login", failureText(result))
      return
    }
    login.toggle()
    await readLogin()
    render()
  }

  private func help(_ topic: HelpTopic) async {
    switch topic {
    case .guide: ui.open(Links.guide)
    case .doctor: await openTerminal(running: ["doctor"])
    case .uninstall: await openTerminal(running: ["uninstall"])
    case .logs: if let logs = snapshot?.logs, !logs.isEmpty { ui.open(URL(fileURLWithPath: logs)) }
    }
  }

  private func openTerminal(running args: [String]) async {
    do {
      ui.open(try CommandScript.write(executable: script.executable, args: args, folder: script.folder))
    } catch {
      await fail("Cannot open Terminal", error.localizedDescription)
    }
  }

  // MARK: Quit

  private func quit() async {
    guard let status, quitAction(for: status) == .runFlow else {
      ui.terminate()
      return
    }
    guard flow.begin(.quit) else { return }
    await poller.pause()
    render()
    let step = await driveQuit()
    await finishQuit(step)
  }

  /// The quit flow's steps until it is finished: each step's dialog or command makes the next event.
  private func driveQuit() async -> QuitStep {
    var (step, _) = startQuit()
    var event = await quitPlanEvent(via: cli)
    while true {
      (step, _) = next(step, on: event)
      if step.isFinished { return step }
      event = await nextQuitEvent(after: step)
    }
  }

  private func nextQuitEvent(after step: QuitStep) async -> QuitEvent {
    switch step {
    case .first(let plan): return await ask(quitFirstSpec(plan), step)
    case .unavailable(let why): return await ask(quitUnavailableSpec(why), step)
    case .busy(let names, _): return await ask(quitBusySpec(names), step)
    case .stillBusy(let names, _): return await ask(quitStillBusySpec(names), step)
    case .running(let choices): return await runQuit(choices, via: cli)
    case .planning: return await quitPlanEvent(via: cli)
    case .failed, .done, .cancelled: return .cancelled
    }
  }

  private func ask(_ spec: DialogSpec, _ step: QuitStep) async -> QuitEvent {
    quitEvent(for: step, answer: await ui.present(spec))
  }

  /// What the quit left: a failure is told, a note too; the app leaves unless the icon is kept; else the
  /// poller is let go and looks once.
  private func finishQuit(_ step: QuitStep) async {
    switch step {
    case .done(_, let note):
      if let note { _ = await ui.present(quitNoteSpec(note)) }
      if step.quitsTheApp {
        ui.terminate()
        return
      }
    case .failed(let why):
      await fail("Quit did not finish", why)
    default:
      break
    }
    flow.end(.quit)
    render()
    await poller.resume()
  }

  // MARK: updates

  /// The update flow's events, one after the other: each makes the next state, which is drawn, and the
  /// commands it asks for are run, their outcomes being events. Returns the events it took.
  private func drain(_ first: [UpdateEvent]) async -> [UpdateEvent] {
    var queue = first
    var taken: [UpdateEvent] = []
    while !queue.isEmpty {
      let event = queue.removeFirst()
      taken.append(event)
      if case .checkRequested(let manual) = event { checkIsManual = manual }
      let (state, commands) = next(update, on: event)
      update = state
      render()
      for command in commands { queue += await execute(command) }
    }
    return taken
  }

  private func execute(_ command: Command) async -> [UpdateEvent] {
    switch command {
    case Bosun.checkUpdates:
      let manual = checkIsManual
      checkIsManual = false
      return [await checkEvent(via: cli, manual: manual)]
    case Bosun.planUpdate:
      return [await planEvent(via: cli)]
    case Bosun.update:
      return await startUpdate(command)
    default:
      let result = await cli.run(command)
      if result.outcome != .exited(0) { await fail("Cannot skip this version", failureText(result)) }
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

  /// The update check that is due (see `UpdateCheckThrottle`), quietly: a badge, never a dialog.
  private func checkInBackground(_ reason: RefreshReason) async {
    guard case .idle = update, throttle.allows(reason, at: now()) else { return }
    _ = await drain([.checkRequested(manual: false)])
  }

  /// A failure the person did not ask for (an update that never started): told, outside the look in progress.
  private func surfaceFailureInBackground() {
    guard case .failed = update, !failureShowing else { return }
    Task { await self.showFailure("Cannot update bosun") }
  }

  /// Tells the update flow's failure and lets it go: the state is `.failed` until it is.
  private func showFailure(_ title: String) async {
    guard case .failed(let why) = update, !failureShowing else { return }
    failureShowing = true
    await fail(title, why)
    failureShowing = false
    _ = await drain([.acknowledged])
  }

  /// "Check for Updates…" (or the offer it became, when an update is waiting): the flow with its dialogs.
  private func checkForUpdates() async {
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
  private func updateBosun() async {
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
