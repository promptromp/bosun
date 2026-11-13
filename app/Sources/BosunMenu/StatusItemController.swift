import AppKit
import BosunMenuCore

// The menu bar icon: bosun's knot as a template image (black on clear, so the menu bar tints it for light
// and dark), a small coloured dot drawn over its corner for a warning, an error or an update, and the menu.

enum StatusIcon {
  /// The art in the app bundle (`MenuIcon`, drawn once as a template image), else a plain drawn knot, so the
  /// icon exists when the app runs outside its bundle.
  @MainActor
  static func template() -> NSImage {
    let image = Bundle.main.image(forResource: "MenuIcon") ?? drawnKnot()
    image.size = NSSize(width: 18, height: 18)
    image.isTemplate = true
    return image
  }

  private static func drawnKnot() -> NSImage {
    NSImage(size: NSSize(width: 18, height: 18), flipped: false) { rect in
      NSColor.black.setStroke()
      for angle in [45.0, -45.0] {
        let ring = NSBezierPath(ovalIn: NSRect(x: -5.5, y: -3, width: 11, height: 6))
        ring.lineWidth = 1.7
        var transform = AffineTransform(translationByX: rect.midX, byY: rect.midY)
        transform.rotate(byDegrees: angle)
        ring.transform(using: transform)
        ring.stroke()
      }
      return true
    }
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
