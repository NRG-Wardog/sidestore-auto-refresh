import SwiftUI
import Foundation

// LC_REFRESH_HISTORY_STORE_V1_BEGIN
/// Stable row identity is separate from display order: refresh events can be
/// prepended while the user is selecting or swiping an older entry.
struct LiveContainerRefreshHistoryEntry: Identifiable, Equatable {
    let id: String
    let values: [String: String]
}

/// The host scheduler writes history on MainActor too. These synchronous
/// read/modify/write operations cannot interleave with a host history append.
/// No network, timer, refresh request, or health-state mutation is involved.
@MainActor
enum LiveContainerRefreshHistoryStore {
    static let key = "liveContainerAutoRefreshHistory"
    static let didChange = Notification.Name("LiveContainerAutoRefreshHistoryChanged")

    private static func identified(_ entries: [[String: String]]) -> [[String: String]] {
        var used = Set<String>()
        return entries.map { entry in
            var value = entry
            if let id = value["id"], !id.isEmpty, used.insert(id).inserted {
                return value
            }
            var id = UUID().uuidString
            while !used.insert(id).inserted { id = UUID().uuidString }
            value["id"] = id
            return value
        }
    }

    private static func rows(_ entries: [[String: String]]) -> [LiveContainerRefreshHistoryEntry] {
        entries.compactMap { value in
            guard let id = value["id"] else { return nil }
            return LiveContainerRefreshHistoryEntry(id: id, values: value)
        }
    }

    static func entries(in defaults: UserDefaults) -> [LiveContainerRefreshHistoryEntry] {
        let previous = defaults.array(forKey: key) as? [[String: String]] ?? []
        let current = identified(previous)
        // Legacy and newly recorded events gain a persistent identity only
        // when this screen needs it. An unchanged read performs no write.
        if current != previous { defaults.set(current, forKey: key) }
        return rows(current)
    }

    static func delete(ids: Set<String>, in defaults: UserDefaults) {
        guard !ids.isEmpty else { return }
        // Never write the view's possibly stale array back over newer events.
        let previous = defaults.array(forKey: key) as? [[String: String]] ?? []
        let current = identified(previous).filter { !ids.contains($0["id"] ?? "") }
        guard current != previous else { return }
        defaults.set(current, forKey: key)
        NotificationCenter.default.post(name: didChange, object: nil)
    }

    static func clear(in defaults: UserDefaults) {
        guard defaults.object(forKey: key) != nil else { return }
        defaults.removeObject(forKey: key)
        NotificationCenter.default.post(name: didChange, object: nil)
    }
}
// LC_REFRESH_HISTORY_STORE_V1_END

