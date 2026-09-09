
// CELLULAR_BOOTSTRAP_SCREEN_V1: compiled only by the explicit experimental patch.
import Network
import CryptoKit
import MinimuxerCommon
import UniformTypeIdentifiers

private final class CellularPathSnapshot: @unchecked Sendable {
    private let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "CellularBootstrap.path")
    private var timeout: DispatchSourceTimer?
    private var completion: CheckedContinuation<CellularBootstrapState.Network, Never>?

    static func read() async -> CellularBootstrapState.Network {
        await withCheckedContinuation { continuation in
            let request = CellularPathSnapshot()
            request.completion = continuation
            let timeout = DispatchSource.makeTimerSource(queue: request.queue)
            request.timeout = timeout
            request.monitor.pathUpdateHandler = { path in
                let network: CellularBootstrapState.Network = path.status != .satisfied ? .unknown :
                    (path.usesInterfaceType(.wifi) ? .wifi : (path.usesInterfaceType(.cellular) ? .cellular : .unknown))
                request.finish(network)
            }
            timeout.setEventHandler { request.finish(.unknown) }
            timeout.schedule(deadline: .now() + 3)
            timeout.resume()
            request.monitor.start(queue: request.queue)
        }
    }

    // Both callbacks execute on queue; clearing handlers breaks ownership cycles.
    private func finish(_ network: CellularBootstrapState.Network) {
        guard let completion else { return }
        self.completion = nil
        monitor.cancel(); monitor.pathUpdateHandler = nil
        timeout?.setEventHandler {}; timeout?.cancel(); timeout = nil
        completion.resume(returning: network)
    }
}

@MainActor
private final class CellularBootstrapModel: ObservableObject {
    @Published var state = CellularBootstrapState()
    @Published var detail = "Checking existing pairing"
    @Published var tunnel = "Not discovered"
    @Published var peer = "Not discovered"
    @Published var working = false
    private var appeared = false
    private var baselinePairing: Data?
    private var baselineConfiguration: String?
    private var lastPairing: Data?
    private var pendingVPNReturn = false
    private var pendingCellular = false

    private func digest(_ value: String) -> Data { Data(SHA256.hash(data: Data(value.utf8))) }

    // One bounded snapshot, not a persistent monitor. The timer is solely an
    // operation deadline and is cancelled when the first update is delivered.
    private nonisolated static func networkSnapshot() async -> CellularBootstrapState.Network {
        await CellularPathSnapshot.read()
    }

    func open() async {
        guard !appeared else { return }
        appeared = true
        await checkAndRun()
    }

    func returned() async {
        guard !working else { return }
        guard pendingVPNReturn || pendingCellular else { return }
        working = true
        // Consume VPN callbacks once. Keep the cellular intent only while Wi-Fi
        // remains enabled; do not spin or retry a failed cellular attempt.
        pendingVPNReturn = false
        let network = await Self.networkSnapshot()
        if pendingCellular && network != .cellular {
            state.updateNetwork(network)
            working = false
            return
        }
        pendingCellular = false
        working = false
        await checkAndRun()
    }

    func inactive() { state.resignedActive() }

    func prepareImport() {
        pendingVPNReturn = false; pendingCellular = false
    }

    func enableVPN() {
        guard !working, UIApplication.shared.applicationState == .active,
              let url = URL(string: "localdevvpn://enable") else { return }
        // Settings in the other app may change the tunnel; require a fresh
        // baseline rather than silently comparing against old configuration.
        state.invalidateBaseline()
        baselinePairing = nil; baselineConfiguration = nil; pendingCellular = false
        pendingVPNReturn = true
        state.requestedVPN()
        UIApplication.shared.open(url, options: [:]) { [weak self] opened in
            Task { @MainActor in
                guard let self else { return }
                if !opened { self.pendingVPNReturn = false }
                self.detail = opened ? "Enable LocalDevVPN, then return here. Readiness will be checked." :
                    "Install official LocalDevVPN, enable its tunnel, then return here."
            }
        }
    }

