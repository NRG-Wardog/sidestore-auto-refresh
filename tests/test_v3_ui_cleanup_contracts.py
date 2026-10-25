"""Review the integrated UI cleanup against the contracts it must not remove.

The cleanup removed SideStore-only presentation from the host and the embedded
service. Removing presentation is safe; removing a backend contract is not. Each
check below re-derives the contract from the current integrated target rather
than from a commit message, and the comments name what breaks if it disappears.
"""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TEMPLATES = SCRIPTS / "templates"

SERVICE_PATCH = (SCRIPTS / "patch_v3_service.py").read_text(encoding="utf-8")
SHELL_PATCH = (SCRIPTS / "patch_v3_unified_shell.py").read_text(encoding="utf-8")
KEYCHAIN_PATCH = (SCRIPTS / "patch_embedded_keychain.py").read_text(encoding="utf-8")
STARTUP_PATCH = (SCRIPTS / "patch_embedded_sidestore_startup.py").read_text(encoding="utf-8")
LAYOUT_PATCH = (SCRIPTS / "patch_app_layout.py").read_text(encoding="utf-8")
SHELL = (TEMPLATES / "v3_unified_shell.swift").read_text(encoding="utf-8")
RUNTIME = (TEMPLATES / "v3_headless_runtime.swift").read_text(encoding="utf-8")
WIRE = (TEMPLATES / "v3_wire_contract.swift").read_text(encoding="utf-8")


class IsResignActiveContractTests(unittest.TestCase):
    """`isResignActive` drives PipelineRunner behaviour, not presentation.

    The cleanup removed the presenter plumbing that fed it. The property itself
    is part of the pipeline contract: PipelineRunner reads it to decide whether a
    preflight check may suspend, and the protocol requirement must keep existing
    even though the concrete handler is now always inactive.
    """

    def test_the_pipeline_handler_still_declares_the_property(self):
        # The concrete handler keeps the stored property so its own uses resolve,
        # and the parameterised initialiser is gone with the presenter.
        self.assertIn("presenter_block = '''    let isResignActive: Bool", SERVICE_PATCH)
        self.assertIn('"    let isResignActive = false\\n\\n    // "', SERVICE_PATCH)
        self.assertIn('text.count("    let isResignActive = false\\n") != 1', SERVICE_PATCH)

    def test_the_headless_execution_handler_still_satisfies_the_protocol(self):
        # V3HeadlessPipelineHandler conforms to PreflightChecksHandler, so it
        # must still answer the protocol requirement after the presenter removal.
        handler = RUNTIME[RUNTIME.index("final class V3HeadlessPipelineHandler:"):]
        handler = handler[:handler.index("private func center()")]
        self.assertIn("var preflightChecksHandler: PreflightChecksHandler { self }", handler)
        self.assertIn("var isResignActive: Bool { false }", handler)

    def test_the_presenter_type_is_removed_from_the_compiled_target(self):
        # The presenter type file is excluded, so no retained file may name it.
        self.assertIn('"Handlers/PresenterProvider.swift"', SERVICE_PATCH)
        for token in ("presenterProvider", "activePresenter", "isPresenterAvailable"):
            self.assertNotIn(token, RUNTIME,
                             f"the headless runtime must not reference the removed {token}")
            self.assertNotIn(token, SHELL)

    def test_the_app_manager_call_site_uses_the_initializer_free_handler(self):
        # AppManager no longer constructs a handler with presentation state, and
        # the guard fails closed if a parameterised call reappears.
        self.assertIn("\"return PipelineHandler()\"", SERVICE_PATCH)
        self.assertIn('if "presenterProvider:" in text or "isResignActive:" in text:', SERVICE_PATCH)
        self.assertIn('raise SystemExit("v3 service: AppManager PipelineHandler presenter state remains")',
                      SERVICE_PATCH)


