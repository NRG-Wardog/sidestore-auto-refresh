
// Only storage IO and transport are doubled. The gate, storage-failure helper,
// direct not-dispatched annotation and remote-boundary guard are production code.
final class Keychain {
    enum State { case verified, pending, inaccessible }
    static let shared = Keychain()
    var state = State.verified
    var reads = 0
    func storageRequiresReconciliation() throws -> Bool {
        reads += 1
        switch state {
        case .verified: return false
        case .pending: return true
        case .inaccessible: throw NSError(domain: NSOSStatusErrorDomain, code: -34018)
        }
    }
}
enum V3BackendCommands {
    static let boolSettings: Set<String> = []
    static let intSettings: Set<String> = []
    static let stringSettings: Set<String> = []
}
private enum V3DirectMutationRecoveryRecord {
    static let allowedOperations: Set<String> = ["certCreate", "certRevoke", "sourceAddConfirmed",
        "sourceRemoveConfirmed", "pairingImportData", "settingsSet", "accountImport"]
    __PRODUCTION_ELIGIBILITY__
}
private enum V3DirectMutationPreDispatchReplyPolicy {
    __PRODUCTION_ANNOTATE__
}
private final class Service {
    var response: Data?
    var reservations = 0
    var remoteMutations = 0
    __PRODUCTION_HELPER__
    func encode(_ response: [String: Any], operation: String) -> Data {
        try! PropertyListSerialization.data(fromPropertyList: response, format: .binary, options: 0)
    }
    func reply(_ response: Data) { self.response = response }
    func receive(_ operation: String, id: String) {
        let request: [String: Any] = ["version": 1, "operation": operation, "id": id, "target": ""]
        __PRODUCTION_GATE__
        reservations += 1
        remoteMutations += 1
    }
    func storageChangedAfterAccountLookup(_ operation: String, id: String) throws {
        // Dispatch already owns its journal. Throwing the production guard must
        // not turn this into a predispatch reply or release that owner.
        reservations += 1
        Keychain.shared.state = .pending
        __PRODUCTION_DISPATCH_GUARD__
        remoteMutations += 1
    }
}

@main
private struct CertificateStorageHarness {
    static func main() throws {
        precondition(!V3AccountDatabaseRecovery.requiresReconciliation,
            "test requires an isolated runner with no existing account journal")
        for operation in ["certCreate", "certRevoke"] {
            for state in [Keychain.State.pending, .inaccessible] {
                Keychain.shared.state = state
                let service = Service(); let id = UUID().uuidString
                service.receive(operation, id: id)
                precondition(service.reservations == 0 && service.remoteMutations == 0)
                let wire = try PropertyListSerialization.propertyList(from: service.response!, format: nil) as! [String: Any]
                precondition(wire["operationNotDispatched"] as? Bool == true)
                precondition(wire["id"] as? String == id)
                let failure = CombinedFailure.decode(wire["failure"] as! [String: Any], expectedID: id)!
                precondition(failure.stage == .persistence && failure.code == .notReady)
                precondition(failure.safeCause == .signingStorageUnverified && failure.retryable == false)
                precondition(failure.recovery.contains("Check Saved Signing State"))
            }
            Keychain.shared.state = .verified
            try V3AccountDatabaseRecovery.begin(previous: ["account:fixture-prior"], intended: ["account:fixture-intended"])
            let blocked = Service()
            blocked.receive(operation, id: UUID().uuidString)
            precondition(blocked.reservations == 0 && blocked.remoteMutations == 0)
            precondition(V3AccountDatabaseRecovery.requiresReconciliation,
                "refusal cannot clear an existing local uncertainty journal")
            // Local reconciliation remains admissible while both stores block
            // remote certificate changes; it performs no certificate request.
            Keychain.shared.state = .pending; Keychain.shared.reads = 0
            let local = Service()
            local.receive("authReconcileStorage", id: UUID().uuidString)
            precondition(local.response == nil && Keychain.shared.reads == 0)
            try V3AccountDatabaseRecovery.reconcile(observed: ["account:fixture-prior"])
            Keychain.shared.state = .verified
            let allowed = Service()
            allowed.receive(operation, id: UUID().uuidString)
            precondition(allowed.response == nil && allowed.reservations == 1 && allowed.remoteMutations == 1)
            let raced = Service()
            do {
                try raced.storageChangedAfterAccountLookup(operation, id: UUID().uuidString)
                preconditionFailure("late storage uncertainty reached Apple")
            } catch let failure as CombinedFailure {
                precondition(failure.safeCause == .signingStorageUnverified)
            }
            precondition(raced.reservations == 1 && raced.remoteMutations == 0 && raced.response == nil,
                "late refusal must preserve already-dispatched recovery ownership")
        }
        Keychain.shared.state = .verified
        print("V3_CERTIFICATE_STORAGE_ADMISSION_PASS")
    }
}
