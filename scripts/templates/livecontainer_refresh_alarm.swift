import Foundation
import UserNotifications
#if canImport(AlarmKit)
import AlarmKit
import AppIntents
import SwiftUI

@available(iOS 26.1, *)
private struct LiveContainerRefreshAlarmMetadata: AlarmMetadata {}

@available(iOS 26.1, *)
private struct LiveContainerRefreshAlarmIntent: LiveActivityIntent {
    static var title: LocalizedStringResource { "Refresh LiveContainer now" }
    static var openAppWhenRun: Bool { true }

    @MainActor
    func perform() async throws -> some IntentResult {
        // Await the operation; returning after spawning an unstructured Task
        // would tell the intent system it can end our execution prematurely.
        await LiveContainerAutoRefreshScheduler.requestRefreshNow()
        return .result()
    }
}

@available(iOS 26.1, *)
@MainActor
enum LiveContainerAutoRefreshAlarmProvider {
    private static let alarmID = UUID(uuidString: "7B0A0E8E-0C90-4E33-9BA9-6DD38D8D5E2E")!

    private static var scheduling = false

    static func requestAuthorization() async {
        do { _ = try await AlarmManager.shared.requestAuthorization() }
        catch { print("[LIVE_CONTAINER_REFRESH] ALARM_AUTHORIZATION_FAIL error=\(error.localizedDescription)") }
        LiveContainerAutoRefreshScheduler.schedule()
    }

    static func scheduleIfAvailable(deadline: Date) async {
        let defaults = LiveContainerAutoRefreshScheduler.defaults
        guard !scheduling else { return }
        scheduling = true
        defer { scheduling = false }
        guard defaults.bool(forKey: LiveContainerAutoRefreshScheduler.enabledKey), deadline > Date(),
              AlarmManager.shared.authorizationState == .authorized else {
            defaults.set(false, forKey: "liveContainerAutoRefreshAlarmScheduled")
            print("[LIVE_CONTAINER_REFRESH] ALARM_UNAVAILABLE reason=disabled_or_not_authorized")
            return // The pre-scheduled local notification remains the fallback.
        }
        let liveAlarms = (try? AlarmManager.shared.alarms) ?? []
        if liveAlarms.contains(where: { $0.id == alarmID }), defaults.bool(forKey: "liveContainerAutoRefreshAlarmScheduled"),
           let existing = defaults.object(forKey: "liveContainerAutoRefreshAlarmDeadline") as? Date,
           abs(existing.timeIntervalSince(deadline)) < 1 {
            UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: [LiveContainerAutoRefreshScheduler.warningIdentifier])
            return
        }
        if liveAlarms.contains(where: { $0.id == alarmID }) {
            do { try AlarmManager.shared.cancel(id: alarmID) }
            catch { print("[LIVE_CONTAINER_REFRESH] ALARM_REPLACE_FAIL error=\(error.localizedDescription)"); return }
        }
        let alert = AlarmPresentation.Alert(
            title: "Automatic refresh needs checking",
            secondaryButton: AlarmButton(text: "Refresh Now", textColor: .white, systemImageName: "arrow.clockwise"),
            secondaryButtonBehavior: .custom)
        let attributes: AlarmAttributes<LiveContainerRefreshAlarmMetadata> = AlarmAttributes(
            presentation: AlarmPresentation(alert: alert), metadata: LiveContainerRefreshAlarmMetadata(), tintColor: Color.orange)
        let configuration: AlarmManager.AlarmConfiguration<LiveContainerRefreshAlarmMetadata> = AlarmManager.AlarmConfiguration.alarm(
            schedule: .fixed(deadline), attributes: attributes, stopIntent: nil,
            secondaryIntent: LiveContainerRefreshAlarmIntent(), sound: .default)
        do {
            _ = try await AlarmManager.shared.schedule(id: alarmID, configuration: configuration)
            guard defaults.bool(forKey: LiveContainerAutoRefreshScheduler.enabledKey),
                  (defaults.object(forKey: LiveContainerAutoRefreshScheduler.deadlineKey) as? Date) == deadline else {
                cancelIfAvailable()
                return
            }
            defaults.set(deadline, forKey: "liveContainerAutoRefreshAlarmDeadline")
            defaults.set(alarmID.uuidString, forKey: "liveContainerAutoRefreshAlarmID")
            defaults.set(true, forKey: "liveContainerAutoRefreshAlarmScheduled")
            defaults.set("native_full", forKey: "liveContainerAutoRefreshStrategy")
            UNUserNotificationCenter.current().removePendingNotificationRequests(
                withIdentifiers: [LiveContainerAutoRefreshScheduler.warningIdentifier])
            print("[LIVE_CONTAINER_REFRESH] ALARM_SCHEDULE_PASS")
        } catch {
            defaults.set(false, forKey: "liveContainerAutoRefreshAlarmScheduled")
            print("[LIVE_CONTAINER_REFRESH] ALARM_SCHEDULE_FAIL error=\(error.localizedDescription)")
            // Do not disable BG refresh or delete the local-notification fallback.
        }
    }

    static func cancelIfAvailable() {
        do { try AlarmManager.shared.cancel(id: alarmID) }
        catch {
            LiveContainerAutoRefreshScheduler.defaults.set(error.localizedDescription, forKey: "liveContainerAutoRefreshDeadlineWarningError")
            print("[LIVE_CONTAINER_REFRESH] ALARM_CANCEL_RESULT error=\(error.localizedDescription)")
            return
        }
        LiveContainerAutoRefreshScheduler.defaults.removeObject(forKey: "liveContainerAutoRefreshAlarmDeadline")
        LiveContainerAutoRefreshScheduler.defaults.set(false, forKey: "liveContainerAutoRefreshAlarmScheduled")
    }
}
#else
@MainActor
enum LiveContainerAutoRefreshAlarmProvider {
    static func requestAuthorization() async {}
    static func scheduleIfAvailable(deadline: Date) async {}
    static func cancelIfAvailable() {}
}
#endif
