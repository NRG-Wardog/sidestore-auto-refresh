import SwiftUI
import Combine
import SideStoreSupport
import UniformTypeIdentifiers

// V3_UNIFIED_SHELL_V1_BEGIN
enum V3AppIdentity: Hashable {
    case guest(path: String)
    case installed(uri: String)
    case source(identifier: String)
}

extension LCAppModel {
    var v3Identity: V3AppIdentity { .guest(path: appInfo.relativeBundlePath ?? appInfo.bundlePath() ?? "") }
}

struct V3UnifiedShell: View {
    var body: some View { V3ApplicationRoot(content: V3UnifiedTabs()) }
}

struct V3UnifiedTabs: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @StateObject private var status = V3SideStoreStatusStore()
    @State private var selectedInstallURL: URL?
    @State private var selectedPairingURL: URL?
    private let monitor = Timer.publish(every: 30, on: .main, in: .common).autoconnect()
    var body: some View {
        TabView(selection: $sharedModel.selectedTab) {
            V3HomeView().tabItem { Label("Home", systemImage: "house.fill") }.tag(LCTabIdentifier.home)
            LCAppListView().tabItem { Label("Apps", systemImage: "square.stack.3d.up.fill") }.tag(LCTabIdentifier.apps)
            V3SourcesView().tabItem { Label("Sources", systemImage: "books.vertical") }.tag(LCTabIdentifier.sources)
            LCSettingsView().tabItem { Label("Settings", systemImage: "gearshape.fill") }.tag(LCTabIdentifier.settings)
        }
        .environmentObject(status)
        .accessibilityIdentifier("V3_UNIFIED_SHELL_V1")
        .task {
            status.reload(manual: false)
            if let pending = UserDefaults.standard.string(forKey: "V3PendingSideStoreURL"), let url = URL(string: pending) {
                UserDefaults.standard.removeObject(forKey: "V3PendingSideStoreURL")
                if url.isFileURL {
                    status.stageSharedIPA(url, bookmark: LCUtils.appGroupUserDefault.data(forKey: "LCLaunchExtensionFileBookmark"), title: "Install shared app")
                    LCUtils.appGroupUserDefault.removeObject(forKey: "LCLaunchExtensionFileBookmark")
                } else { dispatchURL(url) }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in status.reload(manual: false) }
        .onReceive(monitor) { _ in status.reload(manual: false) }
        .onOpenURL(perform: dispatchURL)
        .sheet(isPresented: $status.installPickerPresented, onDismiss: {
            if let url = selectedInstallURL {
                selectedInstallURL = nil
                status.stageSharedIPA(url, title: "Install / Sideload App")
            }
        }) {
            V3IPADocumentPicker { url in
                selectedInstallURL = url
                status.installPickerPresented = false
            }
        }
        .sheet(isPresented: $status.pairingPickerPresented, onDismiss: {
            if let url = selectedPairingURL {
                selectedPairingURL = nil
                status.stagePairingFile(url)
            }
        }) {
            V3PairingDocumentPicker { url in
                selectedPairingURL = url
                status.pairingPickerPresented = false
            }
        }
        .sheet(isPresented: $status.signInPresented, onDismiss: { status.reload() }) {
            NavigationView { V3SignInView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .fullScreenCover(item: $status.presentation) { V3OperationSheet(request: $0).environmentObject(status) }
        .sheet(isPresented: $status.refreshPresented, onDismiss: { status.reload() }) {
            NavigationView { LCEmbeddedSideStoreRefreshView()
                .navigationTitle("Refresh")
                .navigationBarTitleDisplayMode(.inline) }
            .navigationViewStyle(StackNavigationViewStyle())
        }
        .alert("SideStore", isPresented: Binding(get: { status.error != nil }, set: { if !$0 { status.error = nil } })) {
            Button("Copy Diagnostics") { UIPasteboard.general.string = status.error }
            Button("Retry Connection") { status.reload() }
            Button("OK", role: .cancel) { status.error = nil }
        } message: { Text(status.error ?? "") }
    }
    private func dispatchURL(_ url: URL) {
        if ["https", "http"].contains(url.scheme?.lowercased() ?? "") {
            status.perform("installURL", target: url.absoluteString, title: "Install shared app")
            return
        }
        if url.host?.lowercased() == "livecontainer-launch",
           let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
           query.contains(where: { $0.name == "bundle-name" && $0.value == "builtinSideStore" }) {
            if let encoded = query.first(where: { $0.name == "open-url" })?.value,
               let data = Data(base64Encoded: encoded), let value = String(data: data, encoding: .utf8),
               let selected = URL(string: value) {
                if selected.isFileURL {
                    let bookmark = LCUtils.appGroupUserDefault.data(forKey: "LCLaunchExtensionFileBookmark")
                    status.stageSharedIPA(selected, bookmark: bookmark, title: "Install shared app")
                    LCUtils.appGroupUserDefault.removeObject(forKey: "LCLaunchExtensionFileBookmark")
                } else { dispatchURL(selected) }
            } else { sharedModel.selectedTab = .settings }
            return
        }
        if url.scheme?.lowercased() == "sidestore", url.host?.lowercased() == "appbackupresponse" {
            let result = url.path.lowercased() == "/success" ? "success" : "failure"
            Task {
                do { _ = try await V3ServiceBridge.shared.request(operation: "backupResult", target: result) }
                catch { status.error = error.localizedDescription }
            }
            return
        }
        if url.scheme?.lowercased() == "sidestore", url.host?.lowercased() == "install" {
            if let target = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name.lowercased() == "url" })?.value {
                status.perform("installURL", target: target, title: "Install app")
            }
            return
        }
        if url.scheme?.lowercased() == "sidestore", url.host?.lowercased() == "enable-jit" {
            let bundle = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "bundle-id" })?.value
            Task {
                do {
                    status.accept(try await V3ServiceBridge.shared.request(operation: "snapshot"))
                    guard let app = status.installedApps.first(where: {
                        $0.bundleID == bundle || ($0.isHost && bundle == Bundle.main.bundleIdentifier)
                    }) else { status.error = "This app is not in SideStore's library."; return }
                    status.perform("jit", target: app.identifier, title: "Enable JIT for " + app.name)
                } catch { status.error = error.localizedDescription }
            }
            return
        }
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
            case "refresh": status.refreshPresented = true
            default: return
            }
        }
        sharedModel.deepLink = url
    }
}

struct V3IPADocumentPicker: UIViewControllerRepresentable {
    let completion: (URL?) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(completion: completion) }
    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.data], asCopy: true)
        picker.allowsMultipleSelection = false
        picker.delegate = context.coordinator
        return picker
    }
    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}
    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        private var completion: ((URL?) -> Void)?
        init(completion: @escaping (URL?) -> Void) { self.completion = completion }
        private func finish(_ url: URL?) {
            let callback = completion
            completion = nil
            callback?(url)
        }
        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            finish(urls.first)
        }
        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { finish(nil) }
    }
}

struct V3PairingDocumentPicker: UIViewControllerRepresentable {
    let completion: (URL?) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(completion: completion) }
    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let types = ["mobiledevicepairing", "plist", "xml"].compactMap { UTType(filenameExtension: $0) }
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: types.isEmpty ? [.data] : types, asCopy: true)
        picker.allowsMultipleSelection = false
        picker.delegate = context.coordinator
        return picker
    }
    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}
    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        private var completion: ((URL?) -> Void)?
        init(completion: @escaping (URL?) -> Void) { self.completion = completion }
        private func finish(_ url: URL?) {
            let callback = completion
            completion = nil
            callback?(url)
        }
        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            finish(urls.first)
        }
        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { finish(nil) }
    }
}

struct V3RefreshAllButton: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @AppStorage("liveContainerAutoRefreshActiveRunID", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var activeRun = ""
    @AppStorage("liveContainerAutoRefreshHealthState", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var health = "UNKNOWN"
    var body: some View {
        Button {
            NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil)
        } label: {
            HStack {
                if !activeRun.isEmpty { ProgressView() }
                Text("Refresh All")
            }
        }
        .disabled(!activeRun.isEmpty || status.presentation != nil)
        .accessibilityValue(health.replacingOccurrences(of: "_", with: " ").lowercased())
        .onChange(of: health) { _ in status.reload(manual: false) }
        .onChange(of: activeRun) { value in if value.isEmpty { status.reload(manual: false) } }
    }
}

