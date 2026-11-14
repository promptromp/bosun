import Foundation
import Testing

@testable import BosunMenuCore

/// The settings as `settings get --json` prints them, with one entry changed: `value` and `valid` replaced.
private func view(
  _ name: String = "settings-get", wake: String? = nil, key: String? = nil, value: String? = nil,
  valid: Bool = true
) throws -> SettingsView {
  var object = try #require(
    try JSONSerialization.jsonObject(with: Data(Fixtures.text(name).utf8)) as? [String: Any])
  if let wake { object["wake"] = wake }
  if let key {
    var settings = try #require(object["settings"] as? [String: Any])
    var entry = try #require(settings[key] as? [String: Any])
    entry["value"] = value ?? NSNull()
    entry["valid"] = valid
    entry["set"] = true
    settings[key] = entry
    object["settings"] = settings
  }
  let text = try #require(String(bytes: JSONSerialization.data(withJSONObject: object), encoding: .utf8))
  return try decodeContract(SettingsView.self, from: text)
}

private func state(_ view: SettingsView?) -> SettingsState {
  SettingsState(view: view, openAtLogin: true, message: nil, busy: false)
}

@Suite struct SettingsContractTests {
  @Test func theSettingsAreReadWithTheirKeysAsBosunNamesThem() throws {
    let read = try Fixtures.decode(SettingsView.self, "settings-get")
    #expect(read.readable)
    #expect(read.error == nil)
    #expect(read.wake == "ours")
    #expect(read.configDir == "/Users/me/.config/bosun")
    let start = try #require(read.settings[SettingKey.startTime])
    #expect(
      start
        == SettingEntry(
          value: "08:30", defaultValue: "08:00", set: true, valid: true,
          meaning: "When bosun starts on those days (HH:MM); the Mac is woken just after it"))
    #expect(
      Set(read.settings.keys) == [
        SettingKey.startAtLogin, SettingKey.updateChannel, SettingKey.days, SettingKey.startTime,
        SettingKey.awakeUntil,
      ])
  }

  @Test func anUnreadableBosunEnvHasNoValues() throws {
    let read = try Fixtures.decode(SettingsView.self, "settings-get-unreadable")
    #expect(!read.readable)
    #expect(read.error == "Permission denied")
    #expect(read.settings.values.allSatisfy { $0.value == nil && !$0.valid })
  }

  @Test func aChangeAndARefusalAreRead() throws {
    let done = try Fixtures.decode(SettingsSetReply.self, "settings-set")
    #expect(done.changed?[SettingKey.startTime] == ["08:30", "09:00"])
    #expect(done.notes?.count == 1)
    #expect(done.error == nil)
    let refused = try Fixtures.decode(SettingsSetReply.self, "settings-set-refused")
    #expect(refused.error == "BOSUN_START_TIME: '25:00' must be HH:MM, 24-hour")
    #expect(refused.changed == nil)
  }

  @Test func theCommandsAreBosunSettingsWithTheirTimeouts() {
    #expect(Bosun.settingsGet == Command(args: ["settings", "get", "--json"], timeout: .seconds(15)))
    let set = Bosun.settingsSet([SettingKey.startTime: "09:00", SettingKey.awakeUntil: "18:00"])
    #expect(
      set
        == Command(
          args: ["settings", "set", "BOSUN_AWAKE_UNTIL=18:00", "BOSUN_START_TIME=09:00", "--json"],
          timeout: .seconds(30)))
  }
}

@Suite struct SettingsStateTests {
  @Test func testNeedsTerminalOnlyForOursWake() throws {
    let start = [SettingKey.startTime: "09:00"]
    #expect(state(try view(wake: "ours")).needsTerminal(start))
    #expect(state(try view(wake: "ours")).needsTerminal([SettingKey.days: "MTWRFS"]))
    #expect(!state(try view(wake: "none")).needsTerminal(start))
    #expect(!state(try view(wake: "other")).needsTerminal(start))
    #expect(!state(try view(wake: "ours")).needsTerminal([SettingKey.awakeUntil: "18:00"]))
    #expect(!state(try view(wake: "ours")).needsTerminal([SettingKey.updateChannel: "main"]))
    // with no view there is no wake known to be bosun's
    #expect(!state(nil).needsTerminal(start))
  }

  @Test func startingIsBusyWithNoMessage() {
    let before = SettingsState(view: nil, openAtLogin: false, message: "it failed", busy: false)
    #expect(before.starting() == SettingsState(view: nil, openAtLogin: false, message: nil, busy: true))
  }

  @Test func aValueBosunTakesIsTheControls() throws {
    let row = state(try view()).row(SettingKey.startTime)
    #expect(row == SettingRow(editable: true, text: nil, note: nil))
  }

  @Test func testInvalidValueShownAsIs() throws {
    let row = state(try view(key: SettingKey.startTime, value: "8am", valid: false)).row(SettingKey.startTime)
    #expect(!row.editable)
    #expect(row.text == "8am")
    #expect(row.note == "bosun.env says \"8am\": fix it with Change windows and connectors…")
  }

  @Test func aValueThatCannotBeReadIsUnknownNeverItsDefault() throws {
    let unreadable = state(try Fixtures.decode(SettingsView.self, "settings-get-unreadable"))
    for key in [SettingKey.startTime, SettingKey.days, SettingKey.startAtLogin] {
      #expect(unreadable.row(key) == SettingRow(editable: false, text: "unknown", note: nil))
    }
    #expect(state(nil).row(SettingKey.startTime) == SettingRow(editable: false, text: "unknown", note: nil))
    // a key this bosun does not print
    #expect(state(try view()).row("BOSUN_NOTHING").text == "unknown")
  }

