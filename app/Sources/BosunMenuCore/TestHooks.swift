import Foundation

// The test hooks (`--test-hooks <dir>`), for the VM suite: the app writes its menu where a script can read
// it, takes actions a script drops in a folder, and logs what it would open and what it runs. Their files
// and formats live here, tested; the shell only wires them up, and only when it was given the argument.
//
//   <dir>/menu.json        the menu after each render: header, sections, icon badge, and the status level
//   <dir>/actions/<n>.json {"item": "<id>"} as if the item was clicked; {"answer": {"button": "<title>",
//                          "boxes": {"<id>": true}}} for the open dialog; taken in the order of n and then
//                          removed (the script waits for its file to go)
//   <dir>/log.jsonl        one object per line: {"command": [...], "detached": false, "outcome": "exited 0"},
//                          {"open": "<url or path>"}, {"error": "..."}

// MARK: item names

extension HelpTopic {
  var name: String {
    switch self {
    case .guide: return "guide"
    case .doctor: return "doctor"
    case .logs: return "logs"
    case .uninstall: return "uninstall"
    }
  }
}

extension ItemID {
  /// Every item the menu has.
  private static let all: [ItemID] = [
    .startBosun, .showProblems, .openConsole, .checkForUpdates, .openAtLogin, .updateBosun, .helpMenu,
    .help(.guide), .help(.doctor), .help(.logs), .help(.uninstall), .quit,
  ]

  /// The name a hook action uses: `quit`, `help.doctor`.
  public var name: String {
    switch self {
    case .startBosun: return "startBosun"
    case .showProblems: return "showProblems"
    case .openConsole: return "openConsole"
    case .checkForUpdates: return "checkForUpdates"
    case .openAtLogin: return "openAtLogin"
    case .updateBosun: return "updateBosun"
    case .helpMenu: return "helpMenu"
    case .help(let topic): return "help." + topic.name
    case .quit: return "quit"
    }
  }

  public init?(name: String) {
    guard let found = Self.all.first(where: { $0.name == name }) else { return nil }
    self = found
  }
}

// MARK: actions

public struct HookAnswer: Equatable, Sendable, Decodable {
  public let button: String
  public let boxes: [String: Bool]

  public init(button: String, boxes: [String: Bool]) {
    self.button = button
    self.boxes = boxes
  }

  private enum Key: String, CodingKey { case button, boxes }

  public init(from decoder: any Decoder) throws {
    let values = try decoder.container(keyedBy: Key.self)
    self.init(
      button: try values.decode(String.self, forKey: .button),
      boxes: try values.decodeIfPresent([String: Bool].self, forKey: .boxes) ?? [:])
  }
}

public enum HookError: Error, Equatable, Sendable, CustomStringConvertible {
  case unreadable(String)
  case unknownItem(String)
  case noAction

  public var description: String {
    switch self {
    case .unreadable(let why): return "the action could not be read: \(why)"
    case .unknownItem(let name): return "no menu item is called \"\(name)\""
    case .noAction: return "the file has neither \"item\" nor \"answer\""
    }
  }
}

public enum HookAction: Equatable, Sendable {
  case item(ItemID)
  case answer(HookAnswer)

  private struct Raw: Decodable {
    let item: String?
    let answer: HookAnswer?
  }

  public static func decode(_ data: Data) throws(HookError) -> HookAction {
    let raw: Raw
    do {
      raw = try JSONDecoder().decode(Raw.self, from: data)
    } catch {
      throw HookError.unreadable("it is not {\"item\": …} or {\"answer\": {\"button\": …}}")
    }
    if let name = raw.item {
      guard let id = ItemID(name: name) else { throw HookError.unknownItem(name) }
      return .item(id)
    }
    if let answer = raw.answer { return .answer(answer) }
    throw HookError.noAction
  }
}

extension HookAnswer {
  /// The answer as the dialog takes it: the button by its title, the boxes from the dialog's defaults with
  /// the answer's on top. Nil when the dialog has no such button or no such box.
  public func resolve(for spec: DialogSpec) -> DialogAnswer? {
    guard let index = spec.buttons.firstIndex(of: button) else { return nil }
    var states = spec.defaultAnswer.boxes
    for (id, on) in boxes {
      guard states[id] != nil else { return nil }
      states[id] = on
    }
    return DialogAnswer(button: index, boxes: states)
  }
}

// MARK: the log

public enum HookLogEntry: Equatable, Sendable {
  case command([String], detached: Bool, outcome: String)
  case open(String)
  case error(String)

  var object: [String: Any] {
    switch self {
    case .command(let args, let detached, let outcome):
      return ["command": args, "detached": detached, "outcome": outcome]
    case .open(let target): return ["open": target]
    case .error(let why): return ["error": why]
    }
  }
}

/// How a command ended, in a word for the log.
func describe(_ outcome: CommandOutcome) -> String {
  switch outcome {
  case .exited(let code): return "exited \(code)"
  case .started: return "started"
  case .timedOut: return "timed out"
  case .notInstalled: return "not installed"
  case .couldNotStart(let why): return "could not start: \(why)"
  }
}

