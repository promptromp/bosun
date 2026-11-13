import Foundation
import Testing

@testable import BosunMenuSystem

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
