"""Regression coverage for v3.0.3 source-app Update support (issue #30)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"


def shell():
    return SHELL.read_text(encoding="utf-8")


def service():
    return SERVICE.read_text(encoding="utf-8")


class V3SourceUpdateTests(unittest.TestCase):
    def test_catalog_carries_installed_version(self):
        self.assertIn("installedVersion", shell())
        self.assertIn("installedVersion", service())

    def test_update_shown_only_when_versions_differ(self):
        text = shell()
        self.assertIn("Update to ", text)
        # The update action is gated on a real version difference.
        self.assertIn("installed.version != app.version", text)

    def test_update_uses_existing_pipeline(self):
        text = shell()
        self.assertIn('status.perform("update"', text)
        # No duplicate signing/install implementation in the host.
        self.assertNotIn("ALTSigner", text)
        self.assertNotIn("CodeSignValidator", text)

    def test_service_supports_update_operation(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        # Update runs through the headless operation driver (opStart kind
        # "update"), using the existing SideStore update pipeline.
        self.assertIn('case "update":', runtime)
        self.assertIn(".update(appVersion", runtime)
        self.assertIn("performSingleOperation", runtime)
        # The service dispatcher routes opStart into the headless runtime.
        self.assertIn('case "opStart"', service())

    def test_no_update_when_versions_match(self):
        # Same condition that shows Update must hide it on equality.
        text = shell()
        self.assertIn("installed.version != app.version", text)

    def test_malformed_source_handled(self):
        shell_text = shell()
        service_text = service()
        # Missing version/download URL fall back instead of crashing,
        # and install controls are disabled without a usable target.
        self.assertIn('"Unavailable"', service_text)
        self.assertIn('"canInstall": app.latestSupportedVersion != nil', service_text)
        self.assertIn("app.downloadURL.isEmpty", shell_text)
        self.assertIn(".disabled(!app.canInstall", shell_text)

    def test_installed_app_update_action_exists(self):
        text = shell()
        self.assertIn("hasUpdate", text)
        self.assertIn('Button("Update")', text)

    # --- Comprehensive update flow verification ---
    def test_catalog_includes_installed_id_and_version(self):
        text = service()
        # The catalog response includes both installedID and installedVersion
        self.assertIn('"installedID"', text)
        self.assertIn('"installedVersion"', text)
        self.assertIn('app.installedApp?.version', text)

    def test_update_button_only_when_can_install_and_version_differs(self):
        text = shell()
        # Update button requires canInstall AND version difference
        self.assertIn("app.canInstall && installed.version != app.version", text)

    def test_update_calls_opStart_with_kind_update(self):
        text = shell()
        # Update action routes through opStart with kind "update"
        self.assertIn('status.perform("update"', text)
        # Verify the service handles opStart with update kind
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('case "update"', runtime)

    def test_update_uses_existing_appmanager_update(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        # The update operation uses the pipeline runner with .update operation
        self.assertIn(".update(appVersion", runtime)
        self.assertIn("performSingleOperation", runtime)

    def test_source_unavailable_shows_unavailable_version(self):
        text = service()
        # When source is unavailable, version shows "Unavailable"
        self.assertIn('"Unavailable"', text)

    def test_missing_download_url_disables_install(self):
        text = shell()
        self.assertIn("app.downloadURL.isEmpty", text)
        self.assertIn(".disabled(!app.canInstall", text)

    def test_can_install_requires_latest_supported_version(self):
        text = service()
        self.assertIn('"canInstall": app.latestSupportedVersion != nil', text)

    def test_update_pipeline_error_surfaces_structured_failure(self):
        # The CombinedFailure.capture in V3OperationCenter.terminalFailure
        # ensures errors are structured with stage/installation
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('"update": stage = .installation', runtime)
        self.assertIn("terminalFailure", runtime)


if __name__ == "__main__":
    unittest.main()
