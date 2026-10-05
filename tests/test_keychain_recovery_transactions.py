"""Exercise the shipped Keychain transaction code, including failed read-back.

Security.framework and Apple are doubles. A native/device pass is still needed.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import test_embedded_keychain as existing

DOUBLES = existing.DOUBLES.replace(
    "    static var writes = 0",
    "    static var failPendingAfterReady = false\n"
    "    static var corruptReadKey: String?\n"
    "    static var corruptReadAfterWrites = 0\n"
    "    static var writes = 0",
).replace(
    "            let value = Store.data[group]?[key]",
    "            if Store.corruptReadKey == key && Store.writes >= Store.corruptReadAfterWrites {\n"
    "                Store.corruptReadKey = nil\n"
    '                return Data("different-readback".utf8)\n'
    "            }\n"
    "            let value = Store.data[group]?[key]",
).replace(
    "            Store.setCalls += 1",
    "            Store.setCalls += 1\n"
    '            if Store.failPendingAfterReady && value == Data("pending-v1".utf8) &&\n'
    '               Store.data[group]?[key] == Data("1".utf8) {\n'
    '                throw NSError(domain: NSOSStatusErrorDomain, code: -25291)\n'
    "            }",
)

HARNESS = r'''
@main struct RecoveryTests {
    static func main() throws {
        LCEmbeddedSharedKeychain.transactionOverride = { try $0() }
        let client = LCEmbeddedSharedKeychain.makeClient()
        let keychain = Keychain(client)
        let group = Store.keychainGroup
        let marker = LCSharedKeychainMigration.marker
        let route = ["appleIDAdsid": Data("id".utf8), "appleIDXcodeToken": Data("token".utf8)]
        let scenario = CommandLine.arguments[1]
        func expectFailure(_ code: Int, _ work: () throws -> Void) {
            do { try work(); preconditionFailure("expected failure") }
            catch { precondition((error as NSError).code == code) }
        }
        func commit(_ candidate: LCEmbeddedAuthenticationCandidate, email: String = "test@example.com", dsid: String = "id") throws {
            try keychain.writeVerifiedAuthentication(candidate, appleID: email, dsid: dsid, authToken: "verified-token")
        }
        func saveCertificate(_ bytes: String = "new-p12") throws {
            try keychain.writeSigningCertificate(p12Data: Data(bytes.utf8), password: "new-password", serial: "new-serial") { data, password in
                precondition(data == Data(bytes.utf8) && password == "new-password")
            }
        }
        switch scenario {
        case "token_only":
            Store.data[group] = route.merging([marker: LCSharedKeychainMigration.ready]) { _, new in new }
            let candidate = try keychain.authenticationCandidate()!
            precondition(!candidate.credentials.isAuthenticated && candidate.credentials.hasTokenCredentials)
            try commit(candidate)
            let saved = try keychain.authenticationSnapshot()!
            precondition(saved.isAuthenticated && saved.appleIDEmailAddress == "test@example.com")
            precondition(saved.appleIDPassword == nil && saved.appleIDXcodeToken == "verified-token")
        case "markerless_token", "markerless_password":
            Store.data[group] = scenario == "markerless_token" ? route : [
                "appleIDEmailAddress": Data("test@example.com".utf8), "appleIDPassword": Data("password".utf8)]
            let before = Store.data[group]
            let unbound = try keychain.authenticationSnapshot()
            precondition(unbound == nil && Store.data[group] == before)
            let candidate = try keychain.authenticationCandidate()!
            precondition(Store.data[group] == before && candidate.marker == nil)
            try commit(candidate)
            let saved = try keychain.authenticationSnapshot()!
            precondition(saved.isAuthenticated && saved.appleIDEmailAddress == "test@example.com")
            precondition(saved.appleIDPassword == (scenario == "markerless_password" ? "password" : nil))
        case "markerless_partial":
            Store.data[group] = ["appleIDAdsid": Data("id".utf8)]
            Store.data[Store.processGroup] = route
            let before = Store.data
            let candidate = try keychain.authenticationCandidate()
            precondition(candidate == nil && Store.data == before)
        case "tombstone", "unknown_marker":
            Store.data[group] = route
            Store.data[group]![marker] = scenario == "tombstone" ? LCSharedKeychainMigration.signedOut : Data("unknown-version".utf8)
            Store.data[Store.processGroup] = route
            let before = Store.data
            if scenario == "tombstone" {
                let candidate = try keychain.authenticationCandidate()
                precondition(candidate == nil)
            } else { expectFailure(1009) { _ = try keychain.authenticationCandidate() } }
            precondition(Store.data == before)
        case "foreign_conflict":
            Store.data[group] = route
            Store.data[Store.processGroup] = ["appleIDEmailAddress": Data("other@example.com".utf8), "appleIDPassword": Data("other".utf8)]
            let before = Store.data
            expectFailure(1008) { _ = try keychain.authenticationCandidate() }
            precondition(Store.data == before)
            let conflict = LCEmbeddedSharedKeychain.authenticationFailure(
                for: NSError(domain: "LiveContainerRefresh.Configuration", code: 1008))
            precondition(conflict.domain == "LiveContainerRefresh.Configuration" && conflict.code == 1008)
        case "identity_mismatch", "stale_candidate", "signout_during_verification", "foreign_change_during_verification":
            Store.data[group] = route
            let candidate = try keychain.authenticationCandidate()!
            if scenario == "stale_candidate" { Store.data[group]!["appleIDXcodeToken"] = Data("replacement".utf8) }
            if scenario == "signout_during_verification" { Store.data[group]![marker] = LCSharedKeychainMigration.signedOut }
            if scenario == "foreign_change_during_verification" { Store.data[Store.processGroup] = ["appleIDAdsid": Data("other".utf8)] }
            let before = Store.data
            expectFailure(1008) { try commit(candidate, dsid: scenario == "identity_mismatch" ? "another-id" : "id") }
            precondition(Store.data == before)
        case "auth_pending_restart":
            Store.data[group] = route.merging([marker: LCSharedKeychainMigration.pending]) { _, new in new }
            let before = Store.data
            let needsReconciliation = try keychain.storageRequiresReconciliation()
            precondition(needsReconciliation)
            expectFailure(1010) { _ = try keychain.authenticationSnapshot() }
            expectFailure(1010) { _ = try keychain.authenticationCandidate() }
            expectFailure(1010) { try keychain.writeAuthenticationCredentials(appleID: "test@example.com", password: "p", dsid: "id", authToken: "t") }
            precondition(Store.data == before)
        case "auth_each_write_failure":
            // Pending marker, email, DSID, token, ready marker. The token-only
            // route intentionally omits rather than fabricates a password.
            for failAt in 1...6 {
                Store.data[group] = route
                Store.setCalls = 0; Store.setFailAt = failAt
                let candidate = try keychain.authenticationCandidate()!
                expectFailure(-25291) { try commit(candidate) }
                precondition(Store.data[group] == route, "failed commit must restore markerless source")
            }
        case "auth_readback_failure":
            Store.data[group] = route
            let candidate = try keychain.authenticationCandidate()!
            Store.writes = 0; Store.corruptReadKey = "appleIDEmailAddress"; Store.corruptReadAfterWrites = 3
            expectFailure(1009) { try commit(candidate) }
            precondition(Store.data[group] == route)
        case "certificate_commit":
            Store.data[group] = [marker: LCSharedKeychainMigration.signedOut]
            try saveCertificate()
            let saved = try keychain.signingCertificateSnapshot()!
            precondition(saved.p12Data == Data("new-p12".utf8) && saved.password == "new-password")
            precondition(Store.data[group]?["importedCert_new-serial"] == saved.p12Data)
            precondition(Store.data[group]?[marker] == LCSharedKeychainMigration.signedOut)
            let needsReconciliation = try keychain.storageRequiresReconciliation()
            precondition(!needsReconciliation)
        case "certificate_each_write_failure":
            for failAt in 1...6 {
                let old = ["signingCertificate": Data("old-p12".utf8), "signingCertificatePassword": Data("old-password".utf8)]
                Store.data[group] = old; Store.setCalls = 0; Store.setFailAt = failAt
                expectFailure(-25291) { try saveCertificate() }
                precondition(Store.data[group] == old)
                let restored = try keychain.signingCertificateSnapshot()!
                precondition(restored.p12Data == old["signingCertificate"] && restored.password == "old-password")
            }
        case "certificate_readback_failure", "certificate_parse_failure":
            let old = ["signingCertificate": Data("old-p12".utf8), "signingCertificatePassword": Data("old-password".utf8)]
            Store.data[group] = old
            if scenario == "certificate_readback_failure" {
                Store.writes = 0; Store.corruptReadKey = "signingCertificate"; Store.corruptReadAfterWrites = 5
                expectFailure(1009) { try saveCertificate() }
            } else {
                expectFailure(1009) {
                    try keychain.writeSigningCertificate(p12Data: Data("bad-p12".utf8), password: "p", serial: "serial") { _, _ in
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                }
            }
            precondition(Store.data[group] == old)
        case "certificate_uncertain_restart":
            Store.data[group] = ["signingCertificate": Data("old-p12".utf8), "signingCertificatePassword": Data("old-password".utf8)]
            Store.failSetKey = "signingCertificatePassword"; Store.failSetKeyCount = 2
            expectFailure(1010) { try saveCertificate() }
            let restarted = Keychain(LCEmbeddedSharedKeychain.makeClient())
            let needsReconciliation = try restarted.storageRequiresReconciliation()
            precondition(needsReconciliation)
            expectFailure(1010) { _ = try restarted.signingCertificateSnapshot() }
            expectFailure(1010) { try saveCertificate() }
        case "certificate_reconcile_intended", "certificate_reconcile_partial":
            let old = ["signingCertificate": Data("old-p12".utf8), "signingCertificatePassword": Data("old-password".utf8)]
            Store.data[group] = old
            // All intended bytes reached Keychain, validation then failed and
            // the first rollback fence failed. The journal is the only proof.
            expectFailure(1010) {
                try keychain.writeSigningCertificate(p12Data: Data("new-p12".utf8), password: "new-password", serial: "new-serial") { _, _ in
                    Store.setFailAt = Store.setCalls + 1
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
            }
            if scenario == "certificate_reconcile_partial" {
                Store.data[group]!["signingCertificatePassword"] = Data("mixed-password".utf8)
                let before = Store.data
                expectFailure(1010) { try keychain.reconcileStorage() }
                precondition(Store.data == before)
            } else {
                try keychain.reconcileStorage()
                let requiresReconciliation = try keychain.storageRequiresReconciliation()
                let saved = try keychain.signingCertificateSnapshot()!
                precondition(!requiresReconciliation && saved.p12Data == Data("new-p12".utf8))
                precondition(Store.data[group]?["LCSharedCertificateTransactionV1"] == nil)
            }
        case "auth_reconcile_previous", "auth_reconcile_intended", "auth_reconcile_partial", "reconcile_preserves_tombstone", "auth_ready_with_retained_journal":
            let original = route
            let intended = route.merging(["appleIDEmailAddress": Data("test@example.com".utf8)]) { _, new in new }
            let journal: [String: Any] = ["version": 1, "keys": LCSharedKeychainMigration.authKeys,
                "original": original, "expected": intended, "expectedMarker": LCSharedKeychainMigration.ready]
            let data = try PropertyListSerialization.data(fromPropertyList: journal, format: .binary, options: 0)
            Store.data[group] = scenario == "auth_reconcile_previous" ? original : intended
            Store.data[group]![marker] = scenario == "reconcile_preserves_tombstone" ? LCSharedKeychainMigration.signedOut : LCSharedKeychainMigration.pending
            Store.data[group]!["LCSharedAuthenticationTransactionV1"] = data
            if scenario == "auth_ready_with_retained_journal" {
                Store.data[group]![marker] = LCSharedKeychainMigration.ready
                let blocked = try keychain.storageRequiresReconciliation()
                precondition(blocked)
                expectFailure(1010) { _ = try keychain.authenticationSnapshot() }
                expectFailure(1010) { try keychain.writeAuthenticationCredentials(appleID: "other@example.com", password: "p", dsid: "other", authToken: "other") }
            }
            if scenario == "auth_reconcile_partial" { Store.data[group]!["appleIDXcodeToken"] = Data("mixed".utf8) }
            let before = Store.data
            if scenario == "auth_reconcile_partial" {
                expectFailure(1010) { try keychain.reconcileStorage() }
                precondition(Store.data == before)
            } else if scenario == "reconcile_preserves_tombstone" {
                try keychain.reconcileStorage()
                precondition(Store.data[group]?[marker] == LCSharedKeychainMigration.signedOut)
                precondition(Store.data[group]?["appleIDXcodeToken"] == before[group]?["appleIDXcodeToken"])
            } else {
                try keychain.reconcileStorage()
                precondition(Store.data[group] == (scenario == "auth_reconcile_previous" ? original : intended.merging([marker: LCSharedKeychainMigration.ready]) { _, new in new }))
            }
        case "certificate_ready_failed_rollback_fences", "auth_ready_failed_rollback_fences":
            Store.failPendingAfterReady = true
            Store.writes = 0
            Store.corruptReadAfterWrites = 6
            if scenario == "certificate_ready_failed_rollback_fences" {
                Store.corruptReadKey = "signingCertificate"
                expectFailure(1010) { try saveCertificate() }
                precondition(Store.data[group]?["LCSharedCertificateCommitV1"] == LCSharedKeychainMigration.ready)
                expectFailure(1010) { _ = try keychain.signingCertificateSnapshot() }
                expectFailure(1010) { try saveCertificate() }
            } else {
                Store.data[group] = route
                Store.corruptReadKey = "appleIDEmailAddress"
                let candidate = try keychain.authenticationCandidate()!
                expectFailure(1010) { try commit(candidate) }
                precondition(Store.data[group]?[marker] == LCSharedKeychainMigration.ready)
                expectFailure(1010) { _ = try keychain.authenticationSnapshot() }
                expectFailure(1010) { try commit(candidate) }
            }
            let blocked = try keychain.storageRequiresReconciliation()
            precondition(blocked, "retained proof blocks readers even when rollback cannot change ready marker")
            try keychain.reconcileStorage()
            let stillBlocked = try keychain.storageRequiresReconciliation()
            precondition(!stillBlocked, "exact intended readback resolves without rewriting credentials")
        case "certificate_no_cross_group_password":
            Store.data[group] = ["signingCertificate": Data("selected-p12".utf8)]
            Store.data[Store.processGroup] = ["signingCertificatePassword": Data("foreign-password".utf8)]
            let saved = try keychain.signingCertificateSnapshot()!
            precondition(saved.password == nil && saved.p12Data == Data("selected-p12".utf8))
        default: preconditionFailure("unknown scenario")
        }
        print("PASSED: " + scenario)
    }
}
'''

SCENARIOS = (
    "token_only", "markerless_token", "markerless_password", "markerless_partial",
    "tombstone", "unknown_marker", "foreign_conflict", "identity_mismatch", "stale_candidate",
    "signout_during_verification", "foreign_change_during_verification", "auth_pending_restart",
    "auth_each_write_failure", "auth_readback_failure", "certificate_commit", "certificate_each_write_failure",
    "certificate_readback_failure", "certificate_parse_failure", "certificate_uncertain_restart",
    "certificate_ready_failed_rollback_fences", "auth_ready_failed_rollback_fences",
    "certificate_no_cross_group_password", "certificate_reconcile_intended", "certificate_reconcile_partial",
    "auth_reconcile_previous", "auth_reconcile_intended", "auth_reconcile_partial", "reconcile_preserves_tombstone", "auth_ready_with_retained_journal",
)


class KeychainRecoveryTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("swiftc")
        if not compiler:
            raise unittest.SkipTest("swiftc unavailable; production Swift fault harness requires Swift")
        cls.temp = tempfile.TemporaryDirectory(prefix="lc-keychain-recovery-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "Recovery.swift"
        source.write_text(DOUBLES + existing.TEMPLATE.read_text() + existing.module.KEYCHAIN_ACCESS_ADAPTER + HARNESS)
        cls.executable = Path(cls.temp.name) / "recovery"
        result = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", str(source), "-o", str(cls.executable)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_production_transaction_fault_matrix(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.executable), scenario], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASSED: " + scenario, result.stdout)


class GeneratedCertificatePersistenceTests(unittest.TestCase):
    def test_exact_pinned_certificate_patch_orders_commit_before_activation(self):
        source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not source:
            self.skipTest("pinned SideStore source unavailable; required in combined CI")
        original = existing.read_pinned_source(source, "SideStore/Core/Certificates/CertificateManager.swift")
        # This is the only prior CertificateManager transformation in the
        # combined build. Confirm the audited risk before applying the fix.
        prepared = existing.service_module.headless_certificate_serial_log_redaction(original, "CertificateManager")
        self.assertIn("Keychain.shared.signingCertificate = p12Data", prepared)
        self.assertIn("Keychain.shared.signingCertificatePassword = password", prepared)
        fixed = existing.module.patch_certificate_manager(prepared)
        self.assertEqual(existing.module.patch_certificate_manager(fixed), fixed)
        start = fixed.index("public func setActiveCertificate(")
        end = fixed.index("public func clearActiveCertificate(", start)
        active = fixed[start:end]
        self.assertLess(active.index("try Keychain.shared.writeSigningCertificate"), active.index("self.activeCertificate = ActiveSigningCertificate"))
        self.assertIn("try Self.parse(storedData, password: storedPassword)", active)
        self.assertIn("parsed.serialNumber == cert.serialNumber", active)
        self.assertNotIn("saveCertificate(cert)", active)
        self.assertNotIn("Keychain.shared.signingCertificate =", active)
        self.assertIn("native.code == 1010", active)

    def test_generated_token_recovery_preserves_identity_and_errors(self):
        source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not source:
            self.skipTest("pinned SideStore source unavailable; required in combined CI")
        original = existing.read_pinned_source(source, "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift")
        prepared = existing.service_module.patch_sign_in_operation(original)
        fixed = existing.module.patch_sign_in_operation(prepared)
        self.assertEqual(existing.module.patch_sign_in_operation(fixed), fixed)
        start = fixed.index("private func silentSignIn()")
        end = fixed.index("private func authenticationLoop()", start)
        silent = fixed[start:end]
        self.assertLess(silent.index("authenticateWithToken("), silent.index("writeVerifiedAuthentication("))
        self.assertIn("capturedStamp == AuthManager.shared.v3IdentityStamp", silent)
        self.assertIn("account.identifier == session.dsid", silent)
        self.assertIn("candidate.matchesVerifiedIdentity", silent)
        self.assertIn("if error is V3AccountOperationError { throw error }", silent)
        self.assertIn("Task.isCancelled", silent)
        self.assertNotIn('password: ""', silent)
