import SwiftUI
import Combine

// V3_UNIFIED_SHELL_V1_BEGIN
// The host owns navigation. SideStore remains the owner of signing, account,
// source, installation, and refresh data accessed through its existing bridge.
struct V3UnifiedShell: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @StateObject private var sideStoreStatus = V3SideStoreStatusStore()
    @State private var selectedInitialTab = false
    private let monitor = Timer.publish(every: 30, on: .main, in: .common).autoconnect()
    private let contractMarker = "V3_UNIFIED_SHELL_V1"

    var body: some View {
        TabView(selection: $sharedModel.selectedTab) {
            V3HomeView(status: sideStoreStatus)
                .tabItem { Label("Home", systemImage: "house.fill") }
                .tag(LCTabIdentifier.home)

            V3AppsView(status: sideStoreStatus)
                .tabItem { Label("Apps", systemImage: "square.stack.3d.up.fill") }
                .tag(LCTabIdentifier.apps)

            V3SourcesView(status: sideStoreStatus)
                .tabItem { Label("Sources", systemImage: "books.vertical") }
                .tag(LCTabIdentifier.sources)

            LCEmbeddedSideStoreRefreshView()
                .tabItem { Label("Refresh", systemImage: "arrow.clockwise") }
                .tag(LCTabIdentifier.refresh)

            LCSettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
                .tag(LCTabIdentifier.settings)
        }
        .accessibilityIdentifier(contractMarker)
        .task {
            sideStoreStatus.reload()
            guard !selectedInitialTab else { return }
            selectedInitialTab = true
            if sharedModel.deepLink == nil { sharedModel.selectedTab = .home }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            sideStoreStatus.reload()
        }
        .onReceive(monitor) { _ in
            sideStoreStatus.reload()
        }
        .onOpenURL(perform: dispatchURL)
    }

    private func dispatchURL(_ url: URL) {
        if url.isFileURL || url.scheme?.lowercased() == "sidestore" {
            sharedModel.selectedTab = .apps
        } else {
            switch url.host?.lowercased() {
            case "livecontainer-launch", "install", "open-web-page", "open-url":
                sharedModel.selectedTab = .apps
            case "certificate":
                sharedModel.selectedTab = .settings
            case "source":
                sharedModel.selectedTab = .sources
            case "refresh":
                sharedModel.selectedTab = .refresh
            default:
                return
            }
        }
        sharedModel.deepLink = url
    }
}

@MainActor
final class V3SideStoreStatusStore: ObservableObject {
    private let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard
    @Published private(set) var account = "Not available"
    @Published private(set) var signing = "Unknown"
    @Published private(set) var installedAppCount = 0
    @Published private(set) var updatedAt: Date?
    @Published private(set) var installedApps: [V3SideStoreApp] = []
    @Published private(set) var sources: [V3SideStoreSource] = []
    @Published private(set) var isStale = true

    private let maximumSnapshotAge: TimeInterval = 120

    func reload() {
        let snapshot = defaults.dictionary(forKey: "v3SideStoreStatusSnapshot") ?? [:]
        account = snapshot["account"] as? String ?? "Not signed in"
        signing = snapshot["signing"] as? String ?? "Unknown"
        installedAppCount = snapshot["installedAppCount"] as? Int ?? 0
        updatedAt = snapshot["updatedAt"] as? Date
        installedApps = (snapshot["installedApps"] as? [[String: Any]] ?? []).compactMap(V3SideStoreApp.init)
        sources = (snapshot["sources"] as? [[String: Any]] ?? []).compactMap(V3SideStoreSource.init)
        let updatedAt = snapshot["updatedAt"] as? Date
        isStale = updatedAt.map { Date().timeIntervalSince($0) > maximumSnapshotAge } ?? true
    }
}

struct V3SideStoreApp: Identifiable, Hashable {
    let bundleID: String
    let name: String
    let version: String
    let isActive: Bool
    let expirationDate: Date?
    let hasUpdate: Bool
    let certificateStatus: String
    var id: String { "sidestore:" + bundleID }

    init?(_ values: [String: Any]) {
        guard let bundleID = values["bundleID"] as? String,
              let name = values["name"] as? String,
              let version = values["version"] as? String,
              let isActive = values["isActive"] as? Bool,
              let hasUpdate = values["hasUpdate"] as? Bool else { return nil }
        self.bundleID = bundleID
        self.name = name
        self.version = version
        self.isActive = isActive
        self.expirationDate = values["expirationDate"] as? Date
        self.hasUpdate = hasUpdate
        self.certificateStatus = values["certificateStatus"] as? String ?? "valid"
    }
}

