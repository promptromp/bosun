import AppKit
import BosunMenuCore
import SwiftUI

// The Settings window ("bosun Settings", General and Schedule): SwiftUI over the core's `SettingsState`. It binds
// the state and calls back with a `SettingsEdit`; what is shown, what a Save sends and what is said back are the
// core's (Settings.swift, SettingsSchedule.swift), carried out by the coordinator (AppCoordinator+Settings.swift).

/// What the window shows, and where its edits go. What the Schedule pane's draft becomes, what Save sends and
/// what each switch means are the core's (`ScheduleDraft.next`, `SettingsState.saveEdit`, `startAtLoginEdit`).
@MainActor
final class SettingsModel: ObservableObject {
  @Published private(set) var state: SettingsState
  /// The Schedule pane's edits before Save; nil while there are no settings to edit.
  @Published var draft: ScheduleDraft?
  var apply: @MainActor (SettingsEdit) async -> SettingsState

  init(state: SettingsState, apply: @escaping @MainActor (SettingsEdit) async -> SettingsState) {
    self.state = state
    self.apply = apply
    draft = ScheduleDraft.next(nil, from: nil, to: state)
  }

  func show(_ state: SettingsState) {
    draft = ScheduleDraft.next(draft, from: self.state, to: state)
    self.state = state
  }

  func send(_ edit: SettingsEdit) {
    show(state.starting())
    Task { show(await apply(edit)) }
  }

  func saveSchedule() {
    if let edit = state.saveEdit(draft) { send(edit) }
  }
}

/// One window, made on the first Settings… and brought to the front with fresh values on the next. Each time it
/// becomes the key window (back from Terminal, or from another app) it calls `onKey`, which has bosun read again
/// (`AppCoordinator.refreshSettings`: what the window then shows is the core's).
@MainActor
final class SettingsWindowController {
  private struct Handler: @unchecked Sendable {
    let call: @MainActor () -> Void
  }

  private(set) var window: NSWindow?
  private(set) var model: SettingsModel?
  var onKey: @MainActor () -> Void = {}
  private var keyToken: (any NSObjectProtocol)?

  func show(_ state: SettingsState, apply: @escaping @MainActor (SettingsEdit) async -> SettingsState) {
    if let model, let window {
      model.apply = apply
      model.show(state)
      bringToFront(window)
      return
    }
    let model = SettingsModel(state: state, apply: apply)
    let window = NSWindow(contentViewController: NSHostingController(rootView: SettingsPanes(model: model)))
    window.title = "bosun Settings"
    window.styleMask = [.titled, .closable]
    window.isReleasedWhenClosed = false
    window.center()
    self.model = model
    self.window = window
    let handler = Handler { [weak self] in self?.onKey() }
    keyToken = NotificationCenter.default.addObserver(
      forName: NSWindow.didBecomeKeyNotification, object: window, queue: .main
    ) { _ in
      MainActor.assumeIsolated { handler.call() }
    }
    bringToFront(window)
  }

  /// A state the coordinator came to on its own (a test hook's edit, a Save under way), shown if the window is.
  func update(_ state: SettingsState) {
    model?.show(state)
  }

  private func bringToFront(_ window: NSWindow) {
    // a menu bar app is not active when its menu is used: the window comes forward only once it is
    NSApp.activate()
    window.makeKeyAndOrderFront(nil)
  }
}

// MARK: the panes

struct SettingsPanes: View {
  @ObservedObject var model: SettingsModel

  var body: some View {
    TabView {
      GeneralPane(model: model).tabItem { Label("General", systemImage: "gearshape") }
      SchedulePane(model: model).tabItem { Label("Schedule", systemImage: "calendar") }
    }
    .frame(width: 500)
    .fixedSize(horizontal: false, vertical: true)
  }
}

/// A setting's row: its control, or (a value the control cannot show) the value as text with its line.
struct SettingRowView<Control: View>: View {
  let label: String
  let row: SettingRow
  @ViewBuilder let control: () -> Control

  var body: some View {
    if let text = row.text {
      LabeledContent(label) { Text(text).foregroundStyle(.secondary) }
      if let note = row.note { Text(note).font(.caption).foregroundStyle(.secondary) }
    } else {
      control().disabled(!row.editable)
    }
  }
}

/// The line at the top (`bosun.env` cannot be read) and the one under the form (the last failure or note).
struct MessageSection: View {
  let state: SettingsState

  var body: some View {
    if let problem = state.problem { Section { Text(problem).foregroundStyle(.secondary) } }
    if let message = state.message { Section { Text(message).textSelection(.enabled) } }
  }
}

struct GeneralPane: View {
  @ObservedObject var model: SettingsModel

