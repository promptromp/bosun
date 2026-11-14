import AppKit
import BosunMenuCore
import SwiftUI

// The Settings window ("bosun Settings", General and Schedule): SwiftUI over the core's `SettingsState`. It binds
// the state and calls back with a `SettingsEdit`; what is shown, what a Save sends and what is said back are the
// core's (Settings.swift, SettingsSchedule.swift), carried out by the coordinator (AppCoordinator+Settings.swift).

/// What the window shows, and where its edits go.
@MainActor
final class SettingsModel: ObservableObject {
  @Published private(set) var state: SettingsState
  /// The Schedule pane's edits before Save; nil while there are no settings to edit.
  @Published var draft: ScheduleDraft?
  var apply: @MainActor (SettingsEdit) async -> SettingsState

  init(state: SettingsState, apply: @escaping @MainActor (SettingsEdit) async -> SettingsState) {
    self.state = state
    self.apply = apply
    draft = state.view.map(ScheduleDraft.init)
  }

  /// Shows `state`; the Schedule pane's draft starts again from what bosun read when that changed.
  func show(_ state: SettingsState) {
    // every render passes the coordinator's state on: the same one again changes nothing
    if state == self.state { return }
    let readAgain = state.view != self.state.view
    self.state = state
    if readAgain || draft == nil { draft = state.view.map(ScheduleDraft.init) }
  }

  func send(_ edit: SettingsEdit) {
    show(state.starting())
    Task { show(await apply(edit)) }
  }

  /// What bosun read for `key`.
  func value(_ key: String) -> String? {
    state.view?.settings[key]?.value
  }

  func saveSchedule() {
    guard let draft, let view = state.view else { return }
    send(.values(draft.changes(from: view)))
  }

  var canSaveSchedule: Bool {
    guard let draft, let view = state.view else { return false }
    return !state.busy && draft.canSave(view)
  }
}

/// One window, made on the first Settings… and brought to the front with fresh values on the next.
@MainActor
final class SettingsWindowController {
  private(set) var window: NSWindow?
  private(set) var model: SettingsModel?

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
        Toggle(
          "Open bosun.app at login",
          isOn: Binding(get: { model.state.openAtLogin }, set: { model.send(.openAtLogin($0)) })
        )
        .disabled(model.state.busy)
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

  private var startAtLogin: some View {
    let label = "Start bosun when you log in, during your hours"
    return SettingRowView(label: label, row: model.state.row(SettingKey.startAtLogin)) {
      Toggle(
        label,
        isOn: Binding(
          get: { model.value(SettingKey.startAtLogin) == "1" },
          set: { model.send(.values([SettingKey.startAtLogin: $0 ? "1" : "0"])) }))
    }
  }

  private var updates: some View {
    SettingRowView(label: "Updates from", row: model.state.row(SettingKey.updateChannel)) {
      Picker(
        "Updates from",
        selection: Binding(
          get: { model.value(SettingKey.updateChannel) ?? "" },
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
        if let line = model.draft?.problem ?? model.draft?.summary { Text(line).foregroundStyle(.secondary) }
        HStack {
          Spacer()
          Button("Save") { model.saveSchedule() }
            .disabled(!model.canSaveSchedule)
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
