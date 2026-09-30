"""Source-level guards for the Keychain coordination protocol.

The executable Swift harness in test_embedded_keychain.py injects sign-out at
the snapshot boundary. These checks run on hosts without swiftc and verify the
production adapters use the process-shared lock around the relevant operations.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
KEYCHAIN = (ROOT / "scripts/templates/embedded_shared_keychain.swift").read_text(encoding="utf-8")
HANDOFF = (ROOT / "scripts/templates/v3_secret_handoff.swift").read_text(encoding="utf-8")
IPA_STAGING = (ROOT / "scripts/templates/v3_ipa_staging.swift").read_text(encoding="utf-8")
FAILURE = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
RUNTIME = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")


class KeychainCoordinationSourceTests(unittest.TestCase):
    def test_process_lock_resolves_the_shared_runtime_group_without_app_target_helpers(self):
        lock = HANDOFF[HANDOFF.index("enum V3AppGroupProcessLock {"):HANDOFF.index("enum V3SecretHandoffError")]
        # The lock is compiled into three modules, so it may only use Foundation
        # and the one shared identity. A fixed packaged group would put the host
        # and the service in different containers, and the lock would stop
        # coordinating them at all on a re-signed build.
        self.assertIn("V3SharedAppGroup.runtimeIdentity(selectedGroup: selectedGroup)", lock)
        self.assertIn("container = shared.containerRoot", lock)
        self.assertNotIn("forSecurityApplicationGroupIdentifier: \"group.", lock)
        self.assertNotIn("Bundle.main.altstoreAppGroup", lock)
        self.assertNotIn("LCSharedUtils", lock)
        # The same identity produces the staging container, so the lock file and
        # the staged IPA cannot land in different App Groups.
        self.assertIn("static let sideStoreAppGroupIdentifier = V3SharedAppGroup.packagedGroup", IPA_STAGING)
        self.assertIn('static let packagedGroup = "group.com.SideStore.SideStore"',
                      (ROOT / "scripts/templates/v3_shared_app_group.swift").read_text(encoding="utf-8"))

    def test_stale_migration_snapshot_rechecks_tombstone_before_writing(self):
        start = KEYCHAIN.index("static func prepare(group: String")
        end = KEYCHAIN.index("// LC_SHARED_MIGRATION_POLICY_END", start)
        migration = KEYCHAIN[start:end]
        self.assertIn("afterSnapshot()", migration)
        self.assertIn("if currentMarker == signedOut { return false }", migration)
        self.assertLess(migration.index("if currentMarker == signedOut"),
                        migration.index("for key in source.keys.sorted()"))
        self.assertIn("refreshed.values.contains(where: { $0 == source })", migration)

    def test_verified_signout_tombstone_precedes_auth_deletion(self):
        start = KEYCHAIN.index("static func removeChecked(")
        end = KEYCHAIN.index("static func clearAll(", start)
        signout = KEYCHAIN[start:end]
        self.assertLess(signout.index("client.set(LCSharedKeychainMigration.signedOut"),
                        signout.index("try client.remove(key)"))
        self.assertLess(signout.index("== LCSharedKeychainMigration.signedOut"),
                        signout.index("try client.remove(key)"))
        self.assertIn("guard try client.getData(key) == nil", signout)
        self.assertIn("client.set(LCSharedKeychainMigration.signedOut", KEYCHAIN[KEYCHAIN.index("static func clearAll("):])

    def test_signout_failure_restores_snapshot_or_reports_unknown(self):
        start = KEYCHAIN.index("static func clearSignInInfoChecked(")
        end = KEYCHAIN.index("static func clearAll(", start)
        transaction = KEYCHAIN[start:end]
        self.assertLess(transaction.index("let saved = try keys.map"),
                        transaction.index("client.set(LCSharedKeychainMigration.signedOut"))
        self.assertIn("for (key, value) in saved", transaction)
        self.assertIn("LCSharedKeychainMigration.marker) == priorMarker", transaction)
        self.assertIn("code: 1010", transaction)
        self.assertIn("case keychainSignOutOutcomeUnknown", FAILURE)
        self.assertIn("reload account & signing to reconcile which apple account is active", FAILURE.lower())
        self.assertIn("code == 1010", RUNTIME)

    def test_migration_and_signout_use_process_shared_flock(self):
        self.assertIn("LCSharedKeychainFileLock.withLock(appGroup: installedAppGroup)", KEYCHAIN)
        # The migration lock must resolve the same runtime group as the secret
        # handoff. Trusting this bundle's own packaged declaration is what let
        # the credential migration and the handoff take different locks.
        self.assertNotIn("Bundle.main.altstoreAppGroup", KEYCHAIN)
        self.assertIn("let appGroup = V3SharedAppGroup.runtimeIdentity()?.identifier", KEYCHAIN)
        self.assertIn("V3SharedAppGroup.environmentGroup()", KEYCHAIN)
        self.assertIn("V3AppGroupProcessLock.withLock(containerRoot: shared.containerRoot, operation)", KEYCHAIN)
        self.assertIn("flock(descriptor, LOCK_EX)", HANDOFF)
        self.assertNotIn("flock(descriptor", KEYCHAIN)
        self.assertIn("try withSharedTransaction {", KEYCHAIN)
        self.assertIn("older SideStore binary does not participate", KEYCHAIN)
        self.assertIn("no client-side protocol can", KEYCHAIN)

    def test_handoff_copy_and_delete_are_inside_process_shared_lock(self):
        start = HANDOFF.index("private static func consume(_ token: String, kind: String")
        end = HANDOFF.index("static func sharedKeychainAccessGroup()", start)
        consume = HANDOFF[start:end]
        self.assertIn("V3AppGroupProcessLock.withLock(selectedGroup: selectedGroup)", consume)
        self.assertLess(consume.index("V3AppGroupProcessLock.withLock"),
                        consume.index("private static func consumeLocked"))
        self.assertLess(consume.index("SecItemCopyMatching"), consume.index("SecItemDelete"))
        self.assertIn("flock(descriptor, LOCK_EX)", HANDOFF)
        self.assertIn("NSLock is process local", HANDOFF)

    def test_every_handoff_entry_point_accepts_the_injected_runtime_group(self):
        # The host cannot reach LiveContainer's Objective-C helper from this
        # Foundation-only helper, so the selected group is injected explicitly
        # rather than discovered differently on each side.
        for signature in ("static func storeString(_ value: String, selectedGroup: String? = nil)",
                          "static func consumeString(_ token: String, selectedGroup: String? = nil)",
                          "static func storeStringDictionary(_ value: [String: String], selectedGroup: String? = nil)",
                          "static func consumeStringDictionary(_ token: String, selectedGroup: String? = nil)",
                          "static func discard(_ token: String, selectedGroup: String? = nil)",
                          "static func cleanupExpiredItems(selectedGroup: String? = nil)"):
            self.assertIn(signature, HANDOFF)
        self.assertIn("V3SecretHandoffStoreAdmission.add(selectedGroup: selectedGroup", HANDOFF)

    def test_authentication_signin_uses_complete_credential_transaction(self):
        runtime = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        start = runtime.index("def patch_sign_in_operation(text):")
        end = runtime.index("\n\ndef patch(", start)
        patcher = runtime[start:end]
        self.assertIn("V3_AUTH_CREDENTIAL_TRANSACTION_V1", patcher)
        self.assertIn("Keychain.shared.writeAuthenticationCredentials(appleID: appleID, password: password, dsid: session.dsid, authToken: session.authToken)", patcher)
        self.assertIn("func writeAuthenticationCredentials(appleID: String, password: String,", KEYCHAIN)

        transaction = KEYCHAIN[
            KEYCHAIN.index("static func writeAuthenticationCredentials("):
            KEYCHAIN.index("    private static func writeOne(")
        ]
        self.assertLess(transaction.index("try client.set(LCSharedKeychainMigration.signedOut"),
                        transaction.index("for key in keys"))
        self.assertLess(transaction.index("guard written == expected"),
                        transaction.index("try client.set(LCSharedKeychainMigration.ready"))
        self.assertLess(transaction.index("guard committed == expected"),
                        transaction.index("note(\"authWrite\", status: 0)"))
        self.assertIn("note(\"authWrite\", status: 1010)", transaction)
        self.assertIn("native.domain == keychainDomain && native.code == 1010", KEYCHAIN)
        self.assertNotIn("lastIssues", KEYCHAIN,
                         "authentication guidance must derive from the current thrown error")


if __name__ == "__main__":
    unittest.main()
