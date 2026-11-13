import Foundation
import Testing

@testable import BosunMenuCore

@Suite struct ProcessStartTimeTests {
  @Test func readsWhenThisProcessStarted() throws {
    let started = try #require(processStartTime(pid: getpid()))
    let now = Date().timeIntervalSince1970
    #expect(started > 1_000_000_000)
    #expect(started <= now)
  }

  @Test func aParentStartedBeforeItsChild() throws {
    let child = Process()
    child.executableURL = URL(fileURLWithPath: "/bin/sleep")
    child.arguments = ["5"]
    try child.run()
    defer { child.terminate() }
    let parentStart = try #require(processStartTime(pid: getpid()))
    let childStart = try #require(processStartTime(pid: child.processIdentifier))
    #expect(parentStart <= childStart)
  }

  /// No such process (or one the system will not say): nil, never 0.
  @Test func aProcessThatIsNotThereHasNoStartTime() {
    #expect(processStartTime(pid: Int32.max) == nil)
    #expect(processStartTime(pid: -5) == nil)
  }
}

@Suite struct SecondCopyExitsTests {
  private let me: Int32 = 500
  private let bundle = "ai.example.bosun"

  private func exits(
    _ listed: [(pid: Int32, bundleID: String?)], starts: [Int32: Double],
    bundleID: String? = "ai.example.bosun"
  ) -> Bool {
    secondCopyExits(running: listed, myPid: me, bundleID: bundleID) { starts[$0] }
  }

  @Test func theOlderCopyStaysAndTheNewerExits() {
    let listed: [(pid: Int32, bundleID: String?)] = [(100, bundle), (500, bundle)]
    #expect(exits(listed, starts: [100: 1000, 500: 2000]))
    #expect(!exits(listed, starts: [100: 3000, 500: 2000]))
  }

  /// A wrapped pid: the old copy has the higher number.
  @Test func theStartTimeDecidesNotThePid() {
    let listed: [(pid: Int32, bundleID: String?)] = [(98000, bundle), (500, bundle)]
    #expect(exits(listed, starts: [98000: 1000, 500: 2000]))
  }

  @Test func aCopyAloneStaysEvenWhenListingItself() {
    let alone: [(pid: Int32, bundleID: String?)] = [(500, bundle)]
    let nobody: [(pid: Int32, bundleID: String?)] = []
    #expect(!exits(alone, starts: [500: 2000]))
    #expect(!exits(nobody, starts: [500: 2000]))
  }

  @Test func otherApplicationsDoNotCount() {
    let listed: [(pid: Int32, bundleID: String?)] = [(100, "com.apple.finder"), (101, nil), (500, bundle)]
    #expect(!exits(listed, starts: [100: 1, 101: 1, 500: 2000]))
  }

  /// A copy whose start cannot be read is left out: nothing is decided on a time that was not read.
  @Test func aCopyWhoseStartIsUnknownIsLeftOut() {
    let listed: [(pid: Int32, bundleID: String?)] = [(100, bundle), (500, bundle)]
    #expect(!exits(listed, starts: [500: 2000]))
  }

  @Test func ifMyOwnStartIsUnknownIStay() {
    let listed: [(pid: Int32, bundleID: String?)] = [(100, bundle), (500, bundle)]
    #expect(!exits(listed, starts: [100: 1000]))
  }

  /// Unbundled (swift run), there is no identifier and no other copy to find.
  @Test func withNoBundleIdentifierThereIsNothingToCompare() {
    let listed: [(pid: Int32, bundleID: String?)] = [(100, bundle), (500, bundle)]
    #expect(!exits(listed, starts: [100: 1000, 500: 2000], bundleID: nil))
  }

  @Test func theListIsOfRunningAppsThatHaveAStartTime() {
    let listed: [(pid: Int32, bundleID: String?)] = [(100, bundle), (101, bundle)]
    let apps = runningApps(listed) { $0 == 100 ? 1234.5 : nil }
    #expect(apps == [RunningApp(pid: 100, bundleID: bundle, startedAt: 1234.5)])
  }

  @Test func twoStartedTogetherLeaveOneBehind() {
    let listed: [(pid: Int32, bundleID: String?)] = [(100, bundle), (500, bundle)]
    let same = [Int32(100): 1000.0, 500: 1000.0]
    let first = secondCopyExits(running: listed, myPid: 100, bundleID: bundle) { same[$0] }
    let second = secondCopyExits(running: listed, myPid: 500, bundleID: bundle) { same[$0] }
    #expect(first != second)
    #expect(second)  // the higher pid goes
  }
}
