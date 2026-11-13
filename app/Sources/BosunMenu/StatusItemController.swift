import AppKit
import BosunMenuCore

// The menu bar icon: bosun's knot as a template image (black on clear, so the menu bar tints it for light
// and dark), a small coloured dot drawn over its corner for a warning, an error or an update, and the menu.

enum StatusIcon {
  static let side: CGFloat = 18

  /// The art in the app bundle (`MenuBarIcon.png` and `MenuBarIcon@2x.png`: black on clear, made from the
  /// logo by tools/app_icons.py), as a template image so the menu bar tints it for a light or a dark bar.
  /// Without the art (the app run outside its bundle) a plain system symbol stands in.
  @MainActor
  static func template(resources: URL? = Bundle.main.resourceURL) -> NSImage {
    let image =
      artwork(in: resources)
      ?? NSImage(systemSymbolName: "link", accessibilityDescription: "bosun")
      ?? NSImage()
    image.size = NSSize(width: side, height: side)
    image.isTemplate = true
    return image
  }

  /// One image of both scales, each file a representation of the same eighteen points.
  private static func artwork(in folder: URL?) -> NSImage? {
    guard let folder else { return nil }
    let image = NSImage(size: NSSize(width: side, height: side))
    for name in ["MenuBarIcon.png", "MenuBarIcon@2x.png"] {
      guard
        let data = try? Data(contentsOf: folder.appendingPathComponent(name)),
        let representation = NSBitmapImageRep(data: data)
      else { continue }
      representation.size = NSSize(width: side, height: side)
      image.addRepresentation(representation)
    }
    return image.representations.isEmpty ? nil : image
  }

  static func dotColor(for badge: IconBadge?) -> NSColor? {
    switch badge {
    case nil: return nil
    case .warning?: return .systemYellow
    case .error?: return .systemRed
    case .update?: return .systemBlue
    }
  }

  /// A small square in the lower right corner of the button.
  static func dotFrame(in bounds: NSRect) -> NSRect {
    let side: CGFloat = 7
    return NSRect(x: bounds.maxX - side - 3, y: bounds.minY + 3, width: side, height: side)
  }
}

/// The dot over the icon. It takes no clicks: they are the button's.
@MainActor
final class BadgeDotView: NSView {
  private(set) var colour: NSColor?

  init() {
    super.init(frame: .zero)
    isHidden = true
  }

  @available(*, unavailable)
  required init?(coder: NSCoder) { fatalError("not made from a nib") }

  func show(_ badge: IconBadge?) {
    colour = StatusIcon.dotColor(for: badge)
    isHidden = colour == nil
    needsDisplay = true
  }

  override func hitTest(_ point: NSPoint) -> NSView? { nil }

  override func draw(_ dirtyRect: NSRect) {
    guard let colour else { return }
    colour.setFill()
    NSBezierPath(ovalIn: bounds).fill()
  }
}

/// The status item with its menu. The menu refreshes the status when it opens (`onOpen`).
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate {
  private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
  private let menu = NSMenu()
  private let dot = BadgeDotView()
  private let renderer: MenuRenderer
  var onOpen: @MainActor () -> Void = {}

  init(onChoose: @escaping @MainActor (ItemID) -> Void) {
    renderer = MenuRenderer(onChoose: onChoose)
    super.init()
    menu.delegate = self
    statusItem.menu = menu
    if let button = statusItem.button {
      button.image = StatusIcon.template()
      button.addSubview(dot)
    }
  }

  func show(_ model: MenuModel) {
    renderer.render(model, into: menu)
    if let button = statusItem.button { dot.frame = StatusIcon.dotFrame(in: button.bounds) }
    dot.show(model.iconBadge)
  }

  func menuWillOpen(_ menu: NSMenu) {
    onOpen()
  }
}
