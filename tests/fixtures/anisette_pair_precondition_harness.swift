// All data is synthetic. The real generated resolver, keychain adapter and
// ODA/remote continuation methods are inserted by the Python test.
public struct ALTAnisetteData {}
struct AnisetteRequestHeaders {}
enum AnisetteMode { case remoteODA(sourceURL: URL, fallbackURL: URL?) }
enum AppConstants { enum Anisette { static let defaultODAMetadataURL = URL(string: "https://example.invalid/oda")! } }
protocol AnisetteServerHandler { func warnOutdatedAnisetteServer() async throws -> Bool }
enum SideSign {
    typealias AnisetteDataManager = SyntheticAnisetteDataManager
    enum AnisetteError: Error { case noServersConfigured; case outdatedV1Server(server: URL, reason: String?) }
}
typealias AnisetteError = SideSign.AnisetteError
typealias AnisetteDataManager = SyntheticAnisetteDataManager
enum AnisetteServersManager {
    static let shared = AnisetteServersManagerValue()
    static let defaultSource = "https://example.invalid/oda"
    struct AnisetteServersManagerValue {
        func getActiveServerURLs() async -> [String] { ["https://example.invalid/anisette"] }
    }
}
extension UserDefaults {
    var menuAnisetteList: String { "" }
    var useOnDeviceAnisette: Bool { PairTest.onDevice }
    var menuAnisetteURL: String { get { "https://example.invalid/anisette" } set {} }
    var disableAnisetteRotation: Bool { false }
    var defaultServerURL: String { get { "" } set {} }
}
enum PairTest {
    static var onDevice = true
    static var requireLockedReads = false
    static var lockDepth = 0
    static var providerCalls = 0
    static var providerIdentifier: UUID?
    static var providerBlob: Data?
    static var beforeReturn: (() throws -> Void)?
    static var returnedBlob: Data?
    static let originalID = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
    static let otherID = UUID(uuidString: "22222222-2222-2222-2222-222222222222")!
    static let originalBlob = Data("synthetic-blob-bound-to-original-identifier".utf8)
    static var originalIDData: Data { Data(originalID.uuidString.utf8) }
    static var encodedBlob: Data { Data(originalBlob.base64EncodedString().utf8) }
    static let login = ["appleIDEmailAddress": Data("fixture@example.invalid".utf8), "appleIDPassword": Data("synthetic-password".utf8)]
}
final class SyntheticAnisetteDataManager {
    static let shared = SyntheticAnisetteDataManager()
    func fetchAnisetteData(mode: AnisetteMode, identifier: UUID, existingAdiBlob: Data?,
                           headers: AnisetteRequestHeaders) async throws -> (ALTAnisetteData, Data?) {
        try capture(identifier: identifier, existingAdiBlob: existingAdiBlob)
    }
    func fetchAnisetteDataWithFailover(servers: [URL], startIndex: Int, identifier: UUID,
        existingAdiBlob: Data?, headers: AnisetteRequestHeaders,
        onError: @escaping (Error) async throws -> Bool,
        onSuccess: @escaping (URL) async -> Void) async throws -> (ALTAnisetteData, Data?) {
        try capture(identifier: identifier, existingAdiBlob: existingAdiBlob)
    }
    private func capture(identifier: UUID, existingAdiBlob: Data?) throws -> (ALTAnisetteData, Data?) {
        PairTest.providerCalls += 1
        PairTest.providerIdentifier = identifier
        PairTest.providerBlob = existingAdiBlob
        try PairTest.beforeReturn?()
        return (ALTAnisetteData(), PairTest.returnedBlob ?? (existingAdiBlob == nil ? PairTest.originalBlob : nil))
    }
}
public actor AnisetteConfigManager {
    static let shared = AnisetteConfigManager()
    var anisetteIdentifier: String? {
        get { LCEmbeddedSharedKeychain.readString("identifier", client: Keychain.shared.keychain) }
        set { LCEmbeddedSharedKeychain.write("identifier", data: newValue.map { Data($0.utf8) }, client: Keychain.shared.keychain) }
    }
    nonisolated var anisetteAdiBlob: String? {
        get { LCEmbeddedSharedKeychain.readString("adiPb", client: Keychain.shared.keychain) }
        set { LCEmbeddedSharedKeychain.write("adiPb", data: newValue.map { Data($0.utf8) }, client: Keychain.shared.keychain) }
    }
    func makeRequestHeaders() -> AnisetteRequestHeaders { AnisetteRequestHeaders() }
__PRODUCTION_CONFIG_METHODS__
}
actor OnDeviceAnisetteManager {
    static let shared = OnDeviceAnisetteManager()
    let provider = SyntheticAnisetteDataManager()
__PRODUCTION_ODA_METHOD__
}
__PRODUCTION_REMOTE_PROVIDER__

