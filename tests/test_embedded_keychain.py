"""Execute the shipped migration/adapter with isolated Keychain and Security doubles.

These tests verify routing, persistence semantics and OSStatus handling, not
real iOS securityd entitlement enforcement. The full iOS build typechecks it.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/templates/embedded_shared_keychain.swift"
SPEC = importlib.util.spec_from_file_location("embedded_keychain_patch", ROOT / "scripts/patch_embedded_keychain.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
SERVICE_SPEC = importlib.util.spec_from_file_location("v3_service_patch", ROOT / "scripts/patch_v3_service.py")
service_module = importlib.util.module_from_spec(SERVICE_SPEC)
SERVICE_SPEC.loader.exec_module(service_module)
BACKGROUND_SPEC = importlib.util.spec_from_file_location(
    "background_automation_patch", ROOT / "scripts/patch_background_automation.py")
background_module = importlib.util.module_from_spec(BACKGROUND_SPEC)
BACKGROUND_SPEC.loader.exec_module(background_module)

DOUBLES = r'''
import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif
// In-memory namespace isolation; each client pins its group at construction.
enum Store {
    static var group: String? = "group.example.shared"
    static var keychainGroup = "TEAM.com.kdt.livecontainer.shared"
    static var processGroup = "TEAM.host.default"
    static var data: [String: [String: Data]] = [:]
    static var failure = 0
    static var writes = 0
    static var removeCalls = 0
    static var removeFailAt: Int?
    static var setCalls = 0
    static var setFailAt: Int?
    static var failSetKey: String?
    static var failSetKeyCount = 0
    static var logs: [String] = []
    static var pauseNextMigrationMarkerRead = false
    static var migrationMarkerReadEntered: DispatchSemaphore?
    static var allowMigrationMarkerRead = DispatchSemaphore(value: 0)
}
final class SnapshotBox {
    private let lock = NSLock()
    private var storage: LCEmbeddedAuthenticationSnapshot?
    func set(_ value: LCEmbeddedAuthenticationSnapshot?) { lock.lock(); storage = value; lock.unlock() }
    func get() -> LCEmbeddedAuthenticationSnapshot? { lock.lock(); defer { lock.unlock() }; return storage }
}
func debugLog(_ message: String) { Store.logs.append(message) }
enum V3AppGroupProcessLock {
    static func withLock<T>(containerRoot: URL? = nil, _ operation: () throws -> T) throws -> T {
        try operation()
    }
}
enum V3SecretHandoff {
    static func sharedKeychainAccessGroup() throws -> String {
        guard !Store.keychainGroup.isEmpty else { throw NSError(domain: NSOSStatusErrorDomain, code: -34018) }
        return Store.keychainGroup
    }
}
extension Bundle {
    enum Info { static var appbundleIdentifier = "com.kdt.livecontainer" }
    var altstoreAppGroup: String? { Store.group }
}
enum KeychainAccess {
    final class Keychain {
        enum Accessibility { case afterFirstUnlock }
        let group: String
        init(service: String) { group = Store.processGroup }
        init(service: String, accessGroup: String) { group = accessGroup }
        func accessibility(_ value: Accessibility) -> Keychain { self }
        func synchronizable(_ value: Bool) -> Keychain { self }
        func getData(_ key: String) throws -> Data? {
            if Store.failure != 0 {
                throw NSError(domain: "com.kishikawakatsumi.KeychainAccess.error", code: Store.failure)
            }
            let value = Store.data[group]?[key]
            if key == "LCSharedKeychainReadyV1" && Store.pauseNextMigrationMarkerRead &&
               Thread.current.threadDictionary["pauseAuthSnapshotMarker"] as? Bool == true {
                Store.pauseNextMigrationMarkerRead = false
                Store.migrationMarkerReadEntered?.signal()
                _ = Store.allowMigrationMarkerRead.wait(timeout: .now() + 5)
            }
            return value
        }
        func set(_ value: Data, key: String) throws {
            if Store.failure != 0 { throw NSError(domain: NSOSStatusErrorDomain, code: Store.failure) }
            Store.setCalls += 1
            if Store.failSetKey == key && Store.failSetKeyCount > 0 {
                Store.failSetKeyCount -= 1
                if Store.failSetKeyCount == 0 { Store.failSetKey = nil }
                throw NSError(domain: NSOSStatusErrorDomain, code: -25291)
            }
            if let failAt = Store.setFailAt, Store.setCalls == failAt {
                Store.setFailAt = nil; throw NSError(domain: NSOSStatusErrorDomain, code: -25291)
            }
            Store.data[group, default: [:]][key] = value; Store.writes += 1
        }
        func remove(_ key: String) throws {
            if Store.failure != 0 { throw NSError(domain: NSOSStatusErrorDomain, code: Store.failure) }
            Store.removeCalls += 1
            if let failAt = Store.removeFailAt, Store.removeCalls == failAt {
                Store.removeFailAt = nil; throw NSError(domain: NSOSStatusErrorDomain, code: -25291)
            }
            Store.data[group]?.removeValue(forKey: key); Store.writes += 1
        }
        func allKeys() -> [String] { Array(Store.data[group, default: [:]].keys) }
    }
}
final class Keychain {
    let keychain: KeychainAccess.Keychain
    init(_ keychain: KeychainAccess.Keychain) { self.keychain = keychain }
}
typealias CFTypeRef = AnyObject
typealias CFDictionary = [String: Any]
let kSecClass = "class", kSecClassGenericPassword = "genp", kSecAttrService = "svce"
let kSecAttrSynchronizable = "sync", kSecAttrSynchronizableAny = "any"
let kSecMatchLimit = "limit", kSecMatchLimitAll = "all", kSecReturnAttributes = "attrs"
let kSecReturnData = "r_Data", kSecUseAuthenticationUI = "ui", kSecUseAuthenticationUIFail = "fail"
let kSecAttrAccessGroup = "agrp", kSecAttrAccount = "acct", kSecValueData = "v_Data"
let errSecItemNotFound = -25300, errSecSuccess = 0
func SecItemCopyMatching(_ query: CFDictionary, _ result: UnsafeMutablePointer<CFTypeRef?>) -> Int {
    precondition(query[kSecAttrService] as? String == "com.kdt.livecontainer")
    precondition(query[kSecClass] as? String == kSecClassGenericPassword)
    precondition(query[kSecUseAuthenticationUI] as? String == kSecUseAuthenticationUIFail)
    precondition(query[kSecAttrAccessGroup] == nil,
        "legacy migration uses entitlement-filtered service lookup, not the App Group as a Keychain group")
    if Store.failure != 0 { return Store.failure }
    let visible = [Store.processGroup, Store.keychainGroup]
    var rows: [[String: Any]] = []
    for group in visible {
        for (key, value) in Store.data[group, default: [:]] {
            rows.append([kSecAttrAccessGroup: group, kSecAttrAccount: key, kSecValueData: value])
        }
    }
    if rows.isEmpty { return errSecItemNotFound }
    result.pointee = rows as NSArray
    return 0
}
'''

HARNESS = r'''
@main struct Tests {
    static func main() throws {
        // This harness exercises injected operation ordering, not the Darwin
        // process-shared flock used by the product binaries.
        LCEmbeddedSharedKeychain.transactionOverride = { try $0() }
        let scenario = CommandLine.arguments[1]
        let group = Store.keychainGroup
        let login = ["appleIDAdsid": Data("test-account-id".utf8), "appleIDXcodeToken": Data("sensitive-test-token".utf8)]
        func seed() { Store.data[Store.processGroup] = login }
        switch scenario {
        case "shared_route":
            seed()
            let ui = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.prepare(ui)
            precondition(ui.group == group)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: ui) == login["appleIDXcodeToken"])
            Store.processGroup = "TEAM.LiveProcess.default"
            let refresh = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.prepare(refresh)
            precondition(refresh.group == group)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: refresh) == login["appleIDXcodeToken"])
            precondition(Store.data["TEAM.host.default"] == login) // original unchanged
        case "extension_first":
            Store.data["TEAM.host.default"] = login
            Store.processGroup = "TEAM.LiveProcess.default"
            let refresh = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.prepare(refresh)
            precondition(Store.writes == 0)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: refresh) == nil)
            Store.processGroup = "TEAM.host.default"
            let ui = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.prepare(ui)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: refresh) == login["appleIDXcodeToken"])
        case "no_password_or_token_logging":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            _ = LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client)
            LCEmbeddedSharedKeychain.write("appleIDPassword", data: Data("private-test-password".utf8), client: client)
            let logs = Store.logs.joined(separator: "\n")
            for secret in ["sensitive-test-token", "private-test-password", "test-account-id"] { precondition(!logs.contains(secret)) }
        case "locked":
            let client = LCEmbeddedSharedKeychain.makeClient(); Store.failure = -25308
            LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) == nil)
            let error = LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "com.kishikawakatsumi.KeychainAccess.error", code: Store.failure))
            precondition(error.domain == "com.SideStore.Keychain" && error.code == 1005)
            precondition(Store.writes == 0)
        case "migration_retry_after_unlock":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); Store.failure = -25308
            LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client) == nil)
            Store.failure = 0
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client) ==
                         login["appleIDXcodeToken"],
                "the same Keychain client retries a transiently blocked migration")
        case "missing_entitlement":
            let client = LCEmbeddedSharedKeychain.makeClient(); Store.failure = -34018
            LCEmbeddedSharedKeychain.prepare(client)
            _ = LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client)
            precondition(LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "com.SideStore.Keychain", code: -34018)).code == 1006)
            precondition(LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "com.kishikawakatsumi.KeychainAccess.error", code: -34018)).code == 1006,
                "the pinned KeychainAccess entitlement status keeps configuration guidance")
            precondition(LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "UnrelatedDomain", code: -34018)).code == 1009,
                "the same numeric status from an unrelated domain remains generic")
        case "missing_group":
            Store.group = nil
            let client = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.write("appleIDPassword", data: Data("secret".utf8), client: client)
            precondition(Store.writes == 0 && LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "com.SideStore.Keychain", code: -34018)).code == 1006)
        case "wrong_identity":
            Bundle.Info.appbundleIdentifier = "com.SideStore.SideStore"
            let client = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.write("appleIDPassword", data: Data("secret".utf8), client: client)
            precondition(Store.writes == 0 && LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "com.SideStore.Keychain", code: -34018)).code == 1006)
        case "signout_no_resurrection":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            for key in LCSharedKeychainMigration.authKeys { LCEmbeddedSharedKeychain.write(key, data: nil, client: client) }
            LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client) == nil)
            precondition(Store.data[Store.processGroup] == login)
            precondition(Store.data[group]?[LCSharedKeychainMigration.marker] == LCSharedKeychainMigration.signedOut,
                "sign-out leaves a verified tombstone that blocks legacy migration")
        case "stale_snapshot_signout":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let items = login.map { LCLegacyKeychainItem(group: Store.processGroup, key: $0.key, data: $0.value) }
            let migrated = try LCSharedKeychainMigration.prepare(group: group, items: { items },
                read: { try client.getData($0) }, write: { try client.set($1, key: $0) }, afterSnapshot: {
                    try client.set(LCSharedKeychainMigration.signedOut, key: LCSharedKeychainMigration.marker)
                    for key in LCSharedKeychainMigration.authKeys { Store.data[Store.processGroup]?.removeValue(forKey: key) }
                })
            precondition(!migrated && Store.data[group]?[LCSharedKeychainMigration.marker] == LCSharedKeychainMigration.signedOut)
            precondition(Store.data[group]?.keys.filter { LCSharedKeychainMigration.authKeys.contains($0) }.isEmpty == true,
                "a snapshot captured before sign-out cannot populate the shared namespace after its tombstone")
        case "checked_signout_failure":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            Store.failure = -25291
            do {
                try LCEmbeddedSharedKeychain.clearSignInInfoChecked(client)
                preconditionFailure("a locked Keychain deletion must not be reported as confirmed")
            } catch {}
            precondition(Store.data[group]?["appleIDXcodeToken"] == login["appleIDXcodeToken"])
            precondition(Store.data[group]?[LCSharedKeychainMigration.marker] == LCSharedKeychainMigration.ready)
            Store.failure = 0
            try LCEmbeddedSharedKeychain.clearSignInInfoChecked(client)
            precondition(Store.data[group]?[LCSharedKeychainMigration.marker] == LCSharedKeychainMigration.signedOut)
        case "checked_signout_rollback":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            let before = Store.data[group]!
            Store.removeCalls = 0; Store.removeFailAt = 2
            do {
                try LCEmbeddedSharedKeychain.clearSignInInfoChecked(client)
                preconditionFailure("the second key deletion must fail")
            } catch { precondition((error as NSError).code == -25291) }
            precondition(Store.data[group] == before,
                "a failed partial sign-out restores every auth value and its prior migration marker")
        case "checked_signout_outcome_unknown":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            Store.removeCalls = 0; Store.removeFailAt = 2
            Store.setCalls = 0; Store.setFailAt = 2 // fail the first restoration write
            do {
                try LCEmbeddedSharedKeychain.clearSignInInfoChecked(client)
                preconditionFailure("failed rollback must report an unknown result")
            } catch { precondition((error as NSError).code == 1010) }
            precondition(Store.data[group]?[LCSharedKeychainMigration.marker] == LCSharedKeychainMigration.signedOut,
                "an unconfirmed rollback keeps the tombstone to suppress stale migration")
        case "clear_all_no_resurrection":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            LCEmbeddedSharedKeychain.clearAll(client); LCEmbeddedSharedKeychain.prepare(client)
            precondition(Store.data[group] == [LCSharedKeychainMigration.marker: LCSharedKeychainMigration.signedOut])
            precondition(Store.data[Store.processGroup] == login)
        case "unchanged_no_writes":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            Store.writes = 0
            for _ in 0..<50 { LCEmbeddedSharedKeychain.prepare(client); _ = LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) }
            precondition(Store.writes == 0)
        case "partial_retry":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let items = login.map { LCLegacyKeychainItem(group: "old", key: $0.key, data: $0.value) }
            var fail = true
            do {
                _ = try LCSharedKeychainMigration.prepare(group: group, items: { items }, read: { try client.getData($0) }, write: { key, data in
                    if fail && key == "appleIDXcodeToken" { throw NSError(domain: NSOSStatusErrorDomain, code: -25308) }
                    try client.set(data, key: key)
                })
                fatalError("failure was swallowed")
            } catch {}
            precondition(LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) == nil) // uncommitted, not a partial login
            let uncommittedMarker = try client.getData(LCSharedKeychainMigration.marker)
            precondition(uncommittedMarker == nil)
            fail = false
            let ready = try LCSharedKeychainMigration.prepare(group: group, items: { items }, read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
            precondition(ready && LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client) == login["appleIDXcodeToken"])
        case "conflicts_fail_before_writes":
            let client = LCEmbeddedSharedKeychain.makeClient()
            var items = login.map { LCLegacyKeychainItem(group: "old1", key: $0.key, data: $0.value) }
            items += [LCLegacyKeychainItem(group: "old2", key: "appleIDAdsid", data: Data("another".utf8)), LCLegacyKeychainItem(group: "old2", key: "appleIDXcodeToken", data: Data("token2".utf8))]
            do {
                _ = try LCSharedKeychainMigration.prepare(group: group, items: { items }, read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
                fatalError("mixed legacy accounts")
            } catch { precondition((error as NSError).code == 1008) }
            precondition(Store.writes == 0)
        case "no_cross_group_pair":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let items = [LCLegacyKeychainItem(group: "A", key: "appleIDAdsid", data: Data("id".utf8)), LCLegacyKeychainItem(group: "B", key: "appleIDXcodeToken", data: Data("token".utf8))]
            let ready = try LCSharedKeychainMigration.prepare(group: group, items: { items }, read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
            precondition(!ready && Store.writes == 0)
        case "certificate_only":
            let cert = Data("test-imported-cert".utf8)
            Store.data[Store.processGroup] = ["importedCert_test": cert]
            let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("importedCert_test", client: client) == cert)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client) == nil)
            precondition(Store.writes == 0)
        case "invalid_utf8":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            Store.data[group]?["appleIDXcodeToken"] = Data([0xff])
            precondition(LCEmbeddedSharedKeychain.readString("appleIDXcodeToken", client: client) == nil)
            precondition(LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "com.SideStore.Keychain", code: 1009)).code == 1009)
        case "preserve_new_login":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.write("appleIDAdsid", data: Data("new-id".utf8), client: client)
            LCEmbeddedSharedKeychain.write("appleIDXcodeToken", data: Data("new-token".utf8), client: client)
            LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) == Data("new-id".utf8))
        case "partial_single_auth_item_never_marks_ready":
            let client = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.write("appleIDAdsid", data: Data("only-one-item".utf8), client: client)
            let partialMarker = try client.getData(LCSharedKeychainMigration.marker)
            precondition(partialMarker == LCSharedKeychainMigration.signedOut,
                "one authentication item cannot commit the ready marker")
            precondition(LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) == nil,
                "an incomplete route remains unreadable as authentication state")
        case "stale_ready_partial_route_is_downgraded":
            let client = LCEmbeddedSharedKeychain.makeClient()
            try client.set(Data("partial-id".utf8), key: "appleIDAdsid")
            try client.set(LCSharedKeychainMigration.ready, key: LCSharedKeychainMigration.marker)
            LCEmbeddedSharedKeychain.prepare(client)
            let repairedMarker = try client.getData(LCSharedKeychainMigration.marker)
            precondition(repairedMarker == LCSharedKeychainMigration.signedOut,
                "an old ready marker without a full route is downgraded")
            precondition(LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) == nil)
        case "partial_signin_write_failure_no_ready":
            let client = LCEmbeddedSharedKeychain.makeClient()
            Store.failSetKey = "appleIDPassword"
            Store.failSetKeyCount = 1
            do {
                try LCEmbeddedSharedKeychain.writeAuthenticationCredentials(
                    appleID: "new@example.com", password: "new-password",
                    dsid: "new-dsid", authToken: "new-token", client: client)
                preconditionFailure("the injected password write must fail")
            } catch { precondition((error as NSError).code == -25291) }
            let values = try LCSharedKeychainMigration.readAuthenticationValues { try client.getData($0) }
            let marker = try client.getData(LCSharedKeychainMigration.marker)
            precondition(values.isEmpty, "the partial credential route is rolled back")
            precondition(marker == nil,
                "a failed partial sign-in must never strand a ready marker")
            precondition(!LCSharedKeychainMigration.complete(values))
        case "partial_signin_failure_preserves_previous_credentials":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let previous: [String: Data] = [
                "appleIDEmailAddress": Data("old@example.com".utf8),
                "appleIDPassword": Data("old-password".utf8),
                "appleIDAdsid": Data("old-dsid".utf8),
                "appleIDXcodeToken": Data("old-token".utf8)
            ]
            for (key, value) in previous { try client.set(value, key: key) }
            try client.set(LCSharedKeychainMigration.ready, key: LCSharedKeychainMigration.marker)
            Store.failSetKey = "appleIDPassword"
            Store.failSetKeyCount = 1
            do {
                try LCEmbeddedSharedKeychain.writeAuthenticationCredentials(
                    appleID: "new@example.com", password: "new-password",
                    dsid: "new-dsid", authToken: "new-token", client: client)
                preconditionFailure("the injected password write must fail")
            } catch { precondition((error as NSError).code == -25291) }
            let restored = try LCSharedKeychainMigration.readAuthenticationValues { try client.getData($0) }
            let marker = try client.getData(LCSharedKeychainMigration.marker)
            precondition(restored == previous, "all previous credentials survive the failed replacement")
            precondition(marker == LCSharedKeychainMigration.ready)
            precondition(LCSharedKeychainMigration.complete(restored))
        case "partial_signin_rollback_unverified_is_unknown":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let previous: [String: Data] = [
                "appleIDEmailAddress": Data("old@example.com".utf8),
                "appleIDPassword": Data("old-password".utf8),
                "appleIDAdsid": Data("old-dsid".utf8),
                "appleIDXcodeToken": Data("old-token".utf8)
            ]
            for (key, value) in previous { try client.set(value, key: key) }
            try client.set(LCSharedKeychainMigration.ready, key: LCSharedKeychainMigration.marker)
            // Fail the new password and its rollback write. The transaction must
            // return the explicit unknown-outcome code and leave readiness false.
            Store.failSetKey = "appleIDPassword"
            Store.failSetKeyCount = 2
            var writeFailure: Error?
            do {
                try LCEmbeddedSharedKeychain.writeAuthenticationCredentials(
                    appleID: "new@example.com", password: "new-password",
                    dsid: "new-dsid", authToken: "new-token", client: client)
                preconditionFailure("the injected write and rollback failures must be reported")
            } catch { writeFailure = error; precondition((error as NSError).code == 1010) }
            let marker = try client.getData(LCSharedKeychainMigration.marker)
            precondition(marker != LCSharedKeychainMigration.ready,
                "an uncertain rollback must never leave the credential set advertised as ready")
            precondition(LCEmbeddedSharedKeychain.authenticationFailure(for: writeFailure!).code == 1010,
                "the unconfirmed credential save remains visible as an unknown outcome")
        case "credential_snapshot_serializes_bulk_replacement":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let oldValues: [String: Data] = [
                "appleIDEmailAddress": Data("old@example.com".utf8),
                "appleIDPassword": Data("old-password".utf8),
                "appleIDAdsid": Data("old-dsid".utf8),
                "appleIDXcodeToken": Data("old-token".utf8),
                LCSharedKeychainMigration.marker: LCSharedKeychainMigration.ready
            ]
            Store.data[group] = oldValues
            let transactionLock = NSLock()
            let markerEntered = DispatchSemaphore(value: 0)
            Store.migrationMarkerReadEntered = markerEntered
            Store.allowMigrationMarkerRead = DispatchSemaphore(value: 0)
            Store.pauseNextMigrationMarkerRead = true
            let readerFinished = DispatchSemaphore(value: 0)
            let writerFinished = DispatchSemaphore(value: 0)
            let writerAttempted = DispatchSemaphore(value: 0)
            let observed = SnapshotBox()
            let transactionStateLock = NSLock()
            var transactionCount = 0
            LCEmbeddedSharedKeychain.transactionOverride = { operation in
                transactionStateLock.lock()
                transactionCount += 1
                let currentCount = transactionCount
                transactionStateLock.unlock()
                if currentCount == 2 { writerAttempted.signal() }
                transactionLock.lock()
                defer { transactionLock.unlock() }
                try operation()
            }
            DispatchQueue.global().async {
                Thread.current.threadDictionary["pauseAuthSnapshotMarker"] = true
                observed.set(try? Keychain(client).authenticationSnapshot())
                Thread.current.threadDictionary.removeObject(forKey: "pauseAuthSnapshotMarker")
                readerFinished.signal()
            }
            precondition(markerEntered.wait(timeout: .now() + 2) == .success,
                "the reader pauses after reading the old ready marker")
            DispatchQueue.global().async {
                try? Keychain(client).writeAuthenticationCredentials(appleID: "new@example.com",
                    password: "new-password", dsid: "new-dsid", authToken: "new-token")
                writerFinished.signal()
            }
            precondition(writerAttempted.wait(timeout: .now() + 2) == .success,
                "the writer reached the process-shared transaction boundary")
            precondition(writerFinished.wait(timeout: .now()) == .timedOut,
                "the writer cannot replace credentials while a snapshot owns the shared transaction")
            Store.allowMigrationMarkerRead.signal()
            precondition(readerFinished.wait(timeout: .now() + 2) == .success)
            precondition(writerFinished.wait(timeout: .now() + 2) == .success)
            let beforeCommit = observed.get()
            precondition(beforeCommit?.appleIDEmailAddress == "old@example.com" &&
                beforeCommit?.appleIDPassword == "old-password" &&
                beforeCommit?.appleIDAdsid == "old-dsid" &&
                beforeCommit?.appleIDXcodeToken == "old-token" &&
                beforeCommit?.isAuthenticated == true,
                "a credential consumer sees one complete old generation")
            let afterCommit = try Keychain(client).authenticationSnapshot()
            precondition(afterCommit?.appleIDEmailAddress == "new@example.com" &&
                afterCommit?.appleIDPassword == "new-password" &&
                afterCommit?.appleIDAdsid == "new-dsid" &&
                afterCommit?.appleIDXcodeToken == "new-token" &&
                afterCommit?.isAuthenticated == true,
                "the following credential consumer sees one complete new generation")
            LCEmbeddedSharedKeychain.transactionOverride = { try $0() }
            Store.migrationMarkerReadEntered = nil
        case "snapshot_access_error_preserves_actionable_keychain_failure":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let authKeychain = Keychain(client)
            Store.failure = -25308
            var snapshotFailure: Error?
            do {
                _ = try authKeychain.authenticationSnapshot()
                preconditionFailure("locked Keychain snapshot must fail")
            } catch { snapshotFailure = error }
            precondition(LCEmbeddedSharedKeychain.authenticationFailure(for: snapshotFailure!).code == 1005,
                "snapshot access denial must keep the actionable locked-Keychain classification")
            Store.failure = 0
            seed()
            let restored = try authKeychain.authenticationSnapshot()
            precondition(restored?.isAuthenticated == true,
                "a successful read after unlock returns a complete snapshot")
            precondition(LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "OtherDomain", code: -25308)).code == 1009,
                "an unrelated domain's numeric code cannot masquerade as a locked Keychain result")
        case "new_credentials_survive_after_failed_signin_started_from_empty_state":
            let firstClient = Keychain(LCEmbeddedSharedKeychain.makeClient())
            let secondClient = Keychain(LCEmbeddedSharedKeychain.makeClient())
            let beforeAttempt = try firstClient.authenticationSnapshot()
            precondition(beforeAttempt == nil, "the first attempt begins with no stored account")

            // A second client represents another process committing a new login
            // after the attempt's initial empty observation. The generated
            // SignInOperation failure catch is verified separately to contain no
            // implicit signOut call, so the new account cannot be cleared.
            try secondClient.writeAuthenticationCredentials(appleID: "new@example.com", password: "new-password",
                dsid: "new-dsid", authToken: "new-token")
            let afterConcurrentCommit = try firstClient.authenticationSnapshot()
            precondition(afterConcurrentCommit?.appleIDEmailAddress == "new@example.com" &&
                         afterConcurrentCommit?.isAuthenticated == true,
                "credentials committed after the initial empty observation survive the failed attempt")
        default: fatalError("unknown scenario")
        }
        print("PASSED: " + scenario)
    }
}
'''

class EmbeddedKeychainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("swiftc")
        if not compiler:
            raise unittest.SkipTest("swiftc unavailable")
        cls.temp = tempfile.TemporaryDirectory(prefix="lc-keychain-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "KeychainTests.swift"
        source.write_text(DOUBLES + TEMPLATE.read_text() + module.KEYCHAIN_ACCESS_ADAPTER + HARNESS)
        cls.executable = Path(cls.temp.name) / "keychain-tests"
        result = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", "-O", str(source), "-o", str(cls.executable)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_execution_scenarios(self):
        for scenario in ("shared_route", "extension_first", "no_password_or_token_logging", "locked", "migration_retry_after_unlock", "missing_entitlement", "missing_group", "wrong_identity", "signout_no_resurrection", "stale_snapshot_signout", "checked_signout_failure", "checked_signout_rollback", "checked_signout_outcome_unknown", "clear_all_no_resurrection", "unchanged_no_writes", "partial_retry", "conflicts_fail_before_writes", "no_cross_group_pair", "preserve_new_login", "partial_single_auth_item_never_marks_ready", "stale_ready_partial_route_is_downgraded", "partial_signin_write_failure_no_ready", "partial_signin_failure_preserves_previous_credentials", "partial_signin_rollback_unverified_is_unknown", "credential_snapshot_serializes_bulk_replacement", "snapshot_access_error_preserves_actionable_keychain_failure", "new_credentials_survive_after_failed_signin_started_from_empty_state", "certificate_only", "invalid_utf8"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.executable), scenario], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0,
                    f"scenario={scenario} exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
                self.assertIn("PASSED: " + scenario, result.stdout)

    def test_source_safety(self):
        text = TEMPLATE.read_text()
        for forbidden in ("UserDefaults.", "write(to:", "NSLog(", "Timer(", "Task.sleep", "removePersistentDomain", "signOut("):
            self.assertNotIn(forbidden, text)
        self.assertIn("accessGroup: keychainGroup", text)
        self.assertIn("V3SecretHandoff.sharedKeychainAccessGroup()", text)
        self.assertNotIn("accessGroup: appGroup", text)
        self.assertIn("kSecAttrService as String: service", text)
        self.assertIn("kSecUseAuthenticationUIFail", text)
        self.assertNotIn("\\(error)", text)
        self.assertIn(".afterFirstUnlock", text)
        self.assertIn("writeAuthenticationCredentials", text)
        self.assertIn("LCSharedKeychainMigration.complete(written)", text)
        self.assertIn("LCSharedKeychainMigration.complete(committed)", text)
        self.assertIn("static func readAuthenticationSnapshot", text)
        self.assertIn("authenticationValuesLocked", text)
        self.assertIn("could not confirm whether the Apple sign-in credentials were saved", text)

    def test_signout_checks_all_auth_key_deletions_before_reporting_success(self):
        patch = (ROOT / "scripts/patch_embedded_keychain.py").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        self.assertIn("func clearSignInInfoChecked() throws", patch)
        self.assertIn("LCEmbeddedSharedKeychain.clearSignInInfoChecked(self.keychain)", patch)
        prepare = runtime[runtime.index("static func prepareSignOut() throws"):]
        prepare = prepare[:prepare.index("\n    }")]
        self.assertIn("try Keychain.shared.clearSignInInfoChecked()", prepare)
        self.assertIn("case .keychainSignOutFailed", failure)
        self.assertIn("try V3BackendCommands.prepareSignOut()", service)
        self.assertLess(service.index("try V3BackendCommands.prepareSignOut()"),
                        service.index("AuthManager.shared.signOut(keepCertificate: true"))

    def test_pinned_patch_and_idempotence(self):
        source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not source:
            self.skipTest("pinned SideStore source unavailable locally; required in combined CI")
        relative = "AltStore/Core/Components/Keychain.swift"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relatives = (
                relative,
                "SideStore/Core/Auth/AuthManager.swift",
                "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift",
                "SideStore/Utils/importexport/ImportExport.swift",
            )
            destinations = []
            for name in relatives:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                original = (Path(source) / name).read_text(encoding="utf-8")
                if name == "SideStore/Core/Auth/AuthManager.swift":
                    original = service_module.headless_auth_manager(original)
                elif name == "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift":
                    original = service_module.patch_sign_in_operation(original)
                target.write_text(original, encoding="utf-8")
                destinations.append(target)
            op = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text('''func preflight() throws {
        let auth = AuthManager.shared
        let credentials = auth.authenticationSnapshot
        let hasPasswordCredentials = credentials?.appleIDEmailAddress != nil && credentials?.appleIDPassword != nil
        let hasTokenCredentials = credentials?.appleIDAdsid != nil && credentials?.appleIDXcodeToken != nil
        let hasReusableSession = auth.session != nil && auth.team != nil && CertificateManager.shared.activeCertificate != nil
        debugLog("[AUTO_REFRESH] AUTH_CREDENTIAL_VISIBILITY password_path=\\(hasPasswordCredentials) token_path=\\(hasTokenCredentials) session_path=\\(hasReusableSession)")
        guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else {
            let error = NSError(domain: "com.SideStore.Authentication", code: 1004)
            debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_FAIL reason=no_accessible_authentication_path")
            throw error
        }
}''')
            module.patch(root)
            first = tuple(path.read_bytes() for path in destinations) + (op.read_bytes(),)
            self.assertIn("writeAuthenticationCredentials", destinations[0].read_text(encoding="utf-8"))
            self.assertIn(module.AUTH_MANAGER_MARKER, destinations[1].read_text(encoding="utf-8"))
            self.assertIn(module.SIGN_IN_SNAPSHOT_MARKER, destinations[2].read_text(encoding="utf-8"))
            self.assertIn(module.IMPORT_EXPORT_SNAPSHOT_MARKER, destinations[3].read_text(encoding="utf-8"))
            module.patch(root)
            self.assertEqual(first, tuple(path.read_bytes() for path in destinations) + (op.read_bytes(),))
            background_auth = op.read_text(encoding="utf-8")
            self.assertIn(module.BACKGROUND_AUTH_SNAPSHOT_MARKER, background_auth)
            self.assertIn("Keychain.shared.embeddedAuthenticationFailure(error)", background_auth)
            self.assertIn(module.BACKGROUND_AUTH_MISSING_MARKER, background_auth)
            self.assertNotIn("auth.currentAppleID", background_auth)
            self.assertNotIn("auth.adsid", background_auth)
            self.assertIn(module.BACKGROUND_AUTH_SNAPSHOT_MARKER, op.read_text())
            self.assertNotIn("auth.currentAppleID", op.read_text())
            self.assertNotIn("try? Keychain.shared.keychain.get", destinations[0].read_text())


class KeychainPatchGenerationTests(unittest.TestCase):
    def test_pinned_background_refresh_auth_preflight_uses_one_snapshot(self):
        source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not source:
            self.skipTest("pinned SideStore source unavailable locally; required in combined CI")
        relative = "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(source) / relative, target)
            background_module.patch_background_operation(root)
            patched = module.patch_background_auth_snapshot(target.read_text(encoding="utf-8"))
            first = patched
            replay = module.patch_background_auth_snapshot(patched)
            self.assertEqual(first, replay)
            self.assertIn(module.BACKGROUND_AUTH_SNAPSHOT_MARKER, patched)
            self.assertIn("authSnapshot?.appleIDEmailAddress", patched)
            self.assertIn("authSnapshot?.appleIDPassword", patched)
            self.assertIn("authSnapshot?.appleIDAdsid", patched)
            self.assertIn("authSnapshot?.appleIDXcodeToken", patched)
            self.assertIn("Keychain.shared.embeddedAuthenticationFailure(error)", patched)
            self.assertNotIn("auth.currentAppleID", patched)
            self.assertNotIn("auth.adsid", patched)

    def test_pinned_patch_generates_one_snapshot_for_authentication_pairs(self):
        source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not source:
            self.skipTest("pinned SideStore source unavailable locally; required in combined CI")
        relative_files = (
            "AltStore/Core/Components/Keychain.swift",
            "SideStore/Core/Auth/AuthManager.swift",
            "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift",
            "SideStore/Utils/importexport/ImportExport.swift",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for relative in relative_files:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                original = (Path(source) / relative).read_text(encoding="utf-8")
                if relative == "SideStore/Core/Auth/AuthManager.swift":
                    original = service_module.headless_auth_manager(original)
                elif relative == "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift":
                    original = service_module.patch_sign_in_operation(original)
                target.write_text(original, encoding="utf-8")
                paths.append(target)
            background = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
            background.parent.mkdir(parents=True, exist_ok=True)
            background.write_text('''func preflight() throws {
        let auth = AuthManager.shared
        let credentials = auth.authenticationSnapshot
        let hasPasswordCredentials = credentials?.appleIDEmailAddress != nil && credentials?.appleIDPassword != nil
        let hasTokenCredentials = credentials?.appleIDAdsid != nil && credentials?.appleIDXcodeToken != nil
        let hasReusableSession = auth.session != nil && auth.team != nil && CertificateManager.shared.activeCertificate != nil
        debugLog("[AUTO_REFRESH] AUTH_CREDENTIAL_VISIBILITY password_path=\\(hasPasswordCredentials) token_path=\\(hasTokenCredentials) session_path=\\(hasReusableSession)")
        guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else {
            let error = NSError(domain: "com.SideStore.Authentication", code: 1004)
            debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_FAIL reason=no_accessible_authentication_path")
            throw error
        }
}''', encoding="utf-8")
            module.patch(root)
            keychain, auth, sign_in, import_export = [p.read_text(encoding="utf-8") for p in paths]
            self.assertIn("LCEmbeddedSharedKeychain.readAuthenticationSnapshot(self.keychain)", keychain)
            self.assertIn("LC_AUTH_CREDENTIAL_SNAPSHOT_V1", auth)
            self.assertIn("authenticationSnapshot?.isAuthenticated", auth)
            self.assertIn("authenticationSnapshot?.hasPasswordCredentials", auth)
            self.assertIn("authenticationSnapshot?.hasTokenCredentials", auth)
            self.assertNotIn("let hasEmail = Keychain.shared.appleIDEmailAddress", auth)
            auth_session_start = auth.index("public func getAuthenticatedSession()")
            auth_session_end = auth.index("\n    }", auth_session_start)
            auth_session = auth[auth_session_start:auth_session_end]
            self.assertIn(module.AUTH_SESSION_SNAPSHOT_MARKER, auth_session)
            self.assertIn("credentialSnapshot?.appleIDAdsid", auth_session)
            self.assertIn("credentialSnapshot?.appleIDXcodeToken", auth_session)
            self.assertIn("try Keychain.shared.authenticationSnapshot()", auth_session)
            self.assertIn("Keychain.shared.embeddedAuthenticationFailure(error)", auth_session)
            self.assertNotIn("self.adsid", auth_session)
            self.assertNotIn("self.xcodeToken", auth_session)
            self.assertIn("LC_SIGNIN_CREDENTIAL_SNAPSHOT_V1", sign_in)
            self.assertIn("let credentials = AuthManager.shared.authenticationSnapshot", sign_in)
            self.assertNotIn("if let adsid = AuthManager.shared.adsid", sign_in)
            self.assertNotIn("if let appleID = AuthManager.shared.currentAppleID", sign_in)
            self.assertIn("LC_IMPORT_EXPORT_CREDENTIAL_SNAPSHOT_V1", import_export)
            self.assertIn("let authSnapshot = AuthManager.shared.authenticationSnapshot", import_export)
            failure_start = sign_in.index("V3_AUTH_FAILURE_PRESERVES_ACCOUNT_STATE_V1")
            failure_end = sign_in.index("try? await self.finalizeAuthentication", failure_start)
            failure_catch = sign_in[failure_start:failure_end]
            self.assertNotIn("AuthManager.shared.signOut()", failure_catch)
            self.assertNotIn("Keychain.shared.clearSignInInfo", failure_catch)
            self.assertNotIn("hasStoredPassword", failure_catch)
            self.assertNotIn("hasStoredXcodeToken", failure_catch)
            self.assertIn(module.BACKGROUND_AUTH_SNAPSHOT_MARKER, background.read_text(encoding="utf-8"))
            self.assertNotIn("AuthManager.shared.currentAppleID", background.read_text(encoding="utf-8"))
            expected_auth = module.patch_auth_manager(service_module.headless_auth_manager(
                (Path(source) / relative_files[1]).read_text(encoding="utf-8")))
            expected_sign_in = module.patch_sign_in_operation(service_module.patch_sign_in_operation(
                (Path(source) / relative_files[2]).read_text(encoding="utf-8")))
            self.assertEqual(auth, expected_auth)
            self.assertEqual(sign_in, expected_sign_in)
            first = tuple(path.read_bytes() for path in paths) + (background.read_bytes(),)
            module.patch(root)
            self.assertEqual(first, tuple(path.read_bytes() for path in paths) + (background.read_bytes(),),
                "credential snapshot generation must be idempotent")

if __name__ == "__main__":
    unittest.main()
