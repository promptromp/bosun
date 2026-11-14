import Foundation
import Testing

@testable import BosunMenuCore

@Suite struct StopAndQuitAppDialogTests {
  @Test func theFirstDialogIsStopBosun() throws {
    let spec = quitFirstSpec(quitPlan())
    #expect(spec.title == "Stop bosun?")
    #expect(spec.buttons == ["Stop bosun", "Cancel"])
    #expect(spec.cancel == 1)
    #expect(!spec.boxes.contains { $0.title == "Keep bosun in the menu bar" })
  }

  @Test func quitBosunAppIsOfferedWhenThePlanCouldNotLookOnlyWhenTheAppQuits() throws {
    let stop = quitUnavailableSpec("the windows cannot be read", quitsApp: false)
    #expect(stop.buttons == ["Cancel", "Stop Now"])
    #expect(!stop.message.contains("Quit bosun.app"))
    let quit = quitUnavailableSpec("the windows cannot be read", quitsApp: true)
    #expect(quit.buttons == ["Cancel", "Stop Now", "Quit bosun.app"])
    #expect(quit.message.contains("Quit bosun.app leaves bosun as it is"))
    #expect(quit.boxes.map(\.id) == ["pauseMorning", "sleep"])
  }

  @Test func quitAppAsksWhetherToLeaveBosunRunning() throws {
    let spec = quitAppSpec()
    #expect(spec.title == "Quit bosun.app?")
    #expect(
      spec.message == "bosun keeps running without its menu bar icon. Open bosun.app to see it again.")
    #expect(spec.buttons == ["Leave bosun Running", "Stop bosun and Quit…", "Cancel"])
    #expect(spec.defaultButton == 0)
    #expect(spec.cancel == 2)
    #expect(spec.boxes.isEmpty)
  }

  @Test func eachButtonOfQuitAppIsAChoice() throws {
    func choice(_ button: Int) -> QuitAppChoice { quitAppChoice(DialogAnswer(button: button, boxes: [:])) }
    #expect(choice(0) == .leaveRunning)
    #expect(choice(1) == .stopAndQuit)
    #expect(choice(2) == .cancel)
  }

  @Test func theEntryPointNotABoxSaysWhetherTheAppQuits() throws {
    let answer = DialogAnswer(button: 0, boxes: ["pauseMorning": false])
    #expect(
      quitEvent(for: .first(quitPlan()), answer: answer, quitsApp: true)
        == .confirmed(QuitChoices(pauseMorning: false, quitsApp: true)))
    #expect(
      quitEvent(for: .first(quitPlan()), answer: answer)
        == .confirmed(QuitChoices(pauseMorning: false, quitsApp: false)))
    let unavailable = QuitStep.unavailable("x", nil)
    #expect(
      quitEvent(for: unavailable, answer: DialogAnswer(button: 1, boxes: [:]), quitsApp: true)
        == .confirmed(QuitChoices(quitsApp: true)))
  }
}
