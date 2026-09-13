#!/usr/bin/env python3
"""Pinned, transactional and hash-verified v3 command integration."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

TEMPLATES = Path(__file__).with_name("templates")
PINS = ("12377cf3b91d51739a33f14a302e5f522b238593", "ff25922e5c13ccfafd83bda5092910d848ebd409")
MARKER = "V3_COMMAND_PATCH_V1"


def replace(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f"v3 service: expected exactly one anchor {old[:100]!r}, found {text.count(old)}")
    return text.replace(old, new, 1)


def patch(live, side):
    roots = (live, side)
    for root, pin in zip(roots, PINS):
        actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        if actual != pin:
            raise SystemExit(f"v3 service: unpinned input {actual}; expected {pin}")
    manifest = live / ".v3-command-patch.json"
    template_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in TEMPLATES.glob("v3_*.swift")}
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous["templates"] != template_hashes:
            raise SystemExit("v3 service: template changed; apply to fresh pinned sources")
        for index, relative, digest in previous["files"]:
            if hashlib.sha256((roots[index] / relative).read_bytes()).hexdigest() != digest:
                raise SystemExit(f"v3 service: previously patched file drifted: {relative}")
        return

    changes = {}
    def edit(root, relative, transform):
        path = root / relative
        changes[path] = transform(changes.get(path, path.read_text(encoding="utf-8")))

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
        return s + (TEMPLATES / "v3_wire_contract.swift").read_text(encoding="utf-8") + (TEMPLATES / "v3_service_bridge.swift").read_text(encoding="utf-8")
    edit(live, "SideStoreSupport/SideStore.swift", host)
    # The shared combined-startup adapter owns structured refresh error/result encoding.
    edit(side, "AltStore/AppDelegate.swift", lambda s: s + (TEMPLATES / "v3_wire_contract.swift").read_text(encoding="utf-8") + (TEMPLATES / "v3_sidestore_service.swift").read_text(encoding="utf-8"))
    edit(live, "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift", lambda s: replace(s,
        "        NavigationView {\n            ScrollView {", "        NavigationView {\n            ScrollView {\n                V3InstalledAppsSection(query: searchContext.debouncedQuery)"))
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
        // V3_COMMAND_PATCH_V1: no legacy tab bar in a service scene.
        let serviceWindow = UIWindow(windowScene: windowScene)
        V3SideStoreService.presenter.view.backgroundColor = .systemBackground
        serviceWindow.rootViewController = V3SideStoreService.presenter
        self.window = serviceWindow
        serviceWindow.makeKeyAndVisible()'''))

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
    records = []
    for path, content in changes.items():
        encoded = content.encode("utf-8")
        index = 0 if live in path.parents else 1
        records.append([index, str(path.relative_to(roots[index])).replace("\\", "/"), hashlib.sha256(encoded).hexdigest()])
    # Validate all anchors before writing anything.
    for path, content in changes.items():
        path.write_bytes(content.encode("utf-8"))
    manifest.write_text(json.dumps({"pins": PINS, "templates": template_hashes, "files": records}, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: patch_v3_service.py LIVE_CONTAINER SIDE_STORE")
    patch(*(Path(arg).resolve() for arg in sys.argv[1:]))
    print("v3 command patch applied and verified")