struct V3InstallButton: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    var body: some View {
        Button("Install / Sideload App") { status.installPickerPresented = true }
            .accessibilityHint("Choose an IPA to sign and install as an iOS app with SideStore")
            .disabled(status.presentation != nil || status.installPickerPresented)
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
    @Published private(set) var certificateExpiration: Date?
    @Published private(set) var pairing = "Unknown"
    @Published private(set) var updatedAt: Date?
    @Published private(set) var installedApps: [V3SideStoreApp] = []
    @Published private(set) var sources: [V3SideStoreSource] = []
    @Published private(set) var settings: [String: Bool] = [:]
    @Published var error: String?
    @Published var presentation: V3OperationRequest?
    @Published var sourceURL = ""
    @Published var refreshTarget: String?
    @Published var refreshPresented = false
    @Published var installPickerPresented = false
    @Published var pairingPickerPresented = false
    @Published var signInPresented = false
    @Published private(set) var loading = false
    @Published private(set) var connected = false
    @Published private(set) var requiresConnectionRetry = false
    var installedAppCount: Int { installedApps.count }
    var isStale: Bool { !connected || (updatedAt.map { Date().timeIntervalSince($0) > 120 } ?? true) }
    func reload(manual: Bool = true) {
        guard !loading, presentation == nil, manual || !requiresConnectionRetry else { return }
        if manual { requiresConnectionRetry = false }
        loading = true
        Task {
            defer { loading = false }
            do {
                accept(try await V3ServiceBridge.shared.request(operation: "snapshot"))
            } catch { connected = false; requiresConnectionRetry = true; self.error = error.localizedDescription }
        }
    }
    func accept(_ snapshot: [String: Any]) {
        account = snapshot["account"] as? String ?? "Not signed in"
        team = snapshot["team"] as? String ?? "No active team"
        signing = snapshot["signing"] as? String ?? "Unknown"
        certificate = snapshot["certificate"] as? String ?? "Unknown"
        certificateExpiration = (snapshot["certificateExpiration"] as? Date).flatMap { $0 == .distantPast ? nil : $0 }
        pairing = snapshot["pairing"] as? String ?? "Unknown"
        updatedAt = snapshot["updatedAt"] as? Date
        installedApps = (snapshot["installedApps"] as? [[String: Any]] ?? []).compactMap(V3SideStoreApp.init)
        sources = (snapshot["sources"] as? [[String: Any]] ?? []).compactMap(V3SideStoreSource.init)
        settings = snapshot["settings"] as? [String: Bool] ?? [:]
        connected = true
    }
    func perform(_ operation: String, target: String = "", title: String, value: Bool? = nil) {
        guard presentation == nil else { return }
        switch operation {
        case "signOut": signOut()
        case "syncAppIDs": syncAppIDs()
        case "clearCache": clearCache()
        case "refreshSources": refreshSources()
        default:
            presentation = V3OperationRequest(operation: operation, target: target, title: title, value: value)
        }
    }
    func signOut() {
        loading = true
        Task {
            defer { loading = false }
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "signOut")
                reload()
            } catch { self.error = error.localizedDescription }
        }
    }
    func syncAppIDs() {
        loading = true
        Task {
            defer { loading = false }
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "syncAppIDs")
                reload()
            } catch { self.error = error.localizedDescription }
        }
    }
    func clearCache() {
        loading = true
        Task {
            defer { loading = false }
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "clearCache")
            } catch { self.error = error.localizedDescription }
        }
    }
    func refreshSources() {
        loading = true
        Task {
            defer { loading = false }
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "refreshSources")
                reload()
            } catch { self.error = error.localizedDescription }
        }
    }
    func stagePairingFile(_ url: URL) {
        guard presentation == nil else { return }
        do {
            let allowedExtensions = Set(["mobiledevicepairing", "plist", "xml"])
            guard url.isFileURL, allowedExtensions.contains(url.pathExtension.lowercased()) else {
                throw NSError(domain: "V3PairingSelection", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "Choose a .mobiledevicepairing, .plist, or .xml pairing file."])
            }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let token = UUID().uuidString
            let bookmark = try url.bookmarkData(options: URL.BookmarkCreationOptions(rawValue: 1 << 11),
                                                includingResourceValuesForKeys: nil, relativeTo: nil)
            LCUtils.appGroupUserDefault.set(bookmark, forKey: "V3SharedPairing." + token)
            loading = true
            Task {
                defer { loading = false }
                do {
                    let snapshot = try await V3ServiceBridge.shared.request(operation: "importPairingSharedFile", target: token)
                    accept(snapshot)
                } catch {
                    LCUtils.appGroupUserDefault.removeObject(forKey: "V3SharedPairing." + token)
                    self.error = error.localizedDescription
                }
            }
        } catch { self.error = error.localizedDescription }
    }

    func stageSharedIPA(_ url: URL, bookmark: Data? = nil, title: String) {
        guard presentation == nil else { return }
        do {
            guard url.isFileURL, url.pathExtension.lowercased() == "ipa" else {
                throw NSError(domain: "V3IPASelection", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "Choose an IPA file to install with SideStore. Other files cannot be installed."])
            }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let token = UUID().uuidString
            let data = try bookmark ?? url.bookmarkData(options: URL.BookmarkCreationOptions(rawValue: 1 << 11),
                                                       includingResourceValuesForKeys: nil, relativeTo: nil)
            LCUtils.appGroupUserDefault.set(data, forKey: "V3SharedIPA." + token)
            perform("installSharedIPA", target: token, title: title)
        } catch { self.error = error.localizedDescription }
    }
}

struct V3SideStoreApp: Identifiable, Hashable {
    let identifier: String, bundleID: String, name: String, version: String, certificateStatus: String
    let isActive: Bool, hasUpdate: Bool, isHost: Bool
    let expirationDate: Date?
    let openURL: URL?
    var id: V3AppIdentity { .installed(uri: identifier) }
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
    var id: V3AppIdentity { .source(identifier: identifier) }
    init?(_ row: [String: Any]) {
        guard let identifier = row["identifier"] as? String, let name = row["name"] as? String,
              let url = row["url"] as? String, let appCount = row["appCount"] as? Int else { return nil }
        self.identifier = identifier; self.name = name; self.url = url; self.appCount = appCount
        subtitle = row["subtitle"] as? String ?? ""; canRemove = row["canRemove"] as? Bool ?? false
    }
}

struct V3InstalledAppsSection: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @AppStorage(LCGridSize.storageKey, store: LCUtils.appGroupUserDefault) private var gridSize: LCGridSize = .medium
    @ScaledMetric(relativeTo: .caption) private var textScale: CGFloat = 1
    @AppStorage("LCShowAppLabels", store: LCUtils.appGroupUserDefault) private var labels = true
    var query = ""
    private var apps: [V3SideStoreApp] { status.installedApps.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) || $0.bundleID.localizedCaseInsensitiveContains(query) } }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Sideloaded Apps").font(.headline)
                Spacer()
                Text("\(status.installedAppCount)")
                    .font(.caption.weight(.bold))
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(Color(UIColor.secondarySystemFill)))
            }
            if status.isStale {
                Button {
                    status.reload()
                } label: {
                    Label("Reconnect to SideStore", systemImage: "arrow.clockwise")
                        .font(.caption)
                }
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: gridSize.minimumWidth * min(1.5, max(1, textScale))), spacing: 16, alignment: .top)], spacing: 16) {
                ForEach(apps) { app in
                    NavigationLink(destination: V3SideStoreAppDetail(identifier: app.identifier)) {
                        VStack(spacing: 6) {
                            V3InstalledAppIcon(identifier: app.identifier, version: app.version, size: gridSize.iconSize)
                            if labels {
                                Text(app.name).lineLimit(2).font(.caption).foregroundColor(.primary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if !app.isActive {
                                Text("Inactive").font(.caption2).foregroundColor(.secondary)
                            } else if let expiration = app.expirationDate {
                                Text(expiration, style: .relative).font(.caption2).foregroundColor(.secondary)
                            }
                        }.frame(maxWidth: .infinity, minHeight: gridSize.iconSize + 8)
                            .padding(.vertical, 4)
                    }.accessibilityLabel(app.name).contextMenu { V3AppActions(app: app) }
                }
            }
            if apps.isEmpty {
                HStack {
                    Spacer()
                    Text(status.loading ? "Loading apps…" : "No sideloaded apps")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                    Spacer()
                }
                .padding(.vertical, 8)
            }
            Text("LiveContainer Guests").font(.headline).padding(.top)
        }.padding(.horizontal)
    }
}

