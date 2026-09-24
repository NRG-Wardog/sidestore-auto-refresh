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
    @State private var selectedInstallToken: String?
    @State private var showNotificationsPrompt = false
    private let monitor = Timer.publish(every: 30, on: .main, in: .common).autoconnect()
    var body: some View {
        TabView(selection: $sharedModel.selectedTab) {
            V3HomeView().tabItem { Label("Home", systemImage: "house.fill") }.tag(LCTabIdentifier.home)
            LCAppListView().tabItem { Label("Apps", systemImage: "square.stack.3d.up.fill") }.tag(LCTabIdentifier.apps)
            V3SourcesView().tabItem { Label("Sources", systemImage: "books.vertical") }.tag(LCTabIdentifier.sources)
            LCSettingsView().tabItem { Label("Settings", systemImage: "gearshape.fill") }.tag(LCTabIdentifier.settings)
        }
        .environmentObject(status)
        .environment(\.v3StatusStore, status)
        .accessibilityIdentifier("V3_UNIFIED_SHELL_V1")
        .task {
            status.reload(manual: false)
            routePendingSetup()
            if !UserDefaults.standard.bool(forKey: "V3NotificationsPromptShown") {
                UserDefaults.standard.set(true, forKey: "V3NotificationsPromptShown")
                showNotificationsPrompt = true
            }
            if let pending = UserDefaults.standard.string(forKey: "V3PendingSideStoreURL"), let url = URL(string: pending) {
                UserDefaults.standard.removeObject(forKey: "V3PendingSideStoreURL")
                if url.isFileURL {
                    status.stageSharedIPA(url, bookmark: LCUtils.appGroupUserDefault.data(forKey: "LCLaunchExtensionFileBookmark"), title: "Install shared app")
                    LCUtils.appGroupUserDefault.removeObject(forKey: "LCLaunchExtensionFileBookmark")
                } else { dispatchURL(url) }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            status.reload(manual: false)
            routePendingSetup()
        }
        .onReceive(monitor) { _ in status.reload(manual: false) }
        .onOpenURL(perform: dispatchURL)
        .sheet(isPresented: $status.installPickerPresented, onDismiss: {
            if let token = selectedInstallToken {
                selectedInstallToken = nil
                status.presentStagedIPA(token, title: "Install / Sideload App")
            }
        }) {
            V3IPADocumentPicker { url in
                if let url {
                    selectedInstallToken = status.stageSharedIPA(url, title: "Install / Sideload App",
                                                                 presentImmediately: false)
                }
                status.installPickerPresented = false
            }
        }
        .fullScreenCover(item: $status.presentation, onDismiss: operationSheetDidDismiss) {
            V3OperationSheet(request: $0).environmentObject(status)
        }
        .sheet(isPresented: $status.signInPresented, onDismiss: { status.reload() }) {
            NavigationView { V3SignInView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.setupPresented) {
            NavigationView { V3SetupAssistantView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.certificatesPresented) {
            NavigationView { V3CertificatesView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .alert("SideStore", isPresented: Binding(get: { status.error != nil }, set: { if !$0 { status.error = nil } })) {
            Button("Copy Diagnostics") { UIPasteboard.general.string = status.error }
            Button("Retry Connection") { status.reload() }
            Button("OK", role: .cancel) { status.error = nil }
        } message: { Text(status.error ?? "") }
        .alert("SideStore", isPresented: Binding(get: { status.notice != nil }, set: { if !$0 { status.notice = nil } })) {
            Button("OK", role: .cancel) { status.notice = nil }
        } message: { Text(status.notice ?? "") }
        .alert("Stay Informed About Refreshes", isPresented: $showNotificationsPrompt) {
            Button("Allow Notifications") {
                Task { await LiveContainerAutoRefreshScheduler.requestNotificationPermission() }
            }
            Button("Later", role: .cancel) {}
        } message: {
            Text("LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.")
        }
    }
    private func routePendingSetup() {
        guard LCUtils.appGroupUserDefault.bool(forKey: "V3PendingSetupAssistant") else { return }
        LCUtils.appGroupUserDefault.removeObject(forKey: "V3PendingSetupAssistant")
        NSLog("[V3_SETUP] OPEN source=shortcut")
        status.setupPresented = true
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
                do {
                    _ = try await V3ServiceBridge.shared.request(operation: "backupResult", target: result)
                    status.reload()
                }
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
            case "setup":
                NSLog("[V3_SETUP] OPEN source=deep-link")
                status.setupPresented = true
            case "refresh":
                sharedModel.selectedTab = .home
                status.refreshPresented = true
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

struct V3RefreshAllButton: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @AppStorage("liveContainerAutoRefreshActiveRunID", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var activeRun = ""
    @AppStorage("liveContainerAutoRefreshHealthState", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var health = "UNKNOWN"
    @State private var attempt = V3RefreshAllAttemptState()
    @State private var message = ""
    @State private var diagnostics = ""
    @State private var terminalFailure: V3OperationFailureDetails?
    @State private var copied = false
    @State private var monitor: Task<Void, Never>?
    private let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore")

    private var phase: String { attempt.phase.rawValue }
    private var requestID: String { attempt.requestID }
    private var runID: String { attempt.runID }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: start) {
                HStack(spacing: 8) {
                    if ["starting", "refreshing", "verifying"].contains(phase) { ProgressView() }
                    Text(buttonTitle)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .disabled(isBusy || isTerminal || !activeRun.isEmpty || status.presentation != nil || status.loading)
            .accessibilityValue(health.replacingOccurrences(of: "_", with: " ").lowercased())
            if phase == "completed" || phase == "failed" {
                VStack(alignment: .leading, spacing: 6) {
                    Text("What happened").font(.caption.weight(.semibold))
                    Text(message)
                        .font(.footnote)
                        .foregroundColor(phase == "completed" ? .green : .red)
                        .textSelection(.enabled)
                    if phase == "failed", let terminalFailure {
                        Text("What you can do").font(.caption.weight(.semibold)).padding(.top, 4)
                        Text(terminalFailure.recommendedAction).font(.footnote)
                        HStack {
                            if let destination = terminalFailure.recoveryDestination,
                               let action = terminalFailure.recoveryActionTitle {
                                Button(action) { openFailureRecovery(destination) }
                            }
                            if [.allowed, .unknown].contains(terminalFailure.retryDisposition) {
                                Button(terminalFailure.retryDisposition == .unknown
                                    ? "Retry (outcome unknown)" : "Retry") {
                                        retryFailedAttempt()
                                    }
                                    .disabled(!activeRun.isEmpty || status.presentation != nil || status.loading)
                            }
                        }
                    } else if phase == "failed", message == "Refresh did not start." {
                        Button("Start Again") { acknowledge(); start() }
                            .disabled(!activeRun.isEmpty || status.presentation != nil || status.loading)
                    }
                }
                HStack {
                    Button(copied ? "Copied" : "Copy Diagnostics") {
                        UIPasteboard.general.string = diagnostics
                        copied = true
                    }
                    .font(.caption)
                    Button("Dismiss") { acknowledge() }
                        .font(.caption)
                    Spacer(minLength: 0)
                }
            }
        }
        .onChange(of: health) { _ in
            status.reload(manual: false)
            if phase == "refreshing" || phase == "verifying" { inspectSchedulerState() }
        }
        .onChange(of: activeRun) { _ in
            if phase == "starting" || phase == "refreshing" || phase == "verifying" {
                inspectSchedulerState()
            }
            if activeRun.isEmpty { status.reload(manual: false) }
        }
        .accessibilityHint("Starts one manual refresh and shows scheduler state through verified completion or failure.")
    }
    private func operationSheetDidDismiss() {
        guard let destination = status.operationRecoveryDestination else { return }
        status.operationRecoveryDestination = nil
        switch destination {
        case "signIn": status.signInPresented = true
        case "certificates": status.certificatesPresented = true
        case "ipa": status.installPickerPresented = true
        case "setup": status.setupPresented = true
        default: break
        }
    }

    private var isBusy: Bool { ["starting", "refreshing", "verifying"].contains(phase) }
    private var isTerminal: Bool { ["completed", "failed"].contains(phase) }
    private var buttonTitle: String {
        switch phase {
        case "starting": return "Starting Refresh..."
        case "refreshing": return "Refreshing..."
        case "verifying": return "Verifying..."
        default: return "Refresh All"
        }
    }

    private func start() {
        guard attempt.phase == .idle, !isBusy, !isTerminal, activeRun.isEmpty,
              status.presentation == nil, !status.loading else { return }
        let newRequestID = UUID().uuidString
        attempt.begin(requestID: newRequestID)
        message = "Starting Refresh..."
        diagnostics = "manual_refresh_request=\(newRequestID)\nstate=starting"
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil,
                                        userInfo: ["requestID": newRequestID, "origin": "home"])
        monitor = Task { @MainActor in await monitorRun(requestID: newRequestID) }
    }

    private func monitorRun(requestID expectedRequest: String) async {
        let startDeadline = Date().addingTimeInterval(20)
        while !Task.isCancelled && Date() < startDeadline {
            if let record = runRecord(requestID: expectedRequest) {
                _ = attempt.observe(record, schedulerHealth: health, activeRunID: activeRun)
                renderAttempt(record)
                if attempt.isTerminal { return }
                if !attempt.runID.isEmpty { break }
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        guard !Task.isCancelled else { return }
        if attempt.runID.isEmpty {
            attempt.markDidNotStart()
            renderFailure(health: health)
            return
        }

        let finishDeadline = Date().addingTimeInterval(600)
        while !Task.isCancelled && Date() < finishDeadline {
            inspectSchedulerState()
            if attempt.isTerminal { return }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        if !Task.isCancelled && !attempt.isTerminal {
            attempt.markTimedOut()
            renderFailure(health: health)
        }
    }

    private func inspectSchedulerState() {
        guard !attempt.isTerminal, !runID.isEmpty,
              let record = runRecord(requestID: requestID, runID: runID) else { return }
        _ = attempt.observe(record, schedulerHealth: health, activeRunID: activeRun)
        renderAttempt(record)
    }

    private func runRecord(requestID: String, runID: String? = nil) -> [String: Any]? {
        guard let defaults,
              let ledger = defaults.dictionary(forKey: "liveContainerAutoRefreshRunLedger") else { return nil }
        return V3RefreshAllAttemptState.record(in: ledger, requestID: requestID, runID: runID)
    }

    private func renderAttempt(_ record: [String: Any]) {
        switch attempt.phase {
        case .starting:
            message = "Starting Refresh..."
        case .refreshing:
            message = "Refreshing..."
        case .verifying:
            message = "Verifying..."
        case .completed:
            let manifest = record["manifest"] as? [String: Any] ?? [:]
            let results = manifest["results"] as? [[String: Any]] ?? []
            let skipped = manifest["skipped_ids"] as? [String] ?? []
            message = attempt.terminalMessage
            terminalFailure = nil
            diagnostics = "manual_refresh_request=\(requestID)\nrun_id=\(runID)\nstate=completed\nverified_app_count=\(results.count)\nskipped_app_count=\(skipped.count)"
        case .failed:
            renderFailure(health: record["health"] as? String ?? health, record: record)
        case .idle:
            break
        }
    }

    private func renderFailure(health: String, record: [String: Any]? = nil) {
        guard !isTerminal || phase == "failed" else { return }
        message = attempt.terminalMessage.isEmpty ? "Refresh failed. Check Refresh History for details." : attempt.terminalMessage
        terminalFailure = record.flatMap { value in
            guard let wire = value["failure"] as? [String: Any],
                  let failure = CombinedFailure.decode(wire, expectedID: runID),
                  failure.operation == "refresh" else { return nil }
            return V3OperationFailureDetails(failure)
        }
        if let record, !runID.isEmpty,
           let currentRunDiagnostics = V3RefreshAllFailureDiagnostics.text(
               requestID: requestID, runID: runID, record: record) {
            diagnostics = currentRunDiagnostics
        } else {
            diagnostics = [
                "schema=1",
                "manual_refresh_request=\(requestID)",
                "run_id=\(runID.isEmpty ? "not_started" : runID)",
                "state=failed",
                "safe_message=\(message)",
                "health=\(health)"
            ].joined(separator: "\n")
        }
    }

    private func acknowledge() {
        monitor?.cancel()
        monitor = nil
        attempt.acknowledge()
        message = ""
        diagnostics = ""
        terminalFailure = nil
        copied = false
    }

    private func retryFailedAttempt() {
        guard phase == "failed", let terminalFailure,
              [.allowed, .unknown].contains(terminalFailure.retryDisposition),
              activeRun.isEmpty, status.presentation == nil, !status.loading else { return }
        acknowledge()
        start()
    }

    private func openFailureRecovery(_ destination: String) {
        switch destination {
        case "signIn": status.signInPresented = true
        case "certificates": status.certificatesPresented = true
        case "ipa": status.installPickerPresented = true
        case "setup": status.setupPresented = true
        default: break
        }
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
}

struct V3PromptAnswer {
    var fields: [String: String] = [:]
    var choice = ""
    var selected: Set<String> = []
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
    @Published var notice: String?
    @Published var presentation: V3OperationRequest? {
        didSet { if presentation == nil { drainDeferredReload() } }
    }
    @Published var sourceURL = ""
    @Published var refreshTarget: String?
    @Published var refreshPresented = false
    @Published var installPickerPresented = false
    @Published var signInPresented = false
    @Published var setupPresented = false
    @Published var certificatesPresented = false
    @Published var operationRecoveryDestination: String?
    @Published private(set) var loading = false
    @Published private(set) var connected = false
    @Published private(set) var requiresConnectionRetry = false
    private var deferredReloadManual: Bool?
    var installedAppCount: Int { installedApps.count }
    var isStale: Bool { !connected || (updatedAt.map { Date().timeIntervalSince($0) > 120 } ?? true) }
    var needsSignIn: Bool { account == "Not signed in" }
    func reload(manual: Bool = true) {
        if loading || presentation != nil {
            if manual || !requiresConnectionRetry {
                deferredReloadManual = (deferredReloadManual ?? false) || manual
            }
            return
        }
        guard manual || !requiresConnectionRetry else { return }
        if manual { requiresConnectionRetry = false }
        loading = true
        Task {
            defer { loading = false; drainDeferredReload() }
            do {
                accept(try await V3ServiceBridge.shared.request(operation: "snapshot"))
            } catch { connected = false; requiresConnectionRetry = true; self.error = error.localizedDescription }
        }
    }
    private func drainDeferredReload() {
        guard !loading, presentation == nil, let manual = deferredReloadManual else { return }
        deferredReloadManual = nil
        Task { @MainActor in self.reload(manual: manual) }
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
        // A second operation while one is presented must explain itself
        // instead of silently doing nothing (which looks like the first tap
        // was ignored and invites blind retries).
        guard presentation == nil else {
            self.error = "Another operation is already running. Finish or cancel it before starting a new one."
            return
        }
        guard !loading else {
            self.error = "SideStore is still loading. Wait for the current request to finish, then try again."
            return
        }
        switch operation {
        case "signOut": signOut()
        case "syncAppIDs": syncAppIDs()
        case "clearCache": clearCache()
        case "refreshSources": refreshSources()
        case "jit": jit(target: target)
        case "install", "installURL", "installSharedIPA", "update", "refreshApp",
             "activate", "deactivate", "remove", "delete", "backup", "restore":
            presentation = V3OperationRequest(operation: operation, target: target, title: title)
        default: break
        }
    }
    private func needsSignIn(_ error: Error) -> Bool {
        (error as? CombinedFailure)?.stage == .authentication
    }
    private func failed(_ error: Error) {
        if needsSignIn(error) { signInPresented = true }
        else { self.error = error.localizedDescription }
    }
    func signOut() {
        guard !loading else { return }
        loading = true
        Task {
            do {
                // The service answers every one of these mutations with a
                // fresh snapshot: accept it directly, then release the busy
                // state BEFORE reload(). Calling reload() while loading is
                // still true trips its guard and the UI keeps stale state.
                accept(try await V3ServiceBridge.shared.request(operation: "signOut"))
                loading = false
                notice = "Signed out successfully."
                reload()
            } catch { loading = false; failed(error) }
        }
    }
    func jit(target: String) {
        guard !loading else { return }
        loading = true
        Task {
            do {
                accept(try await V3ServiceBridge.shared.request(operation: "jit", target: target))
                loading = false
                notice = "JIT enabled."
                reload()
            } catch { loading = false; failed(error) }
        }
    }
    func syncAppIDs() {
        guard !loading else { return }
        loading = true
        Task {
            do {
                accept(try await V3ServiceBridge.shared.request(operation: "syncAppIDs"))
                loading = false
                notice = "App IDs synced."
                reload()
            } catch { loading = false; failed(error) }
        }
    }
    func clearCache() {
        guard !loading else { return }
        loading = true
        Task {
            do {
                accept(try await V3ServiceBridge.shared.request(operation: "clearCache"))
                loading = false
                notice = "Download cache cleared."
                reload()
            } catch { loading = false; failed(error) }
        }
    }
    func refreshSources() {
        guard !loading else { return }
        loading = true
        Task {
            do {
                accept(try await V3ServiceBridge.shared.request(operation: "refreshSources"))
                loading = false
                notice = "Sources updated."
                reload()
            } catch { loading = false; failed(error) }
        }
    }
    func stageSharedFile(_ data: Data) -> String? {
        guard !data.isEmpty, data.count <= 4_194_304 else {
            self.error = "The selected file is empty or too large to hand to the SideStore service."
            return nil
        }
        let token = UUID().uuidString
        LCUtils.appGroupUserDefault.set(data, forKey: "V3SharedFile." + token)
        return token
    }
    @discardableResult
    func stageSharedIPA(_ url: URL, bookmark: Data? = nil, title: String,
                        presentImmediately: Bool = true) -> String? {
        guard presentation == nil, !loading else {
            self.error = "Another operation is already running. Finish or cancel it before installing another app."
            return nil
        }
        do {
            guard let container = LCSharedUtils.appGroupPath() else { throw CombinedIPAFileError(.fileAccess) }
            let token = try V3IPAStaging.stage(sourceURL: url, bookmark: bookmark, containerRoot: container)
            if presentImmediately { presentStagedIPA(token, title: title) }
            return token
        } catch let failure as CombinedIPAFileError {
            self.error = failure.localizedDescription
        } catch {
            self.error = CombinedIPAFileError(.stagingFailed).localizedDescription
        }
        return nil
    }
    func presentStagedIPA(_ token: String, title: String) {
        do { _ = try V3IPAStaging.canonicalToken(token) }
        catch { self.error = CombinedIPAFileError(.invalidToken).localizedDescription; return }
        // The file has already been durably copied. Yield until a picker sheet
        // is dismissed before presenting the operation sheet.
        Task { @MainActor in
            await Task.yield()
            guard self.presentation == nil else {
                let cleaned = await self.cleanupStagedIPA(token)
                if cleaned {
                    self.error = "Another operation is already running. The selected IPA was discarded; choose it again after the current operation finishes."
                }
                return
            }
            self.perform("installSharedIPA", target: token, title: title)
        }
    }
    func cleanupStagedIPA(_ token: String) async -> Bool {
        do {
            _ = try await V3ServiceBridge.shared.request(operation: "ipaCleanup", target: token)
            return true
        } catch {
            self.error = "SideStore could not remove the staged IPA. Copy Diagnostics and retry cleanup after the current operation ends."
            return false
        }
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
            V3RefreshAllButton()
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
                    Text(status.loading ? "Loading apps..." : "No sideloaded apps")
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
        Button("Refresh") {
            sharedModel.selectedTab = .home
            status.refreshTarget = app.isHost ? nil : app.identifier
            status.refreshPresented = true
        }
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

struct V3SideStoreAppDetail: View {    @EnvironmentObject private var status: V3SideStoreStatusStore
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
    @State private var preview: [String: Any]?
    @State private var previewBusy = false
    @State private var addBusy = false
    @State private var removeBusy = false
    @State private var notice = ""
    @State private var removeCandidate: V3SideStoreSource?
    private var savedGuestSources: [String] {
        (UserDefaults.standard.stringArray(forKey: "LCAltStoreSourceURLs") ?? [])
            .filter { saved in !status.sources.contains(where: { $0.url == saved }) }
    }
    var body: some View {
        NavigationView {
            List {
                if !notice.isEmpty {
                    Section {
                        Text(notice)
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
                }
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
                        Task { await previewSource() }
                    } label: {
                        Label(previewBusy ? "Checking Source..." : "Preview and Add Source", systemImage: "plus.circle.fill")
                    }
                    .disabled(status.sourceURL.isEmpty || previewBusy)
                    if let preview {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(preview["name"] as? String ?? "")
                                .font(.headline)
                            Text(preview["title"] as? String ?? "")
                                .font(.subheadline)
                            Text(preview["message"] as? String ?? "")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        .padding(.vertical, 4)
                        Button {
                            Task { await confirmAdd(url: preview["url"] as? String ?? status.sourceURL) }
                        } label: {
                            Label((preview["alreadyAdded"] as? Bool ?? false) ? "Already Added" : (addBusy ? "Adding Source..." : "Confirm Add Source"), systemImage: "checkmark.circle.fill")
                        }
                        .disabled((preview["alreadyAdded"] as? Bool ?? false) || addBusy)
                    }
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
                                    removeCandidate = source
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
                    .disabled(status.loading)
                }
            }
            .confirmationDialog("Remove this source?", isPresented: Binding(get: { removeCandidate != nil }, set: { if !$0 { removeCandidate = nil } }), titleVisibility: .visible) {
                Button("Remove Source", role: .destructive) {
                    if let candidate = removeCandidate {
                        Task { await confirmRemove(id: candidate.identifier) }
                    }
                }
                Button("Cancel", role: .cancel) { removeCandidate = nil }
            } message: {
                Text("Apps already installed from this source stay installed, but they will no longer receive updates.")
            }
        }
        .navigationViewStyle(StackNavigationViewStyle())
    }
    private func previewSource() async {
        previewBusy = true
        defer { previewBusy = false }
        do {
            var row = try await V3ServiceBridge.shared.request(operation: "sourcePreview", target: status.sourceURL)
            row["url"] = status.sourceURL
            preview = row
        } catch { status.error = error.localizedDescription }
    }
    private func confirmAdd(url: String) async {
        addBusy = true
        notice = ""
        defer { addBusy = false }
        do {
            _ = try await V3ServiceBridge.shared.request(operation: "sourceAddConfirmed", target: url)
            preview = nil
            status.sourceURL = ""
            notice = "Source added."
            status.reload()
        } catch { status.error = error.localizedDescription }
    }
    private func confirmRemove(id: String) async {
        removeCandidate = nil
        removeBusy = true
        notice = "Removing source..."
        defer { removeBusy = false }
        do {
            _ = try await V3ServiceBridge.shared.request(operation: "sourceRemoveConfirmed", target: id)
            notice = "Source removed."
            status.reload()
        } catch { status.error = error.localizedDescription }
    }
}

struct V3CatalogApp: Identifiable {
    let id: String, name: String, version: String, developer: String, description: String, installedID: String
    let canInstall: Bool
    let downloadURL: String
    let installedVersion: String?
    init?(_ row: [String: Any]) {
        guard let id = row["identifier"] as? String, let name = row["name"] as? String else { return nil }
        self.id = id; self.name = name; version = row["version"] as? String ?? ""
        developer = row["developer"] as? String ?? ""; description = row["description"] as? String ?? ""
        installedID = row["installedID"] as? String ?? ""; canInstall = row["canInstall"] as? Bool ?? false
        downloadURL = row["downloadURL"] as? String ?? ""
        installedVersion = row["installedVersion"] as? String
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
    @State private var loadInFlight = false
    var body: some View {
        List {
            if loading {
                HStack {
                    Spacer()
                    ProgressView("Loading catalog...")
                    Spacer()
                }
                .padding()
            }
            if let error {
                VStack(alignment: .leading, spacing: 8) {
                    Text(error).font(.caption).foregroundColor(.red)
                    Button("Retry") { Task { await load() } }
                        .disabled(loadInFlight)
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
        guard !loadInFlight else { return }
        loadInFlight = true
        defer { loadInFlight = false }
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
        Section("Setup") {
            Button {
                NSLog("[V3_SETUP] OPEN source=settings")
                status.setupPresented = true
            } label: {
                Label("Setup Assistant", systemImage: "list.clipboard.fill")
            }
        }
        Section("Account and Signing") {
            if status.needsSignIn {
                V3SignInLink(title: "Sign In with Apple ID")
            } else {
                HStack {
                    Label("Apple ID", systemImage: "person.crop.circle.fill")
                    Spacer()
                    Text(status.account)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
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
            // Re-authenticate and Sign Out exist only for a signed-in account.
            // When signed out, the section above already offers Sign In, so a
            // second sign-in row and a meaningless Sign Out must not appear.
            if !status.needsSignIn {
                NavigationLink {
                    V3SignInView().environmentObject(status)
                } label: {
                    Label("Re-authenticate", systemImage: "person.badge.key.fill")
                }
                .accessibilityHint("Sign in again with the current account")
            }
            Button {
                status.syncAppIDs()
            } label: {
                Label("Sync App IDs", systemImage: "arrow.triangle.2.circlepath")
            }
            link("Certificates", icon: "doc.text") { V3CertificatesView().environmentObject(status) }
            link("Developer Services", icon: "wrench.and.screwdriver") { V3DeveloperServicesView().environmentObject(status) }
            if !status.needsSignIn {
                Button(role: .destructive) {
                    status.signOut()
                } label: {
                    Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                }
            }
        }

        Section("Device") {
            HStack {
                Label("Pairing Status", systemImage: "link")
                Spacer()
                Text(status.pairing)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            NavigationLink {
                V3PairingView().environmentObject(status)
            } label: {
                Label("Import Pairing File", systemImage: "doc.badge.plus")
            }
            link("Connection", icon: "network") { V3ConnectionView().environmentObject(status) }
        }

        Section("Apps and Data") {
            link("SideStore Backups", icon: "archivebox") { V3BackupsView().environmentObject(status) }
            link("Installation and Signing Options", icon: "slider.horizontal.3") { V3CustomizationsView().environmentObject(status) }
            Button {
                status.clearCache()
            } label: {
                Label("Clear Download Cache", systemImage: "trash")
            }
        }

        Section("Services") {
            link("Anisette Servers", icon: "server.rack") { V3AnisetteView().environmentObject(status) }
            link("SideSign Configuration", icon: "pencil.and.outline") { V3SideSignView().environmentObject(status) }
            link("SideJIT Server", icon: "bolt.fill") { V3SideJITView().environmentObject(status) }
            link("Update Channel", icon: "arrow.triangle.merge") { V3ReleaseTrackHostView().environmentObject(status) }
            setting("Beta updates", "isBetaUpdatesEnabled", icon: "sparkles")
            setting("Disable idle timeout", "isIdleTimeoutDisableEnabled", icon: "timer")
        }

        Section("Diagnostics") {
            link("Health Check", icon: "heart.text.square") { V3HealthView().environmentObject(status) }
            link("Operation Logs", icon: "doc.text.magnifyingglass") { V3LogsView().environmentObject(status) }
            link("SideStore Diagnostics", icon: "waveform.path.ecg") { V3DiagnosticsView().environmentObject(status) }
            link("Experimental Features", icon: "flask") { V3ExperimentalView().environmentObject(status) }
        }

        Section("Guest Runtime") {
            NavigationLink {
                LCTweaksView()
            } label: {
                Label("Tweaks", systemImage: "slider.vertical.3")
            }
        }
    }
    private func link<Destination: View>(_ title: String, icon: String, @ViewBuilder destination: () -> Destination) -> some View {
        NavigationLink(destination: destination) {
            HStack {
                Label(title, systemImage: icon)
                Spacer()
            }
        }
    }
    private func setting(_ title: String, _ key: String, icon: String) -> some View {
        V3BoolSettingRow(title: title, key: key, icon: icon)
            .environmentObject(status)
    }
}

struct V3BoolSettingRow: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    let title: String
    let key: String
    let icon: String
    @State private var value = false
    @State private var loaded = false
    @State private var loadingRequest = false
    @State private var writeGenerations = V3SettingsWriteGeneration()
    @State private var confirmedValue: Bool?
    var body: some View {
        Toggle(isOn: Binding(get: { value }, set: { value = $0; save($0) })) {
            Label(title, systemImage: icon)
        }
        .disabled(status.isStale || !loaded)
        .task { await load() }
    }
    private func load() async {
        guard !loadingRequest else { return }
        loadingRequest = true
        defer { loadingRequest = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsGet")
            if let bools = reply["bools"] as? [String: Bool], let current = bools[key] {
                value = current
                confirmedValue = current
            } else if let legacy = status.settings[key] {
                value = legacy
                confirmedValue = legacy
            }
            loaded = true
        } catch { status.error = error.localizedDescription }
    }
    private func save(_ newValue: Bool) {
        let generation = writeGenerations.begin(key)
        Task {
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "bool", "bool": newValue])
                if writeGenerations.isCurrent(generation, for: key) {
                    confirmedValue = newValue
                    status.reload()
                } else {
                    _ = await reloadAuthoritative(generation: writeGenerations.current(for: key))
                }
            } catch {
                guard writeGenerations.isCurrent(generation, for: key) else { return }
                let loaded = await reloadAuthoritative(generation: generation)
                if !loaded, writeGenerations.isCurrent(generation, for: key) {
                    value = confirmedValue ?? !newValue
                }
                status.error = error.localizedDescription
            }
        }
    }
    private func reloadAuthoritative(generation: UInt64) async -> Bool {
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsGet")
            guard writeGenerations.isCurrent(generation, for: key),
                  let bools = reply["bools"] as? [String: Bool], let current = bools[key] else { return false }
            value = current
            confirmedValue = current
            status.reload()
            return true
        } catch {
            return false
        }
    }
}

private struct V3StatusStoreKey: EnvironmentKey {
    static var defaultValue: V3SideStoreStatusStore? { nil }
}

extension EnvironmentValues {
    var v3StatusStore: V3SideStoreStatusStore? {
        get { self[V3StatusStoreKey.self] }
        set { self[V3StatusStoreKey.self] = newValue }
    }
}

struct V3TargetedRefreshSection: View {
    // Custom key with a nil default: programmatic navigation links can
    // evaluate their destination outside the inherited environment on some
    // iOS versions. A missing store must hide this section, never trap.
    @Environment(\.v3StatusStore) private var status
    var body: some View {
        if let status,
           let target = status.refreshTarget,
           let app = status.installedApps.first(where: { $0.identifier == target }) {
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
    @State private var attempt = V3OperationAttemptState()
    @State private var uncertainSessionID: String?
    @State private var state = "working"
    @State private var progress = 0.0
    @State private var prompt: [String: Any]?
    @State private var sourceOffer: [String: String]?
    @State private var message = ""
    @State private var task: Task<Void, Never>?
    @State private var startedGeneration: UUID?
    @State private var isDismissing = false
    @State private var promptSubmitting = false
    @State private var failureContext = V3OperationRetryContext()
    @State private var whatToDo = ""
    @State private var technicalDetails = ""
    @State private var recoveryDestination: String?
    @State private var retryBlocked = false
    @State private var stagedIPACleaned = false
    @State private var copied = false
    private var retryAllowed: Bool {
        guard !retryBlocked else { return false }
        if state == "requiresSource" { return sourceOffer != nil }
        if state == "cancelled" { return true }
        guard state == "failed", !isTransitioning else { return false }
        return [.allowed, .unknown].contains(failureContext.retryDisposition)
    }
    private var retryButtonTitle: String {
        failureContext.retryDisposition == .unknown ? "Retry (outcome unknown)" : "Retry"
    }
    private func recoveryActionTitle(for destination: String) -> String? {
        switch destination {
        case "signIn": return "Open Account & Signing"
        case "certificates": return "Open Certificates"
        case "ipa": return "Choose IPA Again"
        case "setup": return "Open Connection Check"
        default: return nil
        }
    }
    private var isTransitioning: Bool { attempt.transitionInFlight }
    private var isRunning: Bool { ["working", "awaitingPrompt", "cancelling"].contains(state) }
    var body: some View {
        NavigationView {
            List {
                Section {
                    HStack {
                        if state == "working" || state == "awaitingPrompt" {
                            ProgressView(value: progress > 0 ? progress : nil)
                                .frame(maxWidth: .infinity)
                        } else if state == "completed" {
                            Label("Completed", systemImage: "checkmark.circle.fill")
                                .foregroundColor(.green)
                        }
                    }
                    HStack {
                        Text("Status")
                        Spacer()
                        Text(statusText).foregroundColor(.secondary)
                    }
                }
                if let prompt {
                    V3PromptSection(prompt: prompt, isSubmitting: $promptSubmitting) { answer in
                        Task { await answerPrompt(id: prompt["id"] as? String ?? "", answer: answer) }
                    }
                }
                if let offer = sourceOffer {
                    Section("Missing Source") {
                        Text("\"\((offer["name"] ?? ""))\" is not added. Add it, then the operation retries automatically.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                        Button {
                            guard !isTransitioning else { return }
                            Task { await addSourceAndRetry(id: offer["id"] ?? "") }
                        } label: {
                            Label("Add Source and Retry", systemImage: "plus.circle.fill")
                        }
                        .disabled(isTransitioning)
                    }
                }
                if !message.isEmpty {
                    Section("What happened") {
                        Text(message)
                            .font(.footnote)
                            .textSelection(.enabled)
                    }
                    if !whatToDo.isEmpty {
                        Section("What you can do") {
                            Text(whatToDo).font(.footnote)
                            if let destination = recoveryDestination,
                               let action = recoveryActionTitle(for: destination) {
                                Button(action) { openRecoveryDestination(destination) }
                            }
                            if retryAllowed {
                                Button(isTransitioning ? "Waiting..." : retryButtonTitle) { retry() }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(isTransitioning)
                            }
                        }
                    } else if retryAllowed {
                        Section("What you can do") {
                            Button(isTransitioning ? "Waiting..." : retryButtonTitle) { retry() }
                                .buttonStyle(.borderedProminent)
                                .disabled(isTransitioning)
                        }
                    }
                    if !technicalDetails.isEmpty {
                        Section {
                            DisclosureGroup("Technical details") {
                                Text(technicalDetails)
                                    .font(.caption2)
                                    .textSelection(.enabled)
                            }
                            Button(copied ? "Copied" : "Copy Diagnostics") {
                                UIPasteboard.general.string = technicalDetails
                                copied = true
                                Task {
                                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                                    copied = false
                                }
                            }
                            .font(.caption)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(request.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(isRunning ? "Cancel" : "Done") {
                        if isRunning { cancelAttempt() } else { acknowledgeAndDismiss() }
                    }
                    .disabled(isTransitioning)
                }
            }
        }
        .navigationViewStyle(StackNavigationViewStyle())
        .interactiveDismissDisabled(true)
        .task { start() }
        .onDisappear {
            if !isDismissing {
                isDismissing = true
                let oldTask = task
                let mustConfirmCancel = isRunning || uncertainSessionID != nil
                let oldSession = uncertainSessionID ?? attempt.supersede()
                oldTask?.cancel()
                Task { @MainActor in
                    var cancellationConfirmed = !mustConfirmCancel
                    if let oldSession {
                        do {
                            _ = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                            cancellationConfirmed = true
                        } catch { cancellationConfirmed = false }
                    }
                    await oldTask?.value
                    if request.operation == "installSharedIPA", cancellationConfirmed, !stagedIPACleaned {
                        stagedIPACleaned = await status.cleanupStagedIPA(request.target)
                    } else if request.operation == "installSharedIPA", mustConfirmCancel, !cancellationConfirmed {
                        status.error = "The operation was not confirmed as stopped, so its staged IPA was kept safely. Reconnect before cleanup."
                    }
                    status.reload()
                }
            }
        }
    }
    private var statusText: String {
        switch state {
        case "cancelling": return "Cancelling..."
        case "working" where isTransitioning: return "Waiting for previous attempt..."
        case "completed": return "Completed"
        case "awaitingPrompt": return "Needs your input"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        case "requiresSource": return "Source required"
        default: return progress > 0 ? "\(Int(progress * 100))%" : (startedGeneration == nil ? "Preparing..." : "Working...")
        }
    }
    private func start() {
        guard startedGeneration == nil, !attempt.transitionInFlight else { return }
        startAttempt(generation: attempt.begin())
    }
    private func startAttempt(generation: UUID) {
        guard attempt.generation == generation, startedGeneration != generation else { return }
        startedGeneration = generation
        task = Task { await run(generation: generation) }
    }
    private func run(generation: UUID) async {
        var backendSessionStarted = false
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "opStart",
                payload: ["kind": request.operation, "target": request.target,
                          "session": generation.uuidString])
            if reply["failedToStart"] as? Bool == true {
                handleStartFailure(reply, generation: generation)
                return
            }
            guard let id = reply["session"] as? String else {
                if reply["state"] as? String == "failed" {
                    handleStartFailure(reply, generation: generation)
                } else {
                    let failure = CombinedFailure(operation: request.operation, stage: .command,
                        code: .invalidResponse, id: generation.uuidString, retryable: false)
                    failureContext.recordStartFailure(failure)
                    presentCurrentFailure()
                }
                return
            }
            guard id == generation.uuidString else {
                _ = try? await V3ServiceBridge.shared.request(operation: "opCancel", target: id)
                let failure = CombinedFailure(operation: request.operation, stage: .command,
                    code: .staleResult, id: generation.uuidString, retryable: false)
                failureContext.recordStartFailure(failure)
                presentCurrentFailure()
                return
            }
            guard attempt.bind(sessionID: id, generation: generation) else {
                _ = try? await V3ServiceBridge.shared.request(operation: "opCancel", target: id)
                return
            }
            backendSessionStarted = true
            failureContext.operationStarted()
            retryBlocked = false
            try await pollLoop(id: id, generation: generation)
        } catch {
            guard attempt.generation == generation, !attempt.isTerminal else { return }
            let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                operation: request.operation,
                stage: backendSessionStarted ? .xpcConnection : .command,
                id: generation.uuidString)
            if backendSessionStarted {
                failureContext.recordPipelineFailure(failure)
            } else {
                failureContext.recordStartFailure(failure)
            }
            presentCurrentFailure()
        }
    }

    private func handleStartFailure(_ reply: [String: Any], generation: UUID) {
        guard attempt.generation == generation, !attempt.isTerminal else { return }
        let failure = (reply["failure"] as? [String: Any]).flatMap {
            CombinedFailure.decode($0, expectedID: generation.uuidString)
        } ?? CombinedFailure(operation: request.operation,
            stage: CombinedFailure.Stage(rawValue: reply["stage"] as? String ?? "") ?? .command,
            code: CombinedFailure.Code(rawValue: reply["code"] as? String ?? "") ?? .failed,
            id: generation.uuidString, retryable: reply["retryable"] as? Bool)
        failureContext.recordStartFailure(failure)
        presentCurrentFailure()
    }

    private func presentCurrentFailure() {
        _ = attempt.acceptStartFailure(generation: attempt.generation)
        state = "failed"
        message = failureContext.whatHappened
        whatToDo = failureContext.whatToDo
        technicalDetails = failureContext.technicalDetails
        recoveryDestination = failureContext.currentFailure?.recoveryDestination
        recordRefresh("failed", message)
    }
    private func pollLoop(id: String, generation: UUID) async throws {
        while !Task.isCancelled {
            try await Task.sleep(nanoseconds: 1_000_000_000)
            try Task.checkCancellation()
            guard attempt.matches(generation: generation, sessionID: id) else { return }
            let reply = try await V3ServiceBridge.shared.request(operation: "opPoll", target: id)
            guard let current = reply["state"] as? String else {
                throw NSError(domain: "V3Operation", code: 2,
                              userInfo: [NSLocalizedDescriptionKey: "The service returned an unreadable operation state."])
            }
            guard reply["session"] as? String == id else { return }
            apply(reply, generation: generation, sessionID: id)
            guard current == "working" || current == "awaitingPrompt" else { return }
        }
    }
    private func apply(_ reply: [String: Any], generation: UUID, sessionID: String) {
        guard let nextState = reply["state"] as? String,
              attempt.accept(state: nextState, generation: generation, sessionID: sessionID) else { return }
        state = nextState
        progress = reply["progress"] as? Double ?? progress
        let oldPromptID = prompt?["id"] as? String
        let nextPrompt = reply["prompt"] as? [String: Any]
        prompt = nextPrompt
        if oldPromptID != (nextPrompt?["id"] as? String) { promptSubmitting = false }
        switch state {
        case "completed":
            // Terminal success stays visible until the user presses Done.
            // Auto-dismissing here made successful fast operations look like
            // nothing happened.
            if message.isEmpty { message = request.title + " completed successfully." }
            whatToDo = "Reload app status to confirm the installed app and signing state."
            technicalDetails = ""
            recoveryDestination = nil
            failureContext.reset()
            recordRefresh("completed", "The operation completed. Reload the app list to confirm the result.")
            status.reload()
            if request.operation == "installSharedIPA", !stagedIPACleaned {
                Task { stagedIPACleaned = await status.cleanupStagedIPA(request.target) }
            }
        case "cancelled":
            // A backend cancellation the user did not request (the Done
            // button already dismisses locally) stays visible as a terminal
            // result with an explicit message instead of silently returning
            // to the app list.
            message = "The operation was cancelled before it finished. Run it again if the cancellation was not intended."
            whatToDo = "Retry is safe because the backend confirmed that this attempt stopped."
            technicalDetails = ""
            recoveryDestination = nil
            retryBlocked = false
        case "waitingForAuthentication":
            status.signInPresented = true
            message = "Sign in first, then run this action again."
            whatToDo = "Open Account & Signing, complete sign-in, then start a new operation."
            recoveryDestination = "signIn"
            retryBlocked = true
        case "requiresSource":
            sourceOffer = ["id": reply["sourceID"] as? String ?? "",
                           "name": reply["sourceName"] as? String ?? "Unknown source"]
            prompt = nil
        case "failed":
            let failure = (reply["failure"] as? [String: Any]).flatMap {
                CombinedFailure.decode($0, expectedID: sessionID)
            } ?? CombinedFailure(operation: request.operation,
                stage: CombinedFailure.Stage(rawValue: reply["stage"] as? String ?? "") ?? .command,
                code: CombinedFailure.Code(rawValue: reply["code"] as? String ?? "") ?? .failed,
                id: sessionID, retryable: reply["retryable"] as? Bool)
            failureContext.recordPipelineFailure(failure)
            retryBlocked = false
            message = failureContext.whatHappened
            whatToDo = failureContext.whatToDo
            technicalDetails = failureContext.technicalDetails
            recoveryDestination = failureContext.currentFailure?.recoveryDestination
            recordRefresh("failed", message)
        default: break
        }
    }
    private func answerPrompt(id: String, answer: [String: String]) async {
        guard !id.isEmpty, let session = attempt.sessionID else { return }
        let generation = attempt.generation
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "opAnswer", target: session,
                payload: ["prompt": id, "answer": answer])
            guard attempt.matches(generation: generation, sessionID: session),
                  reply["session"] as? String == session else { return }
            apply(reply, generation: generation, sessionID: session)
        } catch {
            guard attempt.generation == generation else { return }
            promptSubmitting = false
            message = "The response could not be submitted. Check the connection, then try once more."
        }
    }
    private func addSourceAndRetry(id: String) async {
        guard !id.isEmpty, attempt.beginTransition() else { return }
        do {
            let preview = try await V3ServiceBridge.shared.request(operation: "sourcePreview", target: id)
            _ = try await V3ServiceBridge.shared.request(operation: "sourceAddConfirmed",
                target: preview["identifier"] as? String ?? id)
            status.reload()
            attempt.endTransition()
            retry()
        } catch {
            attempt.endTransition()
            message = error.localizedDescription
        }
    }
    private func retry() {
        guard retryAllowed, attempt.beginTransition() else { return }
        failureContext.beginRetry()
        let oldTask = task
        let oldSession = attempt.supersede()
        let transitionGeneration = attempt.generation
        uncertainSessionID = oldSession
        startedGeneration = nil
        promptSubmitting = false
        prompt = nil
        sourceOffer = nil
        progress = 0
        message = "Waiting for the previous attempt to stop..."
        whatToDo = "The new attempt will start after the service confirms that the prior session stopped."
        technicalDetails = failureContext.technicalDetails
        recoveryDestination = nil
        retryBlocked = false
        state = "working"
        Task { @MainActor in
            oldTask?.cancel()
            do {
                if let oldSession {
                    _ = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                }
                await oldTask?.value
                guard attempt.transitionInFlight, attempt.generation == transitionGeneration else { return }
                uncertainSessionID = nil
                message = ""
                let generation = attempt.begin()
                attempt.endTransition()
                startAttempt(generation: generation)
            } catch {
                await oldTask?.value
                guard attempt.generation == transitionGeneration else { return }
                attempt.endTransition()
                let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                    operation: request.operation, stage: .xpcConnection,
                    id: transitionGeneration.uuidString)
                failureContext.recordStartFailure(failure)
                retryBlocked = true
                presentCurrentFailure()
            }
        }
    }
    private func cancelAttempt() {
        guard isRunning, attempt.beginTransition() else { return }
        state = "cancelling"
        message = ""
        let oldTask = task
        let oldSession = attempt.supersede()
        let transitionGeneration = attempt.generation
        uncertainSessionID = oldSession
        startedGeneration = nil
        prompt = nil
        Task { @MainActor in
            oldTask?.cancel()
            do {
                if let oldSession {
                    _ = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                }
                await oldTask?.value
                guard attempt.generation == transitionGeneration else { return }
                uncertainSessionID = nil
                state = "cancelled"
                message = "The operation was cancelled and the backend confirmed that it stopped."
                whatToDo = "You can safely start the operation again."
                technicalDetails = ""
                retryBlocked = false
                status.reload()
            } catch {
                await oldTask?.value
                guard attempt.generation == transitionGeneration else { return }
                state = "failed"
                let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                    operation: request.operation, stage: .xpcConnection,
                    id: transitionGeneration.uuidString)
                failureContext.recordPipelineFailure(failure)
                retryBlocked = true
                message = "SideStore could not confirm that the operation stopped. It may still be running."
                whatToDo = "Reconnect and reload operation status before trying another mutation."
                technicalDetails = failure.technicalDetails
            }
            attempt.endTransition()
        }
    }
    private func acknowledgeAndDismiss() {
        guard attempt.beginTransition() else { return }
        isDismissing = true
        let oldTask = task
        let oldSession = attempt.supersede()
        oldTask?.cancel()
        Task { @MainActor in
            await oldTask?.value
            let cancellationTarget = uncertainSessionID ?? oldSession
            if let cancellationTarget {
                do {
                    _ = try await V3ServiceBridge.shared.request(operation: "opCancel", target: cancellationTarget)
                    uncertainSessionID = nil
                } catch {
                    if uncertainSessionID != nil {
                        isDismissing = false
                        attempt.endTransition()
                        retryBlocked = true
                        message = "SideStore could not confirm that the previous operation stopped. The staged IPA was kept safely."
                        whatToDo = "Reconnect before retrying or cleaning up the selected IPA."
                        return
                    }
                }
            }
            if request.operation == "installSharedIPA", !stagedIPACleaned {
                stagedIPACleaned = await status.cleanupStagedIPA(request.target)
            }
            status.reload()
            dismiss()
        }
    }
    private func openRecoveryDestination(_ destination: String) {
        status.operationRecoveryDestination = destination
        acknowledgeAndDismiss()
    }
    private func recordRefresh(_ result: String, _ detail: String) {
        guard request.operation == "refreshApp" else { return }
        NotificationCenter.default.post(name: Notification.Name("V3TargetedRefreshResult"), object: nil,
                                        userInfo: ["result": result, "detail": detail])
    }
}

struct V3PromptSection: View {
    let prompt: [String: Any]
    @Binding var isSubmitting: Bool
    let onAnswer: ([String: String]) -> Void
    @State private var fields: [String: String] = [:]
    @State private var selected: Set<String> = []
    @State private var copiedDetails = false
    private var kind: String { prompt["kind"] as? String ?? "" }
    private var title: String { prompt["title"] as? String ?? "Input Needed" }
    private var message: String { prompt["message"] as? String ?? "" }
    private var fieldDefs: [[String: String]] {
        (prompt["fields"] as? [[String: Any]] ?? []).compactMap { row in
            guard let key = row["key"] as? String else { return nil }
            return ["key": key, "label": row["label"] as? String ?? key,
                    "secure": row["secure"] as? String ?? "false",
                    "value": row["value"] as? String ?? ""]
        }
    }
    private var options: [[String: String]] {
        (prompt["options"] as? [[String: Any]] ?? []).compactMap { row in
            guard let id = row["id"] as? String else { return nil }
            return ["id": id, "label": row["label"] as? String ?? id]
        }
    }
    private var isMulti: Bool { kind == "extensions" || kind == "revocation" }
    private var deliveryOptions: [[String: String]] {
        options.filter { ["trustedDevice", "sms", "voice"].contains($0["id"] ?? "") }
    }
    private var phoneOptions: [[String: String]] {
        options.filter { ($0["id"] ?? "").hasPrefix("phone:") }
    }
    var body: some View {
        Section(title) {
            if !message.isEmpty {
                Text(message)
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }
            if kind == "twoFactor" {
                Text("Step 1 - Choose how Apple sends your code:")
                    .font(.subheadline.weight(.semibold))
                ForEach(deliveryOptions, id: \.self) { option in
                    Button {
                        var answer = fields
                        answer["choice"] = option["id"] ?? ""
                        answer["action"] = option["id"] ?? ""
                        respond(answer)
                    } label: {
                        HStack {
                            Text(option["label"] ?? "")
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(isSubmitting)
                }
                ForEach(phoneOptions, id: \.self) { option in
                    Button {
                        var answer = fields
                        let phoneID = String((option["id"] ?? "").dropFirst("phone:".count))
                        answer["phoneID"] = phoneID
                        let delivery = fields["mode"] == "voice" ? "voice" : "sms"
                        answer["choice"] = delivery
                        answer["action"] = delivery
                        respond(answer)
                    } label: {
                        HStack {
                            Image(systemName: "phone.fill")
                                .foregroundColor(.accentColor)
                            Text(option["label"] ?? "")
                            Spacer()
                        }
                    }
                    .disabled(isSubmitting)
                }
                Text("Step 2 - Enter the code you received:")
                    .font(.subheadline.weight(.semibold))
                    .padding(.top, 4)
                TextField("6-digit code", text: binding("code"))
                    .keyboardType(.numberPad)
                    .textFieldStyle(.roundedBorder)
                Button("Submit Code") {
                    var answer = fields
                    answer["choice"] = "code"
                    answer["action"] = "code"
                    respond(answer)
                }
                .buttonStyle(.borderedProminent)
                .disabled((fields["code"] ?? "").isEmpty || isSubmitting)
                Button("Cancel Sign In", role: .cancel) {
                    respond(["action": "cancel", "choice": "cancel"])
                }
                .disabled(isSubmitting)
            } else {
            ForEach(fieldDefs, id: \.self) { field in
                // The "technical" field is diagnostics-only output: it renders
                // as selectable caption text below, never as an editable field.
                if field["key"] == "mode" || field["key"] == "activeID" || field["key"] == "phoneID" || field["key"] == "url" || field["key"] == "serials" || field["key"] == "technical" {
                    if let value = field["value"], !value.isEmpty, field["key"] == "url" {
                        Text(value)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .textSelection(.enabled)
                    }
                } else if field["secure"] == "true" {
                    SecureField(field["label"] ?? "", text: binding(field["key"] ?? ""))
                } else {
                    TextField(field["label"] ?? "", text: binding(field["key"] ?? ""))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }
            }
            if fieldDefs.count > 0 && options.isEmpty {
                Button("Submit") { submit(choice: "") }
                    .buttonStyle(.borderedProminent)
                    .disabled(isSubmitting)
            }
            // Safe technical diagnostics travel separately from the
            // user-facing message and can be copied without the prompt text.
            if let technical = fieldDefs.first(where: { $0["key"] == "technical" }),
               let value = technical["value"], !value.isEmpty {
                Text("Technical details")
                    .font(.caption.weight(.semibold))
                    .foregroundColor(.secondary)
                    .padding(.top, 4)
                Text(value)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .textSelection(.enabled)
                Button(copiedDetails ? "Copied" : "Copy Details") {
                    UIPasteboard.general.string = value
                    copiedDetails = true
                    Task {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        copiedDetails = false
                    }
                }
                .font(.caption)
            }
            if isMulti {
                ForEach(options.filter { $0["id"] != "keep" && $0["id"] != "keepAll" }, id: \.self) { option in
                    Button {
                        toggle(option["id"] ?? "")
                    } label: {
                        HStack {
                            Image(systemName: selected.contains(option["id"] ?? "") ? "checkmark.circle.fill" : "circle")
                                .foregroundColor(.accentColor)
                            Text(option["label"] ?? "")
                        }
                    }
                }
                if kind == "revocation" {
                    Button("Keep Existing") { submit(choice: "keep") }.disabled(isSubmitting)
                } else {
                    Button("Keep All") { submit(choice: "keepAll") }.disabled(isSubmitting)
                }
                    Button(kind == "revocation" ? "Revoke Selected" : "Remove Selected", role: .destructive) {
                    var answer = fields
                    answer["choice"] = kind == "revocation" ? "revoke" : "selected"
                    answer["ids"] = selected.sorted().joined(separator: ",")
                    answer["serials"] = selected.sorted().joined(separator: ",")
                    respond(answer)
                }
                .disabled(selected.isEmpty || isSubmitting)
            } else {
                ForEach(options, id: \.self) { option in
                    Button(option["label"] ?? "", role: (option["id"] == "cancel" || option["id"] == "deny") ? .cancel : .none) {
                        var answer = fields
                        answer["choice"] = option["id"] ?? ""
                        answer["action"] = option["id"] ?? ""
                        respond(answer)
                    }
                    .disabled(isSubmitting)
                }
            }
            }
        }
        .onAppear {
            for field in fieldDefs {
                if fields[field["key"] ?? ""] == nil {
                    fields[field["key"] ?? ""] = field["value"] ?? ""
                }
            }
        }
    }
    private func binding(_ key: String) -> Binding<String> {
        Binding(get: { fields[key] ?? "" }, set: { fields[key] = $0 })
    }
    private func toggle(_ id: String) {
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
    }
    private func submit(choice: String) {
        var answer = fields
        answer["choice"] = choice
        respond(answer)
    }
    private func respond(_ answer: [String: String]) {
        guard !isSubmitting else { return }
        isSubmitting = true
        onAnswer(answer)
    }
}

@MainActor
final class V3AuthStore: ObservableObject {
    @Published var state = "idle"
    @Published var prompt: [String: Any]?
    @Published var attempts = 0
    @Published var message = ""
    @Published var team = ""
    @Published var promptSubmitting = false
    @Published private(set) var isCancelling = false
    @Published private(set) var cancellationConfirmed = true
    private var session: String?
    private var task: Task<Void, Never>?

    func begin() {
        guard canBegin else { return }
        task?.cancel()
        state = "working"
        message = ""
        prompt = nil
        promptSubmitting = false
        cancellationConfirmed = true
        task = Task { await run() }
    }
    var canBegin: Bool {
        !isCancelling && cancellationConfirmed && !["working", "awaitingPrompt"].contains(state)
    }

    private func run() async {
        do {
            state = "working"
            message = ""
            prompt = nil
            let reply = try await V3ServiceBridge.shared.request(operation: "authBegin")
            guard let id = reply["session"] as? String else {
                throw NSError(domain: "V3Auth", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "The service did not start sign-in."])
            }
            session = id
            try await pollLoop(id: id)
        } catch {
            state = "failed"
            message = error.localizedDescription
        }
    }

    private func pollLoop(id: String) async throws {
        while !Task.isCancelled {
            try await Task.sleep(nanoseconds: 1_000_000_000)
            try Task.checkCancellation()
            let reply = try await V3ServiceBridge.shared.request(operation: "authPoll", target: id)
            apply(reply)
            guard let current = reply["state"] as? String, current == "working" || current == "awaitingPrompt" else { return }
        }
    }

    private func apply(_ reply: [String: Any]) {
        let oldPromptID = prompt?["id"] as? String
        state = reply["state"] as? String ?? state
        attempts = reply["attempts"] as? Int ?? attempts
        prompt = reply["prompt"] as? [String: Any]
        if oldPromptID != (prompt?["id"] as? String) { promptSubmitting = false }
        if state == "completed" {
            team = reply["team"] as? String ?? ""
            prompt = nil
        } else if state == "failed" {
            var detail = "Sign-in failed."
            if let stage = reply["stage"] as? String, let code = reply["code"] as? String {
                detail += " (\(stage): \(code))"
            }
            message = detail
            prompt = nil
        }
    }

    static func failureMessage(from failure: [String: Any]) -> String {
        // The service classifies the real typed error into a display kind.
        // Only show password guidance for proven invalid credentials.
        switch failure["kind"] as? String {
        case "invalidCredentials": return "Apple did not accept the Apple ID or password. Check them and try again."
        case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."
        case "invalidCode": return "The previous verification code was not accepted. Continue to try again with a new code."
        case "rateLimited": return "Too many authentication attempts. Apple is temporarily rate-limiting requests. Wait before trying again."
        case "serviceUnavailable": return "Apple's authentication service did not return a valid response. Try again later."
        case "anisetteFailure", "anisette": return "Authentication could not obtain valid Anisette data."
        case "networkFailure", "network": return "Authentication could not reach the required Apple service. Check the connection and try again."
        case "accountRepairRequired": return "Apple requires attention on this account before signing in."
        case "unknown", nil: break
        default: break
        }
        let code = failure["code"] as? String ?? ""
        let stage = failure["stage"] as? String ?? ""
        switch code {
        case "invalidCredentials": return "Apple did not accept the Apple ID or password. Check them and try again."
        case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."
        case "rateLimited": return "Too many authentication attempts. Apple is temporarily rate-limiting requests. Wait before trying again."
        case "serviceUnavailable": return "Apple's authentication service is temporarily unavailable. Try again later."
        case "anisetteFailure": return "Authentication could not obtain valid Anisette data."
        case "networkFailure": return "Authentication could not reach the required Apple service."
        case "accountRepairRequired": return "Account repair is required. Open the Apple Developer account to resolve."
        default:
            let messages = ["authentication": "Apple ID sign-in failed.",
                           "anisette": "Anisette authentication infrastructure failure.",
                           "network": "Network error during authentication.",
                           "accountRepair": "Account repair required."]
            return messages[stage] ?? "Apple ID sign-in failed."
        }
    }

    static func failureDetails(from failure: [String: Any]) -> String {
        let kind = failure["kind"] as? String ?? ""
        let stage = failure["stage"] as? String ?? ""
        let code = failure["code"] as? String ?? ""
        let correlation = failure["correlationID"] as? String ?? ""
        let underlyingDomain = failure["underlyingDomain"] as? String ?? ""
        let underlyingCode = failure["underlyingCode"] as? Int ?? 0
        let retryable = failure["retryable"] as? Bool ?? false
        return "kind=\(kind) stage=\(stage) code=\(code) correlation=\(correlation) underlying=\(underlyingDomain)/\(underlyingCode) retryable=\(retryable ? "yes" : "no")"
    }

    func answer(promptID: String, answer: [String: String]) {
        guard !promptID.isEmpty, let session,
              prompt?["id"] as? String == promptID else { return }
        promptSubmitting = true
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "authRespond", target: session,
                    payload: ["prompt": promptID, "answer": answer])
                guard self.session == session, reply["session"] as? String == session else { return }
                apply(reply)
            } catch {
                guard self.session == session else { return }
                promptSubmitting = false
                message = "The response could not be submitted. Check the connection, then try once more."
            }
        }
    }

    func cancel() {
        guard !isCancelling, ["working", "awaitingPrompt"].contains(state) else { return }
        isCancelling = true
        cancellationConfirmed = false
        let oldTask = task
        let oldSession = session
        task?.cancel()
        Task { @MainActor in
            do {
                if let oldSession {
                    _ = try await V3ServiceBridge.shared.request(operation: "authCancel", target: oldSession)
                }
                await oldTask?.value
                cancellationConfirmed = true
                state = "cancelled"
                message = "Sign-in was cancelled."
            } catch {
                await oldTask?.value
                state = "failed"
                message = "The service could not confirm sign-in cancellation. Reconnect before starting another sign-in."
            }
            session = nil
            prompt = nil
            promptSubmitting = false
            task = nil
            isCancelling = false
        }
    }
}

struct V3SignInLink: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    let title: String
    var body: some View {
        NavigationLink {
            V3SignInView().environmentObject(status)
        } label: {
            Label(title, systemImage: "person.badge.key.fill")
        }
    }
}

struct V3SignInView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @StateObject private var auth = V3AuthStore()
    var body: some View {
        List {
            Section("Apple ID") {
                HStack {
                    Text("Status")
                    Spacer()
                    Text(statusText).foregroundColor(.secondary)
                }
                if auth.state == "completed" {
                    HStack {
                        Label("Signed in", systemImage: "checkmark.circle.fill")
                            .foregroundColor(.green)
                        Spacer()
                        if !auth.team.isEmpty { Text(auth.team).foregroundColor(.secondary) }
                    }
                }
                if !auth.message.isEmpty {
                    Text(auth.message)
                        .font(.footnote)
                        .foregroundColor(.red)
                        .textSelection(.enabled)
                }
                if auth.state == "idle" || auth.state == "failed" || auth.state == "cancelled" {
                    Button {
                        auth.begin()
                    } label: {
                        Label(auth.state == "idle" ? "Begin Sign In" : "Try Again", systemImage: "person.badge.key.fill")
                    }
                    .disabled(!auth.canBegin)
                }
                if auth.state == "working" || auth.state == "awaitingPrompt" {
                    Button(auth.isCancelling ? "Cancelling..." : "Cancel Sign In", role: .cancel) { auth.cancel() }
                        .disabled(auth.isCancelling)
                }
            }
            if let prompt = auth.prompt {
                if let previousFailure = prompt["previousFailure"] as? [String: Any] {
                    Section {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(V3AuthStore.failureMessage(from: previousFailure))
                                .font(.footnote)
                                .foregroundColor(.orange)
                            Text(V3AuthStore.failureDetails(from: previousFailure))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                }
                V3PromptSection(prompt: prompt, isSubmitting: $auth.promptSubmitting) { answer in
                    auth.answer(promptID: prompt["id"] as? String ?? "", answer: answer)
                }
            }
            Section("About") {
                Text("Sign-in runs entirely in this screen. Credentials and codes go to Apple through the SideStore service; no separate app opens.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Sign In")
        .task { auth.begin() }
        .onDisappear {
            auth.cancel()
            status.reload()
        }
    }
    private var statusText: String {
        switch auth.state {
        case "completed": return "Signed in"
        case "awaitingPrompt": return "Needs your input"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        case "working": return "Working..."
        default: return "Not started"
        }
    }
}

struct V3CertificateRow: Identifiable {
    let serial: String, name: String, machine: String, email: String
    let active: Bool
    let created: Date?
    let expiry: Date?
    var id: String { serial }
    init?(_ row: [String: Any]) {
        guard let serial = row["serial"] as? String, !serial.isEmpty else { return nil }
        self.serial = serial; name = row["name"] as? String ?? serial
        machine = row["machineName"] as? String ?? ""; email = row["requesterEmail"] as? String ?? ""
        active = row["active"] as? Bool ?? false
        created = row["created"] as? Date; expiry = row["expiry"] as? Date
    }
}

struct V3CertificatesView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var local: [V3CertificateRow] = []
    @State private var portal: [V3CertificateRow] = []
    @State private var loading = true
    @State private var portalLoaded = false
    @State private var message = ""
    @State private var notice = ""
    @State private var busy = ""
    @State private var loadingRequest = false
    @State private var confirm: (String, String)?
    var body: some View {
        List {
            if !message.isEmpty {
                Section {
                    Text(message).font(.footnote).foregroundColor(.red).textSelection(.enabled)
                }
            }
            if !notice.isEmpty {
                Section {
                    Text(notice).font(.footnote).foregroundColor(.secondary)
                }
            }
            if status.needsSignIn {
                Section {
                    V3SignInLink(title: "Sign In to Manage Certificates")
                }
            }
            Section("On This Device (\(local.count))") {
                if loading { ProgressView("Loading certificates...") }
                ForEach(local) { cert in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(cert.name).font(.headline)
                            Spacer()
                            if cert.active {
                                Text("Active").font(.caption.weight(.bold)).foregroundColor(.green)
                            }
                        }
                        Text(cert.serial).font(.caption).foregroundColor(.secondary).textSelection(.enabled)
                        if let expiry = cert.expiry {
                            Text("Expires " + expiry.formatted(date: .abbreviated, time: .omitted))
                                .font(.caption).foregroundColor(.secondary)
                        }
                        HStack {
                            if !cert.active {
                                Button(busy == cert.serial ? "Working..." : "Set Active") { setActive(serial: cert.serial) }
                                    .font(.caption)
                                    .disabled(!busy.isEmpty)
                            }
                            Spacer()
                            Button("Delete", role: .destructive) { confirm = ("delete", cert.serial) }
                                .font(.caption)
                                .disabled(!busy.isEmpty)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            Section("Developer Portal") {
                if !portalLoaded {
                    Button(busy == "portal" ? "Loading Portal Certificates..." : "Load Portal Certificates") { Task { await loadPortal() } }
                        .disabled(!busy.isEmpty)
                } else {
                    ForEach(portal) { cert in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(cert.name).font(.headline)
                            Text(cert.serial).font(.caption).foregroundColor(.secondary).textSelection(.enabled)
                            if let expiry = cert.expiry {
                                Text("Expires " + expiry.formatted(date: .abbreviated, time: .omitted))
                                    .font(.caption).foregroundColor(.secondary)
                            }
                            Button("Revoke", role: .destructive) { confirm = ("revoke", cert.serial) }
                                .font(.caption)
                                .disabled(!busy.isEmpty)
                        }
                        .padding(.vertical, 4)
                    }
                    Button("Request New Certificate") { confirm = ("create", "") }
                        .disabled(!busy.isEmpty)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Certificates")
        .task { await reload() }
        .confirmationDialog("Are you sure?", isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } }), titleVisibility: .visible) {
            Button("Confirm", role: .destructive) {
                if let action = confirm { Task { await runConfirmed(action: action.0, serial: action.1) } }
            }
            Button("Cancel", role: .cancel) { confirm = nil }
        } message: {
            Text("Revoking or deleting a certificate affects every app signed with it.")
        }
    }
    private func reload() async {
        guard !loadingRequest else { return }
        loadingRequest = true
        loading = true
        defer { loading = false; loadingRequest = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "certList")
            local = (reply["certificates"] as? [[String: Any]] ?? []).compactMap(V3CertificateRow.init)
            message = ""
        } catch { message = error.localizedDescription }
    }
    private func loadPortal() async {
        busy = "portal"
        notice = ""
        defer { busy = "" }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "certPortalList")
            portal = (reply["certificates"] as? [[String: Any]] ?? []).compactMap(V3CertificateRow.init)
            portalLoaded = true
            message = ""
        } catch { message = error.localizedDescription }
    }
    private func setActive(serial: String) {
        busy = serial
        notice = ""
        Task {
            defer { busy = "" }
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "certSetActive", target: serial)
                status.reload()
                await reload()
                notice = "Active certificate updated."
            } catch { message = error.localizedDescription }
        }
    }
    private func runConfirmed(action: String, serial: String) async {
        confirm = nil
        busy = action + serial
        notice = ""
        defer { busy = "" }
        do {
            switch action {
            case "delete": _ = try await V3ServiceBridge.shared.request(operation: "certDelete", target: serial)
            case "revoke": _ = try await V3ServiceBridge.shared.request(operation: "certRevoke", target: serial)
            default: _ = try await V3ServiceBridge.shared.request(operation: "certCreate")
            }
            status.reload()
            await reload()
            portalLoaded = false
            message = ""
            switch action {
            case "delete": notice = "Certificate deleted."
            case "revoke": notice = "Certificate revoked."
            default: notice = "Certificate requested."
            }
        } catch { message = error.localizedDescription }
    }
}

struct V3DeveloperServicesView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var teams: [[String: String]] = []
    @State private var devices: [[String: String]] = []
    @State private var appIDs: [[String: String]] = []
    @State private var groups: [[String: String]] = []
    @State private var profiles: [[String: Any]] = []
    @State private var message = ""
    @State private var loading = true
    @State private var loadingRequest = false
    var body: some View {
        List {
            if !message.isEmpty {
                Section { Text(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            if status.needsSignIn {
                Section {
                    V3SignInLink(title: "Sign In to Load Developer Data")
                }
            }
            Section("Actions") {
                Button { status.syncAppIDs() } label: { Label("Sync App IDs", systemImage: "arrow.triangle.2.circlepath") }
                    .disabled(status.loading)
                Button(loading ? "Loading Developer Data..." : "Reload Developer Data") { Task { await reload() } }
                    .disabled(loading)
            }
            simpleSection("Teams", rows: teams.map { "\($0["name"] ?? "") (\($0["identifier"] ?? ""))" })
            simpleSection("Devices", rows: devices.map { "\($0["name"] ?? "") · \($0["identifier"] ?? "")" })
            simpleSection("App IDs", rows: appIDs.map { "\($0["name"] ?? "") · \($0["bundleID"] ?? "")" })
            simpleSection("App Groups", rows: groups.map { "\($0["name"] ?? "") · \($0["identifier"] ?? "")" })
            Section("Provisioning Profiles (\(profiles.count))") {
                if loading { ProgressView() }
                ForEach(profiles.indices, id: \.self) { index in
                    let row = profiles[index]
                    let name = row["name"] as? String ?? row["profileName"] as? String ?? "Profile"
                    let detail = row["bundleID"] as? String ?? row["bundleIdentifier"] as? String ?? ""
                    VStack(alignment: .leading, spacing: 2) {
                        Text(name).font(.headline)
                        if !detail.isEmpty {
                            Text(detail)
                                .font(.caption).foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Developer Services")
        .task { await reload() }
    }
    private func simpleSection(_ title: String, rows: [String]) -> some View {
        Section("\(title) (\(rows.count))") {
            if loading { ProgressView() }
            if rows.isEmpty && !loading {
                Text("None").foregroundColor(.secondary)
            }
            ForEach(rows, id: \.self) { row in
                Text(row).font(.subheadline).textSelection(.enabled)
            }
        }
    }
    private func strings(_ reply: [String: Any], key: String) -> [[String: String]] {
        (reply[key] as? [[String: Any]] ?? []).map { row in
            Dictionary(uniqueKeysWithValues: row.compactMap { k, v in (v as? String).map { (k, $0) } })
        }
    }
    private func reload() async {
        guard !loadingRequest else { return }
        loadingRequest = true
        loading = true
        defer { loading = false; loadingRequest = false }
        do {
            async let teamsReply = V3ServiceBridge.shared.request(operation: "devTeams")
            async let devicesReply = V3ServiceBridge.shared.request(operation: "devDevices")
            async let appIDsReply = V3ServiceBridge.shared.request(operation: "devAppIDs")
            async let groupsReply = V3ServiceBridge.shared.request(operation: "devGroups")
            async let profilesReply = V3ServiceBridge.shared.request(operation: "devProfiles")
            let (teamsResult, devicesResult, appIDsResult, groupsResult, profilesResult) =
                try await (teamsReply, devicesReply, appIDsReply, groupsReply, profilesReply)
            teams = strings(teamsResult, key: "teams")
            devices = strings(devicesResult, key: "devices")
            appIDs = strings(appIDsResult, key: "appIDs")
            groups = strings(groupsResult, key: "groups")
            profiles = profilesResult["profiles"] as? [[String: Any]] ?? []
            message = ""
        } catch { message = error.localizedDescription }
    }
}

struct V3FilePicker: UIViewControllerRepresentable {
    let types: [String]
    let completion: (URL?) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(completion: completion) }
    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: types.map { UTType($0) ?? .data }, asCopy: true)
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
        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) { finish(urls.first) }
        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { finish(nil) }
    }
}

struct V3ActivitySheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

struct V3PairingView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var pickerPresented = false
    @State private var message = ""
    @State private var working = false
    var body: some View {
        List {
            Section("Status") {
                HStack {
                    Text("Pairing Status")
                    Spacer()
                    Text(status.pairing).foregroundColor(.secondary)
                }
                if !message.isEmpty {
                    Text(message).font(.footnote).foregroundColor(.red).textSelection(.enabled)
                }
            }
            Section {
                Button {
                    pickerPresented = true
                } label: {
                    Label(working ? "Importing..." : "Select Pairing File", systemImage: "doc.badge.plus")
                }
                .disabled(working)
                Text("Pick a .mobiledevicepairing or .plist file. This screen owns the picker; the service only validates and stores the file.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Pairing File")
        .sheet(isPresented: $pickerPresented) {
            V3FilePicker(types: ["com.apple.property-list", "public.xml", "public.data"]) { url in
                pickerPresented = false
                if let url { Task { await importFile(url) } }
            }
        }
    }
    private func importFile(_ url: URL) async {
        working = true
        defer { working = false }
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            guard let token = status.stageSharedFile(data) else { return }
            _ = try await V3ServiceBridge.shared.request(operation: "pairingImportData", target: token)
            message = ""
            status.reload()
        } catch { message = error.localizedDescription }
    }
}

@MainActor
final class V3SettingsStore: ObservableObject {
    @Published var bools: [String: Bool] = [:]
    @Published var strings: [String: String] = [:]
    @Published var ints: [String: Int] = [:]
    @Published var loaded = false
    @Published var message = ""
    private var writeGenerations = V3SettingsWriteGeneration()
    private var loadingRequest = false
    private var confirmedBools: [String: Bool] = [:]
    private var confirmedStrings: [String: String] = [:]
    private var confirmedInts: [String: Int] = [:]
    func load() async {
        guard !loadingRequest else { return }
        loadingRequest = true
        defer { loadingRequest = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsGet")
            bools = reply["bools"] as? [String: Bool] ?? [:]
            strings = reply["strings"] as? [String: String] ?? [:]
            ints = reply["ints"] as? [String: Int] ?? [:]
            confirmedBools = bools
            confirmedStrings = strings
            confirmedInts = ints
            loaded = true
            message = ""
        } catch { message = error.localizedDescription }
    }
    func setBool(_ key: String, _ value: Bool) {
        let generation = writeGenerations.begin(key)
        bools[key] = value
        Task {
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "bool", "bool": value])
                if writeGenerations.isCurrent(generation, for: key) {
                    confirmedBools[key] = value
                } else {
                    _ = await reloadAuthoritative(key: key, type: "bool", generation: writeGenerations.current(for: key))
                }
            } catch {
                guard writeGenerations.isCurrent(generation, for: key) else { return }
                let loaded = await reloadAuthoritative(key: key, type: "bool", generation: generation)
                if !loaded, writeGenerations.isCurrent(generation, for: key) {
                    if let confirmed = confirmedBools[key] { bools[key] = confirmed }
                    else { bools.removeValue(forKey: key) }
                }
                message = error.localizedDescription
            }
        }
    }
    func setString(_ key: String, _ value: String) {
        let generation = writeGenerations.begin(key)
        strings[key] = value
        Task {
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "string", "string": value])
                if writeGenerations.isCurrent(generation, for: key) {
                    confirmedStrings[key] = value
                } else {
                    _ = await reloadAuthoritative(key: key, type: "string", generation: writeGenerations.current(for: key))
                }
            } catch {
                guard writeGenerations.isCurrent(generation, for: key) else { return }
                let loaded = await reloadAuthoritative(key: key, type: "string", generation: generation)
                if !loaded, writeGenerations.isCurrent(generation, for: key) {
                    if let confirmed = confirmedStrings[key] { strings[key] = confirmed }
                    else { strings.removeValue(forKey: key) }
                }
                message = error.localizedDescription
            }
        }
    }
    func setInt(_ key: String, _ value: Int) {
        let generation = writeGenerations.begin(key)
        ints[key] = value
        Task {
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "int", "int": value])
                if writeGenerations.isCurrent(generation, for: key) {
                    confirmedInts[key] = value
                } else {
                    _ = await reloadAuthoritative(key: key, type: "int", generation: writeGenerations.current(for: key))
                }
            } catch {
                guard writeGenerations.isCurrent(generation, for: key) else { return }
                let loaded = await reloadAuthoritative(key: key, type: "int", generation: generation)
                if !loaded, writeGenerations.isCurrent(generation, for: key) {
                    if let confirmed = confirmedInts[key] { ints[key] = confirmed }
                    else { ints.removeValue(forKey: key) }
                }
                message = error.localizedDescription
            }
        }
    }
    private func reloadAuthoritative(key: String, type: String, generation: UInt64) async -> Bool {
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsGet")
            guard writeGenerations.isCurrent(generation, for: key) else { return false }
            switch type {
            case "bool":
                let values = reply["bools"] as? [String: Bool] ?? [:]
                if let value = values[key] { bools[key] = value; confirmedBools[key] = value }
                else { bools.removeValue(forKey: key); confirmedBools.removeValue(forKey: key) }
            case "string":
                let values = reply["strings"] as? [String: String] ?? [:]
                if let value = values[key] { strings[key] = value; confirmedStrings[key] = value }
                else { strings.removeValue(forKey: key); confirmedStrings.removeValue(forKey: key) }
            default:
                let values = reply["ints"] as? [String: Int] ?? [:]
                if let value = values[key] { ints[key] = value; confirmedInts[key] = value }
                else { ints.removeValue(forKey: key); confirmedInts.removeValue(forKey: key) }
            }
            return true
        } catch {
            return false
        }
    }
}

