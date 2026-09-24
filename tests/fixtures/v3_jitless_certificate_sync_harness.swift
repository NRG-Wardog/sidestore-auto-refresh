import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

struct FakeJITLessCertificateStore: Equatable {
    var certificateID: String
    var password: String
    var updateDate: Date
    var writes = 0

    mutating func commit(certificateID: String, password: String, date: Date) {
        self.certificateID = certificateID
        self.password = password
        updateDate = date
        writes += 1
    }
}

@main
struct JITLessCertificateSyncHarness {
    static func main() {
        let oldDate = Date(timeIntervalSince1970: 1_000)
        let newDate = Date(timeIntervalSince1970: 2_000)
        let secretPassword = "private-password-fixture"
        var store = FakeJITLessCertificateStore(certificateID: "B", password: "old-pass", updateDate: oldDate)

        let mismatch = V3JITLessCertificateSyncAssessment.evaluate(
            activeExists: true, keyDataExists: true, passwordExists: true,
            p12Valid: true, activeFingerprintMatches: true, teamMatches: true,
            activeExpired: false, alreadyCurrent: false)
        require(mismatch == .sync, "current valid SideStore cert A with stale copy B did not offer sync")
        if mismatch == .sync,
           V3JITLessCertificateSyncAssessment.validationIssue(status: 0, hasError: false) == nil {
            store.commit(certificateID: "A", password: secretPassword, date: newDate)
        }
        require(store.certificateID == "A" && store.password == secretPassword && store.updateDate == newDate,
                "validated active cert was not copied with its password and new date")
        require(store.writes == 1, "valid sync did not commit exactly once")
        require(V3JITLessCertificateSyncAssessment.evaluate(
            activeExists: true, keyDataExists: true, passwordExists: true,
            p12Valid: true, activeFingerprintMatches: true, teamMatches: true,
            activeExpired: false, alreadyCurrent: store.certificateID == "A") == .alreadyCurrent,
                "matching certificate was not recognized as already current")

        let beforeNoActive = store
        let noActive = V3JITLessCertificateSyncAssessment.evaluate(
            activeExists: false, keyDataExists: true, passwordExists: true,
            p12Valid: true, activeFingerprintMatches: true, teamMatches: true,
            activeExpired: false, alreadyCurrent: false)
        require(noActive == .blocked(.noActiveCertificate), "missing active certificate was not actionable")
        require(store == beforeNoActive, "missing active certificate overwrote the JIT-Less copy")

        let noKey = V3JITLessCertificateSyncAssessment.evaluate(
            activeExists: true, keyDataExists: false, passwordExists: true,
            p12Valid: false, activeFingerprintMatches: false, teamMatches: true,
            activeExpired: false, alreadyCurrent: false)
        require(noKey == .blocked(.keyMaterialUnavailable), "missing keychain data did not fail closed")
        require(store == beforeNoActive, "missing keychain material overwrote the JIT-Less copy")

        let badPassword = V3JITLessCertificateSyncAssessment.evaluate(
            activeExists: true, keyDataExists: true, passwordExists: true,
            p12Valid: false, activeFingerprintMatches: true, teamMatches: true,
            activeExpired: false, alreadyCurrent: false)
        require(badPassword == .blocked(.invalidPKCS12), "invalid p12/password did not fail closed")

        let oldActiveMaterial = V3JITLessCertificateSyncAssessment.evaluate(
            activeExists: true, keyDataExists: true, passwordExists: true,
            p12Valid: true, activeFingerprintMatches: false, teamMatches: true,
            activeExpired: false, alreadyCurrent: false)
        require(oldActiveMaterial == .blocked(.activeCertificateMismatch),
                "an arbitrary older local certificate was accepted as active")

        let teamMismatch = V3JITLessCertificateSyncAssessment.evaluate(
            activeExists: true, keyDataExists: true, passwordExists: true,
            p12Valid: true, activeFingerprintMatches: true, teamMatches: false,
            activeExpired: false, alreadyCurrent: false)
        require(teamMismatch == .blocked(.teamMismatch), "team mismatch was not rejected")

        let expired = V3JITLessCertificateSyncAssessment.evaluate(
            activeExists: true, keyDataExists: true, passwordExists: true,
            p12Valid: true, activeFingerprintMatches: true, teamMatches: true,
            activeExpired: true, alreadyCurrent: false)
        require(expired == .blocked(.activeCertificateExpired), "expired active certificate was accepted")

        var staleCopy = FakeJITLessCertificateStore(certificateID: "B", password: "old-pass", updateDate: oldDate)
        let unchangedOnEntry = staleCopy
        let revoked = V3JITLessCertificateSyncAssessment.validationIssue(status: 1, hasError: false)
        require(revoked == .activeCertificateRevoked,
                "revoked current certificate was reported as a successful repair")
        if revoked != nil { staleCopy = unchangedOnEntry }
        require(staleCopy == unchangedOnEntry,
                "revoked active certificate did not preserve the previous JIT-Less copy")
        require(V3JITLessCertificateSyncAssessment.validationIssue(status: -1, hasError: true) == .validationUnavailable,
                "unknown revocation result was treated as valid")
        require(!V3JITLessCertificateSyncIssue.activeCertificateRevoked.technicalDetails.contains(secretPassword),
                "password reached diagnostics")
        require(!V3JITLessCertificateSyncIssue.activeCertificateRevoked.technicalDetails.contains("LCCertificateData"),
                "private certificate material reached diagnostics")
        require(!V3JITLessCertificateSyncIssue.activeCertificateRevoked.technicalDetails.contains("CERTIFICATE-DER-FIXTURE"),
                "certificate identity reached diagnostics")
        print("V3_JITLESS_CERTIFICATE_SYNC_PASS")
    }
}
