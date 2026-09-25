"""Source persistence and canonical JIT-Less setup contracts."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"
PATCH_SHELL = ROOT / "scripts/patch_v3_unified_shell.py"


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
        self.assertNotIn("background.fetch(NSFetchRequest<Source>", method)
        self.assertLess(method.index("source.isAdded()"), method.index("background.save()"))
        self.assertLess(method.index("background.save()"), method.index("verificationContext"))
        self.assertIn("verificationContext.count(for: query)", method)
        self.assertIn("authoritativeCount: authoritativeCount", method)
        self.assertIn("object: persistedSource", method)
        self.assertIn("static func authoritativeSourceRows()", runtime)

    def test_service_returns_authoritative_source_snapshot_and_result(self):
        service = text(SERVICE)
        method = region(service, 'case "sourceAddConfirmed":', 'case "sourceRemoveConfirmed":')
        for token in ("sourceAddConfirmed(urlString: target)", "snapshot()",
                      "authoritativeSourceRows()", "persistenceUnverified",
                      'updated["sources"] = persistedSources', "updated.merging(addResult)"):
            self.assertIn(token, method)

    def test_host_shows_success_only_for_verified_authoritative_result(self):
        shell = text(SHELL)
        method = region(shell, "private func confirmAdd(url:", "private func confirmRemove(id:")
        for token in ("V3SourceAddPersistencePolicy.confirmationMessage(result)",
                      'result["persistenceVerified"]', "sources.contains", "status.accept(result)",
                      'status.sourceURL = ""', "notice = message"):
            self.assertTrue(token in method or token in text(PRIMITIVES))
        self.assertNotIn('notice = "Source added."', method)
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


class CanonicalJITLessRouteTests(unittest.TestCase):
    def test_health_and_quick_setup_forward_to_livecontainer_canonical_route(self):
        shell = text(SHELL)
        health = region(shell, "struct V3HealthView", "struct V3BackupsView")
        setup = region(shell, "struct V3SetupAssistantView", "struct V3HomeServiceHeader")
        patch = text(PATCH_SHELL)
        for token in ("livecontainer://jitless-setup", "Open JIT-Less Setup"):
            self.assertIn(token, health)
        for token in ("openCanonicalJITLessSetup()", 'Button("Set Up JIT-Less")',
                      'Button("Refresh JIT-Less Certificate")'):
            self.assertIn(token, setup)
        for token in ("jitless-setup", "importCertificateFromSideStore()",
                      "jitless-diagnose", "V3CanonicalJITLessCertificateUpdated"):
            self.assertIn(token, patch)

    def test_custom_copy_engine_and_side_store_keychain_access_are_absent(self):
        shell = text(SHELL)
        primitives = text(PRIMITIVES)
        for forbidden in ("syncJITLessCertificate", "V3JITLessCertificateSyncAssessment",
                          "CFPreferencesSetMultiple", "signingCertificatePassword", "SecItemCopyMatching"):
            self.assertNotIn(forbidden, shell + primitives)

    def test_pinned_livecontainer_settings_remains_the_import_authority(self):
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
        for token in ('"signingCertificate"', '"signingCertificatePassword"', '"com.kdt.livecontainer"'):
            self.assertIn(token, import_flow)
        for token in ('"LCCertificateData"', '"LCCertificatePassword"', '"LCCertificateUpdateDate"'):
            self.assertIn(token, callback)


if __name__ == "__main__":
    unittest.main()