    func importPairing(_ url: URL) async {
        guard !working, !minimuxer.gateway.hasActiveTransportBatch else {
            detail = "A refresh is using the transport. Try again when it finishes."
            return
        }
        working = true
        do {
            let accessed = url.startAccessingSecurityScopedResource()
            defer { if accessed { url.stopAccessingSecurityScopedResource() } }
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? Int.max
            guard size > 0, size <= 1_048_576 else { throw CocoaError(.fileReadCorruptFile) }
            let data = try Data(contentsOf: url)
            guard data.count <= 1_048_576 else { throw CocoaError(.fileReadCorruptFile) }
            let plist = try PropertyListSerialization.propertyList(from: data, options: [], format: nil)
            let canonicalData = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
            guard let content = String(data: canonicalData, encoding: .utf8),
                  await minimuxer.gateway.cellularValidatePairing(content) else {
                throw CocoaError(.fileReadCorruptFile)
            }
            try PairingFileManager.shared.saveDiagnosticPairing(contents: content)
            state.pairingChanged()
            baselinePairing = nil; baselineConfiguration = nil
            detail = "Pairing imported. Device acceptance still needs validation."
        } catch {
            detail = "Pairing import failed. The existing record was not replaced. Select a valid Lockdown pairing file."
            working = false
            return
        }
        working = false
        await checkAndRun()
    }

    func checkAndRun(recheckWiFi: Bool = false) async {
        guard !working else { return }
        working = true
        defer { working = false }
        guard let content = PairingFileManager.shared.fetchPairingFile() else {
            state.pairingLoaded(present: false, parsed: false)
            detail = "Pairing is required. Use SideStore pairing setup or import your pairing record."
            return
        }
        let pairingDigest = digest(content)
        if lastPairing != nil && lastPairing != pairingDigest {
            state.pairingChanged(); baselinePairing = nil; baselineConfiguration = nil
        }
        lastPairing = pairingDigest
        let parsed = await minimuxer.gateway.cellularValidatePairing(content)
        state.pairingLoaded(present: true, parsed: parsed)
        guard parsed else { detail = "Existing pairing cannot be parsed as a valid Lockdown record."; return }
        let before = await Self.networkSnapshot()
        state.updateNetwork(before)
        guard before != .unknown else { detail = "No usable Wi-Fi or cellular path was detected."; return }
        if before == .wifi && recheckWiFi {
            state.invalidateBaseline()
            baselinePairing = nil; baselineConfiguration = nil; pendingCellular = false
        }
        if before == .cellular && (baselinePairing != pairingDigest || baselineConfiguration == nil) {
            state.invalidateBaseline()
            detail = "Connect Wi-Fi and enable LocalDevVPN first. A successful Wi-Fi baseline is required."
            return
        }
        guard let run = state.begin() else {
            detail = state.nextAction() == .disableWiFi ?
                "Turn Wi-Fi off in Settings. Keep cellular and LocalDevVPN enabled, then return here." :
                "Connect Wi-Fi to validate the baseline first."
            return
        }
        detail = "Checking tunnel routes, authenticated Lockdown and CoreDevice/RSD"
        let discovery = await minimuxer.core.cellularProbeDiscovery()
        let result = await minimuxer.gateway.cellularReadOnlyProbe(content, discovery: discovery,
            expectedConfiguration: before == .cellular ? baselineConfiguration : nil)
        if result.busy || result.failureStage == "UNSUPPORTED_GATEWAY" {
            state.stop(run: run, because: result.busy ? .busy : .unavailable)
            detail = result.busy ? "A production refresh owns the transport. Wait for it to finish, then run again." :
                "This build does not provide the CoreDevice diagnostic gateway."
            return
        }
        if let selected = result.selectedPeer { tunnel = selected.tunnel; peer = selected.ip }
        else { tunnel = discovery.interfaceCount == 0 ? "Not found" : "Not selected"; peer = "Not selected" }
        state.discovered(run: run, interfaces: discovery.interfaceCount, candidates: discovery.peers.count,
                         reachable: result.reachableCount, tcpResult: result.tcp)
        if result.tcp == .pass && !result.configurationMismatch {
            state.lockdownCompleted(run: run, accepted: result.lockdownAccepted, rejected: result.lockdownRejected)
            if result.lockdownAccepted {
                state.transportCompleted(run: run, coreDevicePassed: result.coreDevicePassed, rsdPassed: result.rsdPassed)
            }
        }
        let after = await Self.networkSnapshot()
        let finalDiscovery = await minimuxer.core.cellularProbeDiscovery()
        let finalPairing = PairingFileManager.shared.fetchPairingFile().map { digest($0) }
        let configurationStillPresent = result.selectedPeer.map { selected in
            finalDiscovery.peers.contains { $0.configuration == selected.configuration }
        } ?? true
        if finalPairing != pairingDigest || !configurationStillPresent || result.configurationMismatch {
            state.stop(run: run, because: .environmentChanged)
            baselinePairing = nil; baselineConfiguration = nil; pendingCellular = false
            detail = "Pairing or VPN configuration changed. Reconnect Wi-Fi and establish a new baseline."
            return
        }
        // Rust compares authenticated UniqueDeviceID to the canonical record.
        // The digest binds that same identity without ever exporting its value.
        let identity = result.selectedPeer.map {
            CellularBootstrapState.Identity(build: "this-process-\(ProcessInfo.processInfo.processIdentifier)",
                pairingDigest: pairingDigest, deviceDigest: pairingDigest,
                vpnDigest: digest($0.configuration), resolver: "pinned-minimuxer-v1")
        }
        state.finish(run: run, identity: identity, networkAfter: after)
        if before == .wifi && state.baseline == .pass {
            baselinePairing = pairingDigest; baselineConfiguration = result.selectedPeer?.configuration
            pendingCellular = true
            detail = "Wi-Fi baseline passed. Turn Wi-Fi off in Settings, keep cellular and LocalDevVPN enabled, then return here."
        } else if state.cellular == .pass {
            detail = "The read-only chain passed on cellular with the matching Wi-Fi baseline. Refresh/install is not tested."
        } else if state.reason == "NETWORK_CHANGED_OR_INTERRUPTED" {
            baselinePairing = nil; baselineConfiguration = nil; pendingCellular = false
            detail = "The path changed or the app left the foreground during testing. Reconnect Wi-Fi and repeat the baseline."
        } else if result.tcp == .refused {
            detail = "The discovered VPN peer refused TCP 62078. Pairing was parsed but not authenticated on this attempt."
        } else {
            detail = "Stopped at \(result.failureStage). No refresh or installation was performed."
        }
        NSLog("[CELLULAR_BOOTSTRAP] %@ code=%d", state.report, result.errorCode)
    }
}

