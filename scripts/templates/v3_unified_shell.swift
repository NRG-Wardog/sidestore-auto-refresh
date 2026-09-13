import SwiftUI
import Combine
import SideStoreSupport

// V3_UNIFIED_SHELL_V1_BEGIN
extension LCAppModel {
    var v3Identity: String { "guest:" + (appInfo.relativeBundlePath ?? appInfo.bundlePath() ?? "") }
}

struct V3UnifiedShell: View {
    var body: some View { V3ApplicationRoot(content: V3UnifiedTabs()) }
}

struct V3UnifiedTabs: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @StateObject private var status = V3SideStoreStatusStore()
    @State private var selectedInitialTab = false
    private let monitor = Timer.publish(every: 30, on: .main, in: .common).autoconnect()
    var body: some View {
        TabView(selection: $sharedModel.selectedTab) {
            V3HomeView().tabItem { Label("Home", systemImage: "house.fill") }.tag(LCTabIdentifier.home)
            LCAppListView().tabItem { Label("Apps", systemImage: "square.stack.3d.up.fill") }.tag(LCTabIdentifier.apps)
            V3SourcesView().tabItem { Label("Sources", systemImage: "books.vertical") }.tag(LCTabIdentifier.sources)
            LCEmbeddedSideStoreRefreshView().tabItem { Label("Refresh", systemImage: "arrow.clockwise") }.tag(LCTabIdentifier.refresh)
            LCSettingsView().tabItem { Label("Settings", systemImage: "gearshape.fill") }.tag(LCTabIdentifier.settings)
        }
        .environmentObject(status)
        .accessibilityIdentifier("V3_UNIFIED_SHELL_V1")
        .task {
            status.reload()
            guard !selectedInitialTab else { return }
            selectedInitialTab = true
            if sharedModel.deepLink == nil { sharedModel.selectedTab = .home }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in status.reload() }
        .onReceive(monitor) { _ in status.reload() }
        .onOpenURL(perform: dispatchURL)
        .sheet(item: $status.presentation) { V3OperationSheet(request: $0).environmentObject(status) }
        .alert("SideStore", isPresented: Binding(get: { status.error != nil }, set: { if !$0 { status.error = nil } })) {
            Button("OK", role: .cancel) { status.error = nil }
        } message: { Text(status.error ?? "") }
    }
    private func dispatchURL(_ url: URL) {
        if url.host?.lowercased() == "source" {
            sharedModel.selectedTab = .sources
            if let source = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "url" })?.value { status.sourceURL = source }
            return
        }
        if url.isFileURL || url.scheme?.lowercased() == "sidestore" { sharedModel.selectedTab = .apps }
        else {
            switch url.host?.lowercased() {
            case "livecontainer-launch", "install", "open-web-page", "open-url": sharedModel.selectedTab = .apps
            case "certificate": sharedModel.selectedTab = .settings
            case "refresh": sharedModel.selectedTab = .refresh
            default: return
            }
        }
        sharedModel.deepLink = url
    }
}

struct V3OperationRequest: Identifiable {
    let id = UUID()
    let operation: String
    let target: String
    let title: String
    var value: Bool? = nil
}