@main struct AnisettePairTests {
    static func main() async throws {
        let scenario = CommandLine.arguments[1]
        PairTest.onDevice = !scenario.hasSuffix("_remote")
        let state = scenario.replacingOccurrences(of: "_remote", with: "")
        let client = LCEmbeddedSharedKeychain.makeClient()
        Keychain.shared = Keychain(client)
        let group = Store.keychainGroup
        let originalID = PairTest.originalIDData
        let blob = PairTest.encodedBlob
        switch state {
        case "empty", "old_fallback_creates_orphan": break
        case "valid_id_only", "state_changed", "paired_read_epoch": Store.data[group] = ["identifier": originalID]
        case "complete_pair", "repeated_pair", "cached_blob_matches", "cached_blob_differs": Store.data[group] = ["identifier": originalID, "adiPb": blob]
        case "base64_identifier": Store.data[group] = ["identifier": Data(Data(repeating: 1, count: 16).base64EncodedString().utf8), "adiPb": blob]
        case "orphaned_blob", "unguarded_orphan", "old_fallback_upgrade": Store.data[group] = ["adiPb": blob]
        case "invalid_id_only": Store.data[group] = ["identifier": Data("invalid-id".utf8)]
        case "invalid_id_blob": Store.data[group] = ["identifier": Data("invalid-id".utf8), "adiPb": blob]
        case "invalid_blob": Store.data[group] = ["identifier": originalID, "adiPb": Data([0xff])]
        case "legacy_pair", "interrupted_migration":
            Store.data[Store.processGroup] = PairTest.login.merging(["identifier": originalID, "adiPb": blob]) { _, b in b }
        case "selected_orphan_legacy_id":
            Store.data[group] = ["adiPb": blob]
            Store.data[Store.processGroup] = PairTest.login.merging(["identifier": originalID]) { _, b in b }
        case "legacy_orphan_selected_id":
            Store.data[group] = ["identifier": originalID]
            Store.data[Store.processGroup] = PairTest.login.merging(["adiPb": blob]) { _, b in b }
        case "legacy_orphan_empty", "legacy_orphan_bridge": Store.data[Store.processGroup] = PairTest.login.merging(["adiPb": blob]) { _, b in b }
        case "keychain_read_failure": Store.failure = -25291
        default: fatalError("Unknown synthetic scenario")
        }
        if state == "old_fallback_creates_orphan" {
            Store.data[Store.processGroup] = ["identifier": originalID]
        }
        if state == "interrupted_migration" {
            Store.failSetKey = "identifier"; Store.failSetKeyCount = 1
            LCEmbeddedSharedKeychain.prepare(client)
            precondition(Store.data[group]?["adiPb"] == blob && Store.data[group]?["identifier"] == nil)
        }
        if state == "legacy_pair" || state == "interrupted_migration" {
            LCEmbeddedSharedKeychain.prepare(client)
        }
        if state == "legacy_orphan_bridge" {
            let before = Store.data
            let writes = Store.writes
            do { _ = try Keychain.shared.authenticationSnapshot(); fatalError("unsafe legacy pair admitted") }
            catch {
                let bridged = Keychain.shared.embeddedAuthenticationFailure(error)
                precondition((bridged as? LCAnisettePairError) == .migrationPairConflict,
                    "preflight bridge erased finite pair classification")
            }
            precondition(Store.data == before && Store.writes == writes && PairTest.providerCalls == 0)
            print("ANISETTE_PAIR_PASS " + scenario)
            return
        }
        if state == "selected_orphan_legacy_id" || state == "legacy_orphan_selected_id" || state == "legacy_orphan_empty" {
            let before = Store.data
            let writes = Store.writes
            do {
                _ = try LCSharedKeychainMigration.prepare(group: group, items: {
                    Store.data[Store.processGroup, default: [:]].map { LCLegacyKeychainItem(group: Store.processGroup, key: $0.key, data: $0.value) }
                }, read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
                fatalError("Unsafe partial pair migration was accepted")
            } catch is LCAnisettePairError {}
            precondition(Store.data == before && Store.writes == writes && PairTest.providerCalls == 0)
            print("ANISETTE_PAIR_PASS " + scenario)
            return
        }
        if state == "cached_blob_matches" { PairTest.returnedBlob = PairTest.originalBlob }
        if state == "cached_blob_differs" { PairTest.returnedBlob = Data("stale-cached-blob".utf8) }
        let before = Store.data
        let writes = Store.writes
        var transactionCount = 0
        if state == "paired_read_epoch" {
            PairTest.requireLockedReads = true
            LCEmbeddedSharedKeychain.transactionOverride = { operation in
                transactionCount += 1
                precondition(transactionCount <= 2)
                PairTest.lockDepth += 1
                defer { PairTest.lockDepth -= 1 }
                try operation()
            }
        }
        if state == "state_changed" {
            PairTest.beforeReturn = {
                // A cooperating second process changes the epoch during the
                // async provider call; no old result may overwrite its bytes.
                LCEmbeddedSharedKeychain.write("identifier", data: Data(PairTest.otherID.uuidString.utf8), client: client)
            }
        }
        let blocked = ["orphaned_blob", "old_fallback_upgrade", "invalid_id_only", "invalid_id_blob", "invalid_blob", "interrupted_migration", "keychain_read_failure"].contains(state)
        do {
            _ = try await AnisetteProvider.fetch()
            precondition(!blocked && state != "state_changed" && state != "cached_blob_differs", "Guard did not stop unsafe continuation")
        } catch {
            if state == "cached_blob_differs" {
                precondition((error as? LCAnisettePairError) == .stateChanged)
                precondition(Store.data == before && Store.writes == writes && PairTest.providerCalls == 1,
                    "Cached provider result must not replace the admitted stored blob")
            } else if state == "state_changed" {
                precondition((error as? LCAnisettePairError) == .stateChanged)
                precondition(Store.data[group]?["identifier"] == Data(PairTest.otherID.uuidString.utf8))
                precondition(Store.data[group]?["adiPb"] == nil && PairTest.providerCalls == 1)
            } else {
                precondition(blocked, "Unexpected guarded continuation failure")
                if state != "keychain_read_failure" { precondition(error is LCAnisettePairError) }
                precondition(Store.data == before && Store.writes == writes && PairTest.providerCalls == 0,
                    "Blocked state must preserve every byte and avoid the provider")
            }
            print("ANISETTE_PAIR_PASS " + scenario)
            return
        }
        precondition(PairTest.providerCalls == 1)
        if state == "unguarded_orphan" {
            precondition(Store.writes > writes && PairTest.providerIdentifier != PairTest.originalID)
            precondition(PairTest.providerBlob == PairTest.originalBlob,
                "Baseline must reach provider with generated ID and old blob")
        } else if state == "old_fallback_creates_orphan" {
            precondition(PairTest.providerIdentifier == PairTest.originalID && PairTest.providerBlob == nil)
            precondition(Store.data[group]?["identifier"] == nil && Store.data[group]?["adiPb"] == blob)
        } else if ["empty", "valid_id_only", "paired_read_epoch"].contains(state) {
            precondition(Store.data[group]?["identifier"] != nil && Store.data[group]?["adiPb"] == blob)
            precondition(PairTest.providerBlob == nil)
            if state != "empty" { precondition(PairTest.providerIdentifier == PairTest.originalID) }
            if state == "paired_read_epoch" { precondition(transactionCount == 2) }
        } else {
            precondition(Store.data == before && Store.writes == writes)
            precondition(PairTest.providerBlob == PairTest.originalBlob)
        }
        if state == "repeated_pair" {
            _ = try await AnisetteProvider.fetch()
            precondition(PairTest.providerCalls == 2 && Store.data == before && Store.writes == writes)
        }
        print("ANISETTE_PAIR_PASS " + scenario)
    }
}