private struct CellularBootstrapView: View {
    @StateObject private var model = CellularBootstrapModel()
    @State private var importing = false
    var body: some View {
        List {
            Section("Setup health") {
                row("Pairing", model.state.pairingReady ? "READY" : model.state.pairing.rawValue)
                row("LocalDevVPN", model.state.vpn.rawValue)
                row("Network", model.state.network.rawValue.uppercased())
                row("Tunnel", model.tunnel)
                row("Peer", model.peer)
                row("TCP 62078", model.state.tcp.rawValue)
                row("Lockdown", model.state.lockdown.rawValue)
                row("CoreDevice", model.state.coreDevice.rawValue)
                row("RSD", model.state.rsd.rawValue)
                row("Wi-Fi baseline", model.state.baseline.rawValue)
                row("Cellular", model.state.cellular.rawValue)
            }
            Section {
                Text(model.detail).fixedSize(horizontal: false, vertical: true)
                if model.working { ProgressView("Validating") }
                SwiftUI.Button { Task { await model.checkAndRun(recheckWiFi: true) } } label: {
                    Label("Check Setup", systemImage: "arrow.clockwise")
                }.disabled(model.working)
                SwiftUI.Button { model.enableVPN() } label: {
                    Label("Open LocalDevVPN", systemImage: "network")
                }.disabled(model.working)
                SwiftUI.Button { model.prepareImport(); importing = true } label: {
                    Label("Import Pairing", systemImage: "square.and.arrow.down")
                }.disabled(model.working)
            }
        }
        .navigationTitle("Cellular Diagnostics")
        .task { await model.open() }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            Task { await model.returned() }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in model.inactive() }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.data, .xml, .propertyList]) { result in
            if case .success(let url) = result { Task { await model.importPairing(url) } }
        }
    }
    private func row(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.subheadline)
            Text(value).font(.caption.monospaced()).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
