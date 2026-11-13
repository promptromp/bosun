import AppKit
import BosunMenuCore

// bosun.app: a menu bar app (LSUIElement, no Dock icon). Everything it decides is in BosunMenuCore; this is
// the AppKit shell around it.

let delegate = AppDelegate(options: LaunchOptions.parse(CommandLine.arguments))
let application = NSApplication.shared
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