@MainActor
final class V3SideStoreStatusStore: ObservableObject {
    @Published private(set) var account = "Not available"
    @Published private(set) var signing = "Unknown"
    @Published private(set) var team = "Unknown"
    @Published private(set) var certificate = "Unknown"
    @Published private(set) var pairing = "Unknown"
    @Published private(set) var updatedAt: Date?
    @Published private(set) var installedApps: [V3SideStoreApp] = []
    @Published private(set) var sources: [V3SideStoreSource] = []
    @Published private(set) var settings: [String: Bool] = [:]
    @Published var error: String?
    @Published var presentation: V3OperationRequest?
    @Published var sourceURL = ""
    @Published private(set) var loading = false
    @Published private(set) var connected = false
    var installedAppCount: Int { installedApps.count }
    var isStale: Bool { !connected || (updatedAt.map { Date().timeIntervalSince($0) > 120 } ?? true) }
    func reload() {
        guard !loading, presentation == nil else { return }
        loading = true
        Task {
            defer { loading = false }
            do {
                for attempt in 0..<4 {
                    do { accept(try await V3ServiceBridge.shared.request(operation: "snapshot")); break }
                    catch {
                        guard attempt < 3, (error as NSError).domain == "V3SideStoreService.notReady" else { throw error }
                        try await Task.sleep(nanoseconds: 1_000_000_000)
                    }
                }
            } catch { connected = false; self.error = error.localizedDescription }
        }
    }
    func accept(_ snapshot: [String: Any]) {
        account = snapshot["account"] as? String ?? "Not signed in"
        team = snapshot["team"] as? String ?? "No active team"
        signing = snapshot["signing"] as? String ?? "Unknown"
        certificate = snapshot["certificate"] as? String ?? "Unknown"
        pairing = snapshot["pairing"] as? String ?? "Unknown"
        updatedAt = snapshot["updatedAt"] as? Date
        installedApps = (snapshot["installedApps"] as? [[String: Any]] ?? []).compactMap(V3SideStoreApp.init)
        sources = (snapshot["sources"] as? [[String: Any]] ?? []).compactMap(V3SideStoreSource.init)
        settings = snapshot["settings"] as? [String: Bool] ?? [:]
        connected = true
    }
    func perform(_ operation: String, target: String = "", title: String, value: Bool? = nil) {
        guard presentation == nil else { return }
        presentation = V3OperationRequest(operation: operation, target: target, title: title, value: value)
    }
}

struct V3SideStoreApp: Identifiable, Hashable {
    let identifier: String, bundleID: String, name: String, version: String, certificateStatus: String
    let isActive: Bool, hasUpdate: Bool, isHost: Bool
    let expirationDate: Date?
    let openURL: URL?
    var id: String { "sidestore:" + identifier }
    init?(_ row: [String: Any]) {
        guard let identifier = row["identifier"] as? String, let bundleID = row["bundleID"] as? String,
              let name = row["name"] as? String, let version = row["version"] as? String,
              let isActive = row["isActive"] as? Bool, let hasUpdate = row["hasUpdate"] as? Bool else { return nil }
        self.identifier = identifier; self.bundleID = bundleID; self.name = name; self.version = version
        self.isActive = isActive; self.hasUpdate = hasUpdate
        expirationDate = row["expirationDate"] as? Date
        certificateStatus = row["certificateStatus"] as? String ?? "unknown"
        openURL = (row["openURL"] as? String).flatMap(URL.init(string:))
        isHost = row["isHost"] as? Bool ?? false
    }
}

struct V3SideStoreSource: Identifiable, Hashable {
    let identifier: String, name: String, subtitle: String, url: String
    let appCount: Int
    let canRemove: Bool
    var id: String { "sidestore-source:" + identifier }
    init?(_ row: [String: Any]) {
        guard let identifier = row["identifier"] as? String, let name = row["name"] as? String,
              let url = row["url"] as? String, let appCount = row["appCount"] as? Int else { return nil }
        self.identifier = identifier; self.name = name; self.url = url; self.appCount = appCount
        subtitle = row["subtitle"] as? String ?? ""; canRemove = row["canRemove"] as? Bool ?? false
    }
}

struct V3InstalledAppsSection: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @AppStorage("LCAppLayoutStyle", store: LCUtils.appGroupUserDefault) private var layout: AppLayoutStyle = .list
    @AppStorage("LCShowAppLabels", store: LCUtils.appGroupUserDefault) private var labels = true
    var query = ""
    private var apps: [V3SideStoreApp] { status.installedApps.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) || $0.bundleID.localizedCaseInsensitiveContains(query) } }
    var body: some View {
        VStack(alignment: .leading) {
            Text("Sideloaded Apps").font(.headline)
            if status.isStale { Button("Reconnect to SideStore") { status.reload() } }
            if layout == .grid {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 100))]) {
                    ForEach(apps) { app in
                        NavigationLink(destination: V3SideStoreAppDetail(identifier: app.identifier)) {
                            VStack {
                                Image(systemName: "app.fill").font(.system(size: 44))
                                if labels { Text(app.name).lineLimit(2).font(.caption) }
                                Text(app.isActive ? "Active" : "Inactive").font(.caption2)
                            }.frame(maxWidth: .infinity, minHeight: 88)
                        }.accessibilityLabel(app.name).contextMenu { V3AppActions(app: app) }
                    }
                }
            } else {
                ForEach(apps) { app in
                    NavigationLink(destination: V3SideStoreAppDetail(identifier: app.identifier)) {
                        HStack {
                            Image(systemName: "app.fill").font(layout == .compactList ? .title2 : .largeTitle)
                            VStack(alignment: .leading) {
                                Text(app.name)
                                Text(app.version + " · " + (app.isActive ? "Active" : "Inactive")).font(.caption).foregroundColor(.secondary)
                                if layout != .compactList, let expiration = app.expirationDate { Text("Expires " + expiration.formatted(date: .abbreviated, time: .omitted)).font(.caption) }
                            }
                            Spacer()
                            if app.hasUpdate { Image(systemName: "arrow.down.circle") }
                        }.padding(.vertical, layout == .compactList ? 4 : 12)
                    }.contextMenu { V3AppActions(app: app) }
                }
            }
            if apps.isEmpty { Text(status.loading ? "Loading apps…" : "No sideloaded apps").foregroundColor(.secondary) }
            Text("LiveContainer Guests").font(.headline).padding(.top)
        }.padding(.horizontal)
    }
}

