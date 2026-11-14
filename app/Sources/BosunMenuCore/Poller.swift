import Foundation

// The status is asked for every 30 seconds, when the menu opens, and when the Mac wakes; every 2 seconds
// while the coordinator asks for prompt looks (an update under way, so its end shows at once). The timing is
// the poller's alone and runs on a clock it is given, so it is tested without waiting. Asks that overlap
// are one more refresh after the one running; while paused (the app's own quit) nothing is asked.

public enum RefreshReason: Equatable, Sendable {
  case launch, timer, menuOpened, wake
  /// The app's own quit is over (or was cancelled): one look at what it left.
  case resumed
  /// After an action that changes bosun (Start): look at what it did.
  case afterAction
}

public actor Poller {
  private let clock: any Clock<Duration>
  private let interval: Duration
  private let promptInterval: Duration
  private let refresh: @Sendable (RefreshReason) async -> Void
  private var timer: Task<Void, Never>?
  /// The timer's current wait.
  private var sleeping: Task<Void, any Error>?
  private var inFlight = false
  private var pending: RefreshReason?
  public private(set) var isPaused = false
  /// Whether the timer asks every `promptInterval` rather than every `interval`.
  public private(set) var isPrompt = false

  public init(
    clock: any Clock<Duration> = ContinuousClock(), interval: Duration = .seconds(30),
    promptInterval: Duration = .seconds(2), refresh: @escaping @Sendable (RefreshReason) async -> Void
  ) {
    self.clock = clock
    self.interval = interval
    self.promptInterval = promptInterval
    self.refresh = refresh
  }

  /// Asks now, and then every interval. Starting twice keeps the one timer.
  public func start() async {
    guard timer == nil else { return }
    timer = Task { [weak self] in
      while !Task.isCancelled {
        switch await self?.sleepOnce() {
        case .elapsed?: await self?.refreshNow(.timer)
        case .restarted?: continue
        case .stopped?, nil: return
        }
      }
    }
    await refreshNow(.launch)
  }

  public func stop() {
    timer?.cancel()
    timer = nil
    sleeping?.cancel()
    sleeping = nil
  }

  /// Prompt looks on or off. A change cuts the timer's wait short and starts it afresh at the new interval (a
  /// wait of 30 seconds begun before is not sat out); asking for what is already so changes nothing. Only the
  /// wait is cut, never a look in progress (a look asks for this itself, from inside the timer's task).
  public func setPrompt(_ prompt: Bool) {
    guard prompt != isPrompt else { return }
    isPrompt = prompt
    sleeping?.cancel()
  }

  private enum Wake { case elapsed, restarted, stopped }

  /// One wait of the timer, in a task of its own so that a change of interval can cut it short without
  /// cancelling the timer's task (whose looks run commands).
  private func sleepOnce() async -> Wake {
    // Stopped before this wait began: `stop` had no wait to cut
    if Task.isCancelled { return .stopped }
    let clock = clock
    let wait = isPrompt ? promptInterval : interval
    let sleep = Task { try await clock.sleep(for: wait) }
    sleeping = sleep
    do {
      try await sleep.value
      return .elapsed
    } catch {
      return Task.isCancelled ? .stopped : .restarted
    }
  }

  /// Asks at once, unless paused. An ask that comes while one runs makes one more after it (with its
  /// reason), however many come.
  public func refreshNow(_ reason: RefreshReason) async {
    guard !isPaused else { return }
    if inFlight {
      pending = reason
      return
    }
    inFlight = true
    var next: RefreshReason? = reason
    while let current = next {
      pending = nil
      await refresh(current)
      next = isPaused ? nil : pending
    }
    pending = nil
    inFlight = false
  }

  public func pause() {
    isPaused = true
  }

  /// Ends a pause with one look at what it left.
  public func resume() async {
    guard isPaused else { return }
    isPaused = false
    await refreshNow(.resumed)
  }
}

/// How often the update check runs: the start and every six hours, and when the menu opens at most once a
/// minute (a look is a `git ls-remote`).
public struct UpdateCheckThrottle: Sendable {
  public static let periodic: TimeInterval = 6 * 3600
  public static let menuOpen: TimeInterval = 60
  private var last: Date?

  public init() {}

  /// Whether a check may run for this reason now; if so, it is counted as run.
  public mutating func allows(_ reason: RefreshReason, at now: Date) -> Bool {
    let wait: TimeInterval
    switch reason {
    case .launch, .timer, .wake: wait = Self.periodic
    case .menuOpened: wait = Self.menuOpen
    case .resumed, .afterAction: return false
    }
    // A clock that went backwards counts as long ago
    if let last, now >= last, now.timeIntervalSince(last) < wait { return false }
    last = now
    return true
  }
}