struct V3SideStoreSource: Identifiable, Hashable {
    let identifier: String
    let name: String
    let subtitle: String
    let url: String
    let appCount: Int
    var id: String { "sidestore-source:" + identifier }

    init?(_ values: [String: Any]) {
        guard let identifier = values["identifier"] as? String,
              let name = values["name"] as? String,
              let url = values["url"] as? String,
              let appCount = values["appCount"] as? Int else { return nil }
        self.identifier = identifier
        self.name = name
        self.subtitle = values["subtitle"] as? String ?? ""
        self.url = url
        self.appCount = appCount
    }
}

private struct V3SourcesView: View {
    @ObservedObject var status: V3SideStoreStatusStore

    var body: some View {
        NavigationView {
            List {
                Section("SideStore Sources") {
                    if status.sources.isEmpty { Text("No SideStore sources are available yet.").foregroundColor(.secondary) }
                    ForEach(status.sources) { source in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(source.name)
                            if !source.subtitle.isEmpty { Text(source.subtitle).font(.caption).foregroundColor(.secondary) }
                            Text("\(source.appCount) apps").font(.caption).foregroundColor(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("Sources")
        }
        .navigationViewStyle(StackNavigationViewStyle())
    }
}

private struct V3AppsView: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @ObservedObject var status: V3SideStoreStatusStore

    var body: some View {
        NavigationView {
            List {
                Section("Sideloaded Apps") {
                    if status.installedApps.isEmpty {
                        Text("No sideloaded apps are available yet.").foregroundColor(.secondary)
                    }
                    ForEach(status.installedApps) { app in
                        NavigationLink(destination: V3SideStoreAppDetail(app: app)) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(app.name)
                                Text(app.version + " · " + (app.isActive ? "Active" : "Inactive"))
                                    .font(.caption).foregroundColor(.secondary)
                            }
                        }
                    }
                }
                Section("LiveContainer Guests") {
                    ForEach(sharedModel.apps, id: \.self) { guest in
                        Button { Task { try? await guest.runApp() } } label: {
                            Text(guest.appInfo.displayName())
                        }
                    }
                }
            }
            .navigationTitle("Apps")
        }
        .navigationViewStyle(StackNavigationViewStyle())
    }
}

private struct V3SideStoreAppDetail: View {
    let app: V3SideStoreApp

    var body: some View {
        List {
            Section("Status") {
                Text(app.isActive ? "Active" : "Inactive")
                Text("Certificate: \(app.certificateStatus.capitalized)")
                if let expirationDate = app.expirationDate { Text("Expires \(expirationDate.formatted(date: .abbreviated, time: .omitted))") }
                if app.hasUpdate { Text("Update available") }
            }
            Section("Version") { Text(app.version) }
        }
        .navigationTitle(app.name)
    }
}

private struct V3HomeView: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @ObservedObject var status: V3SideStoreStatusStore
    @AppStorage("liveContainerAutoRefreshHealthState", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var refreshState = "UNKNOWN"
    @State private var lastRefresh: Date?
    private let refreshDefaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard

    var body: some View {
        NavigationView {
            List {
                Section("Status") {
                    Label("\(sharedModel.apps.count) LiveContainer guests", systemImage: "rectangle.stack.fill")
                    Label("\(status.installedAppCount) sideloaded apps", systemImage: "app.badge")
                    v3StatusRow("Signing", status.signing)
                    v3StatusRow("Monitor", status.isStale ? "Waiting for current SideStore status" : "Current")
                    v3StatusRow("Account", status.account)
                }
                Section("Refresh") {
                    v3StatusRow("State", refreshState.replacingOccurrences(of: "_", with: " ").capitalized)
                    if let lastRefresh {
                        v3StatusRow("Last verified run", lastRefresh.formatted(date: .abbreviated, time: .shortened))
                    }
                    Button("Refresh now") { sharedModel.selectedTab = .refresh }
                }
                Section("Quick actions") {
                    Button("Open Apps") { sharedModel.selectedTab = .apps }
                    Button("Browse Sources") { sharedModel.selectedTab = .sources }
                    Button("Open Settings") { sharedModel.selectedTab = .settings }
                }
            }
            .navigationTitle("Home")
            .onAppear { lastRefresh = refreshDefaults.object(forKey: "liveContainerAutoRefreshLastDate") as? Date }
        }
        .navigationViewStyle(StackNavigationViewStyle())
    }

    private func v3StatusRow(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title)
            Spacer()
            Text(value).foregroundColor(.secondary).multilineTextAlignment(.trailing)
        }
    }
}
// V3_UNIFIED_SHELL_V1_END
