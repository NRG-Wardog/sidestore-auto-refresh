import Foundation

// Pure policy shared by the embedded UI and executable regression tests.
// No file, credential, network, timer, logger or application dependencies.
public struct CellularBootstrapState: Sendable {
    public enum Network: String, Sendable { case wifi, cellular, unknown }
    public enum Pairing: String, Sendable {
        case notFound = "PAIRING_NOT_FOUND"
        case found = "PAIRING_FOUND"
        case parsed = "PAIRING_PARSE_OK"
        case parseFailed = "PAIRING_PARSE_FAILED"
        case accepted = "PAIRING_LOCKDOWN_ACCEPTED"
        case rejected = "PAIRING_LOCKDOWN_REJECTED"
    }
    public enum VPN: String, Sendable {
        case notAttempted = "NOT_ATTEMPTED"
        case absent = "VPN_NOT_PRESENT"
        case interfaceFound = "VPN_INTERFACE_FOUND"
        case peerFound = "VPN_PEER_DISCOVERED"
        case ambiguous = "VPN_PEER_AMBIGUOUS"
        case reachable = "VPN_PEER_TCP_REACHABLE"
        case refused = "VPN_PEER_TCP_REFUSED"
        case timedOut = "VPN_PEER_TCP_TIMEOUT"
        case failed = "VPN_PEER_TCP_FAILED"
    }
    public enum Check: String, Sendable {
        case notAttempted = "NOT_ATTEMPTED", running = "RUNNING"
        case pass = "PASS", fail = "FAIL", refused = "REFUSED", timeout = "TIMEOUT"
    }
    public enum Action: Equatable, Sendable {
        case none, importPairing, enableVPN, enableWiFi, disableWiFi, runWiFi, runCellular
    }
    public enum StopReason: String, Sendable {
        case busy = "TRANSPORT_BUSY"
        case unavailable = "UNSUPPORTED_GATEWAY"
        case environmentChanged = "BASELINE_MISMATCH_WIFI_REQUIRED"
        case interrupted = "NETWORK_CHANGED_OR_INTERRUPTED"
    }
    // Opaque comparison identity, never included in a report. Construct from build,
    // canonical pairing, authenticated device identity and normalized VPN config.
    public struct Identity: Equatable, Sendable {
        private let build: String
        private let pairing: Data
        private let device: Data
        private let vpn: Data
        private let resolver: String
        public init(build: String, pairingDigest: Data, deviceDigest: Data,
                    vpnDigest: Data, resolver: String) {
            self.build = build; pairing = pairingDigest; device = deviceDigest
            vpn = vpnDigest; self.resolver = resolver
        }
    }
    public private(set) var pairing: Pairing = .notFound
    public private(set) var vpn: VPN = .notAttempted
    public private(set) var network: Network = .unknown
    public private(set) var tcp: Check = .notAttempted
    public private(set) var lockdown: Check = .notAttempted
    public private(set) var coreDevice: Check = .notAttempted
    public private(set) var rsd: Check = .notAttempted
    public private(set) var baseline: Check = .notAttempted
    public private(set) var cellular: Check = .notAttempted
    public private(set) var reason = "PAIRING_REQUIRED"
    public private(set) var runID: UUID?
    private var baselineIdentity: Identity?
    private var continuationArmed = false
    private var waitingForVPN = false
    private var interrupted = false
    private var runNetwork: Network = .unknown

    public init() {}
    public var pairingReady: Bool { pairing == .accepted }

    public mutating func pairingLoaded(present: Bool, parsed: Bool) {
        guard runID == nil else { return }
        pairing = !present ? .notFound : (parsed ? .parsed : .parseFailed)
        vpn = .notAttempted
        tcp = .notAttempted; lockdown = .notAttempted
        coreDevice = .notAttempted; rsd = .notAttempted
        if !present || !parsed { invalidateBaseline() }
        reason = !present ? "PAIRING_REQUIRED" : (parsed ? "LOCKDOWN_NOT_VALIDATED" : "PAIRING_PARSE_FAILED")
    }

    public mutating func pairingChanged() {
        guard runID == nil else { return }
        invalidateBaseline()
        pairing = .found
        lockdown = .notAttempted; coreDevice = .notAttempted; rsd = .notAttempted
    }

    public mutating func updateNetwork(_ value: Network) {
        network = value
        if runID != nil && value != runNetwork { interrupted = true }
    }

    public func nextAction() -> Action {
        guard runID == nil else { return .none }
        guard pairing == .parsed || pairing == .accepted else { return .importPairing }
        if network == .unknown { return .none }
        if baselineIdentity == nil { return network == .wifi ? .runWiFi : .enableWiFi }
        return network == .cellular ? .runCellular : .disableWiFi
    }

    public mutating func requestedVPN() {
        guard runID == nil else { return }
        waitingForVPN = true
    }

    // Call only for an actual foreground transition, not every SwiftUI render.
    public mutating func becameActive(network: Network) -> Action {
        updateNetwork(network)
        guard runID == nil else { return .none }
        if waitingForVPN {
            waitingForVPN = false
            return nextAction()
        }
        guard continuationArmed, network == .cellular else { return .none }
        continuationArmed = false
        return nextAction()
    }

