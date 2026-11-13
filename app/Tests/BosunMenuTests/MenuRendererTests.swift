import AppKit
import Testing

@testable import BosunMenu
@testable import BosunMenuCore

/// A status the way `bosun status --json` prints it, for a level: running while ok or a warning, and not
/// running when red or stopped (so the red one offers Start).
func snapshot(level: String, sentence: String = "bosun is running") -> StatusSnapshot {
  let detail = #"["3 windows open", "Mac kept awake until 20:00"]"#
  let running = level == "ok" || level == "warning"
  let json = """
    {"contract": 1, "level": "\(level)", "sentence": "\(sentence)", "detail": \(detail),
     "windows": \(running ? 3 : 0), "running": \(running), "waiting": [], "keep_awake_until": null, "stopped": null,
     "update": {"state": "none", "waiting_for": []}, "console": {"running": false, "url": null},
     "problems": ["a problem"], "logs": "/logs"}
    """
  return try! decodeContract(StatusSnapshot.self, from: json)
}

func model(
  _ level: String = "ok", sentence: String = "bosun is running", update: UpdateState = .idle,
  flow: FlowState = FlowState(), login: Bool = false
) -> MenuModel {
  menu(
    for: .loaded(snapshot(level: level, sentence: sentence), current: true), update: update, flow: flow,
    login: login)
}

typealias Chosen = @MainActor (ItemID) -> Void

@MainActor
func render(_ model: MenuModel, into menu: NSMenu = NSMenu(), chosen: @escaping Chosen = { _ in }) -> NSMenu {
  MenuRenderer(onChoose: chosen).render(model, into: menu)
  return menu
}

extension NSMenu {
  /// The titles of the items, a separator as "—".
  var titles: [String] {
    items.map { $0.isSeparatorItem ? "—" : ($0.attributedTitle?.string ?? $0.title) }
  }
}

@MainActor @Suite struct MenuRendererTests {
  @Test func rendersEachLevel() {
    let expected: [(String, NSColor)] = [
      ("ok", .systemGreen), ("warning", .systemYellow), ("error", .systemRed), ("stopped", .systemGray),
    ]
    let mark = "●"
    for (level, colour) in expected {
      let menu = render(model(level))
      let header = menu.items[0]
      #expect(header.attributedTitle?.string == "\(mark) bosun is running", "\(level)")
      #expect(!header.isEnabled)
      let dot = header.attributedTitle?.attribute(.foregroundColor, at: 0, effectiveRange: nil) as? NSColor
      #expect(dot == colour, "\(level)")
    }
  }

