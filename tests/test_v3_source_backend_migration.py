"""Behavioral and boundary coverage for shared AppManager source mutations."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patch_v3_service

RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
HARNESS = ROOT / "tests/fixtures/v3_source_backend_migration_harness.swift"
COREDATA_HARNESS = ROOT / "tests/fixtures/v3_source_view_context_coredata_harness.swift"
SWIFTC = shutil.which("swiftc")


def pinned_source_root():
    override = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
    candidates = [Path(override)] if override else [
        ROOT.parent / "upstream/SideStore", ROOT / "work/EmbeddedSideStore",
        ROOT.parent.parent / "work/EmbeddedSideStore",
    ]
    for candidate in candidates:
        path = candidate / "AltStore/Managing Apps/AppManager.swift"
        if not path.is_file():
            continue
        pinned_file = subprocess.run(["git", "-C", str(candidate), "cat-file", "-e",
                                      f"{patch_v3_service.PINS[1]}:AltStore/Managing Apps/AppManager.swift"],
                                     capture_output=True, text=True)
        if pinned_file.returncode == 0:
            return candidate
        if override:
            raise AssertionError(f"SideStore source must be pinned to {patch_v3_service.PINS[1]}")
    raise unittest.SkipTest("pinned SideStore checkout unavailable")


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
    source_root = pinned_source_root()
    original = subprocess.check_output([
        "git", "-C", str(source_root), "show",
        f"{patch_v3_service.PINS[1]}:AltStore/Managing Apps/AppManager.swift"],
        text=True, encoding="utf-8")
    generated = patch_v3_service.headless_app_manager_source_mutations(original)
    signatures = (
        "func addConfirmed(sourceURL: URL)",
        "private func persistConfirmedSource(",
        "func remove(@AsyncManaged _ source: Source, presentingViewController: UIViewController) async throws",
        "func removeConfirmed(identifier: String,",
    )
    return "\n\n".join(extract_swift_function(generated, signature) for signature in signatures)


def production_persist_helper():
    source_root = pinned_source_root()
    original = subprocess.check_output([
        "git", "-C", str(source_root), "show",
        f"{patch_v3_service.PINS[1]}:AltStore/Managing Apps/AppManager.swift"],
        text=True, encoding="utf-8")
    generated = patch_v3_service.headless_app_manager_source_mutations(original)
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
        source_root = pinned_source_root()
        original = subprocess.check_output([
            "git", "-C", str(source_root), "show",
            f"{patch_v3_service.PINS[1]}:AltStore/Managing Apps/AppManager.swift"],
            text=True, encoding="utf-8")
        generated = patch_v3_service.headless_app_manager_source_mutations(original)
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
        generated_headless = patch_v3_service.headless_app_manager(original)
        self.assertEqual(generated_headless,
                         patch_v3_service.headless_app_manager(generated_headless),
                         "pinned AppManager source mutation patch must be idempotent")

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

    def test_previous_prepared_tree_fails_closed_and_requires_regeneration(self):
        prepared_version = patch_v3_service.PATCH_VERSION - 1
        self.assertEqual(patch_v3_service.PATCH_VERSION, 44)
        with tempfile.TemporaryDirectory(prefix="v3-source-patch-version-") as temporary:
            root = Path(temporary)
            live = root / "live"
            side = root / "side"
            live.mkdir()
            side.mkdir()
            anisette = side / "AltStore/Settings/AnisetteServerList.swift"
            anisette.parent.mkdir(parents=True)
            anisette.write_text("prepared fixture", encoding="utf-8")
            manifest = live / ".v3-command-patch.json"
            manifest.write_text(json.dumps({"patchVersion": prepared_version}) + "\n", encoding="utf-8")
            before = {path: path.read_bytes() for path in (anisette, manifest)}

            def pinned_revision(args, text=False):
                expected = patch_v3_service.PINS[0] if Path(args[2]) == live else patch_v3_service.PINS[1]
                return expected

            with mock.patch.object(patch_v3_service.subprocess, "check_output", side_effect=pinned_revision), \
                    mock.patch.object(patch_v3_service, "headless_anisette_models",
                                      return_value="fixture model"):
                with self.assertRaisesRegex(
                        SystemExit,
                        f"prepared patch version {prepared_version} cannot be migrated safely "
                        f"to v{patch_v3_service.PATCH_VERSION}"):
                    patch_v3_service.patch(live, side)

            self.assertEqual(before, {path: path.read_bytes() for path in before},
                             "a prepared v40 tree must be rejected without partial writes")


if __name__ == "__main__":
    unittest.main()
