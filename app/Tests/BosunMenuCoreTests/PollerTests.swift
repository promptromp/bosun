import Foundation
import Testing

@testable import BosunMenuCore

actor Refreshes {
  private(set) var reasons: [RefreshReason] = []
  private var gate: CheckedContinuation<Void, Never>?
  private var holdFirst = false

  func holdTheFirst() { holdFirst = true }

  func record(_ reason: RefreshReason) async {
    reasons.append(reason)
    if holdFirst {
      holdFirst = false
      await withCheckedContinuation { gate = $0 }
    }
  }

  func release() {
    gate?.resume()
    gate = nil
  }

  var count: Int { reasons.count }
}

@Suite struct PollerTests {
  private func make(_ clock: ManualClock, _ refreshes: Refreshes) -> Poller {
    Poller(clock: clock, interval: .seconds(30)) { reason in await refreshes.record(reason) }
  }

  @Test func refreshesAtLaunchAndThenEveryThirtySeconds() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    #expect(await refreshes.reasons == [.launch])
    await clock.waitForSleepers()
    clock.advance(by: .seconds(29))
    try? await Task.sleep(for: .milliseconds(30))
    #expect(await refreshes.count == 1)
    clock.advance(by: .seconds(1))
    #expect(await eventually { await refreshes.count == 2 })
    await clock.waitForSleepers()
    clock.advance(by: .seconds(30))
    #expect(await eventually { await refreshes.count == 3 })
    #expect(await refreshes.reasons == [.launch, .timer, .timer])
    await poller.stop()
  }

  /// Review focus 3: the Mac wakes and the status refreshes at once, not after 30 s.
  @Test func aWakeRefreshesAtOnce() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await poller.refreshNow(.wake)
    #expect(await refreshes.reasons == [.launch, .wake])
    await poller.stop()
  }

  @Test func theMenuOpeningRefreshesAtOnce() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await poller.refreshNow(.menuOpened)
    #expect(await refreshes.reasons == [.launch, .menuOpened])
    await poller.stop()
  }

  @Test func aPausedPollerAsksNothing() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await poller.pause()
    await clock.waitForSleepers()
    clock.advance(by: .seconds(30))
    try? await Task.sleep(for: .milliseconds(30))
    await poller.refreshNow(.menuOpened)
    await poller.refreshNow(.wake)
    #expect(await refreshes.reasons == [.launch])
    #expect(await poller.isPaused)
    await poller.stop()
  }

  /// The app's own quit pauses it; it refreshes once when the quit is over.
  @Test func resumingRefreshesOnceAndTheTimerGoesOn() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await poller.pause()
    await poller.resume()
    #expect(await refreshes.reasons == [.launch, .resumed])
    #expect(await !poller.isPaused)
    await clock.waitForSleepers()
    clock.advance(by: .seconds(30))
    #expect(await eventually { await refreshes.count == 3 })
    await poller.stop()
  }

  @Test func resumingWhenNotPausedDoesNothing() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.resume()
    #expect(await refreshes.reasons.isEmpty)
  }

  @Test func askingWhileARefreshIsRunningRunsOneMoreAfterIt() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    await refreshes.holdTheFirst()
    let poller = make(clock, refreshes)
    let first = Task { await poller.start() }
    #expect(await eventually { await refreshes.count == 1 })
    // Three asks while the first is out: they make one more refresh, with the last one's reason
    await poller.refreshNow(.menuOpened)
    await poller.refreshNow(.wake)
    await poller.refreshNow(.menuOpened)
    #expect(await refreshes.count == 1)
    await refreshes.release()
    await first.value
    #expect(await refreshes.reasons == [.launch, .menuOpened])
    await poller.stop()
  }

  @Test func pausingWhileARefreshIsRunningDropsTheOneQueued() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    await refreshes.holdTheFirst()
    let poller = make(clock, refreshes)
    let first = Task { await poller.start() }
    #expect(await eventually { await refreshes.count == 1 })
    await poller.refreshNow(.wake)
    await poller.pause()
    await refreshes.release()
    await first.value
    #expect(await refreshes.reasons == [.launch])
    await poller.stop()
  }

  @Test func stoppedItAsksNothingMore() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await clock.waitForSleepers()
    await poller.stop()
    clock.advance(by: .seconds(60))
    try? await Task.sleep(for: .milliseconds(30))
    #expect(await refreshes.reasons == [.launch])
  }

  @Test func startingTwiceKeepsOneTimer() async {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await poller.start()
    await clock.waitForSleepers()
    #expect(clock.sleeperCount == 1)
    #expect(await refreshes.count == 1)
    await poller.stop()
  }
}

/// The throttle behind a reference, so `#expect` can ask it (a macro cannot call a mutating member).
final class Throttle {
  private var inner = UpdateCheckThrottle()
  func allows(_ reason: RefreshReason, at now: Date) -> Bool { inner.allows(reason, at: now) }
}

@Suite struct UpdateCheckThrottleTests {
  private let start = Date(timeIntervalSince1970: 1_800_000_000)

  @Test func theFirstCheckIsAllowedAtLaunch() {
    let throttle = Throttle()
    #expect(throttle.allows(.launch, at: start))
  }

  @Test func aTimerTickChecksEverySixHours() {
    let throttle = Throttle()
    #expect(throttle.allows(.launch, at: start))
    #expect(!throttle.allows(.timer, at: start.addingTimeInterval(30)))
    #expect(!throttle.allows(.timer, at: start.addingTimeInterval(6 * 3600 - 1)))
    #expect(throttle.allows(.timer, at: start.addingTimeInterval(6 * 3600)))
    // That check counts: the next is six hours after it
    #expect(!throttle.allows(.timer, at: start.addingTimeInterval(6 * 3600 + 30)))
  }

  @Test func theMenuOpeningChecksAtMostOnceAMinute() {
    let throttle = Throttle()
    #expect(throttle.allows(.menuOpened, at: start))
    #expect(!throttle.allows(.menuOpened, at: start.addingTimeInterval(59)))
    #expect(throttle.allows(.menuOpened, at: start.addingTimeInterval(60)))
    #expect(!throttle.allows(.menuOpened, at: start.addingTimeInterval(61)))
  }

  @Test func aWakeCountsAsATimerTick() {
    let throttle = Throttle()
    #expect(throttle.allows(.launch, at: start))
    #expect(!throttle.allows(.wake, at: start.addingTimeInterval(3600)))
    #expect(throttle.allows(.wake, at: start.addingTimeInterval(7 * 3600)))
  }

  @Test func resumingAfterAQuitNeverChecks() {
    let throttle = Throttle()
    #expect(!throttle.allows(.resumed, at: start))
    #expect(!throttle.allows(.afterAction, at: start))
  }

  @Test func aCheckThatWasRefusedIsNotRecorded() {
    let throttle = Throttle()
    #expect(!throttle.allows(.resumed, at: start))
    #expect(throttle.allows(.menuOpened, at: start.addingTimeInterval(1)))
  }

  @Test func aClockThatWentBackwardsChecksAgain() {
    let throttle = Throttle()
    #expect(throttle.allows(.launch, at: start))
    #expect(throttle.allows(.menuOpened, at: start.addingTimeInterval(-3600)))
  }
}
