#!/usr/bin/env python3
"""Pinned, transactional and hash-verified v3 command integration."""
from pathlib import Path
import hashlib
import json
import plistlib
import re
import subprocess
import sys

TEMPLATES = Path(__file__).with_name("templates")
PINS = ("12377cf3b91d51739a33f14a302e5f522b238593", "ff25922e5c13ccfafd83bda5092910d848ebd409")
MARKER = "V3_COMMAND_PATCH_V1"
PATCH_VERSION = 21
HEADLESS_SIDESTORE_VIEW_FILES = (
    "Views/Components/AppInfoView.swift",
    "Views/Components/BundleResourceBrowserView.swift",
    "Views/Components/CodeResourcesViewer.swift",
    "Views/Components/InfoPlistContainerView.swift",
    "Views/Components/MachOResourceViewer.swift",
    "Views/Components/UIKit/CollapsingMarkdownView.swift",
    "Views/MyApps/DeleteAppAlertViewController.swift",
    "Views/Settings/Advanced/Anisette/AnisetteDataView.swift",
    "Views/Settings/Advanced/BackupRestore/BackupAndRestoreView.swift",
    "Views/Settings/Advanced/Certificates/ActiveCertSectionView.swift",
    "Views/Settings/Advanced/Certificates/CertificateDetailView.swift",
    "Views/Settings/Advanced/Certificates/CertificateExporter.swift",
    "Views/Settings/Advanced/Certificates/CertificateRowView.swift",
    "Views/Settings/Advanced/Certificates/CertificateTypes.swift",
    "Views/Settings/Advanced/Certificates/CertificatesListView.swift",
    "Views/Settings/Advanced/Certificates/CertificatesView.swift",
    "Views/Settings/Advanced/Certificates/CertificatesViewModel.swift",
    "Views/Settings/Advanced/Certificates/PrivateKeyTextEditor.swift",
    "Views/Settings/Advanced/Certificates/PrivateKeyTextInputView.swift",
    "Views/Settings/Advanced/Certificates/RevokeAlertViewController.swift",
    "Views/Settings/Advanced/Certificates/SetCertificateAlertViewController.swift",
    "Views/Settings/Advanced/Certificates/SignableCertificatesListViewController.swift",
    "Views/Settings/Advanced/Connection/ConnectionConfigView.swift",
    "Views/Settings/Advanced/DeveloperServices/AppGroups/AppGroupsListView.swift",
    "Views/Settings/Advanced/DeveloperServices/AppIDs/AppIDDetailView.swift",
    "Views/Settings/Advanced/DeveloperServices/AppIDs/AppIDsListView.swift",
    "Views/Settings/Advanced/DeveloperServices/Certificates/CertificatePortalDetailView.swift",
    "Views/Settings/Advanced/DeveloperServices/Certificates/CertificatesPortalListView.swift",
    "Views/Settings/Advanced/DeveloperServices/DeveloperServicesView.swift",
    "Views/Settings/Advanced/DeveloperServices/DeveloperServicesViewModel.swift",
    "Views/Settings/Advanced/DeveloperServices/Devices/DevicesListView.swift",
    "Views/Settings/Advanced/DeveloperServices/Profiles/CreateManualProfileView.swift",
    "Views/Settings/Advanced/DeveloperServices/Profiles/ProfilePortalDetailView.swift",
    "Views/Settings/Advanced/DeveloperServices/Profiles/ProfilesListView.swift",
    "Views/Settings/Advanced/JIT/SideJITServerConfigView.swift",
    "Views/Settings/Advanced/NetworkDiscovery/BonjourDiscoveryView.swift",
    "Views/Settings/Advanced/NetworkDiscovery/BonjourDiscoveryViewModel.swift",
    "Views/Settings/Advanced/SideSign/SideSignConfigurationView.swift",
    "Views/Settings/Advanced/UserCustomizations/ThemePickerView.swift",
    "Views/Settings/Advanced/UserCustomizations/UserCustomizationsView.swift",
    "Views/Settings/Advanced/WirelessPair/WirelessPairTargetDialog.swift",
    "Views/Settings/Advanced/WirelessPair/WirelessPairView.swift",
    "Views/Settings/Advanced/WirelessPair/WirelessPairViewModel.swift",
    "Views/Settings/Auth/ExportAccountAlertViewController.swift",
    "Views/Settings/Auth/ImportAccountAlertController.swift",
    "Views/Settings/Auth/ResetAdiAlertViewController.swift",
    "Views/Settings/Auth/RevokeCertificatesAlertViewController.swift",
    "Views/Settings/Auth/SignOutAlertViewController.swift",
    "Views/Settings/Diagnostics/DeveloperOptionsView.swift",
    "Views/Settings/Diagnostics/ExperimentalFeaturesView.swift",
    "Views/Settings/Diagnostics/OperationsLoggingControlView.swift",
    "Views/Settings/TechyThings/ErrorLog/ConsoleLogView.swift",
    "Views/Settings/TechyThings/HealthCheck/HealthCheckView.swift",
    "Views/Settings/TechyThings/HealthCheck/HealthCheckViewModel.swift",
    "Views/Settings/TechyThings/StorageExplorer/DirectoryExplorerView.swift",
    "Views/Settings/TechyThings/StorageExplorer/StorageExplorerView.swift",
    "Views/Settings/TechyThings/StorageExplorer/StorageExplorerViewModel.swift",
    "Views/SplashView.swift",
)


