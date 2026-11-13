import Foundation
import Testing

@testable import BosunMenuCore

@Suite struct LaunchOptionsTests {
  @Test func noArgumentsIsAPlainOpen() throws {
    let options = LaunchOptions.parse(["/Applications/bosun.app/Contents/MacOS/bosun"])
    #expect(!options.atLogin)
    #expect(options.testHooks == nil)
  }

  @Test func atLoginIsRead() throws {
    #expect(LaunchOptions.parse(["bosun", "--at-login"]).atLogin)
  }

  @Test func testHooksTakeTheFolderThatFollows() throws {
    let options = LaunchOptions.parse(["bosun", "--test-hooks", "/tmp/hooks", "--at-login"])
    #expect(options.testHooks == URL(fileURLWithPath: "/tmp/hooks"))
    #expect(options.atLogin)
  }

  /// The hooks exist at run time only with their argument, and only with a folder to use.
  @Test func testHooksWithNoFolderAreOff() throws {
    #expect(LaunchOptions.parse(["bosun", "--test-hooks"]).testHooks == nil)
    #expect(LaunchOptions.parse(["bosun", "--test-hooks", "--at-login"]).testHooks == nil)
  }

  @Test func whatTheSystemAddsIsIgnored() throws {
    let options = LaunchOptions.parse(["bosun", "-psn_0_12345", "-NSDocumentRevisionsDebugMode", "YES"])
    #expect(options == LaunchOptions(atLogin: false, testHooks: nil))
  }
}

@Suite struct LaunchStartTests {
  private func state(_ name: String) throws -> StatusState {
    .loaded(try Fixtures.status(name), current: true)
  }
  private let opened = LaunchOptions(atLogin: false, testHooks: nil)
  private let atLogin = LaunchOptions(atLogin: true, testHooks: nil)

  @Test func openedByYouWhileStoppedItStartsBosun() throws {
    #expect(try shouldStartAtLaunch(opened, status: state("status-stopped")))
  }

  @Test func openedByYouInTheRedStateItStartsBosun() throws {
    #expect(try shouldStartAtLaunch(opened, status: state("status-error")))
  }

  /// A-I2: a bosun that runs with an error is not started again (the morning would run its setup and may
  /// restart the session), nor one whose windows could not be read.
  @Test func openedByYouWhileRunningWithAnErrorItStartsNothing() throws {
    #expect(try !shouldStartAtLaunch(opened, status: state("status-running-error")))
    #expect(try !shouldStartAtLaunch(opened, status: state("status-windows-unreadable")))
  }

  /// A quit that did not finish leaves windows open: opening the app does not run the morning on them (the
  /// menu's Start is there, for the person to choose).
  @Test func openedByYouAfterAQuitThatDidNotFinishItStartsNothing() throws {
    #expect(try !shouldStartAtLaunch(opened, status: state("status-quit-unfinished")))
  }

  @Test func aStatusThatIsNotCurrentStartsNothing() throws {
    let stale = StatusState.loaded(try Fixtures.status("status-stopped"), current: false)
    #expect(!shouldStartAtLaunch(opened, status: stale))
  }

  @Test func openedByYouWhileRunningItStartsNothing() throws {
    #expect(try !shouldStartAtLaunch(opened, status: state("status-ok")))
    #expect(try !shouldStartAtLaunch(opened, status: state("status-warning")))
  }

  @Test func atLoginItOnlyShowsTheIcon() throws {
    #expect(try !shouldStartAtLaunch(atLogin, status: state("status-stopped")))
    #expect(try !shouldStartAtLaunch(atLogin, status: state("status-error")))
  }

  @Test func whereBosunCannotBeRunNothingIsStarted() throws {
    #expect(!shouldStartAtLaunch(opened, status: .notInstalled))
    #expect(!shouldStartAtLaunch(opened, status: .unreachable("no")))
    #expect(!shouldStartAtLaunch(opened, status: .cannotRun("no")))
    #expect(!shouldStartAtLaunch(opened, status: .contractTooNew(2)))
  }
}