struct V3InstalledAppIcon: View {
    let identifier: String
    let version: String
    let size: CGFloat
    @State private var image: UIImage?
    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() }
            else { Image(systemName: "app.fill").resizable().scaledToFit().foregroundColor(.secondary) }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.23))
        .accessibilityHidden(true)
        .task(id: identifier + version) {
            image = nil
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "appIcon", target: identifier)
                try Task.checkCancellation()
                if let data = reply["icon"] as? Data { image = UIImage(data: data) }
            } catch {}
        }
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
        Button("Refresh") { status.refreshTarget = app.isHost ? nil : app.identifier; status.refreshPresented = true }
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
                Section {
                    HStack(spacing: 16) {
                        Image(systemName: "app.fill")
                            .font(.system(size: 48))
                            .foregroundColor(.accentColor)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(app.name)
                                .font(.title3.weight(.bold))
                            Text(app.bundleID)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .textSelection(.enabled)
                            Text("Version " + app.version)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                }
                Section("Status") {
                    HStack {
                        Label("State", systemImage: "circle.fill")
                            .foregroundColor(app.isActive ? .green : .secondary)
                        Spacer()
                        Text(app.isActive ? "Active" : "Inactive")
                            .foregroundColor(.secondary)
                    }
                    HStack {
                        Label("Certificate", systemImage: "signature")
                        Spacer()
                        Text(app.certificateStatus.capitalized)
                            .foregroundColor(.secondary)
                    }
                    if let expiration = app.expirationDate {
                        HStack {
                            Label("Expires", systemImage: "calendar.badge.clock")
                            Spacer()
                            Text(expiration.formatted(date: .abbreviated, time: .shortened))
                                .foregroundColor(.secondary)
                        }
                    }
                }
                Section("Actions") { V3AppActions(app: app) }
            } else {
                Text("This app is no longer in the library.")
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(app?.name ?? "App")
    }
}