  @Test func whileASetRunsNothingIsEditable() throws {
    let busy = SettingsState(view: try view(), openAtLogin: true, message: nil, busy: true)
    #expect(!busy.row(SettingKey.updateChannel).editable)
  }

  @Test func anUnreadableBosunEnvSaysWhyAtTheTop() throws {
    let unreadable = state(try Fixtures.decode(SettingsView.self, "settings-get-unreadable"))
    #expect(
      unreadable.problem
        == "bosun.env cannot be read (Permission denied), so its settings are unknown: "
        + "fix it with Change windows and connectors…")
    #expect(state(try view()).problem == nil)
    #expect(state(nil).problem == nil)
  }

  @Test func aSetsReplyBecomesTheMessage() throws {
    let refused = exited(2, try Fixtures.text("settings-set-refused"))
    #expect(settingsSetMessage(refused) == "BOSUN_START_TIME: '25:00' must be HH:MM, 24-hour")
    let noted = exited(0, try Fixtures.text("settings-set"))
    #expect(settingsSetMessage(noted)?.hasPrefix("another app's repeating wake was left as it is") == true)
    #expect(settingsSetMessage(exited(0, #"{"contract": 1, "changed": {}, "notes": []}"#)) == nil)
    #expect(settingsSetMessage(exited(1, #"{"contract": 1}"#, err: "it broke\n")) == "it broke")
    #expect(settingsSetMessage(exited(1, "", err: "bosun update runs\n")) == "bosun update runs")
    #expect(settingsSetMessage(CommandResult(outcome: .timedOut, stdout: "", stderr: "")) != nil)
  }

  @Test func theTerminalCommandIsTheSetWithoutJSON() {
    #expect(
      settingsSetArgs([SettingKey.startTime: "09:00", SettingKey.days: "MTWRF"])
        == ["settings", "set", "BOSUN_DAYS=MTWRF", "BOSUN_START_TIME=09:00"])
  }
}

@Suite struct ScheduleDraftTests {
  @Test func timesAreHoursAndMinutes() {
    #expect(TimeOfDay("08:30") == TimeOfDay(hour: 8, minute: 30))
    #expect(TimeOfDay("23:59")?.text == "23:59")
    #expect(TimeOfDay(hour: 7, minute: 5).text == "07:05")
    for bad in ["8:30", "24:00", "12:60", "ab:cd", "", "08:30:00", "-1:00"] {
      #expect(TimeOfDay(bad) == nil, "\(bad)")
    }
  }

  @Test func theDraftStartsFromWhatBosunReads() throws {
    let draft = ScheduleDraft(view: try view())
    #expect(draft.days == [true, true, true, true, true, false, false])
    #expect(draft.start == TimeOfDay(hour: 8, minute: 30))
    #expect(draft.until == TimeOfDay(hour: 20, minute: 0))
    #expect(draft.daysValue == "MTWRF")
    #expect(ScheduleDraft.dayLabels == ["M", "T", "W", "T", "F", "S", "S"])
  }

  @Test func theLineSaysTheScheduleBack() throws {
    var draft = ScheduleDraft(view: try view())
    #expect(draft.summary == "bosun starts at 08:30 on weekdays, and lets the Mac sleep after 20:00")
    draft.days = Array(repeating: true, count: 7)
    #expect(draft.summary == "bosun starts at 08:30 every day, and lets the Mac sleep after 20:00")
    draft.days = [false, false, false, false, false, true, true]
    #expect(draft.summary == "bosun starts at 08:30 on weekends, and lets the Mac sleep after 20:00")
    draft.days = [true, false, true, false, true, false, false]
    #expect(draft.summary == "bosun starts at 08:30 on Mon, Wed and Fri, and lets the Mac sleep after 20:00")
    draft.days = [false, false, false, true, false, false, false]
    #expect(draft.summary == "bosun starts at 08:30 on Thu, and lets the Mac sleep after 20:00")
    draft.days = Array(repeating: false, count: 7)
    #expect(draft.summary == nil)
    #expect(draft.problem == "Choose at least one day")
  }

  @Test func onlyWhatChangedIsSaved() throws {
    let read = try view()
    var draft = ScheduleDraft(view: read)
    #expect(draft.changes(from: read).isEmpty)
    #expect(!draft.canSave(read))
    draft.until = TimeOfDay(hour: 18, minute: 0)
    #expect(draft.changes(from: read) == [SettingKey.awakeUntil: "18:00"])
    draft.days[5] = true
    draft.start = TimeOfDay(hour: 9, minute: 0)
    #expect(
      draft.changes(from: read)
        == [SettingKey.awakeUntil: "18:00", SettingKey.days: "MTWRFS", SettingKey.startTime: "09:00"])
    #expect(draft.canSave(read))
    draft.days = Array(repeating: false, count: 7)
    #expect(!draft.canSave(read))
  }

  @Test func whatCannotBeShownIsNeverSavedOrSaidBack() throws {
    let invalid = try view(key: SettingKey.days, value: "XYZ", valid: false)
    var draft = ScheduleDraft(view: invalid)
    #expect(draft.days == Array(repeating: false, count: 7))
    #expect(draft.problem == nil)
    #expect(draft.summary == nil)
    draft.days[0] = true
    draft.until = TimeOfDay(hour: 18, minute: 0)
    #expect(draft.changes(from: invalid) == [SettingKey.awakeUntil: "18:00"])
    let unreadable = try Fixtures.decode(SettingsView.self, "settings-get-unreadable")
    let blank = ScheduleDraft(view: unreadable)
    #expect(blank.summary == nil)
    #expect(blank.changes(from: unreadable).isEmpty)
  }
}
