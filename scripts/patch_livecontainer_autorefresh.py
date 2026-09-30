#!/usr/bin/env python3
"""Integrate host-owned refresh without embedding Swift in Python f-strings.

Templates are ordinary Swift files, parsed in preflight and tested as generated
sources. The host uses its installed BG allowlist, not an assumed signer rewrite.
Transport and the upstream combined packaging engine are left unchanged.
"""
from __future__ import annotations

from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

TASK_ID = "com.kdt.livecontainer.sidestore.automatic-refresh"
MARKER = "[LIVE_CONTAINER_REFRESH] REGISTER_PASS"
TEMPLATES = Path(__file__).resolve().parent / "templates"
SHARED_APP_GROUP_FILE = "LiveContainerSwiftUI/Utilities/V3SharedAppGroup.swift"


def template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


HOST_SCHEDULER = template("livecontainer_refresh_policy.swift") + "\n" + template("livecontainer_network_preflight.swift") + "\n" + template("livecontainer_refresh_scheduler.swift")
ALARM_PROVIDER = template("livecontainer_refresh_alarm.swift")
SETTINGS_VIEW = template("livecontainer_refresh_settings.swift")
BRIDGE = r'''
// LC_REFRESH_BRIDGE_V3_BEGIN
/// Dispatch the canonical SideStore guest refresh intent from the host scheduler.
public enum LiveContainerRefreshBridge {
    public static func refreshAllApps(runID: UUID) async throws {
        guard #available(iOS 17.0, *) else {
            throw NSError(domain: "LiveContainerRefresh.UnsupportedOS", code: 17,
                userInfo: [NSLocalizedDescriptionKey: "The embedded automatic refresh bridge requires iOS 17 or later."])
        }
        try Task.checkCancellation()
        try await RefreshHandler.shared.startScheduledRefresh(
            identifier: "RefreshAllIntent",
            mangledName: "9SideStore20RefreshAllAppsIntentV",
            runID: runID.uuidString
        )
        try Task.checkCancellation()
    }
}
// LC_REFRESH_BRIDGE_V3_END
'''