struct V3SourcesView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    private var savedGuestSources: [String] {
        (UserDefaults.standard.stringArray(forKey: "LCAltStoreSourceURLs") ?? [])
            .filter { saved in !status.sources.contains(where: { $0.url == saved }) }
    }
    var body: some View {
        NavigationView {
            List {
                Section("Add Source") {
                    HStack {
                        Image(systemName: "link")
                            .foregroundColor(.secondary)
                        TextField("https://example.com/source.json", text: $status.sourceURL)
                            .keyboardType(.URL)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                    }
                    Button {
                        status.perform("addSource", target: status.sourceURL, title: "Add Source")
                    } label: {
                        Label("Preview and Add Source", systemImage: "plus.circle.fill")
                    }
                    .disabled(status.sourceURL.isEmpty)
                }
                Section("Sources (\(status.sources.count))") {
                    ForEach(status.sources) { source in
                        NavigationLink(destination: V3CatalogView(source: source)) {
                            HStack(spacing: 12) {
                                Image(systemName: "folder.fill")
                                    .font(.title3)
                                    .foregroundColor(.accentColor)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(source.name)
                                        .font(.headline)
                                    Text("\(source.appCount) apps")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                        .contextMenu {
                            if source.canRemove {
                                Button(role: .destructive) {
                                    status.perform("removeSource", target: source.identifier, title: "Remove source")
                                } label: {
                                    Label("Remove Source", systemImage: "trash")
                                }
                            }
                        }
                    }
                }
                if !savedGuestSources.isEmpty {
                    Section("Previously Saved Guest Sources") {
                        ForEach(savedGuestSources, id: \.self) { url in
                            Button {
                                status.sourceURL = url
                            } label: {
                                HStack {
                                    Image(systemName: "bookmark")
                                        .foregroundColor(.secondary)
                                    Text(url)
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                            }
                        }
                        Text("Select a saved URL to preview and add it to the unified catalog. Existing saved URLs are preserved.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Sources")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        status.refreshSources()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
        }
        .navigationViewStyle(StackNavigationViewStyle())
    }
}

struct V3CatalogApp: Identifiable {
    let id: String, name: String, version: String, developer: String, description: String, installedID: String
    let canInstall: Bool
    let downloadURL: String
    init?(_ row: [String: Any]) {
        guard let id = row["identifier"] as? String, let name = row["name"] as? String else { return nil }
        self.id = id; self.name = name; version = row["version"] as? String ?? ""
        developer = row["developer"] as? String ?? ""; description = row["description"] as? String ?? ""
        installedID = row["installedID"] as? String ?? ""; canInstall = row["canInstall"] as? Bool ?? false
        downloadURL = row["downloadURL"] as? String ?? ""
    }
}

struct V3CatalogView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @EnvironmentObject private var sharedModel: SharedModel
    let source: V3SideStoreSource
    @State private var apps: [V3CatalogApp] = []
    @State private var query = ""
    @State private var loading = true
    @State private var error: String?
    var body: some View {
        List {
            if loading {
                HStack {
                    Spacer()
                    ProgressView("Loading catalog…")
                    Spacer()
                }
                .padding()
            }
            if let error {
                VStack(alignment: .leading, spacing: 8) {
                    Text(error).font(.caption).foregroundColor(.red)
                    Button("Retry") { Task { await load() } }
                }
            }
            ForEach(apps.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }) { app in
                NavigationLink {
                    List {
                        Section {
                            HStack(spacing: 16) {
                                Image(systemName: "app.fill")
                                    .font(.system(size: 48))
                                    .foregroundColor(.accentColor)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(app.name)
                                        .font(.title3.weight(.bold))
                                    Text(app.developer)
                                        .font(.subheadline)
                                        .foregroundColor(.secondary)
                                    Text("Version " + app.version)
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .padding(.vertical, 4)
                        }
                        
                        if !app.description.isEmpty {
                            Section("Description") {
                                Text(app.description)
                                    .font(.body)
                            }
                        }
                        
                        Section("Actions") {
                            if let installed = status.installedApps.first(where: { $0.identifier == app.installedID }) {
                                V3AppActions(app: installed)
                            } else {
                                Button {
                                    status.perform("install", target: app.id, title: "Install " + app.name)
                                } label: {
                                    Label("Install with SideStore", systemImage: "arrow.down.app.fill")
                                }
                                .disabled(!app.canInstall)
                            }
                            Button {
                                var link = URLComponents()
                                link.scheme = "livecontainer"
                                link.host = "install"
                                link.queryItems = [URLQueryItem(name: "url", value: app.downloadURL)]
                                sharedModel.deepLink = link.url
                                sharedModel.selectedTab = .apps
                            } label: {
                                Label("Install as LiveContainer Guest", systemImage: "square.stack.3d.up")
                            }
                            .disabled(!app.canInstall || app.downloadURL.isEmpty)
                        }
                    }
                    .listStyle(.insetGrouped)
                    .navigationTitle(app.name)
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "app.fill")
                            .font(.title2)
                            .foregroundColor(.accentColor)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(app.name)
                                .font(.headline)
                            Text(app.developer + (app.version.isEmpty ? "" : " · v" + app.version))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                        if status.installedApps.contains(where: { $0.identifier == app.installedID }) {
                            Text("Installed")
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.secondary)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Capsule().fill(Color(UIColor.secondarySystemFill)))
                        } else if app.canInstall {
                            Text("GET")
                                .font(.caption.weight(.bold))
                                .foregroundColor(.accentColor)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 4)
                                .background(Capsule().fill(Color.accentColor.opacity(0.15)))
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(source.name)
        .searchable(text: $query, prompt: "Search apps in " + source.name)
        .task { await load() }
    }
    private func load() async {
        loading = true
        error = nil
        defer { loading = false }
        do {
            var cursor = 0
            apps = []
            repeat {
                try Task.checkCancellation()
                let result = try await V3ServiceBridge.shared.request(operation: "catalog", target: source.identifier, cursor: cursor)
                let page = (result["apps"] as? [[String: Any]] ?? []).compactMap(V3CatalogApp.init)
                let existing = Set(apps.map(\.id))
                apps.append(contentsOf: page.filter { !existing.contains($0.id) })
                let next = result["nextCursor"] as? Int ?? -1
                guard next == -1 || next > cursor else { throw NSError(domain: "V3Catalog", code: 1) }
                cursor = next
            } while cursor >= 0
        } catch { self.error = error.localizedDescription }
    }
}

struct V3AccountSettings: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    var body: some View {
        Section("Account and Signing") {
            HStack {
                Label("Apple ID", systemImage: "person.crop.circle.fill")
                Spacer()
                Text(status.account)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            HStack {
                Label("Team", systemImage: "person.2.fill")
                Spacer()
                Text(status.team)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            HStack {
                Label("Signing", systemImage: "signature")
                Spacer()
                Text(status.signing)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            if let date = status.certificateExpiration {
                HStack {
                    Label("Certificate", systemImage: "doc.plaintext")
                    Spacer()
                    Text("Expires " + date.formatted(date: .abbreviated, time: .shortened))
                        .foregroundColor(.secondary)
                }
            }
            ForEach(status.installedApps.filter { $0.isHost }) { app in
                HStack {
                    Label("Host App", systemImage: "app.badge.fill")
                    Spacer()
                    Text(app.certificateStatus.capitalized + (app.expirationDate.map { " (exp " + $0.formatted(date: .abbreviated, time: .omitted) + ")" } ?? ""))
                        .foregroundColor(.secondary)
                }
            }
            Button {
                status.signInPresented = true
            } label: {
                Label("Sign In / Re-authenticate", systemImage: "person.badge.key.fill")
            }
            Button {
                status.syncAppIDs()
            } label: {
                Label("Sync App IDs", systemImage: "arrow.triangle.2.circlepath")
            }
            NavigationLink {
                V3CertificatesView()
            } label: {
                Label("Certificates", systemImage: "doc.text")
            }
            panel("Developer Services", "developerServices", icon: "wrench.and.screwdriver")
            Button(role: .destructive) {
                status.signOut()
            } label: {
                Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
            }
        }
        
        Section("SideStore") {
            HStack {
                Label("Pairing Status", systemImage: "link")
                Spacer()
                Text(status.pairing)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Button {
                status.pairingPickerPresented = true
            } label: {
                Label("Import Pairing File", systemImage: "doc.badge.plus")
            }
            panel("Connection", "connection", icon: "network")
            panel("Anisette Servers", "anisette", icon: "server.rack")
            panel("SideSign Configuration", "sideSign", icon: "pencil.and.outline")
            panel("Installation and Signing Options", "customizations", icon: "slider.horizontal.3")
            panel("Health Check", "health", icon: "heart.text.square")
            panel("SideStore Backups", "backups", icon: "archivebox")
            panel("SideJIT Server", "sideJIT", icon: "bolt.fill")
            setting("Beta updates", "betaUpdates", icon: "sparkles")
            setting("Disable idle timeout", "idleTimeoutDisabled", icon: "timer")
            panel("Update Channel", "releaseTrack", icon: "arrow.triangle.merge")
            panel("SideStore Diagnostics", "diagnostics", icon: "waveform.path.ecg")
            panel("Operation Logs", "logs", icon: "doc.text.magnifyingglass")
            panel("Experimental Features", "experimental", icon: "flask")
            Button {
                status.clearCache()
            } label: {
                Label("Clear Download Cache", systemImage: "trash")
            }
        }
        
        Section("Guest Runtime") {
            NavigationLink {
                LCTweaksView()
            } label: {
                Label("Tweaks", systemImage: "slider.vertical.3")
            }
        }
    }
    private func panel(_ title: String, _ key: String, icon: String) -> some View {
        Button {
            status.perform("panel", target: key, title: title)
        } label: {
            HStack {
                Label(title, systemImage: icon)
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }
    private func setting(_ title: String, _ key: String, icon: String) -> some View {
        Toggle(isOn: Binding(get: { status.settings[key] ?? false }, set: { status.perform("setSetting", target: key, title: title, value: $0) })) {
            Label(title, systemImage: icon)
        }
        .disabled(status.isStale)
    }
}


struct V3SignInView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @Environment(\.dismiss) private var dismiss

    @State private var flow: [String: Any] = [:]
    @State private var sessionID = ""
    @State private var appleID = ""
    @State private var password = ""
    @State private var verificationCode = ""
    @State private var selectedCertificates = Set<String>()
    @State private var selectedExtensions = Set<String>()
    @State private var customBundleID = ""
    @State private var appendTeamID = true
    @State private var localError: String?
    @State private var submitting = false
    @State private var lastChallengeID = ""

    private var phase: String { flow["phase"] as? String ?? "starting" }
    private var kind: String { flow["kind"] as? String ?? "" }
    private var message: String { flow["message"] as? String ?? "" }
    private var fields: [String: Any] { flow["fields"] as? [String: Any] ?? [:] }
    private var challengeID: String { flow["challengeID"] as? String ?? "" }
    private var terminal: Bool { ["completed", "failed", "cancelled"].contains(phase) }

    var body: some View {
        List {
            if let localError {
                Section {
                    Text(localError)
                        .foregroundColor(.red)
                        .textSelection(.enabled)
                }
            }

            switch phase {
            case "starting", "running":
                Section {
                    HStack(spacing: 12) {
                        ProgressView()
                        Text(message.isEmpty ? "Authenticating…" : message)
                            .foregroundColor(.secondary)
                    }
                }
            case "requiresInput":
                challengeContent
            case "completed":
                Section {
                    Label("Signed in successfully", systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                    if let result = flow["fields"] as? [String: Any],
                       let teamName = result["teamName"] as? String, !teamName.isEmpty {
                        HStack {
                            Text("Team")
                            Spacer()
                            Text(teamName).foregroundColor(.secondary)
                        }
                    }
                    Button("Done") {
                        status.reload()
                        dismiss()
                    }
                }
            case "failed":
                Section {
                    Label("Sign in failed", systemImage: "xmark.octagon.fill")
                        .foregroundColor(.red)
                    Text(message)
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                    Button("Try Again") {
                        Task { await begin() }
                    }
                }
            case "cancelled":
                Section {
                    Text("Sign in was cancelled.")
                        .foregroundColor(.secondary)
                    Button("Done") { dismiss() }
                }
            default:
                Section {
                    Text("Unknown authentication state.")
                        .foregroundColor(.secondary)
                    Button("Cancel") {
                        Task { await cancelFlow() }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Sign In")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button(terminal ? "Done" : "Cancel") {
                    if terminal { dismiss() }
                    else { Task { await cancelFlow() } }
                }
            }
        }
        .task {
            await begin()
            await monitor()
        }
        .onDisappear {
            guard !terminal, !sessionID.isEmpty else { return }
            let id = sessionID
            Task {
                _ = try? await V3ServiceBridge.shared.request(operation: "cancelSignIn", target: id)
            }
        }
    }

    @ViewBuilder
    private var challengeContent: some View {
        let currentFields = fields
        switch kind {
        case "credentials":
            Section("Apple ID") {
                if let error = currentFields["error"] as? String, !error.isEmpty {
                    Text(error).foregroundColor(.red)
                }
                TextField("Apple ID", text: $appleID)
                    .keyboardType(.emailAddress)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                SecureField("Password", text: $password)
                Button("Continue") {
                    Task {
                        await respond([
                            "action": "submitCredentials",
                            "appleID": appleID,
                            "password": password
                        ])
                        password = ""
                    }
                }
                .disabled(submitting || appleID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || password.isEmpty)
            }

        case "verificationMethod":
            Section("Verification Method") {
                Text(message).foregroundColor(.secondary)
                Button("Apple Devices") {
                    Task { await respond(["action": "trustedDevice"]) }
                }
                verificationPhoneButtons(fields: currentFields)
            }

        case "verificationCode":
            Section("Verification Code") {
                if let error = currentFields["error"] as? String, !error.isEmpty {
                    Text(error).foregroundColor(.red)
                }
                Text(message).foregroundColor(.secondary)
                TextField("6-digit code", text: $verificationCode)
                    .keyboardType(.numberPad)
                Button("Continue") {
                    let code = verificationCode
                    verificationCode = ""
                    Task { await respond(["action": "submitCode", "code": code]) }
                }
                .disabled(submitting || verificationCode.count != 6)
            }
            Section("Other Options") {
                if (currentFields["mode"] as? String) == "trustedDevice" {
                    Button("Request on Apple Devices Again") {
                        Task { await respond(["action": "trustedDevice"]) }
                    }
                }
                verificationPhoneButtons(fields: currentFields)
            }

        case "teamSelection":
            Section("Developer Team") {
                Text(message).foregroundColor(.secondary)
                let teams = currentFields["teams"] as? [[String: Any]] ?? []
                ForEach(Array(teams.enumerated()), id: \.offset) { _, team in
                    let teamID = team["id"] as? String ?? ""
                    let teamName = team["name"] as? String ?? teamID
                    let teamType = team["type"] as? String ?? ""
                    Button {
                        Task { await respond(["action": "selectTeam", "teamID": teamID]) }
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(teamName)
                            if !teamType.isEmpty {
                                Text(teamType).font(.caption).foregroundColor(.secondary)
                            }
                        }
                    }
                }
            }

        case "revocationDecision":
            Section("Existing Certificates") {
                Text(message).foregroundColor(.secondary)
                let certificates = currentFields["certificates"] as? [[String: Any]] ?? []
                ForEach(Array(certificates.enumerated()), id: \.offset) { _, certificate in
                    let serial = certificate["serial"] as? String ?? ""
                    let name = certificate["machineName"] as? String
                        ?? certificate["name"] as? String
                        ?? serial
                    Toggle(isOn: selectionBinding(serial, in: $selectedCertificates)) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(name)
                            Text(serial).font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                        }
                    }
                }
                Button("Revoke Selected", role: .destructive) {
                    Task {
                        await respond([
                            "action": "revokeSelected",
                            "serials": Array(selectedCertificates)
                        ])
                    }
                }
                .disabled(submitting || selectedCertificates.isEmpty)
                Button("Keep Existing Certificates") {
                    Task { await respond(["action": "keepExisting"]) }
                }
            }

        case "accountRepair":
            Section("Account Repair") {
                Text(message).foregroundColor(.secondary)
                if let value = currentFields["developerURL"] as? String, let url = URL(string: value) {
                    Button("Open Developer Account") {
                        UIApplication.shared.open(url)
                        Task { await respond(["action": "cancel"]) }
                    }
                }
                if let value = currentFields["appleAccountURL"] as? String, let url = URL(string: value) {
                    Button("Open Apple Account") {
                        UIApplication.shared.open(url)
                        Task { await respond(["action": "cancel"]) }
                    }
                }
                Button("Skip & Continue") {
                    Task { await respond(["action": "proceed"]) }
                }
            }

        case "provisioningDecision":
            Section("Developer Portal") {
                Text(message).foregroundColor(.secondary).textSelection(.enabled)
                Button("Retry") { Task { await respond(["action": "retry"]) } }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "postAuth":
            Section {
                Text(message).foregroundColor(.secondary)
                Button("Continue") { Task { await respond(["action": "continue"]) } }
            }

        case "resignDecision":
            Section("SideStore Signature") {
                Text(message).foregroundColor(.secondary)
                if let reason = currentFields["reason"] as? String, !reason.isEmpty {
                    HStack {
                        Text("Reason")
                        Spacer()
                        Text(reason).foregroundColor(.secondary)
                    }
                }
                Button("Resign Now") { Task { await respond(["action": "resignNow"]) } }
                Button("Resign Later") { Task { await respond(["action": "later"]) } }
            }

        case "anisetteWarning":
            Section {
                Text(message).foregroundColor(.secondary)
                Button("Continue") { Task { await respond(["action": "continue"]) } }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "bundleIDMismatch":
            Section {
                Text(message).foregroundColor(.secondary)
                detailRow("Requested", currentFields["targetID"] as? String ?? "")
                detailRow("Active", currentFields["activeEffectiveID"] as? String ?? "")
                Button("Proceed") { Task { await respond(["action": "proceed"]) } }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "permissionsReview":
            Section("Permissions") {
                Text(message).foregroundColor(.secondary)
                let permissions = currentFields["permissions"] as? [String] ?? []
                if permissions.isEmpty {
                    Text("No additional permissions reported.").foregroundColor(.secondary)
                } else {
                    ForEach(permissions, id: \.self) { Text($0).font(.footnote) }
                }
                Button("Continue") { Task { await respond(["action": "continue"]) } }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "extensionRemoval":
            Section("App Extensions") {
                Text(message).foregroundColor(.secondary)
                let extensions = currentFields["extensions"] as? [[String: Any]] ?? []
                ForEach(Array(extensions.enumerated()), id: \.offset) { _, item in
                    let bundleID = item["bundleID"] as? String ?? ""
                    let name = item["name"] as? String ?? bundleID
                    Toggle(isOn: selectionBinding(bundleID, in: $selectedExtensions)) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(name)
                            Text(bundleID).font(.caption2).foregroundColor(.secondary)
                        }
                    }
                }
                if !selectedExtensions.isEmpty {
                    Button("Remove Selected", role: .destructive) {
                        Task {
                            await respond([
                                "action": "removeSelected",
                                "bundleIDs": Array(selectedExtensions)
                            ])
                        }
                    }
                }
                Button("Remove All Extensions", role: .destructive) {
                    Task { await respond(["action": "removeAll"]) }
                }
                Button("Keep Extensions — Main Profile") {
                    Task { await respond(["action": "keepMainProfile"]) }
                }
                Button("Keep Extensions — Separate App IDs") {
                    Task { await respond(["action": "keepSeparateProfiles"]) }
                }
            }

        case "unsupportedVersion":
            Section {
                Text(message).foregroundColor(.secondary)
                let appName = currentFields["appName"] as? String ?? "App"
                let version = currentFields["compatibleVersion"] as? String ?? ""
                Button("Install \(appName) \(version)") {
                    Task { await respond(["action": "useCompatible"]) }
                }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "backgroundSuspension":
            Section {
                Text(message).foregroundColor(.secondary)
                Button("Continue") { Task { await respond(["action": "continue"]) } }
            }

        case "bundleIDCustomization":
            Section("App ID") {
                Text(message).foregroundColor(.secondary)
                TextField("Bundle Identifier", text: $customBundleID)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                Toggle("Append Team ID", isOn: $appendTeamID)
                Button("Confirm") {
                    Task {
                        await respond([
                            "action": "confirm",
                            "bundleID": customBundleID,
                            "appendTeamID": appendTeamID
                        ])
                    }
                }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "appGroupMismatch":
            Section("App Group") {
                Text(message).foregroundColor(.secondary)
                detailRow("Original", currentFields["originalGroup"] as? String ?? "")
                detailRow("Corrected", currentFields["correctedGroup"] as? String ?? "")
                Button("Correct & Proceed") { Task { await respond(["action": "correct"]) } }
                Button("Keep Original") { Task { await respond(["action": "keep"]) } }
            }

        default:
            Section {
                Text(message.isEmpty ? "SideStore requested an unsupported interaction." : message)
                    .foregroundColor(.secondary)
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }
        }
    }

    @ViewBuilder
    private func verificationPhoneButtons(fields: [String: Any]) -> some View {
        let phones = fields["phoneNumbers"] as? [[String: Any]] ?? []
        let activeID = fields["activeID"] as? String
        ForEach(Array(phones.enumerated()), id: \.offset) { _, phone in
            let phoneID = phone["id"] as? String ?? activeID ?? ""
            let number = phone["number"] as? String ?? "Phone"
            if !phoneID.isEmpty {
                Button("Text \(number)") {
                    Task { await respond(["action": "sms", "phoneID": phoneID]) }
                }
                Button("Call \(number)") {
                    Task { await respond(["action": "voice", "phoneID": phoneID]) }
                }
            }
        }
    }

    private func detailRow(_ title: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(title)
            Spacer()
            Text(value)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
    }

    private func selectionBinding(_ value: String, in selection: Binding<Set<String>>) -> Binding<Bool> {
        Binding(
            get: { selection.wrappedValue.contains(value) },
            set: { selected in
                if selected { selection.wrappedValue.insert(value) }
                else { selection.wrappedValue.remove(value) }
            }
        )
    }

    @MainActor
    private func begin() async {
        guard !submitting else { return }
        submitting = true
        localError = nil
        defer { submitting = false }
        do {
            apply(try await V3ServiceBridge.shared.request(operation: "beginSignIn"))
        } catch {
            localError = error.localizedDescription
        }
    }

    @MainActor
    private func monitor() async {
        while !Task.isCancelled {
            if terminal { return }
            do { try await Task.sleep(nanoseconds: 300_000_000) }
            catch { return }
            guard !sessionID.isEmpty, phase != "requiresInput" else { continue }
            do {
                apply(try await V3ServiceBridge.shared.request(operation: "signInState", target: sessionID))
            } catch is CancellationError {
                return
            } catch {
                localError = error.localizedDescription
            }
        }
    }

    @MainActor
    private func respond(_ values: [String: Any]) async {
        guard !submitting, !sessionID.isEmpty, !challengeID.isEmpty else { return }
        submitting = true
        localError = nil
        defer { submitting = false }
        var payload = values
        payload["challengeID"] = challengeID
        do {
            apply(try await V3ServiceBridge.shared.request(
                operation: "signInRespond",
                target: sessionID,
                payload: payload
            ))
        } catch {
            localError = error.localizedDescription
        }
    }

    @MainActor
    private func cancelFlow() async {
        guard !sessionID.isEmpty else { dismiss(); return }
        do {
            apply(try await V3ServiceBridge.shared.request(operation: "cancelSignIn", target: sessionID))
        } catch {
            localError = error.localizedDescription
        }
        dismiss()
    }

    @MainActor
    private func apply(_ value: [String: Any]) {
        flow = value
        sessionID = value["sessionID"] as? String ?? sessionID

        let nextChallenge = value["challengeID"] as? String ?? ""
        if !nextChallenge.isEmpty, nextChallenge != lastChallengeID {
            lastChallengeID = nextChallenge
            verificationCode = ""
            selectedCertificates.removeAll()
            selectedExtensions.removeAll()

            let nextFields = value["fields"] as? [String: Any] ?? [:]
            if (value["kind"] as? String) == "credentials",
               let suggestedAppleID = nextFields["appleID"] as? String,
               appleID.isEmpty {
                appleID = suggestedAppleID
            }
            if (value["kind"] as? String) == "bundleIDCustomization" {
                customBundleID = nextFields["bundleID"] as? String ?? ""
                appendTeamID = nextFields["appendTeamID"] as? Bool ?? true
            }
        }

        if (value["phase"] as? String) == "completed" {
            password = ""
            verificationCode = ""
            status.reload()
        }
    }
}

struct V3CertificateRecord: Identifiable, Hashable {
    let serialNumber: String
    let name: String
    let expirationDate: Date
    let active: Bool
    var id: String { serialNumber }
    init?(_ row: [String: Any]) {
        guard let serialNumber = row["serialNumber"] as? String,
              let name = row["name"] as? String,
              let expirationDate = row["expirationDate"] as? Date,
              let active = row["active"] as? Bool else { return nil }
        self.serialNumber = serialNumber
        self.name = name
        self.expirationDate = expirationDate
        self.active = active
    }
}

struct V3CertificatesView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var certificates: [V3CertificateRecord] = []
    @State private var loading = false
    @State private var error: String?
    @State private var deleteCandidate: V3CertificateRecord?

    var body: some View {
        List {
            if loading && certificates.isEmpty {
                HStack { Spacer(); ProgressView(); Spacer() }
            } else if certificates.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "doc.badge.ellipsis")
                        .font(.title2)
                        .foregroundColor(.secondary)
                    Text("No Local Certificates")
                        .font(.headline)
                    Text("No locally cached signing certificates are available.")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 24)
            } else {
                ForEach(certificates) { certificate in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(certificate.name).font(.headline)
                            Spacer()
                            if certificate.active {
                                Label("Active", systemImage: "checkmark.circle.fill")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        Text(certificate.serialNumber)
                            .font(.caption.monospaced())
                            .foregroundColor(.secondary)
                            .textSelection(.enabled)
                        Text("Expires " + certificate.expirationDate.formatted(date: .abbreviated, time: .shortened))
                            .font(.caption)
                            .foregroundColor(.secondary)
                        HStack {
                            if !certificate.active {
                                Button("Use for Signing") {
                                    Task { await mutate("activateLocalCertificate", target: certificate.serialNumber) }
                                }
                                .buttonStyle(.bordered)
                            }
                            Button("Delete", role: .destructive) { deleteCandidate = certificate }
                                .buttonStyle(.bordered)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .navigationTitle("Certificates")
        .overlay(alignment: .bottom) {
            if let error {
                Text(error)
                    .font(.footnote)
                    .padding(10)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
                    .padding()
                    .onTapGesture { self.error = nil }
            }
        }
        .confirmationDialog("Delete certificate?", isPresented: Binding(
            get: { deleteCandidate != nil },
            set: { if !$0 { deleteCandidate = nil } }
        ), titleVisibility: .visible) {
            if let certificate = deleteCandidate {
                Button("Delete " + certificate.name, role: .destructive) {
                    deleteCandidate = nil
                    Task { await mutate("deleteLocalCertificate", target: certificate.serialNumber) }
                }
            }
            Button("Cancel", role: .cancel) { deleteCandidate = nil }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let result = try await V3ServiceBridge.shared.request(operation: "certificatesSnapshot")
            certificates = (result["certificates"] as? [[String: Any]] ?? []).compactMap(V3CertificateRecord.init)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func mutate(_ operation: String, target: String) async {
        loading = true
        defer { loading = false }
        do {
            let snapshot = try await V3ServiceBridge.shared.request(operation: operation, target: target)
            status.accept(snapshot)
            let result = try await V3ServiceBridge.shared.request(operation: "certificatesSnapshot")
            certificates = (result["certificates"] as? [[String: Any]] ?? []).compactMap(V3CertificateRecord.init)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct V3TargetedRefreshSection: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    var body: some View {
        if let target = status.refreshTarget, let app = status.installedApps.first(where: { $0.identifier == target }) {
            Section("Selected App") {
                HStack {
                    Label(app.name, systemImage: "app.fill")
                    Spacer()
                    if let date = app.expirationDate {
                        Text("Expires " + date.formatted(date: .abbreviated, time: .shortened))
                            .foregroundColor(.secondary)
                    }
                }
                Button {
                    status.perform("refreshApp", target: target, title: "Refresh " + app.name)
                } label: {
                    Label("Refresh " + app.name, systemImage: "arrow.clockwise")
                }
                Button("Clear Selection") { status.refreshTarget = nil }
            }
        }
    }
}

struct V3OperationSheet: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @Environment(\.dismiss) private var dismiss
    let request: V3OperationRequest

    @State private var flow: [String: Any] = [:]
    @State private var sessionID = ""
    @State private var localError: String?
    @State private var submitting = false
    @State private var selectedExtensions = Set<String>()
    @State private var customBundleID = ""
    @State private var appendTeamID = true
    @State private var lastChallengeID = ""

    private var phase: String { flow["phase"] as? String ?? "starting" }
    private var kind: String { flow["kind"] as? String ?? "" }
    private var message: String { flow["message"] as? String ?? "" }
    private var fields: [String: Any] { flow["fields"] as? [String: Any] ?? [:] }
    private var challengeID: String { flow["challengeID"] as? String ?? "" }
    private var terminal: Bool { ["completed", "failed", "cancelled"].contains(phase) }

    var body: some View {
        NavigationView {
            List {
                if let localError {
                    Section {
                        Text(localError)
                            .foregroundColor(.red)
                            .textSelection(.enabled)
                    }
                }

                switch phase {
                case "starting", "running":
                    Section {
                        if let progress = flow["progress"] as? Double {
                            ProgressView(value: progress)
                            Text("\(Int(progress * 100))%")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        } else {
                            ProgressView()
                        }
                        Text(message.isEmpty ? "Working…" : message)
                            .foregroundColor(.secondary)
                    }

                case "requiresInput":
                    challengeContent

                case "completed":
                    Section {
                        Label("Completed", systemImage: "checkmark.circle.fill")
                            .foregroundColor(.green)
                        Button("Done") {
                            finish(result: "completed", detail: "SideStore completed the operation.")
                        }
                    }

                case "failed":
                    Section {
                        Label("Operation Failed", systemImage: "xmark.octagon.fill")
                            .foregroundColor(.red)
                        Text(message)
                            .foregroundColor(.secondary)
                            .textSelection(.enabled)
                        if request.operation != "installSharedIPA" {
                            Button("Try Again") { Task { await begin() } }
                        }
                        Button("Done") {
                            finish(result: "failed", detail: message)
                        }
                    }

                case "cancelled":
                    Section {
                        Text("Operation cancelled.")
                            .foregroundColor(.secondary)
                        Button("Done") {
                            finish(result: "cancelled", detail: "The operation was cancelled.")
                        }
                    }

                default:
                    Section {
                        Text("Unknown operation state.")
                            .foregroundColor(.secondary)
                        Button("Cancel", role: .destructive) {
                            Task { await cancelFlow() }
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(request.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(terminal ? "Done" : "Cancel") {
                        if terminal {
                            finish(result: phase, detail: message)
                        } else {
                            Task { await cancelFlow() }
                        }
                    }
                }
            }
        }
        .navigationViewStyle(StackNavigationViewStyle())
        .task {
            await begin()
            await monitor()
        }
        .onDisappear {
            if !terminal, !sessionID.isEmpty {
                let id = sessionID
                Task {
                    _ = try? await V3ServiceBridge.shared.request(operation: "cancelOperation", target: id)
                }
            }
            if request.operation == "installSharedIPA" {
                LCUtils.appGroupUserDefault.removeObject(forKey: "V3SharedIPA." + request.target)
            }
            status.reload()
        }
    }

    @ViewBuilder
    private var challengeContent: some View {
        let currentFields = fields
        switch kind {
        case "sourceAddConfirmation":
            Section("Source") {
                detailRow("Name", currentFields["name"] as? String ?? "")
                detailRow("URL", currentFields["url"] as? String ?? "")
                if !message.isEmpty { Text(message).foregroundColor(.secondary) }
                Button("Add Source") {
                    Task { await respond(["action": "confirm"]) }
                }
                Button("Cancel", role: .destructive) {
                    Task { await cancelFlow() }
                }
            }

        case "sourceRemoveConfirmation":
            Section("Source") {
                detailRow("Name", currentFields["name"] as? String ?? "")
                Text(message).foregroundColor(.secondary)
                Button("Remove Source", role: .destructive) {
                    Task { await respond(["action": "confirm"]) }
                }
                Button("Cancel") { Task { await cancelFlow() } }
            }

        case "bundleIDMismatch":
            Section {
                Text(message).foregroundColor(.secondary)
                detailRow("Requested", currentFields["targetID"] as? String ?? "")
                detailRow("Active", currentFields["activeEffectiveID"] as? String ?? "")
                Button("Proceed") { Task { await respond(["action": "proceed"]) } }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "permissionsReview":
            Section("Permissions") {
                Text(message).foregroundColor(.secondary)
                let permissions = currentFields["permissions"] as? [String] ?? []
                if permissions.isEmpty {
                    Text("No additional permissions reported.")
                        .foregroundColor(.secondary)
                } else {
                    ForEach(permissions, id: \.self) {
                        Text($0).font(.footnote)
                    }
                }
                Button("Continue") { Task { await respond(["action": "continue"]) } }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "extensionRemoval":
            Section("App Extensions") {
                Text(message).foregroundColor(.secondary)
                let extensions = currentFields["extensions"] as? [[String: Any]] ?? []
                ForEach(Array(extensions.enumerated()), id: \.offset) { _, item in
                    let bundleID = item["bundleID"] as? String ?? ""
                    let name = item["name"] as? String ?? bundleID
                    Toggle(isOn: selectionBinding(bundleID)) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(name)
                            Text(bundleID).font(.caption2).foregroundColor(.secondary)
                        }
                    }
                }
                if !selectedExtensions.isEmpty {
                    Button("Remove Selected", role: .destructive) {
                        Task {
                            await respond([
                                "action": "removeSelected",
                                "bundleIDs": Array(selectedExtensions)
                            ])
                        }
                    }
                }
                Button("Remove All Extensions", role: .destructive) {
                    Task { await respond(["action": "removeAll"]) }
                }
                Button("Keep Extensions — Main Profile") {
                    Task { await respond(["action": "keepMainProfile"]) }
                }
                Button("Keep Extensions — Separate App IDs") {
                    Task { await respond(["action": "keepSeparateProfiles"]) }
                }
            }

        case "unsupportedVersion":
            Section {
                Text(message).foregroundColor(.secondary)
                let appName = currentFields["appName"] as? String ?? "App"
                let version = currentFields["compatibleVersion"] as? String ?? ""
                Button("Install \(appName) \(version)") {
                    Task { await respond(["action": "useCompatible"]) }
                }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "backgroundSuspension":
            Section {
                Text(message).foregroundColor(.secondary)
                Button("Continue") { Task { await respond(["action": "continue"]) } }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "bundleIDCustomization":
            Section("App ID") {
                Text(message).foregroundColor(.secondary)
                TextField("Bundle Identifier", text: $customBundleID)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                Toggle("Append Team ID", isOn: $appendTeamID)
                Button("Confirm") {
                    Task {
                        await respond([
                            "action": "confirm",
                            "bundleID": customBundleID,
                            "appendTeamID": appendTeamID
                        ])
                    }
                }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        case "appGroupMismatch":
            Section("App Group") {
                Text(message).foregroundColor(.secondary)
                detailRow("Original", currentFields["originalGroup"] as? String ?? "")
                detailRow("Corrected", currentFields["correctedGroup"] as? String ?? "")
                Button("Correct & Proceed") { Task { await respond(["action": "correct"]) } }
                Button("Keep Original") { Task { await respond(["action": "keep"]) } }
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }

        default:
            Section {
                Text(message.isEmpty ? "SideStore requested an unsupported interaction." : message)
                    .foregroundColor(.secondary)
                Button("Cancel", role: .destructive) { Task { await cancelFlow() } }
            }
        }
    }

    private func detailRow(_ title: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(title)
            Spacer()
            Text(value)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
    }

    private func selectionBinding(_ value: String) -> Binding<Bool> {
        Binding(
            get: { selectedExtensions.contains(value) },
            set: { enabled in
                if enabled { selectedExtensions.insert(value) }
                else { selectedExtensions.remove(value) }
            }
        )
    }

    @MainActor
    private func begin() async {
        guard !submitting else { return }
        submitting = true
        localError = nil
        defer { submitting = false }
        do {
            let result = try await V3ServiceBridge.shared.request(
                operation: request.operation,
                target: request.target,
                value: request.value
            )
            if result["sessionID"] as? String != nil {
                apply(result)
            } else {
                status.accept(result)
                recordRefresh("completed", "SideStore completed the selected app's refresh. Check its current expiration above.")
                dismiss()
            }
        } catch {
            localError = error.localizedDescription
            flow = ["phase": "failed", "message": error.localizedDescription]
            recordRefresh("failed", error.localizedDescription)
        }
    }

    @MainActor
    private func monitor() async {
        while !Task.isCancelled {
            if terminal { return }
            do { try await Task.sleep(nanoseconds: 300_000_000) }
            catch { return }
            guard !sessionID.isEmpty, phase != "requiresInput" else { continue }
            do {
                apply(try await V3ServiceBridge.shared.request(operation: "operationState", target: sessionID))
            } catch is CancellationError {
                return
            } catch {
                localError = error.localizedDescription
            }
        }
    }

    @MainActor
    private func respond(_ values: [String: Any]) async {
        guard !submitting, !sessionID.isEmpty, !challengeID.isEmpty else { return }
        submitting = true
        localError = nil
        defer { submitting = false }

        var payload = values
        payload["challengeID"] = challengeID
        do {
            apply(try await V3ServiceBridge.shared.request(
                operation: "operationRespond",
                target: sessionID,
                payload: payload
            ))
        } catch {
            localError = error.localizedDescription
        }
    }

    @MainActor
    private func cancelFlow() async {
        if !sessionID.isEmpty {
            do {
                apply(try await V3ServiceBridge.shared.request(operation: "cancelOperation", target: sessionID))
            } catch {
                localError = error.localizedDescription
            }
        }
        finish(result: "cancelled", detail: "The operation was cancelled.")
    }

    @MainActor
    private func apply(_ value: [String: Any]) {
        flow = value
        sessionID = value["sessionID"] as? String ?? sessionID

        let nextChallenge = value["challengeID"] as? String ?? ""
        if !nextChallenge.isEmpty, nextChallenge != lastChallengeID {
            lastChallengeID = nextChallenge
            selectedExtensions.removeAll()

            let nextFields = value["fields"] as? [String: Any] ?? [:]
            if (value["kind"] as? String) == "bundleIDCustomization" {
                customBundleID = nextFields["bundleID"] as? String ?? ""
                appendTeamID = nextFields["appendTeamID"] as? Bool ?? true
            }
        }

        if (value["phase"] as? String) == "completed" {
            status.reload()
            recordRefresh("completed", "SideStore completed the selected app's refresh. Check its current expiration above.")
        } else if (value["phase"] as? String) == "failed" {
            recordRefresh("failed", value["message"] as? String ?? "Operation failed.")
        }
    }

    private func finish(result: String, detail: String) {
        recordRefresh(result, detail)
        status.reload()
        dismiss()
    }

    private func recordRefresh(_ result: String, _ detail: String) {
        guard request.operation == "refreshApp" else { return }
        NotificationCenter.default.post(
            name: Notification.Name("V3TargetedRefreshResult"),
            object: nil,
            userInfo: ["result": result, "detail": detail]
        )
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
                Section {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack(spacing: 12) {
                            Image(systemName: "shippingbox.circle.fill")
                                .font(.system(size: 38))
                                .foregroundColor(.accentColor)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("LiveContainer + SideStore")
                                    .font(.headline)
                                HStack(spacing: 6) {
                                    Circle()
                                        .fill(status.connected ? Color.green : (status.loading ? Color.orange : Color.gray))
                                        .frame(width: 8, height: 8)
                                    Text(status.connected ? "Active & Connected" : (status.loading ? "Connecting…" : "Not Connected"))
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            Spacer()
                            Button {
                                status.reload()
                            } label: {
                                Image(systemName: "arrow.clockwise")
                                    .font(.system(size: 14, weight: .semibold))
                            }
                            .buttonStyle(.bordered)
                            .buttonBorderShape(.capsule)
                            .disabled(status.loading)
                        }
                        
                        Divider()
                        
                        HStack(spacing: 0) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(sharedModel.apps.count)")
                                    .font(.title2.weight(.bold))
                                Text("Guests")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            
                            Divider().frame(height: 28)
                            
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(status.installedAppCount)")
                                    .font(.title2.weight(.bold))
                                Text("Sideloaded")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.leading, 12)
                            
                            Divider().frame(height: 28)
                            
                            VStack(alignment: .leading, spacing: 2) {
                                if let date = status.installedApps.filter({ $0.isActive }).compactMap(\.expirationDate).min() {
                                    Text(date, style: .relative)
                                        .font(.callout.weight(.bold))
                                        .foregroundColor(Calendar.current.dateComponents([.day], from: Date(), to: date).day ?? 0 <= 2 ? .red : .orange)
                                    Text("Next Expiry")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                } else {
                                    Text("—")
                                        .font(.title2.weight(.bold))
                                    Text("Next Expiry")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.leading, 12)
                        }
                    }
                    .padding(.vertical, 4)
                }
                
                Section("Status & Identity") {
                    HStack {
                        Label("Apple ID", systemImage: "person.crop.circle")
                        Spacer()
                        Text(status.account)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                    HStack {
                        Label("Developer Team", systemImage: "person.2")
                        Spacer()
                        Text(status.team)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                    HStack {
                        Label("Signing Status", systemImage: "signature")
                        Spacer()
                        Text(status.signing)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                    HStack {
                        Label("Pairing Status", systemImage: "link")
                        Spacer()
                        Text(status.pairing)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                    if let date = status.certificateExpiration {
                        HStack {
                            Label("Certificate Expiry", systemImage: "calendar.badge.clock")
                            Spacer()
                            Text(date.formatted(date: .abbreviated, time: .shortened))
                                .foregroundColor(.secondary)
                        }
                    }
                }
                
                Section("Background Refresh") {
                    HStack {
                        Label("Daemon Health", systemImage: "bolt.badge.clock")
                        Spacer()
                        Text(refreshState.replacingOccurrences(of: "_", with: " ").capitalized)
                            .foregroundColor(.secondary)
                    }
                    if let date = defaults?.object(forKey: "liveContainerAutoRefreshLastSuccessfulRefresh") as? Date {
                        HStack {
                            Label("Last Verified Run", systemImage: "checkmark.circle")
                            Spacer()
                            Text(date.formatted(date: .abbreviated, time: .shortened))
                                .foregroundColor(.secondary)
                        }
                    }
                    if let date = defaults?.object(forKey: "liveContainerAutoRefreshTargetDeadline") as? Date {
                        HStack {
                            Label("Refresh Deadline", systemImage: "hourglass")
                            Spacer()
                            Text(date.formatted(date: .abbreviated, time: .shortened))
                                .foregroundColor(.secondary)
                        }
                    }
                    if let error = defaults?.string(forKey: "liveContainerAutoRefreshLastError"), !error.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Label("Last Refresh Warning", systemImage: "exclamationmark.triangle")
                                .foregroundColor(.red)
                                .font(.caption)
                            Text(error)
                                .font(.caption2)
                                .foregroundColor(.red)
                        }
                    }
                    Button {
                        status.refreshPresented = true
                    } label: {
                        Label("Open Refresh Manager", systemImage: "arrow.clockwise")
                    }
                }
                
                Section("Quick Actions") {
                    Button {
                        sharedModel.selectedTab = .apps
                    } label: {
                        HStack {
                            Label("Manage Installed Apps", systemImage: "square.stack.3d.up.fill")
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    Button {
                        sharedModel.selectedTab = .sources
                    } label: {
                        HStack {
                            Label("Browse App Sources", systemImage: "books.vertical.fill")
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    Button {
                        sharedModel.selectedTab = .settings
                    } label: {
                        HStack {
                            Label("SideStore & Account Settings", systemImage: "gearshape.fill")
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Home")
        }
        .navigationViewStyle(StackNavigationViewStyle())
    }
}
// V3_UNIFIED_SHELL_V1_END