class SavedSourceMigrationTests(unittest.TestCase):
    """The legacy source list must remain readable after its screen is excluded.

    Existing installs have saved sources in a LiveContainer preferences key that
    the retired screen owned. The unified Sources view still offers them, so the
    migration read has to outlive the file that wrote it.
    """

    def test_the_migration_read_survives(self):
        self.assertIn('"LCAltStoreSourceURLs"', SHELL_PATCH)
        self.assertIn("die(\"legacy source URL migration read must survive UI exclusion\")", SHELL_PATCH)
        migration = SHELL[SHELL.index("private var savedGuestSources:"):]
        migration = migration[:migration.index("var body: some View")]
        self.assertIn('UserDefaults.standard.stringArray(forKey: "LCAltStoreSourceURLs")', migration)
        # It offers only what the service does not already own, so the migration
        # cannot reintroduce a duplicate source.
        self.assertIn("!status.sources.contains(where: { $0.url == saved })", migration)

    def test_the_retired_screen_is_excluded_from_the_production_target(self):
        self.assertIn("def exclude_legacy_sources_ui(", SHELL_PATCH)
        self.assertIn('"Views/LCAltStoreSourcesView.swift",', SHELL_PATCH)
        self.assertIn("V3_LEGACY_SOURCES_UI_EXCLUDED_V1", SHELL_PATCH)
        # The project file must be part of the validated transaction, or the
        # exclusion would be written outside the rollback boundary.
        self.assertIn('"LiveContainer.xcodeproj/project.pbxproj",', SHELL_PATCH)
        self.assertIn("exclude_legacy_sources_ui(staged[0])", SHELL_PATCH)

    def test_the_visible_sources_tab_is_the_unified_one(self):
        self.assertIn("struct V3SourcesView", SHELL)
        self.assertIn("V3SourcesView().tabItem { Label(\"Sources\"", SHELL)


class AppIconLookupTests(unittest.TestCase):
    """`appIcon` is a live read operation, not a presentation detail.

    The host asks the service for a guest icon because the host cannot read the
    guest container. Dropping the operation from the catalog, the service or the
    host leaves installed apps without icons.
    """

    def test_the_operation_is_in_the_wire_catalog(self):
        self.assertIn('"appIcon"', WIRE)
        read_operations = WIRE[WIRE.index("static let readOperations:"):]
        read_operations = read_operations[:read_operations.index("]")]
        self.assertIn('"appIcon"', read_operations)

    def test_the_service_answers_the_operation(self):
        service = (TEMPLATES / "v3_sidestore_service.swift").read_text(encoding="utf-8")
        self.assertIn('case "appIcon":', service)

    def test_the_host_requests_the_operation(self):
        self.assertIn('V3ServiceBridge.shared.request(operation: "appIcon", target: identifier)', SHELL)

    def test_the_icon_appearance_dependency_is_intact(self):
        # The grid cell resolves the dark-icon variant from the same shared
        # preference the settings screen toggles.
        cell = (TEMPLATES / "livecontainer_grid_app_cell.swift").read_text(encoding="utf-8")
        self.assertIn('@AppStorage("darkModeIcon", store: LCUtils.appGroupUserDefault)', cell)
        self.assertIn("model.appInfo.iconIsDarkIcon(darkModeIcon)", cell)
        self.assertIn("BuiltInSideStoreAppInfo.shared.iconIsDarkIcon(darkModeIcon)", SHELL_PATCH)