def die(message: str) -> None:
    raise SystemExit(f"patch_livecontainer_autorefresh: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_support(root: Path) -> None:
    path = root / "SideStoreSupport/SideStore.swift"
    text = path.read_text(encoding="utf-8")
    intent_marker = "LC_REFRESH_INTENT_TO_HOST_SCHEDULER_V1"
    if intent_marker not in text:
        old_intent = '''@available(iOS 17.0, *)
func performIntentRefresh(identifier: String, mangledTypeName: String, intentProgress: Progress) async throws {
    intentProgress.totalUnitCount = 100
    if UserDefaults.isSideStore() {
        try await SideStoreIntentCaller.shared.callRefreshIntent(mangledTypeName: mangledTypeName)
    } else {
        RefreshHandler.shared.progress = intentProgress
        try await RefreshHandler.shared.startRefresh(identifier: identifier, mangledName: mangledTypeName)
    }
}'''
        new_intent = '''@available(iOS 17.0, *)
func performIntentRefresh(identifier: String, mangledTypeName: String, intentProgress: Progress) async throws {
    // LC_REFRESH_INTENT_TO_HOST_SCHEDULER_V1: a host shortcut requests work from
    // the host scheduler. Only the scheduler launches the SideStore refresh engine.
    _ = intentProgress
    if UserDefaults.isSideStore() {
        try await SideStoreIntentCaller.shared.callRefreshIntent(mangledTypeName: mangledTypeName)
        return
    }
    try Task.checkCancellation()
    let request = V3ShortcutRefreshRequest.make()
    NotificationCenter.default.post(
        name: Notification.Name("LiveContainerAutoRefreshRunNow"),
        object: nil, userInfo: request.userInfo)
}'''
        text = replace_once(text, old_intent, new_intent, "route host AppIntent through scheduler")
    elif ("V3ShortcutRefreshRequest.make()" not in text or
          'Notification.Name("LiveContainerAutoRefreshRunNow")' not in text or
          "RefreshHandler.shared.startRefresh(identifier: identifier" in text):
        die("host AppIntent is not routed through the shared scheduler")
    if 'dialog: "All apps have been refreshed."' in text:
        text = replace_once(text, 'dialog: "All apps have been refreshed."',
            'dialog: "Refresh All was requested in LiveContainer. Check Refresh History for the run result."',
            "truthful host AppIntent completion copy")
    widget_start = text.index("public struct RefreshAllAppsWidgetIntent")
    main_start = text.index("public struct RefreshAllAppsIntent", widget_start)
    handler_start = text.index("class RefreshHandler:", main_start)
    widget_intent = text[widget_start:main_start]
    main_intent = text[main_start:handler_start]
    requested_dialog = 'return .result(dialog: "Refresh All was requested in LiveContainer. Check Refresh History for the run result.")'
    if "return .result()" in widget_intent:
        widget_intent = replace_once(widget_intent, "return .result()", requested_dialog,
                                     "truthful widget AppIntent request completion copy")
        text = text[:widget_start] + widget_intent + text[main_start:]
    elif requested_dialog not in widget_intent:
        die("widget AppIntent does not distinguish request submission from refresh completion")
    widget_title = '    public static var title: LocalizedStringResource { "Refresh Apps via Widget" }\n'
    main_title = '    public static var title: LocalizedStringResource = "Refresh All Apps"\n'
    if "public static var openAppWhenRun = true" not in widget_intent:
        text = replace_once(text, widget_title,
            widget_title + "    // LC_REFRESH_HOST_INTENT_FOREGROUND_V1: widget requests must execute in the host app.\n"
            "    public static var openAppWhenRun = true\n", "foreground widget intent")
    if "public static var openAppWhenRun = true" not in main_intent:
        text = replace_once(text, main_title,
            main_title + "    public static var openAppWhenRun = true\n", "foreground refresh intent")
    if "LC_REFRESH_BRIDGE_V3_BEGIN" in text:
        if BRIDGE.strip() not in text:
            die("outdated bridge template: reapply to the pinned clean source")
        return
    if "LC_REFRESH_BRIDGE_V2_BEGIN" in text or "public enum LiveContainerRefreshBridge" in text:
        die("legacy bridge already patched: reapply to the pinned clean source")
    text = replace_once(text, "\nclass RefreshHandler: NSObject, RefreshServer {",
                        BRIDGE + "\nclass RefreshHandler: NSObject, RefreshServer {", "refresh bridge insertion")
    # Existing upstream guards must not silently return apparent success.
    replacements = (
        ("guard let listener = startAnonymousListener(self) else {\n                return\n            }",
         "guard let listener = startAnonymousListener(self) else {\n                throw NSError(domain: \"LiveContainerRefresh.XPC\", code: 1, userInfo: [NSLocalizedDescriptionKey: \"Unable to create the embedded SideStore XPC listener.\"])\n            }"),
        ("guard let listener = self.listener else {\n            return\n        }",
         "guard let listener = self.listener else {\n            throw NSError(domain: \"LiveContainerRefresh.XPC\", code: 2, userInfo: [NSLocalizedDescriptionKey: \"Embedded SideStore XPC listener is unavailable.\"])\n        }"),
        ("guard let ext else {\n                return\n            }",
         "guard let ext else {\n                throw NSError(domain: \"LiveContainerRefresh.XPC\", code: 3, userInfo: [NSLocalizedDescriptionKey: \"LiveProcess could not be created.\"])\n            }"),
    )
    for old, new in replacements:
        # Minimal unit fixtures contain no upstream implementation. A real
        # pinned source must contain these guards and is covered by CI fixtures.
        if old in text:
            text = replace_once(text, old, new, "explicit XPC failure")
    old = '''        self.client?.refreshAllApps(withIdentifier: identifier, mangledTypeName: mangledName)
        
        try await withUnsafeThrowingContinuation { c in
            self.c = c
        }'''
    new = '''        guard let client = self.client else {
            throw NSError(domain: "LiveContainerRefresh.XPC", code: 4,
                userInfo: [NSLocalizedDescriptionKey: "Embedded SideStore did not establish its refresh connection."])
        }
        try await withUnsafeThrowingContinuation { c in
            self.c = c
            // Store the continuation BEFORE sending work to the remote process.
            client.refreshAllApps(withIdentifier: identifier, mangledTypeName: mangledName)
        }'''
    if "self.client?.refreshAllApps(" in text:
        text = replace_once(text, old, new, "XPC continuation-before-dispatch")
    path.write_text(text, encoding="utf-8")


def patch_host_delegate(root: Path) -> None:
    path = root / "LiveContainerSwiftUI/App/AppDelegate.swift"
    text = path.read_text(encoding="utf-8")
    if "// LC_REFRESH_HOST_V2" in text:
        if HOST_SCHEDULER not in text:
            die("outdated host template: reapply to the pinned clean source")
        return
    if "enum LiveContainerAutoRefreshScheduler" in text:
        die("legacy scheduler already patched: reapply to the pinned clean source")
    text = replace_once(text, "import Intents\n",
                        "import Intents\nimport Foundation\nimport BackgroundTasks\nimport SideStoreSupport\nimport UserNotifications\n", "host imports")
    startup = '''        application.shortcutItems = nil
        // LC_REFRESH_HOST_V2
        UNUserNotificationCenter.current().delegate = self
        // V3_RUNTIME_SHARED_REFRESH_STORE_V1: the host owns the App Group
        // selection. Publish it before any shared refresh state is touched, so
        // the embedded service, which cannot see LiveContainer's Objective-C
        // helper, resolves the identical group instead of a packaged name.
        V3SharedAppGroup.publishRuntimeGroup(LCSharedUtils.appGroupID())
        LiveContainerAutoRefreshScheduler.register()
        LiveContainerAutoRefreshScheduler.recoverAfterLaunchOrResume()
        LiveContainerAutoRefreshScheduler.schedule()
        NotificationCenter.default.addObserver(forName: Notification.Name("LiveContainerAutoRefreshScheduleChanged"), object: nil, queue: .main) { _ in
            Task { @MainActor in LiveContainerAutoRefreshScheduler.scheduleChanged() }
        }
        NotificationCenter.default.addObserver(forName: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil, queue: .main) { notification in
            guard let request = V3ShortcutRefreshRequest(userInfo: notification.userInfo) else {
                NSLog("[V3_REFRESH] RUN_NOW_REJECTED reason=invalid_request_identity")
                return
            }
            let requestID = request.requestID
            let origin = request.origin
            Task { @MainActor in LiveContainerAutoRefreshScheduler.runNow(requestID: requestID, origin: origin) }
        }
        NotificationCenter.default.addObserver(forName: UIScene.didActivateNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in LiveContainerAutoRefreshScheduler.recoverAfterLaunchOrResume() }
        }
        NotificationCenter.default.addObserver(forName: UIScene.didEnterBackgroundNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in LiveContainerAutoRefreshScheduler.schedule() }
        }
'''
    text = replace_once(text, "        application.shortcutItems = nil\n", startup, "host scheduler startup")
    text = replace_once(text, "class SceneDelegate:", HOST_SCHEDULER + "\nclass SceneDelegate:", "host scheduler implementation")
    text += '''
// Existing embedded notification handling remains in SideStoreSupport.
extension AppDelegate: UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
'''
    path.write_text(text, encoding="utf-8")


def patch_host_info(root: Path) -> None:
    path = root / "LiveContainer/Info.plist"
    info = plistlib.loads(path.read_bytes())
    identifiers = list(info.get("BGTaskSchedulerPermittedIdentifiers", []))
    modes = list(info.get("UIBackgroundModes", []))
    for identifier in (TASK_ID, TASK_ID + ".watchdog"):
        if identifier not in identifiers:
            identifiers.append(identifier)
    for mode in ("processing", "fetch"):
        if mode not in modes:
            modes.append(mode)
    info["BGTaskSchedulerPermittedIdentifiers"] = identifiers
    info["UIBackgroundModes"] = modes
    info["LCRefreshContractVersion"] = 2
    info.setdefault("NSAlarmKitUsageDescription", "Warn when an automatic refresh deadline needs attention.")
    path.write_bytes(plistlib.dumps(info, sort_keys=False))


def patch_alarm_provider(root: Path) -> None:
    (root / "LiveContainerSwiftUI/App/LiveContainerAutoRefreshAlarm.swift").write_text(ALARM_PROVIDER, encoding="utf-8")


def patch_project(root: Path) -> None:
    # Preserve the pinned project's target graph and deployment targets.
    path = root / "LiveContainer.xcodeproj/project.pbxproj"
    text = path.read_text(encoding="utf-8")
    if "SideStoreSupport.framework in Frameworks" not in text:
        text = replace_once(text, "/* Begin PBXBuildFile section */\n", "/* Begin PBXBuildFile section */\n\tA17ECAFE2DCA000000000001 = {isa = PBXBuildFile; fileRef = 173545A82E2C7913001B3B4C /* SideStoreSupport.framework */; };\n\tA17ECAFE2DCA000000000002 = {isa = PBXBuildFile; fileRef = 173545A82E2C7913001B3B4C /* SideStoreSupport.framework */; };\n", "host framework link build file")
        text = replace_once(text, "17554B6A2DA165D8004C6D90 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t);", "17554B6A2DA165D8004C6D90 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t\tA17ECAFE2DCA000000000001 /* SideStoreSupport.framework in Frameworks */,\n\t\t\t);", "host framework link phase")
        text = replace_once(text, "17413FB22D9C0BAE00F3F928 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n", "17413FB22D9C0BAE00F3F928 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t\tA17ECAFE2DCA000000000002 /* SideStoreSupport.framework in Frameworks */,\n", "host SwiftUI framework link phase")
        text = replace_once(text, "/* Begin PBXTargetDependency section */\n", "/* Begin PBXTargetDependency section */\n\tA17ECAFE2DCA000000000003 = {isa = PBXTargetDependency; target = 173545A72E2C7913001B3B4C /* SideStoreSupport */; targetProxy = 173545AC2E2C7913001B3B4C /* PBXContainerItemProxy */; };\n", "host SwiftUI target dependency")
        text = replace_once(text, "\t\t\tdependencies = (\n\t\t\t);\n\t\t\tfileSystemSynchronizedGroups = (\n\t\t\t\t17413FB62D9C0BAE00F3F928 /* LiveContainerSwiftUI */", "\t\t\tdependencies = (\n\t\t\t\tA17ECAFE2DCA000000000003 /* PBXTargetDependency */,\n\t\t\t);\n\t\t\tfileSystemSynchronizedGroups = (\n\t\t\t\t17413FB62D9C0BAE00F3F928 /* LiveContainerSwiftUI */", "host SwiftUI target dependency list")
    if "-weak_framework" not in text:
        old = '\t\t\t\tOTHER_LDFLAGS = (\n\t\t\t\t\t"-e",\n\t\t\t\t\t_LiveContainerMainC,\n\t\t\t\t);'
        new = '\t\t\t\tOTHER_LDFLAGS = (\n\t\t\t\t\t"-e",\n\t\t\t\t\t_LiveContainerMainC,\n\t\t\t\t\t"-weak_framework",\n\t\t\t\t\tAlarmKit,\n\t\t\t\t);'
        if old not in text:
            die("host AlarmKit weak-link anchor missing")
        text = text.replace(old, new)
    swiftui_flags = 'OTHER_LDFLAGS = "-Wl,-U,_OBJC_CLASS_$_RBSTarget";'
    if swiftui_flags in text:
        text = text.replace(swiftui_flags, 'OTHER_LDFLAGS = "-Wl,-U,_OBJC_CLASS_$_RBSTarget -weak_framework AlarmKit";')
    path.write_text(text, encoding="utf-8")


def patch_settings(root: Path) -> None:
    path = root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
    text = path.read_text(encoding="utf-8")
    link = '''                if store == .SideStore {
                    Section {
                        NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("SideStore scheduled refresh") }
                    }
                }
'''
    if "LCEmbeddedSideStoreRefreshView" not in text:
        text = replace_once(text, "            Form {\n", "            Form {\n" + link, "host refresh settings link")
        path.write_text(text, encoding="utf-8")
    (path.parent / "LCEmbeddedSideStoreRefreshView.swift").write_text(SETTINGS_VIEW, encoding="utf-8")


def patch_shared_app_group(root: Path) -> None:
    """Install the one runtime App Group identity as its own host source file.

    The LiveContainerSwiftUI target uses synchronized file-system groups, so a
    dedicated file is the only way to define the identity exactly once for the
    host module: the scheduler, the refresh settings screen, the unified shell
    and the app delegate all consume it, and duplicating it inside any one of
    those files would be a redeclaration in the same module.
    """
    path = root / SHARED_APP_GROUP_FILE
    expected = template("v3_shared_app_group.swift")
    if path.exists() and path.read_text(encoding="utf-8") != expected:
        die("outdated shared App Group template: reapply to the pinned clean source")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expected, encoding="utf-8")