def remove_pbx_object(text, object_marker):
    if text.count(object_marker) != 1:
        raise SystemExit(f"v3 service: expected exactly one project object {object_marker!r}")
    marker_at = text.index(object_marker)
    line_start = text.rfind("\n", 0, marker_at) + 1
    brace_at = text.index("{", marker_at)
    depth = 0
    end = None
    for index in range(brace_at, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                if end < len(text) and text[end] == ";":
                    end += 1
                if end < len(text) and text[end] == "\r":
                    end += 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                break
    if end is None:
        raise SystemExit(f"v3 service: unbalanced project object {object_marker!r}")
    return text[:line_start] + text[end:]


# AltWidgetExtension remains a production dependency: the combined packager
# moves that app product into LiveContainer as LiveWidgetExtension.
def headless_project(text):
    side_exception = '''A8EEC8CB2F4B146B00F2436D /* PBXFileSystemSynchronizedBuildFileExceptionSet */ = {
			isa = PBXFileSystemSynchronizedBuildFileExceptionSet;
			membershipExceptions = (
				Info.plist,
				Resources/ReleaseEntitlements.plist,
			);
			platformFiltersByRelativePath = {'''
    headless_exception = '''A8EEC8CB2F4B146B00F2436D /* PBXFileSystemSynchronizedBuildFileExceptionSet */ = {
			isa = PBXFileSystemSynchronizedBuildFileExceptionSet;
			membershipExceptions = (
				Info.plist,
				Resources/ReleaseEntitlements.plist,
				"Components/BackgroundTaskManager.swift",
				"Browse/FeaturedViewController.swift",
				"Browse/BrowseViewController.swift",
				"Browse/FeaturedComponents.swift",
				"Browse/ScreenshotCollectionViewCell.swift",
				"News/NewsViewController.swift",
				"TabBarController.swift",
				"Components/ForwardingNavigationController.swift",
				"Components/HeaderContentViewController.swift",
				"Components/NavigationBar.swift",
				"App Detail/AppContentViewController.swift",
				"App Detail/AppContentViewControllerCells.swift",
				"App Detail/AppDetailCollectionViewController.swift",
				"App Detail/AppPermissionsCard.swift",
				"App Detail/AppViewController.swift",
				"App Detail/Screenshots/AppScreenshotsViewController.swift",
				"App Detail/Screenshots/PreviewAppScreenshotsViewController.swift",
				"App Detail/Screenshots/AppScreenshotCollectionViewCell.swift",
				"Components/AppCardCollectionViewCell.swift",
				"App IDs/AppIDsViewController.swift",
				"News/NewsCollectionViewCell.swift",
				"LaunchViewController.swift",
				"Resources/Silence.m4a",
				"Authentication/Authentication.storyboard",
				"Authentication/AuthenticationViewController.swift",
				"Authentication/InstructionsViewController.swift",
				"Authentication/ResignAltStoreViewController.swift",
				"Authentication/SelectTeamViewController.swift",
				"Authentication/tvOS/Authentication.storyboard",
				"Components/AppBannerView.xib",
				"Components/tvOS/AppBannerView.xib",
				"My Apps/InstalledAppsCollectionHeaderView.xib",
				"My Apps/UpdateCollectionViewCell.xib",
				"My Apps/tvOS/InstalledAppsCollectionHeaderView.xib",
				"My Apps/tvOS/UpdateCollectionViewCell.xib",
				"My Apps/MyAppsComponents.swift",
				"My Apps/InstalledAppsCollectionHeaderView.swift",
				"My Apps/UpdateCollectionViewCell.swift",
				"My Apps/MyAppsViewController.swift",
				"News/NewsCollectionViewCell.xib",
				"News/tvOS/NewsCollectionViewCell.xib",
				"Core/Intents/ViewAppIntentHandler.swift",
				"Intents/Legacy/IntentHandler.swift",
				"Settings/AboutPatreonHeaderView.xib",
				"Settings/tvOS/AboutPatreonHeaderView.xib",
				"Settings/AltAppIconsViewController.swift",
				"Settings/SettingsViewController.swift",
				"Settings/PatreonViewController.swift",
				"Settings/LicensesViewController.swift",
				"Settings/RefreshAttemptsViewController.swift",
				"Settings/Error Log/ErrorDetailsViewController.swift",
				"Settings/Error Log/ErrorLogTableViewCell.swift",
				"Settings/Error Log/ErrorLogViewController.swift",
				"Settings/Settings.storyboard",
				"Settings/SettingsHeaderFooterView.xib",
				"Settings/tvOS/Settings.storyboard",
				"Settings/tvOS/SettingsHeaderFooterView.xib",
				"Resources/AltIcons.plist",
				"Resources/Icons.xcassets/Modern/BlueIcon.appiconset",
				"Resources/Icons.xcassets/Modern/DarkIcon.appiconset",
				"Resources/Icons.xcassets/Modern/HoneydewIcon.appiconset",
				"Resources/Icons.xcassets/Modern/PrideIcon.appiconset",
				"Resources/Icons.xcassets/Modern/SandyIcon.appiconset",
				"Resources/Icons.xcassets/Modern/SkyIcon.appiconset",
				"Resources/Icons.xcassets/Modern/SnowIcon.appiconset",
				"Resources/Icons.xcassets/Modern/StarburstIcon.appiconset",
				"Resources/Icons.xcassets/Modern/StormIcon.appiconset",
				"Resources/Icons.xcassets/Modern/VistaIcon.appiconset",
				"Resources/Icons.xcassets/Modern/WinterIcon.appiconset",
				"Sources/Components/SourceHeaderView.xib",
				"Sources/Components/tvOS/SourceHeaderView.xib",
				"Sources/Components/SourceComponents.swift",
				"Sources/Components/SourceHeaderView.swift",
				"Sources/Components/AddSourceTextFieldCell.swift",
				"Sources/AddSourceViewController.swift",
				"Sources/SourcesViewController.swift",
				"Sources/SourceDetailViewController.swift",
				"Sources/SourceDetailContentViewController.swift",
				"Extensions/INInteraction+AltStore.swift",
				"Sources/Sources.storyboard",
				"Sources/tvOS/Sources.storyboard",
				"iOS/LaunchScreen.storyboard",
				"iOS/Main.storyboard",
			"tvOS/Main.storyboard",
			);
			platformFiltersByRelativePath = {'''
    if text.count(side_exception) != 1:
        raise SystemExit("v3 service: SideStore resource-exclusion anchor changed")
    text = text.replace(side_exception, headless_exception, 1)
    side_store_source_exception = '''A8EECF492F4B195000F2436D /* PBXFileSystemSynchronizedBuildFileExceptionSet */ = {
			isa = PBXFileSystemSynchronizedBuildFileExceptionSet;
			membershipExceptions = (
				Tests/UITests/UITests.swift,
				Tests/UITests/UITestsLaunchTests.swift,
				Tests/UnitTests/datastructures/DataStructuresTests.swift,
				Tests/UnitTests/datastructures/LinkedHashMapTests.swift,
				Tests/UnitTests/datastructures/TreeMapTests.swift,
				"Utils/misc/xcmapping-diff-reporter/xcmapping-diff.py",
			);
			target = BFD247692284B9A500981D42 /* SideStore */;
		};'''
    headless_side_store_source_exception = '''A8EECF492F4B195000F2436D /* PBXFileSystemSynchronizedBuildFileExceptionSet */ = {
			isa = PBXFileSystemSynchronizedBuildFileExceptionSet;
			membershipExceptions = (
				Tests/UITests/UITests.swift,
				Tests/UITests/UITestsLaunchTests.swift,
				Tests/UnitTests/datastructures/DataStructuresTests.swift,
				Tests/UnitTests/datastructures/LinkedHashMapTests.swift,
				Tests/UnitTests/datastructures/TreeMapTests.swift,
				"Utils/misc/xcmapping-diff-reporter/xcmapping-diff.py",
				"Handlers/SignInFlowHandler.swift",
			);
			target = BFD247692284B9A500981D42 /* SideStore */;
		};'''
    view_exclusions = "".join(f'\t\t\t\t"{path}",\n' for path in HEADLESS_SIDESTORE_VIEW_FILES)
    headless_side_store_source_exception = replace(
        headless_side_store_source_exception,
        '\t\t\t\t"Handlers/SignInFlowHandler.swift",\n',
        '\t\t\t\t"Handlers/SignInFlowHandler.swift",\n' + view_exclusions)
    text = replace(text, side_store_source_exception, headless_side_store_source_exception)
    # Starscream is linked by the pinned project but has no source references
    # in that checkout. Remove its product and package lock so it is not fetched
    # or linked into the backend build.
    for marker in (
        'A8C37035302DA84D0010213A /* Starscream in Frameworks */ = {',
        'A8C37033302DA84D0010213A /* XCRemoteSwiftPackageReference "Starscream" */ = {',
        'A8C37034302DA84D0010213A /* Starscream */ = {',
    ):
        text = remove_pbx_object(text, marker)
    references = (
        r"(?m)^\s*A8C37035302DA84D0010213A /\* Starscream in Frameworks \*/,\r?\n",
        r"(?m)^\s*A8C37034302DA84D0010213A /\* Starscream \*/,\r?\n",
        r"(?m)^\s*A8C37033302DA84D0010213A /\* XCRemoteSwiftPackageReference \"Starscream\" \*/,\r?\n",
    )
    for pattern in references:
        text, count = re.subn(pattern, "", text)
        if count != 1:
            raise SystemExit(f"v3 service: expected one Starscream project reference, found {count}")
    icon_setting = "ASSETCATALOG_COMPILER_INCLUDE_ALL_APPICON_ASSETS = YES;"
    if text.count(icon_setting) != 2:
        raise SystemExit("v3 service: expected Debug and Release alternate-icon settings")
    text = text.replace(icon_setting, "ASSETCATALOG_COMPILER_INCLUDE_ALL_APPICON_ASSETS = NO;")
    return text


def headless_info(text):
    info = plistlib.loads(text.encode("utf-8"))
    info.pop("UIMainStoryboardFile", None)
    info.pop("UILaunchStoryboardName", None)
    info.pop("UIBackgroundModes", None)
    info.pop("INIntentsSupported", None)
    info.pop("NSUserActivityTypes", None)
    for icon_key in ("CFBundleIcons", "CFBundleIcons~ipad"):
        icons = info.get(icon_key)
        if isinstance(icons, dict):
            icons.pop("CFBundleAlternateIcons", None)
    scene_manifest = info.get("UIApplicationSceneManifest")
    if not isinstance(scene_manifest, dict):
        raise SystemExit("v3 service: SideStore scene manifest anchor is missing")
    configurations = scene_manifest.get("UISceneConfigurations")
    if not isinstance(configurations, dict):
        raise SystemExit("v3 service: SideStore scene configurations are missing")
    removed = 0
    for scenes in configurations.values():
        if not isinstance(scenes, list):
            continue
        for scene in scenes:
            if isinstance(scene, dict):
                scene.pop("UILaunchStoryboardName", None)
            if isinstance(scene, dict) and scene.pop("UISceneStoryboardFile", None) is not None:
                removed += 1
    if removed != 1:
        raise SystemExit(f"v3 service: expected one configured scene storyboard, found {removed}")
    return plistlib.dumps(info, fmt=plistlib.FMT_XML, sort_keys=False).decode("utf-8")


def headless_auth_manager(text):
    start_marker = "    @discardableResult\n    func signIn(\n        presentingViewController: UIViewController? = nil,"
    end_marker = "    // Developer Portal Operations"
    if "V3_HEADLESS_AUTH_ENTRYPOINT_V1" in text:
        if "SignInFlowHandler" in text or "presentingViewController: UIViewController? = nil" in text:
            raise SystemExit("v3 service: legacy UIKit sign-in entry point removal is partial")
        return text
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise SystemExit("v3 service: AuthManager sign-in entry point changed")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    old = text[start:end]
    if "SignInFlowHandler" not in old or "SignInOperation" not in old:
        raise SystemExit("v3 service: AuthManager sign-in wrapper no longer matches the UI-only path")
    text = text[:start] + (
        "    // V3_HEADLESS_AUTH_ENTRYPOINT_V1: LiveContainer owns credentials and 2FA UI; "
        "the embedded service still executes SignInOperation through V3HeadlessAuthHandler.\n"
    ) + text[end:]
    text = replace(text, "@preconcurrency import UIKit\n", "")
    if "UIViewController" in text or "SignInFlowHandler" in text:
        raise SystemExit("v3 service: UIKit sign-in presentation remains in AuthManager")
    return text


def headless_app_manager_ui(text):
    marker = "V3_HEADLESS_APP_MANAGER_SIGNIN_REMOVED_V1"
    if marker in text:
        if ("AuthManager.shared.signIn(\n                    presentingViewController:" in text
                or "import Intents\n" in text
                or "ResignAltStoreViewController" in text):
            raise SystemExit("v3 service: legacy AppManager sign-in wrapper removal is partial")
        return text
    start_marker = "    func signIn(presentingViewController: UIViewController?,\n"
    end_marker = "\n    func deactivateApps("
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise SystemExit("v3 service: AppManager UIKit sign-in wrapper changed")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    old = text[start:end]
    if "AuthManager.shared.signIn" not in old:
        raise SystemExit("v3 service: AppManager sign-in wrapper no longer targets AuthManager")
    text = text[:start] + "    // " + marker + ": interactive sign-in is owned by the LiveContainer host.\n" + text[end:]
    text = replace(text,
        "isResignActive: presentingViewController is ResignAltStoreViewController",
        "isResignActive: false")
    if "ResignAltStoreViewController" in text:
        raise SystemExit("v3 service: legacy resign presenter still reaches AppManager")
    return replace(text, "import Intents\n", "")


def headless_app_intents(text, relative):
    marker = "V3_HEADLESS_INSTALL_IPA_INTENT_REMOVED_V1"
    if marker in text:
        if "InstallIPAIntent" in text:
            raise SystemExit(f"v3 service: legacy IPA shortcut remains in {relative}")
        return text
    if relative.endswith("RefreshAllAppsIntent.swift"):
        start_marker = "@available(iOS 17.0, tvOS 17.0, *)\nstruct InstallIPAIntent: AppIntent, ProgressReportingIntent"
        end_marker = "@available(iOS 17.0, tvOS 17.0, *)\nextension RefreshAllAppsIntent"
        if text.count(start_marker) != 1 or text.count(end_marker) != 1:
            raise SystemExit("v3 service: InstallIPAIntent adapter changed")
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        text = text[:start] + "// " + marker + ": IPA installation is host-owned.\n\n" + text[end:]
        if "struct InstallIPAIntent" in text or "AppManager.shared.install(.url" in text:
            raise SystemExit("v3 service: legacy IPA installation shortcut removal is partial")
        error_preserved_marker = "V3_SHORTCUT_REFRESH_ERROR_PRESERVED_V1"
        if error_preserved_marker not in text:
            old_start = "let operation = try? AppManager.shared.backgroundRefresh("
            if text.count(old_start) != 1:
                raise SystemExit("v3 service: refresh operation creation anchor changed")
            text = text.replace(old_start,
                '''// V3_SHORTCUT_REFRESH_ERROR_PRESERVED_V1: preserve native creation failures.
            let operation: BackgroundRefreshAppsOperation
            do {
                operation = try V3ShortcutRefreshFailurePolicy.createOperation(
                correlationID: UUID().uuidString) {
                try AppManager.shared.backgroundRefresh(''', 1)
            old_boundary = re.compile(
                r"            \}\r?\n[ \t]*\r?\n            guard let operation else \{")
            if len(old_boundary.findall(text)) != 1:
                raise SystemExit("v3 service: refresh completion closure boundary changed")
            text = old_boundary.sub("            }\n            }\n\n            guard let operation else {", text, count=1)
            old_guard = re.compile(
                r"            guard let operation else \{\r?\n"
                r"                debugLog\(\"\[RefreshAllAppsIntent\] backgroundRefresh instance is nil\"\)\r?\n"
                r"                return[ \t]*\r?\n"
                r"            \}\r?\n[ \t]*\r?\n")
            if len(old_guard.findall(text)) != 1:
                raise SystemExit("v3 service: refresh nil-operation guard changed")
            text = old_guard.sub("", text, count=1)
            post_creation = re.compile(
                r"            \}\r?\n[ \t]*\r?\n            operation\.ignoresServerNotFoundError")
            if len(post_creation.findall(text)) != 1:
                raise SystemExit("v3 service: refresh operation post-creation anchor changed")
            text = post_creation.sub(
                '''            }
            } catch {
                continuation.resume(throwing: V3ShortcutRefreshFailurePolicy.propagate(error))
                return
            }

            operation.ignoresServerNotFoundError''', text, count=1)
        if ("try? AppManager.shared.backgroundRefresh" in text or
                "V3ShortcutRefreshFailurePolicy.createOperation" not in text or
                "continuation.resume(throwing: V3ShortcutRefreshFailurePolicy.propagate(error))" not in text):
            raise SystemExit("v3 service: refresh creation errors are not preserved")
        return text
    if relative.endswith("AppShortcuts.swift"):
        start_marker = "        AppShortcut(intent: InstallIPAIntent(),"
        end_marker = "                    systemImageName: \"square.and.arrow.down\")"
        if text.count(start_marker) != 1 or text.count(end_marker) != 1:
            raise SystemExit("v3 service: InstallIPAIntent shortcut anchor changed")
        start = text.index(start_marker)
        end = text.index(end_marker, start) + len(end_marker)
        text = text[:start] + "        // " + marker + ": the install flow is owned by LiveContainer.\n" + text[end:]
        if "InstallIPAIntent" in text:
            raise SystemExit("v3 service: legacy IPA shortcut reference remains")
        return text
    raise SystemExit(f"v3 service: unsupported App Intent adapter source {relative}")


def headless_widget_refresh_intent(text):
    marker = "V3_SHORTCUT_WIDGET_FAILURE_PROPAGATION_V1"
    if marker in text:
        if 'debugLog("Failed to refresh apps via widget. \\(error)")' in text:
            raise SystemExit("v3 service: widget intent still logs raw refresh errors")
        return text
    text = replace(text,
        '''        catch
        {
            debugLog("Failed to refresh apps via widget. \\(error)")
        }
''',
        '''        catch
        {
            // V3_SHORTCUT_WIDGET_FAILURE_PROPAGATION_V1: do not report success for a failed refresh.
            debugLog("Widget refresh failed.")
            throw V3ShortcutRefreshFailurePolicy.propagate(error)
        }
''')
    if "throw V3ShortcutRefreshFailurePolicy.propagate(error)" not in text:
        raise SystemExit("v3 service: widget refresh failure is swallowed")
    return text


def headless_app_intent_routing(text):
    marker = "V3_HEADLESS_INTENT_ROUTING_REMOVED_V1"
    if marker in text:
        if "handlerFor intent: INIntent" in text or "import Intents" in text:
            raise SystemExit("v3 service: legacy SideStore App Intent routing removal is partial")
        return text
    text = replace(text, "import Intents\n", "")
    text = replace(text, "    private let intentHandler = IntentHandler()\n", "")
    text = replace(text, "    private let viewAppIntentHandler = ViewAppIntentHandler()\n", "")
    start_marker = "    #if !os(tvOS)\n    func application(_ application: UIApplication, handlerFor intent: INIntent) -> Any?\n"
    end_marker = "    #endif"
    if text.count(start_marker) != 1:
        raise SystemExit("v3 service: AppDelegate App Intent handler anchor changed")
    start = text.index(start_marker)
    end = text.index(end_marker, start) + len(end_marker)
    text = text[:start] + "    // " + marker + ": LiveContainer declares the host-owned intents.\n" + text[end:]
    return text


def headless_background_fetch(text):
    text = replace(text, "import AVFoundation\n", "")
    text = replace(text, "        self.prepareForBackgroundFetch()\n", "")
    preparation_start = text.index("    private func prepareForBackgroundFetch()")
    preparation_end = text.index(
        "    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken",
        preparation_start)
    text = text[:preparation_start] + text[preparation_end:]
    start = text.index("    func application(_ application: UIApplication, didReceiveRemoteNotification")
    end = text.index("\nprivate extension AppDelegate\n{\n    func fetchSources(", start)
    replacement = '''    func application(_ application: UIApplication, didReceiveRemoteNotification userInfo: [AnyHashable : Any], fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void)
    {
        // V3_HEADLESS_SERVICE_V1: refresh scheduling belongs to LiveContainer.
        completionHandler(.noData)
    }

    func application(_ application: UIApplication, performFetchWithCompletionHandler backgroundFetchCompletionHandler: @escaping (UIBackgroundFetchResult) -> Void)
    {
        // The embedded backend is invoked by the host scheduler, not by a
        // second SideStore background-refresh engine.
        backgroundFetchCompletionHandler(.noData)
    }
}
'''
    text = text[:start] + replacement + text[end:]
    extension_start = text.index("\nprivate extension AppDelegate\n{\n    func fetchSources(")
    extension_end = text.index("\nprivate extension AppDelegate {\n    func setupCrashHandler()", extension_start)
    return text[:extension_start] + text[extension_end:]


def replace(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f"v3 service: expected exactly one anchor {old[:100]!r}, found {text.count(old)}")
    return text.replace(old, new, 1)


def patch_sign_in_operation(text):
    marker = "V3_PROVISIONING_RETRY_BYPASSES_CACHED_SIGNIN_V1"
    if marker in text:
        required = (
            "v3ForceProvisioningRetry: Bool",
            "V3ProvisioningResumeExecutionPolicy.mayUseCachedSignIn",
            "V3ProvisioningResumeExecutionPolicy.mayPromptForCredentials",
            "V3ProvisioningResumeUnavailableError()",
            "session.anisetteData = try await self.getAnisetteData()",
            "handleSignInResult(.success(silentResult))",
            "V3TwoFactorRetryPolicy.shouldReuseCredentialsForCodeRetry",
            "if self.isCancelled || error is CancellationError || v3ClassifyAuthError(error) == nil",
            "if self.v3ForceProvisioningRetry {",
            "!(error is V3ProvisioningResumeUnavailableError)",
        )
        if text.count(marker) != 1 or any(value not in text for value in required):
            raise SystemExit("v3 service: provisioning retry SignInOperation patch is partial")
        return text

    text = replace(text,
        "    let skipCertificateProvisioning: Bool\n",
        "    let skipCertificateProvisioning: Bool\n"
        "    // V3_PROVISIONING_RETRY_BYPASSES_CACHED_SIGNIN_V1\n"
        "    let v3ForceProvisioningRetry: Bool\n")
    text = replace(text,
        "        skipCertificateProvisioning: Bool = false\n",
        "        skipCertificateProvisioning: Bool = false,\n"
        "        v3ForceProvisioningRetry: Bool = false\n")
    text = replace(text,
        "        self.skipCertificateProvisioning = skipCertificateProvisioning\n",
        "        self.skipCertificateProvisioning = skipCertificateProvisioning\n"
        "        self.v3ForceProvisioningRetry = v3ForceProvisioningRetry\n")
    text = replace(text,
        "            if var session = AuthManager.shared.session,\n",
        "            if self.v3ForceProvisioningRetry {\n"
        "                guard var session = AuthManager.shared.session,\n"
        "                      let team = AuthManager.shared.team,\n"
        "                      let account = team.account else {\n"
        "                    throw V3ProvisioningResumeUnavailableError()\n"
        "                }\n"
        "                session.anisetteData = try await self.getAnisetteData()\n"
        "                AuthManager.shared.session = session\n"
        "                authResult = try await self.provisioningLoop(account: account, session: session,\n"
        "                    reportProgress: { [weak self] progress in self?.setProgress(progress) })\n"
        "            } else if V3ProvisioningResumeExecutionPolicy.mayUseCachedSignIn(\n"
        "                forceProvisioningRetry: self.v3ForceProvisioningRetry),\n"
        "               var session = AuthManager.shared.session,\n")
    text = replace(text,
        "        let (account, session) = if let silentResult = try await self.silentSignIn() {\n"
        "            silentResult\n"
        "        } else {\n"
        "            try await self.authenticationLoop()\n"
        "        }\n",
        "        let silentResult = try await self.silentSignIn()\n"
        "        let (account, session) = if let silentResult {\n"
        "            silentResult\n"
        "        } else if V3ProvisioningResumeExecutionPolicy.mayPromptForCredentials(\n"
        "            forceProvisioningRetry: self.v3ForceProvisioningRetry) {\n"
        "            try await self.authenticationLoop()\n"
        "        } else {\n"
        "            throw V3ProvisioningResumeUnavailableError()\n"
        "        }\n"
        "        if let silentResult {\n"
        "            await self.signInHandler.handleSignInResult(.success(silentResult))\n"
        "        }\n")
    text = replace(text,
        "        while true {\n"
        "            let (appleID, password) = try await handler.credentials()\n",
        "        var retryCredentials: (String, String)?\n"
        "        while true {\n"
        "            let credentials: (String, String)\n"
        "            if let retry = retryCredentials {\n"
        "                credentials = retry\n"
        "                retryCredentials = nil\n"
        "            } else {\n"
        "                credentials = try await handler.credentials()\n"
        "            }\n"
        "            let (appleID, password) = credentials\n")
    text = replace(text,
        "            } catch {\n"
        "                self.debugLog(\"[SignInOperation] authenticationLoop: Attempt failed with error: \\(error)\")\n",
        "            } catch {\n"
        "                if self.isCancelled || error is CancellationError || v3ClassifyAuthError(error) == nil {\n"
        "                    throw OperationError.cancelled\n"
        "                }\n"
        "                self.debugLog(\"[SignInOperation] authenticationLoop: Attempt failed with error: \\(error)\")\n")
    text = replace(text,
        "                await handler.handleSignInResult(.failure(error))\n",
        "                await handler.handleSignInResult(.failure(error))\n"
        "                if V3TwoFactorRetryPolicy.shouldReuseCredentialsForCodeRetry(\n"
        "                    authFailureKind: v3ClassifyAuthError(error)?.rawValue) {\n"
        "                    retryCredentials = (appleID, password)\n"
        "                }\n")
    text = replace(text,
        "            if !AuthManager.shared.hasStoredPassword &&\n"
        "               !AuthManager.shared.hasStoredXcodeToken\n",
        "            if !AuthManager.shared.hasStoredPassword &&\n"
        "               !AuthManager.shared.hasStoredXcodeToken &&\n"
        "               !(error is V3ProvisioningResumeUnavailableError)\n")
    return text


def patch(live, side):
    roots = (live, side)
    for root, pin in zip(roots, PINS):
        actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        if actual != pin:
            raise SystemExit(f"v3 service: unpinned input {actual}; expected {pin}")
    manifest = live / ".v3-command-patch.json"
    template_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in TEMPLATES.glob("v3_*.swift")}
    group_policy_template = TEMPLATES / "LCAppGroupSelectionPolicy.h"
    template_hashes[group_policy_template.name] = hashlib.sha256(group_policy_template.read_bytes()).hexdigest()
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous.get("patchVersion") != PATCH_VERSION or previous["templates"] != template_hashes:
            raise SystemExit("v3 service: template changed; apply to fresh pinned sources")
        for index, relative, digest in previous["files"]:
            if hashlib.sha256((roots[index] / relative).read_bytes()).hexdigest() != digest:
                raise SystemExit(f"v3 service: previously patched file drifted: {relative}")
        return

    changes = {}
    def edit(root, relative, transform):
        path = root / relative
        changes[path] = transform(changes.get(path, path.read_text(encoding="utf-8")))

    changes[live / "LiveContainer/LCAppGroupSelectionPolicy.h"] = group_policy_template.read_text(encoding="utf-8")

    def lifecycle(s):
        s = replace(s, "struct LCTabView: View {", "struct V3ApplicationRoot<Content: View>: View {\n    let content: Content")
        start = s.index("        TabView(selection: $sharedModel.selectedTab) {")
        end = s.index("        .downloadAlert", start)
        s = s[:start] + "        content\n" + s[end:]
        return replace(s, "        .onOpenURL { url in\n            dispatchURL(url: url)\n        }", "        // URL routing belongs to V3UnifiedTabs.")
    edit(live, "LiveContainerSwiftUI/Views/LCTabView.swift", lifecycle)

    edit(live, "SideStoreSupport/XPCServer.h", lambda s: replace(s, "@protocol RefreshClient\n", '''@protocol RefreshClient
// V3_COMMAND_PATCH_V1: primitive NSData only; the service validates its schema.
- (void)v3Execute:(NSData* _Nonnull)request reply:(void (^ _Nonnull)(NSData* _Nonnull))reply NS_SWIFT_NAME(v3Execute(_:reply:));
'''))
    edit(live, "SideStoreSupport/XPCClient.m", lambda s: replace(s, "@implementation SideStoreClient", '''@protocol V3CommandService
+ (void)execute:(NSData *)request reply:(void (^)(NSData *))reply;
@end

@implementation SideStoreClient
- (void)v3Execute:(NSData *)request reply:(void (^)(NSData *))reply {
    Class<V3CommandService> service = (Class<V3CommandService>)NSClassFromString(@"V3SideStoreService");
    if (service && [(id)service respondsToSelector:@selector(execute:reply:)]) {
        [service execute:request reply:reply];
    } else {
        reply([NSData data]);
    }
}
'''))
    def host(s):
        # Shared startup/refresh responsibilities are installed by the combined startup adapter.
        return s + (TEMPLATES / "v3_wire_contract.swift").read_text(encoding="utf-8") + \
            (TEMPLATES / "v3_behavioral_primitives.swift").read_text(encoding="utf-8") + \
            (TEMPLATES / "v3_service_bridge.swift").read_text(encoding="utf-8")
    edit(live, "SideStoreSupport/SideStore.swift", host)
    # The shared combined-startup adapter owns structured refresh error/result encoding.
    def sidestore_app_delegate(s):
        s = headless_background_fetch(s)
        s = headless_app_intent_routing(s)
        return s + (TEMPLATES / "v3_wire_contract.swift").read_text(encoding="utf-8") + \
            (TEMPLATES / "v3_behavioral_primitives.swift").read_text(encoding="utf-8") + \
            (TEMPLATES / "v3_ipa_staging.swift").read_text(encoding="utf-8") + \
            (TEMPLATES / "v3_sidestore_service.swift").read_text(encoding="utf-8") + \
            (TEMPLATES / "v3_headless_runtime.swift").read_text(encoding="utf-8")
    edit(side, "AltStore/AppDelegate.swift", sidestore_app_delegate)
    edit(side, "SideStore/Core/Auth/AuthManager.swift", headless_auth_manager)
    edit(side, "AltStore/Managing Apps/AppManager.swift", headless_app_manager_ui)
    edit(side, "AltStore/Intents/App Intents/RefreshAllAppsIntent.swift",
         lambda s: headless_app_intents(s, "RefreshAllAppsIntent.swift"))
    edit(side, "AltStore/Intents/App Intents/AppShortcuts.swift",
         lambda s: headless_app_intents(s, "AppShortcuts.swift"))
    edit(side, "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift",
         headless_widget_refresh_intent)
    edit(side, "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift",
         patch_sign_in_operation)
    edit(side, "AltStore/Info.plist", headless_info)
    edit(side, "AltStore.xcodeproj/project.pbxproj", headless_project)
    def remove_starscream_pin(text):
        resolved = json.loads(text)
        pins = resolved.get("pins")
        if not isinstance(pins, list):
            raise SystemExit("v3 service: SideStore package lock has no pin list")
        filtered = [pin for pin in pins if pin.get("identity") != "starscream"]
        if len(pins) - len(filtered) != 1:
            raise SystemExit("v3 service: expected exactly one pinned Starscream package")
        resolved["pins"] = filtered
        return json.dumps(resolved, indent=2) + "\n"
    edit(side, "AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved",
         remove_starscream_pin)
    edit(live, "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift", lambda s: replace(s,
        "        NavigationView {\n            ScrollView {", "        NavigationView {\n            ScrollView {\n                V3InstalledAppsSection(query: searchContext.debouncedQuery)"))
    edit(live, "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift", lambda s: replace(s,
        '            .navigationTitle("lc.appList.myApps".loc)\n            .toolbar {',
        '            .navigationTitle("My Apps")\n            .toolbar {'))
    edit(live, "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift", lambda s: replace(s,
        '''                                Button("lc.appList.installFromIpa".loc, systemImage: "doc.badge.plus", action: {
                                    choosingIPA = true
                                })''', '''                                V3InstallButton()
                                Button("Add to LiveContainer", systemImage: "doc.badge.plus", action: {
                                    choosingIPA = true
                                })'''))
    edit(live, "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift", lambda s: replace(replace(s,
        '''        if appFound == nil && bundleId == "builtinSideStore" {
            appFound = LCAppModel(appInfo: BuiltInSideStoreAppInfo.shared)
        }''', '''        if bundleId == "builtinSideStore" {
            sharedModel.selectedTab = .settings
            return
        }'''), '''            UserDefaults.standard.setValue(url.absoluteString, forKey: "launchAppUrlScheme")
            LCUtils.openSideStore(delegate: self)''', '''            sharedModel.selectedTab = .sources'''))
    edit(live, "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift", lambda s:
         s.replace('ForEach(filteredApps, id: \\.self)', 'ForEach(filteredApps, id: \\.v3Identity)')
          .replace('ForEach(filteredHiddenApps, id: \\.self)', 'ForEach(filteredHiddenApps, id: \\.v3Identity)'))
    edit(live, "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift", lambda s: replace(s,
        "            Form {", "            Form {\n                V3AccountSettings()"))
    edit(live, "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift", lambda s: replace(s,
        "        let storeScheme : String", '''        // Combined certificate import never falls through to a legacy app URL.
        if UserDefaults.sideStoreExist() { return }
        let storeScheme : String'''))
    def multi_lc(s):
        s = replace(s, "struct LCMultiLCManagementView : View, InstallAnotherLCButtonDelegate {",
            "struct LCMultiLCManagementView : View, InstallAnotherLCButtonDelegate {\n    @EnvironmentObject private var v3Status: V3SideStoreStatusStore")
        start = s.index("                let launchURLStr = packedIpaUrl.absoluteString")
        end = s.index("\n                return", start)
        old = s[start:end]
        if "LCUtils.openSideStore(urlStr: launchURLStr)" not in old:
            raise SystemExit("v3 multi-instance install route changed")
        return s[:start] + '                v3Status.stageSharedIPA(packedIpaUrl, title: "Install " + name)' + s[end:]
    edit(live, "LiveContainerSwiftUI/Views/Settings/LCMultiLCManagementView.swift", multi_lc)
    edit(live, "ShareExtension/ShareExtensionViewModel.swift", lambda s: replace(s,
        '        sharedDefaults?.set("builtinSideStore", forKey: "LCLaunchExtensionBundleID")',
        '        // V3_COMMAND_PATCH_V1: always open the unified host for installation.\n        sharedDefaults?.removeObject(forKey: "LCLaunchExtensionBundleID")'))
    edit(live, "LaunchAppExtension/LaunchAppExtension.swift", lambda s: replace(s,
        '            lcSharedDefaults.set("builtinSideStore", forKey: "LCLaunchExtensionBundleID")',
        '            // V3_COMMAND_PATCH_V1: the host routes SideStore links through its service.\n            lcSharedDefaults.removeObject(forKey: "LCLaunchExtensionBundleID")'))
    def guest_jit(s):
        s = replace(s, "import LocalAuthentication", "import LocalAuthentication\nimport SideStoreSupport")
        start = s.index('            onServerMessage?("JIT acquisition will continue in SideStore.")')
        end = s.index("\n        }\n        return false", start)
        if 'await UIApplication.shared.open(launchURL)' not in s[start:end]:
            raise SystemExit("v3 guest JIT route changed")
        return s[:start] + '''            onServerMessage?("Requesting JIT from the SideStore service.")
            do {
                let snapshot = try await V3ServiceBridge.shared.request(operation: "snapshot")
                guard let apps = snapshot["installedApps"] as? [[String: Any]],
                      let host = apps.first(where: { $0["isHost"] as? Bool == true }),
                      let identifier = host["identifier"] as? String else {
                    onServerMessage?("The host is not in SideStore's library. Check Account and Signing.")
                    return false
                }
                _ = try await V3ServiceBridge.shared.request(operation: "jit", target: identifier)
                onServerMessage?("SideStore completed the JIT request.")
            } catch { onServerMessage?(error.localizedDescription) }''' + s[end:]
    edit(live, "LiveContainerSwiftUI/Utilities/LCUtilsExtensions.swift", guest_jit)
    edit(live, "LiveContainer/LCBootstrap.m", lambda s: replace(s,
        '    if([lcUserDefaults boolForKey:@"LCOpenSideStore"] || [selectedApp isEqualToString:@"builtinSideStore"]) {',
        '''    // V3_COMMAND_PATCH_V1: upgrade old startup selection into unified navigation.
    // The dedicated LiveProcess service still boots SideStore normally.
    if (!isLiveProcess && sideStoreExist &&
        ([lcUserDefaults boolForKey:@"LCOpenSideStore"] || [selectedApp isEqualToString:@"builtinSideStore"])) {
        if (launchUrl.length) [lcUserDefaults setObject:launchUrl forKey:@"V3PendingSideStoreURL"];
        [lcUserDefaults setBool:NO forKey:@"LCOpenSideStore"];
        [lcUserDefaults removeObjectForKey:@"selected"];
        [lcUserDefaults removeObjectForKey:@"selectedContainer"];
        selectedApp = nil;
        selectedContainer = nil;
        launchUrl = nil;
    }
    if([lcUserDefaults boolForKey:@"LCOpenSideStore"] || [selectedApp isEqualToString:@"builtinSideStore"]) {'''))
    edit(live, "LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift", lambda s: replace(s,
        '        Form {\n            Section("Status") {', '        Form {\n            V3TargetedRefreshSection()\n            Section("Status") {'))
    edit(live, "LiveContainerSwiftUI/App/AppDelegate.swift", lambda s: replace(replace(s,
        '    private static func record(source: String, result: String, detail: String = "") {',
        '    static func record(source: String, result: String, detail: String = "") {'),
        '        // LC_REFRESH_HOST_V2', '''        NotificationCenter.default.addObserver(forName: Notification.Name("V3TargetedRefreshResult"), object: nil, queue: .main) { notification in
            let result = notification.userInfo?["result"] as? String ?? "unknown"
            let detail = notification.userInfo?["detail"] as? String ?? ""
            Task { @MainActor in LiveContainerAutoRefreshScheduler.record(source: "manual_selected_app", result: result, detail: detail) }
        }
        // LC_REFRESH_HOST_V2'''))
    # A service-owned blank presenter replaces the legacy tab controller. Auth and
    # operation confirmation controllers render remotely within the host sheet.
    edit(side, "AltStore/SceneDelegate.swift", lambda s: replace(s,
        '        guard let _ = (scene as? UIWindowScene) else { return }',
        '''        guard let windowScene = scene as? UIWindowScene else { return }
        // V3_HEADLESS_SERVICE_V2: no window, tab bar, presenter, or visible UI
        // in a service scene. The process executes headless backend commands.
        _ = windowScene'''))

    def delete_uninstall_evidence(s):
        marker = "V3_DELETE_NATIVE_SUCCESS_EVIDENCE_V1"
        if marker in s:
            if s.count(marker) != 1 or "recordNativeUninstallSucceeded" not in s:
                raise SystemExit("v3 service: delete uninstall evidence patch is partial")
            return s
        return replace(s,
            "        try await removeApp(resignedBundleIdentifier)\n",
            "        try await removeApp(resignedBundleIdentifier)\n"
            "        // V3_DELETE_NATIVE_SUCCESS_EVIDENCE_V1: native uninstall succeeded; the service still verifies library absence.\n"
            "        if let handler = self.context.handler as? V3HeadlessPipelineHandler {\n"
            "            await handler.recordNativeUninstallSucceeded()\n"
            "        }\n",
            )
    edit(side, "SideStore/Core/Operations/PipelineOperations/UninstallAppOperation.swift",
         delete_uninstall_evidence)

    # Attach a remote scene to the existing service process, never a second DB owner.
    edit(live, "MultitaskSupport/AppSceneViewController.h", lambda s: replace(s,
        "- (void)setBackgroundNotificationEnabled:(bool)enabled;",
        "- (instancetype)initWithServicePID:(int)pid delegate:(id<AppSceneViewControllerDelegate>)delegate;\n- (void)setBackgroundNotificationEnabled:(bool)enabled;"))
    edit(live, "MultitaskSupport/AppSceneViewController.m", lambda s: replace(s,
        "- (void)setUpAppPresenter {", '''// V3_COMMAND_PATCH_V1: the service owns process lifetime; this owns presentation only.
- (instancetype)initWithServicePID:(int)pid delegate:(id<AppSceneViewControllerDelegate>)delegate {
    self = [super initWithNibName:nil bundle:nil];
    if (self) {
        self.delegate = delegate;
        self.pid = pid;
        self.bundleId = @"builtinSideStore";
        self.dataUUID = @"v3-service";
        self.scaleRatio = 1.0;
        UIKitFixesInit();
        dispatch_async(dispatch_get_main_queue(), ^{ [self setUpAppPresenter]; });
    }
    return self;
}

- (void)setUpAppPresenter {''').replace("[center removeObserver:self.extension", "if (self.extension) [center removeObserver:self.extension"))
    def scene_hooks(s):
        if s.count("UIKitFixesInit();") != 2:
            raise SystemExit("v3 guest/service UIKit initialization anchors changed")
        s = s.replace("UIKitFixesInit();", "V3InitializeUIKitFixes();")
        return replace(s, "@implementation AppSceneViewController", '''// Both guest and service scenes share one swizzle installation for the host process.
static void V3InitializeUIKitFixes(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{ UIKitFixesInit(); });
}

@implementation AppSceneViewController''')
    edit(live, "MultitaskSupport/AppSceneViewController.m", scene_hooks)
    def forward_selected_app_group(s):
        s = replace(s, '#import "LCSharedUtils.h"',
            '#import "LCSharedUtils.h"\n#import "../LiveContainer/LCAppGroupSelectionPolicy.h"')
        return replace(s,
            '        @"lcHomePath": NSHomeDirectory(),\n    }.mutableCopy;\n',
            '        @"lcHomePath": NSHomeDirectory(),\n    }.mutableCopy;\n'
            '    NSString *hostGroupID = LCValidatedAppGroupID([LCSharedUtils appGroupID], ^BOOL(NSString *groupID) {\n'
            '        return [NSFileManager.defaultManager containerURLForSecurityApplicationGroupIdentifier:groupID] != nil;\n'
            '    });\n'
            '    if (hostGroupID) [userInfo setObject:hostGroupID forKey:@"lcAppGroupID"];\n')
    edit(live, "MultitaskSupport/AppSceneViewController.m", forward_selected_app_group)
    def apply_inherited_app_group(s):
        s = replace(s, '#import "../SideStoreSupport/XPCServer.h"',
            '#import "../SideStoreSupport/XPCServer.h"\n#import "../LiveContainer/LCAppGroupSelectionPolicy.h"')
        return replace(s,
            '    NSUserDefaults *lcUserDefaults = NSUserDefaults.standardUserDefaults;\n',
            '    NSUserDefaults *lcUserDefaults = NSUserDefaults.standardUserDefaults;\n'
            '    NSString *inheritedGroupID = LCValidatedAppGroupID(appInfo[@"lcAppGroupID"], ^BOOL(NSString *groupID) {\n'
            '        return [NSFileManager.defaultManager containerURLForSecurityApplicationGroupIdentifier:groupID] != nil;\n'
            '    });\n'
            '    if (inheritedGroupID) {\n'
            '        [lcUserDefaults setObject:inheritedGroupID forKey:@"LCInheritedAppGroupID"];\n'
            '    } else {\n'
            '        [lcUserDefaults removeObjectForKey:@"LCInheritedAppGroupID"];\n'
            '    }\n')
    edit(live, "LiveProcess/main.m", apply_inherited_app_group)
    def honor_inherited_app_group(s):
        s = replace(s, '#import "LCSharedUtils.h"',
            '#import "LCSharedUtils.h"\n#import "LCAppGroupSelectionPolicy.h"')
        return replace(s,
            '    dispatch_once(&once, ^{\n        NSArray* possibleAppGroups = @[',
            '    dispatch_once(&once, ^{\n'
            '        NSString *inherited = LCValidatedAppGroupID(\n'
            '            [NSUserDefaults.standardUserDefaults objectForKey:@"LCInheritedAppGroupID"],\n'
            '            ^BOOL(NSString *groupID) {\n'
            '                return [NSFileManager.defaultManager containerURLForSecurityApplicationGroupIdentifier:groupID] != nil;\n'
            '            });\n'
            '        if (inherited) { appGroupID = inherited; return; }\n'
            '        NSArray* possibleAppGroups = @[')
    edit(live, "LiveContainer/LCSharedUtils.m", honor_inherited_app_group)
    records = []
    for path, content in changes.items():
        encoded = content.encode("utf-8")
        index = 0 if live in path.parents else 1
        records.append([index, str(path.relative_to(roots[index])).replace("\\", "/"), hashlib.sha256(encoded).hexdigest()])
    # Validate all anchors before writing anything.
    for path, content in changes.items():
        path.write_bytes(content.encode("utf-8"))
    manifest.write_text(json.dumps({"patchVersion": PATCH_VERSION, "pins": PINS,
                                    "templates": template_hashes, "files": records}, indent=2) + "\n")


