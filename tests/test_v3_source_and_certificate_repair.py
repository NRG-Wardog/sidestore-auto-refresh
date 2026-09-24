"""#38 persisted source add and #39 JIT-Less certificate repair contracts."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"


def text(path):
    return path.read_text(encoding="utf-8")


def region(source, start, end):
    value = source[source.index(start):]
    return value[:value.index(end)]


class SourceAddPersistenceContractTests(unittest.TestCase):
    def test_headless_add_uses_persisted_check_save_and_fresh_context_verification(self):
        runtime = text(RUNTIME)
        method = region(runtime, "static func sourceAddConfirmed(urlString:",
                        "static func sourceRemoveConfirmed(identifier:")
        self.assertIn("V3SourceAddPersistencePolicy.validatedURL(urlString)", method)
        self.assertIn("AppManager.shared.fetchSource", method)
        self.assertIn("source.isAdded()", method)
        self.assertIn("V3SourceAddPersistencePolicy.decision(sourceIsPersisted: wasPersisted)", method)
        self.assertNotIn("background.fetch(NSFetchRequest<Source>", method)
        self.assertLess(method.index("source.isAdded()"), method.index("background.save()"))
        self.assertLess(method.index("background.save()"), method.index("verificationContext"))
        self.assertIn("verificationContext.count(for: query)", method)
        self.assertIn("authoritativeCount: authoritativeCount", method)
        self.assertLess(method.index("authoritativeCount: authoritativeCount"),
                        method.index("didAddSourceNotification"))
        self.assertIn("object: persistedSource", method)
        self.assertIn("static func authoritativeSourceRows()", runtime)

    def test_service_returns_authoritative_source_snapshot_and_result(self):
        service = text(SERVICE)
        method = region(service, 'case "sourceAddConfirmed":', 'case "sourceRemoveConfirmed":')
        for token in ("sourceAddConfirmed(urlString: target)", "snapshot()",
                      "authoritativeSourceRows()", "persistenceUnverified",
                      'updated["sources"] = persistedSources',
                      "updated.merging(addResult)"):
            self.assertIn(token, method)

    def test_host_shows_success_only_for_verified_authoritative_result(self):
        shell = text(SHELL)
        method = region(shell, "private func confirmAdd(url:", "private func confirmRemove(id:")
        for token in ("V3SourceAddPersistencePolicy.confirmationMessage(result)",
                      'result["persistenceVerified"]', "sources.contains", "status.accept(result)",
                      'status.sourceURL = ""', "notice = message"):
            # The policy validates persistenceVerified and both outcome flags.
            self.assertTrue(token in method or token in text(ROOT / "scripts/templates/v3_behavioral_primitives.swift"))
        self.assertNotIn('notice = "Source added."', method)
        self.assertIn("Section(\"What happened\")", shell)
        self.assertIn("sourceFailure.technicalDetails", shell)

    def test_pinned_sidestore_uses_fresh_context_source_is_added_semantics(self):
        side = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side:
            self.skipTest("pinned SideStore source is supplied by macOS CI")
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import patch_v3_service
        app_manager = subprocess.check_output([
            "git", "-C", side, "show",
            patch_v3_service.PINS[1] + ":AltStore/Managing Apps/AppManager.swift"],
            text=True, encoding="utf-8")
        source_model = subprocess.check_output([
            "git", "-C", side, "show",
            patch_v3_service.PINS[1] + ":AltStore/Core/Model/Source.swift"],
            text=True, encoding="utf-8")
        add = region(app_manager, "func add(@AsyncManaged _ source: Source,", "func remove(")
        is_added = region(source_model, "nonisolated func isAdded() async throws -> Bool", "var isPersisted:")
        self.assertIn("fetchSource(sourceURL: sourceURL, managedObjectContext: context)", add)
        self.assertIn("fetchedSource.isAdded()", add)
        self.assertIn("context.save()", add)
        self.assertIn("didAddSourceNotification", add)
        self.assertIn("newBackgroundContext()", is_added)
        self.assertIn("backgroundContext.count(for: fetchRequest)", is_added)
        self.assertIn("IFF it has been saved to disk", source_model)


class JITLessCertificateSyncContractTests(unittest.TestCase):
    def test_sync_uses_side_store_active_public_identity_and_private_shared_keychain(self):
        runtime = text(RUNTIME)
        shell = text(SHELL)
        state = region(runtime, "static func certificateState()", "static func accountExport(")
        sync = region(shell, "private func syncJITLessCertificate()", "private func certComparison(")
        for token in ("CertificateManager.shared.activeCertificate", "certificateIdentitySHA256",
                      "SHA256.hash(data: certificateDER)"):
            self.assertIn(token, state)
        for token in ('account: "signingCertificate"', 'account: "signingCertificatePassword"',
                      '"com.kdt.livecontainer"', "SecPKCS12Import", "LCUtils.getCertTeamId",
                      "certificateIdentitySHA256", "teamMatches", "activeExpired"):
            self.assertIn(token, sync)
        self.assertNotIn('payload: ["p12"', sync)
        self.assertNotIn('payload: ["password"', sync)

    def test_sync_validates_before_batch_write_reloads_and_rolls_back(self):
        shell = text(SHELL)
        sync = region(shell, "private func syncJITLessCertificate()", "private func keychainData(")
        self.assertLess(sync.index("parsedCertificate(data: data, password: password)"),
                        sync.index("writeJITLessCertificate(data: data"))
        self.assertIn("CFPreferencesSetMultiple", shell)
        self.assertIn('"LCCertificateData"', shell)
        self.assertIn('"LCCertificatePassword"', shell)
        self.assertIn('"LCCertificateUpdateDate"', shell)
        self.assertIn("writeJITLessCertificate(data: oldData, password: oldPassword, updateDate: oldDate)", sync)
        self.assertLess(sync.index("let validation = await validateCurrentJITLessCertificate()"),
                        sync.index("notice = \"The JIT-Less copy now matches"))
        self.assertIn("await reload()", sync)
        self.assertIn("Section(\"JIT-Less Certificate\")", shell)
        self.assertIn("Sync JIT-Less Certificate from SideStore", shell)
        self.assertIn("status.certificatesPresented = true", shell)

    def test_sync_diagnostics_are_static_safe_vocabulary(self):
        primitives = text(ROOT / "scripts/templates/v3_behavioral_primitives.swift")
        issue = region(primitives, "enum V3JITLessCertificateSyncIssue:", "enum V3JITLessCertificateSyncAssessment:")
        for sensitive in ("p12Data", "privateKey", "password=", "certificateDER", "appleID"):
            self.assertNotIn(sensitive, issue)
        for token in ("activeCertificateRevoked", "keyMaterialUnavailable", "teamMismatch",
                      "validationUnavailable", "schema=1\\noperation=jitlessCertificateSync"):
            self.assertIn(token, issue)

    def test_pinned_livecontainer_import_uses_canonical_active_keychain_slots(self):
        live = os.environ.get("LIVE_CONTAINER_TEST_SOURCE")
        if not live:
            self.skipTest("pinned LiveContainer source is supplied by macOS CI")
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import patch_v3_service
        source = subprocess.check_output([
            "git", "-C", live, "show",
            patch_v3_service.PINS[0] + ":LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"],
            text=True, encoding="utf-8")
        import_flow = region(source, "func importCertificateFromSideStore() async", "func onSideStoreCertificateCallback")
        callback = region(source, "func onSideStoreCertificateCallback", "func removeCertificate()")
        for token in ('"signingCertificate"', '"signingCertificatePassword"',
                      '"com.kdt.livecontainer"'):
            self.assertIn(token, import_flow)
        for token in ('"LCCertificateData"', '"LCCertificatePassword"', '"LCCertificateUpdateDate"'):
            self.assertIn(token, callback)


if __name__ == "__main__":
    unittest.main()
