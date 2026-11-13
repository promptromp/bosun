import Foundation

/// A clock the test moves: `sleep` returns when `advance` passes its deadline, so timing is exact and
/// no test waits on real time.
final class ManualClock: Clock, @unchecked Sendable {
  struct Instant: InstantProtocol {
    var offset: Swift.Duration

    func advanced(by duration: Swift.Duration) -> Instant { Instant(offset: offset + duration) }
    func duration(to other: Instant) -> Swift.Duration { other.offset - offset }
    static func < (lhs: Instant, rhs: Instant) -> Bool { lhs.offset < rhs.offset }
  }

  private struct Sleeper {
    let id: UUID
    let deadline: Instant
    let continuation: CheckedContinuation<Void, any Error>
  }

  private let lock = NSLock()
  private var current = Instant(offset: .zero)
  private var sleepers: [Sleeper] = []

  var now: Instant { lock.withLock { current } }
  var minimumResolution: Swift.Duration { .zero }

  var sleeperCount: Int { lock.withLock { sleepers.count } }

  func sleep(until deadline: Instant, tolerance: Swift.Duration?) async throws {
    let id = UUID()
    try await withTaskCancellationHandler {
      try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
        lock.lock()
        if Task.isCancelled {
          lock.unlock()
          continuation.resume(throwing: CancellationError())
        } else if deadline <= current {
          lock.unlock()
          continuation.resume()
        } else {
          sleepers.append(Sleeper(id: id, deadline: deadline, continuation: continuation))
          lock.unlock()
        }
      }
    } onCancel: {
      let found = lock.withLock { () -> Sleeper? in
        guard let index = sleepers.firstIndex(where: { $0.id == id }) else { return nil }
        return sleepers.remove(at: index)
      }
      found?.continuation.resume(throwing: CancellationError())
    }
  }

  /// Moves the clock on, waking each sleeper whose deadline it reaches.
  func advance(by duration: Swift.Duration) {
    let due = lock.withLock { () -> [Sleeper] in
      current = current.advanced(by: duration)
      let due = sleepers.filter { $0.deadline <= current }
      sleepers.removeAll { $0.deadline <= current }
      return due
    }
    for sleeper in due { sleeper.continuation.resume() }
  }

  /// Waits (in real time, up to five seconds) until a task sleeps on this clock with exactly this
  /// deadline: the sleeper a test means, whatever else sleeps here (the poller's own timer counts as
  /// one). False if none registered.
  func waitForSleeper(until deadline: Instant) async -> Bool {
    let end = Date().addingTimeInterval(5)
    while !hasSleeper(until: deadline) && Date() < end {
      try? await Task.sleep(for: .milliseconds(2))
    }
    return hasSleeper(until: deadline)
  }

  private func hasSleeper(until deadline: Instant) -> Bool {
    lock.withLock { sleepers.contains { $0.deadline == deadline } }
  }

  /// Waits (in real time, briefly) until `count` tasks sleep on this clock.
  func waitForSleepers(_ count: Int = 1) async {
    let end = Date().addingTimeInterval(5)
    while sleeperCount < count && Date() < end {
      try? await Task.sleep(for: .milliseconds(2))
    }
  }
}

/// Polls `condition` (in real time, briefly) until it holds; false when it never did.
func eventually(seconds: Double = 5, _ condition: @MainActor () async -> Bool) async -> Bool {
  let end = Date().addingTimeInterval(seconds)
  while Date() < end {
    if await condition() { return true }
    try? await Task.sleep(for: .milliseconds(2))
  }
  return await condition()
}
