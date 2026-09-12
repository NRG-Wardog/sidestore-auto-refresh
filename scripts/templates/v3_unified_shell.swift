import SwiftUI

// V3_UNIFIED_SHELL_V1_BEGIN
// The host owns navigation. SideStore remains the owner of signing, account,
// source, installation, and refresh data accessed through its existing bridge.
struct V3UnifiedShell: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @StateObject private var sideStoreStatus = V3SideStoreStatusStore()
    @State private var selectedInitialTab = false
    private let contractMarker = "V3_UNIFIED_SHELL_V1"

    var body: some View {
        TabView(selection: $sharedModel.selectedTab) {
            V3HomeView(status: sideStoreStatus)
                .tabItem { Label("Home", systemImage: "house.fill") }
                .tag(LCTabIdentifier.home)

            LCAppListView()
                .tabItem { Label("Apps", systemImage: "square.stack.3d.up.fill") }
                .tag(LCTabIdentifier.apps)

            LCSourcesView()
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

    func reload() {
        let snapshot = defaults.dictionary(forKey: "v3SideStoreStatusSnapshot") ?? [:]
        account = snapshot["account"] as? String ?? "Not signed in"
        signing = snapshot["signing"] as? String ?? "Unknown"
        installedAppCount = snapshot["installedAppCount"] as? Int ?? 0
        updatedAt = snapshot["updatedAt"] as? Date
    }
}

private struct V3HomeView: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @ObservedObject var status: V3SideStoreStatusStore
    @AppStorage("liveContainerAutoRefreshHealthState", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var refreshState = "UNKNOWN"
    @AppStorage("liveContainerAutoRefreshLastDate", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var lastRefresh: Date?

    var body: some View {
        NavigationView {
            List {
                Section("Status") {
                    Label("\(sharedModel.apps.count) LiveContainer guests", systemImage: "rectangle.stack.fill")
                    Label("\(status.installedAppCount) sideloaded apps", systemImage: "app.badge")
                    LabeledContent("Signing", value: status.signing)
                    LabeledContent("Account", value: status.account)
                }
                Section("Refresh") {
                    LabeledContent("State", value: refreshState.replacingOccurrences(of: "_", with: " ").capitalized)
                    if let lastRefresh {
                        LabeledContent("Last verified run", value: lastRefresh.formatted(date: .abbreviated, time: .shortened))
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
        }
        .navigationViewStyle(StackNavigationViewStyle())
    }
}
// V3_UNIFIED_SHELL_V1_END
