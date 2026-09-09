import Foundation

let identity = CellularBootstrapState.Identity(build: "test", pairingDigest: Data([1]),
    deviceDigest: Data([2]), vpnDigest: Data([3]), resolver: "test")
let changed = CellularBootstrapState.Identity(build: "test", pairingDigest: Data([1]),
    deviceDigest: Data([2]), vpnDigest: Data([4]), resolver: "test")

func prepared(_ network: CellularBootstrapState.Network = .wifi) -> CellularBootstrapState {
    var state = CellularBootstrapState()
    state.pairingLoaded(present: true, parsed: true)
    state.updateNetwork(network)
    return state
}

func succeed(_ state: inout CellularBootstrapState, run: UUID) {
    state.discovered(run: run, interfaces: 1, candidates: 1, reachable: 1, tcpResult: .pass)
    state.lockdownCompleted(run: run, accepted: true, rejected: false)
    state.transportCompleted(run: run, coreDevicePassed: true, rsdPassed: true)
}

var missing = CellularBootstrapState()
assert(missing.nextAction() == .importPairing)
assert(missing.begin() == nil)
missing.pairingLoaded(present: true, parsed: false)
assert(missing.pairing == .parseFailed && !missing.pairingReady)

var noBaseline = prepared(.cellular)
assert(noBaseline.nextAction() == .enableWiFi && noBaseline.begin() == nil)

for failure: CellularBootstrapState.Check in [.refused, .timeout, .fail] {
    var state = prepared()
    let run = state.begin()!
    assert(state.begin() == nil)
    state.discovered(run: run, interfaces: 1, candidates: 1, reachable: 0, tcpResult: failure)
    state.lockdownCompleted(run: run, accepted: true, rejected: true)
    state.transportCompleted(run: run, coreDevicePassed: true, rsdPassed: true)
    state.finish(run: run, identity: nil, networkAfter: .wifi)
    assert(state.tcp == failure && state.lockdown == .notAttempted)
    assert(state.pairing == .parsed && !state.pairingReady)
    assert(state.coreDevice == .notAttempted && state.rsd == .notAttempted)
}

var rejected = prepared()
let rejectedRun = rejected.begin()!
rejected.discovered(run: rejectedRun, interfaces: 1, candidates: 1, reachable: 1, tcpResult: .pass)
rejected.lockdownCompleted(run: rejectedRun, accepted: false, rejected: true)
rejected.transportCompleted(run: rejectedRun, coreDevicePassed: true, rsdPassed: true)
assert(rejected.pairing == .rejected && rejected.coreDevice == .notAttempted)

for responders in [0, 2] {
    var ambiguous = prepared()
    let run = ambiguous.begin()!
    ambiguous.discovered(run: run, interfaces: 2, candidates: 2, reachable: responders, tcpResult: .pass)
    ambiguous.lockdownCompleted(run: run, accepted: true, rejected: false)
    assert(ambiguous.vpn == .ambiguous && ambiguous.lockdown == .notAttempted)
}

var baseline = prepared()
let first = baseline.begin()!
succeed(&baseline, run: first)
baseline.finish(run: first, identity: identity, networkAfter: .wifi)
assert(baseline.baseline == .pass && baseline.matchesBaseline(identity))
assert(baseline.becameActive(network: .wifi) == .none)
assert(baseline.becameActive(network: .cellular) == .runCellular)
assert(baseline.becameActive(network: .cellular) == .none)
let cellular = baseline.begin()!
succeed(&baseline, run: cellular)
baseline.finish(run: cellular, identity: identity, networkAfter: .cellular)
assert(baseline.cellular == .pass)

// TCP refusal in a matching cellular attempt must retain the first failure.
let refusedCellular = baseline.begin()!
baseline.discovered(run: refusedCellular, interfaces: 1, candidates: 1, reachable: 0, tcpResult: .refused)
baseline.finish(run: refusedCellular, identity: identity, networkAfter: .cellular)
assert(baseline.reason == "VPN_PEER_TCP_REFUSED" && baseline.pairing == .parsed)

let wrongVPN = baseline.begin()!
succeed(&baseline, run: wrongVPN)
baseline.finish(run: wrongVPN, identity: changed, networkAfter: .cellular)
assert(baseline.cellular == .fail && baseline.baseline == .notAttempted)
assert(baseline.nextAction() == .enableWiFi)

var interrupted = prepared()
let interruptedRun = interrupted.begin()!
succeed(&interrupted, run: interruptedRun)
interrupted.resignedActive()
interrupted.finish(run: interruptedRun, identity: identity, networkAfter: .wifi)
assert(interrupted.baseline != .pass && !interrupted.matchesBaseline(identity))

var stale = prepared()
let staleRun = stale.begin()!
succeed(&stale, run: UUID())
assert(stale.lockdown == .notAttempted)
stale.finish(run: UUID(), identity: identity, networkAfter: .wifi)
assert(stale.runID == staleRun)

for forbidden in ["pairingDigest", "deviceDigest", "vpnDigest", "HostPrivateKey", "EscrowBag"] {
    assert(!baseline.report.contains(forbidden))
}
print("CELLULAR_BOOTSTRAP_POLICY_TESTS_PASS")
