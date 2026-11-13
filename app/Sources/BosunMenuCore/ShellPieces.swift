import Foundation

// Small pieces the AppKit shell needs that have nothing of AppKit in them: the guide's address, what
// `bosun console --url` printed read as an address to open, and the `.command` files that open Terminal.

public enum Links {
  /// The guide: bosun's docs site (mkdocs.yml's `site_url`).
  public static let guide = URL(string: "https://promptromp.github.io/bosun/") ?? URL(fileURLWithPath: "/")
}

// MARK: the console's address

public struct ConsoleURLError: Error, Equatable, Sendable {
  public let message: String
}

/// The console's address from `bosun console --url`: its first line. The answer is opened in the browser, so
/// it must be the console on this Mac (http, 127.0.0.1 or localhost) and nothing else.
public func consoleURL(from result: CommandResult) -> Result<URL, ConsoleURLError> {
  switch result.outcome {
  case .exited(0):
    return localAddress(in: result.stdout)
  case .exited(let code):
    return .failure(ConsoleURLError(message: FetchFailure.exited(code, firstLine(result.stderr)).message))
  default:
    return .failure(ConsoleURLError(message: failureText(result)))
  }
}

private func localAddress(in text: String) -> Result<URL, ConsoleURLError> {
  let line = firstLine(text)
  guard let url = URL(string: line), url.scheme == "http", let host = url.host,
    host == "127.0.0.1" || host == "localhost"
  else {
    return .failure(
      ConsoleURLError(message: "bosun gave an address that is not the console on this Mac: \(line)"))
  }
  return .success(url)
}

/// Why a command did not work, in a line: what it said, or what happened to it.
func failureText(_ result: CommandResult) -> String {
  switch result.outcome {
  case .notInstalled: return FetchFailure.notInstalled.message
  case .timedOut: return FetchFailure.timedOut.message
  case .couldNotStart(let why): return why
  case .exited(let code): return FetchFailure.exited(code, firstLine(result.stderr)).message
  case .started: return "bosun was started and left running"
  }
}

// MARK: Terminal

/// Makes the `.command` file that runs `bosun <args>` in Terminal and says where it is. Terminal is reached by
/// opening a file, never by AppleScript (which would ask for the Automation permission the app does not have).
/// The writing is BosunMenuSystem's.
public protocol ScriptWriting: Sendable {
  func write(args: [String]) throws -> URL
}

public enum CommandScript {
  /// A word as a shell takes it literally.
  public static func quote(_ word: String) -> String {
    "'" + word.replacingOccurrences(of: "'", with: "'\\''") + "'"
  }

  /// The script: bosun with the arguments, then a wait for Return, so the window (which Terminal closes on a
  /// clean exit) stays until it has been read; bosun's status is the script's.
  public static func text(executable: String, args: [String]) -> String {
    let command = ([executable] + args).map(quote).joined(separator: " ")
    return """
      #!/bin/sh
      \(command)
      status=$?
      printf '\\nPress Return to close this window. '
      read -r _answer
      exit $status

      """
  }
}

// MARK: lookups

extension MenuModel {
  /// The item with this id, in the menu or one of its submenus.
  public func find(_ id: ItemID) -> MenuItem? {
    func find(_ items: [MenuItem]) -> MenuItem? {
      for item in items {
        if item.id == id { return item }
        if let found = find(item.children) { return found }
      }
      return nil
    }
    return find(sections.flatMap { $0 })
  }
}
