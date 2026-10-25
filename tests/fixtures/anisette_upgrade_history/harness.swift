// Historical functions are inserted verbatim from the hash-pinned minimal
// slices. Only dependencies outside the exercised Anisette key path are doubles.
enum LegacyAnisetteStorage {
    static let installedGroup: String? = Store.keychainGroup
    static let service = "com.kdt.livecontainer"
    static func note(_ key: String, status: Int) {}
    // INSERT_LEGACY_READ
    // INSERT_LEGACY_READ_STRING
    // INSERT_LEGACY_WRITE
}
enum MidAnisetteStorage {
    static let installedGroup: String? = Store.keychainGroup
    static let service = "com.kdt.livecontainer"
    private static let authenticationJournal = "LCSharedAuthenticationTransactionV1"
    private static let certificateJournal = "LCSharedCertificateTransactionV1"
    static func note(_ key: String, status: Int) {}
    private static func withSharedTransaction<T>(_ operation: () throws -> T) rethrows -> T { try operation() }
    private static func authenticationValuesLocked(_ client: KeychainAccess.Keychain) -> [String: Data]? {
        preconditionFailure("historical Anisette reproduction must not access Apple credentials")
    }
    private static func readSigningCertificateSnapshot(_ client: KeychainAccess.Keychain) -> LCEmbeddedSigningCertificateSnapshot? {
        preconditionFailure("historical Anisette reproduction must not access certificates")
    }
    // INSERT_MID_READ
    // INSERT_LEGACY_READ_STRING
    // INSERT_MID_WRITE
    // INSERT_MID_WRITE_ONE
}
struct HistoricalAnisetteResolver {
    let read: () -> String?
    let write: (String) -> Void
    var anisetteIdentifier: String? {
        get { read() }
        nonmutating set { if let newValue { write(newValue) } }
    }
    // INSERT_RESOLVER
}
@main struct HistoricalAnisetteUpgradeHarness {
    static func main() async throws {
        LCEmbeddedSharedKeychain.transactionOverride = { try $0() }
        let selected = Store.keychainGroup, legacy = Store.processGroup
        let a = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
        let blobA = Data("synthetic-blob-owned-by-A".utf8)
        let rawBlobA = Data(blobA.base64EncodedString().utf8)
        Store.data = [legacy: ["identifier": Data(a.uuidString.utf8)]]
        let client = LCEmbeddedSharedKeychain.makeClient()
        let oldResolver = HistoricalAnisetteResolver(
            read: { LegacyAnisetteStorage.readString("identifier", client: client) },
            write: { LegacyAnisetteStorage.write("identifier", data: Data($0.utf8), client: client) })
        precondition(oldResolver.resolveDeviceIdentifier() == a)
        precondition(Store.data[selected]?["identifier"] == nil)
        // Exact old setter path for the synthetic provider result. It persists
        // the blob in selected storage, not the borrowed legacy identifier.
        LegacyAnisetteStorage.write("adiPb", data: rawBlobA, client: client)
        let orphaned = Store.data
        precondition(Store.data[selected]?["identifier"] == nil && Store.data[selected]?["adiPb"] == rawBlobA)
        let midResolver = HistoricalAnisetteResolver(
            read: { MidAnisetteStorage.readString("identifier", client: client) },
            write: { MidAnisetteStorage.write("identifier", data: Data($0.utf8), client: client) })
        let b = midResolver.resolveDeviceIdentifier()
        precondition(b != a && MidAnisetteStorage.read("adiPb", client: client) == rawBlobA)
        let wrongPair = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client)
        precondition(wrongPair.identifier == b && wrongPair.adiBlob == blobA)
        let beforeProbe = Store.data
        let candidate = try LCEmbeddedSharedKeychain.anisetteRecoveryCandidate(for: wrongPair, client: client)!
        precondition(Store.data == beforeProbe)
        // A failed native probe cannot change the mismatched pair.
        do {
            let _: (proof: LCAnisetteRecoveryProof, result: Int) = try await candidate.validateNativeOTP { _, _ in
                throw NSError(domain: "NativeOTPFixture", code: -45061)
            }
            preconditionFailure("failed OTP accepted")
        } catch { precondition((error as NSError).code == -45061) }
        precondition(Store.data == beforeProbe)
        // Successful isolated OTP plus unchanged selected/source evidence can
        // restore only the original identity, without reprovisioning the blob.
        let verified = try await candidate.validateNativeOTP { id, blob in
            precondition(id == a && blob == blobA)
            return (result: 1, oneTimePassword: "b3Rw", machineID: "bWlk")
        }
        let recovered = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(verified.proof, client: client)
        precondition(recovered.identifier == a && recovered.adiBlob == blobA)
        precondition(Store.data[selected]?["adiPb"] == rawBlobA)
        precondition(Store.data[legacy] == ["identifier": Data(a.uuidString.utf8)])
        // Control: direct upgrade to the pair guard rejects the earlier orphan.
        Store.data = orphaned
        do { _ = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client); preconditionFailure("orphan admitted") }
        catch { precondition(error as? LCAnisettePairError == .orphanedBlob) }
        precondition(Store.data == orphaned)
        print("HISTORICAL_ANISETTE_UPGRADE_PASS")
    }
}
