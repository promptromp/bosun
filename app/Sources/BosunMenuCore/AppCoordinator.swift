import Foundation

// The app's behaviour, with no AppKit in it: it holds what the app knows (the status, the update's state,
// which flow runs, whether Open at Login is on), draws the menu through `AppUI`, and carries out what is
// chosen by running `bosun` commands and asking the person through dialogs. The shell implements `AppUI`
// (the status item, the alerts, the workspace); everything the flows decide is here, tested with a fake
// bosun and a fake shell. The update flow's side is in AppCoordinator+Update.swift.

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

  let cli: any BosunCLI
  let ui: any AppUI
  let clock: any Clock<Duration>
  let now: @Sendable () -> Date
  private let options: LaunchOptions
  private let scripts: any ScriptWriting

  public private(set) var status: StatusState?
  public internal(set) var update: UpdateState = .idle
  public internal(set) var flow = FlowState()
  public private(set) var login = false
  /// Whether the poller looks every few seconds (an update under way) rather than every 30: see `followUpdate`.
  public private(set) var looksPromptly = false

  // What the update flow keeps (AppCoordinator+Update.swift)
  var throttle = UpdateCheckThrottle()
  var checkIsManual = false
  var failureShowing = false
  /// The grace an `update --yes` was started with: its sleep, cancelled when the update leaves `starting`
  /// (so an earlier grace never fires for a later start), and the work that follows it.
  var graceSleep: Task<Void, any Error>?
  /// When the app last started `update --yes`: a record that began before it is an earlier update's.
  var updateStartedAt: Date?
  var graceWork: Task<Void, Never>?

  // Work that goes on outside a look, so a slow command never holds the next look. Each is chained after the
  // last of its kind (one at a time), and `settle` waits for them.
  var backgroundWork: Task<Void, Never>?
  var failureWork: Task<Void, Never>?
  private var launchWork: Task<Void, Never>?
  private var launchDecided = false
  /// Counts Open at Login's switches: a reading of it begun before the last switch is dropped, so a look
  /// that began earlier cannot put the old state back.
  private var loginGeneration = 0
  private lazy var poller = Poller(clock: clock) { [weak self] reason in
    await self?.look(reason)
  }

  public init(
    cli: any BosunCLI, ui: any AppUI, options: LaunchOptions, clock: any Clock<Duration> = ContinuousClock(),
    now: @escaping @Sendable () -> Date = { Date() }, scripts: any ScriptWriting
  ) {
    self.cli = cli
    self.ui = ui
    self.options = options
    self.clock = clock
    self.now = now
    self.scripts = scripts
  }

  public var menuModel: MenuModel {
    guard let status else { return loadingMenu(flow: flow) }
    return menu(for: status, update: update, flow: flow, login: login)
  }

  func render() {
    ui.render(menuModel, status: status)
  }

  private var snapshot: StatusSnapshot? {
    if case .loaded(let snapshot, _)? = status { return snapshot }
    return nil
  }

  // MARK: looking

  /// Draws what is known, and starts looking: now, then every 30 seconds (every 2 while an update is under way).
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

  /// Waits for what went on outside the looks (the update checks, a failure being told, the start at launch,
  /// a grace that woke), until none started more of it: the tests' way to let the background settle. A grace
  /// still asleep (the update is `starting`) is not waited for: it wakes only when its time comes.
  public func settle() async {
    while true {
      let before = [launchWork, backgroundWork, failureWork, graceWork]
      await launchWork?.value
      await backgroundWork?.value
      await failureWork?.value
      if !update.isStarting { await graceWork?.value }
      if [launchWork, backgroundWork, failureWork, graceWork] == before { return }
    }
  }

  /// One look: the status, drawn at once; the login when it is likely to have changed. What the status asks of
  /// the update flow and the update check that is due run outside the look. A look that lands while the app's
  /// own quit runs is dropped (`quit.json` is written before the windows go: "bosun quit did not finish" is
  /// what it would show).
  private func look(_ reason: RefreshReason) async {
    let state = await loadStatus(via: cli, previous: snapshot)
    guard flow.pollsStatus else { return }
    status = state
    if case .loaded(let snapshot, current: true) = state {
      runInBackground(updateEvents(snapshot).flatMap { apply($0) })
    }
    render()
    await followUpdate()
    if [.launch, .menuOpened, .resumed].contains(reason) {
      await readLogin()
      guard flow.pollsStatus else { return }
      render()
    }
    startAtLaunch(state)
    checkInBackground(reason)
  }

  /// What a status tells the update flow: whether an update runs, and, while the one the app started has not
  /// shown, whether its record says it already ended.
  private func updateEvents(_ snapshot: StatusSnapshot) -> [UpdateEvent] {
    guard update.isStarting, let startedAt = updateStartedAt,
      let end = endOfStartedUpdate(snapshot.update, startedAt: startedAt)
    else { return [snapshot.updateEvent] }
    return [snapshot.updateEvent, end]
  }

  /// The poller's cadence for the update's state: prompt looks while one is under way (`wantsPromptLooks`), so
  /// its end is drawn within seconds, and the 30 seconds again once it is over. Asked after every look and
  /// every run of the update flow's events.
  func followUpdate() async {
    let prompt = wantsPromptLooks(update, status: status)
    guard prompt != looksPromptly else { return }
    looksPromptly = prompt
    await poller.setPrompt(prompt)
  }

  /// Whether Open at Login is on, as `bosun app status` says; a reading begun before the last switch is
  /// dropped (it may say the state from before it).
  private func readLogin() async {
    let asked = loginGeneration
    guard case .success(let app) = await fetch(AppStatus.self, Bosun.appStatus, via: cli) else { return }
    if asked == loginGeneration { login = app.login }
  }

  /// Opened by you with bosun not running (stopped, or red with no window open): start it, once, on the first
  /// status that says so. Its own task: a failure's dialog must not hold the look.
  private func startAtLaunch(_ state: StatusState) {
    switch state {
    case .unreachable, .cannotRun: return
    default: break
    }
    guard !launchDecided else { return }
    launchDecided = true
    if shouldStartAtLaunch(options, status: state) {
      launchWork = Task { await self.startBosun() }
    }
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

  func fail(_ title: String, _ why: String) async {
    _ = await ui.present(failureSpec(title, why))
  }

  /// Start: `bosun morning --now --background`, which hands the morning run to a launchd job and returns, so
  /// bosun's processes are not the app's children. Its progress is read back from `status`.
  private func startBosun() async {
    guard flow.begin(.start) else { return }
    render()
    let result = await cli.run(Bosun.start)
    if result.outcome != .exited(0) { await fail("bosun did not start", failureText(result)) }
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

  /// Open at Login, switched: one at a time (the item is greyed meanwhile), and what a look read before or
  /// during the switch is dropped.
  private func toggleLogin() async {
    guard flow.begin(.login) else { return }
    render()
    loginGeneration += 1
    let result = await cli.run(Bosun.appLogin(on: !login))
    loginGeneration += 1
    if result.outcome == .exited(0) {
      login.toggle()
      await readLogin()
    } else {
      await fail("Cannot change Open at Login", failureText(result))
    }
    flow.end(.login)
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
      ui.open(try scripts.write(args: args))
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

  /// The quit flow's steps until it is finished: each step's dialog or command makes the next event. While
  /// `bosun quit` runs, a line under the status says what it waits for.
  private func driveQuit() async -> QuitStep {
    var (step, _) = startQuit()
    var event = await quitPlanEvent(via: cli)
    var busy: [String] = []
    while true {
      (step, _) = next(step, on: event)
      if step.isFinished { return step }
      busy = step.busyNames ?? busy
      flow.detail = quittingLine(step, busy: busy)
      render()
      event = await nextQuitEvent(after: step)
    }
  }

  private func nextQuitEvent(after step: QuitStep) async -> QuitEvent {
    switch step {
    case .first(let plan): return await ask(quitFirstSpec(plan), step)
    case .unavailable(let why, _): return await ask(quitUnavailableSpec(why), step)
    case .busy(let names, _): return await ask(quitBusySpec(names), step)
    case .stillBusy(let names, _): return await ask(quitStillBusySpec(names), step)
    case .running(let choices): return await runQuit(choices, via: cli)
    case .planning: return await quitPlanEvent(via: cli)
    case .failed, .done, .appOnly, .cancelled: return .cancelled
    }
  }

  private func ask(_ spec: DialogSpec, _ step: QuitStep) async -> QuitEvent {
    quitEvent(for: step, answer: await ui.present(spec))
  }

  /// What the quit left: a failure is told, a note too; the app leaves unless the icon is kept (or leaves
  /// alone, bosun as it is, when that was chosen); else the poller is let go and looks once.
  private func finishQuit(_ step: QuitStep) async {
    switch step {
    case .done(_, let note):
      if let note { _ = await ui.present(quitNoteSpec(note)) }
    case .failed(let why):
      await fail("Quit did not finish", why)
    default:
      break
    }
    if step.quitsTheApp {
      ui.terminate()
      return
    }
    flow.end(.quit)
    render()
    await poller.resume()
  }
}
