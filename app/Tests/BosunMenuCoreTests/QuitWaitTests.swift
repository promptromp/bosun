import Foundation
import Testing

@testable import BosunMenuCore

// The wait a `--let-finish` quit is given comes from the plan (`wait_minutes`, `lock_wait_seconds`): the
// argv names the minutes, the CLI's timeout is the wait plus the lock wait plus a margin, and "Keep
// Waiting" runs the same quit again with the same number.

@Suite struct QuitWaitTests {
  private let wait = QuitWait(minutes: 10, lockSeconds: 180)

  @Test func thePlanDecodesTheWaitBosunSaid() {
    let plan = Fixtures.decode(QuitPlan.self, "quit-plan")
    #expect(plan.waitMinutes == 10)
    #expect(plan.lockWaitSeconds == 180)
    #expect(plan.wait == wait)
  }

  @Test func theFailureObjectCarriesTheWaitToo() {
    let failed = Fixtures.quitResult("quit-plan-failed")
    #expect(failed.plan.wait == wait)
  }

  @Test func aQuitResultKeepsTheWaitOfItsPlan() {
    #expect(Fixtures.quitResult("quit-result-stopped").plan.wait == wait)
    // The busy sample is a run that was given no minutes: its plan says so
    #expect(Fixtures.quitResult("quit-result-busy").plan.wait == QuitWait(minutes: 0, lockSeconds: 180))
  }

  @Test func aPlanObjectWithoutTheFieldsHasNoWait() throws {
    let text = Fixtures.text("quit-plan")
      .replacingOccurrences(of: "\"wait_minutes\": 10", with: "\"unrelated\": 10")
    let plan = try decodeContract(QuitPlan.self, from: text)
    #expect(plan.waitMinutes == nil)
    #expect(plan.lockWaitSeconds == 180)
    #expect(plan.wait == nil)
  }

  @Test func theLetFinishArgvNamesTheMinutes() {
    #expect(
      quitCommand(QuitChoices(wait: wait))
        == ["quit", "--let-finish=10", "--pause-morning", "--sleep", "--json"])
    #expect(
      quitCommand(QuitChoices(pauseMorning: false, sleep: false, wait: QuitWait(minutes: 0, lockSeconds: 5)))
        == ["quit", "--let-finish=0", "--json"])
  }

  @Test func stopNowNamesNoMinutesWhateverTheWait() {
    let args = quitCommand(QuitChoices(busy: .stopNow, wait: wait))
    #expect(args == ["quit", "--stop-now", "--pause-morning", "--sleep", "--json"])
  }

  @Test func theTimeoutIsTheWaitTheLockWaitAndAMargin() {
    let finish = Bosun.quit(QuitChoices(wait: wait))
    #expect(finish.timeout == .seconds(10 * 60 + 180 + 120))
    #expect(Bosun.quit(QuitChoices(wait: QuitWait(minutes: 25, lockSeconds: 30))).timeout == .seconds(1650))
    #expect(Bosun.quit(QuitChoices(wait: QuitWait(minutes: 0, lockSeconds: 180))).timeout == .seconds(300))
  }

  @Test func stopNowKeepsItsShortTimeout() {
    #expect(Bosun.quit(QuitChoices(busy: .stopNow, wait: wait)).timeout == .seconds(120))
  }

  /// Only a plan without the fields leaves this: bosun's own default minutes and the old hour.
  @Test func withoutAWaitItIsBosunsDefaultMinutesAndAnHour() {
    let finish = Bosun.quit(QuitChoices(wait: nil))
    #expect(finish.args == ["quit", "--let-finish", "--pause-morning", "--sleep", "--json"])
    #expect(finish.timeout == .seconds(60 * 60))
  }

  @Test func theFirstDialogsAnswerTakesTheWaitFromThePlanWithBusyAgents() {
    let plan = quitPlan(busy: ["acme-exporter"], waitMinutes: 15, lockWaitSeconds: 60)
    let (busy, none) = next(.first(plan), on: .confirmed(QuitChoices()))
    let expected = QuitWait(minutes: 15, lockSeconds: 60)
    #expect(busy == .busy(["acme-exporter"], QuitChoices(wait: expected)))
    #expect(none.isEmpty)
    let (running, commands) = next(busy, on: .busyChosen(.letFinish))
    #expect(running == .running(QuitChoices(wait: expected)))
    #expect(commands.map(\.args) == [["quit", "--let-finish=15", "--pause-morning", "--sleep", "--json"]])
    #expect(commands.map(\.timeout) == [.seconds(15 * 60 + 60 + 120)])
  }

  @Test func theFirstDialogsAnswerWithNoBusyAgentsRunsTheWaitingQuitAtOnce() {
    let (running, commands) = next(.first(quitPlan()), on: .confirmed(QuitChoices(sleep: false)))
    #expect(running == .running(QuitChoices(sleep: false, wait: wait)))
    #expect(commands.map(\.args) == [["quit", "--let-finish=10", "--pause-morning", "--json"]])
  }

  @Test func stopNowAfterBusyAgentsAreNamedKeepsTheMinutesOutOfTheCommand() {
    let (busy, _) = next(.first(quitPlan(busy: ["a"])), on: .confirmed(QuitChoices()))
    let (_, commands) = next(busy, on: .busyChosen(.stopNow))
    #expect(commands.map(\.args) == [["quit", "--stop-now", "--pause-morning", "--sleep", "--json"]])
  }

  @Test func keepWaitingRunsTheSameMinutesAgain() {
    let plan = quitPlan(busy: ["acme-exporter"], waitMinutes: 15, lockWaitSeconds: 60)
    let (busy, _) = next(.first(plan), on: .confirmed(QuitChoices()))
    let (first, firstCommands) = next(busy, on: .busyChosen(.letFinish))
    let (still, _) = next(first, on: .ended(.busy(["acme-exporter"])))
    let (again, againCommands) = next(still, on: .busyChosen(.letFinish))
    #expect(again == first)
    #expect(againCommands.map(\.args) == firstCommands.map(\.args))
    #expect(againCommands.map(\.timeout) == firstCommands.map(\.timeout))
  }

  @Test func aPlanWithoutTheFieldsRunsTheOldQuit() {
    let plan = quitPlan(busy: ["a"], waitMinutes: nil, lockWaitSeconds: nil)
    let (busy, _) = next(.first(plan), on: .confirmed(QuitChoices()))
    let (_, commands) = next(busy, on: .busyChosen(.letFinish))
    #expect(commands.map(\.args) == [["quit", "--let-finish", "--pause-morning", "--sleep", "--json"]])
    #expect(commands.map(\.timeout) == [.seconds(60 * 60)])
  }
}