@MainActor
struct LCEmbeddedSideStoreRefreshView: View {
    private let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard
    @Environment(\.layoutDirection) private var layoutDirection
    @State private var history: [LiveContainerRefreshHistoryEntry] = []
    @State private var isSelectingHistory = false
    @State private var selectedHistoryIDs: Set<String> = []
    @State private var showClearHistoryConfirmation = false
    @AppStorage("liveContainerAutoRefreshLastError", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var lastError = ""
    @AppStorage("liveContainerAutoRefreshHealthState", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var healthState = "UNKNOWN"
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
                Text("Refresh: \(healthState.replacingOccurrences(of: "_", with: " ").capitalized)")
                Text("Background execution remains best-effort. A scheduled request is not a completed refresh.")
                    .font(.caption).foregroundColor(.secondary)
                if !lastError.isEmpty {
                    Text(lastError).font(.caption).foregroundColor(.red)
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
            historySection
        }
        .navigationTitle("SideStore refresh")
        .onAppear { reloadHistory() }
        .onReceive(NotificationCenter.default.publisher(for: LiveContainerRefreshHistoryStore.didChange).receive(on: RunLoop.main)) { _ in reloadHistory() }
        .confirmationDialog("Clear all refresh history?", isPresented: $showClearHistoryConfirmation, titleVisibility: .visible) {
            Button("Clear All", role: .destructive) { clearHistory() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This deletes history entries only. Refresh settings and the current signing status are not changed.")
        }
    }

    private var historySection: some View {
        Section {
            if !history.isEmpty {
                HStack {
                    Button(isSelectingHistory ? "Done" : "Select") {
                        withAnimation {
                            isSelectingHistory.toggle()
                            if !isSelectingHistory { selectedHistoryIDs.removeAll() }
                        }
                    }
                    Spacer()
                    Button("Clear All", role: .destructive) { showClearHistoryConfirmation = true }
                }
                // Keep buttons independently tappable inside the same Form row.
                .buttonStyle(.borderless)
                if isSelectingHistory {
                    HStack {
                        Text("\(selectedHistoryIDs.count) selected").foregroundColor(.secondary)
                        Spacer()
                        Button("Delete Selected", role: .destructive, action: deleteSelectedHistory)
                            .disabled(selectedHistoryIDs.isEmpty)
                    }
                    .buttonStyle(.borderless)
                }
            }
            if history.isEmpty {
                Text("No refreshes recorded").foregroundColor(.secondary)
            }
            // The scheduler already caps retention at 50. Do not hide the older
            // 30 entries from selection while Clear All still deletes them.
            ForEach(history) { entry in historyRow(entry) }
        } header: {
            Text("History")
        } footer: {
            Text("Swipe right to reveal Delete, or use Select to delete several entries. Deleting history does not change refresh status or scheduled tasks.")
        }
    }

    @ViewBuilder
    private func historyRow(_ entry: LiveContainerRefreshHistoryEntry) -> some View {
        if isSelectingHistory {
            Button { toggleSelection(entry.id) } label: { historyContent(entry) }
                .buttonStyle(.plain)
                .accessibilityValue(selectedHistoryIDs.contains(entry.id) ? "Selected" : "Not selected")
                .accessibilityAddTraits(selectedHistoryIDs.contains(entry.id) ? [.isSelected] : [])
        } else {
            historyContent(entry)
                // Preserve a physical rightward gesture in LTR and RTL layouts.
                .swipeActions(edge: layoutDirection == .rightToLeft ? .trailing : .leading, allowsFullSwipe: false) {
                    Button(role: .destructive) { deleteHistoryEntry(id: entry.id) } label: {
                        Label("Delete", systemImage: "trash")
                    }
                }
                .accessibilityAction(named: Text("Delete")) { deleteHistoryEntry(id: entry.id) }
        }
    }

    private func historyContent(_ entry: LiveContainerRefreshHistoryEntry) -> some View {
        HStack(alignment: .top, spacing: 10) {
            if isSelectingHistory {
                Image(systemName: selectedHistoryIDs.contains(entry.id) ? "checkmark.circle.fill" : "circle")
                    .foregroundColor(selectedHistoryIDs.contains(entry.id) ? .accentColor : .secondary)
                    .font(.title3)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("\(entry.values["source"]?.capitalized ?? "Unknown") - \(entry.values["result"]?.capitalized ?? "Unknown")")
                Text(entry.values["date"] ?? "").font(.caption).foregroundColor(.secondary)
                if let detail = entry.values["detail"], !detail.isEmpty { Text(detail).font(.caption2).foregroundColor(.secondary) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .contentShape(Rectangle())
    }

    private func toggleSelection(_ id: String) {
        if selectedHistoryIDs.contains(id) { selectedHistoryIDs.remove(id) }
        else { selectedHistoryIDs.insert(id) }
    }

    private func notifyScheduleChanged() {
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshScheduleChanged"), object: nil)
    }

    private func notifyManualRefresh() {
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil)
    }

    private func deleteHistoryEntry(id: String) {
        withAnimation {
            LiveContainerRefreshHistoryStore.delete(ids: [id], in: defaults)
            reloadHistory()
        }
    }

    private func deleteSelectedHistory() {
        withAnimation {
            LiveContainerRefreshHistoryStore.delete(ids: selectedHistoryIDs, in: defaults)
            selectedHistoryIDs.removeAll()
            isSelectingHistory = false
            reloadHistory()
        }
    }

    private func clearHistory() {
        withAnimation {
            LiveContainerRefreshHistoryStore.clear(in: defaults)
            selectedHistoryIDs.removeAll()
            isSelectingHistory = false
            reloadHistory()
        }
    }

    private func reloadHistory() {
        history = LiveContainerRefreshHistoryStore.entries(in: defaults)
        selectedHistoryIDs.formIntersection(Set(history.map(\.id)))
        if history.isEmpty { isSelectingHistory = false }
    }
}
