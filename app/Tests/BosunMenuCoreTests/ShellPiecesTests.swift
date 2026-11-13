import Foundation
import Testing

@testable import BosunMenuCore

@Suite struct LoadingMenuTests {
  @Test func beforeTheFirstStatusTheMenuOnlySaysItIsLooking() {
    let model = loadingMenu(flow: FlowState())
    #expect(model.header.dot == .grey)
    #expect(model.header.sentence == "Checking bosun…")
    #expect(model.iconBadge == nil)
    let items = model.sections.flatMap { $0 }
    #expect(items.map(\.id) == [.helpMenu, .quit])
    #expect(items.last?.title == "Quit bosun.app")
    // Nothing that runs bosun is offered before it is known to be there
    let help = items.first?.children ?? []
    #expect(help.first { $0.id == .help(.guide) }?.enabled == true)
    #expect(help.filter { $0.id != .help(.guide) }.allSatisfy { !$0.enabled })
  }
}

@Suite struct StartFailedTests {
  private let check = Fixtures.decode(UpdateCheck.self, "update-check-available")

  @Test func anUpdateThatCouldNotBeStartedFailsWithItsReason() {
    let (state, commands) = next(UpdateState.starting(check), on: UpdateEvent.startFailed("no permission"))
    #expect(state == .failed("no permission"))
    #expect(commands.isEmpty)
  }

  @Test func inAnyOtherStateItMeansNothing() {
    let (state, commands) = next(UpdateState.idle, on: UpdateEvent.startFailed("no permission"))
    #expect(state == .idle)
    #expect(commands.isEmpty)
  }
}

@Suite struct ConsoleURLTests {
  @Test func theAddressIsTheFirstLine() throws {
    let url = try consoleURL(from: exited(0, "http://127.0.0.1:8123/?t=abc\n")).get()
    #expect(url == URL(string: "http://127.0.0.1:8123/?t=abc"))
  }

  @Test func localhostIsAlsoLocal() throws {
    let url = try consoleURL(from: exited(0, "\n  http://localhost:8123/?t=abc  \n")).get()
    #expect(url.host == "localhost")
  }

  /// The answer is opened in the browser: only the console on this Mac may be.
  @Test func anythingButTheLocalConsoleIsRefused() {
    for text in [
      "file:///etc/passwd", "https://example.com/?t=x", "http://192.168.1.4:8123/", "javascript:alert(1)",
      "not a url", "",
    ] {
      let result = consoleURL(from: exited(0, text + "\n"))
      guard case .failure(let error) = result else {
        Issue.record("\(text) should be refused")
        continue
      }
      #expect(!error.message.isEmpty)
    }
  }

  @Test func aFailureSaysWhatBosunSaid() {
    guard
      case .failure(let error) = consoleURL(from: exited(1, "", err: "bosun: the console is not running\n"))
    else {
      Issue.record("expected a failure")
      return
    }
    #expect(error.message == "bosun: the console is not running")
  }

  @Test func aFailureThatSaidNothingSaysTheStatus() {
    guard case .failure(let error) = consoleURL(from: exited(2)) else {
      Issue.record("expected a failure")
      return
    }
    #expect(error.message == "bosun exited with status 2 and said nothing")
  }

  @Test func aHangAndAMissingBosunSayWhat() {
    let hung = CommandResult(outcome: .timedOut, stdout: "", stderr: "")
    let missing = CommandResult(outcome: .notInstalled, stdout: "", stderr: "")
    guard case .failure(let first) = consoleURL(from: hung),
      case .failure(let second) = consoleURL(from: missing)
    else {
      Issue.record("expected failures")
      return
    }
    #expect(first.message == "bosun did not answer in time")
    #expect(second.message == "bosun is not installed")
  }
}

@Suite struct CommandScriptTextTests {
  @Test func theScriptRunsBosunAndWaitsForAKeyBeforeTheWindowCloses() {
    let text = CommandScript.text(executable: "/Users/me/.local/bin/bosun", args: ["doctor"])
    #expect(text.hasPrefix("#!/bin/sh\n"))
    #expect(text.contains("'/Users/me/.local/bin/bosun' 'doctor'\n"))
    #expect(text.contains("read -r"))
    #expect(text.contains("exit $status"))
  }

  @Test func whatIsNotPlainIsQuoted() {
    #expect(CommandScript.quote("plain") == "'plain'")
    #expect(CommandScript.quote("a b") == "'a b'")
    #expect(CommandScript.quote("it's") == "'it'\\''s'")
    #expect(CommandScript.quote("$(rm -rf ~)") == "'$(rm -rf ~)'")
  }
}

@Suite struct LinkTests {
  @Test func theGuideIsTheDocsSite() {
    #expect(Links.guide.scheme == "https")
    #expect(Links.guide.absoluteString == "https://promptromp.github.io/bosun/")
  }
}
