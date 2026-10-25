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

final class LockedSessionState: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: String
    private var staleTaskCommitted = true
    init(_ value: String) { storage = value }
    func set(_ value: String) { lock.lock(); storage = value; lock.unlock() }
    func recordStaleCommit(_ value: Bool) { lock.lock(); staleTaskCommitted = value; lock.unlock() }
    func snapshot() -> (session: String, staleCommitted: Bool) {
        lock.lock()
        defer { lock.unlock() }
        return (storage, staleTaskCommitted)
    }
}

@main
enum V3AuthReadStampHarness {
    static func main() {
        let authState = V3AuthIdentityStampState()
        let initialIdentity = authState.snapshot
        authState.beginTransition()
        var route = ["dsid": "A", "token": "A", "appleID": "A", "session": "A"]
        for (field, value) in [("dsid", "B"), ("token", "B"), ("appleID", "B"), ("session", "B")] {
            route[field] = value
            let duringWrite = authState.snapshot
            precondition(!duringWrite.stable &&
                !V3AuthReadStampPolicy.mayReturn(capturedStamp: initialIdentity.stamp,
                    currentStamp: duringWrite.stamp, stable: duringWrite.stable),
                "portal reads remain barred after the intermediate \(field) write")
        }
        authState.completeTransition()
        let committedIdentity = authState.snapshot
        precondition(committedIdentity.stable && committedIdentity.stamp != initialIdentity.stamp &&
            route.values.allSatisfy { $0 == "B" },
            "the new tuple and in-memory session publish under one fresh stable stamp")

        authState.beginTransition()
        authState.beginTransition()
        authState.completeTransition()
        precondition(!authState.snapshot.stable, "nested identity changes cannot publish early")
        authState.completeTransition()
        precondition(authState.snapshot.stable, "the outer identity transaction owns finalization")

        let a = "opaque-process-nonce:4"
        let b = "opaque-process-nonce:6"
        let fiveA: [String?] = [a, a, a, a, a]
        precondition(V3AuthReadStampPolicy.mayCommit(capturedTicket: 1, currentTicket: 1,
            capturedStamp: a, currentStamp: a, stable: true, resultStamps: fiveA),
            "a same-owner five-response batch commits")
        precondition(!V3AuthReadStampPolicy.mayCommit(capturedTicket: 1, currentTicket: 1,
            capturedStamp: a, currentStamp: a, stable: true, resultStamps: fiveA,
            authenticationActive: true),
            "all five account-scoped reads are rejected while authentication is active")
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
        let oldStamp = host.stamp!
        host.stamp = a
        host.commit(ticket: oldTicket, stamp: oldStamp, results: Array(repeating: oldStamp, count: 5))
        precondition(host.rows.isEmpty, "host identity change after replies and before commit rejects them")

        host.stamp = a
        let overlappingTicket = host.ticket
        host.invalidate() // Newer reload owns the ticket and old defer/result is stale.
        host.commit(ticket: overlappingTicket, stamp: a, results: fiveA)
        precondition(host.rows.isEmpty, "overlapping reload or dismiss prevents an old commit")

        var loading = true
        let currentTicket = host.ticket
        let staleIdentityMayFinish = V3AuthReadStampPolicy.ownsTicket(
            captured: currentTicket, current: host.ticket)
        if staleIdentityMayFinish { loading = false }
        precondition(!loading, "current stale-identity return clears its loading indicator")
        loading = true
        if V3AuthReadStampPolicy.ownsTicket(captured: currentTicket, current: host.ticket &+ 1) {
            loading = false
        }
        precondition(loading, "an old request cannot clear a newer request's loading state")

        // A failed B attempt ends in a new stable A-owned stamp; old A reads
        // stay invalid, while subsequent A reads can commit normally.
        let afterFailedB = "opaque-process-nonce:8"
        precondition(!V3AuthReadStampPolicy.mayCommit(capturedTicket: 2, currentTicket: 2,
            capturedStamp: a, currentStamp: afterFailedB, stable: true, resultStamps: fiveA))
        precondition(V3AuthReadStampPolicy.mayCommit(capturedTicket: 3, currentTicket: 3,
            capturedStamp: afterFailedB, currentStamp: afterFailedB, stable: true,
            resultStamps: Array(repeating: afterFailedB, count: 5)),
            "failed B sign-in can leave A valid under a fresh stamp")

        // Barrier ordering for AuthManager.getAuthenticatedSession(): A has
        // read its Keychain tuple and is suspended before Anisette returns.
        // B commits its tuple and session, then A resumes. The conditional
        // production helper must refuse A's late cached-session write.
        let sessionState = V3AuthIdentityStampState()
        let capturedA = sessionState.snapshot
        let aWaiting = DispatchSemaphore(value: 0)
        let releaseA = DispatchSemaphore(value: 0)
        let aFinished = DispatchGroup()
        let cached = LockedSessionState("A")
        aFinished.enter()
        DispatchQueue.global().async {
            aWaiting.signal()
            releaseA.wait()
            let committed = sessionState.runIfCurrent(capturedA.stamp) {
                cached.set("A")
            }
            cached.recordStaleCommit(committed)
            aFinished.leave()
        }
        aWaiting.wait()
        sessionState.beginTransition()
        cached.set("B")
        sessionState.completeTransition()
        let capturedB = sessionState.snapshot
        precondition(V3AuthSessionCoalescerKey.value(for: capturedA.stamp) !=
                     V3AuthSessionCoalescerKey.value(for: capturedB.stamp),
            "B never joins A's in-flight session coalescer task")
        releaseA.signal()
        precondition(aFinished.wait(timeout: .now() + 5) == .success,
            "the late A session task reaches its guarded commit")
        let afterLateA = cached.snapshot()
        let sessionAfterLateA = afterLateA.session
        let lateACommitted = afterLateA.staleCommitted
        precondition(!lateACommitted && sessionAfterLateA == "B",
            "late A completion cannot overwrite B's cached session")
        var portalCalls = 0
        let afterBCommit = sessionState.snapshot
        if V3AuthReadStampPolicy.mayReturn(capturedStamp: capturedA.stamp,
            currentStamp: afterBCommit.stamp, stable: afterBCommit.stable) {
            portalCalls += 1
        }
        precondition(portalCalls == 0, "no A request dispatches under B's route")
        if V3AuthReadStampPolicy.mayReturn(capturedStamp: capturedB.stamp,
            currentStamp: afterBCommit.stamp, stable: afterBCommit.stable) {
            portalCalls += 1
        }
        precondition(portalCalls == 1, "a current B request can dispatch")
        precondition(sessionState.runIfCurrent(capturedB.stamp) {
            cached.set("B-retry")
        }, "B can retry successfully after A's stale coalescer task drains")

        let beforeSignOut = sessionState.snapshot
        sessionState.beginTransition()
        cached.set("signed-out")
        sessionState.completeTransition()
        precondition(!sessionState.runIfCurrent(beforeSignOut.stamp) {
            cached.set("B")
        }, "sign-out rejects an outstanding authenticated session result")

        let beforeTokenRotation = sessionState.snapshot
        sessionState.beginTransition()
        cached.set("B-token-2")
        sessionState.completeTransition()
        precondition(!sessionState.runIfCurrent(beforeTokenRotation.stamp) {
            cached.set("B-token-1")
        }, "same-DSID token rotation rejects the older token session")

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
