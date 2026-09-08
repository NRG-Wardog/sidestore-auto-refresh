import Network
import Darwin

// One-shot checks owned by an actual refresh run. No idle monitoring.
@MainActor
enum LiveContainerNetworkPreflight {
    private static let pendingKey = "liveContainerPendingVPNRefresh"
    private static var defaults: UserDefaults { LiveContainerAutoRefreshScheduler.defaults }

    // Consume once on a fresh host launch; never replay an expired handoff.
    static func consumePendingReturn() -> Bool {
        guard let date = defaults.object(forKey: pendingKey) as? Date else { return false }
        defaults.removeObject(forKey: pendingKey)
        let age = Date().timeIntervalSince(date)
        guard age >= 0 && age < 120 else {
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

    private static func enableInForeground() async -> Bool {
        guard UIApplication.shared.applicationState == .active,
              let scheme = UserDefaults.lcAppUrlScheme(), !scheme.isEmpty else { return false }
        var components = URLComponents(string: "localdevvpn://enable")!
        components.queryItems = [URLQueryItem(name: "scheme", value: scheme)]
        guard let url = components.url else { return false }
        defaults.set(Date(), forKey: pendingKey)
        defer { defaults.removeObject(forKey: pendingKey) }
        return await withCheckedContinuation { continuation in
            var completed = false
            var leftHost = false
            var observers: [NSObjectProtocol] = []
            func finish(_ returned: Bool) {
                guard !completed else { return }
                completed = true
                observers.forEach { NotificationCenter.default.removeObserver($0) }
                observers.removeAll()
                continuation.resume(returning: returned)
            }
            observers.append(NotificationCenter.default.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { _ in
                Task { @MainActor in leftHost = true }
            })
            observers.append(NotificationCenter.default.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { _ in
                Task { @MainActor in
                    guard leftHost else { return }
                    // A user can also return manually. Neither event proves VPN readiness.
                    print("[AUTO_REFRESH] LOCALDEVVPN_RETURN_RECEIVED")
                    finish(true)
                }
            })
            print("[AUTO_REFRESH] LOCALDEVVPN_ENABLE_REQUESTED")
            UIApplication.shared.open(url, options: [:]) { opened in
                Task { @MainActor in if !opened { finish(false) } }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
                finish(false)
            }
        }
    }

    static func check(allowForegroundActivation: Bool) async throws {
        guard await wifiAvailable() else {
            print("[AUTO_REFRESH] WIFI_PREFLIGHT_FAIL reason=no_wifi_path")
            throw failure(1, "Wi-Fi is unavailable. Connect to Wi-Fi before refreshing.")
        }
        try Task.checkCancellation()
        print("[AUTO_REFRESH] WIFI_PREFLIGHT_PASS")
        if !hasTunnelInterface(), allowForegroundActivation {
            guard await enableInForeground() else {
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
