import Foundation
import Testing

@testable import BosunMenuCore

/// `schedule` in `status --json` (whether now is within your hours, and the next automatic start).
@Suite struct ScheduleContractTests {
  @Test func restingOutsideTheHoursIsReadWithTheNextStart() throws {
    let status = try Fixtures.status("status-resting")
    #expect(status.schedule == Schedule(inHours: false, nextStart: "2026-11-11T08:00+00:00"))
    #expect(status.offersStart)
  }

  @Test func runningOutsideTheHoursOffersNoStart() throws {
    let status = try Fixtures.status("status-running-outside-hours")
    #expect(status.schedule?.inHours == false)
    #expect(!status.offersStart)
  }

  @Test func testOlderBosunWithoutSchedule() throws {
    let older = try decodeContract(
      StatusSnapshot.self, from: Fixtures.without("schedule", in: "status-resting"))
    #expect(older.schedule == nil)
    // the old rule: an ok level never offers Start
    #expect(!older.offersStart)
    let stopped = try decodeContract(
      StatusSnapshot.self, from: Fixtures.without("schedule", in: "status-stopped"))
    #expect(stopped.offersStart)
    let drawn = menu(for: .loaded(stopped, current: true), update: .idle, flow: FlowState())
    #expect(drawn.item(.startBosun).title == "Start bosun")
  }

  @Test func eitherFieldMayBeNull() throws {
    let text = try Fixtures.text("status-resting").replacingOccurrences(
      of: "\"in_hours\": false,\n    \"next_start\": \"2026-11-11T08:00+00:00\"",
      with: "\"in_hours\": null,\n    \"next_start\": null")
    let status = try decodeContract(StatusSnapshot.self, from: text)
    #expect(status.schedule == Schedule(inHours: nil, nextStart: nil))
    #expect(!status.offersStart)
  }
}

/// `starting` in `status --json`: a morning run setting bosun up, which outside your hours (Start bosun now) is
/// shown as inside them: green, no Start, and opening the app starts no second run.
@Suite struct StartingContractTests {
  private let fixture = "status-starting-outside-hours"

  @Test func aRunSettingUpOutsideTheHoursIsStartingNotResting() throws {
    let status = try Fixtures.status(fixture)
    #expect(status.starting == true)
    #expect(status.isStarting)
    #expect(status.outsideHours && status.running == false && status.level == .ok)
    #expect(!status.isResting)
    #expect(!status.offersStart)
  }

  @Test func itsMenuIsGreenWithNoStart() throws {
    let drawn = menu(
      for: .loaded(try Fixtures.status(fixture), current: true), update: .idle, flow: FlowState())
    #expect(drawn.header.dot == .green)
    #expect(drawn.header.sentence == "bosun is starting (the morning run)")
    #expect(!drawn.has(.startBosun))
    #expect(!drawn.has(.stopBosun))  // no window is open yet: nothing to stop
  }

  @Test func withAWarningItIsYellowWithNoStart() throws {
    let text = try Fixtures.text(fixture).replacingOccurrences(
      of: "\"level\": \"ok\"", with: "\"level\": \"warning\"")
    let status = try decodeContract(StatusSnapshot.self, from: text)
    #expect(!status.offersStart)
    let drawn = menu(for: .loaded(status, current: true), update: .idle, flow: FlowState())
    #expect(drawn.header.dot == .yellow)
    #expect(!drawn.has(.startBosun))
  }

  @Test func openingTheAppStartsNoSecondRun() throws {
    let state = StatusState.loaded(try Fixtures.status(fixture), current: true)
    #expect(!shouldStartAtLaunch(LaunchOptions(), status: state))
  }

  @Test func restingIsNotStartingAndGrey() throws {
    let status = try Fixtures.status("status-resting")
    #expect(status.starting == false)
    #expect(status.isResting)
    let drawn = menu(for: .loaded(status, current: true), update: .idle, flow: FlowState())
    #expect(drawn.header.dot == .grey)
  }

  /// An older bosun does not say: no field is no start, so a bosun resting outside the hours is still grey
  /// with "Start bosun now".
  @Test func anOlderBosunWithoutTheFieldIsNotStarting() throws {
    let older = try decodeContract(
      StatusSnapshot.self, from: Fixtures.without("starting", in: "status-resting"))
    #expect(older.starting == nil)
    #expect(!older.isStarting)
    #expect(older.isResting && older.offersStart)
    let drawn = menu(for: .loaded(older, current: true), update: .idle, flow: FlowState())
    #expect(drawn.header.dot == .grey)
    #expect(drawn.item(.startBosun).title == "Start bosun now")
  }
}
