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


def template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


HOST_SCHEDULER = template("livecontainer_refresh_policy.swift") + "\n" + template("livecontainer_network_preflight.swift") + "\n" + template("livecontainer_refresh_scheduler.swift")
ALARM_PROVIDER = template("livecontainer_refresh_alarm.swift")
SETTINGS_VIEW = template("livecontainer_refresh_settings.swift")
BRIDGE = r'''
// LC_REFRESH_BRIDGE_V2_BEGIN
/// Use the action identity present in the combined package's intent metadata.
public enum LiveContainerRefreshBridge {
    public static func refreshAllApps() async throws {
        guard #available(iOS 17.0, *) else {
            throw NSError(domain: "LiveContainerRefresh.UnsupportedOS", code: 17,
                userInfo: [NSLocalizedDescriptionKey: "The embedded automatic refresh bridge requires iOS 17 or later."])
        }
        try Task.checkCancellation()
        try await RefreshHandler.shared.startRefresh(
            identifier: "RefreshAllIntent",
            mangledName: "16SideStoreSupport20RefreshAllAppsIntentV"
        )
        try Task.checkCancellation()
    }
}
// LC_REFRESH_BRIDGE_V2_END
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
    if "LC_REFRESH_BRIDGE_V2_BEGIN" in text:
        if BRIDGE.strip() not in text:
            die("outdated bridge template: reapply to the pinned clean source")
        return
    if "public enum LiveContainerRefreshBridge" in text:
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
        LiveContainerAutoRefreshScheduler.register()
        LiveContainerAutoRefreshScheduler.recoverAfterLaunchOrResume()
        LiveContainerAutoRefreshScheduler.schedule()
        NotificationCenter.default.addObserver(forName: Notification.Name("LiveContainerAutoRefreshScheduleChanged"), object: nil, queue: .main) { _ in
            Task { @MainActor in LiveContainerAutoRefreshScheduler.scheduleChanged() }
        }
        NotificationCenter.default.addObserver(forName: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil, queue: .main) { _ in
            Task { @MainActor in LiveContainerAutoRefreshScheduler.runNow() }
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


def verify(root: Path) -> None:
    delegate = (root / "LiveContainerSwiftUI/App/AppDelegate.swift").read_text(encoding="utf-8")
    support = (root / "SideStoreSupport/SideStore.swift").read_text(encoding="utf-8")
    info = plistlib.loads((root / "LiveContainer/Info.plist").read_bytes())
    for identifier in (TASK_ID, TASK_ID + ".watchdog"):
        if identifier not in info.get("BGTaskSchedulerPermittedIdentifiers", []):
            die(f"missing permitted task identifier: {identifier}")
    if not {"processing", "fetch"}.issubset(info.get("UIBackgroundModes", [])):
        die("both processing and fetch background modes are required")
    for marker in (MARKER, "LiveContainerRefreshTaskIdentifiers.resolve", "LiveContainerRefreshCompletionGate",
                   "verifyRefreshManifest", "HOST_REFRESH_VERIFIED", "MISSED_BACKGROUND_REFRESH", "UNUserNotificationCenterDelegate"):
        if marker not in delegate:
            die(f"generated host missing {marker}")
    if BRIDGE.strip() not in support:
        die("combined intent bridge does not match the packaged metadata contract")
    if r'\\(' in HOST_SCHEDULER:
        die("Swift interpolation was double-escaped in a plain Swift template")
    compiler = shutil.which("swiftc")
    if compiler:
        for relative in ("LiveContainerSwiftUI/App/AppDelegate.swift", "LiveContainerSwiftUI/App/LiveContainerAutoRefreshAlarm.swift",
                         "LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift", "SideStoreSupport/SideStore.swift"):
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
