"""Behavioral and boundary coverage for shared AppManager source mutations."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from tests.owner_source import owner_source
from tests.support.product_sources import ProductSource

RUNTIME = ProductSource("v3_headless_runtime.swift")
SERVICE = ProductSource("v3_sidestore_service.swift")
HARNESS = ROOT / "tests/fixtures/v3_source_backend_migration_harness.swift"
COREDATA_HARNESS = ROOT / "tests/fixtures/v3_source_view_context_coredata_harness.swift"
SWIFTC = shutil.which("swiftc")


def extract_swift_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for offset in range(opening, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                return source[start:offset + 1]
    raise AssertionError(f"unbalanced function: {signature}")


def production_core_methods():
    generated = owner_source("AltStore/Managing Apps/AppManager.swift", "SideStore")
    signatures = (
        "func addConfirmed(sourceURL: URL)",
        "private func persistConfirmedSource(",
        "func remove(@AsyncManaged _ source: Source, presentingViewController: UIViewController) async throws",
        "func removeConfirmed(identifier: String,",
    )
    return "\n\n".join(extract_swift_function(generated, signature) for signature in signatures)


def production_persist_helper():
    generated = owner_source("AltStore/Managing Apps/AppManager.swift", "SideStore")
    return extract_swift_function(generated, "private func persistConfirmedSource(")


class V3SourceBackendMigrationTests(unittest.TestCase):
    def test_coredata_persist_helper_resolves_view_context_source_after_background_save(self):
        if sys.platform != "darwin":
            self.skipTest("Core Data behavioral harness requires macOS")
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; Core Data harness runs in macOS CI")
        helper = production_persist_helper()
        fixture = COREDATA_HARNESS.read_text(encoding="utf-8")
        self.assertEqual(fixture.count("__PRODUCTION_PERSIST_HELPER__"), 1)
        fixture = fixture.replace("__PRODUCTION_PERSIST_HELPER__", helper)
        with tempfile.TemporaryDirectory(prefix="v3-source-coredata-") as temporary:
            main = Path(temporary) / "main.swift"
            executable = Path(temporary) / "source-coredata"
            main.write_text(fixture, encoding="utf-8")
            compiled = subprocess.run(
                [SWIFTC, "-parse-as-library", "-framework", "CoreData", str(main), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_SOURCE_VIEW_CONTEXT_COREDATA_PASS", result.stdout)

    def test_production_appmanager_core_executes_persistence_matrix(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; executable harness runs in macOS CI")
        methods = production_core_methods()
        fixture = HARNESS.read_text(encoding="utf-8")
        self.assertEqual(fixture.count("__PRODUCTION_SOURCE_METHODS__"), 1)
        fixture = fixture.replace("__PRODUCTION_SOURCE_METHODS__", methods)
        with tempfile.TemporaryDirectory(prefix="v3-source-backend-") as temporary:
            main = Path(temporary) / "main.swift"
            executable = Path(temporary) / "source-backend"
            main.write_text(fixture, encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(main), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_SOURCE_BACKEND_MIGRATION_PASS", result.stdout)

    def test_ui_and_headless_use_one_appmanager_mutation_core(self):
        generated = owner_source("AltStore/Managing Apps/AppManager.swift", "SideStore")
        ui_add = generated[generated.index("func add(@AsyncManaged _ source: Source,"):
                            generated.index("func addConfirmed(sourceURL: URL)")]
        ui_remove = generated[generated.index("func remove(@AsyncManaged _ source: Source,"):
                               generated.index("func removeConfirmed(identifier: String,")]
        self.assertIn("persistConfirmedSource(fetched, in: context, notificationSource: source)", ui_add)
        self.assertIn("SourceError.duplicate(source, existingSource: nil)", ui_add)
        self.assertIn("removeConfirmed(identifier: sourceID, notificationSource: source)", ui_remove)
        default_guard = ui_remove.index("guard sourceID != Source.altStoreIdentifier")
        confirmation = ui_remove.index("presentingViewController.presentConfirmationAlert")
        self.assertLess(default_guard, confirmation,
                        "the UI must reject the default source before presenting confirmation")
        self.assertIn("OperationError.forbidden", ui_remove)
        self.assertIn("guard source.identifier != Source.altStoreIdentifier", generated)
        runtime = RUNTIME.read_text(encoding="utf-8")
        add = runtime[runtime.index("static func sourceAddConfirmed(urlString:"):
                     runtime.index("static func authoritativeSourceRows()")]
        remove = extract_swift_function(runtime, "static func sourceRemoveConfirmed(identifier:")
        self.assertIn("AppManager.shared.addConfirmed(sourceURL: url)", add)
        self.assertIn("AppManager.shared.removeConfirmed(identifier: identifier)", remove)
        for forbidden in ("context.save()", "context.delete(", "Source.altStoreIdentifier",
                          "didAddSourceNotification", "didRemoveSourceNotification", "source.isAdded()"):
            self.assertNotIn(forbidden, add + remove)

    def test_issue38_readback_and_catalog_failure_contract_remain(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        add = runtime[runtime.index("static func sourceAddConfirmed(urlString:"):
                     runtime.index("static func authoritativeSourceRows()")]
        catalog_start = service.index('case "catalog":')
        catalog = service[catalog_start:service.index('case "refreshSources":', catalog_start)]
        dispatch_add = service[service.index('case "sourceAddConfirmed":'):
                               service.index('case "sourceRemoveConfirmed":')]
        self.assertIn("verificationContext.count(for: query)", add)
        self.assertIn("authoritativeCount: authoritativeCount", add)
        self.assertIn("authoritativeSourceRows()", dispatch_add)
        self.assertIn("persistedSources.contains", dispatch_add)
        self.assertIn("V3SideStoreServiceError.catalogSourceUnavailable", catalog)
        self.assertIn('response["failure"] = CombinedFailure(operation: "catalog"', service)



if __name__ == "__main__":
    unittest.main()