struct V3ToggleRow: View {
    @ObservedObject var store: V3SettingsStore
    let title: String
    let key: String
    var body: some View {
        Toggle(title, isOn: Binding(get: { store.bools[key] ?? false },
                                    set: { store.setBool(key, $0) }))
            .disabled(!store.loaded)
    }
}

struct V3TextRow: View {
    @ObservedObject var store: V3SettingsStore
    let title: String
    let key: String
    @State private var text = ""
    @State private var seeded = false
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.subheadline)
            TextField("Not set", text: $text, onCommit: { store.setString(key, text) })
                .textFieldStyle(.roundedBorder)
                .autocapitalization(.none)
                .disableAutocorrection(true)
                .disabled(!store.loaded)
        }
        .padding(.vertical, 2)
        .onReceive(store.$strings) { strings in
            if !seeded, let current = strings[key] {
                text = current
                seeded = true
            }
        }
        .onChange(of: store.strings[key]) { current in
            if let current { text = current; seeded = true }
        }
    }
}

struct V3ConnectionView: View {
    @StateObject private var store = V3SettingsStore()
    @State private var port = ""
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { Text(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Connection") {
                V3ToggleRow(store: store, title: "Always Show VPN Configuration", key: "alwaysShowWireGuardConfig")
                V3ToggleRow(store: store, title: "Accept IPv6 Connections", key: "acceptIPv6ConnectionConfig")
                V3ToggleRow(store: store, title: "Use Local VPN", key: "useLocalVPN")
                VStack(alignment: .leading, spacing: 4) {
                    Text("Remote Pairing Port Override (0 = default)").font(.subheadline)
                    TextField("0", text: $port, onCommit: {
                        store.setInt("remotePairingPortOverride", Int(port) ?? 0)
                    })
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.numberPad)
                    .disabled(!store.loaded)
                }
                .padding(.vertical, 2)
                .onReceive(store.$ints) { ints in
                    if let value = ints["remotePairingPortOverride"] {
                        port = String(value)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Connection")
        .task { await store.load() }
    }
}

struct V3AnisetteServerRow: Identifiable {
    let id: String, name: String, address: String
    let hidden: Bool, active: Bool
    init?(_ row: [String: Any]) {
        guard let id = row["id"] as? String, !id.isEmpty else { return nil }
        self.id = id; name = row["name"] as? String ?? id; address = row["address"] as? String ?? ""
        hidden = row["hidden"] as? Bool ?? false; active = row["active"] as? Bool ?? false
    }
}

struct V3AnisetteView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @StateObject private var store = V3SettingsStore()
    @State private var servers: [V3AnisetteServerRow] = []
    @State private var message = ""
    @State private var notice = ""
    @State private var remoteBusy = false
    var body: some View {
        List {
            if !message.isEmpty {
                Section { Text(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            if !notice.isEmpty {
                Section { Text(notice).font(.footnote).foregroundColor(.secondary) }
            }
            if !store.message.isEmpty {
                Section { Text(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Servers (\(servers.count))") {
                ForEach(servers) { server in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(server.name).font(.headline)
                            Spacer()
                            if server.active {
                                Text("Active").font(.caption.weight(.bold)).foregroundColor(.green)
                            }
                        }
                        Text(server.address).font(.caption).foregroundColor(.secondary).textSelection(.enabled)
                        if !server.active && !server.hidden {
                            Button("Use This Server") {
                                store.setString("menuAnisetteURL", server.address)
                            }
                            .font(.caption)
                        }
                    }
                    .padding(.vertical, 2)
                }
                HStack {
                    Button(remoteBusy ? "Working..." : "Sync with Remote") { Task { await remote("anisetteSync") } }
                        .disabled(remoteBusy)
                    Spacer()
                    Button("Reset to Defaults", role: .destructive) { Task { await remote("anisetteReset") } }
                        .disabled(remoteBusy)
                }
                .font(.caption)
            }
            Section("Options") {
                V3ToggleRow(store: store, title: "Offline Mode", key: "isAnisetteOfflineMode")
                V3ToggleRow(store: store, title: "Disable Rotation", key: "disableAnisetteRotation")
                V3ToggleRow(store: store, title: "On-Device Anisette", key: "useOnDeviceAnisette")
                V3TextRow(store: store, title: "Custom Server URL", key: "textInputAnisetteURL")
                V3TextRow(store: store, title: "Custom Anisette URL Override", key: "customAnisetteURL")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Anisette Servers")
        .task {
            await store.load()
            await reload()
        }
    }
    private func reload() async {
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "anisetteList")
            servers = (reply["servers"] as? [[String: Any]] ?? []).compactMap(V3AnisetteServerRow.init)
            message = ""
        } catch { message = error.localizedDescription }
    }
    private func remote(_ operation: String) async {
        remoteBusy = true
        notice = ""
        defer { remoteBusy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: operation)
            servers = (reply["servers"] as? [[String: Any]] ?? []).compactMap(V3AnisetteServerRow.init)
            message = ""
            notice = operation == "anisetteReset" ? "Anisette servers reset." : "Anisette servers synced."
        } catch { message = error.localizedDescription }
    }
}

struct V3SideSignView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var config = ""
    @State private var message = ""
    @State private var notice = ""
    @State private var busy = false
    @State private var exporting = false
    @State private var pickerPresented = false
    @State private var shareItems: [Any]?
    var body: some View {
        List {
            if !message.isEmpty {
                Section { Text(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            if !notice.isEmpty {
                Section { Text(notice).font(.footnote).foregroundColor(.secondary) }
            }
            Section("Configuration JSON") {
                TextEditor(text: $config)
                    .font(.system(.caption, design: .monospaced))
                    .frame(minHeight: 220)
                HStack {
                    Button(busy ? "Saving..." : "Save") { Task { await save() } }
                        .disabled(busy)
                    Spacer()
                    Button("Reset to Defaults", role: .destructive) { Task { await remote("sidesignReset") } }
                        .disabled(busy)
                }
                .font(.caption)
            }
            Section("Import / Export") {
                Button("Import from File") { pickerPresented = true }
                    .disabled(busy)
                Button(exporting ? "Exporting..." : "Export to File") { Task { await exportConfig() } }
                    .disabled(busy || exporting)
                Text("The picker belongs to this screen; the service only parses and stores the file.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("SideSign Configuration")
        .task { await reload() }
        .sheet(isPresented: $pickerPresented) {
            V3FilePicker(types: ["public.json"]) { url in
                pickerPresented = false
                if let url { Task { await importFile(url) } }
            }
        }
        .sheet(item: Binding(get: { shareItems.map { V3ShareBox(items: $0) } }, set: { _ in shareItems = nil })) { box in
            V3ActivitySheet(items: box.items)
        }
    }
    private func reload() async {
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignGet")
            config = reply["config"] as? String ?? "{}"
            message = ""
        } catch { message = error.localizedDescription }
    }
    private func save() async {
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignSet", payload: ["config": config])
            config = reply["config"] as? String ?? config
            message = ""
            notice = "Configuration saved."
        } catch { message = error.localizedDescription }
    }
    private func remote(_ operation: String) async {
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: operation)
            config = reply["config"] as? String ?? config
            message = ""
            notice = "Configuration reset."
        } catch { message = error.localizedDescription }
    }
    private func importFile(_ url: URL) async {
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            guard let token = status.stageSharedFile(data) else { return }
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignImport", target: token)
            config = reply["config"] as? String ?? config
            message = ""
            notice = "Configuration imported."
        } catch { message = error.localizedDescription }
    }
    private func exportConfig() async {
        guard !busy, !exporting else { return }
        exporting = true
        defer { exporting = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignExport")
            let text = reply["config"] as? String ?? "{}"
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("sidesign-config.json")
            try text.write(to: url, atomically: true, encoding: .utf8)
            shareItems = [url]
        } catch { message = error.localizedDescription }
    }
}

struct V3ShareBox: Identifiable {
    let id = UUID()
    let items: [Any]
}

struct V3CustomizationsView: View {
    @StateObject private var store = V3SettingsStore()
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { Text(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Signing") {
                V3ToggleRow(store: store, title: "Customize App ID", key: "customizeAppId")
                V3ToggleRow(store: store, title: "Customize App Extensions", key: "customizeAppExtensions")
                V3ToggleRow(store: store, title: "Auto-Fix App Group IDs", key: "autoFixAppGroupIDs")
                V3ToggleRow(store: store, title: "Prefer Resigned IPA", key: "preferResignedIPA")
                V3ToggleRow(store: store, title: "Export Resigned App", key: "isExportResignedAppEnabled")
                V3TextRow(store: store, title: "Minimuxer Gateway Backend", key: "minimuxerGatewayBackend")
            }
            Section("Verification") {
                V3ToggleRow(store: store, title: "App Verification Disabled", key: "appVerificationDisabled")
                V3ToggleRow(store: store, title: "Verify Bundle ID", key: "isBundleIDVerificationEnabled")
                V3ToggleRow(store: store, title: "Verify iOS Version", key: "isiOSVersionVerificationEnabled")
                V3ToggleRow(store: store, title: "Verify App Version", key: "isAppVersionVerificationEnabled")
                V3ToggleRow(store: store, title: "Verify Checksum", key: "isChecksumVerificationEnabled")
                V3ToggleRow(store: store, title: "Verify File Size", key: "isFileSizeVerificationEnabled")
                V3ToggleRow(store: store, title: "Disable Permission Checking", key: "permissionCheckingDisabled")
            }
            Section("Backups") {
                V3ToggleRow(store: store, title: "Skip Non-Copyable Backup Files", key: "skipNonCopyableBackupFiles")
            }
            Section("Network") {
                V3ToggleRow(store: store, title: "On-Device Anisette", key: "useOnDeviceAnisette")
                V3ToggleRow(store: store, title: "WireGuard EMP", key: "enableEMPforWireguard")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Installation Options")
        .task { await store.load() }
    }
}

struct V3HealthView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var rows: [(String, String)] = []
    @State private var certRows: [(String, String)] = []
    @State private var message = ""
    @State private var checking = false
    var body: some View {
        List {
            if status.needsSignIn {
                Section {
                    V3SignInLink(title: "Sign In to Check Account Health")
                }
            }
            if !message.isEmpty {
                Section { Text(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            Section("Health") {
                ForEach(rows, id: \.0) { row in
                    HStack {
                        Text(row.0)
                        Spacer()
                        Text(row.1).foregroundColor(.secondary).multilineTextAlignment(.trailing)
                    }
                    .font(.subheadline)
                }
            }
            Section {
                Button(checking ? "Checking..." : "Re-check") { Task { await reload() } }
                    .disabled(checking)
            }
            Section("Certificates") {
                ForEach(certRows, id: \.0) { row in
                    HStack {
                        Text(row.0)
                        Spacer()
                        Text(row.1).foregroundColor(.secondary).multilineTextAlignment(.trailing)
                    }
                    .font(.subheadline)
                }
                Text("The refresh pipeline signs with the SideStore active certificate, never the JIT-Less copy. The copy is separate; its revoked state alone does not cause a SideStore refresh failure. Re-import it under Settings when JIT-Less signing needs the current certificate.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Health Check")
        .task { await reload() }
    }
    private func reload() async {
        guard !checking else { return }
        checking = true
        defer { checking = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
            var result: [(String, String)] = []
            result.append(("Account", reply["account"] as? String ?? ""))
            result.append(("Team", reply["team"] as? String ?? ""))
            result.append(("Certificate", reply["certificate"] as? String ?? ""))
            result.append(("Pairing", reply["pairing"] as? String ?? ""))
            if let anisette = reply["anisette"] as? [String: Any] {
                result.append(("Anisette Servers", "\(anisette["servers"] as? Int ?? 0)"))
            }
            if let sidesign = reply["sidesign"] as? [String: Any] {
                result.append(("SideSign Configured", (sidesign["configured"] as? Bool ?? false) ? "Yes" : "No"))
            }
            rows = result
            certRows = certComparison(service: reply["certificateState"] as? [String: Any] ?? [:])
            message = ""
        } catch { message = error.localizedDescription }
    }
    // Compares the SideStore pipeline certificate (service facts) against the
    // LiveContainer JIT-Less copy (host facts: presence, team via local p12
    // parse, last import date). Teams compare in full; serials stay suffixes
    // and no key material is ever read. A "same team" verdict does not prove
    // serial identity: if JIT-Less Diagnose still reports Revoked while
    // SideStore reports Active, the copy predates the current certificate
    // and must be re-imported.
    private func certComparison(service: [String: Any]) -> [(String, String)] {
        let active = service["active"] as? Bool ?? false
        let serialSuffix = service["serialSuffix"] as? String ?? ""
        let team = service["team"] as? String ?? ""
        let expiry = service["expiry"] as? Date
        var result: [(String, String)] = []
        result.append(("SideStore Active", active ? "Yes" : "No"))
        if active {
            if !serialSuffix.isEmpty { result.append(("Active Serial", "…" + serialSuffix)) }
            if !team.isEmpty { result.append(("Active Team", "…" + String(team.suffix(4)))) }
            if let expiry { result.append(("Active Expiry", expiry.formatted(date: .abbreviated, time: .omitted))) }
        }
        let lcPresent = LCUtils.certificateData() != nil
        result.append(("JIT-Less Copy", lcPresent ? "Imported" : "Not imported"))
        var lcTeam = ""
        if lcPresent,
           let nsData = LCUtils.certificateData(),
           let password = LCSharedUtils.certificatePassword(),
           let parsed = LCUtils.getCertTeamId(withKeyData: nsData as Data, password: password) {
            lcTeam = parsed
            result.append(("Copy Team", "…" + String(parsed.suffix(4))))
        }
        if let lastUpdate = LCUtils.appGroupUserDefault.object(forKey: "LCCertificateUpdateDate") as? Date {
            result.append(("Copy Imported", lastUpdate.formatted(date: .abbreviated, time: .shortened)))
        }
        let verdict: String
        if !active {
            verdict = "unknown: SideStore has no active certificate"
        } else if !lcPresent || lcTeam.isEmpty {
            verdict = "unknown: no comparable JIT-Less copy"
        } else if lcTeam == team, !team.isEmpty {
            verdict = "yes: same team"
        } else {
            verdict = "no: different teams"
        }
        result.append(("Team Match", verdict))
        return result
    }
}

struct V3BackupsView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var exportPassword = ""
    @State private var includeApple = false
    @State private var importPassword = ""
    @State private var pickerPresented = false
    @State private var shareItems: [Any]?
    @State private var message = ""
    @State private var importedEmail = ""
    @State private var exportBusy = false
    @State private var importBusy = false
    var body: some View {
        List {
            if !message.isEmpty {
                Section { Text(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            Section("App Backups") {
                ForEach(status.installedApps.filter { !$0.isHost }) { app in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(app.name).font(.headline)
                        HStack {
                            Button("Back Up") {
                                status.perform("backup", target: app.identifier, title: "Back up " + app.name)
                            }
                            .font(.caption)
                            Spacer()
                            Button("Restore") {
                                status.perform("restore", target: app.identifier, title: "Restore " + app.name)
                            }
                            .font(.caption)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
            Section("Export Account") {
                SecureField("File Password", text: $exportPassword)
                    .textFieldStyle(.roundedBorder)
                Toggle("Include Apple Password", isOn: $includeApple)
                Button(exportBusy ? "Exporting..." : "Export Account File") { Task { await exportAccount() } }
                    .disabled(exportPassword.isEmpty || exportBusy || importBusy)
            }
            Section("Import Account") {
                Button(importBusy ? "Importing..." : "Select Backup File") { pickerPresented = true }
                    .disabled(exportBusy || importBusy)
                SecureField("File Password", text: $importPassword)
                    .textFieldStyle(.roundedBorder)
                if !importedEmail.isEmpty {
                    Text("Imported account for \(importedEmail). Sign in with its Apple password to finish.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    Button("Continue to Sign In") { status.signInPresented = true }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Backups")
        .sheet(isPresented: $pickerPresented) {
            V3FilePicker(types: ["public.data"]) { url in
                pickerPresented = false
                if let url { Task { await importAccount(url) } }
            }
        }
        .sheet(item: Binding(get: { shareItems.map { V3ShareBox(items: $0) } }, set: { _ in shareItems = nil })) { box in
            V3ActivitySheet(items: box.items)
        }
    }
    private func exportAccount() async {
        exportBusy = true
        defer { exportBusy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "accountExport",
                payload: ["password": exportPassword, "includeApple": includeApple])
            guard let encoded = reply["backup"] as? String,
                  let data = Data(base64Encoded: encoded) else {
                throw NSError(domain: "V3Backups", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "The service returned an unreadable backup."])
            }
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("sidestore-account.sidestorebackup")
            try data.write(to: url, options: .atomic)
            shareItems = [url]
            message = ""
        } catch { message = error.localizedDescription }
    }
    private func importAccount(_ url: URL) async {
        importBusy = true
        defer { importBusy = false }
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            guard let token = status.stageSharedFile(data) else { return }
            let reply = try await V3ServiceBridge.shared.request(operation: "accountImport", target: token,
                payload: ["password": importPassword])
            importedEmail = reply["email"] as? String ?? ""
            message = ""
            status.reload()
        } catch { message = error.localizedDescription }
    }
}

struct V3SideJITView: View {
    @StateObject private var store = V3SettingsStore()
    @State private var ping = ""
    @State private var testing = false
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { Text(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Server") {
                V3ToggleRow(store: store, title: "SideJIT Server Enabled", key: "isSideJITServerEnabled")
                V3TextRow(store: store, title: "Server Address", key: "textInputSideJITServerurl")
                Button(testing ? "Checking..." : "Test Reachability") { test() }
                    .disabled(testing)
                if !ping.isEmpty {
                    Text(ping).font(.caption).foregroundColor(.secondary)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("SideJIT Server")
        .task { await store.load() }
    }
    private func test() {
        guard !testing else { return }
        guard let address = store.strings["textInputSideJITServerurl"], !address.isEmpty,
              let url = URL(string: address.hasPrefix("http") ? address : "http://" + address) else {
            ping = "Enter a server address first."
            return
        }
        testing = true
        ping = "Checking..."
        Task {
            defer { testing = false }
            do {
                var request = URLRequest(url: url, timeoutInterval: 10)
                request.httpMethod = "GET"
                let (_, response) = try await URLSession.shared.data(for: request)
                ping = (response as? HTTPURLResponse).map { "Reachable (HTTP \($0.statusCode))." } ?? "Reachable."
            } catch {
                ping = "Unreachable: \(error.localizedDescription)"
            }
        }
    }
}

struct V3ReleaseTrackHostView: View {
    @StateObject private var store = V3SettingsStore()
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { Text(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Update Channel") {
                V3TextRow(store: store, title: "Beta Track", key: "betaUdpatesTrack")
                Text("Leave empty for the default channel.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Update Channel")
        .task { await store.load() }
    }
}

struct V3DiagnosticsView: View {
    @StateObject private var store = V3SettingsStore()
    @State private var confirmReset = false
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { Text(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Logging") {
                V3ToggleRow(store: store, title: "Verbose Operations", key: "isVerboseOperationsLoggingEnabled")
                V3ToggleRow(store: store, title: "Verbose SideStore", key: "isSideStoreVerboseLoggingEnabled")
                V3ToggleRow(store: store, title: "Verbose Signing", key: "isAltSignVerboseLoggingEnabled")
                V3ToggleRow(store: store, title: "Verbose Transport", key: "isMinimuxerVerboseLoggingEnabled")
                V3ToggleRow(store: store, title: "Widget Logging", key: "widgetVerboseLogging")
                V3ToggleRow(store: store, title: "Rotate Logs on Startup", key: "isRotateLogsOnStartupEnabled")
                V3ToggleRow(store: store, title: "Disable Response Caching", key: "responseCachingDisabled")
            }
            Section("Advanced") {
                V3ToggleRow(store: store, title: "Cellular Refresh", key: "isCellularRefreshEnabled")
                V3ToggleRow(store: store, title: "Debug Mode", key: "isDebugModeEnabled")
                Button("Recreate Database on Next Start", role: .destructive) { confirmReset = true }
                    .confirmationDialog("Recreate the database on next start?", isPresented: $confirmReset, titleVisibility: .visible) {
                        Button("Confirm", role: .destructive) { store.setBool("recreateDatabaseOnNextStart", true) }
                        Button("Cancel", role: .cancel) {}
                    }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Diagnostics")
        .task { await store.load() }
    }
}

struct V3LogsView: View {
    @State private var tail = ""
    @State private var message = ""
    @State private var copied = false
    @State private var reloading = false
    var body: some View {
        List {
            if !message.isEmpty {
                Section { Text(message).font(.footnote).foregroundColor(.red) }
            }
            Section {
                Button(reloading ? "Loading Logs..." : "Reload Logs") { Task { await reload() } }
                    .disabled(reloading)
                Button(copied ? "Copied" : "Copy Logs") {
                    UIPasteboard.general.string = tail
                    copied = true
                    Task {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        copied = false
                    }
                }
                .disabled(reloading || tail.isEmpty)
            }
            Section("Operation Logs") {
                Text(String(tail.suffix(120_000)))
                    .font(.system(.caption2, design: .monospaced))
                    .textSelection(.enabled)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Operation Logs")
        .task { await reload() }
    }
    private func reload() async {
        guard !reloading else { return }
        reloading = true
        defer { reloading = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "logTail")
            tail = reply["tail"] as? String ?? ""
            message = ""
        } catch { message = error.localizedDescription }
    }
}

struct V3ExperimentalView: View {
    @StateObject private var store = V3SettingsStore()
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { Text(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Experimental") {
                V3ToggleRow(store: store, title: "Cellular Refresh", key: "isCellularRefreshEnabled")
                Text("Experimental options can change or disappear. Current signing state is never reset by toggling them.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Experimental Features")
        .task { await store.load() }
    }
}

struct V3RefreshDetailView: View {
    var body: some View {
        LCEmbeddedSideStoreRefreshView()
            .navigationTitle("Refresh")
            .navigationBarTitleDisplayMode(.inline)
    }
}

struct V3SetupStepState: Equatable {
    var state = "checking"
    var detail = ""
}

@MainActor
final class V3SetupStore: ObservableObject {
    @Published var device = V3SetupStepState()
    @Published var pairing = V3SetupStepState()
    @Published var account = V3SetupStepState()
    @Published var network = V3SetupStepState()
    @Published var tunnel = V3SetupStepState()
    @Published var background = V3SetupStepState()
    @Published var schedule = V3SetupStepState()
    @Published var verification = V3SetupStepState()
    @Published var failureOperation = ""
    @Published var failureStage = ""
    @Published var failureCode = ""
    @Published var failureCorrelation = ""
    @Published var failureRetryable = ""
    @Published var testRunning = false
    @Published var lastVerified: Date?
    @Published var diagnostics = ""
    private var testTask: Task<Void, Never>?
    private var testRequestID: String?
    private var testRunID: String?

    private var groupDefaults: UserDefaults? {
        UserDefaults(suiteName: "group.com.SideStore.SideStore")
    }

    // Setup Complete requires every required item: pairing, signed-in account
    // with team, acceptable network and tunnel, available Background App
    // Refresh, an enabled schedule, and a test verified in this assistant
    // session. Developer Mode stays advisory and never gates.
    var isComplete: Bool {
        pairing.state == "complete" &&
        account.state == "complete" &&
        network.state == "complete" &&
        tunnel.state == "complete" &&
        background.state == "complete" &&
        schedule.state == "complete" &&
        verification.state == "complete"
    }

    func recalculate(status: V3SideStoreStatusStore) async {
        NSLog("[V3_SETUP] STATUS recalculating")
        device = V3SetupStepState(state: "complete", detail: "App running")
        if status.pairing == "Pairing file available" {
            pairing = V3SetupStepState(state: "complete", detail: "Pairing file available")
        } else {
            pairing = V3SetupStepState(state: "actionRequired", detail: "No pairing file yet")
        }
        if status.needsSignIn {
            account = V3SetupStepState(state: "actionRequired", detail: "Not signed in")
        } else if status.team == "No active team" {
            account = V3SetupStepState(state: "warning", detail: "Signed in without an active team")
        } else {
            account = V3SetupStepState(state: "complete", detail: status.account)
        }
        network = V3SetupStepState(state: "checking", detail: "Checking Wi-Fi…")
        let wifi = await LiveContainerNetworkPreflight.wifiAvailable()
        if !wifi {
            network = V3SetupStepState(state: "failed", detail: "Wi-Fi unavailable")
            tunnel = V3SetupStepState(state: "unavailable", detail: "Needs Wi-Fi first")
            NSLog("[V3_SETUP] STATUS step=network state=failed")
        } else {
            network = V3SetupStepState(state: "complete", detail: "Wi-Fi available")
            NSLog("[V3_SETUP] STATUS step=network state=ready")
            if LiveContainerNetworkPreflight.hasTunnelInterface() {
                tunnel = V3SetupStepState(state: "complete", detail: "Tunnel interface present (not a CoreDevice proof)")
                NSLog("[V3_SETUP] STATUS step=tunnel state=ready")
            } else {
                tunnel = V3SetupStepState(state: "actionRequired", detail: "Tunnel not present")
                NSLog("[V3_SETUP] STATUS step=tunnel state=action_required")
            }
        }
        switch UIApplication.shared.backgroundRefreshStatus {
        case .available:
            background = V3SetupStepState(state: "complete", detail: "Background App Refresh available")
        case .denied:
            background = V3SetupStepState(state: "warning", detail: "Background App Refresh denied")
        case .restricted:
            background = V3SetupStepState(state: "warning", detail: "Background App Refresh restricted")
        @unknown default:
            background = V3SetupStepState(state: "warning", detail: "Background App Refresh state unknown")
        }
        NSLog("[V3_SETUP] STATUS step=background state=\(background.state)")
        if let defaults = groupDefaults, defaults.bool(forKey: "liveContainerAutoRefreshEnabled") {
            let frequency = defaults.string(forKey: "liveContainerAutoRefreshFrequency") ?? "interval"
            var summary = "Scheduled refresh enabled (\(frequency))"
            if let deadline = defaults.object(forKey: "liveContainerAutoRefreshTargetDeadline") as? Date {
                summary += ", next expected " + deadline.formatted(date: .abbreviated, time: .shortened)
            }
            schedule = V3SetupStepState(state: "complete", detail: summary)
        } else {
            schedule = V3SetupStepState(state: "actionRequired", detail: "Scheduled refresh disabled")
        }
        NSLog("[V3_SETUP] STATUS step=schedule state=\(schedule.state)")
        refreshVerificationRow()
        NSLog("[V3_SETUP] STATUS step=account state=\(account.state) step=pairing state=\(pairing.state)")
    }

    private func verificationManifest() -> [String: Any]? {
        groupDefaults?.dictionary(forKey: "liveContainerAutoRefreshVerification")
    }

    private func refreshVerificationRow() {
        // History display only. A past manifest updates the timestamp row but
        // never satisfies the current setup test; only checkTestResult() may
        // mark verification complete, and only for a new fully-covered run.
        if let manifest = verificationManifest(),
           let date = manifest["date"] as? Date {
            lastVerified = date
        }
        if verification.state == "checking" {
            verification = V3SetupStepState(state: "actionRequired", detail: "No verified refresh in this session yet")
        }
    }

    func recordFailure(operation: String, stage: String, code: String, correlation: String, retryable: String) {
        failureOperation = operation
        failureStage = stage
        failureCode = code
        failureCorrelation = correlation
        failureRetryable = retryable
        NSLog("[V3_SETUP] FAILURE operation=%@ stage=%@ code=%@ correlation=%@", operation, stage, code, correlation)
    }

    func recordError(_ error: Error, operation: String) {
        if let failure = error as? CombinedFailure {
            let technical = failure.technicalDetails
            recordFailure(operation: operation, stage: failure.stage.rawValue, code: failure.code.rawValue,
                          correlation: failure.correlationID,
                          retryable: failure.retryable.map { $0 ? "true" : "false" } ?? "")
            verification = V3SetupStepState(state: "failed", detail: technical)
        } else if let native = error as NSError? {
            // No stage/code is invented for generic errors, but the available
            // domain and code travel with the message instead of being dropped.
            recordFailure(operation: operation, stage: "", code: "",
                          correlation: "", retryable: "")
            verification = V3SetupStepState(state: "failed",
                detail: error.localizedDescription + " (\(native.domain) \(native.code))")
        } else {
            verification = V3SetupStepState(state: "failed", detail: error.localizedDescription)
        }
    }

    func runTestRefresh() {
        guard !testRunning else { return }
        testRunning = true
        let requestID = UUID().uuidString
        testRequestID = requestID
        testRunID = nil
        verification = V3SetupStepState(state: "running", detail: "Test refresh running…")
        NSLog("[V3_SETUP] TEST_REFRESH_START")
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil,
                                        userInfo: ["requestID": requestID, "origin": "setupAssistant"])
        testTask = Task {
            do {
                let deadline = Date().addingTimeInterval(600)
                while !Task.isCancelled && Date() < deadline {
                    try await Task.sleep(nanoseconds: 2_000_000_000)
                    try Task.checkCancellation()
                    if await checkTestResult() { return }
                }
                if !Task.isCancelled {
                    verification = V3SetupStepState(state: "warning", detail: "No verified result yet. Check Refresh Manager for progress.")
                    NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=timeout")
                }
            } catch {
                recordError(error, operation: "refresh")
                NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=error")
            }
            testRunning = false
        }
    }

    private func checkTestResult() async -> Bool {
        guard let requestID = testRequestID,
              let ledger = groupDefaults?.dictionary(forKey: "liveContainerAutoRefreshRunLedger"),
              let runRecord = V3RefreshAllAttemptState.record(in: ledger, requestID: requestID),
              let runID = runRecord["run_id"] as? String else { return false }
        if let testRunID, testRunID != runID { return false }
        testRunID = runID
        let runState = runRecord["state"] as? String ?? ""
        guard runState == "completed" || runState == "failed" else { return false }
        let manifest = runRecord["manifest"] as? [String: Any] ?? [:]
        guard manifest["run_id"] as? String == runID,
              CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID) else {
            if runState == "failed" {
                verification = V3SetupStepState(state: "failed", detail: runRecord["message"] as? String ?? "Refresh failed")
                testRunning = false
                return true
            }
            return false
        }
        let results = manifest["results"] as? [[String: Any]] ?? []
        if runState == "completed" && results.allSatisfy({ $0["success"] as? Bool == true }) {
            verification = V3SetupStepState(state: "complete", detail: "Refresh verified")
            if let date = manifest["date"] as? Date {
                lastVerified = date
            }
        } else {
            var detail = "Refresh reported failures"
            if let failed = results.first(where: { $0["success"] as? Bool != true }) {
                recordFailure(operation: "refresh", stage: "", code: "", correlation: runID, retryable: "")
                if let message = failed["error"] as? String, !message.isEmpty {
                    detail = message
                }
                if let failure = failed["failure"] as? [String: Any] {
                    recordFailure(operation: failure["operation"] as? String ?? "refresh",
                                  stage: failure["stage"] as? String ?? "",
                                  code: failure["code"] as? String ?? "",
                                  correlation: failure["correlationID"] as? String ?? runID,
                                  retryable: (failure["retryable"] as? Bool).map { $0 ? "true" : "false" } ?? "")
                }
            }
            verification = V3SetupStepState(state: "failed", detail: detail)
        }
        testRunning = false
        if verification.state == "complete" {
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=verified")
        } else {
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=failed")
        }
        return true
    }

    func cancelTest() {
        testTask?.cancel()
        testTask = nil
        testRunning = false
        testRequestID = nil
        testRunID = nil
    }

    func buildDiagnostics(status: V3SideStoreStatusStore) {
        var lines: [String] = ["Setup Assistant"]
        lines.append("Product: " + (Bundle.main.object(forInfoDictionaryKey: "LCProductLine") as? String ?? "unknown"))
        lines.append("iOS: " + UIDevice.current.systemVersion)
        lines.append("Pairing: " + (status.pairing == "Pairing file available" ? "available" : "missing"))
        lines.append("Account: " + (status.needsSignIn ? "signed out" : "signed in"))
        lines.append("Team: " + status.team)
        lines.append("Wi-Fi: " + (network.state == "failed" ? "unavailable" : "available"))
        lines.append("VPN interface: " + (LiveContainerNetworkPreflight.hasTunnelInterface() ? "present" : "absent"))
        lines.append("CoreDevice: " + (verification.state == "complete" ? "verified" : "not checked"))
        switch UIApplication.shared.backgroundRefreshStatus {
        case .available: lines.append("Background App Refresh: available")
        case .denied: lines.append("Background App Refresh: denied")
        case .restricted: lines.append("Background App Refresh: restricted")
        @unknown default: lines.append("Background App Refresh: unknown")
        }
        lines.append("Refresh schedule: " + schedule.detail)
        if let date = lastVerified {
            lines.append("Last verified refresh: " + date.formatted(date: .abbreviated, time: .shortened))
        } else {
            lines.append("Last verified refresh: none")
        }
        if !failureOperation.isEmpty {
            lines.append("Last structured failure: operation=\(failureOperation) stage=\(failureStage) code=\(failureCode) correlation=\(failureCorrelation) retryable=\(failureRetryable)")
        }
        diagnostics = lines.joined(separator: "\n")
    }
}

struct V3SetupAssistantView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @StateObject private var setup = V3SetupStore()
    @State private var vpnWorking = false
    @State private var copiedDiagnostics = false
    var body: some View {
        List {
            Section("Device") {
                setupRow(icon: "app.badge.checkmark", title: "App Running",
                         state: setup.device, destination: nil)
                setupRow(icon: "graduationcap", title: "Developer Mode",
                         state: V3SetupStepState(state: "warning", detail: "Guidance only: keep Developer Mode on in iOS Settings. Setup continues regardless."),
                         destination: nil)
            }
            Section("Pairing") {
                setupRow(icon: "link", title: "Pairing File",
                         state: setup.pairing,
                         destination: AnyView(V3PairingView().environmentObject(status)))
            }
            Section("Apple Account") {
                setupRow(icon: "person.crop.circle", title: "Apple ID",
                         state: setup.account,
                         destination: AnyView(V3SignInView().environmentObject(status)))
            }
            Section("Network") {
                setupRow(icon: "wifi", title: "Wi-Fi",
                         state: setup.network, destination: nil)
                setupRow(icon: "network", title: "VPN Tunnel",
                         state: setup.tunnel, destination: nil)
                if setup.tunnel.state == "actionRequired" {
                    Button {
                        openLocalVPN()
                    } label: {
                        Label(vpnWorking ? "Opening LocalDevVPN…" : "Open / Enable LocalDevVPN", systemImage: "network")
                    }
                    .disabled(vpnWorking)
                }
                setupRow(icon: "cpu", title: "CoreDevice",
                         state: coredeviceState(), destination: nil)
            }
            Section("Background Refresh") {
                setupRow(icon: "clock.arrow.circlepath", title: "Background App Refresh",
                         state: setup.background, destination: nil)
                if setup.background.state == "warning" {
                    Button {
                        openSystemSettings()
                    } label: {
                        Label("Open Settings", systemImage: "gearshape")
                    }
                }
            }
            Section("Notifications") {
                Text("Refresh start, completion and deadline warnings arrive as notifications.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Button {
                    Task { await LiveContainerAutoRefreshScheduler.requestNotificationPermission() }
                } label: {
                    Label("Allow Refresh Notifications", systemImage: "bell.fill")
                }
            }
            Section("Automatic Refresh") {
                setupRow(icon: "calendar.badge.clock", title: "Schedule",
                         state: setup.schedule,
                         destination: AnyView(V3RefreshDetailView()))
            }
            Section("Verification") {
                setupRow(icon: "checkmark.seal", title: "Test Refresh",
                         state: setup.verification, destination: nil)
                if setup.verification.state == "failed" && !setup.failureOperation.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("operation=\(setup.failureOperation) stage=\(setup.failureStage) code=\(setup.failureCode)")
                            .font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                        Text("correlation=\(setup.failureCorrelation) retryable=\(setup.failureRetryable)")
                            .font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                    }
                }
                if setup.testRunning {
                    Button("Cancel Test", role: .cancel) { setup.cancelTest() }
                } else if setup.verification.state != "complete" {
                    Button {
                        setup.runTestRefresh()
                    } label: {
                        Label("Run Test Refresh", systemImage: "arrow.clockwise")
                    }
                }
                if let date = setup.lastVerified {
                    Text("Last verified " + date.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            if setup.isComplete {
                Section("Setup Complete") {
                    Label("Ready to use", systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                    Text("Account, pairing and a verified refresh are all in place.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    Button("Done") { dismiss() }
                }
            }
            Section("Diagnostics") {
                Button(copiedDiagnostics ? "Copied" : "Copy Setup Diagnostics") {
                    setup.buildDiagnostics(status: status)
                    UIPasteboard.general.string = setup.diagnostics
                    copiedDiagnostics = true
                    Task {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        copiedDiagnostics = false
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Setup Assistant")
        .task { await setup.recalculate(status: status) }
        .onChange(of: scenePhase) { phase in
            if phase == .active {
                Task { await setup.recalculate(status: status) }
            }
        }
    }
    private func coredeviceState() -> V3SetupStepState {
        if setup.verification.state == "complete" {
            return V3SetupStepState(state: "complete", detail: "Verified by successful refresh")
        }
        return V3SetupStepState(state: "unavailable", detail: "Checked after a successful refresh")
    }
    @ViewBuilder
    private func setupRow(icon: String, title: String, state: V3SetupStepState, destination: AnyView?) -> some View {
        if let destination {
            NavigationLink(destination: destination.onDisappear {
                Task { await setup.recalculate(status: status) }
            }) {
                rowContent(icon: icon, title: title, state: state, linked: true)
            }
        } else {
            rowContent(icon: icon, title: title, state: state, linked: false)
        }
    }
    private func rowContent(icon: String, title: String, state: V3SetupStepState, linked: Bool) -> some View {
        HStack(spacing: 12) {
            Image(systemName: stateIcon(state.state))
                .foregroundColor(stateColor(state.state))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                Text(state.detail.isEmpty ? stateLabel(state.state) : state.detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
            if linked {
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(title + ", " + stateLabel(state.state))
    }
    private func stateIcon(_ state: String) -> String {
        switch state {
        case "complete": return "checkmark.circle.fill"
        case "actionRequired": return "exclamationmark.circle.fill"
        case "checking", "running": return "clock.arrow.circlepath"
        case "warning": return "exclamationmark.triangle.fill"
        case "failed": return "xmark.circle.fill"
        default: return "minus.circle"
        }
    }
    private func stateColor(_ state: String) -> Color {
        switch state {
        case "complete": return .green
        case "actionRequired": return .orange
        case "warning": return .yellow
        case "failed": return .red
        default: return .secondary
        }
    }
    private func stateLabel(_ state: String) -> String {
        switch state {
        case "complete": return "Ready"
        case "actionRequired": return "Action required"
        case "checking": return "Checking"
        case "running": return "Running"
        case "warning": return "Warning"
        case "failed": return "Failed"
        default: return "Unavailable"
        }
    }
    private func openLocalVPN() {
        NSLog("[V3_SETUP] ACTION step=network action=open")
        vpnWorking = true
        defer { vpnWorking = false }
        guard UIApplication.shared.applicationState == .active,
              let scheme = UserDefaults.lcAppUrlScheme(), !scheme.isEmpty,
              var components = URLComponents(string: "localdevvpn://enable") else { return }
        components.queryItems = [URLQueryItem(name: "scheme", value: scheme)]
        if let url = components.url { UIApplication.shared.open(url) }
    }
    private func openSystemSettings() {
        NSLog("[V3_SETUP] ACTION step=background action=open-settings")
        if let url = URL(string: UIApplication.openSettingsURLString) {
            UIApplication.shared.open(url)
        }
    }
}

struct V3HomeServiceHeader: View {
    let isConnected: Bool
    let isLoading: Bool
    let onReload: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: "shippingbox.circle.fill")
                    .font(.system(size: 38))
                    .foregroundColor(.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("LiveContainer + SideStore")
                        .font(.headline)
                    HStack(spacing: 6) {
                        Circle()
                            .fill(isConnected ? Color.green : (isLoading ? Color.orange : Color.gray))
                            .frame(width: 8, height: 8)
                        Text(isConnected ? "Active & Connected" : (isLoading ? "Connecting..." : "Not Connected"))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Button(action: onReload) {
                Label {
                    Text("Reload Status")
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "arrow.triangle.2.circlepath")
                }
                .font(.caption.weight(.semibold))
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.capsule)
            .frame(maxWidth: .infinity)
            .disabled(isLoading)
            .accessibilityHint("Reloads the latest SideStore connection and account status. This does not refresh installed apps.")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 4)
    }
}

private struct V3HomeView: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @AppStorage("liveContainerAutoRefreshHealthState", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var refreshState = "UNKNOWN"
    private let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore")
    // The banner is a nudge, not acceptance: it hides only when account,
    // pairing, schedule, Background App Refresh and at least one verified
    // refresh are all in place. Acceptance itself stays in V3SetupStore.
    private var setupIncomplete: Bool {
        if status.needsSignIn || status.pairing == "Pairing file required" { return true }
        if let defaults, !defaults.bool(forKey: "liveContainerAutoRefreshEnabled") { return true }
        if UIApplication.shared.backgroundRefreshStatus != .available { return true }
        let verifiedID = defaults?.dictionary(forKey: "liveContainerAutoRefreshVerification")?["run_id"] as? String
        if verifiedID?.isEmpty != false { return true }
        return false
    }
    var body: some View {
        NavigationView {
            List {
                Section {
                    VStack(alignment: .leading, spacing: 14) {
                        V3HomeServiceHeader(isConnected: status.connected, isLoading: status.loading) {
                            status.reload()
                        }
                        
                        Divider()
                        
                        HStack(spacing: 0) {
                            Button {
                                sharedModel.selectedTab = .apps
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("\(sharedModel.apps.count)")
                                        .font(.title2.weight(.bold))
                                    Text("Guests")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .buttonStyle(.plain)

                            Divider().frame(height: 28)

                            Button {
                                sharedModel.selectedTab = .apps
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("\(status.installedAppCount)")
                                        .font(.title2.weight(.bold))
                                    Text("Sideloaded")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.leading, 12)
                            }
                            .buttonStyle(.plain)

                            Divider().frame(height: 28)

                            Button {
                                sharedModel.selectedTab = .apps
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    if let date = status.installedApps.filter({ $0.isActive }).compactMap(\.expirationDate).min() {
                                        Text(date, style: .relative)
                                            .font(.callout.weight(.bold))
                                            .foregroundColor(Calendar.current.dateComponents([.day], from: Date(), to: date).day ?? 0 <= 2 ? .red : .orange)
                                        Text("Next Expiry")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    } else {
                                        Text("-")
                                            .font(.title2.weight(.bold))
                                        Text("Next Expiry")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.leading, 12)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.vertical, 4)
                }
                
                if setupIncomplete {
                    Section {
                        Button {
                            NSLog("[V3_SETUP] OPEN source=home")
                            status.setupPresented = true
                        } label: {
                            HStack {
                                Label("Finish Setup", systemImage: "list.clipboard.fill")
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                }

                Section("Status & Identity") {
                    NavigationLink {
                        V3SignInView().environmentObject(status)
                    } label: {
                        HStack {
                            Label("Apple ID", systemImage: "person.crop.circle")
                            Spacer()
                            Text(status.account)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    NavigationLink {
                        V3DeveloperServicesView().environmentObject(status)
                    } label: {
                        HStack {
                            Label("Developer Team", systemImage: "person.2")
                            Spacer()
                            Text(status.team)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    NavigationLink {
                        V3CertificatesView().environmentObject(status)
                    } label: {
                        HStack {
                            Label("Signing Status", systemImage: "signature")
                            Spacer()
                            Text(status.signing)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    NavigationLink {
                        V3PairingView().environmentObject(status)
                    } label: {
                        HStack {
                            Label("Pairing Status", systemImage: "link")
                            Spacer()
                            Text(status.pairing)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    if let date = status.certificateExpiration {
                        NavigationLink {
                            V3CertificatesView().environmentObject(status)
                        } label: {
                            HStack {
                                Label("Certificate Expiry", systemImage: "calendar.badge.clock")
                                Spacer()
                                Text(date.formatted(date: .abbreviated, time: .shortened))
                                    .foregroundColor(.secondary)
                            }
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
                    NavigationLink(isActive: $status.refreshPresented) {
                        V3RefreshDetailView().environmentObject(status)
                    } label: {
                        Label("Open Refresh Manager", systemImage: "arrow.clockwise")
                    }
                }
                
                Section("About") {
                    Text("LiveContainer + SideStore unified build")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    if let url = URL(string: "https://github.com/NRG-Wardog") {
                        Link(destination: url) {
                            Label("NRG-Wardog on GitHub", systemImage: "link")
                        }
                    }
                    if let product = Bundle.main.object(forInfoDictionaryKey: "LCProductLine") as? String {
                        Text(product)
                            .font(.caption)
                            .foregroundColor(.secondary)
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
