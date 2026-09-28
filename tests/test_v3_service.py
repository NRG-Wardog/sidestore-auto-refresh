"""Exercise pinned patch transactions and the actual shipped wire decoder."""
import importlib.util
import json
import os
import plistlib
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch as mock

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


service = module("patch_v3_service")
shell = module("patch_v3_unified_shell")
refresh = module("patch_livecontainer_autorefresh")
results = module("patch_refresh_result_bridge")


class ServicePatchTests(unittest.TestCase):
    def test_missing_auth_poll_session_is_typed_as_session_unavailable(self):
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        poll = source[source.index('case "authPoll":'):source.index('case "authRespond":')]
        self.assertIn("safeCause: .authSessionUnavailable", poll)
        self.assertIn("operation: \"signIn\"", poll)
        self.assertIn("stage: .authentication", poll)
        self.assertIn("retryable: false", poll)

    def test_typed_service_failure_is_correlated_to_reply_request_id(self):
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        receive = source[source.index("private func receive("):source.index("private func invalidRequestReply")]
        self.assertIn("structuredFailure.correlating(to: id).wire", receive)
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        self.assertIn("public func correlating(to id: String)", failure)

    def test_prompt_session_history_has_no_removed_single_identifier_reference(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bsession\.acceptedPromptID\b", runtime))
        self.assertGreaterEqual(runtime.count("session.acceptedPromptIDs"), 4)

    def test_service_admission_uses_typed_busy_cause_policy(self):
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        self.assertIn("V3ServiceMutationBusyCausePolicy.safeCause", source)
        self.assertIn("authenticationActive: authenticationActive", source)
        self.assertIn("safeCause: .operationInProgress", source)
        self.assertIn("guard !V3HeadlessRuntime.shared.auth.hasActiveSession else { throw ServiceError.busy }", source)
        self.assertIn("responseCapacityAvailable: responseCapacityAvailable", source)
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        self.assertIn("case CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue:", primitives)

    def test_successful_service_replies_are_not_reparsed_for_fallback_logs(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        encode = service[service.index("private func encode(_ value:"):]
        encode = encode[:encode.index("private func run(")]
        self.assertIn("V3ResponseEncoder.encodeDetailed", encode)
        self.assertIn("encoded.fallbackToken", encode)
        self.assertNotIn("PropertyListSerialization.propertyList(from: data", encode)
        harness = (ROOT / "tests/fixtures/v3_catalog_response_encoding_harness.swift").read_text(encoding="utf-8")
        self.assertIn("detailedSuccess.fallbackToken == nil", harness)
        self.assertIn("detailedFailure.fallbackToken", harness)

    def test_inflight_request_id_replay_never_claims_operation_was_not_dispatched(self):
        service_source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        replay = service_source[service_source.index("guard tasks[id] == nil else {"):]
        replay = replay[:replay.index("let mutation =")]
        self.assertNotIn('"operationNotDispatched"', replay)
        admission = service_source[service_source.index("guard V3ServiceMutationAdmissionPolicy.admits"):]
        admission = admission[:admission.index("if mutation { mutationID = id }")]
        self.assertIn('response["operationNotDispatched"] = true', admission)

    def fixture(self, directory):
        live_source = os.getenv("LIVE_CONTAINER_TEST_SOURCE")
        side_source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not live_source or not side_source:
            self.skipTest("Set pinned source environment variables")
        roots = (directory / "live", directory / "side")
        files = (
            ["SideStoreSupport/" + name for name in ("XPCServer.h", "XPCServer.m", "XPCClient.m", "SideStore.swift", "SideStoreClient.swift")] +
            ["LiveContainerSwiftUI/" + name for name in ("Views/LCTabView.swift", "Views/AppList/LCAppListView.swift",
             "Views/Settings/LCSettingsView.swift", "Views/Settings/LCMultiLCManagementView.swift",
             "Utilities/Shared.swift", "Utilities/LCUtilsExtensions.swift", "App/LiveContainerSwiftUIApp.swift", "App/AppDelegate.swift")] +
            ["MultitaskSupport/AppSceneViewController." + suffix for suffix in ("h", "m")] +
            ["LiveContainer/LCBootstrap.m", "LiveContainer/LCSharedUtils.m", "LiveProcess/main.m",
             "ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"],
            ["AltStore/AppDelegate.swift", "AltStore/SceneDelegate.swift",
             "AltStore/Managing Apps/AppManager.swift",
             "SideStore/Views/Settings/Advanced/Connection/ConnectionConfig.swift",
             "SideStore/Core/DeviceApi/MinimuxerWrapper.swift",
             "AltStore/Authentication/AuthenticationViewController.swift",
             "AltStore/Authentication/InstructionsViewController.swift",
             "AltStore/Authentication/ResignAltStoreViewController.swift",
             "AltStore/Authentication/SelectTeamViewController.swift",
             "SideStore/Core/Auth/AuthManager.swift", "SideStore/Handlers/SignInFlowHandler.swift",
             "SideStore/Core/Operations/PipelineExecutor.swift",
             "SideStore/Core/Operations/PipelineRunner.swift",
             "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift",
             "SideStore/Core/Operations/PipelineOperations/UninstallAppOperation.swift",
             "AltStore/My Apps/MyAppsViewController.swift",
             "AltStore/Intents/App Intents/RefreshAllAppsIntent.swift",
             "AltStore/Intents/App Intents/AppShortcuts.swift",
             "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift",
             "AltStore/Info.plist", "AltStore.xcodeproj/project.pbxproj",
             "AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"])
        for source, root, pin, names in zip((live_source, side_source), roots, service.PINS, files):
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(subprocess.check_output(["git", "-C", source, "show", pin + ":" + name]))
        refresh.patch_support(roots[0])
        refresh.patch_host_delegate(roots[0])
        refresh.patch_settings(roots[0])
        results.patch(roots[0])
        shell.patch(*roots)
        return roots

    def apply(self, roots):
        def revision(args, **kwargs):
            return service.PINS[0 if str(roots[0]) == args[2] else 1]
        with mock.object(service.subprocess, "check_output", side_effect=revision):
            service.patch(*roots)

    def snapshot(self, directory):
        return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}

    def test_pinned_patch_replay_and_tamper(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            first = self.snapshot(directory)
            project = (roots[1] / "AltStore.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
            exception_anchor = project.index("A8EEC8CB2F4B146B00F2436D")
            member_start = project.index("membershipExceptions = (", exception_anchor)
            member_end = project.index(");", member_start)
            self.assertIn('"My Apps/MyAppsViewController.swift"', project[member_start:member_end])
            self.assertIn('"Components/HeaderContentViewController.swift"', project[member_start:member_end])
            self.assertIn('"Components/NavigationBar.swift"', project[member_start:member_end])
            self.assertIn('"Extensions/INInteraction+AltStore.swift"', project[member_start:member_end])
            manager = (roots[1] / "AltStore/Managing Apps/AppManager.swift").read_text(encoding="utf-8")
            self.assertIn("isResignActive: false,", manager)
            self.assertIn("presenterProvider:", manager)
            self.assertNotIn("isResignActive: false //", manager,
                             "the generated backend call must preserve the argument separator")
            self.apply(roots)
            self.assertEqual(first, self.snapshot(directory))
            path = roots[0] / "SideStoreSupport/XPCClient.m"
            path.write_text(path.read_text() + "\n// unexpected drift\n")
            with self.assertRaises(SystemExit):
                self.apply(roots)
            self.assertNotIn("LCUtils.openSideStore", (roots[0] / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text())
            self.assertIn(".downloadAlert", (roots[0] / "LiveContainerSwiftUI/Views/LCTabView.swift").read_text())
            self.assertNotIn("LCUtils.openSideStore", (roots[0] / "LiveContainerSwiftUI/Views/Settings/LCMultiLCManagementView.swift").read_text(encoding="utf-8"))

    def test_pinned_headless_ui_adapter_verifier_rejects_drift(self):
        side_source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side_source:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the pinned source checkout")
        real_check_output = service.subprocess.check_output

        def read_pinned_source(arguments, **kwargs):
            relative = arguments[-1].split(":", 1)[1]
            return real_check_output(["git", "-C", side_source, "show",
                f"{service.PINS[1]}:{relative}"], text=True, encoding="utf-8")

        with tempfile.TemporaryDirectory() as name:
            roots = self.fixture(Path(name))
            self.apply(roots)
            with mock.object(service.subprocess, "check_output", side_effect=read_pinned_source):
                service.verify_headless_ui_adapters(roots[1], service.PINS[1])
                manager = roots[1] / "AltStore/Managing Apps/AppManager.swift"
                manager.write_text(manager.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    service.verify_headless_ui_adapters(roots[1], service.PINS[1])

    def test_prepared_sidestore_has_no_automatic_storyboard_root_or_unused_starscream(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            side = roots[1]
            info = plistlib.loads((side / "AltStore/Info.plist").read_bytes())
            self.assertNotIn("UIMainStoryboardFile", info)
            self.assertNotIn("UIBackgroundModes", info)
            icons = info.get("CFBundleIcons", {})
            self.assertIsInstance(icons.get("CFBundlePrimaryIcon"), dict)
            self.assertNotIn("CFBundleAlternateIcons", icons)
            scene_configs = info["UIApplicationSceneManifest"]["UISceneConfigurations"]
            for configurations in scene_configs.values():
                for configuration in configurations:
                    self.assertNotIn("UISceneStoryboardFile", configuration)
                    self.assertNotIn("UILaunchStoryboardName", configuration)
            self.assertNotIn("UILaunchStoryboardName", info)
            self.assertNotIn("INIntentsSupported", info)
            self.assertNotIn("NSUserActivityTypes", info)
            project = (side / "AltStore.xcodeproj/project.pbxproj").read_text()
            self.assertNotIn("Starscream", project)
            for widget_edge in ("BF989175250AABF4002ACF50", "BF989176250AABF4002ACF50",
                                "BF989177250AABF4002ACF50", "BF98917B250AABF4002ACF50"):
                self.assertIn(widget_edge, project,
                              "the SideStore widget must remain available for host widget repackaging")
            self.assertIn("BF989166250AABF3002ACF50 /* AltWidgetExtension */", project,
                          "keep the upstream widget target available for standalone SideStore")
            self.assertIn("0ED4AEC92E6DDB2A0039E2C0 /* PBXTargetDependency */", project,
                          "the SideBackup backend dependency remains in the project")
            self.assertNotIn("C0DE00000000000000000001", project)
            exception_anchor = project.index("A8EEC8CB2F4B146B00F2436D")
            member_start = project.index("membershipExceptions = (", exception_anchor)
            member_end = project.index(");", member_start)
            membership = project[member_start:member_end]
            for required_host_intent_adapter in (
                '"Intents/App Intents/AppShortcuts.swift"',
                '"Intents/App Intents/RefreshAllAppsIntent.swift"',
                '"Intents/App Intents/RefreshAllAppsWidgetIntent.swift"',
            ):
                self.assertNotIn(required_host_intent_adapter, membership,
                                 "host App Intents metadata requires these UI-free backend adapters")
            self.assertNotIn('"Intents/Legacy/Intents.intentdefinition"', membership,
                             "the intent schema is staged into the host package then removed from the backend")
            removed_ui_resources = (
                '"iOS/LaunchScreen.storyboard"', '"iOS/Main.storyboard"',
                '"tvOS/Main.storyboard"',
                '"Browse/BrowseViewController.swift"',
                '"Browse/FeaturedViewController.swift"', '"LaunchViewController.swift"',
                '"Browse/FeaturedComponents.swift"',
                '"Browse/ScreenshotCollectionViewCell.swift"',
                '"News/NewsViewController.swift"',
                '"TabBarController.swift"',
                '"Components/ForwardingNavigationController.swift"',
                '"Components/HeaderContentViewController.swift"',
                '"Components/NavigationBar.swift"',
                '"App Detail/AppContentViewController.swift"',
                '"App Detail/AppContentViewControllerCells.swift"',
                '"App Detail/AppDetailCollectionViewController.swift"',
                '"App Detail/AppPermissionsCard.swift"',
                '"App Detail/AppViewController.swift"',
                '"App Detail/Screenshots/AppScreenshotsViewController.swift"',
                '"App Detail/Screenshots/PreviewAppScreenshotsViewController.swift"',
                '"App Detail/Screenshots/AppScreenshotCollectionViewCell.swift"',
                '"Components/AppCardCollectionViewCell.swift"',
                '"App IDs/AppIDsViewController.swift"',
                '"News/NewsCollectionViewCell.swift"',
                '"Authentication/tvOS/Authentication.storyboard"',
                '"Authentication/ResignAltStoreViewController.swift"',
                '"Core/Intents/ViewAppIntentHandler.swift"',
                '"Intents/Legacy/IntentHandler.swift"',
                '"My Apps/MyAppsViewController.swift"',
                '"My Apps/tvOS/InstalledAppsCollectionHeaderView.xib"',
                '"My Apps/tvOS/UpdateCollectionViewCell.xib"',
                '"My Apps/MyAppsComponents.swift"',
                '"My Apps/InstalledAppsCollectionHeaderView.swift"',
                '"My Apps/UpdateCollectionViewCell.swift"',
                '"Authentication/Authentication.storyboard"', '"Settings/Settings.storyboard"',
                '"Sources/Sources.storyboard"', '"Components/AppBannerView.xib"',
                '"Components/tvOS/AppBannerView.xib"',
                '"Sources/AddSourceViewController.swift"', '"Sources/tvOS/Sources.storyboard"',
                '"News/tvOS/NewsCollectionViewCell.xib"',
                '"Settings/tvOS/Settings.storyboard"',
                '"Settings/tvOS/AboutPatreonHeaderView.xib"',
                '"Settings/tvOS/SettingsHeaderFooterView.xib"',
                '"Sources/Components/tvOS/SourceHeaderView.xib"',
                '"Sources/Components/SourceComponents.swift"',
                '"Sources/Components/SourceHeaderView.swift"',
                '"Sources/Components/AddSourceTextFieldCell.swift"',
                '"Sources/SourcesViewController.swift"',
                '"Sources/SourceDetailViewController.swift"',
                '"Sources/SourceDetailContentViewController.swift"',
                '"Extensions/INInteraction+AltStore.swift"',
                '"My Apps/InstalledAppsCollectionHeaderView.xib"', '"My Apps/UpdateCollectionViewCell.xib"',
                '"News/NewsCollectionViewCell.xib"', '"Settings/AboutPatreonHeaderView.xib"',
                '"Settings/SettingsHeaderFooterView.xib"', '"Sources/Components/SourceHeaderView.xib"',
                '"Settings/PatreonViewController.swift"', '"Settings/LicensesViewController.swift"',
                '"Settings/SettingsViewController.swift"',
                '"Settings/RefreshAttemptsViewController.swift"',
                '"Settings/Error Log/ErrorDetailsViewController.swift"',
                '"Settings/Error Log/ErrorLogTableViewCell.swift"',
                '"Settings/Error Log/ErrorLogViewController.swift"',
                '"Components/BackgroundTaskManager.swift"', '"Resources/Silence.m4a"',
                '"Settings/AltAppIconsViewController.swift"', '"Resources/AltIcons.plist"',
                '"Resources/Icons.xcassets/Modern/BlueIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/DarkIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/HoneydewIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/PrideIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/SandyIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/SkyIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/SnowIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/StarburstIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/StormIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/VistaIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/WinterIcon.appiconset"')
            for path in removed_ui_resources:
                self.assertIn(path, membership)
            self.assertNotIn('"Resources/Icons.xcassets/AppIcon.appiconset"', membership,
                             "the primary SideStore app icon remains part of the backend bundle")
            self.assertNotIn('"Resources/Icons.xcassets/Classic"', membership,
                             "the runtime-selected Classic preview images remain available")
            self.assertNotIn('"Resources/Icons.xcassets/Modern"', membership,
                             "the runtime-selected Modern preview images remain available")
            self.assertNotIn("ASSETCATALOG_COMPILER_INCLUDE_ALL_APPICON_ASSETS = YES", project)
            self.assertEqual(project.count("ASSETCATALOG_COMPILER_INCLUDE_ALL_APPICON_ASSETS = NO"), 2)
            side_exception_anchor = project.index("A8EECF492F4B195000F2436D")
            side_member_start = project.index("membershipExceptions = (", side_exception_anchor)
            side_member_end = project.index(");", side_member_start)
            side_membership = project[side_member_start:side_member_end]
            for path in service.HEADLESS_SIDESTORE_VIEW_FILES:
                self.assertIn(f'"{path}"', side_membership)
            for retained_backend_dependency in (
                '"Views/Components/CustomAppIDAlertViewController.swift"',
                '"Views/Settings/Advanced/Connection/ConnectionConfig.swift"',
                '"Views/Settings/Advanced/CacheMgmt/CacheManagementView.swift"',
                '"Views/Settings/Advanced/CacheMgmt/CacheViewModel.swift"',
            ):
                self.assertNotIn(retained_backend_dependency, side_membership)
            connection_config = (side / "SideStore/Views/Settings/Advanced/Connection/ConnectionConfig.swift").read_text(encoding="utf-8")
            self.assertIn("V3_HEADLESS_ACTIVE_STATE_MODEL_V1", connection_config)
            self.assertIn('enum ActiveState: String', connection_config)
            self.assertIn("var tunnelPeerActive: ActiveState", connection_config)
            app_delegate = (side / "AltStore/AppDelegate.swift").read_text()
            self.assertNotIn("import Intents", app_delegate)
            self.assertNotIn("handlerFor intent: INIntent", app_delegate)
            self.assertNotIn("ViewAppIntentHandler()", app_delegate)
            self.assertIn("case .invalidPairingFile(_) = operationError", app_delegate)
            self.assertIn("V3HeadlessPairingFailure.tagIfInvalidPairing", app_delegate)
            auth_manager = (side / "SideStore/Core/Auth/AuthManager.swift").read_text()
            self.assertNotIn("SignInFlowHandler", auth_manager)
            self.assertNotIn("UIViewController", auth_manager)
            self.assertNotIn("import UIKit", auth_manager)
            app_manager = (side / "AltStore/Managing Apps/AppManager.swift").read_text(encoding="utf-8")
            self.assertNotIn("func signIn(presentingViewController:", app_manager)
            self.assertNotIn("import Intents", app_manager)
            self.assertNotIn("ResignAltStoreViewController", app_manager)
            self.assertIn("V3_HEADLESS_APP_MANAGER_SIGNIN_REMOVED_V1", app_manager)
            self.assertIn("V3_TYPED_PAIRING_FAILURE_PROPAGATION_V1", app_manager)
            self.assertIn("V3HeadlessPairingFailure.tagIfInvalidPairing(error)", app_manager)
            service_template = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
            self.assertIn("V3HeadlessPairingFailure.tagIfInvalidPairing(error)", service_template)
            minimuxer_wrapper = (side / "SideStore/Core/DeviceApi/MinimuxerWrapper.swift").read_text(encoding="utf-8")
            self.assertIn("case .invalidPairing(_, let reason)", minimuxer_wrapper)
            self.assertIn("return .invalidPairingFile(reason: reason)", minimuxer_wrapper)
            self.assertNotIn("prepareForBackgroundFetch", app_delegate)
            self.assertNotIn("requestAuthorization(options: [.alert, .badge, .sound])", app_delegate,
                             "the embedded backend must not request a second app's notification permission")
            self.assertNotIn("BackgroundTaskManager.shared", app_delegate)
            self.assertNotIn("AppManager.shared.backgroundRefresh", app_delegate)
            refresh_intent_source = (side / "AltStore/Intents/App Intents/RefreshAllAppsIntent.swift").read_text(encoding="utf-8")
            shortcuts_source = (side / "AltStore/Intents/App Intents/AppShortcuts.swift").read_text(encoding="utf-8")
            self.assertIn("struct RefreshAllAppsIntent", refresh_intent_source)
            self.assertNotIn("struct InstallIPAIntent", refresh_intent_source)
            self.assertNotIn("AppManager.shared.install(.url", refresh_intent_source)
            self.assertIn("V3_SHORTCUT_GUEST_BACKEND_PIPELINE_V1", refresh_intent_source)
            self.assertIn("AppManager.shared.backgroundRefresh", refresh_intent_source)
            self.assertIn("V3RefreshIntentStartPolicy.create", refresh_intent_source)
            self.assertIn("classify: V3HeadlessPairingFailure.tagIfInvalidPairing", refresh_intent_source)
            self.assertNotIn("try? AppManager.shared.backgroundRefresh", refresh_intent_source)
            self.assertIn("throw V3HeadlessPairingFailure.tagIfInvalidPairing(error)", refresh_intent_source)
            self.assertIn("DatabaseManager.shared.start()", refresh_intent_source)
            self.assertIn("ProgressReportingIntent", refresh_intent_source)
            self.assertIn("operationActor", refresh_intent_source)
            self.assertNotIn('Notification.Name("LiveContainerAutoRefreshRunNow")', refresh_intent_source)
            shortcut_request_policy = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
            self.assertIn('"origin": "manualUnknown"', shortcut_request_policy)
            self.assertIn("static var openAppWhenRun = true", refresh_intent_source)
            widget_intent_source = (side / "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift").read_text(encoding="utf-8")
            self.assertIn("ProgressReportingIntent", widget_intent_source)
            self.assertIn("RefreshAllAppsIntent(presentsNotifications: true)", widget_intent_source)
            support = (live / "SideStoreSupport/SideStore.swift").read_text(encoding="utf-8")
            self.assertIn("V3ShortcutRefreshRequest.make()", support)
            self.assertIn('Notification.Name("LiveContainerAutoRefreshRunNow")', support)
            intent_helper = support[support.index("func performIntentRefresh("):support.index("class RefreshHandler")]
            self.assertNotIn("RefreshHandler.shared.startRefresh(identifier: identifier", intent_helper)
            self.assertIn("Refresh All was requested in LiveContainer", support)
            self.assertIn('Notification.Name("LiveContainerAutoRefreshRunNow")', support)
            self.assertIn('"origin": "manualUnknown"', shortcut_request_policy)
            self.assertIn("AppShortcut(intent: RefreshAllAppsIntent()", shortcuts_source)
            self.assertNotIn("InstallIPAIntent", shortcuts_source)
            widget_intent_source = (side / "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift").read_text(encoding="utf-8")
            self.assertIn("V3_SHORTCUT_WIDGET_BACKEND_FORWARD_V1", widget_intent_source)
            self.assertIn("RefreshAllAppsIntent(presentsNotifications: true)", widget_intent_source)
            self.assertIn("ProgressReportingIntent", widget_intent_source)
            pairing_view = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
            pairing_view = pairing_view[pairing_view.index("struct V3PairingView:"):pairing_view.index("@MainActor\nfinal class V3SettingsStore")]
            self.assertIn("failure.recovery", pairing_view)
            self.assertIn("Text(failure.technicalDetails)", pairing_view)
            self.assertIn("Choose Pairing File Again", pairing_view)
            self.assertNotIn("self.fetchSources", app_delegate)
            self.assertIn("completionHandler(.noData)", app_delegate)
            resolved = json.loads((side / "AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved").read_text())
            self.assertNotIn("starscream", [pin["identity"] for pin in resolved["pins"]])
            jit = (roots[0] / "LiveContainerSwiftUI/Utilities/LCUtilsExtensions.swift").read_text(encoding="utf-8")
            self.assertNotIn('sidestore://enable-jit', jit)
            self.assertIn('V3ServiceBridge.shared.request(operation: "jit"', jit)
            scene = (roots[0] / "MultitaskSupport/AppSceneViewController.m").read_text(encoding="utf-8")
            self.assertEqual(scene.count("UIKitFixesInit();"), 1)
            self.assertEqual(scene.count("V3InitializeUIKitFixes();"), 2)
            self.assertIn("dispatch_once(&onceToken, ^{ UIKitFixesInit(); });", scene)
            self.assertIn('forKey:@"lcAppGroupID"', scene)
            live_process = (roots[0] / "LiveProcess/main.m").read_text(encoding="utf-8")
            self.assertIn('forKey:@"LCInheritedAppGroupID"', live_process)
            shared_utils = (roots[0] / "LiveContainer/LCSharedUtils.m").read_text(encoding="utf-8")
            self.assertLess(shared_utils.index('objectForKey:@"LCInheritedAppGroupID"'),
                            shared_utils.index("NSArray* possibleAppGroups"))
            self.assertTrue((roots[0] / "LiveContainer/LCAppGroupSelectionPolicy.h").exists())
            self.assertIn("!isLiveProcess && sideStoreExist", (roots[0] / "LiveContainer/LCBootstrap.m").read_text(encoding="utf-8"))
            for name in ("ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"):
                self.assertNotIn('set("builtinSideStore", forKey: "LCLaunchExtensionBundleID")', (roots[0] / name).read_text(encoding="utf-8"))
            uninstall = (roots[1] / "SideStore/Core/Operations/PipelineOperations/UninstallAppOperation.swift").read_text(encoding="utf-8")
            self.assertIn("V3_DELETE_NATIVE_SUCCESS_EVIDENCE_V1", uninstall)
            self.assertIn("await handler.recordNativeUninstallSucceeded()", uninstall)
            sign_in = (roots[1] / "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift").read_text()
            self.assertIn("V3_PROVISIONING_RETRY_BYPASSES_CACHED_SIGNIN_V1", sign_in)
            self.assertIn("V3ProvisioningResumeExecutionPolicy.mayUseCachedSignIn", sign_in)
            self.assertIn("handleSignInResult(.success(silentResult))", sign_in)
            self.assertIn("V3ProvisioningResumeUnavailableError()", sign_in)
            self.assertIn("if self.v3ForceProvisioningRetry {", sign_in)
            self.assertIn("let account = team.account else", sign_in)
            retry = sign_in[sign_in.index("if self.v3ForceProvisioningRetry {"):sign_in.index("} else if V3ProvisioningResumeExecutionPolicy")]
            self.assertIn("self.provisioningLoop(account: account, session: session", retry)
            self.assertIn("session.anisetteData = try await self.getAnisetteData()", retry)
            self.assertIn("AuthManager.shared.session = session", retry)
            self.assertNotIn("silentSignIn()", retry,
                             "provisioning retry must reuse the authenticated session without reauthentication")
            self.assertIn("retryCredentials: (String, String)?", sign_in)
            self.assertIn("V3TwoFactorRetryPolicy.shouldReuseCredentialsForCodeRetry", sign_in)
            self.assertIn("v3ClassifyAuthError(error) == nil", sign_in)
            self.assertIn("!(error is V3ProvisioningResumeUnavailableError)", sign_in)
            start_authentication = sign_in.index("private func startAuthentication")
            self.assertLess(sign_in.index("handleSignInResult(.success(silentResult))", start_authentication),
                            sign_in.index("self.provisioningLoop(", start_authentication))

    def test_workflow_verifies_exact_pinned_signin_and_headless_adapter_patches(self):
        workflow = (ROOT / ".github/workflows/livecontainer-build.yml").read_text(encoding="utf-8")
        self.assertIn("SideStore/Core/Anisette", workflow)
        self.assertIn("AltStore/Managing Apps/AppManager.swift", workflow)
        self.assertIn("--verify-headless-ui-adapters", workflow)
        self.assertIn("--verify-sign-in-operation", workflow)
        self.assertIn('"$EMBEDDED_SIDESTORE_REF"', workflow)
        patcher = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        self.assertIn('"--verify-headless-ui-adapters"', patcher)
        self.assertIn('git", "-C", str(side), "show", f"{pinned_ref}:{relative}"', patcher)
        self.assertIn("actual != expected", patcher)

    def test_standalone_refresh_run_does_not_reuse_stale_scheduler_identity(self):
        refresh = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        self.assertIn("V3RefreshRunIdentitySelection.select(", refresh)
        self.assertIn("schedulerRunID: schedulerRunID", refresh)
        self.assertIn('forKey: "liveContainerAutoRefreshActiveRunID"', refresh)
        self.assertIn("if !selectedRun.schedulerOwned", refresh)
        self.assertIn('removeObject(forKey: "liveContainerAutoRefreshExpectedRunID")', refresh)
        self.assertIn("V3DirectRefreshRunClaimPolicy.defaultsKey", refresh)
        self.assertIn("V3DirectRefreshRunClaimPolicy.isActive", refresh)
        self.assertIn("V3DirectRefreshPreflightPolicy.isBlocked", refresh)
        self.assertIn('forKey: "liveContainerAutoRefreshHostHandoff"', refresh)
        self.assertIn('forKey: "liveContainerAutoRefreshUncertainMutationRunID"', refresh)
        perform = refresh[refresh.index("private func performRefresh(identifier:"):]
        perform = perform[:perform.index("private func releaseRefreshAdmission")]
        preflight_positions = [index for index in range(len(perform))
            if perform.startswith("V3DirectRefreshPreflightPolicy.isBlocked", index)]
        self.assertEqual(len(preflight_positions), 2)
        self.assertLess(preflight_positions[0], perform.index("try await ensureServiceConnected()"))
        self.assertLess(perform.index("try await ensureServiceConnected()"), preflight_positions[1])
        self.assertLess(preflight_positions[1], perform.index("let token = UUID()"))
        self.assertLess(perform.index("try await ensureServiceConnected()"), perform.index("v3RefreshToken = token"))
        self.assertLess(perform.index("v3RefreshToken = token"), perform.index("sharedDefaults.set([\"run_id\": directClaimID"))
        admission = perform.index('operation: "refreshAdmissionBegin"')
        renewal = perform.index("sharedDefaults.set([\"run_id\": directClaimID", admission)
        self.assertLess(admission, renewal)
        startup = (ROOT / "scripts/patch_combined_service_startup.py").read_text(encoding="utf-8")
        self.assertIn('handler = handler.replace("/*REFRESH_READINESS*/", "")', startup)
        self.assertNotIn('let status = try await V3ServiceBridge.shared.request(operation: "snapshot")', refresh)
        self.assertIn("V3RefreshAdmissionLease.lifetime + 60", perform)
        bridge = (ROOT / "scripts/patch_livecontainer_autorefresh.py").read_text(encoding="utf-8")
        self.assertIn("startScheduledRefresh(", bridge)
        self.assertIn("runID: runID.uuidString", bridge)
        scheduler = (ROOT / "scripts/templates/livecontainer_refresh_scheduler.swift").read_text(encoding="utf-8")
        self.assertIn("LiveContainerRefreshBridge.refreshAllApps(runID: runID)", scheduler)
        self.assertIn("V3DirectRefreshRunClaimPolicy.isActive", scheduler)

    def test_refresh_terminal_intent_recovers_crash_after_active_release(self):
        scheduler = (ROOT / "scripts/templates/livecontainer_refresh_scheduler.swift").read_text(encoding="utf-8")
        verified = scheduler[scheduler.index("private static func markVerified"):scheduler.index("private static func markFailed")]
        failed = scheduler[scheduler.index("private static func markFailed"):scheduler.index("private static func verifyPendingHostHandoff")]
        self.assertLess(verified.index('runRecord["terminal_intent"] = "verified"'), verified.index("endRun("))
        self.assertLess(verified.index("endRun("), verified.index('runRecord["state"] = "completed"'))
        self.assertLess(failed.index('runRecord["terminal_intent"] = "failed"'), failed.index("endRun("))
        self.assertLess(failed.index("endRun("), failed.index('runRecord["state"] = "failed"'))
        self.assertIn("recoverOrphanedRunLedger()", scheduler)
        self.assertIn("V3RefreshTerminalRecoveryPolicy.action", scheduler)
        self.assertIn("private static func terminalManifestSummary", scheduler)
        self.assertIn('"requested_count": (manifest["requested_ids"] as? [String] ?? []).count', scheduler)
        self.assertIn('runRecord.removeValue(forKey: "manifest")', scheduler)
        self.assertIn('["completed", "failed"].contains(currentState)', scheduler)
        host_handoff = scheduler[scheduler.index("private static func verifyPendingHostHandoff"):
                                 scheduler.index("private static func recoverOrphanedRunLedger")]
        self.assertIn('health: "HOST_REFRESH_FAILED"', host_handoff)
        self.assertIn('result: "host_refresh_failed"', host_handoff)
        self.assertIn("markFailed(runID: runID", host_handoff)

    def test_service_and_startup_adapters_compose_on_pinned_sources(self):
        startup = module("patch_combined_service_startup")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name); roots = self.fixture(directory); self.apply(roots)
            with mock.object(startup.subprocess, "check_output", side_effect=lambda args, **kw: service.PINS[0 if args[2] == str(roots[0]) else 1]):
                startup.patch(*roots, "v3")
                first = self.snapshot(directory); startup.patch(*roots, "v3")
                self.assertEqual(first, self.snapshot(directory))
            source = (roots[0] / "SideStoreSupport/SideStore.swift").read_text(encoding="utf-8")
            self.assertNotIn("__v3_connect", source)
            self.assertNotIn("bookmarkForURL(sideStoreHomeURL)!", source)
            client = (roots[0] / "SideStoreSupport/SideStoreClient.swift").read_text(encoding="utf-8")
            self.assertIn("CombinedVerification.sanitized(payload", client)
            self.assertNotIn("reportRefreshResult(error.localizedDescription", client)

    def test_anchor_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            path = roots[1] / "AltStore/SceneDelegate.swift"
            path.write_text(path.read_text().replace("guard let _ = (scene as? UIWindowScene)", "guard let changed = (scene as? UIWindowScene)"))
            before = self.snapshot(directory)
            with self.assertRaises(SystemExit):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory))

    def test_wrong_revision_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with mock.object(service.subprocess, "check_output", return_value="unknown"):
                with self.assertRaises(SystemExit):
                    service.patch(directory, directory)
            self.assertEqual({}, self.snapshot(directory))

    def test_owner_boundary(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        for token in ("CoreData", "NSManagedObject", "appleIDXcodeToken"):
            self.assertNotIn(token, host + bridge)
        self.assertIn("V3JITLessStatusReader", host)
        self.assertIn("livecontainer://jitless-setup", host)
        self.assertNotIn("syncJITLessCertificate", host)
        self.assertNotIn('account: "signingCertificate"', host)
        self.assertNotIn("writeJITLessCertificate", host)
        self.assertNotIn("v3SideStoreStatusSnapshot", host)
        self.assertIn("pending.removeValue", bridge)
        self.assertIn("decoded[\"id\"] as? String == id", bridge)

    def test_headless_service_has_no_presentation(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        for token in ("Self.presenter", "presentingViewController:", "UIHostingController",
                      "UINavigationController(rootViewController", "CertificatesView(",
                      "DeveloperServicesView(", "importPairingFile(presentingVC",
                      "presentConfirmationAlert", "V3RemoteServiceView", "serviceWindow",
                      "makeKeyAndVisible", "AppManager.shared.signIn(presentingViewController",
                      "AuthManager.shared.signIn(presentingViewController"):
            self.assertNotIn(token, service + runtime)
        self.assertNotIn("present(", service + runtime)
        self.assertNotIn("dismiss(", service + runtime)

    def test_source_remove_preserves_busy_and_service_readiness_causes(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        receive = service[service.index("private func receive("):service.index("private func invalidRequestReply")]
        self.assertIn("safeCause: .sourceRemoveBusy", receive)
        self.assertIn("case .notReady = serviceError", receive)
        self.assertIn("case .notReady = headlessError", receive)
        self.assertIn("code: .notReady, id: id, retryable: true", receive)
        self.assertIn("safeCause: .sourceRemoveFailed", receive)

    def test_headless_operation_inventory(self):
        contract = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        for removed in ("panel", "signIn", "install", "refreshApp", "addSource",
                        "removeSource", "importPairing", "update", "activate",
                        "deactivate", "remove", "delete", "backup", "restore",
                        "installURL", "installSharedIPA", "setSetting"):
            self.assertNotIn(f'"{removed}"', contract)
        for op in ("authBegin", "authPoll", "authRespond", "authCancel", "opStart", "opPoll",
                   "opAnswer", "opCancel", "certList", "certSetActive", "certDelete",
                   "certPortalList", "certRevoke", "certCreate", "devTeams", "devDevices",
                   "devAppIDs", "devGroups", "devProfiles", "sourcePreview", "sourceAddConfirmed",
                   "sourceRemoveConfirmed", "pairingImportData", "settingsGet", "settingsSet",
                   "anisetteList", "anisetteReset", "anisetteSync", "sidesignGet", "sidesignSet",
                   "sidesignReset", "sidesignImport", "sidesignExport", "logTail",
                   "healthSnapshot", "accountExport", "accountImport"):
            self.assertIn(f'"{op}"', contract)
            self.assertIn(f'case "{op}"', service)
        self.assertIn('"payload"', contract)

    def test_prompt_kinds_are_closed_set(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        kinds = set(re.findall(r'kind: "([a-zA-Z]+)"', runtime))
        expected = {"credentials", "twoFactor", "team", "accountRepair", "provisioningError",
                    "postAuth", "revocation", "resign", "anisetteOutdated", "bundleIDMismatch",
                    "permissions", "extensions", "unsupportedVersion", "bundleIDOverride",
                    "appGroupMismatch"}
        self.assertEqual(kinds, expected)
        self.assertIn("V3PromptSection", host)
        self.assertIn("V3SignInView", host)


class RefreshAdmissionTemplateTests(unittest.TestCase):
    def test_native_refresh_contention_has_typed_busy_guidance(self):
        refresh = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        perform = refresh[refresh.index("func performRefresh(identifier:"):]
        perform = perform[:perform.index("func releaseRefreshAdmission")]
        self.assertGreaterEqual(perform.count("safeCause: .operationInProgress"), 3)

    def test_operation_prompt_does_not_use_auth_only_revision_state(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        auth = runtime[runtime.index("final class V3HeadlessAuthHandler:"):
                       runtime.index("final class V3HeadlessPipelineHandler:")]
        operation = runtime[runtime.index("final class V3HeadlessPipelineHandler:"):
                            runtime.index("// MARK: - Headless operation sessions")]
        self.assertIn("center.sessions[sessionID]?.revision += 1", auth)
        operation_prompt = operation[operation.index("private func ask(kind:"):]
        operation_prompt = operation_prompt[:operation_prompt.index("func resolveBundleIDMismatch")]
        self.assertNotIn(".revision", operation_prompt)

    def test_refresh_owner_brackets_direct_refresh_and_confirms_release(self):
        refresh = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        begin = refresh.index('operation: "refreshAdmissionBegin"')
        dispatch = refresh.index("client.refreshAllApps(", begin)
        release = refresh.index("await releaseRefreshAdmission(run)", dispatch)
        self.assertLess(refresh.index("v3RefreshAdmissionRunID = run"), begin)
        self.assertLess(begin, dispatch)
        self.assertLess(dispatch, release)
        self.assertIn("v3RefreshAdmissionRunID", bridge)
        self.assertIn("ownsRefreshAdmissionControl", bridge)
        self.assertIn('reply["runID"] as? String == runID', refresh)
        self.assertIn('strictBool(reply["released"]) == true', refresh)
        self.assertIn("self.v3_stopService()", refresh)
        self.assertIn("v3RefreshDispatchedRunID", refresh)
        self.assertIn("refreshAdmission.release(requestID: target)", service)
        self.assertIn('cancellationReply["refreshAdmissionReleased"] = true', service)
        self.assertIn("pendingRefreshAdmissionRequests", service)
        end_case = service[service.index('case "refreshAdmissionEnd":'):]
        self.assertIn("UUID(uuidString: target)", end_case)
        self.assertIn("V3CancellationRecoveryReplyPolicy.mayCancelRetirement", bridge)
        self.assertIn("V3RequestRetirementPolicy", bridge)

    def test_auth_begin_request_expiry_cancels_its_reserved_session(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIn("pendingAuthStartSessions[id] = session", service)
        self.assertIn("pendingAuthStartSessions[id] = nil", service)
        self.assertIn("auth.cancelBeforeBegin(id: session)", service)
        self.assertIn("operations.activeMutationID", service)
        self.assertIn("hasConflictingOperationMutation", service)
        self.assertIn("operations.activeMutationID != nil || refreshAdmission.isActive", service)
        self.assertIn("V3MutationReplyCacheBudget.responseCountLimit(isControlResponse: controlReply)", service)
        self.assertIn("V3MutationReplyCacheBudget.minimumReplyBytesToAdmit(operation: operation)", service)
        self.assertIn("!cacheResponse ||", service)
        self.assertIn("completedCacheBudget.remove(byteCount)", service)
        self.assertIn('["opStart", "authBegin", "authRetryProvisioning"].contains(operation)', service)
        begin = runtime[runtime.index("func begin(deadline: Date, mode: BeginMode = .interactive,"):]
        begin = begin[:begin.index("    func run(id: String)")]
        self.assertIn("let requestExpired = Task.isCancelled", begin)
        self.assertIn("requestCancelled: requestExpired", begin)

    def test_terminal_reply_cache_reserves_capacity_for_user_continuations(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        budget = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        self.assertIn("V3MutationReplyCacheBudget.isControlReply(operation: operation)", service)
        self.assertIn("V3MutationReplyCacheBudget.shouldCacheResponse(operation: operation)", service)
        self.assertIn("if mutation && cacheResponse", service)
        self.assertIn("completedCacheBudget.canReserve(", service)
        self.assertIn("completedCacheBudget.record(encoded.count, controlResponse: controlReply)", service)
        for control in ("refreshAdmissionEnd", "authBegin", "authRetryProvisioning", "opStart"):
            self.assertIn(f'"{control}"', budget)
        self.assertIn('!["authRespond", "opAnswer"].contains(operation)', budget)

    def test_request_deadline_task_is_cancelled_when_operation_settles(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        self.assertIn("private var deadlineTasks: [String: Task<Void, Never>]", service)
        self.assertIn("deadlineTasks.removeValue(forKey: id)?.cancel()", service)
        self.assertIn("deadlineTasks[id] = Task { @MainActor in", service)


class WireExecutionTests(unittest.TestCase):
    def test_shipped_native_callback_settles_once(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text()
        gate = source[source.index("final class V3ServiceCallbackGate:"):
                      source.index("// V3_NATIVE_CALLBACK_GATE_END")]
        callback_start = source.index("    private func callback(")
        callback_open = source.index("{", callback_start)
        depth = 1
        callback_end = callback_open + 1
        while depth:
            depth += (source[callback_end] == "{") - (source[callback_end] == "}")
            callback_end += 1
        callback = source[callback_start:callback_end]
        callback = callback.replace("private func callback", "func callback")
        program = "import Foundation\n" + gate + "\nstruct Adapter {\n" + callback + "}\n" + r'''
enum Failure: Error { case native }
@main struct CallbackTests {
    static func main() async throws {
        let adapter = Adapter()
        // Executes the production callback adapter, not a model of the gate.
        try await adapter.callback { done in
            done(.success(()))
            done(.failure(Failure.native))
            done(.success(()))
        }
        do {
            try await adapter.callback { done in
                done(.failure(Failure.native))
                done(.success(()))
            }
            preconditionFailure("native failure was lost")
        } catch Failure.native {}
        for _ in 0..<100 {
            try await adapter.callback { done in
                DispatchQueue.concurrentPerform(iterations: 16) { _ in done(.success(())) }
            }
        }
        // A cancelled task must keep awaiting the native terminal callback. Releasing
        // the continuation on cancellation would free the service mutation gate early.
        let nativeFinished = DispatchSemaphore(value: 0)
        let task = Task {
            try await adapter.callback { done in
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.03) {
                    nativeFinished.signal()
                    done(.success(()))
                    done(.failure(Failure.native)) // Late callback is ignored.
                }
            }
        }
        task.cancel()
        try await task.value
        precondition(nativeFinished.wait(timeout: .now()) == .success)
        print("V3 native callback exactly-once PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            swift = directory / "main.swift"
            swift.write_text(program)
            executable = directory / "callback-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 native callback exactly-once PASS", result.stdout)

    def test_shipped_bridge_lifecycle(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "tests/fixtures/v3_bridge_harness.swift").read_text() +
                               (ROOT / "scripts/templates/combined_failure.swift").read_text() +
                               (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text() +
                               (ROOT / "scripts/templates/combined_service_connection.swift").read_text() +
                               (ROOT / "scripts/templates/v3_wire_contract.swift").read_text() +
                               (ROOT / "scripts/templates/v3_service_bridge.swift").read_text())
            executable = directory / "bridge-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 lifecycle PASS", result.stdout)

    def test_shipped_decoder_rejects_secrets_stale_and_malformed_requests(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/v3_wire_contract.swift").read_text() + r'''
let now = Date(timeIntervalSince1970: 100000)
let valid: [String: Any] = ["version": 1, "id": UUID().uuidString, "operation": "snapshot",
                          "target": "", "deadline": now.addingTimeInterval(30)]
func encode(_ value: [String: Any]) -> Data {
    try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
}
precondition(V3WireContract.decodeRequest(encode(valid), now: now) != nil)
var booleanVersion = valid; booleanVersion["version"] = true
precondition(V3WireContract.decodeRequest(encode(booleanVersion), now: now) == nil)
var page = valid; page["operation"] = "catalog"; page["cursor"] = 50
precondition(V3WireContract.decodeRequest(encode(page), now: now) != nil)
for cursor in [-1, 1_000_001, true, "50", 1.5] as [Any] {
    page["cursor"] = cursor
    precondition(V3WireContract.decodeRequest(encode(page), now: now) == nil)
}
var nonCatalog = valid; nonCatalog["cursor"] = 0
precondition(V3WireContract.decodeRequest(encode(nonCatalog), now: now) == nil)
for (key, value) in [("password", "secret"), ("token", "secret"), ("certificate", "secret"),
                     ("operation", "arbitrarySelector"), ("id", "bad"), ("target", String(repeating: "a", count: 4097))] {
    var request = valid
    request[key] = value
    precondition(V3WireContract.decodeRequest(encode(request), now: now) == nil)
}
for date in [now.addingTimeInterval(-1), now, now.addingTimeInterval(611)] {
    var request = valid; request["deadline"] = date
    precondition(V3WireContract.decodeRequest(encode(request), now: now) == nil)
}
var setting = valid; setting["operation"] = "settingsSet"; setting["target"] = "isBetaUpdatesEnabled"
setting["payload"] = ["key": "isBetaUpdatesEnabled", "type": "bool", "bool": true]
precondition(V3WireContract.decodeRequest(encode(setting), now: now) != nil)
setting["payload"] = ["key": "isBetaUpdatesEnabled", "type": "bool", "bool": 1]
precondition(V3WireContract.decodeRequest(encode(setting), now: now) != nil)
var legacySetting = valid; legacySetting["operation"] = "setSetting"; legacySetting["target"] = "betaUpdates"
legacySetting["value"] = true
precondition(V3WireContract.decodeRequest(encode(legacySetting), now: now) == nil)
precondition(V3WireContract.decodeRequest(Data(repeating: 0, count: 16385), now: now) == nil)
precondition(V3WireContract.decodeRequest(Data([1, 2, 3]), now: now) == nil)
print("V3 wire contract PASS")
''')
            executable = directory / "wire-tests"
            subprocess.run([compiler, str(program), "-o", str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("PASS", result.stdout)

    def test_headless_wire_contract_accepts_payload_and_session_ops(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/v3_wire_contract.swift").read_text() + r'''
let now = Date(timeIntervalSince1970: 100000)
func encode(_ value: [String: Any]) -> Data {
    try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
}
func base(_ operation: String) -> [String: Any] {
    ["version": 1, "id": UUID().uuidString, "operation": operation,
     "target": UUID().uuidString, "deadline": now.addingTimeInterval(30)]
}
for operation in ["authBegin", "authRetryProvisioning", "authPoll", "authRespond", "opStart", "opPoll", "opAnswer",
                  "certList", "certRevoke", "devTeams", "sourcePreview", "sourceAddConfirmed",
                  "pairingImportData", "settingsGet", "settingsSet", "anisetteList", "ipaActiveTokens",
                  "sidesignGet", "logTail", "healthSnapshot", "accountExport", "accountImport"] {
    var request = base(operation)
    if operation == "authBegin" || operation == "authRetryProvisioning" {
        let session = UUID().uuidString
        request["target"] = session
        request["payload"] = ["session": session, "sessionDeadline": now.addingTimeInterval(600)]
    } else {
        request["payload"] = ["kind": "install", "answer": ["choice": "proceed"]]
    }
    precondition(V3WireContract.decodeRequest(encode(request), now: now) != nil, operation)
    precondition(V3WireContract.readOperations.contains(operation) == ["authPoll", "opPoll", "certList", "devTeams", "sourcePreview", "settingsGet", "anisetteList", "ipaActiveTokens", "sidesignGet", "logTail", "healthSnapshot"].contains(operation), operation)
}
let leasedToken = UUID().uuidString.lowercased()
let tokenReply: [String: Any] = ["version": 1, "id": UUID().uuidString,
    "ok": true, "result": ["tokens": [leasedToken]]]
let encodedReply = try PropertyListSerialization.data(fromPropertyList: tokenReply, format: .binary, options: 0)
let decodedReply = try PropertyListSerialization.propertyList(from: encodedReply, format: nil) as! [String: Any]
let decodedTokens = ((decodedReply["result"] as! [String: Any])["tokens"] as! [String])
precondition(decodedTokens == [leasedToken], "the staged IPA lease list survives a property-list reply round trip")
for removed in ["panel", "signIn", "install", "refreshApp", "addSource", "removeSource", "importPairing", "setSetting", "update", "activate", "deactivate", "remove", "delete", "backup", "restore", "installURL", "installSharedIPA"] {
    precondition(V3WireContract.decodeRequest(encode(base(removed)), now: now) == nil, removed)
}
var badPayload = base("opStart")
badPayload["payload"] = "not-a-dict"
precondition(V3WireContract.decodeRequest(encode(badPayload), now: now) == nil)
var validCancel = base("cancel")
validCancel["payload"] = ["scope": "operation"]
precondition(V3WireContract.decodeRequest(encode(validCancel), now: now) != nil)
var missingCancelScope = base("cancel")
missingCancelScope["payload"] = [:]
precondition(V3WireContract.decodeRequest(encode(missingCancelScope), now: now) == nil)
var invalidCancelScope = base("cancel")
invalidCancelScope["payload"] = ["scope": "auth-or-operation"]
precondition(V3WireContract.decodeRequest(encode(invalidCancelScope), now: now) == nil)
var legacyValue = base("snapshot")
legacyValue["value"] = true
precondition(V3WireContract.decodeRequest(encode(legacyValue), now: now) == nil)
print("V3 headless wire contract PASS")
''')
            executable = directory / "headless-wire-tests"
            compiled = subprocess.run([compiler, str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 headless wire contract PASS", result.stdout)

    def test_shipped_prompt_gate_parks_and_resumes_once(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        source = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        gate = source[source.index("enum V3PromptAnswerDisposition:"):]
        gate = gate[:gate.index("\n@MainActor\nfinal class V3HeadlessRuntime")]
        program = "import Foundation\n" + gate + r'''
@main struct PromptGateTests {
    static func main() async throws {
        let center = V3PromptCenter()
        let first = Task { try await center.park(promptID: "p1") }
        try await Task.sleep(nanoseconds: 20_000_000)
        precondition(center.answer(promptID: "p1", answer: ["choice": "proceed"]) == .accepted)
        precondition(center.answer(promptID: "p1", answer: ["choice": "proceed"]) == .alreadySettled)
        precondition(center.answer(promptID: "missing", answer: [:]) == .unavailable)
        let firstAnswer = try await first.value
        precondition(firstAnswer["choice"] == "proceed")
        let second = Task { try await center.park(promptID: "p2") }
        try await Task.sleep(nanoseconds: 10_000_000)
        second.cancel()
        do {
            _ = try await second.value
            preconditionFailure("cancelled park resumed")
        } catch is CancellationError {}
        let third = Task { try await center.park(promptID: "p3") }
        try await Task.sleep(nanoseconds: 10_000_000)
        third.cancel()
        do {
            _ = try await third.value
            preconditionFailure("task cancel did not resume")
        } catch is CancellationError {}
        print("V3 prompt gate PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            swift = directory / "main.swift"
            swift.write_text(program)
            executable = directory / "prompt-gate-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 prompt gate PASS", result.stdout)


class GsaPreparedTreeTests(unittest.TestCase):
    def test_gsa_connection_close_in_prepared_tree(self):
        side = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the pinned source checkout")
        auth = Path(side) / "Dependencies/SideSign/Sources/DeveloperPortal/Authentication.swift"
        text = auth.read_text(encoding="utf-8")
        hits = [m.start() for m in re.finditer(r'"Connection": "close"', text)]
        self.assertEqual(len(hits), 2)
        enclosing = []
        for position in hits:
            before = text[:position]
            found = [m.group(1) for m in re.finditer(r"func\s+(\w+)\s*\(", before)][-1]
            enclosing.append(found)
        self.assertEqual(enclosing, ["sendAuthenticationRequest", "makeTwoFactorAuthRequest"])
        self.assertEqual(len(re.findall(r"URLRequest\(", text)), 2)

    def test_no_builder_patch_modifies_sidesign_auth(self):
        scripts = (ROOT / "scripts").glob("*.py")
        for script in scripts:
            content = script.read_text(encoding="utf-8")
            if script.name == "patch_sidesign_privacy.py":
                self.assertIn('LOGGING = Path("Sources/Logging.swift")', content)
                self.assertNotIn("Authentication.swift", content)
                continue
            if script.name == "patch_sidesign_2fa_state.py":
                self.assertIn("Authentication.swift", content)
                self.assertIn("DeveloperPortalAPI.swift", content)
                continue
            self.assertNotIn("DeveloperPortal/Authentication", content)
        medic = (ROOT / "scripts/combined_build_evidence.py").read_text(encoding="utf-8")
        self.assertNotIn("Dependencies/SideSign", medic)

    def test_auth_is_single_flight_without_retry_loops(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIn("let previousID = activeID", runtime)
        self.assertIn("mayLaunchCreatedSession", runtime)
        self.assertIn("if let oldTask { await oldTask.value }", runtime)
        auth = runtime.split("final class V3OperationCenter")[0]
        self.assertIn("func cancelAndWait(id: String) async -> Bool", auth)
        self.assertIn("func cancelAndWait(id: String) async -> Bool", runtime)
        auth = runtime.split("final class V3OperationCenter")[0]
        self.assertNotRegex(auth, r"(?m)^\s*while\s")
        for marker in ("[V3_AUTH] BEGIN", "[V3_AUTH] PROMPT", "[V3_AUTH] TERMINAL",
                       "[V3_AUTH] CANCEL", "[V3_OP] PROMPT", "[V3_OP] TERMINAL"):
            self.assertIn(marker, runtime)

    def test_host_starts_auth_only_from_user_flow(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertEqual(host.count('"authBegin"'), 1)

    def test_shipped_failure_preservation(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/combined_failure.swift").read_text() + "\n"
                               + 'let id = UUID().uuidString\nlet known = CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .timedOut, id: id, retryable: true)\nlet kept = CombinedFailure.preserving(known, operation: "connect", stage: .serviceReadiness, id: id)\nprecondition(kept.stage == .serviceReadiness && kept.code == .timedOut)\nprecondition(kept.correlationID == id && kept.retryable == true)\nlet invalid = CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .invalidResponse, id: id)\nlet keptInvalid = CombinedFailure.preserving(invalid, operation: "connect", stage: .command, id: UUID().uuidString)\nprecondition(keptInvalid.stage == .serviceReadiness && keptInvalid.code == .invalidResponse)\nprecondition(keptInvalid.correlationID == id)\nlet plain = NSError(domain: NSCocoaErrorDomain, code: 42)\nlet wrapped = CombinedFailure.preserving(plain, operation: "connect", stage: .serviceReadiness, code: .failed, id: id)\nprecondition(wrapped.stage == .serviceReadiness && wrapped.code == .failed)\nprecondition(wrapped.correlationID == id && wrapped.underlyingCode == 42)\nlet cancelled = CombinedFailure.preserving(CancellationError(), operation: "connect", stage: .serviceReadiness, id: id)\nprecondition(cancelled.code == .failed)\nprint("V3 failure preservation PASS")')
            executable = directory / "preserve-tests"
            compiled = subprocess.run([compiler, str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 failure preservation PASS", result.stdout)
