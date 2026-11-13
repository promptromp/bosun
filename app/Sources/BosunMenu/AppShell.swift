import AppKit
import BosunMenuCore

// The shell that ties the pieces together: it is what the core's coordinator draws and asks through
// (`AppUI`): the status item, the alerts, the workspace; and it routes what happens to the app (a menu item
// chosen, the menu opening, the Mac waking) to the coordinator.

@MainActor
protocol URLOpening {
  func open(_ url: URL)
}

/// Opens a web address, a file or a folder with whatever the Mac opens it with (a `.command` file: Terminal).
@MainActor
struct WorkspaceOpener: URLOpening {
  func open(_ url: URL) {
    NSWorkspace.shared.open(url)
  }
}

/// Calls back when the Mac wakes from sleep, so the status is current at once, not 30 seconds later.
@MainActor
final class WakeObserver {
  private struct Handler: @unchecked Sendable {
    let call: @MainActor () -> Void
  }

  private let center: NotificationCenter
  private var token: (any NSObjectProtocol)?

  init(
    center: NotificationCenter = NSWorkspace.shared.notificationCenter,
    onWake: @escaping @MainActor () -> Void
  ) {
    self.center = center
    let handler = Handler(call: onWake)
    token = center.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { _ in
      MainActor.assumeIsolated { handler.call() }
    }
  }

  func stop() {
    if let token { center.removeObserver(token) }
    token = nil
  }
}

@MainActor
final class AppShell: AppUI {
  private let hookFiles: TestHookFiles?
  private let presenter = AlertPresenter()
  private let opener: any URLOpening
  private let cli: any BosunCLI
  private let options: LaunchOptions
  private var statusItem: StatusItemController?
  private var hooks: HookRunner?
  private var wake: WakeObserver?
  private lazy var coordinator = AppCoordinator(
    cli: cli, ui: self, options: options,
    script: ScriptSettings(
      executable: ProcessBosunCLI.installLink(),
      folder: FileManager.default.temporaryDirectory.appendingPathComponent("bosun-app")))

  init(options: LaunchOptions) {
    self.options = options
    let real = ProcessBosunCLI.standard()
    if let folder = options.testHooks {
      let files = TestHookFiles(directory: folder)
      do {
        try files.prepare()
      } catch {
        files.log(.error("cannot prepare the hooks folder: \(error.localizedDescription)"))
      }
      hookFiles = files
      cli = LoggingBosunCLI(wrapping: real) { files.log($0) }
      opener = RecordingOpener(files: files)
    } else {
      hookFiles = nil
      cli = real
      opener = WorkspaceOpener()
    }
  }

  /// Shows the icon, starts looking at bosun, and listens for what changes the status.
  func start() async {
    let item = StatusItemController { [weak self] id in
      guard let self else { return }
      Task { await self.coordinator.perform(id) }
    }
    item.onOpen = { [weak self] in
      guard let self else { return }
      Task { await self.coordinator.menuWillOpen() }
    }
    statusItem = item
    wake = WakeObserver { [weak self] in
      guard let self else { return }
      Task { await self.coordinator.didWake() }
    }
    startHooks()
    await coordinator.start()
  }

  private func startHooks() {
    guard let files = hookFiles else { return }
    let runner = HookRunner(
      files: files,
      perform: { [weak self] id in
        guard let self else { return }
        Task { await self.coordinator.perform(id) }
      },
      answer: { [weak self] hook in self?.presenter.answerOpenDialog(hook) ?? false },
      dialogOpen: { [weak self] in self?.presenter.hasOpenDialog ?? false })
    runner.start()
    hooks = runner
  }

  func stop() {
    hooks?.stop()
    wake?.stop()
  }

  // MARK: AppUI

  func render(_ model: MenuModel, status: StatusState?) {
    statusItem?.show(model)
    guard let files = hookFiles else { return }
    do {
      try files.writeMenu(model, status: status)
    } catch {
      files.log(.error("cannot write menu.json: \(error.localizedDescription)"))
    }
  }

  func present(_ spec: DialogSpec) async -> DialogAnswer {
    await presenter.present(spec)
  }

  func open(_ url: URL) {
    opener.open(url)
  }

  func terminate() {
    NSApp.terminate(nil)
  }
}