  @Test func theDetailLinesAreGreyUnderTheStatusLine() {
    let menu = render(model("ok"))
    #expect(
      menu.titles.prefix(4) == ["● bosun is running", "3 windows open", "Mac kept awake until 20:00", "—"])
    for line in menu.items[1...2] {
      #expect(!line.isEnabled)
      let colour = line.attributedTitle?.attribute(.foregroundColor, at: 0, effectiveRange: nil) as? NSColor
      #expect(colour == .secondaryLabelColor)
    }
  }

  @Test func theItemsComeInTheOrderOfTheModelWithASeparatorBetweenSections() {
    let menu = render(model("error"))
    #expect(
      menu.titles == [
        "● bosun is running", "3 windows open", "Mac kept awake until 20:00", "—",
        "Start bosun", "Show What's Wrong…", "—", "Open Console", "—", "Check for Updates…", "Open at Login",
        "Help",
        "—", "Quit bosun…",
      ])
  }

  @Test func theStatusMenuOfEachLevelHasTheItemsItSays() {
    #expect(render(model("stopped")).titles.contains("Start bosun"))
    #expect(!render(model("ok")).titles.contains("Start bosun"))
    #expect(render(model("warning")).titles.contains("Show What's Wrong…"))
    #expect(!render(model("ok")).titles.contains("Show What's Wrong…"))
    #expect(render(model("stopped")).titles.last == "Quit bosun")
  }

  @Test func enabledStatesAreTheModelsAndNotAppKitsOwn() {
    let menu = render(model("stopped"))
    #expect(!menu.autoenablesItems)
    let console = menu.items.first { $0.title == "Open Console" }
    #expect(console?.isEnabled == false)
    let start = menu.items.first { $0.title == "Start bosun" }
    #expect(start?.isEnabled == true)
  }

  @Test func keyEquivalentsAreTheModelsWithCommand() {
    let menu = render(model("ok"))
    let console = menu.items.first { $0.title == "Open Console" }
    let quit = menu.items.first { $0.title == "Quit bosun…" }
    #expect(console?.keyEquivalent == "o")
    #expect(console?.keyEquivalentModifierMask == .command)
    #expect(quit?.keyEquivalent == "q")
    #expect(quit?.keyEquivalentModifierMask == .command)
    let start = menu.items.first { $0.title == "Check for Updates…" }
    #expect(start?.keyEquivalent == "")
  }

  @Test func theHelpMenuIsASubmenu() {
    let menu = render(model("ok"))
    let help = menu.items.first { $0.title == "Help" }
    #expect(help?.submenu?.titles == ["bosun Guide", "Run bosun doctor", "Show Logs", "Uninstall bosun…"])
    #expect(help?.submenu?.autoenablesItems == false)
  }

  @Test func openAtLoginIsCheckedWhenItIsOn() {
    let on = render(model("ok", login: true)).items.first { $0.title == "Open at Login" }
    let off = render(model("ok", login: false)).items.first { $0.title == "Open at Login" }
    #expect(on?.state == .on)
    #expect(off?.state == .off)
  }

  @Test func anUpdateIsBadged() {
    let check = try! decodeContract(
      UpdateCheck.self,
      from: """
        {"contract": 1, "channel": "releases", "installed": {"version": "v1", "commit": "a"},
         "latest": {"version": "v2", "commit": "b", "notes": "n"}, "available": true, "skipped": false,
         "ahead": false, "error": null}
        """)
    let menu = render(model("ok", update: .available(check)))
    let item = menu.items.first { $0.attributedTitle == nil && $0.title.hasPrefix("Update Available") }
    #expect(item?.title == "Update Available: v2…")
    #expect(item?.badge?.stringValue == "New")
    let plain = render(model("ok")).items.first { $0.title == "Check for Updates…" }
    #expect(plain?.badge == nil)
  }

  @Test func eachActionItemCarriesItsIDAndAnAction() {
    var chosen: [ItemID] = []
    let menu = render(model("error")) { chosen.append($0) }
    let all = menu.items + menu.items.flatMap { $0.submenu?.items ?? [] }
    // (an item that opens a submenu has AppKit's own action, which is not ours)
    let actions = all.filter { $0.action != nil && $0.submenu == nil && !$0.isSeparatorItem }
    #expect(actions.count == 10)
    for item in actions {
      _ = item.target?.perform(item.action, with: item)
    }
    #expect(
      chosen == [
        .startBosun, .showProblems, .openConsole, .checkForUpdates, .openAtLogin, .quit, .help(.guide),
        .help(.doctor), .help(.logs), .help(.uninstall),
      ])
  }

  @Test func theStatusLinesAreNoActions() {
    let menu = render(model("ok"))
    #expect(menu.items[0].action == nil)
    #expect(menu.items[1].action == nil)
  }

  @Test func theLoadingMenuRenders() {
    let menu = render(loadingMenu(flow: FlowState()))
    #expect(menu.titles == ["● Checking bosun…", "—", "Help", "—", "Quit bosun.app"])
  }

  @Test func eachOfTheOtherStatesRenders() {
    let states: [StatusState] = [.notInstalled, .unreachable("cannot execute"), .contractTooNew(2)]
    for state in states {
      let menu = render(menu(for: state, update: .idle, flow: FlowState()))
      #expect(menu.items.count >= 4)
      #expect(menu.items.last?.title.hasPrefix("Quit bosun") == true)
    }
  }
}

