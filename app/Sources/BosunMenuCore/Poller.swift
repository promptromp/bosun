import Foundation

// The status is asked for every 30 seconds, when the menu opens, and when the Mac wakes. The timing is
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
  private let refresh: @Sendable (RefreshReason) async -> Void
  private var timer: Task<Void, Never>?
  private var inFlight = false
  private var pending: RefreshReason?
  public private(set) var isPaused = false

  public init(
    clock: any Clock<Duration> = ContinuousClock(), interval: Duration = .seconds(30),
    refresh: @escaping @Sendable (RefreshReason) async -> Void
  ) {
    self.clock = clock
    self.interval = interval
    self.refresh = refresh
  }

  /// Asks now, and then every interval. Starting twice keeps the one timer.
  public func start() async {
    guard timer == nil else { return }
    let clock = clock
    let interval = interval
    timer = Task { [weak self] in
      while !Task.isCancelled {
        do {
          try await clock.sleep(for: interval)
        } catch {
          return
        }
        await self?.refreshNow(.timer)
      }
    }
    await refreshNow(.launch)
  }

  public func stop() {
    timer?.cancel()
    timer = nil
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
