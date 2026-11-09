"""Production UI boundaries and backend contracts after removing legacy screens."""
import unittest
from tests.owner_source import owner_source
from tests.support.product_sources import read_product_source
from tests.test_refresh_result_bridge import declaration


class ProductUIContracts(unittest.TestCase):
    def test_pipeline_keeps_protocol_without_presenter(self):
        handler = owner_source("SideStore/Handlers/PipelineHandler.swift", "SideStore")
        protocol = owner_source("SideStore/Core/Operations/PipelineExecutionHandler.swift", "SideStore")
        manager = owner_source("AltStore/Managing Apps/AppManager.swift", "SideStore")
        self.assertIn("let isResignActive = false", handler)
        self.assertIn("var isResignActive: Bool { get }", protocol)
        self.assertIn("return PipelineHandler()", manager)
        self.assertNotIn("presenterProvider:", manager)
        self.assertNotIn("isResignActive:", manager)
        runtime = read_product_source("v3_headless_runtime.swift")
        headless = runtime[runtime.index("final class V3HeadlessPipelineHandler:"):]
        self.assertIn("var preflightChecksHandler: PreflightChecksHandler { self }", headless)
        self.assertIn("var isResignActive: Bool { false }", headless)
        project = owner_source("AltStore.xcodeproj/project.pbxproj", "SideStore")
        self.assertIn('"Handlers/PresenterProvider.swift"', project)
        for source in (runtime, read_product_source("v3_unified_shell.swift"), handler):
            for token in ("presenterProvider", "activePresenter", "isPresenterAvailable"):
                self.assertNotIn(token, source)

    def test_saved_sources_survive_screen_exclusion_without_duplicates(self):
        shell = read_product_source("v3_unified_shell.swift")
        migration = declaration(shell, "private var savedGuestSources:")
        self.assertIn('UserDefaults.standard.stringArray(forKey: "LCAltStoreSourceURLs")', migration)
        self.assertIn("!status.sources.contains(where: { $0.url == saved })", migration)
        project = owner_source("LiveContainer.xcodeproj/project.pbxproj")
        self.assertIn('"Views/LCAltStoreSourcesView.swift"', project)
        self.assertIn("V3SourcesView().tabItem { Label(\"Sources\"", shell)

    def test_app_icons_still_cross_service_boundary(self):
        wire = read_product_source("v3_wire_contract.swift")
        reads = wire[wire.index("static let readOperations:"):]
        self.assertIn('"appIcon"', reads[:reads.index("]")])
        self.assertIn('case "appIcon":', read_product_source("v3_sidestore_service.swift"))
        self.assertIn('V3ServiceBridge.shared.request(operation: "appIcon", target: identifier)',
                      read_product_source("v3_unified_shell.swift"))
        cell = read_product_source("livecontainer_grid_app_cell.swift")
        self.assertIn('@AppStorage("darkModeIcon", store: LCUtils.appGroupUserDefault)', cell)
        self.assertIn("model.appInfo.iconIsDarkIcon(darkModeIcon)", cell)
        # SideStore is represented by the unified service catalog, not a
        # separately injected built-in tile with another icon implementation.
        self.assertNotIn("BuiltInSideStoreAppInfo.shared.iconIsDarkIcon", owner_source("LiveContainerSwiftUI/Views/AppList/LCAppListView.swift"))

    def test_shared_theme_preference_still_has_editor_and_consumer(self):
        preference = '@AppStorage("dynamicColors", store: LCUtils.appGroupUserDefault)'
        self.assertIn(preference, read_product_source("livecontainer_grid_app_cell.swift"))
        settings = owner_source("LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift")
        self.assertIn(preference, settings)
        self.assertIn("Toggle(isOn: $dynamicColors)", settings)

    def test_backend_has_no_standalone_appearance_or_legacy_host_launcher(self):
        app = owner_source("AltStore/AppDelegate.swift", "SideStore")
        for token in ("UIStackView.appearance(whenContainedInInstancesOf:", "stackViewAppearance.spacing",
                      "openPatreonSettingsDeepLinkNotification", "setTintColor()"):
            self.assertNotIn(token, app)
        self.assertNotIn("func dispatchURL(", owner_source("LiveContainerSwiftUI/Views/LCTabView.swift"))
        self.assertNotIn("static func openSideStore(", owner_source("LiveContainerSwiftUI/Utilities/LCUtilsExtensions.swift"))

    def test_encrypted_account_backups_remain_without_document_presenters(self):
        source = owner_source("SideStore/Utils/importexport/ImportExport.swift", "SideStore")
        for token in ("public static func exportAccount(password: String, includeApplePassword: Bool)",
                      "public static func importAccount(_ encryptedData: Data, filePassword: String)",
                      "AES.GCM.seal(jsonData, using: key)", "AES.GCM.open(sealedBox, using: key)",
                      "AuthManager.shared.authenticationSnapshot"):
            self.assertIn(token, source)
        for token in ("UIDocumentPicker", "DocumentPickerHandler", "getPreviousBackupURL("):
            self.assertNotIn(token, source)

    def test_standalone_ui_stays_absent_from_hooks(self):
        hooks = owner_source("SideStoreSupport/SideStoreHooks.m")
        for token in ('#import <sys/sysctl.h>', '@import UIKit;', 'SSVersionWindows',
                      'SSSceneObserver', 'PassthroughWindow', 'SSInstallVersionWindow'):
            self.assertNotIn(token, hooks)


if __name__ == "__main__": unittest.main()
