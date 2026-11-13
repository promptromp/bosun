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
  private let me: Int32 = 4200
  private let id = "ai.example.bosun"

  @Test func secondCopyExits() {
    let running = [RunningApp(pid: 4100, bundleID: id), RunningApp(pid: me, bundleID: id)]
    #expect(shouldExitAsSecondCopy(running: running, me: me, bundleID: id))
  }

  @Test func theOnlyCopyStays() {
    #expect(!shouldExitAsSecondCopy(running: [RunningApp(pid: me, bundleID: id)], me: me, bundleID: id))
    #expect(!shouldExitAsSecondCopy(running: [], me: me, bundleID: id))
  }

  @Test func otherApplicationsDoNotCount() {
    let running = [
      RunningApp(pid: 100, bundleID: "com.apple.finder"), RunningApp(pid: 101, bundleID: nil),
      RunningApp(pid: me, bundleID: id),
    ]
    #expect(!shouldExitAsSecondCopy(running: running, me: me, bundleID: id))
  }

  /// Two copies launched together each see the other: one must stay, so the older (the lower pid) does.
  @Test func ofTwoLaunchedTogetherOnlyTheNewerExits() {
    let running = [RunningApp(pid: 4100, bundleID: id), RunningApp(pid: me, bundleID: id)]
    #expect(shouldExitAsSecondCopy(running: running, me: me, bundleID: id))
    #expect(!shouldExitAsSecondCopy(running: running, me: 4100, bundleID: id))
  }
}
