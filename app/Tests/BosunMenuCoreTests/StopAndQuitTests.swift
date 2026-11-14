import Testing

@testable import BosunMenuCore

/// Stop bosun… and Quit bosun.app apart (docs/design/control-panel.md, "Stop and Quit").
@Suite struct StopAndQuitAppTests {
  @Test func testQuitCommandHasNoKeepIcon() throws {
    #expect(QuitChoices().quitsApp == false)
    #expect(quitCommand(QuitChoices()) == ["quit", "--let-finish", "--pause-morning", "--sleep", "--json"])
    #expect(quitCommand(QuitChoices(quitsApp: true)) == quitCommand(QuitChoices(quitsApp: false)))
  }

  @Test func theFlowEndsWithWhetherTheAppQuits() throws {
    for quitsApp in [false, true] {
      let running = QuitStep.running(QuitChoices(quitsApp: quitsApp))
      let done = next(running, on: .ended(.stopped(note: nil))).0
      #expect(done == .done(quitsApp: quitsApp, note: nil))
      #expect(done.quitsTheApp == quitsApp)
    }
  }

  @Test func showsStopWhileTheSessionRunsOrAQuitDidNotFinish() throws {
    #expect(showsStop(try Fixtures.status("status-ok")))
    #expect(showsStop(try Fixtures.status("status-running-outside-hours")))
    #expect(showsStop(try Fixtures.status("status-windows-unreadable")))
    #expect(showsStop(try Fixtures.status("status-quit-unfinished")))
    #expect(!showsStop(try Fixtures.status("status-stopped")))
    #expect(!showsStop(try Fixtures.status("status-resting")))
    #expect(!showsStop(try Fixtures.status("status-error")))
  }
}
