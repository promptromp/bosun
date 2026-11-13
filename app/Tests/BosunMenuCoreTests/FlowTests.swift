import Testing

@testable import BosunMenuCore

extension FlowState {
  /// `begin`, with its answer where `#expect` can read it (a macro cannot call a mutating member).
  mutating func starts(_ flow: Flow) -> Bool { begin(flow) }
}

func flowRunning(_ flow: Flow) -> FlowState {
  var state = FlowState()
  _ = state.begin(flow)
  return state
}

/// Review focus 4: two Quits at once (⌘Q twice, or Quit while an update dialog is open).
@Suite struct OneFlowAtATimeTests {
  @Test func oneFlowAtATime() {
    var flow = FlowState()
    #expect(flow.isIdle)
    let first = flow.starts(.quit)
    #expect(first)
    #expect(flow.active == .quit)
    // The second Quit, an update, a Start: all ignored while one runs
    let secondQuit = flow.starts(.quit)
    let anUpdate = flow.starts(.update)
    let aStart = flow.starts(.start)
    #expect(!secondQuit)
    #expect(!anUpdate)
    #expect(!aStart)
    #expect(flow.active == .quit)
  }

  @Test func aQuitWhileTheUpdateDialogIsOpenIsIgnored() {
    var flow = flowRunning(.update)
    let quit = flow.starts(.quit)
    #expect(!quit)
    #expect(flow.active == .update)
  }

  @Test func aFlowEndedMakesRoomForTheNext() {
    var flow = flowRunning(.update)
    flow.end(.update)
    #expect(flow.isIdle)
    let quit = flow.starts(.quit)
    #expect(quit)
  }

  @Test func onlyTheFlowThatIsRunningCanEndIt() {
    var flow = flowRunning(.quit)
    flow.end(.update)
    #expect(flow.active == .quit)
    flow.end(.quit)
    #expect(flow.isIdle)
    flow.end(.quit)  // ending twice is harmless
    #expect(flow.isIdle)
  }

  /// The seconds between `quit.json` and the stop read as "bosun quit did not finish".
  @Test func theStatusIsNotPolledWhileTheAppsOwnQuitRuns() {
    #expect(FlowState().pollsStatus)
    #expect(flowRunning(.update).pollsStatus)
    #expect(flowRunning(.start).pollsStatus)
    #expect(!flowRunning(.quit).pollsStatus)
    var flow = flowRunning(.quit)
    flow.end(.quit)
    #expect(flow.pollsStatus)
  }
}

@Suite struct SingleCopyTests {
  private let id = "ai.example.bosun"

  private func app(_ pid: Int32, started: Double, bundle: String? = "ai.example.bosun") -> RunningApp {
    RunningApp(pid: pid, bundleID: bundle, startedAt: started)
  }

  @Test func secondCopyExits() {
    let me = app(4200, started: 2000)
    let running = [app(4100, started: 1000), me]
    #expect(shouldExitAsSecondCopy(running: running, me: me, bundleID: id))
  }

  @Test func theOnlyCopyStays() {
    let me = app(4200, started: 2000)
    #expect(!shouldExitAsSecondCopy(running: [me], me: me, bundleID: id))
    #expect(!shouldExitAsSecondCopy(running: [], me: me, bundleID: id))
  }

  @Test func otherApplicationsDoNotCount() {
    let me = app(4200, started: 2000)
    let running = [
      app(100, started: 1, bundle: "com.apple.finder"), app(101, started: 1, bundle: nil), me,
    ]
    #expect(!shouldExitAsSecondCopy(running: running, me: me, bundleID: id))
  }

  /// A copy that started later than another exits, and the one that started earlier stays.
  @Test func theCopyThatStartedEarliestStays() {
    let older = app(4100, started: 1000)
    let newer = app(4200, started: 2000)
    let running = [older, newer]
    #expect(shouldExitAsSecondCopy(running: running, me: newer, bundleID: id))
    #expect(!shouldExitAsSecondCopy(running: running, me: older, bundleID: id))
  }

  /// pids wrap at 99999: the copy launchd started days later can have the lower pid, and is still the
  /// newer. Going by pid alone would keep it and leave two icons.
  @Test func aWrappedPidDoesNotMakeTheNewCopyTheOlder() {
    let old = app(98000, started: 1_000_000)
    let new = app(120, started: 1_300_000)
    let running = [old, new]
    #expect(shouldExitAsSecondCopy(running: running, me: new, bundleID: id))
    #expect(!shouldExitAsSecondCopy(running: running, me: old, bundleID: id))
  }

  /// Started in the same instant, the lower pid stays, so one of two always does.
  @Test func withTheSameStartTheLowerPidStays() {
    let low = app(4100, started: 1000)
    let high = app(4200, started: 1000)
    #expect(!shouldExitAsSecondCopy(running: [low, high], me: low, bundleID: id))
    #expect(shouldExitAsSecondCopy(running: [low, high], me: high, bundleID: id))
  }

  @Test func ofThreeOnlyTheEarliestStays() {
    let copies = [app(300, started: 3000), app(100, started: 1000), app(200, started: 2000)]
    let exits = copies.map { shouldExitAsSecondCopy(running: copies, me: $0, bundleID: id) }
    #expect(exits == [true, false, true])
  }
}