@MainActor @Suite struct MenuRebuildTests {
  @Test func rebuildsInPlace() {
    let menu = NSMenu()
    let renderer = MenuRenderer(onChoose: { _ in })
    renderer.render(model("ok"), into: menu)
    let items = menu.items
    // Same shape, other state: the same items, updated (an open menu keeps its highlight)
    var flow = FlowState()
    _ = flow.begin(.quit)
    renderer.render(model("ok", flow: flow), into: menu)
    #expect(menu.items.count == items.count)
    #expect(zip(menu.items, items).allSatisfy { $0 === $1 })
    #expect(menu.items.first { $0.title == "Quit bosun…" }?.isEnabled == false)
    renderer.render(model("ok"), into: menu)
    #expect(menu.items.first { $0.title == "Quit bosun…" }?.isEnabled == true)
  }

  @Test func theSameMenuObjectIsKeptWhenTheShapeChanges() {
    let menu = NSMenu()
    let renderer = MenuRenderer(onChoose: { _ in })
    renderer.render(model("ok"), into: menu)
    renderer.render(model("error"), into: menu)
    #expect(menu.titles.contains("Start bosun"))
    #expect(menu.titles.contains("Show What's Wrong…"))
    renderer.render(model("ok"), into: menu)
    #expect(!menu.titles.contains("Start bosun"))
  }

  @Test func repeatedRendersDoNotLeakItems() {
    let menu = NSMenu()
    let renderer = MenuRenderer(onChoose: { _ in })
    renderer.render(model("ok"), into: menu)
    let first = menu.numberOfItems
    for index in 0..<20 {
      renderer.render(model(["ok", "error", "stopped", "warning"][index % 4]), into: menu)
    }
    renderer.render(model("ok"), into: menu)
    #expect(menu.numberOfItems == first)
    #expect(menu.titles == render(model("ok")).titles)
  }

  @Test func aChangedStatusLineIsUpdatedInPlace() {
    let menu = NSMenu()
    let renderer = MenuRenderer(onChoose: { _ in })
    renderer.render(model("ok"), into: menu)
    let header = menu.items[0]
    renderer.render(model("ok", sentence: "bosun is running, 2 waiting"), into: menu)
    #expect(menu.items[0] === header)
    #expect(menu.items[0].attributedTitle?.string == "● bosun is running, 2 waiting")
  }

  @Test func theDotChangesColourWithTheLevel() {
    let menu = NSMenu()
    let renderer = MenuRenderer(onChoose: { _ in })
    renderer.render(model("ok"), into: menu)
    renderer.render(model("warning"), into: menu)
    let dot =
      menu.items[0].attributedTitle?.attribute(.foregroundColor, at: 0, effectiveRange: nil) as? NSColor
    #expect(dot == .systemYellow)
  }

  @Test func aMenuOfAnotherShapeReplacesTheItems() {
    let menu = NSMenu()
    let renderer = MenuRenderer(onChoose: { _ in })
    renderer.render(model("ok"), into: menu)
    let loaded = model("ok")
    renderer.render(loadingMenu(flow: FlowState()), into: menu)
    #expect(menu.titles.first == "● Checking bosun…")
    renderer.render(loaded, into: menu)
    #expect(menu.titles.first == "● bosun is running")
  }

  @Test func theSubmenuIsUpdatedInPlaceToo() {
    let menu = NSMenu()
    let renderer = MenuRenderer(onChoose: { _ in })
    renderer.render(model("ok"), into: menu)
    let help = menu.items.first { $0.title == "Help" }?.submenu
    var flow = FlowState()
    _ = flow.begin(.update)
    renderer.render(model("ok", flow: flow), into: menu)
    #expect(menu.items.first { $0.title == "Help" }?.submenu === help)
    #expect(help?.items.last?.isEnabled == false)
  }
}
