import Foundation
import Testing

@testable import BosunMenuCore

// What the quit flow's coordinator tests share: the `bosun quit` commands of the fixture's plan, its replies, and a
// world ready to quit.

let letFinishCommand = ["quit", "--let-finish=10", "--pause-morning", "--sleep", "--json"]
let stopNowCommand = ["quit", "--stop-now", "--pause-morning", "--sleep", "--json"]

/// The fixture's plan, with or without its busy agent.
func planText(busy: Bool) throws -> String {
  let text = try Fixtures.text("quit-plan")
  return busy
    ? text : text.replacingOccurrences(of: "\"busy\": [\n    \"acme-exporter\"\n  ]", with: "\"busy\": []")
}

/// A quit sample as bosun gave it, with the exit status its run returned.
func quitReply(_ fixture: String) throws -> CommandResult {
  try Fixtures.reply(fixture)
}

@MainActor
func readyToQuit(busy: Bool = false, status: String = "status-ok") async throws -> World {
  let world = try await World(status: status, atLogin: true)
  await world.reply(Bosun.quitPlan, exited(0, try planText(busy: busy)))
  await world.refresh(.launch)
  return world
}

/// The timeout each run of `command` was given.
func timeouts(of command: [String], in world: World) async -> [Duration] {
  let (calls, timeouts) = (await world.cli.calls, await world.cli.timeouts)
  return zip(calls, timeouts).filter { $0.0 == command }.map(\.1)
}

func statusLooks(_ world: World) async -> Int {
  await world.cli.calls.filter { $0 == Bosun.status.args }.count
}
