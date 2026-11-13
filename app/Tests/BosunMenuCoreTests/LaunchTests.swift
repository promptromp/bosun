import Foundation
import Testing

@testable import BosunMenuCore

@Suite struct LaunchOptionsTests {
  @Test func noArgumentsIsAPlainOpen() {
    let options = LaunchOptions.parse(["/Applications/bosun.app/Contents/MacOS/bosun"])
    #expect(!options.atLogin)
    #expect(options.testHooks == nil)
  }

  @Test func atLoginIsRead() {
    #expect(LaunchOptions.parse(["bosun", "--at-login"]).atLogin)
  }

  @Test func testHooksTakeTheFolderThatFollows() {
    let options = LaunchOptions.parse(["bosun", "--test-hooks", "/tmp/hooks", "--at-login"])
    #expect(options.testHooks == URL(fileURLWithPath: "/tmp/hooks"))
    #expect(options.atLogin)
  }

  /// The hooks exist at run time only with their argument, and only with a folder to use.
  @Test func testHooksWithNoFolderAreOff() {
    #expect(LaunchOptions.parse(["bosun", "--test-hooks"]).testHooks == nil)
    #expect(LaunchOptions.parse(["bosun", "--test-hooks", "--at-login"]).testHooks == nil)
  }

  @Test func whatTheSystemAddsIsIgnored() {
    let options = LaunchOptions.parse(["bosun", "-psn_0_12345", "-NSDocumentRevisionsDebugMode", "YES"])
    #expect(options == LaunchOptions(atLogin: false, testHooks: nil))
  }
}

@Suite struct LaunchStartTests {
  private func state(_ name: String) -> StatusState { .loaded(Fixtures.status(name), current: true) }
  private let opened = LaunchOptions(atLogin: false, testHooks: nil)
  private let atLogin = LaunchOptions(atLogin: true, testHooks: nil)

  @Test func openedByYouWhileStoppedItStartsBosun() {
    #expect(shouldStartAtLaunch(opened, status: state("status-stopped")))
  }

  @Test func openedByYouInTheRedStateItStartsBosun() {
    #expect(shouldStartAtLaunch(opened, status: state("status-error")))
  }

  @Test func openedByYouWhileRunningItStartsNothing() {
    #expect(!shouldStartAtLaunch(opened, status: state("status-ok")))
    #expect(!shouldStartAtLaunch(opened, status: state("status-warning")))
  }

  @Test func atLoginItOnlyShowsTheIcon() {
    #expect(!shouldStartAtLaunch(atLogin, status: state("status-stopped")))
    #expect(!shouldStartAtLaunch(atLogin, status: state("status-error")))
  }

  @Test func whereBosunCannotBeRunNothingIsStarted() {
    #expect(!shouldStartAtLaunch(opened, status: .notInstalled))
    #expect(!shouldStartAtLaunch(opened, status: .unreachable("no")))
    #expect(!shouldStartAtLaunch(opened, status: .contractTooNew(2)))
  }
}
