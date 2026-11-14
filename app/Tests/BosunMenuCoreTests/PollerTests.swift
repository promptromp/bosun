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

  @Test func refreshesAtLaunchAndThenEveryThirtySeconds() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    #expect(await refreshes.reasons == [.launch])
    await clock.waitForSleepers()
    clock.advance(by: .seconds(29))
    #expect(clock.sleeperCount == 1)  // the timer was not woken, so it cannot have asked
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
  @Test func aWakeRefreshesAtOnce() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await poller.refreshNow(.wake)
    #expect(await refreshes.reasons == [.launch, .wake])
    await poller.stop()
  }

  @Test func theMenuOpeningRefreshesAtOnce() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await poller.refreshNow(.menuOpened)
    #expect(await refreshes.reasons == [.launch, .menuOpened])
    await poller.stop()
  }

  @Test func aPausedPollerAsksNothing() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await poller.pause()
    await clock.waitForSleepers()
    clock.advance(by: .seconds(30))
    // The timer woke, was refused (paused) and sleeps again, to the next 30 seconds
    #expect(await clock.waitForSleeper(until: clock.now.advanced(by: .seconds(30))))
    await poller.refreshNow(.menuOpened)
    await poller.refreshNow(.wake)
    #expect(await refreshes.reasons == [.launch])
    #expect(await poller.isPaused)
    await poller.stop()
  }

  /// The app's own quit pauses it; it refreshes once when the quit is over.
  @Test func resumingRefreshesOnceAndTheTimerGoesOn() async throws {
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

  @Test func resumingWhenNotPausedDoesNothing() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.resume()
    #expect(await refreshes.reasons.isEmpty)
  }

  @Test func askingWhileARefreshIsRunningRunsOneMoreAfterIt() async throws {
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

  @Test func pausingWhileARefreshIsRunningDropsTheOneQueued() async throws {
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

  @Test func stoppedItAsksNothingMore() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    await clock.waitForSleepers()
    await poller.stop()
    #expect(clock.sleeperCount == 0)  // stopping took the timer's sleep away: nothing is left to wake
    clock.advance(by: .seconds(60))
    #expect(await refreshes.reasons == [.launch])
  }

  /// An update under way: the 30-second wait already begun is cut, and the timer asks every 2 seconds until
  /// prompt looks are off again.
  @Test func promptLooksAskEveryTwoSecondsAndThenThirtyAgain() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    #expect(await clock.waitForSleeper(until: clock.now.advanced(by: .seconds(30))))
    await poller.setPrompt(true)
    #expect(await poller.isPrompt)
    #expect(await clock.waitForSleeper(until: clock.now.advanced(by: .seconds(2))))
    clock.advance(by: .seconds(2))
    #expect(await eventually { await refreshes.count == 2 })
    #expect(await clock.waitForSleeper(until: clock.now.advanced(by: .seconds(2))))
    clock.advance(by: .seconds(2))
    #expect(await eventually { await refreshes.count == 3 })
    await poller.setPrompt(false)
    #expect(await clock.waitForSleeper(until: clock.now.advanced(by: .seconds(30))))
    #expect(clock.sleeperCount == 1)  // the 2-second wait was cut, not left beside the new one
    clock.advance(by: .seconds(29))
    #expect(await refreshes.count == 3)
    clock.advance(by: .seconds(1))
    #expect(await eventually { await refreshes.count == 4 })
    #expect(await refreshes.reasons == [.launch, .timer, .timer, .timer])
    await poller.stop()
  }

  @Test func promptLooksAskedForBeforeTheStartSetTheFirstWait() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.setPrompt(true)
    #expect(clock.sleeperCount == 0)
    await poller.start()
    #expect(await clock.waitForSleeper(until: clock.now.advanced(by: .seconds(2))))
    await poller.stop()
    #expect(clock.sleeperCount == 0)
  }

  @Test func askingForThePaceItHasKeepsItsWait() async throws {
    let clock = ManualClock()
    let refreshes = Refreshes()
    let poller = make(clock, refreshes)
    await poller.start()
    #expect(await clock.waitForSleeper(until: clock.now.advanced(by: .seconds(30))))
    clock.advance(by: .seconds(10))
    await poller.setPrompt(false)
    clock.advance(by: .seconds(20))
    #expect(await eventually { await refreshes.count == 2 })
    await poller.stop()
  }

  @Test func startingTwiceKeepsOneTimer() async throws {
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

  @Test func theFirstCheckIsAllowedAtLaunch() throws {
    let throttle = Throttle()
    #expect(throttle.allows(.launch, at: start))
  }

  @Test func aTimerTickChecksEverySixHours() throws {
    let throttle = Throttle()
    #expect(throttle.allows(.launch, at: start))
    #expect(!throttle.allows(.timer, at: start.addingTimeInterval(30)))
    #expect(!throttle.allows(.timer, at: start.addingTimeInterval(6 * 3600 - 1)))
    #expect(throttle.allows(.timer, at: start.addingTimeInterval(6 * 3600)))
    // That check counts: the next is six hours after it
    #expect(!throttle.allows(.timer, at: start.addingTimeInterval(6 * 3600 + 30)))
  }

  @Test func theMenuOpeningChecksAtMostOnceAMinute() throws {
    let throttle = Throttle()
    #expect(throttle.allows(.menuOpened, at: start))
    #expect(!throttle.allows(.menuOpened, at: start.addingTimeInterval(59)))
    #expect(throttle.allows(.menuOpened, at: start.addingTimeInterval(60)))
    #expect(!throttle.allows(.menuOpened, at: start.addingTimeInterval(61)))
  }

  @Test func aWakeCountsAsATimerTick() throws {
    let throttle = Throttle()
    #expect(throttle.allows(.launch, at: start))
    #expect(!throttle.allows(.wake, at: start.addingTimeInterval(3600)))
    #expect(throttle.allows(.wake, at: start.addingTimeInterval(7 * 3600)))
  }

  @Test func resumingAfterAQuitNeverChecks() throws {
    let throttle = Throttle()
    #expect(!throttle.allows(.resumed, at: start))
    #expect(!throttle.allows(.afterAction, at: start))
  }

  @Test func aCheckThatWasRefusedIsNotRecorded() throws {
    let throttle = Throttle()
    #expect(!throttle.allows(.resumed, at: start))
    #expect(throttle.allows(.menuOpened, at: start.addingTimeInterval(1)))
  }

  @Test func aClockThatWentBackwardsChecksAgain() throws {
    let throttle = Throttle()
    #expect(throttle.allows(.launch, at: start))
    #expect(throttle.allows(.menuOpened, at: start.addingTimeInterval(-3600)))
  }
}