    public mutating func resignedActive() {
        if runID != nil { interrupted = true }
    }

    @discardableResult
    public mutating func begin() -> UUID? {
        let action = nextAction()
        guard action == .runWiFi || action == .runCellular else { return nil }
        let id = UUID()
        runID = id; runNetwork = network; interrupted = false
        continuationArmed = false
        pairing = .parsed; vpn = .notAttempted
        tcp = .notAttempted; lockdown = .notAttempted
        coreDevice = .notAttempted; rsd = .notAttempted
        reason = "RUNNING"
        if network == .wifi { baseline = .running; cellular = .notAttempted } else { cellular = .running }
        return id
    }

    public mutating func discovered(run: UUID, interfaces: Int, candidates: Int,
                                   reachable: Int, tcpResult: Check) {
        guard runID == run else { return }
        if interfaces == 0 { vpn = .absent; reason = "VPN_NOT_PRESENT"; return }
        vpn = .interfaceFound
        if candidates == 0 { reason = "VPN_PEER_NOT_FOUND"; return }
        vpn = .peerFound
        // Multiple responders must never select the first response. Multiple
        // non-responders also cannot identify one definitive failed peer.
        if reachable > 1 || (reachable == 0 && candidates > 1) {
            vpn = .ambiguous; reason = "VPN_PEER_AMBIGUOUS"; return
        }
        tcp = tcpResult
        if reachable == 1 && tcpResult == .pass { vpn = .reachable; return }
        switch tcpResult {
        case .refused: vpn = .refused
        case .timeout: vpn = .timedOut
        default: vpn = .failed
        }
        reason = vpn.rawValue
    }

    public mutating func lockdownCompleted(run: UUID, accepted: Bool, rejected: Bool) {
        guard runID == run, tcp == .pass, vpn == .reachable else { return }
        lockdown = accepted ? .pass : .fail
        if accepted { pairing = .accepted }
        else if rejected { pairing = .rejected; reason = "PAIRING_LOCKDOWN_REJECTED" }
        else { reason = "LOCKDOWN_FAILED_PAIRING_UNVERIFIED" }
    }

    public mutating func transportCompleted(run: UUID, coreDevicePassed: Bool, rsdPassed: Bool) {
        guard runID == run, lockdown == .pass else { return }
        coreDevice = coreDevicePassed ? .pass : .fail
        rsd = coreDevicePassed ? (rsdPassed ? .pass : .fail) : .notAttempted
        if !coreDevicePassed { reason = "COREDEVICE_FAILED" }
        else if !rsdPassed { reason = "RSD_FAILED" }
    }

    // Check configuration before attempting the cellular transport. Check the
    // authenticated device identity again at completion before comparing results.
    public func matchesBaseline(_ identity: Identity) -> Bool { baselineIdentity == identity }

    public mutating func finish(run: UUID, identity: Identity?, networkAfter: Network) {
        guard runID == run else { return }
        defer { runID = nil }
        let stable = !interrupted && networkAfter == runNetwork && networkAfter != .unknown
        let passed = stable && pairingReady && tcp == .pass && lockdown == .pass &&
            coreDevice == .pass && rsd == .pass && identity != nil
        if !stable { reason = "NETWORK_CHANGED_OR_INTERRUPTED"; invalidateBaseline() }
        if runNetwork == .wifi {
            baseline = passed ? .pass : .fail
            baselineIdentity = passed ? identity : nil
            continuationArmed = passed
            if passed { reason = "WIFI_BASELINE_PASS_DISABLE_WIFI_IN_SETTINGS" }
        } else {
            let matches = identity != nil && identity == baselineIdentity
            cellular = passed && matches ? .pass : .fail
            if identity != nil && !matches {
                reason = "BASELINE_MISMATCH_WIFI_REQUIRED"
                invalidateBaseline()
            } else if passed { reason = "CELLULAR_READ_ONLY_CHAIN_PASS" }
        }
    }

    public mutating func invalidateBaseline() {
        baselineIdentity = nil; baseline = .notAttempted; continuationArmed = false
    }

    public mutating func stop(run: UUID, because cause: StopReason) {
        guard runID == run else { return }
        runID = nil
        reason = cause.rawValue
        if baseline == .running { baseline = .notAttempted }
        if cellular == .running { cellular = .notAttempted }
        if cause == .environmentChanged || cause == .interrupted { invalidateBaseline() }
    }

    // Deliberate allowlist: never reflect request objects, pairing or identities.
    public var report: String {
        ["pairing=\(pairing.rawValue)", "pairing_ready=\(pairingReady)",
         "vpn=\(vpn.rawValue)", "network=\(network.rawValue)",
         "tcp=\(tcp.rawValue)", "lockdown=\(lockdown.rawValue)",
         "coredevice=\(coreDevice.rawValue)", "rsd=\(rsd.rawValue)",
         "wifi_baseline=\(baseline.rawValue)", "cellular=\(cellular.rawValue)",
         "reason=\(reason)"].joined(separator: "\n")
    }
}
