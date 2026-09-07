import SwiftUI

struct LCEmbeddedSideStoreRefreshView: View {
    private let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard
    @State private var history: [[String: String]] = []
    @AppStorage("liveContainerAutoRefreshEnabled", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var enabled = false
    @AppStorage("liveContainerAutoRefreshFrequency", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var frequency = "interval"
    @AppStorage("liveContainerAutoRefreshWeekday", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var weekday = 2
    @AppStorage("liveContainerAutoRefreshMinutes", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var minutes = 600

    private var time: Binding<Date> {
        Binding(get: {
            Calendar.autoupdatingCurrent.date(bySettingHour: minutes / 60, minute: minutes % 60, second: 0, of: Date()) ?? Date()
        }, set: { value in
            let parts = Calendar.autoupdatingCurrent.dateComponents([.hour, .minute], from: value)
            minutes = (parts.hour ?? 10) * 60 + (parts.minute ?? 0)
            notifyScheduleChanged()
        })
    }

    var body: some View {
        Form {
            Section("Status") {
                Text("Auto Refresh: \(enabled ? "Enabled" : "Disabled")")
                let strategy = defaults.string(forKey: "liveContainerAutoRefreshStrategy") ?? "foreground_recovery_only"
                let protection = strategy == "native_full" ? "Enhanced" :
                    (strategy == "native_without_alarmkit" ? "Standard" : "Limited")
                Text("Protection: \(protection)")
                Text("Background execution remains best-effort. A scheduled request is not a completed refresh.")
                    .font(.caption).foregroundColor(.secondary)
                if let error = defaults.string(forKey: "liveContainerAutoRefreshLastError"), !error.isEmpty {
                    Text(error).font(.caption).foregroundColor(.red)
                }
            }
            Section {
                Toggle("Scheduled refresh", isOn: Binding(get: { enabled }, set: { enabled = $0; notifyScheduleChanged() }))
                Button("Refresh SideStore now", action: notifyManualRefresh)
                Picker("Frequency", selection: Binding(get: { frequency }, set: { frequency = $0; notifyScheduleChanged() })) {
                    Text("Every six hours").tag("interval")
                    Text("Daily").tag("daily")
                    Text("Weekly").tag("weekly")
                }.disabled(!enabled)
                if frequency == "weekly" {
                    Picker("Weekday", selection: Binding(get: { weekday }, set: { weekday = $0; notifyScheduleChanged() })) {
                        ForEach(1...7, id: \.self) { day in Text(Calendar.autoupdatingCurrent.weekdaySymbols[day - 1]).tag(day) }
                    }.disabled(!enabled)
                    Text("A weekly schedule can be too late for free-account signing. Prefer daily refresh.")
                        .font(.caption).foregroundColor(.secondary)
                }
                if frequency != "interval" {
                    DatePicker("Target time (local)", selection: time, displayedComponents: .hourAndMinute).disabled(!enabled)
                }
                Text("Refresh can start before the target time to allow for iOS scheduling delays.")
                    .font(.caption).foregroundColor(.secondary)
            }
            Section("Warnings") {
                Button("Allow refresh notifications") {
                    Task { @MainActor in await LiveContainerAutoRefreshScheduler.requestNotificationPermission(); LiveContainerAutoRefreshScheduler.schedule() }
                }
                if #available(iOS 26.1, *) {
                    Button("Enable optional deadline alarm") {
                        Task { @MainActor in await LiveContainerAutoRefreshAlarmProvider.requestAuthorization() }
                    }
                }
                Text("Warnings require permission. A deadline warning asks you to check an unconfirmed refresh; it cannot diagnose a task that never ran.")
                    .font(.caption).foregroundColor(.secondary)
            }
            if let result = defaults.string(forKey: "liveContainerAutoRefreshLastResult") {
                Section("Last result") {
                    Text(result.replacingOccurrences(of: "_", with: " ").capitalized)
                    if let date = defaults.object(forKey: "liveContainerAutoRefreshLastDate") as? Date {
                        Text(date.formatted(date: .abbreviated, time: .shortened)).font(.caption)
                    }
                }
            }
            Section("History") {
                if history.isEmpty { Text("No refreshes recorded").foregroundColor(.secondary) }
                ForEach(Array(history.prefix(20).enumerated()), id: \.offset) { _, entry in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(entry["source"]?.capitalized ?? "Unknown") - \(entry["result"]?.capitalized ?? "Unknown")")
                        Text(entry["date"] ?? "").font(.caption).foregroundColor(.secondary)
                        if let detail = entry["detail"], !detail.isEmpty { Text(detail).font(.caption2).foregroundColor(.secondary) }
                    }
                }
            }
        }
        .navigationTitle("SideStore refresh")
        .onAppear { reloadHistory() }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("LiveContainerAutoRefreshHistoryChanged")).receive(on: RunLoop.main)) { _ in reloadHistory() }
    }

    private func notifyScheduleChanged() {
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshScheduleChanged"), object: nil)
    }
    private func notifyManualRefresh() {
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil)
    }
    private func reloadHistory() { history = defaults.array(forKey: "liveContainerAutoRefreshHistory") as? [[String: String]] ?? [] }
}
