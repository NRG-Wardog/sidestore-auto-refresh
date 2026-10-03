import Foundation
import Security
import CryptoKit

enum V3JITLessTestEnvironment {
    static let osMajor = 26
}

enum NativeCertificateObservation {
    static var data: Data?
    static var password: String?
    static var facts: [String: String]?
    static var validationStatus = 0
    static var validationError: String?
    static var factsCalls = 0
    static var validationCalls = 0

    static func reset(data: Data?, password: String?, facts: [String: String]?,
                      validationStatus: Int = 0, validationError: String? = nil) {
        self.data = data
        self.password = password
        self.facts = facts
        self.validationStatus = validationStatus
        self.validationError = validationError
        factsCalls = 0
        validationCalls = 0
    }
}

// This mocks the existing LCUtils native boundary, not its C++ parser. In
// particular, opaqueDataSecurityRejects is accepted only because this test
// fixture explicitly provides canonical facts; it does not prove a real ZSign
// PKCS#12 acceptance or a physical-device parser discrepancy.
enum LCUtils {
    static func certificateData() -> Data? { NativeCertificateObservation.data }

    static func certificateFacts(withKeyData data: Data,
                                 password: String) -> [String: String]? {
        NativeCertificateObservation.factsCalls += 1
        guard data == NativeCertificateObservation.data,
              password == NativeCertificateObservation.password else { return nil }
        return NativeCertificateObservation.facts
    }

    // The 4ef8 reader calls this old API after Security PKCS#12 import.
    static func getCertTeamId(withKeyData data: Data, password: String) -> String? {
        guard data == NativeCertificateObservation.data,
              password == NativeCertificateObservation.password else { return nil }
        return NativeCertificateObservation.facts?["teamIdentifier"]
    }

    static func validateCertificate(
        _ completionHandler: (Int, Date?, String?, String?) -> Void
    ) -> Int {
        NativeCertificateObservation.validationCalls += 1
        completionHandler(NativeCertificateObservation.validationStatus,
                          nil, nil, NativeCertificateObservation.validationError)
        return 1
    }
}

enum LCSharedUtils {
    static func certificatePassword() -> String? { NativeCertificateObservation.password }
}

// {{PRODUCTION_READINESS_SLICES}}

@main
enum V3JITLessNativeReadinessHarness {
    static let opaqueDataSecurityRejects = Data("native-boundary-accepted-fixture".utf8)
    static let password = "fixture-password"
    static let team = "TEAM42"
    static let activeFingerprint = String(repeating: "a", count: 64)
    static let otherFingerprint = String(repeating: "b", count: 64)

    static func service(activeFingerprint: String = V3JITLessNativeReadinessHarness.activeFingerprint,
                        active: Bool = true,
                        validation: String = "valid") -> [String: Any] {
        ["active": active,
         "validation": validation,
         "certificateIdentitySHA256": activeFingerprint]
    }

    static func canonicalFacts(
        _ fingerprint: String = V3JITLessNativeReadinessHarness.activeFingerprint,
        team: String = V3JITLessNativeReadinessHarness.team
    ) -> [String: String] {
        ["teamIdentifier": team, "identitySHA256": fingerprint]
    }

    static func main() async {
        // New reader: a copy accepted by LC's native facts API reaches the real
        // production identity/policy path and compares the certificate DER hash.
        NativeCertificateObservation.reset(data: opaqueDataSecurityRejects,
            password: password, facts: canonicalFacts(), validationStatus: 0)
        let matching = await V3JITLessStatusReader.read(serviceCertificate: service())
        if matching.readiness != .ready {
            fputs("NATIVE_FACTS_MATCH_DID_NOT_REACH_READY state=\(matching.readiness.rawValue)\n", stderr)
            exit(20)
        }
        precondition(matching.hasImportedCopy &&
                     matching.certificateFacts?.teamIdentifier == team &&
                     matching.certificateFacts?.identitySHA256 == activeFingerprint,
                     "production result carries only the expected public certificate facts")
        precondition(NativeCertificateObservation.factsCalls == 1 &&
                     NativeCertificateObservation.validationCalls == 1,
                     "the production reader calls native facts and validation once each")

        NativeCertificateObservation.reset(data: opaqueDataSecurityRejects,
            password: password, facts: canonicalFacts(otherFingerprint), validationStatus: 0)
        let mismatch = await V3JITLessStatusReader.read(serviceCertificate: service())
        precondition(mismatch.readiness == .certificateMismatch &&
                     V3JITLessPresentation.present(mismatch.readiness).isOutstandingSetupTask &&
                     V3JITLessPresentation.present(mismatch.readiness).title ==
                         "JIT-Less certificate copy is out of date",
                     "a different exact certificate hash remains a refresh-required state")

        NativeCertificateObservation.reset(data: opaqueDataSecurityRejects,
            password: password, facts: canonicalFacts(otherFingerprint), validationStatus: 1)
        let revoked = await V3JITLessStatusReader.read(serviceCertificate: service())
        precondition(revoked.readiness == .revoked &&
                     !V3JITLessCompletionPolicy.isComplete(revoked.readiness),
                     "native revocation status remains an outstanding, truthful failure")

        NativeCertificateObservation.reset(data: nil, password: password, facts: nil,
            validationStatus: 0)
        let missingData = await V3JITLessStatusReader.read(serviceCertificate: service())
        precondition(missingData.readiness == .setupRequired &&
                     !missingData.hasImportedCopy &&
                     NativeCertificateObservation.factsCalls == 0 &&
                     NativeCertificateObservation.validationCalls == 0,
                     "missing stored bytes remain setup-required without parser calls")

        NativeCertificateObservation.reset(data: opaqueDataSecurityRejects,
            password: nil, facts: nil, validationStatus: 0)
        let missingPassword = await V3JITLessStatusReader.read(serviceCertificate: service())
        precondition(missingPassword.readiness == .setupRequired &&
                     missingPassword.hasImportedCopy &&
                     NativeCertificateObservation.factsCalls == 0 &&
                     NativeCertificateObservation.validationCalls == 0,
                     "missing password remains setup-required without parser calls")

        NativeCertificateObservation.reset(data: opaqueDataSecurityRejects,
            password: password, facts: nil, validationStatus: 0)
        let noCanonicalFacts = await V3JITLessStatusReader.read(serviceCertificate: service())
        precondition(noCanonicalFacts.hasImportedCopy &&
                     noCanonicalFacts.certificateFacts == nil &&
                     noCanonicalFacts.readiness == .unknown &&
                     noCanonicalFacts.readiness != .setupRequired,
                     "stored bytes with no native facts are unknown, not missing")

        NativeCertificateObservation.reset(data: opaqueDataSecurityRejects,
            password: password,
            facts: ["teamIdentifier": team, "identitySHA256": "bad-digest"],
            validationStatus: 0)
        let malformedFacts = await V3JITLessStatusReader.read(serviceCertificate: service())
        precondition(malformedFacts.hasImportedCopy &&
                     malformedFacts.certificateFacts == nil &&
                     malformedFacts.readiness == .unknown,
                     "malformed native facts cannot claim readiness or a missing copy")

        print("V3_JITLESS_NATIVE_READINESS_PASS")
    }
}
