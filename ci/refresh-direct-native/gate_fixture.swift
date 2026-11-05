import Foundation
// V3_REFRESH_EXECUTION_GATE_V1: one in-process execution owner across the
// external AppIntent and the admitted host XPC path. MainActor owns this value.
struct V3RefreshExecutionTicket: Equatable, Sendable {
    let id: UUID
    let hostRunID: String?
    let hostAdmissionID: String?
}

struct V3RefreshExecutionGate {
    private(set) var current: V3RefreshExecutionTicket?
    private var consumedHostAdmissionID: String?
    private var consumedHostRunID: String?
    var isActive: Bool { current != nil }
    var isExternalActive: Bool { current != nil && current?.hostRunID == nil }

    mutating func acquire(hostRunID: String?, hostAdmissionID: String? = nil, hostLeaseActive: Bool) -> V3RefreshExecutionTicket? {
        guard current == nil else { return nil }
        if let hostRunID {
            guard hostLeaseActive, UUID(uuidString: hostRunID)?.uuidString == hostRunID,
                  let hostAdmissionID, UUID(uuidString: hostAdmissionID)?.uuidString == hostAdmissionID,
                  consumedHostAdmissionID != hostAdmissionID else { return nil }
        } else {
            guard !hostLeaseActive, hostAdmissionID == nil else { return nil }
        }
        let ticket = V3RefreshExecutionTicket(id: UUID(), hostRunID: hostRunID, hostAdmissionID: hostAdmissionID)
        current = ticket
        if let hostAdmissionID {
            consumedHostAdmissionID = hostAdmissionID
            consumedHostRunID = hostRunID
        }
        return ticket
    }

    func hasDispatched(hostRunID: String, hostAdmissionID: String) -> Bool {
        consumedHostAdmissionID == hostAdmissionID && consumedHostRunID == hostRunID
    }

    func owns(_ ticket: V3RefreshExecutionTicket) -> Bool { current == ticket }

    mutating func release(_ ticket: V3RefreshExecutionTicket) {
        guard current == ticket else { return }
        current = nil
    }
}

import Foundation

@main struct GateCases {
    static func main() {
        let run = UUID().uuidString
        let admission = UUID().uuidString
        var gate = V3RefreshExecutionGate()
        precondition(gate.acquire(hostRunID: run, hostAdmissionID: admission, hostLeaseActive: false) == nil)
        precondition(gate.acquire(hostRunID: "not-a-uuid", hostLeaseActive: true) == nil)
        precondition(gate.acquire(hostRunID: nil, hostLeaseActive: true) == nil)
        let external = gate.acquire(hostRunID: nil, hostLeaseActive: false)!
        precondition(gate.isActive && gate.isExternalActive)
        precondition(gate.acquire(hostRunID: run, hostAdmissionID: admission, hostLeaseActive: true) == nil)
        precondition(gate.acquire(hostRunID: nil, hostLeaseActive: false) == nil)
        gate.release(V3RefreshExecutionTicket(id: UUID(), hostRunID: nil, hostAdmissionID: nil))
        precondition(gate.owns(external))
        gate.release(external)
        let host = gate.acquire(hostRunID: run, hostAdmissionID: admission, hostLeaseActive: true)!
        precondition(gate.isActive && !gate.isExternalActive)
        precondition(gate.hasDispatched(hostRunID: run, hostAdmissionID: admission))
        precondition(!gate.hasDispatched(hostRunID: UUID().uuidString, hostAdmissionID: admission))
        precondition(gate.acquire(hostRunID: nil, hostLeaseActive: false) == nil)
        precondition(gate.acquire(hostRunID: run, hostAdmissionID: admission, hostLeaseActive: true) == nil)
        gate.release(external)
        precondition(gate.owns(host))
        gate.release(host)
        precondition(!gate.isActive)
        precondition(gate.acquire(hostRunID: run, hostAdmissionID: admission, hostLeaseActive: true) == nil)
        // A genuinely new admission allows the existing safe same-run VPN retry.
        let retry = gate.acquire(hostRunID: run, hostAdmissionID: UUID().uuidString, hostLeaseActive: true)!
        gate.release(retry)
        let successor = gate.acquire(hostRunID: UUID().uuidString, hostAdmissionID: UUID().uuidString, hostLeaseActive: true)!
        gate.release(host)
        precondition(gate.owns(successor))
        gate.release(successor)
        print("PASS: host/external overlap, malformed admission, duplicate and stale release controls")
    }
}
