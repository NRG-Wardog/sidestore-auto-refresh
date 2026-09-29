import Network
import Darwin

// V3_VPN_HANDOFF_OWNER_BEGIN
// Pure owner used by the activation flow and its host-side interleaving harness.
// It also makes callbacks arriving after settlement harmless.
@MainActor
final class LiveContainerVPNActivationWaiter {
    enum Outcome: Equatable {
        case returned
        case openFailed
        case timedOut
        case cancelled
    }

    let runID: String
    private let onSettle: @MainActor (String, Outcome) -> Void
    private var continuation: CheckedContinuation<Outcome, Never>?
    private(set) var outcome: Outcome?

    init(runID: String, onSettle: @escaping @MainActor (String, Outcome) -> Void = { _, _ in }) {
        self.runID = runID
        self.onSettle = onSettle
    }

    func value() async -> Outcome {
        await withCheckedContinuation { continuation in
            if let outcome {
                continuation.resume(returning: outcome)
            } else {
                self.continuation = continuation
            }
        }
    }

    @discardableResult
    func settle(_ outcome: Outcome) -> Bool {
        guard self.outcome == nil else { return false }
        self.outcome = outcome
        onSettle(runID, outcome)
        let continuation = self.continuation
        self.continuation = nil
        continuation?.resume(returning: outcome)
        return true
    }
}

@MainActor
final class LiveContainerVPNActivationState {
    var leftHost = false
}

enum LiveContainerVPNReturnMarkerPolicy {
    static func marker(runID: String, requestedAt: Date) -> [String: Any]? {
        guard UUID(uuidString: runID)?.uuidString == runID else { return nil }
        return ["run_id": runID, "requested_at": requestedAt]
    }

    static func owner(in value: Any?) -> String? {
        guard let marker = value as? [String: Any],
              let runID = marker["run_id"] as? String,
              UUID(uuidString: runID)?.uuidString == runID,
              marker["requested_at"] is Date else { return nil }
        return runID
    }

    static func requestedAt(in value: Any?) -> Date? {
        if let marker = value as? [String: Any], owner(in: marker) != nil {
            return marker["requested_at"] as? Date
        }
        // A Date marker is the prior on-disk format. Consume it once so an
        // upgrade during VPN activation keeps its existing return behavior.
        return value as? Date
    }

    static func isSameMarker(_ first: Any?, _ second: Any?) -> Bool {
        if let firstOwner = owner(in: first),
           let secondOwner = owner(in: second) {
            let firstDate = (first as? [String: Any])?["requested_at"] as? Date
            let secondDate = (second as? [String: Any])?["requested_at"] as? Date
            return firstOwner == secondOwner && firstDate == secondDate
        }
        if owner(in: first) == nil, owner(in: second) == nil,
           let firstDate = first as? Date, let secondDate = second as? Date {
            return firstDate == secondDate
        }
        return false
    }

    static func isOwned(by runID: String, value: Any?) -> Bool {
        owner(in: value) == runID
    }

    static func shouldConsume(requestedAt: Date, now: Date) -> Bool {
        let age = now.timeIntervalSince(requestedAt)
        return age >= 0 && age < 120
    }

    static func shouldResume(_ value: Any?, now: Date) -> Bool {
        guard let requestedAt = requestedAt(in: value) else { return false }
        return shouldConsume(requestedAt: requestedAt, now: now)
    }
}
// V3_VPN_HANDOFF_OWNER_END

// One-shot checks owned by an actual refresh run. No idle monitoring.
@MainActor
enum LiveContainerNetworkPreflight {
    private static let pendingKey = "liveContainerPendingVPNRefresh"
    private static var defaults: UserDefaults { LiveContainerAutoRefreshScheduler.defaults }

    // Consume once on a fresh host launch; never replay an expired handoff.
    // The run identity is retained while a live owner exists and validated for
    // new-format markers. A recent legacy Date is consumed once for upgrades.
    static func consumePendingReturn() -> Bool {
        guard let value = defaults.object(forKey: pendingKey) else { return false }
        guard LiveContainerVPNReturnMarkerPolicy.requestedAt(in: value) != nil else {
            defaults.removeObject(forKey: pendingKey)
            return false
        }
        // Do not consume a newer run's marker if another process replaced it.
        guard LiveContainerVPNReturnMarkerPolicy.isSameMarker(
            value, defaults.object(forKey: pendingKey)) else { return false }
        defaults.removeObject(forKey: pendingKey)
        guard LiveContainerVPNReturnMarkerPolicy.shouldResume(value, now: Date()) else {
            print("[AUTO_REFRESH] LOCALDEVVPN_VERIFY_FAIL reason=expired_handoff")
            return false
        }
        return true
    }
    private static func failure(_ code: Int, _ message: String) -> NSError {
        NSError(domain: "LiveContainerRefresh.Network", code: code,
                userInfo: [NSLocalizedDescriptionKey: message])
    }