  var body: some View {
    Form {
      Section {
        openAtLogin
        startAtLogin
        updates
      }
      Section {
        Button("Change windows and connectors…") { model.send(.changeWindows) }
        HStack {
          Button("Open Config Folder") { model.send(.openConfigFolder) }
          Button("Show Logs") { model.send(.showLogs) }
        }
      }
      MessageSection(state: model.state)
    }
    .formStyle(.grouped)
  }

  /// Its switch while bosun could tell, else "unknown" with a line and the two choices: a switch drawn off would
  /// say it is off, and a mixed switch would not say which way a click goes.
  @ViewBuilder private var openAtLogin: some View {
    let label = "Open bosun.app at login"
    let row = model.state.openAtLoginRow
    if let text = row.text {
      LabeledContent(label) {
        HStack {
          Text(text).foregroundStyle(.secondary)
          Button("Turn On") { model.send(.openAtLogin(true)) }
          Button("Turn Off") { model.send(.openAtLogin(false)) }
        }
        .disabled(!row.editable)
      }
      if let note = row.note { Text(note).font(.caption).foregroundStyle(.secondary) }
    } else {
      Toggle(
        label,
        isOn: Binding(get: { model.state.openAtLogin == true }, set: { model.send(.openAtLogin($0)) })
      )
      .disabled(!row.editable)
    }
  }

  private var startAtLogin: some View {
    let label = "Start bosun when you log in, during your hours"
    return SettingRowView(label: label, row: model.state.row(SettingKey.startAtLogin)) {
      Toggle(
        label,
        isOn: Binding(
          get: { model.state.startsAtLogin },
          set: { model.send(model.state.startAtLoginEdit($0)) }))
    }
  }

  private var updates: some View {
    SettingRowView(label: "Updates from", row: model.state.row(SettingKey.updateChannel)) {
      Picker(
        "Updates from",
        selection: Binding(
          get: { model.state.value(SettingKey.updateChannel) ?? "" },
          set: { model.send(.values([SettingKey.updateChannel: $0])) })
      ) {
        Text("Releases").tag("releases")
        Text("main").tag("main")
      }
    }
  }
}

struct SchedulePane: View {
  @ObservedObject var model: SettingsModel

  var body: some View {
    Form {
      Section {
        SettingRowView(label: "Days", row: model.state.row(SettingKey.days)) {
          LabeledContent("Days") {
            HStack(spacing: 4) {
              ForEach(0..<7, id: \.self) { day in
                Toggle(ScheduleDraft.dayLabels[day], isOn: dayBinding(day)).toggleStyle(.button)
              }
            }
          }
        }
        SettingRowView(label: "Start at", row: model.state.row(SettingKey.startTime)) {
          DatePicker("Start at", selection: timeBinding(\.start), displayedComponents: .hourAndMinute)
        }
        SettingRowView(label: "Keep the Mac awake until", row: model.state.row(SettingKey.awakeUntil)) {
          DatePicker(
            "Keep the Mac awake until", selection: timeBinding(\.until), displayedComponents: .hourAndMinute)
        }
      }
      Section {
        if let line = model.draft?.problem ?? model.state.scheduleLine {
          Text(line).foregroundStyle(.secondary)
        }
        HStack {
          Spacer()
          Button("Save") { model.saveSchedule() }
            .disabled(!model.state.canSave(model.draft))
            .keyboardShortcut(.defaultAction)
        }
      }
      MessageSection(state: model.state)
    }
    .formStyle(.grouped)
  }

  private func dayBinding(_ day: Int) -> Binding<Bool> {
    Binding(get: { model.draft?.days[day] ?? false }, set: { model.draft?.days[day] = $0 })
  }

  private func timeBinding(_ path: WritableKeyPath<ScheduleDraft, TimeOfDay>) -> Binding<Date> {
    Binding(
      get: { TimeOfDay.date(of: model.draft?[keyPath: path] ?? TimeOfDay(hour: 0, minute: 0)) },
      set: { model.draft?[keyPath: path] = TimeOfDay.time(of: $0) })
  }
}

extension TimeOfDay {
  /// Today at this time, for a `DatePicker` that shows hours and minutes.
  static func date(of time: TimeOfDay, calendar: Calendar = .current) -> Date {
    calendar.date(bySettingHour: time.hour, minute: time.minute, second: 0, of: Date()) ?? Date()
  }

  /// The hours and minutes of a `DatePicker`'s date.
  static func time(of date: Date, calendar: Calendar = .current) -> TimeOfDay {
    let parts = calendar.dateComponents([.hour, .minute], from: date)
    return TimeOfDay(hour: parts.hour ?? 0, minute: parts.minute ?? 0)
  }
}