/// A `BosunCLI` that logs each command it passes on and how it ended.
public struct LoggingBosunCLI: BosunCLI {
  private let inner: any BosunCLI
  private let log: @Sendable (HookLogEntry) -> Void

  public init(wrapping inner: any BosunCLI, log: @escaping @Sendable (HookLogEntry) -> Void) {
    self.inner = inner
    self.log = log
  }

  public func run(_ args: [String], timeout: Duration) async -> CommandResult {
    let result = await inner.run(args, timeout: timeout)
    log(.command(args, detached: false, outcome: describe(result.outcome)))
    return result
  }

  public func start(_ args: [String]) async -> CommandResult {
    let result = await inner.start(args)
    log(.command(args, detached: true, outcome: describe(result.outcome)))
    return result
  }
}

// MARK: the files

public struct TestHookFiles: Sendable {
  public let directory: URL

  public init(directory: URL) {
    self.directory = directory
  }

  private var actionsFolder: URL { directory.appendingPathComponent("actions") }

  public func prepare() throws {
    try FileManager.default.createDirectory(at: actionsFolder, withIntermediateDirectories: true)
  }

  public func writeMenu(_ model: MenuModel, status: StatusState?) throws {
    try menuJSON(model, status: status).write(
      to: directory.appendingPathComponent("menu.json"), options: .atomic)
  }

  /// The actions waiting, in the order of their numbers, taken (their files removed). An answer is for the
  /// open dialog: with none open it waits, and so does everything after it. A file that is not an action is
  /// logged and removed.
  public func takeActions(dialogOpen: Bool) -> [HookAction] {
    var taken: [HookAction] = []
    for file in pendingFiles() {
      guard let data = try? Data(contentsOf: file) else { continue }
      do {
        let action = try HookAction.decode(data)
        if case .answer = action, !dialogOpen { break }
        taken.append(action)
      } catch {
        log(.error("\(file.lastPathComponent): \(error)"))
      }
      try? FileManager.default.removeItem(at: file)
    }
    return taken
  }

  /// `<n>.json` files, by n.
  private func pendingFiles() -> [URL] {
    let names = (try? FileManager.default.contentsOfDirectory(atPath: actionsFolder.path)) ?? []
    return names.compactMap { name -> (Int, URL)? in
      guard name.hasSuffix(".json"), let number = Int(name.dropLast(".json".count)) else { return nil }
      return (number, actionsFolder.appendingPathComponent(name))
    }
    .sorted { $0.0 < $1.0 }.map(\.1)
  }

  public func log(_ entry: HookLogEntry) {
    let options: JSONSerialization.WritingOptions = [.sortedKeys, .withoutEscapingSlashes]
    guard var line = try? JSONSerialization.data(withJSONObject: entry.object, options: options) else {
      return
    }
    line.append(0x0A)
    let url = directory.appendingPathComponent("log.jsonl")
    if !FileManager.default.fileExists(atPath: url.path) {
      FileManager.default.createFile(atPath: url.path, contents: nil)
    }
    guard let handle = try? FileHandle(forWritingTo: url) else { return }
    defer { try? handle.close() }
    _ = try? handle.seekToEnd()
    try? handle.write(contentsOf: line)
  }
}

// MARK: the menu as JSON

/// The status level, or what the app knows when there is no status.
public func statusLevelName(_ status: StatusState?) -> String {
  switch status {
  case nil: return "loading"
  case .loaded(let snapshot, _)?: return snapshot.level.rawValue
  case .unreachable?: return "unreachable"
  case .notInstalled?: return "not_installed"
  case .contractTooNew?: return "contract_too_new"
  }
}

private func dotName(_ dot: Dot) -> String {
  switch dot {
  case .green: return "green"
  case .yellow: return "yellow"
  case .red: return "red"
  case .grey: return "grey"
  }
}

private func badgeName(_ badge: IconBadge?) -> Any {
  switch badge {
  case nil: return NSNull()
  case .warning?: return "warning"
  case .error?: return "error"
  case .update?: return "update"
  }
}

private func itemObject(_ item: MenuItem) -> [String: Any] {
  [
    "id": item.id.name, "title": item.title, "enabled": item.enabled,
    "key": item.key.map { $0 as Any } ?? NSNull(),
    "badge": item.badge, "checked": item.checked, "children": item.children.map(itemObject),
  ]
}

/// The menu as the test hooks write it (`menu.json`), with the status level.
public func menuJSON(_ model: MenuModel, status: StatusState?) -> Data {
  let object: [String: Any] = [
    "level": statusLevelName(status),
    "header": [
      "dot": dotName(model.header.dot), "sentence": model.header.sentence, "detail": model.header.detail,
    ],
    "icon_badge": badgeName(model.iconBadge),
    "sections": model.sections.map { $0.map(itemObject) },
  ]
  return
    (try? JSONSerialization.data(
      withJSONObject: object, options: [.sortedKeys, .prettyPrinted, .withoutEscapingSlashes])) ?? Data()
}
