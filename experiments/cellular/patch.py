"""Opt-in diagnostic only. Apply AFTER the combined transport patch."""
from pathlib import Path
import sys

MARKER = "CELLULAR_READONLY_V1"

GATEWAY = r'''
    // CELLULAR_READONLY_V1: single FFI queue operation, never reuse a Wi-Fi session.
    public func cellularReadOnlyProbe(peer: String) async -> String {
        await withCheckedContinuation { continuation in
            ffiQueue.async {
                var events: [String] = []
                let run = UUID().uuidString
                let start = Date()
                func event(_ message: String) {
                    let line = "[CELLULAR_DIAG] run=\(run) elapsed_ms=\(Int(Date().timeIntervalSince(start) * 1000)) \(message)"
                    events.append(line)
                    debugLog(line)
                }
                defer { continuation.resume(returning: events.joined(separator: "\n")) }
                guard self.batchCount == 0 else { event("REFUSED reason=refresh_batch_active"); return }
                guard self.usesCoreDevice else { event("REFUSED reason=coredevice_not_selected"); return }
                guard self.adapter == nil, self.handshake == nil, self.coreDeviceProvider == nil else {
                    event("REFUSED reason=existing_transport_close_and_reopen_sidestore")
                    return
                }
                let previousPeer = self.deviceEndpointIp
                self.setDeviceEndpointIp(peer)
                defer {
                    self.releaseTransport()
                    self.setDeviceEndpointIp(previousPeer)
                    event("RESOURCES_RELEASED")
                }
                var stage = "COREDEVICE_CONNECT"
                do {
                    event("COREDEVICE_CONNECT_START fresh=true")
                    try self.ensureCoreDeviceConnection()
                    event("COREDEVICE_RSD_PASS")
                    stage = "INSTALL_PROXY_CONNECT"
                    event("INSTALL_PROXY_CONNECT_START")
                    var client: OpaquePointer?
                    let connectError = installation_proxy_connect_rsd(self.adapter, self.handshake, &client)
                    defer { if let client { installation_proxy_client_free(client) } }
                    if let error = connectError {
                        let message = self.getErrorMessage(from: error)
                        idevice_error_free(error)
                        throw IdeviceGatewayError(.serviceError, reason: message)
                    }
                    guard let client else { throw IdeviceGatewayError(.serviceError, reason: "Missing InstallationProxy handle") }
                    event("INSTALL_PROXY_CONNECT_PASS")
                    stage = "BROWSE"
                    event("BROWSE_START")
                    var result: UnsafeMutableRawPointer?
                    var count = 0
                    let browseError = installation_proxy_get_apps(client, nil, nil, 0, &result, &count)
                    defer {
                        if let result { idevice_plist_array_free(result.assumingMemoryBound(to: plist_t?.self), UInt(count)) }
                    }
                    if let error = browseError {
                        let message = self.getErrorMessage(from: error)
                        idevice_error_free(error)
                        throw IdeviceGatewayError(.serviceError, reason: message)
                    }
                    guard count > 0, result != nil else { throw IdeviceGatewayError(.serviceError, reason: "Empty Browse response is inconclusive") }
                    event("BROWSE_PASS count=\(count) refresh_performed=false")
                } catch {
                    let error = error as NSError
                    event("FAIL stage=\(stage) domain=\(error.domain) code=\(error.code) reason=\(error.localizedDescription)")
                }
            }
        }
    }
'''

MUX = r'''
    // CELLULAR_READONLY_V1: no call to isReady and no global policy override.
    func cellularReadOnlyProbe() async -> String {
        debugLog("[CELLULAR_DIAG] ROUTE_CHECK_START ipsec_interface_present=\(network.isIKEv2IPSecAvailable)")
        guard isPairingFileLoaded, gateway.pairingFileType == .lockdown else {
            return "REFUSED: a loaded Lockdown pairing record is required."
        }
        guard await getConnectionMode() == .localVPN, gateway.coreDeviceTransportEnabled else {
            return "REFUSED: select the LocalDevVPN/CoreDevice connection first."
        }
        guard !gateway.hasActiveTransportBatch else { return "REFUSED: refresh is active." }
        await network.refreshEndpoint()
        // Require a peer from the existing tunnel route, never an external-server override.
        guard await connectionManager.isDerivedPeerIpReachable,
              let peer = await connectionManager.derivedPeerIp else {
            return "FAIL stage=ROUTE_TCP: no reachable derived tunnel peer; CoreDevice was not initialized."
        }
        return await gateway.cellularReadOnlyProbe(peer: peer)
    }
'''

