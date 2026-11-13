import Foundation
import Testing

@testable import BosunMenuCore

// How long a quit is given comes from the plan (`wait_minutes`, `lock_wait_seconds`, `steps_seconds`): the
// argv names the minutes, the CLI's timeout outlasts what bosun quit itself may wait for (the minutes with
// --let-finish, the lock wait, the steps after it) plus a margin, and "Keep Waiting" runs the same quit again
// with the same number.

@Suite struct QuitWaitTests {
  private let wait = QuitWait(minutes: 10, lockSeconds: 180, stepsSeconds: 225)

  @Test func thePlanDecodesTheWaitBosunSaid() throws {
    let plan = try Fixtures.decode(QuitPlan.self, "quit-plan")
    #expect(plan.waitMinutes == 10)
    #expect(plan.lockWaitSeconds == 180)
    #expect(plan.wait == wait)
  }

  @Test func theFailureObjectCarriesTheWaitToo() throws {
    let failed = try Fixtures.quitResult("quit-plan-failed")
    #expect(failed.plan.wait == wait)
  }

  @Test func aQuitResultKeepsTheWaitOfItsPlan() throws {
    #expect(try Fixtures.quitResult("quit-result-stopped").plan.wait == wait)
    // The busy sample is a run that was given no minutes: its plan says so
    #expect(
      try Fixtures.quitResult("quit-result-busy").plan.wait
        == QuitWait(minutes: 0, lockSeconds: 180, stepsSeconds: 225))
  }

  @Test func aPlanObjectWithoutTheFieldsHasNoWait() throws {
    let text = try Fixtures.text("quit-plan")
      .replacingOccurrences(of: "\"wait_minutes\": 10", with: "\"unrelated\": 10")
    let plan = try decodeContract(QuitPlan.self, from: text)
    #expect(plan.waitMinutes == nil)
    #expect(plan.lockWaitSeconds == 180)
    #expect(plan.wait == nil)
  }

  @Test func theLetFinishArgvNamesTheMinutes() throws {
    #expect(
      quitCommand(QuitChoices(wait: wait))
        == ["quit", "--let-finish=10", "--pause-morning", "--sleep", "--json"])
    #expect(
      quitCommand(QuitChoices(pauseMorning: false, sleep: false, wait: QuitWait(minutes: 0, lockSeconds: 5)))
        == ["quit", "--let-finish=0", "--json"])
  }

  @Test func stopNowNamesNoMinutesWhateverTheWait() throws {
    let args = quitCommand(QuitChoices(busy: .stopNow, wait: wait))
    #expect(args == ["quit", "--stop-now", "--pause-morning", "--sleep", "--json"])
  }

  @Test func theTimeoutIsTheWaitTheLockWaitTheStepsAndAMargin() throws {
    let finish = Bosun.quit(QuitChoices(wait: wait))
    #expect(finish.timeout == .seconds(1065))  // 10 * 60 + 180 + 225 + 60
    let short = QuitWait(minutes: 25, lockSeconds: 30, stepsSeconds: 100)
    #expect(Bosun.quit(QuitChoices(wait: short)).timeout == .seconds(1690))  // 25 * 60 + 30 + 100 + 60
    let none = QuitWait(minutes: 0, lockSeconds: 180, stepsSeconds: 225)
    #expect(Bosun.quit(QuitChoices(wait: none)).timeout == .seconds(465))  // 180 + 225 + 60
  }

  /// A-I1: a stop-now quit can wait the whole lock wait for a start that holds it, and then stop the console
  /// and run `bosun stop`: its timeout outlasts both, never the old two minutes.
  @Test func stopNowOutlastsTheLockWaitAndTheStop() throws {
    let now = Bosun.quit(QuitChoices(busy: .stopNow, wait: wait))
    #expect(now.timeout == .seconds(465))  // 180 + 225 + 60
    #expect(now.timeout > .seconds(300))  // 180 + 120: the lock wait and bosun stop's own two minutes
    let longLock = QuitWait(minutes: 10, lockSeconds: 600, stepsSeconds: 225)
    // 600 + 225 + 60
    #expect(Bosun.quit(QuitChoices(busy: .stopNow, wait: longLock)).timeout == .seconds(885))
  }

  @Test func aPlanFromBeforeTheStepsWereSaidGetsWhatTheyWereThen() throws {
    let text = try Fixtures.text("quit-plan").replacingOccurrences(
      of: "\"steps_seconds\": 225", with: "\"x\": 1")
    let plan = try decodeContract(QuitPlan.self, from: text)
    #expect(plan.stepsSeconds == nil)
    #expect(plan.wait == QuitWait(minutes: 10, lockSeconds: 180, stepsSeconds: QuitWait.stepsBefore))
  }

  @Test func theStepsThePlanSaysAreRead() throws {
    let text = try Fixtures.text("quit-plan").replacingOccurrences(
      of: "\"steps_seconds\": 225", with: "\"steps_seconds\": 400")
    #expect(try decodeContract(QuitPlan.self, from: text).wait?.stepsSeconds == 400)
  }

  /// Only a plan without the fields leaves this: bosun's own default minutes and the old hour.
  @Test func withoutAWaitItIsBosunsDefaultMinutesAndAnHour() throws {
    let finish = Bosun.quit(QuitChoices(wait: nil))
    #expect(finish.args == ["quit", "--let-finish", "--pause-morning", "--sleep", "--json"])
    #expect(finish.timeout == .seconds(60 * 60))
  }

  @Test func theFirstDialogsAnswerTakesTheWaitFromThePlanWithBusyAgents() throws {
    let plan = quitPlan(busy: ["acme-exporter"], waitMinutes: 15, lockWaitSeconds: 60)
    let (busy, none) = next(.first(plan), on: .confirmed(QuitChoices()))
    let expected = QuitWait(minutes: 15, lockSeconds: 60, stepsSeconds: 225)
    #expect(busy == .busy(["acme-exporter"], QuitChoices(wait: expected)))
    #expect(none.isEmpty)
    let (running, commands) = next(busy, on: .busyChosen(.letFinish))
    #expect(running == .running(QuitChoices(wait: expected)))
    #expect(commands.map(\.args) == [["quit", "--let-finish=15", "--pause-morning", "--sleep", "--json"]])
    #expect(commands.map(\.timeout) == [.seconds(1245)])  // 15 * 60 + 60 + 225 + 60
  }

  @Test func theFirstDialogsAnswerWithNoBusyAgentsRunsTheWaitingQuitAtOnce() throws {
    let (running, commands) = next(.first(quitPlan()), on: .confirmed(QuitChoices(sleep: false)))
    #expect(running == .running(QuitChoices(sleep: false, wait: wait)))
    #expect(commands.map(\.args) == [["quit", "--let-finish=10", "--pause-morning", "--json"]])
  }

  @Test func stopNowAfterBusyAgentsAreNamedKeepsTheMinutesOutOfTheCommand() throws {
    let (busy, _) = next(.first(quitPlan(busy: ["a"])), on: .confirmed(QuitChoices()))
    let (_, commands) = next(busy, on: .busyChosen(.stopNow))
    #expect(commands.map(\.args) == [["quit", "--stop-now", "--pause-morning", "--sleep", "--json"]])
  }

  @Test func keepWaitingRunsTheSameMinutesAgain() throws {
    let plan = quitPlan(busy: ["acme-exporter"], waitMinutes: 15, lockWaitSeconds: 60)
    let (busy, _) = next(.first(plan), on: .confirmed(QuitChoices()))
    let (first, firstCommands) = next(busy, on: .busyChosen(.letFinish))
    let (still, _) = next(first, on: .ended(.busy(["acme-exporter"])))
    let (again, againCommands) = next(still, on: .busyChosen(.letFinish))
    #expect(again == first)
    #expect(againCommands.map(\.args) == firstCommands.map(\.args))
    #expect(againCommands.map(\.timeout) == firstCommands.map(\.timeout))
  }

  @Test func aPlanWithoutTheFieldsRunsTheOldQuit() throws {
    let plan = quitPlan(busy: ["a"], waitMinutes: nil, lockWaitSeconds: nil)
    let (busy, _) = next(.first(plan), on: .confirmed(QuitChoices()))
    let (_, commands) = next(busy, on: .busyChosen(.letFinish))
    #expect(commands.map(\.args) == [["quit", "--let-finish", "--pause-morning", "--sleep", "--json"]])
    #expect(commands.map(\.timeout) == [.seconds(60 * 60)])
  }
}
