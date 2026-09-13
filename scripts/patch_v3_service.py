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
        s = replace(s, "class RefreshHandler: NSObject, RefreshServer {", "@MainActor\nclass RefreshHandler: NSObject, RefreshServer {")
        s = replace(s, "        RefreshHandler.shared.progress = intentProgress",
                    "        await MainActor.run { RefreshHandler.shared.progress = intentProgress }")
        s = replace(s, "    func startRefresh(identifier: String, mangledName: String) async throws {", '''
    var v3RefreshToken: UUID?
    private var v3StoppingPID: Int32 = 0

    func startRefresh(identifier: String, mangledName: String) async throws {
        if identifier == "__v3_connect" {
            return try await v3_startRefresh(identifier: identifier, mangledName: mangledName)
        }
        guard v3RefreshToken == nil, !V3ServiceBridge.shared.isMutating else {
            throw NSError(domain: "V3SideStoreService", code: 5,
                userInfo: [NSLocalizedDescriptionKey: "Another SideStore operation is running."])
        }
        let token = UUID()
        v3RefreshToken = token
        defer { if v3RefreshToken == token { v3RefreshToken = nil } }
        let timeout = Task { @MainActor in
            do { try await Task.sleep(nanoseconds: 600_000_000_000) } catch { return }
            self.v3_cancelRefresh(token)
        }
        defer { timeout.cancel() }
        try await withTaskCancellationHandler(operation: {
            try Task.checkCancellation()
            try await v3_startRefresh(identifier: identifier, mangledName: mangledName)
        }, onCancel: {
            Task { @MainActor in self.v3_cancelRefresh(token) }
        })
    }

    private func v3_cancelRefresh(_ token: UUID) {
        guard v3RefreshToken == token else { return }
        v3_stopService()
    }

    func v3_stopService() {
        v3RefreshToken = nil
        c?.resume(throwing: CancellationError())
        c = nil
        launchContinuation?.resume(throwing: CancellationError())
        launchContinuation = nil
        v3LaunchID = UUID()
        v3StoppingPID = sideStorePid
        ext?._kill(15)
        client = nil
        v3Connection?.invalidate()
        v3Connection = nil
        sideStorePid = 0
        V3ServiceBridge.shared.disconnected()
    }

    private func v3_startRefresh(identifier: String, mangledName: String) async throws {
        if v3StoppingPID > 0 {
            let until = Date().addingTimeInterval(3)
            while getpgid(v3StoppingPID) > 0 && Date() < until {
                try await Task.sleep(nanoseconds: 50_000_000)
            }
            if getpgid(v3StoppingPID) > 0 {
                ext?._kill(9)
                throw NSError(domain: "V3SideStoreService", code: 6,
                    userInfo: [NSLocalizedDescriptionKey: "SideStore is stopping. Reconnect shortly."])
            }
            v3StoppingPID = 0
        }
''')
        s = replace(s, "        guard let client = self.client else {", '''        // V3_COMMAND_PATCH_V1: connect without invoking signing or refresh.
        if identifier == "__v3_connect" {
            guard self.client != nil else { throw NSError(domain: "V3SideStoreService", code: 1) }
            return
        }
        guard let client = self.client else {''')
        s = replace(s, "    var ext: NSExtension? = nil", "    var ext: NSExtension? = nil\n    var v3Connection: NSXPCConnection?\n    var v3LaunchID = UUID()")
        s = replace(s, "        connection.remoteObjectInterface = NSXPCInterface(with: RefreshClient.self)", '''        v3Connection = connection
        connection.invalidationHandler = { [weak self, weak connection] in
            DispatchQueue.main.async {
                guard let self, let connection, self.v3Connection === connection else { return }
                self.client = nil
                self.v3Connection = nil
                // Retire the disconnected process before allowing another DB owner.
                self.v3StoppingPID = self.sideStorePid
                self.v3LaunchID = UUID()
                self.ext?._kill(15)
                self.sideStorePid = 0
                let error = NSError(domain: "V3SideStoreService", code: 2, userInfo: [NSLocalizedDescriptionKey: "SideStore disconnected."])
                self.launchContinuation?.resume(throwing: error)
                self.launchContinuation = nil
                self.c?.resume(throwing: error)
                self.c = nil
                Task { @MainActor in V3ServiceBridge.shared.disconnected() }
            }
        }
        connection.interruptionHandler = connection.invalidationHandler
        connection.remoteObjectInterface = NSXPCInterface(with: RefreshClient.self)''')
        # The pinned launch callback could beat registration of its continuation.
        start = s.index("            let uuid = await ext.beginRequest(withInputItems: [extensionItem])")
        end = s.index("\n        }\n", start)
        s = s[:start] + '''            let launchID = UUID()
            self.v3LaunchID = launchID
            try await withUnsafeThrowingContinuation { continuation in
                self.launchContinuation = continuation
                Task {
                    let uuid = await ext.beginRequest(withInputItems: [extensionItem])
                    guard self.v3LaunchID == launchID else { return }
                    self.sideStorePid = ext.pid(forRequestIdentifier: uuid)
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 45) {
                    guard self.v3LaunchID == launchID, let pending = self.launchContinuation else { return }
                    self.launchContinuation = nil
                    self.client = nil
                    self.sideStorePid = 0
                    self.v3LaunchID = UUID()
                    pending.resume(throwing: NSError(domain: "V3SideStoreService", code: 3,
                        userInfo: [NSLocalizedDescriptionKey: "SideStore did not start within 45 seconds."]))
                    ext._kill(9)
                }
            }''' + s[end:]
        s = replace(s, "                self.launchContinuation = nil\n            }", '''                self.launchContinuation?.resume(throwing: NSError(domain: "V3SideStoreService", code: 4,
                    userInfo: [NSLocalizedDescriptionKey: "SideStore stopped during startup."]))
                self.launchContinuation = nil
                self.client = nil
                Task { @MainActor in V3ServiceBridge.shared.disconnected() }
            }''')
        # NSXPC and NSExtension callbacks arrive on arbitrary queues. Funnel every
        # continuation, client and PID transition through the main actor.
        s = replace(s, "            ext.setRequestInterruptionBlock { uuid in",
                    "            ext.setRequestInterruptionBlock { uuid in\n                Task { @MainActor in\n                guard self.ext === ext else { return }")
        s = replace(s, "            }\n            \n            let launchID = UUID()",
                    "                }\n            }\n            \n            let launchID = UUID()")
        callbacks = [
            ("func updateProgress(_ value: Double)", "updateProgress", "_ value: Double", "value"),
            ("func finishRefresh(_ error: String?, runID: String, verification: Data?)", "finishRefresh", "_ error: String?, runID: String, verification: Data?", "error, runID: runID, verification: verification"),
            ("func finish(_ error: String?)", "finish", "_ error: String?", "error"),
            ("func onConnection(_ connection: NSXPCConnection!)", "onConnection", "_ connection: NSXPCConnection!", "connection"),
            ("func finishedLaunching()", "finishedLaunching", "", ""),
            ("func add(_ request: UNNotificationRequest)", "add", "_ request: UNNotificationRequest", "request"),
            ("func removePendingNotificationRequests(withIdentifiers identifiers: [String])", "removePendingNotificationRequests", "withIdentifiers identifiers: [String]", "withIdentifiers: identifiers"),
        ]
        for declaration, name, arguments, call in callbacks:
            s = replace(s, "    " + declaration + " {",
                "    nonisolated " + declaration + " {\n"
                "        Task { @MainActor in self.v3_" + name + "(" + call + ") }\n"
                "    }\n\n    private func v3_" + name + "(" + arguments + ") {")
        s = s.replace('            finish(', '            v3_finish(').replace('        finish(error)', '        v3_finish(error)')
        s = replace(s, "        try await withUnsafeThrowingContinuation { c in\n            self.c = c",
                    '''        // A cancelled/timed-out command may still be unwinding in SideStore.
        // Ask its authoritative gate before executing the separate refresh intent.
        let serviceStatus = try await V3ServiceBridge.shared.request(operation: "snapshot")
        guard serviceStatus["busy"] as? Bool == false else {
            throw NSError(domain: "V3SideStoreService", code: 5,
                userInfo: [NSLocalizedDescriptionKey: "SideStore is finishing another operation. Retry shortly."])
        }
        guard self.c == nil, !V3ServiceBridge.shared.isMutating else {
            throw NSError(domain: "V3SideStoreService", code: 5,
                userInfo: [NSLocalizedDescriptionKey: "Another SideStore operation is running."])
        }
        try await withUnsafeThrowingContinuation { c in
            self.c = c''')
        # A connected service can answer status queries while a refresh runs.
        s = replace(s, "        if c != nil {", '        if c != nil && identifier != "__v3_connect" {')
        s = replace(s, '        if identifier == "__v3_connect" {\n            guard self.client != nil',
                    '''        if identifier == "__v3_connect" {
            let until = Date().addingTimeInterval(45)
            while (self.launchContinuation != nil || self.sideStorePid <= 0) && Date() < until {
                try Task.checkCancellation()
                try await Task.sleep(nanoseconds: 50_000_000)
            }
            guard self.client != nil''')
        return s + (TEMPLATES / "v3_wire_contract.swift").read_text(encoding="utf-8") + (TEMPLATES / "v3_service_bridge.swift").read_text(encoding="utf-8")
    edit(live, "SideStoreSupport/SideStore.swift", host)
    edit(live, "SideStoreSupport/SideStoreClient.swift", lambda s: replace(replace(s,
        "reportRefreshResult(error.localizedDescription, server: server)",
        'reportRefreshResult("SideStore refresh failed. Check account, pairing and operation diagnostics.", server: server)'),
        '"SideStore could not encode installation results: " + error.localizedDescription',
        '"SideStore could not encode installation results."'))
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