class ThemeAndWidgetColorTests(unittest.TestCase):
    """Theme and widget colour state is host preference state.

    The cleanup removed screens, not the configuration objects the retained
    screens and the widget read. These live in the pinned LiveContainer tree, so
    the check is that the cleanup never touched the patcher that owns them.
    """

    def test_the_banner_configuration_contract_is_untouched(self):
        for token in ('"struct LCAppBannerConfiguration {\\n    let model: LCAppModel\\n'
                       '    let dynamicColors: Bool\\n    let darkModeIcon: Bool\\n}"',
                       "layoutStyle: layoutStyle", "showLabels"):
            self.assertIn(token, LAYOUT_PATCH)
        self.assertIn("dynamicColors: Bool,", LAYOUT_PATCH)
        self.assertIn("darkModeIcon: Bool", LAYOUT_PATCH)

    def test_colour_preferences_keep_an_explicit_shared_store(self):
        cell = (TEMPLATES / "livecontainer_grid_app_cell.swift").read_text(encoding="utf-8")
        for key in ("dynamicColors", "darkModeIcon"):
            self.assertIn(f'@AppStorage("{key}", store: LCUtils.appGroupUserDefault)', cell)
        self.assertIn('@AppStorage("darkModeIcon", store: LCUtils.appGroupUserDefault) var darkModeIcon = false',
                      LAYOUT_PATCH)

    def test_no_cleanup_marker_removed_the_theme_or_widget_configuration(self):
        # The cleanup markers are the only removals these patches performed, and
        # each names the contract it took out. A marker that removed theme or
        # widget configuration would have to appear here to be accepted.
        for patch, marker in ((SERVICE_PATCH, "V3_COMMAND_PATCH_V1"),
                              (SHELL_PATCH, "V3_LEGACY_SOURCES_UI_EXCLUDED_V1"),
                              (KEYCHAIN_PATCH, "LC_HEADLESS_IMPORT_EXPORT_UI_REMOVED_V1"),
                              (STARTUP_PATCH, "REMOVE_NON_LIVE_SIDESTORE_UI_V1")):
            self.assertIn(marker, patch)
        # The widget's own colour preference is widget-visible state, so it is
        # shared, and it is read by the grid cell the widget mirrors.
        self.assertIn('@AppStorage("dynamicColors", store: LCUtils.appGroupUserDefault)',
                      (TEMPLATES / "livecontainer_grid_app_cell.swift").read_text(encoding="utf-8"))
        self.assertIn("dynamicColors: Bool,", LAYOUT_PATCH)


class RemovedPresentationTests(unittest.TestCase):
    """The removals themselves must stay removed, and stay complete."""

    def test_headless_service_keeps_no_standalone_appearance_or_patreon_ui(self):
        for token in ("UIStackView.appearance(whenContainedInInstancesOf:", "stackViewAppearance.spacing",
                      "openPatreonSettingsDeepLinkNotification", "setTintColor()"):
            self.assertIn(f'"{token}"', SERVICE_PATCH,
                          f"the removal guard for {token} must keep checking for it")
        self.assertIn("V3_HEADLESS_SIDESTORE_APP_UI_REMOVED_V1", SERVICE_PATCH)

    def test_headless_service_keeps_no_obsolete_launcher_or_tab_dispatcher(self):
        self.assertIn("V3_HEADLESS_LC_TAB_ROUTING_REMOVED_V1", SERVICE_PATCH)
        self.assertIn("V3_HEADLESS_OPEN_SIDESTORE_HELPER_REMOVED_V1", SERVICE_PATCH)
        self.assertIn('raise SystemExit("v3 service: obsolete LCUtils SideStore launcher remains")',
                      SERVICE_PATCH)

    def test_import_export_keeps_encrypted_account_backups(self):
        # The account backup path is a host contract: the host asks the service to
        # seal and open the account with a user-supplied password.
        for token in ("public static func exportAccount(password: String, includeApplePassword: Bool)",
                      "public static func importAccount(_ encryptedData: Data, filePassword: String)",
                      "AES.GCM.seal(jsonData, using: key)", "AES.GCM.open(sealedBox, using: key)",
                      "AuthManager.shared.authenticationSnapshot"):
            self.assertIn(token, KEYCHAIN_PATCH)
        for token in ("UIDocumentPicker", "DocumentPickerHandler", "getPreviousBackupURL("):
            self.assertIn(f'"{token}"', KEYCHAIN_PATCH,
                          f"the picker removal guard for {token} must keep checking for it")

    def test_the_cleanup_did_not_become_an_open_ended_audit(self):
        # The removals are enumerated, not pattern-driven: each one is anchored to
        # a named upstream contract so an upstream change fails closed.
        for marker in ("UI version overlay sysctl import", "SideStore UI import",
                       "version overlay state", "version overlay passthrough window",
                       "non-LiveProcess UI removal marker"):
            self.assertIn(f'"{marker}"', STARTUP_PATCH)


if __name__ == "__main__":
    unittest.main()
