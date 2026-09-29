import Foundation

struct BatchOwner {
    var ticket: UInt64 = 0
    var stamp: String? = "process:1"
    var stable = true
    var rows: [String] = []
    mutating func invalidate() {
        ticket &+= 1
        rows = []
    }
    mutating func commit(ticket: UInt64, stamp: String, results: [String?]) {
        guard V3AuthReadStampPolicy.mayCommit(capturedTicket: ticket,
            currentTicket: self.ticket, capturedStamp: stamp, currentStamp: self.stamp,
            stable: stable, resultStamps: results) else { return }
        rows = ["committed"]
    }
}

@main
enum V3AuthReadStampHarness {
    static func main() {
        let a = "opaque-process-nonce:4"
        let b = "opaque-process-nonce:6"
        let fiveA: [String?] = [a, a, a, a, a]
        precondition(V3AuthReadStampPolicy.mayCommit(capturedTicket: 1, currentTicket: 1,
            capturedStamp: a, currentStamp: a, stable: true, resultStamps: fiveA),
            "a same-owner five-response batch commits")
        precondition(!V3AuthReadStampPolicy.mayCommit(capturedTicket: 1, currentTicket: 1,
            capturedStamp: a, currentStamp: b, stable: true, resultStamps: fiveA),
            "A replies cannot commit after an A to B switch while authenticated remains true")
        precondition(!V3AuthReadStampPolicy.mayCommit(capturedTicket: 1, currentTicket: 1,
            capturedStamp: a, currentStamp: a, stable: true, resultStamps: [a, b, a, a, a]),
            "a mixed five-view batch is rejected")
        precondition(!V3AuthReadStampPolicy.mayCommit(capturedTicket: 1, currentTicket: 1,
            capturedStamp: a, currentStamp: a, stable: false, resultStamps: fiveA),
            "sign-out or account replacement during an API await invalidates the read")
        precondition(!V3AuthReadStampPolicy.mayReturn(capturedStamp: a,
            currentStamp: b, stable: true), "stale Developer Portal data is rejected after await")
        precondition(!V3AuthReadStampPolicy.mayReturn(capturedStamp: a,
            currentStamp: a, stable: false), "portal reads cannot return during transition")

        var host = BatchOwner()
        let oldTicket = host.ticket
        host.stamp = a
        host.commit(ticket: oldTicket, stamp: a, results: fiveA)
        precondition(host.rows.isEmpty, "host identity change after replies and before commit rejects them")

        host.stamp = a
        let overlappingTicket = host.ticket
        host.invalidate() // Newer reload owns the ticket and old defer/result is stale.
        host.commit(ticket: overlappingTicket, stamp: a, results: fiveA)
        precondition(host.rows.isEmpty, "overlapping reload or dismiss prevents an old commit")

        // A failed B attempt ends in a new stable A-owned stamp; old A reads
        // stay invalid, while subsequent A reads can commit normally.
        let afterFailedB = "opaque-process-nonce:8"
        precondition(!V3AuthReadStampPolicy.mayCommit(capturedTicket: 2, currentTicket: 2,
            capturedStamp: a, currentStamp: afterFailedB, stable: true, resultStamps: fiveA))
        precondition(V3AuthReadStampPolicy.mayCommit(capturedTicket: 3, currentTicket: 3,
            capturedStamp: afterFailedB, currentStamp: afterFailedB, stable: true,
            resultStamps: Array(repeating: afterFailedB, count: 5)),
            "failed B sign-in can leave A valid under a fresh stamp")

        // Team selection is not an account identity transition: concurrent
        // reads for multiple teams under the same owner retain one stamp.
        precondition(V3AuthReadStampPolicy.mayCommit(capturedTicket: 4, currentTicket: 4,
            capturedStamp: b, currentStamp: b, stable: true, resultStamps: Array(repeating: b, count: 5)))
        let plistData = try! PropertyListSerialization.data(fromPropertyList: ["identityStamp": a],
            format: .binary, options: 0)
        let decoded = try! PropertyListSerialization.propertyList(from: plistData, format: nil) as! [String: String]
        precondition(decoded["identityStamp"] == a && !a.contains("@"),
            "wire stamp is property-list safe and contains no account address")
        print("V3_AUTH_READ_STAMP_PASS")
    }
}
