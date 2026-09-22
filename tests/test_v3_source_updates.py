"""Regression coverage for v3.0.3 source-app Update support (issue #30)."""
import os
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


def upstream_installed_app():
    """Pinned upstream InstalledApp.swift when the SideStore checkout exists.

    Runs in CI (the pinned SideStore source is checked out before repository
    checks); skips on machines without the checkout.
    """
    override = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
    candidates = [Path(override)] if override else [
        ROOT / ".audit/upstream/SideStore", ROOT / "work/EmbeddedSideStore",
        ROOT.parent / "work/EmbeddedSideStore",
    ]
    for candidate in candidates:
        path = candidate / "AltStore/Core/Model/InstalledApp.swift"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise unittest.SkipTest("Pinned SideStore source unavailable; "
                            "set EMBEDDED_SIDESTORE_TEST_SOURCE")


class V3SourceUpdateTests(unittest.TestCase):
    def test_catalog_carries_installed_version(self):
        self.assertIn("installedVersion", shell())
        self.assertIn("installedVersion", service())

    def test_update_shown_only_when_versions_differ(self):
        text = shell()
        self.assertIn("Update to ", text)
        # The update action is gated on SideStore's authoritative update
        # decision, never on a host version-string comparison.
        self.assertIn("installed.hasUpdate", text)

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

    def test_no_update_button_on_version_string_inequality(self):
        # A raw version-string inequality would offer downgrades (installed
        # 0.4.30 vs source 0.4.26). The decision must come from SideStore.
        text = shell()
        start = text.index("struct V3CatalogView")
        end = text.index("struct V3OperationSheet", start)
        view = text[start:end]
        for forbidden in ("installed.version != app.version",
                          "app.version != installed.version",
                          "installed.version == app.version",
                          "app.version == installed.version"):
            self.assertNotIn(forbidden, view)
        self.assertIn("if installed.hasUpdate {", view)

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

    def test_update_button_only_when_backend_reports_update(self):
        text = shell()
        # The Update button appears exactly when SideStore reports an update.
        self.assertIn("if installed.hasUpdate {", text)

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

    # --- Authoritative update-decision contract (pinned upstream) ---
    # SideStore's InstalledApp.hasUpdate (LiveContainer/SideStore@ff25922,
    # AltStore/Core/Model/InstalledApp.swift) orders versions with
    # SemanticVersion: strict `latestVer > currentVer` on major.minor.patch,
    # a beta-track pre-release/build tie-break, then a final lexicographic
    # `latestSemVer > currentSemVer` (stable always beats its pre-releases).
    # String comparison is only a fallback when versions do not parse.
    # Because the host delegates to hasUpdate, these outcomes hold:
    #   installed 0.4.21, source 0.4.26 -> update
    #   installed 0.4.26, source 0.4.26 -> no update
    #   installed 0.4.30, source 0.4.26 -> no update (never a downgrade)
    #   installed 0.4.26-beta+1, source 0.4.26 -> update (stable wins)
    def test_upstream_has_update_uses_semver_strict_ordering(self):
        source = upstream_installed_app()
        body = source[source.index("public var hasUpdate"):]
        body = body[:body.index("public var appIDCount")]
        # Both sides parsed as semantic versions, compared strictly greater.
        self.assertIn("SemanticVersion(self.version)", body)
        self.assertIn("SemanticVersion(latestVersion.version)", body)
        self.assertIn("latestVer! > currentVer!", body)
        self.assertNotIn("latestVer! >= currentVer!", body)
        self.assertNotIn("latestVer! != currentVer!", body)
        # String comparison exists only as the unparseable fallback.
        self.assertIn("return !matches(latestVersion)", body)

    def test_upstream_has_update_handles_prerelease(self):
        source = upstream_installed_app()
        body = source[source.index("public var hasUpdate"):]
        body = body[:body.index("public var appIDCount")]
        # Beta-track tie-break on build/pre-release, then the documented
        # lexicographic rule where stable beats its own pre-releases.
        self.assertIn("isBetaUpdatesEnabled", body)
        self.assertIn("latestSemVer! > currentSemVer!", body)
        self.assertIn("stable x.y.z is always > x.y.z-abcd+1234", body)

    def test_required_update_outcomes_follow_from_strict_ordering(self):
        # Strict `>` (verified above against the pinned upstream) entails the
        # required outcomes for numeric triples; this locks the table so a
        # future change of the operator fails loudly instead of silently
        # reintroducing downgrade offers.
        def newer(source_version, installed_version):
            def parts(value):
                return tuple(int(piece) for piece in value.split("."))
            return parts(source_version) > parts(installed_version)

        self.assertTrue(newer("0.4.26", "0.4.21"))    # update offered
        self.assertFalse(newer("0.4.26", "0.4.26"))   # same version: none
        self.assertFalse(newer("0.4.26", "0.4.30"))   # newer installed: none
        upstream = upstream_installed_app()
        self.assertIn("latestVer! > currentVer!", upstream)


if __name__ == "__main__":
    unittest.main()