def verify_sign_in_operation(side, pinned_ref):
    relative = "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift"
    source = subprocess.check_output(
        ["git", "-C", str(side), "show", f"{pinned_ref}:{relative}"],
        text=True, encoding="utf-8")
    expected = patch_sign_in_operation(source)
    actual = (side / relative).read_text(encoding="utf-8")
    if actual != expected:
        raise SystemExit("v3 service: SignInOperation differs from the exact generated pinned patch")


def verify_headless_ui_adapters(side, pinned_ref):
    adapters = (
        ("SideStore/Core/Auth/AuthManager.swift", headless_auth_manager),
        ("AltStore/Managing Apps/AppManager.swift", headless_app_manager_ui),
    )
    for relative, transform in adapters:
        source = subprocess.check_output(
            ["git", "-C", str(side), "show", f"{pinned_ref}:{relative}"],
            text=True, encoding="utf-8")
        expected = transform(source)
        actual = (side / relative).read_text(encoding="utf-8")
        if actual != expected:
            raise SystemExit(f"v3 service: {relative} differs from its exact pinned headless UI patch")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify-sign-in-operation":
        verify_sign_in_operation(Path(sys.argv[2]).resolve(), sys.argv[3])
        print("pinned SignInOperation patch verified")
    elif len(sys.argv) == 4 and sys.argv[1] == "--verify-headless-ui-adapters":
        verify_headless_ui_adapters(Path(sys.argv[2]).resolve(), sys.argv[3])
        print("pinned headless auth/UI adapter patches verified")
    else:
        if len(sys.argv) != 3:
            raise SystemExit("usage: patch_v3_service.py LIVE_CONTAINER SIDE_STORE")
        patch(*(Path(arg).resolve() for arg in sys.argv[1:]))
        print("v3 command patch applied and verified")
