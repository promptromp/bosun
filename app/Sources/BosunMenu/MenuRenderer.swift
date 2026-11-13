import AppKit
import BosunMenuCore

// A `MenuModel` as an `NSMenu`: the status line with its coloured dot, the grey detail lines, a separator
// before each section, the items with their enabled state, key equivalent, check mark and badge, and
// submenus. The menu is updated in place: when the shape is the same as the last render (the same items in
// the same order) its items are kept and only their state changes, so a menu that is open keeps its
// highlight while its titles change; when the shape differs the items are replaced. Either way the same
// `NSMenu` and no item left over.

/// What an action item carries: its id, and what to do when it is chosen. It is the item's target and its
/// represented object, so the item keeps it alive (a menu item's target is weak).
@MainActor
final class ItemBox: NSObject {
  let id: ItemID
  let onChoose: @MainActor (ItemID) -> Void

  init(id: ItemID, onChoose: @escaping @MainActor (ItemID) -> Void) {
    self.id = id
    self.onChoose = onChoose
  }

  @objc func chosen(_ sender: Any?) {
    onChoose(id)
  }
}

/// Marks the items that are the status line and its detail lines.
private final class LineTag: NSObject {
  let kind: String
  init(_ kind: String) { self.kind = kind }
}

private enum Row {
  case header(StatusHeader)
  case detail(String)
  case separator
  case item(MenuItem)

  var shape: String {
    switch self {
    case .header: return "header"
    case .detail: return "detail"
    case .separator: return "—"
    case .item(let item): return Row.shape(of: item)
    }
  }

  static func shape(of item: MenuItem) -> String {
    item.id.name
      + (item.children.isEmpty ? "" : "[" + item.children.map(shape(of:)).joined(separator: ",") + "]")
  }
}

private func rows(of model: MenuModel) -> [Row] {
  var rows: [Row] = [.header(model.header)] + model.header.detail.map(Row.detail)
  for section in model.sections {
    rows.append(.separator)
    rows += section.map(Row.item)
  }
  return rows
}

private func colour(of dot: Dot) -> NSColor {
  switch dot {
  case .green: return .systemGreen
  case .yellow: return .systemYellow
  case .red: return .systemRed
  case .grey: return .systemGray
  }
}

@MainActor
struct MenuRenderer {
  let onChoose: @MainActor (ItemID) -> Void

  func render(_ model: MenuModel, into menu: NSMenu) {
    menu.autoenablesItems = false
    let wanted = rows(of: model)
    if menu.items.map(Self.shape(of:)) == wanted.map(\.shape) {
      for (item, row) in zip(menu.items, wanted) { update(item, from: row) }
    } else {
      menu.removeAllItems()
      for row in wanted {
        let item = makeItem(for: row)
        update(item, from: row)
        menu.addItem(item)
      }
    }
  }

  // MARK: shapes

  /// The shape of an item already in a menu, as `Row.shape` says it for a row.
  private static func shape(of item: NSMenuItem) -> String {
    if item.isSeparatorItem { return "—" }
    if let box = item.representedObject as? ItemBox {
      guard let submenu = item.submenu else { return box.id.name }
      return box.id.name + "[" + submenu.items.map(shape(of:)).joined(separator: ",") + "]"
    }
    return (item.representedObject as? LineTag)?.kind ?? "?"
  }

  // MARK: making and updating

  /// An item with the identity of its row (id, target, submenu); `update` gives it its state.
  private func makeItem(for row: Row) -> NSMenuItem {
    switch row {
    case .separator:
      return .separator()
    case .header:
      return textRow("header")
    case .detail:
      return textRow("detail")
    case .item(let model):
      let item = NSMenuItem(title: model.title, action: nil, keyEquivalent: "")
      let box = ItemBox(id: model.id, onChoose: onChoose)
      item.representedObject = box
      if model.children.isEmpty {
        item.target = box
        item.action = #selector(ItemBox.chosen(_:))
      } else {
        let submenu = NSMenu(title: model.title)
        submenu.autoenablesItems = false
        for child in model.children { submenu.addItem(makeItem(for: .item(child))) }
        item.submenu = submenu
      }
      return item
    }
  }

  private func textRow(_ kind: String) -> NSMenuItem {
    let item = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    item.representedObject = LineTag(kind)
    item.isEnabled = false
    return item
  }

  private func update(_ item: NSMenuItem, from row: Row) {
    switch row {
    case .separator:
      break
    case .header(let header):
      item.attributedTitle = Self.statusLine(header)
    case .detail(let text):
      item.attributedTitle = NSAttributedString(
        string: text,
        attributes: [
          .foregroundColor: NSColor.secondaryLabelColor,
          .font: NSFont.menuFont(ofSize: NSFont.smallSystemFontSize),
        ])
    case .item(let model):
      apply(model, to: item)
    }
  }

  private func apply(_ model: MenuItem, to item: NSMenuItem) {
    item.title = model.title
    item.isEnabled = model.enabled
    item.state = model.checked ? .on : .off
    item.keyEquivalent = model.key ?? ""
    item.keyEquivalentModifierMask = model.key == nil ? [] : .command
    item.badge = model.badge ? NSMenuItemBadge(string: "New") : nil
    if let submenu = item.submenu {
      for (child, childModel) in zip(submenu.items, model.children) { apply(childModel, to: child) }
    }
  }

  /// The dot in its colour, then the sentence.
  private static func statusLine(_ header: StatusHeader) -> NSAttributedString {
    let line = NSMutableAttributedString(
      string: "● ", attributes: [.foregroundColor: colour(of: header.dot)])
    line.append(
      NSAttributedString(string: header.sentence, attributes: [.foregroundColor: NSColor.labelColor]))
    return line
  }
}