def verify(root: Path) -> None:
    delegate = (root / "LiveContainerSwiftUI/App/AppDelegate.swift").read_text(encoding="utf-8")
    support = (root / "SideStoreSupport/SideStore.swift").read_text(encoding="utf-8")
    shared_group = (root / SHARED_APP_GROUP_FILE).read_text(encoding="utf-8")
    info = plistlib.loads((root / "LiveContainer/Info.plist").read_bytes())
    for identifier in (TASK_ID, TASK_ID + ".watchdog"):
        if identifier not in info.get("BGTaskSchedulerPermittedIdentifiers", []):
            die(f"missing permitted task identifier: {identifier}")
    if not {"processing", "fetch"}.issubset(info.get("UIBackgroundModes", [])):
        die("both processing and fetch background modes are required")
    for marker in (MARKER, "LiveContainerRefreshTaskIdentifiers.resolve", "LiveContainerRefreshCompletionGate",
                   "verifyRefreshManifest", "HOST_REFRESH_VERIFIED", "MISSED_BACKGROUND_REFRESH", "UNUserNotificationCenterDelegate",
                   "V3SharedAppGroup.publishRuntimeGroup(LCSharedUtils.appGroupID())"):
        if marker not in delegate:
            die(f"generated host missing {marker}")
    if "enum V3SharedAppGroup {" not in shared_group or "enum V3SharedRefreshStore {" not in shared_group:
        die("the host shared App Group identity was not installed")
    if shared_group.count("enum V3SharedAppGroup {") != 1:
        die("the host shared App Group identity is declared more than once")
    if shared_group != template("v3_shared_app_group.swift"):
        die("the installed host shared App Group identity is not the current template")
    # The quarantine store must never be an App Group suite: a quarantined write
    # that landed in the real group would look like a successful shared write.
    quarantine = shared_group[shared_group.index("static func quarantinedUserDefaults()"):]
    self_line = quarantine[:quarantine.index("\n    }\n")]
    if "com.kdt.livecontainer.v3.quarantined-shared-store" not in self_line:
        die("the quarantined read store must use a private suite name")
    if "group." in self_line:
        die("the quarantined read store must never open an App Group suite")
    if "throws -> UserDefaults {" not in shared_group or "case sharedStore" not in shared_group:
        die("an unavailable shared store must be a typed recoverable failure")
    if "V3ShortcutRefreshRequest(userInfo: notification.userInfo)" not in delegate:
        die("host refresh observer does not validate the request handoff")
    if "guard let request = V3ShortcutRefreshRequest(userInfo: notification.userInfo) else" not in delegate:
        die("host refresh observer does not reject malformed requests before scheduler admission")
    if BRIDGE.strip() not in support:
        die("combined intent bridge does not match the packaged metadata contract")
    helper_start = support.index("func performIntentRefresh(")
    helper_end = support.index("\n}\n", helper_start) + len("\n}\n")
    intent_helper = support[helper_start:helper_end]
    for required in ("LC_REFRESH_INTENT_TO_HOST_SCHEDULER_V1", "V3ShortcutRefreshRequest.make()",
                     'Notification.Name("LiveContainerAutoRefreshRunNow")', "request.userInfo"):
        if required not in intent_helper:
            die(f"host AppIntent scheduler adapter missing {required}")
    if "RefreshHandler.shared.startRefresh(identifier: identifier" in intent_helper:
        die("host AppIntent still bypasses the shared scheduler")
    if 'dialog: "All apps have been refreshed."' in support:
        die("host AppIntent still reports completion before the scheduler run")
    widget_start = support.index("public struct RefreshAllAppsWidgetIntent")
    main_start = support.index("public struct RefreshAllAppsIntent", widget_start)
    handler_start = support.index("class RefreshHandler:", main_start)
    widget_intent = support[widget_start:main_start]
    if "public static var openAppWhenRun = true" not in widget_intent:
        die("host widget AppIntent is not configured to execute in LiveContainer")
    if 'return .result(dialog: "Refresh All was requested in LiveContainer. Check Refresh History for the run result.")' not in widget_intent:
        die("host widget AppIntent reports success without directing the user to the scheduler result")
    if "public static var openAppWhenRun = true" not in support[main_start:handler_start]:
        die("host Refresh All AppIntent is not configured to execute in LiveContainer")
    if r'\\(' in HOST_SCHEDULER:
        die("Swift interpolation was double-escaped in a plain Swift template")
    compiler = shutil.which("swiftc")
    if compiler:
        for relative in ("LiveContainerSwiftUI/App/AppDelegate.swift", "LiveContainerSwiftUI/App/LiveContainerAutoRefreshAlarm.swift",
                         "LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift", "SideStoreSupport/SideStore.swift",
                         SHARED_APP_GROUP_FILE):
            subprocess.run([compiler, "-frontend", "-parse", str(root / relative)], check=True)
        client = root / "SideStoreSupport/SideStoreClient.swift"
        if client.exists():
            subprocess.run([compiler, "-frontend", "-parse", str(client)], check=True)


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_livecontainer_autorefresh.py <livecontainer-root>")
    root = Path(sys.argv[1]).resolve()
    if not (root / "LiveContainer.xcodeproj").exists():
        die(f"not a LiveContainer checkout: {root}")
    patch_support(root)
    patch_host_delegate(root)
    patch_shared_app_group(root)
    patch_host_info(root)
    patch_project(root)
    patch_alarm_provider(root)
    patch_settings(root)
    from patch_refresh_result_bridge import patch as patch_result_bridge
    patch_result_bridge(root)
    verify(root)
    print("LiveContainer host auto-refresh patch applied and verified")


if __name__ == "__main__":
    main()
