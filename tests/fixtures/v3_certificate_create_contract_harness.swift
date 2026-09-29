struct FakeCertificate {
    let serial: String
}

enum FakePortalError: Error {
    case unavailable
}

@main
struct V3CertificateCreateContractHarness {
    static func main() async throws {
        var portalCalls = 0
        var saves = 0
        let activeSerial = "preexisting-active"
        var storedSerial: String?
        let created = FakeCertificate(serial: "new-certificate")

        let success = try await V3CertificateCreateAdapter.createAndPersist(
            create: {
                portalCalls += 1
                return created
            },
            persist: { certificate in
                saves += 1
                storedSerial = certificate.serial
            },
            verifyStored: { certificate in
                V3CertificateCreateAdapter.matchesCreatedSerial(
                    expected: certificate.serial, parsed: storedSerial)
            })

        precondition(portalCalls == 1, "the portal create request must run once")
        precondition(saves == 1, "the upstream persistence method must run once")
        precondition(success == .createdAndStored)
        precondition(activeSerial == "preexisting-active",
                     "create must not mutate active certificate ownership")
        precondition(V3CertificateCreatePresentation.isVerified(success.rawValue))
        precondition(V3CertificateCreatePresentation.message(for: success.rawValue) ==
                     "Certificate created and saved.")
        precondition(!success.rawValue.contains(created.serial),
                     "the wire outcome must not expose certificate identifiers")

        let partial = try await V3CertificateCreateAdapter.createAndPersist(
            create: { created },
            persist: { _ in },
            verifyStored: { _ in
                V3CertificateCreateAdapter.matchesCreatedSerial(
                    expected: "new-certificate", parsed: nil)
            })
        precondition(partial == .remoteCreatedLocalStorageUnverified)
        precondition(!V3CertificateCreatePresentation.isVerified(partial.rawValue))
        let partialMessage = V3CertificateCreatePresentation.message(for: partial.rawValue)
        precondition(partialMessage.contains("could not be verified"))
        precondition(partialMessage.contains("before creating another certificate"))
        precondition(partialMessage != V3CertificateCreatePresentation.message(for: success.rawValue),
                     "partial persistence must never show the success message")
        precondition(!partialMessage.lowercased().contains("activated"),
                     "create must not imply the new certificate was activated")
        precondition(!partial.rawValue.contains(created.serial))

        var failedPortalSaves = 0
        do {
            _ = try await V3CertificateCreateAdapter.createAndPersist(
                create: { () async throws -> FakeCertificate in
                    portalCalls += 1
                    throw FakePortalError.unavailable
                },
                persist: { _ in failedPortalSaves += 1 },
                verifyStored: { _ in true })
            preconditionFailure("portal errors must propagate")
        } catch FakePortalError.unavailable {
            precondition(failedPortalSaves == 0,
                         "a failed portal request must not write local certificate state")
        }
        precondition(portalCalls == 2)

        precondition(V3CertificateCreateAdapter.matchesCreatedSerial(
            expected: "new-certificate", parsed: "new-certificate"))
        precondition(!V3CertificateCreateAdapter.matchesCreatedSerial(
            expected: "new-certificate", parsed: "different-certificate"))
        precondition(!V3CertificateCreateAdapter.matchesCreatedSerial(
            expected: "new-certificate", parsed: nil))

        let unknownMessage = V3CertificateCreatePresentation.message(for: nil)
        precondition(unknownMessage.contains("could not be confirmed"))
        precondition(!V3CertificateCreatePresentation.isVerified(nil))
        print("certificate create contract passed")
    }
}