struct V3AppActions: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @EnvironmentObject private var sharedModel: SharedModel
    let app: V3SideStoreApp
    var body: some View {
        if app.isActive, let url = app.openURL, !app.isHost {
            Button("Open") { UIApplication.shared.open(url) { opened in
                if !opened { Task { @MainActor in status.error = "The app could not be opened. Check whether it is still installed." } }
            } }
        }
        Button("Refresh") { sharedModel.selectedTab = .refresh }
        if app.hasUpdate { Button("Update") { action("update", "Update " + app.name) } }
        if !app.isHost {
            Button(app.isActive ? "Deactivate" : "Activate") { action(app.isActive ? "deactivate" : "activate", app.isActive ? "Deactivate app" : "Activate app") }
            Button("Back Up") { action("backup", "Back up app") }
            Button("Restore Backup") { action("restore", "Restore backup") }
            Button("Enable JIT") { action("jit", "Enable JIT") }
            Button("Remove from Library", role: .destructive) { action("remove", "Remove " + app.name + " from library and erase its backups") }
            if app.isActive { Button("Delete from Device", role: .destructive) { action("delete", "Delete " + app.name + " and erase its data and backups") } }
        }
    }
    private func action(_ operation: String, _ title: String) { status.perform(operation, target: app.identifier, title: title) }
}

struct V3SideStoreAppDetail: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    let identifier: String
    private var app: V3SideStoreApp? { status.installedApps.first { $0.identifier == identifier } }
    var body: some View {
        List {
            if let app {
                Section("Status") {
                    Text(app.isActive ? "Active" : "Inactive"); Text("Certificate: " + app.certificateStatus.capitalized)
                    if let expiration = app.expirationDate { Text("Expires " + expiration.formatted(date: .abbreviated, time: .shortened)) }
                    Text("Version " + app.version); Text(app.bundleID).font(.caption).textSelection(.enabled)
                }
                Section("Actions") { V3AppActions(app: app) }
            } else { Text("This app is no longer in the library.") }
        }.navigationTitle(app?.name ?? "App")
    }
}

struct V3SourcesView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    var body: some View {
        NavigationView {
            List {
                Section("Add Source") {
                    TextField("https://example.com/source.json", text: $status.sourceURL).keyboardType(.URL).autocapitalization(.none).disableAutocorrection(true)
                    Button("Preview and Add Source") { status.perform("addSource", target: status.sourceURL, title: "Add source") }.disabled(status.sourceURL.isEmpty)
                }
                Section("Sources") {
                    ForEach(status.sources) { source in
                        NavigationLink(destination: V3CatalogView(source: source)) {
                            VStack(alignment: .leading) { Text(source.name); Text("\(source.appCount) apps").font(.caption).foregroundColor(.secondary) }
                        }.contextMenu {
                            if source.canRemove { Button("Remove Source", role: .destructive) { status.perform("removeSource", target: source.identifier, title: "Remove source") } }
                        }
                    }
                }
            }.navigationTitle("Sources").toolbar { Button("Reload") { status.perform("refreshSources", title: "Update sources") } }
        }.navigationViewStyle(StackNavigationViewStyle())
    }
}

struct V3CatalogApp: Identifiable {
    let id: String, name: String, version: String, developer: String, description: String, installedID: String
    let canInstall: Bool
    init?(_ row: [String: Any]) {
        guard let id = row["identifier"] as? String, let name = row["name"] as? String else { return nil }
        self.id = id; self.name = name; version = row["version"] as? String ?? ""
        developer = row["developer"] as? String ?? ""; description = row["description"] as? String ?? ""
        installedID = row["installedID"] as? String ?? ""; canInstall = row["canInstall"] as? Bool ?? false
    }
}