    static func wifiAvailable() async -> Bool {
        let monitor = NWPathMonitor(requiredInterfaceType: .wifi)
        return await withCheckedContinuation { continuation in
            var completed = false
            func finish(_ available: Bool) {
                guard !completed else { return }
                completed = true
                monitor.cancel()
                continuation.resume(returning: available)
            }
            monitor.pathUpdateHandler = { path in
                let available = path.status == .satisfied && path.usesInterfaceType(.wifi)
                Task { @MainActor in finish(available) }
            }
            monitor.start(queue: DispatchQueue(label: "LiveContainer.WiFiPreflight"))
            // Bound a single pending OS path request; this is not a retry loop.
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                finish(false)
            }
        }
    }

    static func hasTunnelInterface() -> Bool {
        var first: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&first) == 0 else { return false }
        defer { freeifaddrs(first) }
        var current = first
        while let entry = current {
            if String(cString: entry.pointee.ifa_name).hasPrefix("utun"),
               entry.pointee.ifa_flags & UInt32(IFF_UP) != 0 { return true }
            current = entry.pointee.ifa_next
        }
        return false
    }

    private static func enableInForeground(runID: String) async throws -> Bool {
        guard UIApplication.shared.applicationState == .active,
              let scheme = UserDefaults.lcAppUrlScheme(), !scheme.isEmpty,
              UUID(uuidString: runID)?.uuidString == runID else { return false }
        var components = URLComponents(string: "localdevvpn://enable")!
        components.queryItems = [URLQueryItem(name: "scheme", value: scheme)]
        guard let url = components.url else { return false }
        try Task.checkCancellation()
        guard let marker = LiveContainerVPNReturnMarkerPolicy.marker(runID: runID, requestedAt: Date()) else {
            return false
        }
        defaults.set(marker, forKey: pendingKey)
        var observers: [NSObjectProtocol] = []
        var timeout: DispatchWorkItem?
        let waiter = LiveContainerVPNActivationWaiter(runID: runID) { ownerRunID, _ in
            observers.forEach { NotificationCenter.default.removeObserver($0) }
            observers.removeAll()
            timeout?.cancel()
            timeout = nil
            Self.clearPendingReturn(ownedBy: ownerRunID)
        }
        let activationState = LiveContainerVPNActivationState()
        observers.append(NotificationCenter.default.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in activationState.leftHost = true }
        })
        observers.append(NotificationCenter.default.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in
                guard activationState.leftHost else { return }
                // A user can also return manually. Neither event proves VPN readiness.
                print("[AUTO_REFRESH] LOCALDEVVPN_RETURN_RECEIVED")
                waiter.settle(.returned)
            }
        })
        let timeoutWork = DispatchWorkItem {
            Task { @MainActor in waiter.settle(.timedOut) }
        }
        timeout = timeoutWork
        DispatchQueue.main.asyncAfter(deadline: .now() + 30, execute: timeoutWork)
        print("[AUTO_REFRESH] LOCALDEVVPN_ENABLE_REQUESTED run_id=\(runID)")
        UIApplication.shared.open(url, options: [:]) { opened in
            Task { @MainActor in if !opened { waiter.settle(.openFailed) } }
        }
        let outcome = await withTaskCancellationHandler {
            await waiter.value()
        } onCancel: {
            Task { @MainActor in waiter.settle(.cancelled) }
        }
        if Task.isCancelled {
            waiter.settle(.cancelled)
            throw CancellationError()
        }
        switch outcome {
        case .returned: return true
        case .openFailed, .timedOut: return false
        case .cancelled: throw CancellationError()
        }
    }

    private static func clearPendingReturn(ownedBy runID: String) {
        guard LiveContainerVPNReturnMarkerPolicy.isOwned(
            by: runID, value: defaults.object(forKey: pendingKey)) else { return }
        defaults.removeObject(forKey: pendingKey)
    }

    static func check(allowForegroundActivation: Bool, runID: String) async throws {
        guard UUID(uuidString: runID)?.uuidString == runID else { throw CancellationError() }
        guard await wifiAvailable() else {
            print("[AUTO_REFRESH] WIFI_PREFLIGHT_FAIL reason=no_wifi_path")
            throw failure(1, "Wi-Fi is unavailable. Connect to Wi-Fi before refreshing.")
        }
        try Task.checkCancellation()
        print("[AUTO_REFRESH] WIFI_PREFLIGHT_PASS")
        if !hasTunnelInterface(), allowForegroundActivation {
            guard try await enableInForeground(runID: runID) else {
                throw failure(2, "LocalDevVPN activation did not return. Enable LocalDevVPN and retry refresh.")
            }
            try Task.checkCancellation()
            guard await wifiAvailable() else {
                print("[AUTO_REFRESH] WIFI_PREFLIGHT_FAIL reason=wifi_lost_during_activation")
                throw failure(1, "Wi-Fi was lost while enabling LocalDevVPN. Reconnect and retry.")
            }
        }
        guard hasTunnelInterface() else {
            if !allowForegroundActivation { print("[AUTO_REFRESH] VPN_UNAVAILABLE_BACKGROUND") }
            print("[AUTO_REFRESH] LOCALDEVVPN_VERIFY_FAIL reason=no_utun_interface")
            throw failure(2, "Open LiveContainer and enable LocalDevVPN to continue refresh.")
        }
        // Interface presence is not proof of provider identity or CoreDevice readiness.
        print("[AUTO_REFRESH] VPN_INTERFACE_PRESENT readiness=requires_coredevice_verification")
    }
}
