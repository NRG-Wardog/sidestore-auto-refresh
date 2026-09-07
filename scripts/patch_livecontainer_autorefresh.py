#!/usr/bin/env python3
"""Integrate host-owned scheduled refresh into pinned LiveContainer sources.

The embedded SideStore process cannot own BGTaskScheduler registration: iOS
registers background tasks against the containing LiveContainer application.
This patch keeps the existing LiveProcess/XPC refresh bridge and moves the
task registration, schedule UI, and persisted result state into the host.
"""

from __future__ import annotations

from pathlib import Path
import sys


TASK_ID = "com.kdt.livecontainer.sidestore.automatic-refresh"
MARKER = "[LIVE_CONTAINER_REFRESH] REGISTER_PASS"


def die(message: str) -> None:
    raise SystemExit(f"patch_livecontainer_autorefresh: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0:
        die(f"missing {label}")
    if count > 1:
        die(f"ambiguous {label}: {count} matches")
    return text.replace(old, new, 1)


BRIDGE = r'''

/// Narrow host-facing bridge. The refresh still executes in the embedded
/// SideStore through the existing LiveProcess/XPC path.
public enum LiveContainerRefreshBridge {
    public static func refreshAllApps() async throws {
        try await RefreshHandler.shared.startRefresh(
            identifier: "LiveContainerScheduledRefresh",
            mangledName: "16SideStoreSupport20RefreshAllAppsIntentV"
        )
    }
}
'''


HOST_SCHEDULER = rf'''

enum LiveContainerAutoRefreshScheduler {{
    // The initial signer rewrites the permitted identifiers from the original
    // host ID. Build the runtime IDs from that signed host ID as well.
    static let taskIdentifier = "\(Bundle.main.bundleIdentifier ?? \"com.kdt.livecontainer\").sidestore.automatic-refresh"
    static let watchdogIdentifier = "\(taskIdentifier).watchdog"
    static let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard
    static let enabledKey = "liveContainerAutoRefreshEnabled"
    static let frequencyKey = "liveContainerAutoRefreshFrequency"
    static let weekdayKey = "liveContainerAutoRefreshWeekday"
    static let minutesKey = "liveContainerAutoRefreshMinutes"
    static let lastResultKey = "liveContainerAutoRefreshLastResult"
    static let lastDateKey = "liveContainerAutoRefreshLastDate"
    static let historyKey = "liveContainerAutoRefreshHistory"
    static let earliestEligibleKey = "liveContainerAutoRefreshEarliestEligibleAt"
    static let deadlineKey = "liveContainerAutoRefreshTargetDeadline"
    static let nextRetryKey = "liveContainerAutoRefreshNextRetryAt"
    static let lastTaskKey = "liveContainerAutoRefreshLastTaskTrigger"
    static let lastAttemptKey = "liveContainerAutoRefreshLastAttempt"
    static let activeRunKey = "liveContainerAutoRefreshActiveRunID"
    static let retryCountKey = "liveContainerAutoRefreshRetryCount"
    static let hostHandoffKey = "liveContainerAutoRefreshHostHandoff"
    static let hostHandoffRunKey = "liveContainerAutoRefreshHostHandoffRunID"
    static let hostHandoffStartedKey = "liveContainerAutoRefreshHostHandoffStartedAt"
    static let hostPreviousExpirationKey = "liveContainerAutoRefreshHostPreviousExpiration"
    static let verificationKey = "liveContainerAutoRefreshVerification"
    static let expectedRunKey = "liveContainerAutoRefreshExpectedRunID"
    static let hostVerifiedKey = "liveContainerAutoRefreshHostVerifiedAfterRelaunch"
    static let strategyKey = "liveContainerAutoRefreshStrategy"
    static let alarmScheduledKey = "liveContainerAutoRefreshAlarmScheduled"
    static let alarmDeadlineKey = "liveContainerAutoRefreshAlarmDeadline"
    static let healthStateKey = "liveContainerAutoRefreshHealthState"
    static let lastErrorKey = "liveContainerAutoRefreshLastError"
    static let lastSuccessfulKey = "liveContainerAutoRefreshLastSuccessfulRefresh"
    static let leadTime: TimeInterval = 60 * 60
    static let coalescingWindow: TimeInterval = 60
    private static let lock = NSLock()

    private static func diagnosticDate(_ date: Date, timeZone: TimeZone) -> String {{
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = timeZone
        return formatter.string(from: date)
    }}

    private static func cancelDeadlineAlarm() {{
        if #available(iOS 26.1, *) {{
            LiveContainerAutoRefreshAlarmProvider.cancelIfAvailable()
        }}
        defaults.set(false, forKey: alarmScheduledKey)
    }}

    private static func record(source: String, result: String, detail: String = "") {{
        let entry: [String: String] = [
            "date": ISO8601DateFormatter().string(from: Date()),
            "source": source,
            "result": result,
            "detail": String(detail.prefix(300))
        ]
        var history = defaults.array(forKey: historyKey) as? [[String: String]] ?? []
        history.insert(entry, at: 0)
        defaults.set(Array(history.prefix(50)), forKey: historyKey)
        defaults.set(result, forKey: lastResultKey)
        defaults.set(Date(), forKey: lastDateKey)
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshHistoryChanged"), object: nil)
    }}

    private static func compactWorkIsDue(now: Date) -> Bool {{
        if defaults.object(forKey: earliestEligibleKey) == nil {{ return true }}
        if defaults.bool(forKey: hostHandoffKey) {{ return false }}
        if let retry = defaults.object(forKey: nextRetryKey) as? Date, retry > now {{ return false }}
        if let eligible = defaults.object(forKey: earliestEligibleKey) as? Date, eligible > now {{
            if let deadline = defaults.object(forKey: deadlineKey) as? Date {{ return deadline <= now }}
            return false
        }}
        return true
    }}

    private static func beginRun(source: String) -> UUID? {{
        lock.lock()
        defer {{ lock.unlock() }}
        if defaults.string(forKey: activeRunKey) != nil {{ return nil }}
        if let last = defaults.object(forKey: lastAttemptKey) as? Date,
           Date().timeIntervalSince(last) < coalescingWindow {{ return nil }}
        let runID = UUID()
        defaults.set(runID.uuidString, forKey: activeRunKey)
        defaults.set(Date(), forKey: lastAttemptKey)
        defaults.set(Date(), forKey: lastTaskKey)
        defaults.set("REFRESH_IN_PROGRESS", forKey: healthStateKey)
        print("[LIVE_CONTAINER_REFRESH] RUN_BEGIN source=\\(source) run_id=\\(runID.uuidString)")
        return runID
    }}

    private static func endRun() {{
        lock.lock()
        defaults.removeObject(forKey: activeRunKey)
        lock.unlock()
    }}

    private static func performRefresh(runID: UUID) async throws {{
        print("[LIVE_CONTAINER_REFRESH] REFRESH_ATTEMPT_STARTED run_id=\\(runID.uuidString)")
        defaults.set(runID.uuidString, forKey: expectedRunKey)
        try await LiveContainerRefreshBridge.refreshAllApps()
        print("[LIVE_CONTAINER_REFRESH] REFRESH_PIPELINE_RETURNED run_id=\\(runID.uuidString)")
    }}

    private static func verifyRefreshManifest() -> (verified: Bool, hostHandoff: Bool, reason: String) {{
        guard let manifest = defaults.dictionary(forKey: verificationKey),
              let results = manifest["results"] as? [[String: Any]], !results.isEmpty else {{
            return (false, defaults.bool(forKey: hostHandoffKey), "verification_manifest_missing")
        }}
        guard manifest["run_id"] as? String == defaults.string(forKey: expectedRunKey) else {{
            return (false, defaults.bool(forKey: hostHandoffKey), "verification_manifest_run_mismatch")
        }}
        let failures = results.filter {{ ($0["success"] as? Bool) != true }}
        let hostHandoff = defaults.bool(forKey: hostHandoffKey)
        if !failures.isEmpty {{ return (false, hostHandoff, "verification_manifest_contains_failure") }}
        if hostHandoff {{ return (false, true, "host_handoff_awaiting_relaunch") }}
        return (true, false, "verified_installed_app_records")
    }}

    private static func verifyPendingHostHandoff() {{
        guard defaults.bool(forKey: hostHandoffKey) else {{ return }}
        let previous = defaults.object(forKey: hostPreviousExpirationKey) as? Date
        let manifest = defaults.dictionary(forKey: verificationKey)
        guard manifest?["run_id"] as? String == defaults.string(forKey: hostHandoffRunKey) else {{
            defaults.set("HOST_REFRESH_FAILED", forKey: healthStateKey)
            defaults.set("verification_manifest_run_mismatch", forKey: lastErrorKey)
            print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_FAILED reason=verification_manifest_run_mismatch")
            return
        }}
        let results = manifest?["results"] as? [[String: Any]] ?? []
        let hostResult = results.first {{ ($0["bundle_id"] as? String) == "com.kdt.livecontainer" }}
        let expiration = hostResult?["expiration_date"] as? Date
        if let previous, let expiration, expiration > previous {{
            defaults.set(true, forKey: hostVerifiedKey)
            defaults.set("HOST_REFRESH_VERIFIED", forKey: lastResultKey)
            defaults.removeObject(forKey: hostHandoffKey)
            print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_VERIFIED expiration_advanced=true")
            cancelDeadlineAlarm()
        }} else {{
            defaults.set("HOST_REFRESH_FAILED", forKey: lastResultKey)
            defaults.set("HOST_REFRESH_FAILED", forKey: healthStateKey)
            defaults.set("expiration_not_verified", forKey: lastErrorKey)
            print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_FAILED reason=expiration_not_verified")
        }}
    }}

    private static func verifyGuestSignatures() -> Bool {{
        let guests = DataManager.shared.model.apps + DataManager.shared.model.hiddenApps
        var allValid = true
        for guest in guests {{
            guard let path = guest.appInfo.bundlePath(),
                  let executable = Bundle(path: path)?.executableURL else {{
                allValid = false
                print("[LIVE_CONTAINER_REFRESH] GUEST_SIGNATURE_INVALID bundle_id=\\(guest.appInfo.bundleIdentifier()) reason=executable_missing")
                continue
            }}
            let valid = executable.path.withCString {{ checkCodeSignature($0) }}
            print("[LIVE_CONTAINER_REFRESH] GUEST_SIGNATURE_\\(valid ? \"VALID\" : \"INVALID\") bundle_id=\\(guest.appInfo.bundleIdentifier())")
            if !valid {{ allValid = false }}
        }}
        return allValid
    }}

    private static func execute(source: String, task: BGTask? = nil) async {{
        let started = Date()
        let timeZone = TimeZone.autoupdatingCurrent
        let deadline = defaults.object(forKey: deadlineKey) as? Date
        let earliest = deadline?.addingTimeInterval(-leadTime)
        print("[LIVE_CONTAINER_REFRESH] TASK_TRIGGERED source=\\(source) task_actual_start_local=\\(diagnosticDate(started, timeZone: timeZone)) task_actual_start_utc=\\(diagnosticDate(started, timeZone: TimeZone(secondsFromGMT: 0) ?? timeZone)) delay_from_earliest_begin=\\(earliest.map {{ String(format: \"%.0f\", started.timeIntervalSince($0)) }} ?? \"unknown\") timezone=\\(timeZone.identifier)")
        guard compactWorkIsDue(now: started) else {{
            print("[LIVE_CONTAINER_REFRESH] NO_OP source=\\(source)")
            task?.setTaskCompleted(success: true)
            return
        }}
        if source == "bgapprefresh" {{
            print("[LIVE_CONTAINER_REFRESH] WATCHDOG_DUE action=resubmit_bgprocessing")
            schedule()
            task?.setTaskCompleted(success: true)
            return
        }}
        guard let runID = beginRun(source: source) else {{
            print("[LIVE_CONTAINER_REFRESH] RUN_COALESCED source=\\(source)")
            task?.setTaskCompleted(success: true)
            return
        }}
        defer {{ endRun() }}
        do {{
            try await performRefresh(runID: runID)
            let verification = verifyRefreshManifest()
            if verification.hostHandoff {{
                defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
                record(source: source, result: "host_handoff_awaiting_relaunch", detail: verification.reason)
                print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_AWAITING_RELAUNCH run_id=\\(runID.uuidString)")
                task?.setTaskCompleted(success: true)
            }} else if verification.verified && verifyGuestSignatures() {{
                defaults.set(Date().addingTimeInterval(6 * 60 * 60), forKey: earliestEligibleKey)
                defaults.removeObject(forKey: nextRetryKey)
                defaults.set(0, forKey: retryCountKey)
                defaults.set("REFRESH_SUCCEEDED", forKey: healthStateKey)
                defaults.set(Date(), forKey: lastSuccessfulKey)
                defaults.removeObject(forKey: lastErrorKey)
                record(source: source, result: "verified", detail: verification.reason)
                print("[LIVE_CONTAINER_REFRESH] REFRESH_RESULT run_id=\\(runID.uuidString) success=true verified=true")
                cancelDeadlineAlarm()
                task?.setTaskCompleted(success: true)
            }} else if verification.verified {{
                defaults.set("GUEST_SIGNATURE_INVALID", forKey: healthStateKey)
                defaults.set("guest_signature_invalid", forKey: lastErrorKey)
                record(source: source, result: "guest_signature_invalid", detail: "A LiveContainer guest signature could not be validated.")
                task?.setTaskCompleted(success: false)
            }} else {{
                throw NSError(domain: "LiveContainerRefresh", code: 1001,
                    userInfo: [NSLocalizedDescriptionKey: verification.reason])
            }}
        }} catch {{
            let count = defaults.integer(forKey: retryCountKey) + 1
            defaults.set(count, forKey: retryCountKey)
            let delays: [TimeInterval] = [5 * 60, 20 * 60, 60 * 60]
            if count <= delays.count {{ defaults.set(Date().addingTimeInterval(delays[count - 1]), forKey: nextRetryKey) }}
            defaults.set("REFRESH_FAILED", forKey: healthStateKey)
            defaults.set(error.localizedDescription, forKey: lastErrorKey)
            record(source: source, result: "failure", detail: error.localizedDescription)
            print("[LIVE_CONTAINER_REFRESH] REFRESH_RESULT run_id=\\(runID.uuidString) success=false verified=false error_code=\\((error as NSError).code) error_domain=\\((error as NSError).domain) stage=refresh error=\\(error.localizedDescription)")
            task?.setTaskCompleted(success: false)
        }}
    }}

    static func register() {{
        BGTaskScheduler.shared.register(forTaskWithIdentifier: taskIdentifier, using: nil) {{ task in
            guard let task = task as? BGProcessingTask else {{ return }}
            let operation = Task {{ await execute(source: "bgprocessing", task: task) }}
            task.expirationHandler = {{
                operation.cancel()
                record(source: "bgprocessing", result: "expired", detail: "BGTask expiration")
                print("[LIVE_CONTAINER_REFRESH] TASK_EXPIRED")
                task.setTaskCompleted(success: false)
            }}
        }}
        if #available(iOS 13.0, *) {{
            BGTaskScheduler.shared.register(forTaskWithIdentifier: watchdogIdentifier, using: nil) {{ task in
                guard let task = task as? BGAppRefreshTask else {{ return }}
                let operation = Task {{ await execute(source: "bgapprefresh", task: task) }}
                task.expirationHandler = {{ operation.cancel(); task.setTaskCompleted(success: false) }}
            }}
        }}
        print("{MARKER}")
    }}

    static func requestRefreshNow() {{
        Task {{ await execute(source: "alarm_action") }}
    }}

    static func runNow() {{
        Task {{ await execute(source: "manual") }}
    }}

    static func recoverAfterLaunchOrResume() {{
        verifyPendingHostHandoff()
        guard defaults.object(forKey: earliestEligibleKey) != nil,
              compactWorkIsDue(now: Date()) else {{ return }}
        print("[LIVE_CONTAINER_REFRESH] RECOVERY_DUE source=launch_or_resume")
        Task {{ await execute(source: "launch_or_resume") }}
    }}

    static func schedule() {{
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: taskIdentifier)
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: watchdogIdentifier)
        guard defaults.bool(forKey: enabledKey) else {{
            print("[LIVE_CONTAINER_REFRESH] SCHEDULE_DISABLED")
            return
        }}
        let now = Date()
        let deadline: Date
        if let saved = defaults.object(forKey: deadlineKey) as? Date, saved > now {{
            deadline = saved
        }} else {{
            deadline = nextDate(after: now)
        }}
        defaults.set(deadline, forKey: deadlineKey)
        defaults.set("native_without_alarmkit", forKey: strategyKey)
        let earliest = max(now, deadline.addingTimeInterval(-leadTime))
        let request = BGProcessingTaskRequest(identifier: taskIdentifier)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        request.earliestBeginDate = earliest
        do {{
            try BGTaskScheduler.shared.submit(request)
            print("[LIVE_CONTAINER_REFRESH] SCHEDULE_PASS target_deadline=\\(deadline.timeIntervalSince1970) earliest_begin=\\(earliest.timeIntervalSince1970)")
        }} catch {{
            record(source: "scheduler", result: "bgprocessing_submit_failed", detail: error.localizedDescription)
            print("[LIVE_CONTAINER_REFRESH] SCHEDULE_FAIL mechanism=bgprocessing error_code=\\((error as NSError).code) error_domain=\\((error as NSError).domain) error=\\(error.localizedDescription)")
        }}
        let watchdog = BGAppRefreshTaskRequest(identifier: watchdogIdentifier)
        watchdog.earliestBeginDate = deadline
        do {{
            try BGTaskScheduler.shared.submit(watchdog)
            print("[LIVE_CONTAINER_REFRESH] WATCHDOG_SCHEDULE_PASS target_deadline=\\(deadline.timeIntervalSince1970)")
        }} catch {{
            record(source: "scheduler", result: "bgapprefresh_submit_failed", detail: error.localizedDescription)
            print("[LIVE_CONTAINER_REFRESH] WATCHDOG_SCHEDULE_FAIL error_code=\\((error as NSError).code) error_domain=\\((error as NSError).domain) error=\\(error.localizedDescription)")
        }}
        if #available(iOS 26.1, *) {{
            Task {{ await LiveContainerAutoRefreshAlarmProvider.scheduleIfAvailable(deadline: deadline) }}
        }} else {{
            defaults.set("legacy_background", forKey: strategyKey)
            print("[LIVE_CONTAINER_REFRESH] STRATEGY_SELECTED value=legacy_background")
        }}
    }}

    private static func nextDate(after now: Date) -> Date {{
        let frequency = defaults.string(forKey: frequencyKey) ?? "interval"
        if frequency == "interval" {{ return now.addingTimeInterval(6 * 60 * 60) }}
        let minutes = max(0, min(1439, defaults.object(forKey: minutesKey) as? Int ?? 600))
        var components = DateComponents(hour: minutes / 60, minute: minutes % 60, second: 0)
        if frequency == "weekly" {{ components.weekday = max(1, min(7, defaults.object(forKey: weekdayKey) as? Int ?? 2)) }}
        return Calendar.autoupdatingCurrent.nextDate(after: now, matching: components,
            matchingPolicy: .nextTime, repeatedTimePolicy: .first) ?? now.addingTimeInterval(6 * 60 * 60)
    }}
}}
'''


ALARM_PROVIDER = r'''
#if canImport(AlarmKit)
import AlarmKit
import AppIntents
import SwiftUI

@available(iOS 26.1, *)
private struct LiveContainerRefreshAlarmMetadata: AlarmMetadata {}

@available(iOS 26.1, *)
private struct LiveContainerRefreshAlarmIntent: LiveActivityIntent {
    static var title: LocalizedStringResource { "Refresh LiveContainer now" }

    func perform() async throws -> some IntentResult {
        LiveContainerAutoRefreshScheduler.requestRefreshNow()
        return .result()
    }
}

@available(iOS 26.1, *)
enum LiveContainerAutoRefreshAlarmProvider {
    private static let alarmID = UUID(uuidString: "7B0A0E8E-0C90-4E33-9BA9-6DD38D8D5E2E")!

    static func scheduleIfAvailable(deadline: Date) async {
        guard AlarmManager.shared.authorizationState == .authorized else {
            LiveContainerAutoRefreshScheduler.defaults.set("native_without_alarmkit", forKey: "liveContainerAutoRefreshStrategy")
            LiveContainerAutoRefreshScheduler.defaults.set(false, forKey: "liveContainerAutoRefreshAlarmScheduled")
            print("[LIVE_CONTAINER_REFRESH] ALARM_UNAVAILABLE reason=not_authorized")
            return
        }
        if LiveContainerAutoRefreshScheduler.defaults.bool(forKey: "liveContainerAutoRefreshAlarmScheduled"),
           let existing = LiveContainerAutoRefreshScheduler.defaults.object(forKey: "liveContainerAutoRefreshAlarmDeadline") as? Date,
           abs(existing.timeIntervalSince(deadline)) < 1 {
            return
        }
        let alert = AlarmPresentation.Alert(
            title: "Automatic refresh deadline",
            secondaryButton: AlarmButton(text: "Refresh Now", textColor: .white, systemImageName: "arrow.clockwise"),
            secondaryButtonBehavior: .custom
        )
        let attributes: AlarmAttributes<LiveContainerRefreshAlarmMetadata> = AlarmAttributes(
            presentation: AlarmPresentation(alert: alert),
            metadata: LiveContainerRefreshAlarmMetadata(),
            tintColor: Color.orange
        )
        let configuration: AlarmManager.AlarmConfiguration<LiveContainerRefreshAlarmMetadata> = AlarmManager.AlarmConfiguration.alarm(
            schedule: .fixed(deadline),
            attributes: attributes,
            stopIntent: nil,
            secondaryIntent: LiveContainerRefreshAlarmIntent(),
            sound: .default
        )
        do {
            _ = try await AlarmManager.shared.schedule(id: alarmID, configuration: configuration)
            LiveContainerAutoRefreshScheduler.defaults.set(alarmID.uuidString, forKey: "liveContainerAutoRefreshAlarmID")
            LiveContainerAutoRefreshScheduler.defaults.set(deadline, forKey: "liveContainerAutoRefreshAlarmDeadline")
            LiveContainerAutoRefreshScheduler.defaults.set("native_full", forKey: "liveContainerAutoRefreshStrategy")
            LiveContainerAutoRefreshScheduler.defaults.set(true, forKey: "liveContainerAutoRefreshAlarmScheduled")
            print("[LIVE_CONTAINER_REFRESH] STRATEGY_SELECTED value=native_full")
            print("[LIVE_CONTAINER_REFRESH] ALARM_SCHEDULE_PASS")
        } catch {
            print("[LIVE_CONTAINER_REFRESH] ALARM_SCHEDULE_FAIL error_code=\((error as NSError).code) error_domain=\((error as NSError).domain) error=\(error.localizedDescription)")
        }
    }

    static func cancelIfAvailable() {
        try? AlarmManager.shared.cancel(id: alarmID)
        LiveContainerAutoRefreshScheduler.defaults.removeObject(forKey: "liveContainerAutoRefreshAlarmDeadline")
    }
}
#else
enum LiveContainerAutoRefreshAlarmProvider {
    static func scheduleIfAvailable(deadline: Date) async {}
    static func cancelIfAvailable() {}
}
#endif
'''


SETTINGS_VIEW = r'''
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
            let calendar = Calendar.autoupdatingCurrent
            return calendar.date(bySettingHour: minutes / 60, minute: minutes % 60, second: 0, of: Date()) ?? Date()
        }, set: { value in
            let parts = Calendar.autoupdatingCurrent.dateComponents([.hour, .minute], from: value)
            minutes = (parts.hour ?? 10) * 60 + (parts.minute ?? 0)
            notifyScheduleChanged()
        })
    }

    var body: some View {
        Form {
            Section("Status") {
                Text("Auto Refresh: \(enabled ? "Active" : "Inactive")")
                let strategy = defaults.string(forKey: "liveContainerAutoRefreshStrategy") ?? "legacy_background"
                let protection = strategy == "native_full" ? "Enhanced" : (strategy == "legacy_background" ? "Limited" : "Standard")
                Text("Protection: \(protection)")
                if protection == "Enhanced" {
                    Text("Automatic background refresh + deadline protection")
                        .font(.caption).foregroundColor(.secondary)
                } else if protection == "Standard" {
                    Text("Automatic background refresh is active. Exact deadline alerts are unavailable.")
                        .font(.caption).foregroundColor(.secondary)
                } else {
                    Text("Refresh will be attempted whenever LiveContainer becomes active.")
                        .font(.caption).foregroundColor(.secondary)
                }
            }
            Section {
                Toggle("Scheduled refresh", isOn: Binding(get: { enabled }, set: {
                    enabled = $0
                    notifyScheduleChanged()
                }))
                Button("Refresh SideStore now", action: notifyManualRefresh)
                Picker("Frequency", selection: Binding(get: { frequency }, set: {
                    frequency = $0
                    notifyScheduleChanged()
                })) {
                    Text("Every six hours").tag("interval")
                    Text("Daily").tag("daily")
                    Text("Weekly").tag("weekly")
                }.disabled(!enabled)
                if frequency == "weekly" {
                    Picker("Weekday", selection: Binding(get: { weekday }, set: {
                        weekday = $0
                        notifyScheduleChanged()
                    })) {
                        ForEach(1...7, id: \.self) { day in
                            Text(Calendar.autoupdatingCurrent.weekdaySymbols[day - 1]).tag(day)
                        }
                    }.disabled(!enabled)
                }
                if frequency != "interval" {
                    DatePicker("Preferred time (local)", selection: time, displayedComponents: .hourAndMinute)
                        .disabled(!enabled)
                }
                Text("The host app owns this schedule. iOS may start it later than the requested time.")
                    .font(.caption).foregroundColor(.secondary)
            }
            if let result = defaults.string(forKey: "liveContainerAutoRefreshLastResult") {
                Section("Last result") {
                    Text(result.capitalized)
                    if let date = defaults.object(forKey: "liveContainerAutoRefreshLastDate") as? Date {
                        Text(date.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundColor(.secondary)
                    }
                }
            }
            Section("History") {
                if history.isEmpty {
                    Text("No refreshes recorded")
                        .foregroundColor(.secondary)
                } else {
                    ForEach(Array(history.prefix(20).enumerated()), id: \.offset) { _, entry in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(entry["source"]?.capitalized ?? "Unknown") - \(entry["result"]?.capitalized ?? "Unknown")")
                            Text(entry["date"] ?? "")
                                .font(.caption)
                                .foregroundColor(.secondary)
                            if let detail = entry["detail"], !detail.isEmpty {
                                Text(detail).font(.caption2).foregroundColor(.secondary)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("SideStore refresh")
        .onAppear { reloadHistory() }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("LiveContainerAutoRefreshHistoryChanged"))) { _ in
            reloadHistory()
        }
    }

    private func notifyScheduleChanged() {
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshScheduleChanged"), object: nil)
    }

    private func notifyManualRefresh() {
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil)
    }

    private func reloadHistory() {
        history = defaults.array(forKey: "liveContainerAutoRefreshHistory") as? [[String: String]] ?? []
    }
}
'''


def patch_support(root: Path) -> None:
    path = root / "SideStoreSupport" / "SideStore.swift"
    text = path.read_text(encoding="utf-8")
    if "LiveContainerRefreshBridge" not in text:
        text = replace_once(text, "\nclass RefreshHandler: NSObject, RefreshServer {", BRIDGE + "\nclass RefreshHandler: NSObject, RefreshServer {", "refresh bridge insertion")
        path.write_text(text, encoding="utf-8")


def patch_host_delegate(root: Path) -> None:
    path = root / "LiveContainerSwiftUI" / "App" / "AppDelegate.swift"
    text = path.read_text(encoding="utf-8")
    if "import BackgroundTasks" not in text:
        text = replace_once(text, "import Intents\n", "import Intents\nimport BackgroundTasks\nimport SideStoreSupport\n", "host imports")
    if "LiveContainerAutoRefreshScheduler.register()" not in text:
        text = replace_once(text, "        application.shortcutItems = nil\n", "        application.shortcutItems = nil\n        LiveContainerAutoRefreshScheduler.register()\n        LiveContainerAutoRefreshScheduler.schedule()\n        LiveContainerAutoRefreshScheduler.recoverAfterLaunchOrResume()\n        NotificationCenter.default.addObserver(forName: Notification.Name(\"LiveContainerAutoRefreshScheduleChanged\"), object: nil, queue: .main) { _ in\n            LiveContainerAutoRefreshScheduler.schedule()\n        }\n        NotificationCenter.default.addObserver(forName: Notification.Name(\"LiveContainerAutoRefreshRunNow\"), object: nil, queue: .main) { _ in\n            LiveContainerAutoRefreshScheduler.runNow()\n        }\n", "host scheduler startup")
        text = replace_once(text, "    func application(_ application: UIApplication, configurationForConnecting", "    func applicationDidEnterBackground(_ application: UIApplication) {\n        LiveContainerAutoRefreshScheduler.schedule()\n    }\n\n    func applicationWillEnterForeground(_ application: UIApplication) {\n        LiveContainerAutoRefreshScheduler.recoverAfterLaunchOrResume()\n    }\n\n    func application(_ application: UIApplication, configurationForConnecting", "host background reschedule")
        text = replace_once(text, "class SceneDelegate:", HOST_SCHEDULER + "\nclass SceneDelegate:", "host scheduler implementation")
    path.write_text(text, encoding="utf-8")


def patch_host_info(root: Path) -> None:
    path = root / "LiveContainer" / "Info.plist"
    text = path.read_text(encoding="utf-8")
    key = "<key>BGTaskSchedulerPermittedIdentifiers</key>"
    if TASK_ID not in text or f"{TASK_ID}.watchdog" not in text:
        insertion = f"\t{key}\n\t<array>\n\t\t<string>{TASK_ID}</string>\n\t\t<string>{TASK_ID}.watchdog</string>\n\t</array>\n"
        closing = "</dict>\n</plist>"
        if key in text:
            start = text.index(key)
            end = text.index("</array>", start) + len("</array>\n")
            text = text[:start] + insertion + text[end:]
        else:
            text = replace_once(text, closing, insertion + closing, "host background task plist insertion")
    if "<string>processing</string>" not in text:
        if "\t<key>UIBackgroundModes</key>\n\t<array>\n" in text:
            text = replace_once(
                text,
                "\t<key>UIBackgroundModes</key>\n\t<array>\n",
                "\t<key>UIBackgroundModes</key>\n\t<array>\n\t\t<string>processing</string>\n",
                "host processing background mode",
            )
        else:
            text = replace_once(text, "</dict>\n</plist>", "\t<key>UIBackgroundModes</key>\n\t<array>\n\t\t<string>processing</string>\n\t</array>\n</dict>\n</plist>", "host processing background mode insertion")
    if "NSAlarmKitUsageDescription" not in text:
        text = replace_once(
            text,
            "</dict>\n</plist>",
            "\t<key>NSAlarmKitUsageDescription</key>\n\t<string>Protect automatic refresh deadlines.</string>\n</dict>\n</plist>",
            "AlarmKit usage description",
        )
    path.write_text(text, encoding="utf-8")


def patch_alarm_provider(root: Path) -> None:
    path = root / "LiveContainerSwiftUI" / "App" / "LiveContainerAutoRefreshAlarm.swift"
    if not path.exists():
        path.write_text(ALARM_PROVIDER.lstrip(), encoding="utf-8")


def patch_project(root: Path) -> None:
    path = root / "LiveContainer.xcodeproj" / "project.pbxproj"
    text = path.read_text(encoding="utf-8")
    if "SideStoreSupport.framework in Frameworks" not in text:
        text = replace_once(text, "/* Begin PBXBuildFile section */\n", "/* Begin PBXBuildFile section */\n\tA17ECAFE2DCA000000000001 = {isa = PBXBuildFile; fileRef = 173545A82E2C7913001B3B4C /* SideStoreSupport.framework */; };\n\tA17ECAFE2DCA000000000002 = {isa = PBXBuildFile; fileRef = 173545A82E2C7913001B3B4C /* SideStoreSupport.framework */; };\n", "host framework link build file")
        text = replace_once(text, "17554B6A2DA165D8004C6D90 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t);", "17554B6A2DA165D8004C6D90 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t\tA17ECAFE2DCA000000000001 /* SideStoreSupport.framework in Frameworks */,\n\t\t\t);", "host framework link phase")
        text = replace_once(text, "17413FB22D9C0BAE00F3F928 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n", "17413FB22D9C0BAE00F3F928 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t\tA17ECAFE2DCA000000000002 /* SideStoreSupport.framework in Frameworks */,\n", "host SwiftUI framework link phase")
        text = replace_once(text, "/* Begin PBXTargetDependency section */\n", "/* Begin PBXTargetDependency section */\n\tA17ECAFE2DCA000000000003 = {isa = PBXTargetDependency; target = 173545A72E2C7913001B3B4C /* SideStoreSupport */; targetProxy = 173545AC2E2C7913001B3B4C /* PBXContainerItemProxy */; };\n", "host SwiftUI target dependency")
        text = replace_once(text, "\t\t\tdependencies = (\n\t\t\t);\n\t\t\tfileSystemSynchronizedGroups = (\n\t\t\t\t17413FB62D9C0BAE00F3F928 /* LiveContainerSwiftUI */", "\t\t\tdependencies = (\n\t\t\t\tA17ECAFE2DCA000000000003 /* PBXTargetDependency */,\n\t\t\t);\n\t\t\tfileSystemSynchronizedGroups = (\n\t\t\t\t17413FB62D9C0BAE00F3F928 /* LiveContainerSwiftUI */", "host SwiftUI target dependency list")
        path.write_text(text, encoding="utf-8")
    if "-weak_framework" not in text:
        release_flags = '''\t\t\t\tOTHER_LDFLAGS = (\n\t\t\t\t\t"-e",\n\t\t\t\t\t_LiveContainerMainC,\n\t\t\t\t);'''
        weak_flags = '''\t\t\t\tOTHER_LDFLAGS = (\n\t\t\t\t\t"-e",\n\t\t\t\t\t_LiveContainerMainC,\n\t\t\t\t\t"-weak_framework",\n\t\t\t\t\tAlarmKit,\n\t\t\t\t);'''
        if release_flags in text:
            text = text.replace(release_flags, weak_flags, 1)
            path.write_text(text, encoding="utf-8")


def patch_settings(root: Path) -> None:
    settings = root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCSettingsView.swift"
    text = settings.read_text(encoding="utf-8")
    link = '''                if store == .SideStore {
                    Section {
                        NavigationLink {
                            LCEmbeddedSideStoreRefreshView()
                        } label: {
                            Text("SideStore scheduled refresh")
                        }
                    }
                }
'''
    if "LCEmbeddedSideStoreRefreshView" not in text:
        text = replace_once(text, "            Form {\n", "            Form {\n" + link, "host refresh settings link")
        settings.write_text(text, encoding="utf-8")

    view = root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCEmbeddedSideStoreRefreshView.swift"
    if not view.exists():
        view.write_text(SETTINGS_VIEW.lstrip(), encoding="utf-8")


def verify(root: Path) -> None:
    delegate = (root / "LiveContainerSwiftUI" / "App" / "AppDelegate.swift").read_text(encoding="utf-8")
    support = (root / "SideStoreSupport" / "SideStore.swift").read_text(encoding="utf-8")
    settings = (root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCEmbeddedSideStoreRefreshView.swift").read_text(encoding="utf-8")
    info = (root / "LiveContainer" / "Info.plist").read_text(encoding="utf-8")
    project = (root / "LiveContainer.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    alarm = (root / "LiveContainerSwiftUI/App/LiveContainerAutoRefreshAlarm.swift").read_text(encoding="utf-8")
    required = [
        (delegate, "BGTaskScheduler.shared.register", "host registration"),
        (delegate, "LiveContainerRefreshBridge.refreshAllApps", "host refresh bridge"),
        (delegate, "requiresNetworkConnectivity = true", "network requirement"),
        (delegate, "REFRESH_RESULT run_id=", "refresh result diagnostics"),
        (delegate, "SCHEDULE_PASS target_deadline=", "deadline scheduling"),
        (delegate, "task.setTaskCompleted(success: false)", "expiration completion"),
        (delegate, "result: \"expired\"", "expiration history"),
        (delegate, "LiveContainerAutoRefreshScheduler.runNow()", "manual refresh dispatch"),
        (delegate, "verifyRefreshManifest", "refresh verification"),
        (delegate, "HOST_REFRESH_AWAITING_RELAUNCH", "host handoff state"),
        (delegate, "recoverAfterLaunchOrResume", "launch resume recovery"),
        (delegate, "verifyGuestSignatures", "guest signature verification"),
        (delegate, "GUEST_SIGNATURE_INVALID", "guest failure state"),
        (support, "public enum LiveContainerRefreshBridge", "public bridge"),
        (support, "RefreshHandler.shared.startRefresh", "embedded SideStore refresh"),
        (support, "16SideStoreSupport20RefreshAllAppsIntentV", "combined refresh intent type"),
        (settings, "liveContainerAutoRefreshFrequency", "schedule persistence"),
        (settings, "Refresh SideStore now", "manual refresh control"),
        (settings, "LiveContainerAutoRefreshRunNow", "manual refresh notification"),
        (delegate, "record(source: source", "coalesced history"),
        (delegate, "liveContainerAutoRefreshHistory", "history persistence"),
        (delegate, "RUN_BEGIN source=", "run diagnostics"),
        (info, TASK_ID, "permitted task identifier"),
        (info, f"{TASK_ID}.watchdog", "permitted watchdog identifier"),
        (project, "A17ECAFE2DCA000000000001", "host framework link"),
        (project, "A17ECAFE2DCA000000000002", "host SwiftUI framework link"),
        (project, "A17ECAFE2DCA000000000003", "host SwiftUI target dependency"),
        (alarm, "#if canImport(AlarmKit)", "AlarmKit compile isolation"),
        (alarm, "@available(iOS 26.1, *)", "AlarmKit availability isolation"),
        (alarm, "secondaryIntent", "AlarmKit user action fallback"),
        (project, "-weak_framework", "AlarmKit weak link"),
        (info, "<string>processing</string>", "host processing mode"),
        (info, "NSAlarmKitUsageDescription", "AlarmKit usage description"),
    ]
    missing = [label for content, needle, label in required if needle not in content]
    if missing:
        die("verification failed: " + ", ".join(missing))
    if 'static let taskIdentifier = "\\(Bundle.main.bundleIdentifier' not in delegate:
        die("verification failed: signed host task identifier")
    if "mangledName: \"9SideStore20RefreshAllAppsIntentV\"" in support:
        die("verification failed: obsolete standalone refresh intent type")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_livecontainer_autorefresh.py <livecontainer-root>")
    root = Path(sys.argv[1]).resolve()
    if not (root / "LiveContainer.xcodeproj").exists():
        die(f"not a LiveContainer checkout: {root}")
    patch_support(root)
    patch_host_delegate(root)
    patch_host_info(root)
    patch_project(root)
    patch_alarm_provider(root)
    patch_settings(root)
    verify(root)
    print("LiveContainer host auto-refresh patch applied and verified")


if __name__ == "__main__":
    main()