struct V3CatalogView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    let source: V3SideStoreSource
    @State private var apps: [V3CatalogApp] = []
    @State private var query = ""
    @State private var loading = true
    @State private var error: String?
    var body: some View {
        List {
            if loading { ProgressView() }
            if let error { Text(error); Button("Retry") { Task { await load() } } }
            ForEach(apps.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }) { app in
                NavigationLink {
                    List {
                        Section { Text(app.developer); Text(app.version); Text(app.description) }
                        Section {
                            if let installed = status.installedApps.first(where: { $0.identifier == app.installedID }) { V3AppActions(app: installed) }
                            else { Button("Install") { status.perform("install", target: app.id, title: "Install " + app.name) }.disabled(!app.canInstall) }
                        }
                    }.navigationTitle(app.name)
                } label: { VStack(alignment: .leading) { Text(app.name); Text(app.version).font(.caption) } }
            }
        }.navigationTitle(source.name).searchable(text: $query).task { await load() }
    }
    private func load() async {
        loading = true; error = nil
        defer { loading = false }
        do {
            let result = try await V3ServiceBridge.shared.request(operation: "catalog", target: source.identifier)
            apps = (result["apps"] as? [[String: Any]] ?? []).compactMap(V3CatalogApp.init)
        } catch { self.error = error.localizedDescription }
    }
}

struct V3AccountSettings: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    var body: some View {
        Section("Account and Signing") {
            Text(status.account); Text(status.team); Text(status.signing)
            Text(status.certificate)
            ForEach(status.installedApps.filter { $0.isHost }) { app in
                Text("Certificate: " + app.certificateStatus.capitalized)
                if let date = app.expirationDate { Text("Host expires " + date.formatted(date: .abbreviated, time: .shortened)) }
            }
            Button("Sign In / Authenticate") { status.perform("signIn", title: "Account and signing") }
            Button("Sync App IDs") { status.perform("syncAppIDs", title: "Sync App IDs") }
            panel("Certificates", "certificates")
            panel("Developer Services", "developerServices")
            Button("Sign Out", role: .destructive) { status.perform("signOut", title: "Sign out") }
        }
        Section("SideStore") {
            Text(status.pairing)
            Button("Import Pairing File") { status.perform("importPairing", title: "Import pairing file") }
            panel("Connection", "connection"); panel("Anisette Servers", "anisette")
            panel("Health Check", "health"); panel("SideStore Backups", "backups")
            panel("SideJIT Server", "sideJIT")
            setting("Beta updates", "betaUpdates"); setting("Disable idle timeout", "idleTimeoutDisabled")
            setting("Disable response caching", "responseCachingDisabled"); setting("Detailed operation logging", "verboseOperations")
            Button("Clear Download Cache") { status.perform("clearCache", title: "Clear download cache") }
        }
        Section("Guest Runtime") {
            NavigationLink("Tweaks", destination: LCTweaksView())
        }
    }
    private func panel(_ title: String, _ key: String) -> some View {
        Button(title) { status.perform("panel", target: key, title: title) }
    }
    private func setting(_ title: String, _ key: String) -> some View {
        Toggle(title, isOn: Binding(get: { status.settings[key] ?? false }, set: { status.perform("setSetting", target: key, title: title, value: $0) })).disabled(status.isStale)
    }
}

struct V3OperationSheet: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @Environment(\.dismiss) private var dismiss
    let request: V3OperationRequest
    @State private var pid: Int32 = 0
    @State private var ready = false
    @State private var task: Task<Void, Never>?
    @State private var message = ""
    @State private var started = false
    var body: some View {
        NavigationView {
            VStack {
                if !started { Text(request.title).padding(); Button("Continue") { start() }.disabled(!ready) }
                else { ProgressView("Working…").padding() }
                if !message.isEmpty { Text(message).padding() }
                if #available(iOS 16.0, *), pid > 0 { V3RemoteServiceView(pid: pid, ready: $ready) }
                else { Text("Interactive SideStore operations require iOS 16 or later.") }
            }.navigationTitle(request.title)
                .toolbar { Button(started ? "Cancel" : "Close") { task?.cancel(); dismiss() } }
                .task {
                    do { try await V3ServiceBridge.shared.connect(); pid = V3ServiceBridge.shared.processID }
                    catch { message = error.localizedDescription }
                }
        }.navigationViewStyle(StackNavigationViewStyle()).interactiveDismissDisabled(started)
            .onDisappear { task?.cancel(); status.reload() }
    }
    private func start() {
        started = true
        task = Task {
            do { status.accept(try await V3ServiceBridge.shared.request(operation: request.operation, target: request.target, value: request.value)); dismiss() }
            catch { message = error.localizedDescription; started = false }
        }
    }
}

