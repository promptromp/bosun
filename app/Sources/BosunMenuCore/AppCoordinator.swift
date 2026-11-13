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

  // What the update flow keeps (AppCoordinator+Update.swift)
  var throttle = UpdateCheckThrottle()
  var checkIsManual = false
  var failureShowing = false

  // Work that goes on outside a look, so a slow command never holds the next look. Each is chained after the
  // last of its kind (one at a time), and `settle` waits for them.
  var backgroundWork: Task<Void, Never>?
  var failureWork: Task<Void, Never>?
  private var launchWork: Task<Void, Never>?
  private var launchDecided = false
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

  /// Waits for what went on outside the looks: the update checks, a failure being told, the start at launch.
  public func settle() async {
    for _ in 0..<3 {
      await launchWork?.value
      await backgroundWork?.value
      await failureWork?.value
    }
  }

  /// One look: the status, drawn at once; the login when it is likely to have changed. What the status asks of
  /// the update flow and the update check that is due run outside the look. A look that lands while the app's
  /// own quit runs is dropped (`quit.json` is written before the windows go: "bosun quit did not finish" is
  /// what it would show).
  private func look(_ reason: RefreshReason) async {
    let state = await loadStatus(via: cli, previous: snapshot)
    guard flow.active != .quit else { return }
    status = state
    if case .loaded(let snapshot, current: true) = state {
      runInBackground(apply(snapshot.updateEvent))
    }
    render()
    if [.launch, .menuOpened, .resumed].contains(reason) {
      await readLogin()
      guard flow.active != .quit else { return }
      render()
    }
    startAtLaunch(state)
    checkInBackground(reason)
  }

  private func readLogin() async {
    if case .success(let app) = await fetch(AppStatus.self, Bosun.appStatus, via: cli) {
      login = app.login
    }
  }

  /// Opened by you with bosun stopped (or red): start it, once, on the first status that says so. Its own
  /// task: a failure's dialog must not hold the look.
  private func startAtLaunch(_ state: StatusState) {
    if case .unreachable = state { return }
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
}
