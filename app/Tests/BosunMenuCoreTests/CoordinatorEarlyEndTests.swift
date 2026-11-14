import BosunMenuTestSupport
import Foundation
import Testing

@testable import BosunMenuCore

// An update that ends before any look sees it running: the status's `update.last` (the record the update
// left) ends "Updating…" at once, instead of the 90 s grace. The fixtures' record began at 09:59:30 UTC.

private var available: UpdateCheck {
  get throws { try Fixtures.decode(UpdateCheck.self, "update-check-available") }
}

private func at(_ text: String) -> Date {
  ISO8601DateFormatter().date(from: text)!  // swiftlint:disable:this force_unwrapping
}

/// When the fixtures' record began.
private let recorded = "2026-11-11T09:59:30+00:00"

private func progress(_ state: UpdateRun = .none, step: String, began: String = recorded) -> UpdateProgress {
  let last = LastUpdate(id: "u1", startedAt: began, step: step)
  return UpdateProgress(state: state, waitingFor: [], last: last)
}

@Suite struct EndOfStartedUpdateTests {
  private let appStart = at("2026-11-11T09:59:25Z")

  @Test func aRecordThatBeganAfterTheStartAndEndedEndsIt() {
    #expect(endOfStartedUpdate(progress(step: "done"), startedAt: appStart) == .startEnded(failed: false))
    // past its release, its windows left to the check-in loop
    #expect(endOfStartedUpdate(progress(step: "agents"), startedAt: appStart) == .startEnded(failed: false))
    #expect(endOfStartedUpdate(progress(step: "failed"), startedAt: appStart) == .startEnded(failed: true))
    #expect(endOfStartedUpdate(progress(step: "restored"), startedAt: appStart) == .startEnded(failed: true))
  }

  @Test func aRecordToTheSecondMayReadUpToTwoSecondsEarly() {
    #expect(endOfStartedUpdate(progress(step: "done"), startedAt: at("2026-11-11T09:59:32Z")) != nil)
    #expect(endOfStartedUpdate(progress(step: "done"), startedAt: at("2026-11-11T09:59:33Z")) == nil)
  }

  @Test func anEarlierUpdatesRecordEndsNothing() {
    #expect(endOfStartedUpdate(progress(step: "done"), startedAt: at("2026-11-11T10:05:00Z")) == nil)
  }

  @Test func anUpdateStillRunningOrAtAStepThatDoesNotEndItEndsNothing() {
    #expect(endOfStartedUpdate(progress(.running, step: "agents"), startedAt: appStart) == nil)
    for step in ["planned", "migrated", "switched", "a-newer-step"] {
      #expect(endOfStartedUpdate(progress(step: step), startedAt: appStart) == nil, "\(step)")
    }
  }

  @Test func noRecordOrATimeThatCannotBeReadEndsNothing() {
    #expect(
      endOfStartedUpdate(UpdateProgress(state: .none, waitingFor: [], last: nil), startedAt: appStart) == nil)
    #expect(endOfStartedUpdate(progress(step: "done", began: "yesterday"), startedAt: appStart) == nil)
  }

  @Test func onlyAStartingUpdateIsEndedSo() throws {
    #expect(
      next(.starting(try available), on: .startEnded(failed: false)) == (.checking, [Bosun.checkUpdates]))
    #expect(
      next(.starting(try available), on: .startEnded(failed: true)) == (.failed(updateDidNotFinishText), []))
    for state: UpdateState in [.idle, .running(waitingFor: []), .checkingStart(try available), .checking] {
      let (after, commands) = next(state, on: .startEnded(failed: true))
      #expect(after == state)
      #expect(commands.isEmpty)
    }
  }
}

@MainActor @Suite struct CoordinatorEarlyEndTests {
  /// Started at `startedAt` (the world's clock), with the offer and the plan answered.
  private func started(at startedAt: String = "2026-11-11T09:59:25Z") async throws -> World {
    let date = at(startedAt)
    let world = try await World(status: "status-ok", now: { date })
    await world.refresh(.launch)
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-available")))
    await world.reply(Bosun.planUpdate, exited(0, try Fixtures.text("update-plan")))
    world.ui.answer("Update Now")
    world.ui.answer("Update")
    await world.coordinator.perform(.checkForUpdates)
    #expect(try world.coordinator.update == .starting(available))
    return world
  }

  @Test func anUpdateThatEndedBeforeAnyLookSawItIsDrawnOverAtTheNextLook() async throws {
    let world = try await started()
    await world.replace(Bosun.checkUpdates, exited(0, try Fixtures.text("update-check-current")))
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-done")))
    await world.refresh(.timer)
    // Over at once (no grace sat out): a fresh check, which finds bosun current
    #expect(world.coordinator.update == .idle)
    #expect(world.item(.checkForUpdates)?.title == "Check for Updates…")
    #expect(!world.coordinator.looksPromptly)
    #expect(world.ui.presented.count == 2)  // the offer and the plan, nothing more
  }

  @Test func anUpdateWhoseRecordSaysItDidNotFinishIsToldAtOnce() async throws {
    let world = try await started()
    world.ui.answer("OK")
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-failed")))
    await world.refresh(.timer)
    #expect(world.ui.lastSpec?.title == "Cannot update bosun")
    #expect(world.ui.lastSpec?.message == updateDidNotFinishText)
    #expect(world.coordinator.update == .idle)
  }

  @Test func theRecordOfAnEarlierUpdateLeavesItToTheGrace() async throws {
    let world = try await started(at: "2026-11-11T10:05:00Z")
    await world.replace(Bosun.status, exited(0, try Fixtures.text("status-update-done")))
    await world.refresh(.timer)
    #expect(try world.coordinator.update == .starting(available))
    #expect(world.coordinator.looksPromptly)
  }
}