UI = r'''
    // CELLULAR_READONLY_V1: foreground-only, no persisted enable switch.
    @State private var cellularProbeRunning = false
    @State private var cellularProbeResult = "Not run"

    @MainActor
    private func samplePath(_ type: NWInterface.InterfaceType) async -> Bool? {
        let monitor = NWPathMonitor(requiredInterfaceType: type)
        return await withCheckedContinuation { continuation in
            var finished = false
            func finish(_ result: Bool?) {
                guard !finished else { return }
                finished = true
                monitor.cancel()
                continuation.resume(returning: result)
            }
            monitor.pathUpdateHandler = { path in
                let satisfied = path.status == .satisfied && path.usesInterfaceType(type)
                Task { @MainActor in finish(satisfied) }
            }
            monitor.start(queue: DispatchQueue(label: "CellularDiagnostic.Path"))
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { finish(nil) }
        }
    }

    @MainActor
    private func runCellularProbe() async {
        guard !cellularProbeRunning else { return }
        cellularProbeRunning = true
        defer { cellularProbeRunning = false }
        guard UIApplication.shared.applicationState == .active else { return }
        guard !UserDefaults.standard.isCellularRefreshEnabled else {
            cellularProbeResult = "REFUSED: disable the existing Cellular Refresh shortcut toggle for this test."
            return
        }
        cellularProbeResult = "Checking network paths..."
        let wifi = await samplePath(.wifi)
        let cellular = await samplePath(.cellular)
        guard wifi == false, cellular == true, UIApplication.shared.applicationState == .active else {
            cellularProbeResult = "REFUSED: Wi-Fi must be off, cellular must be available, and SideStore must stay open."
            debugLog("[CELLULAR_DIAG] PATH_PREFLIGHT_REFUSED")
            return
        }
        cellularProbeResult = "Testing fresh CoreDevice connection. No apps will be refreshed."
        let result = await minimuxer.core.cellularReadOnlyProbe()
        let wifiAfter = await samplePath(.wifi)
        let cellularAfter = await samplePath(.cellular)
        let active = UIApplication.shared.applicationState == .active
        cellularProbeResult = "Paths before: wifi=\(String(describing: wifi)) cellular=\(String(describing: cellular))\n" + result +
            "\nPaths after: wifi=\(String(describing: wifiAfter)) cellular=\(String(describing: cellularAfter)) active=\(active)\n" +
            "Path snapshots are not proof that the path stayed unchanged throughout. No refresh was performed."
        debugLog("[CELLULAR_DIAG] REPORT " + cellularProbeResult)
    }
'''

ROW = r'''
                VStack(alignment: .leading, spacing: 8) {
                    Button {
                        Task { await runCellularProbe() }
                    } label: {
                        Label("Test Cellular Transport (Read Only)", systemImage: "antenna.radiowaves.left.and.right")
                    }.disabled(cellularProbeRunning)
                    Text(cellularProbeResult).font(.caption).textSelection(.enabled)
                }
'''


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Cellular diagnostic anchor changed: " + old)
    return text.replace(old, new, 1)


def patch(root):
    root = Path(root)
    mux = root / "Dependencies/minimuxer"
    paths = [mux / "DeviceGateway/idevice/IdeviceGateway.swift",
             mux / "DeviceGateway/DeviceGatewayAPI.swift", mux / "Sources/MinimuxerImpl.swift",
             mux / "Sources/MinimuxerApi.swift",
             root / "SideStore/Views/Settings/Diagnostics/ExperimentalFeaturesView.swift"]
    gateway, api, impl, mux_api, view = [p.read_text(encoding="utf-8") for p in paths]
    if MARKER in gateway:
        assert GATEWAY in gateway and MUX in impl and UI in view and ROW in view
        assert 'func cellularReadOnlyProbe(peer: String)' in api and 'func cellularReadOnlyProbe()' in mux_api
        return
    if "COMBINED_COREDEVICE_BATCH_V1" not in gateway:
        raise ValueError("Apply combined transport first")
    gateway = replace(gateway, "    private func ensureCoreDeviceConnection() throws {", GATEWAY + "\n    private func ensureCoreDeviceConnection() throws {")
    api = replace(api, "public protocol DeviceGatewayAPI: AnyObject, Sendable {", "public protocol DeviceGatewayAPI: AnyObject, Sendable {\n    func cellularReadOnlyProbe(peer: String) async -> String")
    api = replace(api, "public extension DeviceGatewayAPI {", 'public extension DeviceGatewayAPI {\n    func cellularReadOnlyProbe(peer: String) async -> String { "REFUSED: unsupported backend" }')
    impl = replace(impl, "    @discardableResult\n    private func configureRefreshTransport()", MUX + "\n    @discardableResult\n    private func configureRefreshTransport()")
    mux_api = replace(mux_api, "public protocol MinimuxerAPI: AnyObject {", "public protocol MinimuxerAPI: AnyObject {\n    func cellularReadOnlyProbe() async -> String")
    view = replace(view, "import SwiftUI", "import SwiftUI\nimport Network\nimport Minimuxer")
    view = replace(view, "struct ExperimentalFeaturesView: View {", "struct ExperimentalFeaturesView: View {\n" + UI)
    view = replace(view, "                // Section 1: STANDALONE FEATURES", ROW + "\n                // Section 1: STANDALONE FEATURES")
    for path, value in zip(paths, (gateway, api, impl, mux_api, view)):
        path.write_text(value, encoding="utf-8")


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
