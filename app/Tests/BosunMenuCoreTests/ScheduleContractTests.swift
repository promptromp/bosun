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
