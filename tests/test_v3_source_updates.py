"""Regression coverage for v3.0.3 source-app Update support (issue #30)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"


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


if __name__ == "__main__":
    unittest.main()