@available(iOS 16.0, *)
struct V3RemoteServiceView: UIViewControllerRepresentable {
    let pid: Int32
    @Binding var ready: Bool
    func makeCoordinator() -> Coordinator { Coordinator(ready: $ready) }
    func makeUIViewController(context: Context) -> AppSceneViewController { AppSceneViewController(servicePID: pid, delegate: context.coordinator) }
    func updateUIViewController(_ controller: AppSceneViewController, context: Context) {}
    static func dismantleUIViewController(_ controller: AppSceneViewController, coordinator: Coordinator) { controller.appTerminationCleanUp() }
    final class Coordinator: NSObject, AppSceneViewControllerDelegate {
        var ready: Binding<Bool>
        init(ready: Binding<Bool>) { self.ready = ready }
        func appSceneVCAppDidExit(_ vc: AppSceneViewController!) { ready.wrappedValue = false }
        func appSceneVC(_ vc: AppSceneViewController!, didInitializeWithError error: Error!) { ready.wrappedValue = error == nil }
        func appSceneVCWillActivateScene(_ vc: AppSceneViewController!) { DispatchQueue.main.async { self.ready.wrappedValue = true } }
        func appSceneVC(_ vc: AppSceneViewController!, didUpdateFrom settings: UIMutableApplicationSceneSettings!, transitionContext context: Any!, lifecycleActionType: UInt32) {}
    }
}

private struct V3HomeView: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @AppStorage("liveContainerAutoRefreshHealthState", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var refreshState = "UNKNOWN"
    private let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore")
    var body: some View {
        NavigationView {
            List {
                Section("Status") {
                    Label("\(sharedModel.apps.count) LiveContainer guests", systemImage: "rectangle.stack.fill")
                    Label("\(status.installedAppCount) sideloaded apps", systemImage: "app.badge")
                    Text(status.account); Text(status.team); Text(status.signing); Text(status.certificate); Text(status.pairing)
                    Text(status.isStale ? "SideStore status is out of date" : "SideStore connected").foregroundColor(.secondary)
                    if let date = status.installedApps.filter({ $0.isActive }).compactMap(\.expirationDate).min() { Text("Next expiration: " + date.formatted(date: .abbreviated, time: .shortened)) }
                    Button("Reload Status") { status.reload() }
                }
                Section("Refresh") {
                    Text(refreshState.replacingOccurrences(of: "_", with: " ").capitalized)
                    if let date = defaults?.object(forKey: "liveContainerAutoRefreshLastSuccessfulRefresh") as? Date { Text("Last verified run: " + date.formatted(date: .abbreviated, time: .shortened)) }
                    if let date = defaults?.object(forKey: "liveContainerAutoRefreshTargetDeadline") as? Date { Text("Refresh deadline: " + date.formatted(date: .abbreviated, time: .shortened)) }
                    if let error = defaults?.string(forKey: "liveContainerAutoRefreshLastError"), !error.isEmpty { Text(error).font(.caption).foregroundColor(.red) }
                    Text(MultitaskManager.isMultitasking() ? "LiveProcess guests are running" : "No LiveProcess guests are running").font(.caption)
                    Text("Refresh requires Wi-Fi and LocalDevVPN.").font(.caption)
                    Button("Refresh and Schedule") { sharedModel.selectedTab = .refresh }
                }
                Section("Quick Actions") {
                    Button("Open Apps") { sharedModel.selectedTab = .apps }
                    Button("Browse Sources") { sharedModel.selectedTab = .sources }
                    Button("Account and Settings") { sharedModel.selectedTab = .settings }
                }
            }.navigationTitle("Home")
        }.navigationViewStyle(StackNavigationViewStyle())
    }
}
// V3_UNIFIED_SHELL_V1_END
