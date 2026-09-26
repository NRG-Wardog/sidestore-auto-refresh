import SwiftUI
import Combine
import SideStoreSupport
import UniformTypeIdentifiers
import UIKit
import CoreFoundation
import CryptoKit
import Security

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
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3CanonicalJITLessCertificateUpdated"))) { _ in
            // V3_AWAITABLE_RELOAD_V1: a certificate import just changed
            // authoritative state. The snapshot is awaited before Setup is
            // reopened, so the assistant never recomputes JIT-Less from the
            // pre-import snapshot.
            Task {
                await status.reloadAndWait()
                if status.returnToSetupAfterJITLess {
                    status.returnToSetupAfterJITLess = false
                    status.setupPresented = true
                }
            }
        }
        .onReceive(monitor) { _ in status.reload(manual: false) }
        .onOpenURL(perform: dispatchURL)
        // V3_USER_FACING_ISSUE_V1: a source failure routes the user to Sources.
        // Without this the flag was written but never read, so the action would
        // have appeared to do nothing.
        .onChange(of: status.sourcesPresented) { presented in
            guard presented else { return }
            status.sourcesPresented = false
            sharedModel.selectedTab = .sources
        }
        .overlay(alignment: .topLeading) {
            V3InstallPickerPresenter(status: status)
                .frame(width: 1, height: 1)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .fullScreenCover(item: $status.presentation, onDismiss: {
            status.operationCoverDidDismiss()
            operationSheetDidDismiss()
        }) { request in
            V3OperationSheet(request: request).environmentObject(status)
        }
        .sheet(isPresented: $status.signInPresented, onDismiss: {
            status.reload()
            routePendingCanonicalJITLessSetup()
        }) {
            NavigationView { V3SignInView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.setupPresented, onDismiss: { routePendingCanonicalJITLessSetup() }) {
            NavigationView { V3SetupAssistantView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.connectionPresented) {
            NavigationView { V3ConnectionView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.certificatesPresented) {
            NavigationView { V3CertificatesView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.pairingPresented) {
            NavigationView { V3PairingView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .alert(status.issue?.title ?? "SideStore",
              isPresented: Binding(get: { status.error != nil },
                                   set: { if !$0 { status.clearIssue() } })) {
            // V3_USER_FACING_ISSUE_V1: the primary action is the one the typed
            // evidence supports. "Retry Connection" is only offered when the
            // failure actually implicates connection or service readiness, and
            // "Retry Source" re-requests the sources rather than reloading status.
            //
            // A plain message with no structured issue has no action to offer.
            // It used to render a button labelled "OK" that then did nothing,
            // alongside a second "OK" that dismissed, so a refusal to work
            // looked like a choice.
            if let action = status.issue?.primaryAction, action != .dismiss {
                Button(action.title) {
                    status.performPrimaryIssueAction()
                    status.clearIssue()
                }
            }
            if status.hasUncertainInstallCancellation {
                Button("Retry Cancellation") { status.retryInstallCancellation() }
            }
            Button("Copy Diagnostics") {
                UIPasteboard.general.string = status.issue?.technicalDetails ?? status.error
            }
            Button("OK", role: .cancel) { status.clearIssue() }
        } message: {
            VStack(alignment: .leading, spacing: 6) {
                Text(status.issue?.whatHappened ?? status.error ?? "")
                if let whatToDo = status.issue?.whatToDo, !whatToDo.isEmpty {
                    Text("What you can do").font(.caption.weight(.semibold))
                    Text(whatToDo).font(.caption)
                }
            }
        }
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
    private func routePendingCanonicalJITLessSetup() {
        guard status.pendingCanonicalJITLessSetup else { return }
        status.pendingCanonicalJITLessSetup = false
        sharedModel.selectedTab = .settings
        sharedModel.deepLink = URL(string: "livecontainer://jitless-setup")
    }
    private func operationSheetDidDismiss() {
        guard let destination = status.operationRecoveryDestination else { return }
        status.operationRecoveryDestination = nil
        switch destination {
        case "signIn": status.signInPresented = true
        case "certificates": status.certificatesPresented = true
        case "ipa": status.beginInstallPicker()
        case "setup": status.setupPresented = true
        case "connection": status.connectionPresented = true
        default: break
        }
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
                catch { status.present(error) }
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
                } catch { status.present(error) }
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

@MainActor
final class V3InstallPickerAnchorController: UIViewController {
    var onDidAppear: (() -> Void)?

    override func loadView() {
        let anchorView = UIView(frame: .zero)
        anchorView.backgroundColor = .clear
        anchorView.isUserInteractionEnabled = false
        view = anchorView
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        onDidAppear?()
    }
}

// The initial tap presents UIDocumentPickerViewController directly from a
// root-attached UIKit controller. The operation cover is requested only after
// UIKit reports that this picker has actually left the presentation stack.
struct V3InstallPickerPresenter: UIViewControllerRepresentable {
    @ObservedObject var status: V3SideStoreStatusStore

    func makeCoordinator() -> Coordinator { Coordinator(status: status) }

    func makeUIViewController(context: Context) -> V3InstallPickerAnchorController {
        let controller = V3InstallPickerAnchorController()
        context.coordinator.attach(controller)
        return controller
    }

    func updateUIViewController(_ controller: V3InstallPickerAnchorController, context: Context) {
        context.coordinator.update(status: status)
    }

    static func dismantleUIViewController(_ controller: V3InstallPickerAnchorController,
                                           coordinator: Coordinator) {
        coordinator.detach()
    }

    @MainActor
    final class Coordinator: NSObject, UIDocumentPickerDelegate, UIAdaptivePresentationControllerDelegate {
        private weak var anchor: V3InstallPickerAnchorController?
        private weak var status: V3SideStoreStatusStore?
        private let presentation = V3InstallPickerPresentationCoordinator()
        private var picker: UIDocumentPickerViewController?
        private var selectionStaged = false
        private var isDetaching = false

        init(status: V3SideStoreStatusStore) { self.status = status }

        func attach(_ controller: V3InstallPickerAnchorController) {
            anchor = controller
            controller.onDidAppear = { [weak self] in self?.anchorDidAppear() }
            if let status { update(status: status) }
        }

        func update(status: V3SideStoreStatusStore) {
            self.status = status
            guard let attemptID = status.installAttempt.attemptID,
                  status.installAttempt.phase == .pickerPresented,
                  presentation.phase == .idle else { return }
            let ready = anchor?.viewIfLoaded?.window != nil
            let decision = presentation.request(attemptID: attemptID,
                presenterReady: ready, presenterBusy: hasPresentedController())
            handle(decision)
        }

        func detach() {
            guard let attemptID = presentation.attemptID else { return }
            isDetaching = true
            if let picker, picker.presentingViewController != nil {
                dismissPicker(picker, attemptID: attemptID)
            } else {
                status?.cancelInstallPicker(attemptID: attemptID)
                _ = presentation.fail(attemptID: attemptID)
            }
            if presentation.phase == .idle {
                anchor?.onDidAppear = nil
                anchor = nil
                picker = nil
            }
        }

        private func anchorDidAppear() {
            if presentation.phase == .dismissing || presentation.phase == .awaitingDismissal,
               let attemptID = presentation.attemptID {
                completeDismissal(attemptID: attemptID)
                return
            }
            handle(presentation.presenterBecameReady(isBusy: hasPresentedController()))
        }

        private func handle(_ decision: V3InstallPickerPresentationCoordinator.Decision) {
            switch decision {
            case .present(let attemptID): presentPicker(attemptID: attemptID)
            case .rejected(let attemptID, let reason):
                _ = presentation.fail(attemptID: attemptID)
                status?.installPickerPresentationFailed(attemptID: attemptID, reason: reason)
            case .dismissed(let attemptID): finishDismissal(attemptID: attemptID)
            case .queued, .none: break
            }
        }

        private func presentPicker(attemptID: UUID) {
            guard let anchor, anchor.viewIfLoaded?.window != nil,
                  !hasPresentedController() else {
                _ = presentation.fail(attemptID: attemptID)
                status?.installPickerPresentationFailed(attemptID: attemptID,
                    reason: self.anchor == nil ? "presenter_unavailable" :
                        (self.anchor?.viewIfLoaded?.window == nil ? "presenter_not_in_window" : "presentation_active"))
                return
            }
            let documentPicker = UIDocumentPickerViewController(forOpeningContentTypes: [.data], asCopy: true)
            documentPicker.allowsMultipleSelection = false
            documentPicker.modalPresentationStyle = .formSheet
            documentPicker.delegate = self
            documentPicker.presentationController?.delegate = self
            picker = documentPicker
            selectionStaged = false
            status?.installPickerPresentationRequested(attemptID: attemptID)
            anchor.present(documentPicker, animated: true) { [weak self, weak documentPicker] in
                guard let self, let documentPicker else { return }
                guard self.presentation.didPresent(attemptID: attemptID) else {
                    guard self.presentation.attemptID == attemptID else { return }
                    if self.presentation.phase == .dismissing || self.presentation.phase == .awaitingDismissal {
                        documentPicker.presentationController?.delegate = self
                        self.completeDismissal(attemptID: attemptID)
                        return
                    }
                    self.presentation.fail(attemptID: attemptID)
                    self.status?.installPickerPresentationFailed(
                        attemptID: attemptID, reason: "presentation_interrupted")
                    return
                }
                guard self.anchor?.presentedViewController === documentPicker else {
                    self.presentation.fail(attemptID: attemptID)
                    self.status?.installPickerPresentationFailed(
                        attemptID: attemptID, reason: "presentation_interrupted")
                    return
                }
                documentPicker.presentationController?.delegate = self
                self.status?.installPickerDidPresent(attemptID: attemptID)
            }
        }

        func documentPicker(_ controller: UIDocumentPickerViewController,
                            didPickDocumentsAt urls: [URL]) {
            guard let attemptID = presentation.attemptID,
                  presentation.phase == .presented,
                  let url = urls.first else { return }
            selectionStaged = status?.stagePickerIPA(url, attemptID: attemptID) != nil
            dismissPicker(controller, attemptID: attemptID)
        }

        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
            guard let attemptID = presentation.attemptID else { return }
            selectionStaged = false
            dismissPicker(controller, attemptID: attemptID)
        }

        func presentationControllerDidDismiss(_ presentationController: UIPresentationController) {
            guard let attemptID = presentation.attemptID else { return }
            if presentation.phase == .presented || presentation.phase == .presenting {
                _ = presentation.beginDismissal(attemptID: attemptID)
            }
            completeDismissal(attemptID: attemptID)
        }

        private func dismissPicker(_ controller: UIDocumentPickerViewController, attemptID: UUID) {
            guard presentation.beginDismissal(attemptID: attemptID) else { return }
            if controller.isBeingDismissed {
                controller.transitionCoordinator?.animate(alongsideTransition: nil) { [weak self] _ in
                    self?.completeDismissal(attemptID: attemptID)
                }
                completeDismissal(attemptID: attemptID)
            } else if controller.presentingViewController == nil {
                completeDismissal(attemptID: attemptID)
            } else {
                controller.dismiss(animated: true) { [weak self] in
                    self?.completeDismissal(attemptID: attemptID)
                }
            }
        }

        private func completeDismissal(attemptID: UUID) {
            guard presentation.attemptID == attemptID else { return }
            let dismissed = presentation.didDismiss(attemptID: attemptID,
                presenterIsClear: !hasPresentedController())
            guard dismissed else {
                NSLog("[V3_INSTALL_UI] picker_dismiss_wait attempt=%@", attemptID.uuidString)
                return
            }
            finishDismissal(attemptID: attemptID)
        }

        private func finishDismissal(attemptID: UUID) {
            let selected = selectionStaged
            picker = nil
            selectionStaged = false
            NSLog("[V3_INSTALL_UI] picker_dismissed attempt=%@ selected=%d",
                  attemptID.uuidString, selected ? 1 : 0)
            if isDetaching {
                status?.cancelInstallPicker(attemptID: attemptID)
            } else if selected {
                status?.installPickerDidDisappear(attemptID: attemptID)
            } else {
                status?.cancelInstallPicker(attemptID: attemptID)
            }
        }

        private func hasPresentedController() -> Bool {
            var current: UIViewController? = anchor
            while let controller = current {
                if controller.presentedViewController != nil { return true }
                current = controller.parent
            }
            return false
        }
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
        // V3_REFRESH_PREREQUISITE_POLICY_V1: shared policy, evaluated before the
        // mutation request is posted. A known-missing pairing file blocks here
        // instead of surfacing later as an unexplained refresh failure.
        if let failure = V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing)
            .failure(correlationID: newRequestID) {
            attempt.failBeforeStart(message: failure.safeMessage)
            terminalFailure = V3OperationFailureDetails(failure)
            message = failure.safeMessage
            diagnostics = "schema=1\nrequest_id=\(newRequestID)\nrun_id=not_started\nstate=failed\n\(failure.technicalDetails)\nsafe_message=\(failure.safeMessage)"
            return
        }
        print("[V3_HOME_REFRESH] REQUEST request_id=\(newRequestID) origin=home health=\(health) active_run_id=\(activeRun.isEmpty ? "none" : activeRun)")
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
                "request_id=\(requestID)",
                "manual_refresh_request=\(requestID)",
                "run_id=\(runID.isEmpty ? "not_started" : runID)",
                "state=failed",
                "operation=refresh",
                "stage=refreshVerification",
                "code=unknown",
                "source_step=unknown",
                "correlation=\(runID.isEmpty ? "unknown" : runID)",
                "underlying_domain=redacted",
                "underlying_code=unknown",
                "retryable=unknown",
                "safe_cause=unknown",
                "origin=unknown",
                "network_preflight=unknown",
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
        case "ipa": status.beginInstallPicker()
        case "setup": status.setupPresented = true
        case "connection": status.connectionPresented = true
        case "pairing": status.pairingPresented = true
        default: break
        }
    }
}

struct V3InstallButton: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    var body: some View {
        Button("Install with SideStore", systemImage: "arrow.down.app") {
            status.beginInstallPicker()
        }
        .accessibilityLabel("Install / Sideload App with SideStore")
        .accessibilityHint("Choose an IPA to sign and install as an iOS app.")
    }
}

struct V3OperationRequest: Identifiable {
    let id: UUID
    let operation: String
    let target: String
    let title: String
    let installAttemptID: UUID?

    init(id: UUID = UUID(), operation: String, target: String, title: String,
         installAttemptID: UUID? = nil) {
        self.id = id
        self.operation = operation
        self.target = target
        self.title = title
        self.installAttemptID = installAttemptID
    }
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
    // V3_AUTH_SESSION_SNAPSHOT_V1: the service reports the authenticated Apple
    // session separately from the active account row, because authentication
    // completes before provisioning activates that row.
    @Published private(set) var authenticated = false
    @Published private(set) var provisioningIncomplete = false
    // V3_SETUP_COMPLETION_POLICY_V1: the last authoritative Wi-Fi observation.
    // Probing Wi-Fi is async, so Home reads this cache instead of guessing.
    // nil means "not observed yet", which counts as outstanding. It is written
    // only through recordWifiAvailability so the cached fact has one owner.
    @Published private(set) var wifiAvailable: Bool?

    /// Records the authoritative Wi-Fi observation for the shared setup policy.
    func recordWifiAvailability(_ available: Bool) {
        wifiAvailable = available
    }

    // V3_SHARED_JITLESS_FACT_V1: the last authoritative JIT-Less readiness.
    // Health, Certificates and the Setup Assistant all observe it, and Home
    // reads it, so no surface can claim a different completion answer.
    // nil means "not observed yet" and counts as outstanding.
    @Published private(set) var jitlessReadiness: V3JITLessReadiness?

    /// Publishes an observed JIT-Less readiness for every setup surface to share.
    func recordJITLessReadiness(_ readiness: V3JITLessReadiness) {
        jitlessReadiness = readiness
    }
    @Published private(set) var updatedAt: Date?
    @Published private(set) var installedApps: [V3SideStoreApp] = []
    @Published private(set) var sources: [V3SideStoreSource] = []
    @Published private(set) var settings: [String: Bool] = [:]
    @Published var error: String?
    // V3_USER_FACING_ISSUE_V1: the structured issue behind the global alert.
    // The string form is retained for compatibility and copyable summaries, but
    // actions are chosen from the typed issue, never from the string.
    @Published private(set) var issue: V3UserFacingIssue?
    @Published var notice: String?

    /// Presents a failure with the action its typed evidence supports.
    func present(_ error: Error) {
        if let combined = error as? CombinedFailure {
            let structured = V3UserFacingIssue.make(combined)
            issue = structured
            self.error = structured.summary
        } else {
            // V3_FAILURE_GUIDANCE_V1: an untyped failure still gets guidance, and
            // still never claims a connection problem without evidence. The raw
            // description is not shown to the user, because for a bridged NSError
            // it is a numeric domain and code; it is kept in the diagnostics the
            // user can copy instead.
            let structured = V3UserFacingIssue.make(
                operation: "command", stage: CombinedFailure.Stage.command.rawValue,
                code: CombinedFailure.Code.failed.rawValue, safeCause: nil, sourceStep: nil,
                retryable: nil,
                whatHappened: "That action did not complete.",
                whatToDo: V3FailureGuidance.message(error),
                technicalDetails: V3FailureGuidance.diagnostics(error))
            issue = structured
            self.error = structured.summary
        }
    }

    func clearIssue() {
        issue = nil
        error = nil
    }

    /// Opens the destination an issue's primary action points at.
    func openIssueRecovery() {
        guard let destination = issue?.recoveryDestination else { return }
        switch destination {
        case "signIn": signInPresented = true
        case "certificates": certificatesPresented = true
        case "ipa": beginInstallPicker()
        case "setup": setupPresented = true
        case "connection": connectionPresented = true
        case "pairing": pairingPresented = true
        case "sources": sourcesPresented = true
        default: break
        }
    }

    /// Runs the primary action the typed evidence selected.
    ///
    /// The two retry actions are deliberately different. A connection failure is
    /// re-observed by reloading status. A source failure must re-request the
    /// sources themselves, because reloading status does not re-fetch a manifest
    /// and would leave the user looking at the same empty or stale catalog while
    /// the button claims it retried.
    func performPrimaryIssueAction() {
        guard let action = issue?.primaryAction else { return }
        switch action {
        case .retryConnection:
            reload()
        case .retrySource:
            refreshSources()
        default:
            openIssueRecovery()
        }
    }

    @Published var presentation: V3OperationRequest? {
        didSet {
            // A presented operation owns the state a snapshot would report, so a
            // deferred snapshot is owed until it ends. Draining here is what
            // guarantees a parked continuation is resumed rather than stranded.
            if presentation == nil { drainOwedSnapshot() }
        }
    }
    @Published var sourceURL = ""
    @Published var refreshTarget: String?
    @Published var refreshPresented = false
    @Published var signInPresented = false
    @Published var setupPresented = false
    @Published var pendingCanonicalJITLessSetup = false
    @Published var returnToSetupAfterJITLess = false
    @Published var connectionPresented = false
    @Published var certificatesPresented = false
    @Published var pairingPresented = false
    // V3_USER_FACING_ISSUE_V1: a source failure routes back to Sources.
    @Published var sourcesPresented = false
    @Published var operationRecoveryDestination: String?
    // V3_LOAD_ACTIVITY_OWNERSHIP_V1: `loading` keeps its user-facing meaning of
    // "the service is busy", but it is now derived from a named activity so the
    // snapshot gate can tell a snapshot from a mutation. Five of the six
    // activities that used to set this flag were mutations.
    @Published private(set) var loading = false
    @Published private(set) var connected = false
    @Published private(set) var requiresConnectionRetry = false
    // The activity that currently owns the service. Only `.snapshot` may resolve
    // a snapshot waiter.
    private var loadActivity: V3LoadActivity = .idle
    // At most one snapshot is owed, because at most one can be pending. Starting
    // any snapshot discharges it, so an unrelated reload can never leave a stale
    // intent behind to cause a second fetch.
    private var snapshotOwed = false
    // Callers awaiting an authoritative snapshot. Each carries whether it needs
    // a manual snapshot, because the owed drain is shared and a non-manual
    // monitor tick must not discharge a caller's manual requirement.
    private struct SnapshotWaiter {
        let manual: Bool
        let continuation: CheckedContinuation<V3ReloadOutcome, Never>
    }
    private var snapshotWaiters: [SnapshotWaiter] = []
    private var pendingPickerError: (attemptID: UUID, message: String)?
    @Published private(set) var installAttempt = V3InstallAttemptState()
    var installedAppCount: Int { installedApps.count }
    var hasUncertainInstallCancellation: Bool {
        installAttempt.backendSessionID != nil &&
            (installAttempt.phase == .operationStarted || installAttempt.phase == .operationPresented)
    }
    var isStale: Bool { !connected || (updatedAt.map { Date().timeIntervalSince($0) > 120 } ?? true) }
    var needsSignIn: Bool { account == "Not signed in" && !authenticated }
    // V3_AWAITABLE_RELOAD_V1 / V3_LOAD_ACTIVITY_OWNERSHIP_V1
    // reload() is fire-and-forget: it starts the snapshot and continues
    // immediately, so any code that reads status right after it sees the
    // PREVIOUS snapshot. reloadAndWait() completes only after an authoritative
    // snapshot has been applied, which is what callers that depend on ordering
    // must use. No delay or sleep is involved: the caller awaits the real
    // snapshot.
    func reload(manual: Bool = true) {
        switch beginSnapshot(manual: manual) {
        case .performSnapshot:
            Task { _ = await performSnapshot() }
        case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation:
            // The request is remembered and satisfied by the drain once the
            // blocking activity ends. No continuation is parked, because this
            // caller does not wait.
            break
        case .doNotObserve:
            break
        }
    }

    /// The result of awaiting an authoritative snapshot.
    ///
    /// A Bool could not distinguish "the snapshot ran and failed" from "no
    /// snapshot ran at all", so a caller that recomputes derived state could be
    /// told it had fresh state when it had been handed the previous snapshot
    /// unchanged.
    enum V3ReloadOutcome: Equatable {
        /// A snapshot was performed and accepted.
        case applied
        /// A snapshot was performed and failed.
        case snapshotFailed
        /// No snapshot was performed. A caller that recomputes derived state
        /// must treat this as "unknown", never as "up to date".
        case notObserved
    }

    /// Performs one authoritative snapshot and returns only once the resulting
    /// state has been applied.
    ///
    /// If a snapshot is already in flight this joins that one. If a mutation is
    /// in flight it waits for a snapshot performed after that mutation, because
    /// a mutation's completion says nothing about authoritative status. If a
    /// presented operation owns the state it waits for the deferred snapshot. In
    /// every parked case only a snapshot completion resumes the caller.
    @discardableResult
    func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome {
        switch beginSnapshot(manual: manual) {
        case .performSnapshot:
            return await performSnapshot()
        case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation:
            // Parked. There is no suspension between the gate decision and this
            // append, so a snapshot finishing in between cannot be missed and a
            // continuation cannot be left stranded.
            return await withCheckedContinuation { continuation in
                snapshotWaiters.append(SnapshotWaiter(manual: manual, continuation: continuation))
            }
        case .doNotObserve:
            // Nothing ran and nothing is owed, so no continuation is parked.
            return .notObserved
        }
    }

    /// The shared synchronous gate. It names the activity instead of inferring
    /// one from a shared busy flag, and it is the only place a snapshot is
    /// started or a waiter is parked.
    private func beginSnapshot(manual: Bool) -> V3SnapshotDecision {
        let decision = V3SnapshotGate.decide(
            activity: loadActivity, presentationActive: presentation != nil,
            manual: manual, requiresConnectionRetry: requiresConnectionRetry)
        switch decision {
        case .performSnapshot:
            startSnapshot(manual: manual)
        case .joinSnapshot:
            break
        case .awaitMutationThenSnapshot, .deferForPresentation:
            // A snapshot is owed. It is owed once, not once per requester, so a
            // burst of requests cannot queue a burst of fetches.
            snapshotOwed = true
        case .doNotObserve:
            break
        }
        return decision
    }

    /// Claims the service for a snapshot. Starting any snapshot discharges the
    /// owed intent, which is what stops an unrelated reload from leaving a stale
    /// request behind to cause a second fetch.
    private func startSnapshot(manual: Bool) {
        snapshotOwed = false
        if manual { requiresConnectionRetry = false }
        loadActivity = .snapshot
        loading = true
        if installAttempt.hasActiveAttempt {
            NSLog("[V3_INSTALL_STATE] attempt=%@ event=snapshot_started phase=%@",
                  installAttempt.attemptID?.uuidString ?? "none", installAttempt.phase.rawValue)
        }
    }

    /// Claims the service for a mutation. A mutation never resolves a snapshot
    /// waiter; it only makes an owed snapshot due.
    private func beginMutation() {
        loadActivity = .mutation
        loading = true
    }

    private func performSnapshot() async -> V3ReloadOutcome {
        var succeeded = false
        do {
            accept(try await V3ServiceBridge.shared.request(operation: "snapshot"))
            succeeded = true
        } catch {
            connected = false
            requiresConnectionRetry = true
            present(error)
        }
        let outcome: V3ReloadOutcome = succeeded ? .applied : .snapshotFailed
        // The only place a snapshot waiter is ever resumed. State is fully
        // applied first, so no caller can observe a partially updated snapshot.
        finishSnapshot(outcome: outcome)
        if installAttempt.hasActiveAttempt {
            NSLog("[V3_INSTALL_STATE] attempt=%@ event=snapshot_finished phase=%@",
                  installAttempt.attemptID?.uuidString ?? "none", installAttempt.phase.rawValue)
        }
        drainInstallPresentation(trigger: "snapshot_finished")
        return outcome
    }

    /// V3_AWAITABLE_RELOAD_V1: the single place a snapshot activity ends.
    private func finishSnapshot(outcome: V3ReloadOutcome) {
        loadActivity = .idle
        loading = false
        // The install presentation gate is snapshot-scoped: it advances only once
        // authoritative state has landed. A mutation advancing it would claim a
        // snapshot had happened.
        installAttempt.reloadFinished()
        let waiting = snapshotWaiters
        snapshotWaiters.removeAll()
        for waiter in waiting { waiter.continuation.resume(returning: outcome) }
        drainOwedSnapshot()
    }

    /// V3_LOAD_ACTIVITY_OWNERSHIP_V1: the single place a mutation activity ends.
    /// It resolves nothing. A caller awaiting authoritative status stays parked
    /// until a real snapshot completes, because a mutation's reply says nothing
    /// about the state a snapshot reports.
    private func finishMutation() {
        loadActivity = .idle
        loading = false
        drainOwedSnapshot()
    }

    /// Runs the single owed snapshot once nothing blocks it.
    ///
    /// The decision is total. If policy refuses the snapshot, every parked
    /// continuation is resumed with `.notObserved` rather than left suspended,
    /// which is what let a non-manual deferred reload hang a task forever.
    private func drainOwedSnapshot() {
        let needsManual = snapshotWaiters.contains { $0.manual }
        switch V3SnapshotGate.drain(activity: loadActivity, presentationActive: presentation != nil,
                                    owed: snapshotOwed, anyWaiterNeedsManual: needsManual,
                                    requiresConnectionRetry: requiresConnectionRetry) {
        case .performSnapshot:
            startSnapshot(manual: needsManual || !requiresConnectionRetry)
            Task { _ = await performSnapshot() }
        case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation:
            // Still blocked. The owed intent is kept for whoever ends it.
            break
        case .doNotObserve:
            guard snapshotOwed else { return }
            snapshotOwed = false
            let waiting = snapshotWaiters
            snapshotWaiters.removeAll()
            for waiter in waiting { waiter.continuation.resume(returning: .notObserved) }
        }
    }
    func accept(_ snapshot: [String: Any]) {
        account = snapshot["account"] as? String ?? "Not signed in"
        team = snapshot["team"] as? String ?? "No active team"
        signing = snapshot["signing"] as? String ?? "Unknown"
        certificate = snapshot["certificate"] as? String ?? "Unknown"
        certificateExpiration = (snapshot["certificateExpiration"] as? Date).flatMap { $0 == .distantPast ? nil : $0 }
        pairing = snapshot["pairing"] as? String ?? "Unknown"
        authenticated = snapshot["authenticated"] as? Bool ?? false
        provisioningIncomplete = snapshot["provisioningIncomplete"] as? Bool ?? false
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
        guard presentation == nil, !installAttempt.hasActiveAttempt else {
            self.error = "Another operation is already running. Finish or cancel it before starting a new one."
            return
        }
        guard loadActivity == .idle else {
            presentBusy()
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
            let request = V3OperationRequest(operation: operation, target: target, title: title)
            presentation = request
        default: break
        }
    }
    private func needsSignIn(_ error: Error) -> Bool {
        (error as? CombinedFailure)?.stage == .authentication
    }
    private func failed(_ error: Error) {
        if needsSignIn(error) { signInPresented = true }
        else { present(error) }
    }

    /// V3_LOAD_ACTIVITY_OWNERSHIP_V1: a busy store explains itself.
    ///
    /// refreshSources() was reachable from the global alert's "Retry Source"
    /// action, where a silent guard produced no work, no message, and a
    /// dismissed alert: the user was told a source retry had happened when
    /// nothing had been requested. Every entry point now reports the conflict.
    private func presentBusy() {
        self.error = "SideStore is still loading. Wait for the current request to finish, then try again."
    }

    /// Runs one service mutation under an explicit mutation activity.
    ///
    /// A mutation owns the service but is not a snapshot, so it must never
    /// resolve a caller awaiting authoritative status. A snapshot is requested
    /// after it; a caller already parked is released by that snapshot, not by
    /// this one. The trailing reload is a plain request, so if the drain already
    /// started the owed snapshot this joins it instead of fetching twice.
    private func runMutation(_ operation: String, target: String = "", successNotice: String) {
        guard loadActivity == .idle else {
            presentBusy()
            return
        }
        beginMutation()
        Task {
            do {
                accept(try await V3ServiceBridge.shared.request(operation: operation, target: target))
                finishMutation()
                notice = successNotice
                reload()
            } catch {
                finishMutation()
                failed(error)
            }
        }
    }
    func signOut() { runMutation("signOut", successNotice: "Signed out successfully.") }
    func jit(target: String) { runMutation("jit", target: target, successNotice: "JIT enabled.") }
    func syncAppIDs() { runMutation("syncAppIDs", successNotice: "App IDs synced.") }
    func clearCache() { runMutation("clearCache", successNotice: "Download cache cleared.") }
    func refreshSources() { runMutation("refreshSources", successNotice: "Sources updated.") }
    func stageSharedFile(_ data: Data) -> String? {
        guard !data.isEmpty, data.count <= 4_194_304 else {
            self.error = "The selected file is empty or too large to hand to the SideStore service."
            return nil
        }
        let token = UUID().uuidString
        LCUtils.appGroupUserDefault.set(data, forKey: "V3SharedFile." + token)
        return token
    }
    func beginInstallPicker() {
        NSLog("[V3_INSTALL_UI] tap")
        guard presentation == nil else {
            NSLog("[V3_INSTALL_UI] tap_rejected reason=presentation_active")
            error = "Another operation is already running. Finish or cancel it before installing another app."
            return
        }
        guard !installAttempt.hasActiveAttempt else {
            NSLog("[V3_INSTALL_UI] tap_rejected reason=attempt_not_idle phase=%@",
                  installAttempt.phase.rawValue)
            error = hasUncertainInstallCancellation
                ? "SideStore has not confirmed that the previous install stopped. No new install was started; retry cancellation."
                : "An install attempt is still being resolved. Wait for it to finish, then try again."
            return
        }
        guard let attemptID = installAttempt.beginPicker() else {
            NSLog("[V3_INSTALL_UI] tap_rejected reason=attempt_not_idle phase=%@",
                  installAttempt.phase.rawValue)
            error = "An install attempt is still being resolved. Wait for it to finish, then try again."
            return
        }
        operationRecoveryDestination = nil
        NSLog("[V3_INSTALL_UI] begin_attempt result=started attempt=%@ loading=%d",
              attemptID.uuidString, loading ? 1 : 0)
    }

    func installPickerPresentationRequested(attemptID: UUID) {
        NSLog("[V3_INSTALL_UI] picker_present_requested attempt=%@", attemptID.uuidString)
    }

    func installPickerDidPresent(attemptID: UUID) {
        NSLog("[V3_INSTALL_UI] picker_did_present attempt=%@", attemptID.uuidString)
    }

    func installPickerPresentationFailed(attemptID: UUID, reason: String) {
        NSLog("[V3_INSTALL_UI] tap_rejected reason=%@ attempt=%@", reason, attemptID.uuidString)
        NSLog("[V3_INSTALL_UI] picker_present_failed attempt=%@ reason=%@",
              attemptID.uuidString, reason)
        let token = resetInstallUI(attemptID: attemptID, outcome: "picker_presentation_failed")
        if let token { Task { _ = await cleanupStagedIPA(token) } }
        error = "The IPA picker could not be opened. Tap Install / Sideload App to try again."
    }

    func cancelInstallPicker(attemptID: UUID) {
        if let pending = pendingPickerError, pending.attemptID == attemptID {
            pendingPickerError = nil
            NSLog("[V3_INSTALL_UI] terminal attempt=%@ outcome=staging_failed", attemptID.uuidString)
            error = pending.message
            return
        }
        guard installAttempt.attemptID == attemptID else { return }
        let token = resetInstallUI(attemptID: attemptID, outcome: "picker_cancelled")
        if let token { Task { _ = await cleanupStagedIPA(token) } }
    }

    @discardableResult
    func stagePickerIPA(_ url: URL, attemptID: UUID) -> String? {
        NSLog("[V3_INSTALL_UI] picker_selected attempt=%@", attemptID.uuidString)
        guard installAttempt.beginStaging(attemptID: attemptID) else {
            NSLog("[V3_INSTALL_UI] picker_selection_rejected attempt=%@ reason=stale_attempt",
                  attemptID.uuidString)
            return nil
        }
        NSLog("[V3_INSTALL_STATE] attempt=%@ event=staging_started", attemptID.uuidString)
        return stageIPA(url, attemptID: attemptID, bookmark: nil,
                        title: "Install / Sideload App with SideStore",
                        waitsForPickerDismissal: true)
    }

    @discardableResult
    func stageSharedIPA(_ url: URL, bookmark: Data? = nil, title: String) -> String? {
        guard presentation == nil, !installAttempt.hasActiveAttempt,
              let attemptID = installAttempt.beginDirectStaging() else {
            error = "Another operation is already running. Finish or cancel it before installing another app."
            return nil
        }
        return stageIPA(url, attemptID: attemptID, bookmark: bookmark, title: title,
                        waitsForPickerDismissal: false)
    }

    private func stageIPA(_ url: URL, attemptID: UUID, bookmark: Data?, title: String,
                          waitsForPickerDismissal: Bool) -> String? {
        do {
            guard let container = LCSharedUtils.appGroupPath() else { throw CombinedIPAFileError(.fileAccess) }
            let token = try V3IPAStaging.stage(sourceURL: url, bookmark: bookmark, containerRoot: container)
            guard installAttempt.staged(attemptID: attemptID, token: token, title: title,
                                        waitsForPickerDismissal: waitsForPickerDismissal,
                                        isLoading: loading) else {
                _ = resetInstallUI(attemptID: attemptID, outcome: "stage_handoff_failed")
                Task { _ = await cleanupStagedIPA(token) }
                let message = "The selected IPA could not be queued for presentation. Choose it again."
                if waitsForPickerDismissal { pendingPickerError = (attemptID, message) }
                else { error = message }
                return nil
            }
            NSLog("[V3_INSTALL_UI] staged attempt=%@ phase=%@ loading=%d",
                  attemptID.uuidString, installAttempt.phase.rawValue, loading ? 1 : 0)
            if !waitsForPickerDismissal {
                drainInstallPresentation(trigger: "input_staged")
            }
            return token
        } catch let failure as CombinedIPAFileError {
            _ = resetInstallUI(attemptID: attemptID, outcome: "staging_failed")
            if waitsForPickerDismissal { pendingPickerError = (attemptID, V3FailureGuidance.message(failure)) }
            else { self.error = V3FailureGuidance.message(failure) }
        } catch {
            _ = resetInstallUI(attemptID: attemptID, outcome: "staging_failed")
            let message = CombinedIPAFileError(.stagingFailed).localizedDescription
            if waitsForPickerDismissal { pendingPickerError = (attemptID, message) }
            else { self.error = message }
        }
        return nil
    }

    private func drainInstallPresentation(trigger: String) {
        guard let request = installAttempt.takeReadyOperation(
            isLoading: loading, hasActiveOperationPresentation: presentation != nil
        ) else {
            if installAttempt.hasActiveAttempt {
                NSLog("[V3_INSTALL_UI] operation_present_wait trigger=%@ phase=%@ loading=%d presentation_active=%d",
                      trigger, installAttempt.phase.rawValue, loading ? 1 : 0,
                      presentation == nil ? 0 : 1)
            }
            return
        }
        NSLog("[V3_INSTALL_UI] host_cover_request attempt=%@ operation=%@ trigger=%@",
              request.attemptID.uuidString, request.operationID.uuidString, trigger)
        presentation = V3OperationRequest(id: request.operationID, operation: "installSharedIPA",
            target: request.token, title: request.title, installAttemptID: request.attemptID)
        NSLog("[V3_INSTALL_UI] operation_present_requested attempt=%@ operation=%@",
              request.attemptID.uuidString, request.operationID.uuidString)
    }

    func installPickerDidDisappear(attemptID: UUID) {
        guard installAttempt.pickerDidDisappear(attemptID: attemptID, isLoading: loading) else { return }
        NSLog("[V3_INSTALL_UI] picker_dismissed attempt=%@ loading=%d",
              attemptID.uuidString, loading ? 1 : 0)
        drainInstallPresentation(trigger: "picker_did_dismiss")
    }

    func installBackendStartRequested(attemptID: UUID?, operationID: UUID, sessionID: String) {
        guard let attemptID,
              installAttempt.backendStartRequested(attemptID: attemptID,
                  operationID: operationID, sessionID: sessionID) else { return }
        NSLog("[V3_INSTALL_STATE] attempt=%@ event=backend_start_requested session=%@",
              attemptID.uuidString, sessionID)
    }

    func installOperationDidPresent(attemptID: UUID?, operationID: UUID) {
        guard let attemptID,
              installAttempt.markOperationViewDidAppear(attemptID: attemptID, operationID: operationID) else { return }
        NSLog("[V3_INSTALL_UI] operation_did_present attempt=%@ operation=%@",
              attemptID.uuidString, operationID.uuidString)
    }

    func installBackendStarted(attemptID: UUID?, operationID: UUID, sessionID: String) {
        guard let attemptID,
              installAttempt.backendStarted(attemptID: attemptID, operationID: operationID, sessionID: sessionID) else { return }
        NSLog("[V3_INSTALL_STATE] attempt=%@ phase=operationStarted backend_session=%@",
              attemptID.uuidString, sessionID)
    }

    func installTerminal(attemptID: UUID?, operationID: UUID, outcome: String) {
        guard let attemptID,
              installAttempt.recordTerminal(attemptID: attemptID, operationID: operationID, outcome: outcome) else { return }
        NSLog("[V3_INSTALL_UI] terminal attempt=%@ operation=%@ outcome=%@",
              attemptID.uuidString, operationID.uuidString, outcome)
    }

    func prepareInstallRetry(attemptID: UUID?) {
        guard let attemptID, let operationID = presentation?.id,
              installAttempt.prepareRetry(attemptID: attemptID, operationID: operationID) else { return }
        NSLog("[V3_INSTALL_UI] retry_started attempt=%@ operation=%@",
              attemptID.uuidString, operationID.uuidString)
    }

    @discardableResult
    func resetInstallUI(attemptID: UUID, outcome: String,
                        preserveRecoveryDestination: Bool = false) -> String? {
        guard installAttempt.attemptID == attemptID else { return nil }
        let token = installAttempt.token
        switch installAttempt.phase {
        case .terminal:
            guard installAttempt.beginCleanup(attemptID: attemptID),
                  installAttempt.finishCleanup(attemptID: attemptID) else { return nil }
        case .cleaningUp:
            guard installAttempt.finishCleanup(attemptID: attemptID) else { return nil }
        default:
            guard installAttempt.resetBeforeBackend(attemptID: attemptID) else { return nil }
        }
        if presentation?.installAttemptID == attemptID { presentation = nil }
        if pendingPickerError?.attemptID == attemptID { pendingPickerError = nil }
        if !preserveRecoveryDestination { operationRecoveryDestination = nil }
        NSLog("[V3_INSTALL_UI] reset_to_idle attempt=%@ outcome=%@",
              attemptID.uuidString, outcome)
        return token
    }

    func operationCoverDidDismiss() {
        guard let attemptID = installAttempt.attemptID,
              installAttempt.phase == .operationPresented,
              !installAttempt.operationViewDidAppear,
              installAttempt.backendSessionID == nil else { return }
        let token = resetInstallUI(attemptID: attemptID, outcome: "operation_presentation_failed")
        error = "The install screen could not be opened. The attempt was cleared; tap Install / Sideload App again."
        if let token { Task { _ = await cleanupStagedIPA(token) } }
    }

    func retryInstallCancellation() {
        guard let attemptID = installAttempt.attemptID,
              let operationID = installAttempt.operationID,
              let sessionID = installAttempt.backendSessionID else {
            error = "No install session is available to cancel. Keep this screen open and reload operation status."
            return
        }
        Task { @MainActor in
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "opCancel", target: sessionID)
                _ = installAttempt.recordTerminal(attemptID: attemptID,
                    operationID: operationID, outcome: "cancelled")
                self.error = nil
                let token = resetInstallUI(attemptID: attemptID, outcome: "cancelled")
                if let token { _ = await cleanupStagedIPA(token) }
                reload()
            } catch {
                NSLog("[V3_INSTALL_UI] cancellation_unconfirmed attempt=%@ session=%@",
                      attemptID.uuidString, sessionID)
                self.error = "SideStore still cannot confirm that the install stopped. No new install was started. Reconnect, then retry cancellation."
            }
        }
    }

    func cleanupStagedIPA(_ token: String) async -> Bool {
        do {
            _ = try await V3ServiceBridge.shared.request(operation: "ipaCleanup", target: token)
            return true
        } catch {
            // The host uses the same canonical UUID-only staging helper for a
            // local fallback. It never accepts or constructs a caller path.
            do {
                guard let container = LCSharedUtils.appGroupPath() else {
                    throw CombinedIPAFileError(.fileAccess)
                }
                try V3IPAStaging.cleanup(token: token, containerRoot: container)
                NSLog("[V3_INSTALL_UI] staged_cleanup_fallback result=success")
                return true
            } catch {
                NSLog("[V3_INSTALL_UI] staged_cleanup_failed reason=service_and_local_cleanup_unavailable")
                return false
            }
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
    @State private var sourceFailure: V3SourceAddFailure?
    // V3_SOURCE_SEMANTIC_STATE_V1: a successful add is a success, and is never
    // rendered with the same neutral grey as an informational note.
    @State private var addSucceeded = false
    @State private var removeCandidate: V3SideStoreSource?
    // V3_SOURCE_KEYBOARD_DISMISS_V1 (issue #40): the field had no focus state and
    // no explicit dismiss action, so the only way out of the keyboard felt like
    // Return, which read as if Return were also the submit action.
    @FocusState private var sourceFieldFocused: Bool
    // @State so the pre-edit value survives; the view is a struct, so a plain
    // stored var could not be assigned from a non-mutating method.
    @State private var sourceURLBeforeEditing: String = ""
    private var savedGuestSources: [String] {
        (UserDefaults.standard.stringArray(forKey: "LCAltStoreSourceURLs") ?? [])
            .filter { saved in !status.sources.contains(where: { $0.url == saved }) }
    }
    var body: some View {
        NavigationView {
            List {
                if addSucceeded && !notice.isEmpty {
                    Section {
                        Label(notice, systemImage: V3StatusSeverity.completed.icon)
                            .font(.footnote)
                            .foregroundColor(.green)
                    }
                } else if !notice.isEmpty {
                    Section {
                        // V3_SOURCE_SEMANTIC_STATE_V1: an informational notice is
                        // neutral, never styled as if it were a result.
                        Label(notice, systemImage: "info.circle.fill")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
                }
                if let sourceFailure {
                    Section("What happened") {
                        // A source failure is a failure, and says so.
                        Label(sourceFailure.whatHappened, systemImage: V3StatusSeverity.failed.icon)
                            .font(.footnote)
                            .foregroundColor(.red)
                    }
                    Section("What you can do") {
                        Text(sourceFailure.whatToDo).font(.footnote)
                    }
                    Section {
                        DisclosureGroup("Technical details") {
                            Text(sourceFailure.technicalDetails)
                                .font(.caption2)
                                .textSelection(.enabled)
                        }
                        Button("Copy Diagnostics") {
                            UIPasteboard.general.string = sourceFailure.technicalDetails
                        }
                        .font(.caption)
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
                            .focused($sourceFieldFocused)
                            // Return only dismisses the keyboard. It never
                            // previews and never adds a source.
                            .submitLabel(.done)
                            .onSubmit { dismissKeyboard() }
                            // The pre-edit value is captured when editing actually
                            // begins, which is focus. It used to be captured when a
                            // preview was requested, so a Cancel after typing but
                            // before previewing restored the wrong value, and a
                            // Cancel after previewing restored the value that was
                            // already on screen. Only the rising edge captures,
                            // because Cancel itself drops focus and must not
                            // overwrite the value it is about to restore.
                            .onChange(of: sourceFieldFocused) { focused in
                                if focused { sourceURLBeforeEditing = status.sourceURL }
                            }
                    }
                    // Explicit keyboard dismissal, with an explicit Cancel that
                    // performs no preview, no network request and no persistence.
                    .toolbar {
                        ToolbarItemGroup(placement: .keyboard) {
                            Spacer()
                            Button("Cancel") { cancelSourceEditing() }
                            Button("Done") { dismissKeyboard() }
                        }
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
    // V3_SOURCE_KEYBOARD_DISMISS_V1: dismissing the keyboard is a pure UI action.
    // It previews nothing, requests nothing and persists nothing.
    private func dismissKeyboard() {
        sourceFieldFocused = false
    }

    /// Cancel restores the URL that was present when editing began and dismisses
    /// the keyboard. It performs no preview, no network request, and no source
    /// persistence. The pre-edit value is restored rather than clearing the
    /// field, so a URL the user may want to keep is never silently discarded and
    /// a later focus always starts from the same predictable value.
    private func cancelSourceEditing() {
        status.sourceURL = V3SourceEditingPolicy.resolved(
            V3SourceEditingPolicy.cancel(typed: status.sourceURL, beforeEditing: sourceURLBeforeEditing),
            typed: status.sourceURL)
        sourceFieldFocused = false
        preview = nil
    }

    private func previewSource() async {
        previewBusy = true
        defer { previewBusy = false }
        sourceFailure = nil
        notice = ""
        addSucceeded = false
        do {
            var row = try await V3ServiceBridge.shared.request(operation: "sourcePreview", target: status.sourceURL)
            row["url"] = status.sourceURL
            preview = row
            // Previewing is an explicit action, so the keyboard has served its
            // purpose once the preview is on screen.
            dismissKeyboard()
        } catch { sourceFailure = V3SourceAddFailure(error) }
    }
    private func confirmAdd(url: String) async {
        addBusy = true
        notice = ""
        sourceFailure = nil
        defer { addBusy = false }
        do {
            let result = try await V3ServiceBridge.shared.request(operation: "sourceAddConfirmed", target: url)
            guard let message = V3SourceAddPersistencePolicy.confirmationMessage(result),
                  let identifier = result["identifier"] as? String,
                  let sources = result["sources"] as? [[String: Any]],
                  sources.contains(where: { $0["identifier"] as? String == identifier }) else {
                throw CombinedFailure(operation: "sourceAddConfirmed", stage: .command,
                    code: .invalidResponse, id: UUID().uuidString, retryable: false)
            }
            status.accept(result)
            preview = nil
            status.sourceURL = ""
            notice = message
            addSucceeded = true
        } catch { sourceFailure = V3SourceAddFailure(error) }
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
        } catch { status.present(error) }
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
    @State private var failure: V3OperationFailureDetails?
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
                Section("What happened") {
                    Text(failure?.whatHappened ?? error).font(.footnote).foregroundColor(.red)
                    if let failure {
                        Text("What you can do").font(.caption.weight(.semibold)).padding(.top, 4)
                        Text(failure.whatToDo).font(.footnote)
                        DisclosureGroup("Technical details") {
                            Text(failure.technical).font(.caption2).textSelection(.enabled)
                        }
                        Button("Copy Diagnostics") { UIPasteboard.general.string = failure.technical }
                    }
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
        failure = nil
        defer { loading = false }
        do {
            var cursor = 0
            // V3_CATALOG_ROW_POLICY_V1: raw rows are accumulated so the shared
            // deduplication rule can be applied, then mapped to the display model
            // once. Accumulating display models instead would require a second,
            // weaker dedupe path.
            var accumulated: [[String: Any]] = []
            repeat {
                try Task.checkCancellation()
                // V3_CATALOG_PAGE_VALIDATION_V1: a page is validated instead of
                // being coerced. A missing or mistyped app list is no longer
                // silently shown as an empty catalog, and a cursor that does not
                // advance is a typed invalid response rather than a raw error.
                let result = try await V3ServiceBridge.shared.request(operation: "catalog", target: source.identifier, cursor: cursor)
                guard let rawApps = result["apps"] as? [[String: Any]] else {
                    throw catalogResponseFailure(cursor: cursor)
                }
                let page = rawApps.compactMap(V3CatalogApp.init)
                guard page.count == rawApps.count else { throw catalogResponseFailure(cursor: cursor) }
                guard let number = result["nextCursor"] as? NSNumber,
                      CFGetTypeID(number) != CFBooleanGetTypeID(),
                      let next = number as? Int else {
                    throw catalogResponseFailure(cursor: cursor)
                }
                // The real deduplication rule: duplicates are removed within a
                // page and across pages, first-seen order preserved.
                accumulated = V3CatalogRowPolicy.appending(rawApps, to: accumulated)
                guard next == -1 || next > cursor else { throw catalogResponseFailure(cursor: cursor) }
                cursor = next
            } while cursor >= 0
            apps = accumulated.compactMap(V3CatalogApp.init)
        } catch is CancellationError {
            // A cancelled load is lifecycle, not a catalog failure. Presenting it
            // as an error would blame the source for a navigation change.
            error = nil
            failure = nil
        } catch {
            if let combined = error as? CombinedFailure {
                failure = V3OperationFailureDetails(combined)
                self.error = combined.safeMessage
            } else {
                failure = nil
                self.error = "The source catalog could not be loaded."
            }
        }
    }

    // V3_CATALOG_DIAGNOSTICS_V1: an unreadable page is reported against the
    // catalog stage with the request's own correlation, and never contains the
    // source identifier, app rows, or any raw payload.
    private func catalogResponseFailure(cursor: Int) -> CombinedFailure {
        var failure = CombinedFailure(operation: "catalog", stage: .catalog, code: .invalidResponse,
                                     id: UUID().uuidString, safeCause: .catalogUnavailable,
                                     sourceStep: .catalogRead)
        failure.annotatingCatalogPage(cursor: cursor)
        return failure
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
            // V3_PROVISIONING_NEEDS_ATTENTION_V1: an authenticated session with
            // incomplete provisioning is signed in, so the recovery row is
            // presented as provisioning work, never as a sign-in problem. It is
            // placed before the signed-in-only block because
            // provisioningIncomplete can only be true for a signed-in account.
            if status.provisioningIncomplete {
                NavigationLink {
                    V3SignInView().environmentObject(status)
                } label: {
                    Label("Provisioning needs attention", systemImage: "exclamationmark.triangle.fill")
                        .foregroundColor(.orange)
                }
                .accessibilityHint("Apple ID is signed in. Retry provisioning or finish later.")
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
        } catch { status.present(error) }
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
                status.present(error)
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

private struct V3SourceAddFailure {
    let whatHappened: String
    let whatToDo: String
    let technicalDetails: String

    init(_ error: Error) {
        let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
            operation: "sourceAddConfirmed", stage: .command, id: UUID().uuidString)
        whatHappened = failure.safeMessage
        whatToDo = failure.recovery
        technicalDetails = failure.technicalDetails
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
                // V3_REFRESH_PREREQUISITE_POLICY_V1: targeted refresh uses the
                // same contract. A known-missing pairing file blocks the mutation
                // and offers the same recovery action instead of starting a run
                // that can only fail.
                if V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).blocksTargetedRefresh {
                    Text("A pairing file is required before this device can be refreshed.")
                        .font(.footnote)
                    Text("Place or import a valid pairing file, then try again.")
                        .font(.footnote).foregroundColor(.secondary)
                    Button("Show Pairing Setup") { status.pairingPresented = true }
                }
                Button {
                    status.perform("refreshApp", target: target, title: "Refresh " + app.name)
                } label: {
                    Label("Refresh " + app.name, systemImage: "arrow.clockwise")
                }
                .disabled(V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).blocksTargetedRefresh)
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
    @State private var hasProgress = false
    @State private var operationPhase = V3OperationPhase.working
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
        case "connection": return "Open Connection Check"
        case "setup": return "Open Connection Check"
        default: return nil
        }
    }
    private var isTransitioning: Bool { attempt.transitionInFlight }
    private var isRunning: Bool { ["working", "awaitingPrompt", "cancelling"].contains(state) }
    private var displayProgress: Double { V3NormalizedProgress.displayValue(progress, state: state) }
    private var progressPercent: Int { V3NormalizedProgress.percent(progress, state: state) }
    var body: some View {
        NavigationView {
            List {
                Section {
                    HStack {
                        Text("Status")
                        Spacer()
                        if state == "completed" {
                            Label("Completed", systemImage: "checkmark.circle.fill")
                                .foregroundColor(.green)
                        } else {
                            Text(statusText).foregroundColor(.secondary)
                        }
                    }
                    if isRunning || state == "completed" {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text("Progress")
                                Spacer()
                                Text(hasProgress || state == "completed" ? "\(progressPercent)%" : "—")
                                    .foregroundColor(.secondary)
                            }
                            ProgressView(value: hasProgress || state == "completed" ? displayProgress : nil)
                        }
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
        .task {
            if request.operation == "installSharedIPA" {
                status.installOperationDidPresent(attemptID: request.installAttemptID,
                    operationID: request.id)
            }
            start()
        }
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
                    if request.operation == "installSharedIPA", cancellationConfirmed {
                        status.installTerminal(attemptID: request.installAttemptID,
                            operationID: request.id, outcome: "cancelled")
                        let token = request.installAttemptID.flatMap {
                            status.resetInstallUI(attemptID: $0, outcome: "unexpected_cover_dismissal")
                        }
                        if let token { stagedIPACleaned = await status.cleanupStagedIPA(token) }
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
        default: return isTransitioning ? "Waiting for previous attempt..." : operationPhase.label
        }
    }
    private func start() {
        guard startedGeneration == nil, !attempt.transitionInFlight else { return }
        startAttempt(generation: attempt.begin())
    }
    private func startAttempt(generation: UUID) {
        guard attempt.generation == generation, startedGeneration != generation else { return }
        startedGeneration = generation
        progress = 0
        hasProgress = false
        operationPhase = .working
        task = Task { await run(generation: generation) }
    }
    private func run(generation: UUID) async {
        var backendSessionStarted = false
        do {
            status.installBackendStartRequested(attemptID: request.installAttemptID,
                operationID: request.id, sessionID: generation.uuidString)
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
            status.installBackendStarted(attemptID: request.installAttemptID,
                operationID: request.id, sessionID: id)
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
        status.installTerminal(attemptID: request.installAttemptID,
            operationID: request.id, outcome: "failed")
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
        if !["working", "awaitingPrompt"].contains(nextState) {
            status.installTerminal(attemptID: request.installAttemptID,
                operationID: request.id, outcome: nextState)
        }
        if let rawProgress = reply["progress"] as? Double {
            progress = V3NormalizedProgress.clamp(rawProgress)
            hasProgress = true
        }
        if let rawPhase = reply["phase"] as? String {
            operationPhase = V3OperationPhase(rawValue: rawPhase) ?? .working
        }
        let oldPromptID = prompt?["id"] as? String
        let nextPrompt = reply["prompt"] as? [String: Any]
        prompt = nextPrompt
        if oldPromptID != (nextPrompt?["id"] as? String) { promptSubmitting = false }
        switch state {
        case "completed":
            progress = 1
            hasProgress = true
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
            message = V3FailureGuidance.message(error)
        }
    }
    private func retry() {
        guard retryAllowed, attempt.beginTransition() else { return }
        status.prepareInstallRetry(attemptID: request.installAttemptID)
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
                status.installTerminal(attemptID: request.installAttemptID,
                    operationID: request.id, outcome: "cancelled")
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
            status.installTerminal(attemptID: request.installAttemptID,
                operationID: request.id, outcome: "cancelled")
            if request.operation == "installSharedIPA" {
                let token = request.installAttemptID.flatMap {
                    status.resetInstallUI(attemptID: $0, outcome: "acknowledged",
                        preserveRecoveryDestination: status.operationRecoveryDestination != nil)
                }
                if let token, !stagedIPACleaned {
                    stagedIPACleaned = await status.cleanupStagedIPA(token)
                }
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
    private var twoFactorStep: V3TwoFactorStep {
        let raw = fieldDefs.first(where: { $0["key"] == "step" })?["value"] ?? "chooseDeliveryMethod"
        return V3TwoFactorStep(rawValue: raw) ?? .chooseDeliveryMethod
    }
    var body: some View {
        Section(title) {
            if !message.isEmpty {
                Text(message)
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }
            if kind == "twoFactor" {
                switch twoFactorStep {
                case .chooseDeliveryMethod:
                    Text("Choose how Apple sends your verification code.")
                        .font(.subheadline.weight(.semibold))
                    ForEach(deliveryOptions, id: \.self) { option in twoFactorOption(option) }
                    twoFactorCancelButton()
                case .choosePhoneNumber:
                    Text("Choose the phone number for this request.")
                        .font(.subheadline.weight(.semibold))
                    ForEach(phoneOptions, id: \.self) { option in twoFactorOption(option) }
                    twoFactorCancelButton()
                case .enterVerificationCode:
                    TextField("Verification code", text: binding("code"))
                        .keyboardType(.numberPad)
                        .textFieldStyle(.roundedBorder)
                        .textContentType(.oneTimeCode)
                    Button("Verify Code") {
                        var answer = fields
                        answer["choice"] = "code"
                        answer["action"] = "code"
                        respond(answer)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled((fields["code"] ?? "").isEmpty || isSubmitting)
                    Button("Change Verification Method", systemImage: "arrow.uturn.backward") {
                        var answer = fields
                        answer["action"] = "changeMethod"
                        answer["choice"] = "changeMethod"
                        respond(answer)
                    }
                    .disabled(isSubmitting)
                    twoFactorCancelButton()
                case .verifyingCode:
                    ProgressView("Verifying code...")
                case .deliveryRequested:
                    ProgressView("Requesting verification...")
                case .completed:
                    Label("Verification complete", systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                case .failed:
                    Text("Verification could not continue. You can change method or cancel sign-in.")
                        .font(.footnote)
                    twoFactorCancelButton()
                case .cancelled:
                    Text("Sign-in was cancelled.").font(.footnote)
                }
            } else {
            ForEach(fieldDefs, id: \.self) { field in
                // The "technical" field is diagnostics-only output: it renders
                // as selectable caption text below, never as an editable field.
                if field["key"] == "step" || field["key"] == "mode" || field["key"] == "activeID" || field["key"] == "phoneID" || field["key"] == "url" || field["key"] == "serials" || field["key"] == "technical" {
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
            loadFields()
        }
        .onChange(of: prompt["id"] as? String ?? "") { _ in loadFields() }
    }
    private func loadFields() {
        fields = [:]
        selected = []
        isSubmitting = false
        for field in fieldDefs { fields[field["key"] ?? ""] = field["value"] ?? "" }
    }
    private func twoFactorOption(_ option: [String: String]) -> some View {
        Button {
            var answer = fields
            let id = option["id"] ?? ""
            answer["choice"] = id
            answer["action"] = id
            respond(answer)
        } label: {
            HStack {
                if (option["id"] ?? "").hasPrefix("phone:") {
                    Image(systemName: "phone.fill").foregroundColor(.accentColor)
                }
                Text(option["label"] ?? "")
                Spacer()
                Image(systemName: "chevron.right").font(.caption).foregroundColor(.secondary)
            }
        }
        .buttonStyle(.bordered)
        .disabled(isSubmitting)
    }
    private func twoFactorCancelButton() -> some View {
        Button("Cancel Sign In", role: .cancel) {
            respond(["action": "cancel", "choice": "cancel"])
        }
        .disabled(isSubmitting)
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
    @Published var previousFailure: [String: Any]?
    @Published var attempts = 0
    @Published var message = ""
    @Published var deliveryProgressMessage = ""
    @Published var twoFactorTransientStep: V3TwoFactorStep?
    @Published var team = ""
    @Published var promptSubmitting = false
    @Published private(set) var isCancelling = false
    @Published private(set) var cancellationConfirmed = true
    // V3_PROVISIONING_RECOVERY_STATE_V1: a successful Apple sign-in and a failed
    // provisioning attempt are two separate facts and are stored separately, so
    // neither can be presented as the other. The typed provisioning guidance and
    // the safe technical line produced by the service are preserved verbatim
    // instead of being discarded with the terminal payload.
    @Published private(set) var provisioningMessage = ""
    @Published private(set) var provisioningTechnical = ""
    @Published private(set) var provisioningCode = ""
    @Published private(set) var provisioningStage = ""
    @Published private(set) var provisioningCorrelation = ""
    @Published private(set) var provisioningRetryAvailable = false
    @Published private(set) var provisioningFinishedLater = false
    // Sticky once the service reports an authenticated terminal. It survives a
    // provisioning retry so the screen keeps saying the sign-in succeeded while
    // provisioning is running again.
    @Published private(set) var signedIn = false
    private var session: String?
    private var task: Task<Void, Never>?

    // V3_AUTH_SUCCESS_IS_NOT_PROVISIONING_SUCCESS_V1: authentication succeeded
    // whenever the service reports an authenticated terminal, regardless of
    // whether provisioning then failed.
    var isSignedIn: Bool { signedIn }
    // The provisioning problem is only present when a classified failure arrived.
    var hasProvisioningProblem: Bool {
        state == "authenticatedProvisioningIncomplete" && !provisioningMessage.isEmpty
            && !provisioningFinishedLater
    }

    func begin() {
        guard canBegin else { return }
        task?.cancel()
        state = "working"
        message = ""
        deliveryProgressMessage = ""
        twoFactorTransientStep = nil
        prompt = nil
        previousFailure = nil
        promptSubmitting = false
        cancellationConfirmed = true
        signedIn = false
        clearProvisioningOutcome()
        task = Task { await run() }
    }
    var canBegin: Bool {
        !isCancelling && cancellationConfirmed && !["working", "awaitingPrompt"].contains(state)
    }

    private func clearProvisioningOutcome() {
        provisioningMessage = ""
        provisioningTechnical = ""
        provisioningCode = ""
        provisioningStage = ""
        provisioningCorrelation = ""
        provisioningRetryAvailable = false
        provisioningFinishedLater = false
    }

    // V3_RETRY_PROVISIONING_REUSES_SESSION_V1: the Apple session is already
    // authenticated, so the retry is a distinct operation. It deliberately does
    // not reuse the interactive begin operation, which would ask for credentials
    // and 2FA again.
    func retryProvisioning() {
        guard !isCancelling, !["working", "awaitingPrompt"].contains(state) else { return }
        task?.cancel()
        state = "working"
        message = ""
        prompt = nil
        previousFailure = nil
        promptSubmitting = false
        clearProvisioningOutcome()
        task = Task { await runProvisioningRetry() }
    }
    var canRetryProvisioning: Bool { provisioningRetryAvailable }

    private func runProvisioningRetry() async {
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "authRetryProvisioning")
            guard let id = reply["session"] as? String,
                  reply["state"] as? String != "failed" else {
                // The saved session is gone. Fall back to a full, honest sign-in
                // instead of silently claiming provisioning was retried.
                state = "failed"
                message = reply["message"] as? String
                    ?? "The saved Apple session is no longer valid. Sign in again with this Apple ID."
                provisioningMessage = ""
                return
            }
            session = id
            try await pollLoop(id: id)
        } catch {
            state = "authenticatedProvisioningIncomplete"
            provisioningMessage = "Retry Provisioning could not be started."
            provisioningTechnical = (error as? CombinedFailure)?.technicalDetails ?? ""
        }
    }

    // V3_FINISH_LATER_PRESERVES_ACCOUNT_V1: closing the provisioning flow must
    // not sign the account out. It only dismisses the local recovery
    // presentation; authoritative account state is reloaded afterwards.
    func finishProvisioningLater() {
        provisioningMessage = ""
        provisioningTechnical = ""
        provisioningFinishedLater = true
    }

    func reconcile() async {
        guard !["working", "awaitingPrompt"].contains(state) else { return }
        do {
            let snapshot = try await V3ServiceBridge.shared.request(operation: "snapshot")
            let account = snapshot["account"] as? String ?? "Not signed in"
            let authoritative = (snapshot["authenticated"] as? Bool ?? false) || !account.isEmpty && account != "Not signed in"
            let incomplete = snapshot["provisioningIncomplete"] as? Bool ?? false
            if authoritative {
                // V3_FINISH_LATER_RECONCILES_AS_SIGNED_IN_V1: authoritative state
                // wins. A finished-later provisioning attempt still reconciles as
                // signed in, never back to "sign in again".
                signedIn = true
                if incomplete {
                    // The service still reports an authenticated session whose
                    // provisioning never activated an account. Stay signed in and
                    // keep the provisioning recovery actions available. The
                    // classified reason from the original attempt is not
                    // reconstructed here, because nothing persisted it.
                    if state != "authenticatedProvisioningIncomplete" {
                        state = "authenticatedProvisioningIncomplete"
                        message = "Apple ID signed in successfully."
                    }
                    if provisioningMessage.isEmpty {
                        provisioningMessage = "Device provisioning did not complete. Retry provisioning, or finish later and come back."
                    }
                    // The service only reports an authenticated session here, so
                    // the saved session is present and a retry can skip 2FA.
                    provisioningRetryAvailable = true
                } else {
                    state = "completed"
                    team = snapshot["team"] as? String ?? ""
                    message = ""
                    clearProvisioningOutcome()
                }
            } else if state == "idle" || state == "completed" {
                state = "idle"
                team = ""
            }
        } catch {
            if state == "idle" { message = "Could not confirm the current SideStore account. Reload status and try again." }
        }
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
            message = V3FailureGuidance.message(error)
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
        previousFailure = V3AuthPromptFailurePolicy.applying(reply: reply, current: previousFailure)
        if reply["authenticated"] as? Bool == true { signedIn = true }
        if oldPromptID != (prompt?["id"] as? String) {
            promptSubmitting = false
            if state == "awaitingPrompt" {
                deliveryProgressMessage = ""
                twoFactorTransientStep = nil
            }
        }
        if state == "completed" {
            team = reply["team"] as? String ?? ""
            prompt = nil
            message = ""
            deliveryProgressMessage = ""
            clearProvisioningOutcome()
        } else if state == "authenticatedProvisioningIncomplete" {
            // V3_PROVISIONING_TERMINAL_NOT_A_SIGNIN_FAILURE_V1: the terminal
            // payload carries the classified provisioning problem, which is
            // retained verbatim. This state is never collapsed into "failed".
            team = reply["team"] as? String ?? team
            message = "Apple ID signed in successfully."
            prompt = nil
            deliveryProgressMessage = ""
            provisioningMessage = reply["message"] as? String ?? "Provisioning could not be completed."
            provisioningStage = reply["stage"] as? String ?? ""
            provisioningCode = reply["code"] as? String ?? ""
            provisioningTechnical = reply["technicalDetails"] as? String ?? ""
            provisioningCorrelation = (reply["failure"] as? [String: Any])?["correlationID"] as? String ?? ""
            provisioningRetryAvailable = reply["resumable"] as? Bool ?? false
            provisioningFinishedLater = false
        } else if state == "failed" {
            message = reply["message"] as? String ?? "The sign-in request failed for an unknown reason."
            if let failure = reply["failure"] as? [String: Any] { previousFailure = failure }
            prompt = nil
            deliveryProgressMessage = ""
            clearProvisioningOutcome()
        } else if state == "cancelled" {
            message = "Sign-in was cancelled."
            prompt = nil
        }
    }

    static func failureMessage(from failure: [String: Any]) -> String {
        // The service classifies the real typed error into a display kind.
        // Only show password guidance for proven invalid credentials.
        switch failure["kind"] as? String {
        case "invalidCredentials": return "Apple did not accept the Apple ID or password. Check them and try again."
        case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."
        case "invalidCode": return "The verification code was not accepted. Enter a new code and try again."
        case "rateLimited": return "Too many authentication attempts. Apple is temporarily rate-limiting requests. Wait before trying again."
        case "serviceUnavailable": return "Apple's authentication service did not return a valid response. Try again later."
        case "anisetteFailure", "anisette": return "Authentication could not obtain valid Anisette data."
        case "networkFailure", "network": return "Authentication could not reach the required Apple service. Check the connection and try again."
        case "accountRepairRequired": return "Apple requires attention on this account before signing in."
        case "unknown": return "Apple sign-in returned an error that could not be safely classified."
        case nil: break
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
        previousFailure = V3AuthPromptFailurePolicy.clearingAfterSubmission(
            previousFailure, promptKind: prompt?["kind"] as? String)
        if prompt?["kind"] as? String == "twoFactor" {
            switch answer["action"] {
            case "trustedDevice":
                twoFactorTransientStep = .deliveryRequested
                deliveryProgressMessage = "Requesting approval from your trusted devices..."
            case "sms", "voice":
                let choices = prompt?["options"] as? [[String: Any]] ?? []
                let phoneCount = choices.filter { ($0["id"] as? String ?? "").hasPrefix("phone:") }.count
                if phoneCount > 1 {
                    twoFactorTransientStep = .choosePhoneNumber
                    deliveryProgressMessage = "Choose a phone number for this verification request..."
                } else if answer["action"] == "voice" {
                    twoFactorTransientStep = .deliveryRequested
                    deliveryProgressMessage = "Requesting a verification call..."
                } else {
                    twoFactorTransientStep = .deliveryRequested
                    deliveryProgressMessage = "Requesting a verification code by SMS..."
                }
            case "code":
                twoFactorTransientStep = .verifyingCode
                deliveryProgressMessage = "Verifying code..."
            case "changeMethod":
                twoFactorTransientStep = .chooseDeliveryMethod
                deliveryProgressMessage = "Opening verification methods..."
            default:
                if answer["action"]?.hasPrefix("phone:") == true {
                    let mode = answer["mode"] ?? "sms"
                    twoFactorTransientStep = .deliveryRequested
                    deliveryProgressMessage = mode == "voice" ? "Requesting a verification call..." : "Requesting a verification code by SMS..."
                }
            }
        }
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

    func clearPreviousFailure() {
        previousFailure = V3AuthPromptFailurePolicy.clearingOnDismiss(previousFailure)
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
    @EnvironmentObject private var sharedModel: SharedModel
    @StateObject private var auth = V3AuthStore()
    var body: some View {
        List {
            Section("Apple ID") {
                HStack {
                    Text("Status")
                    Spacer()
                    Text(statusText).foregroundColor(.secondary)
                }
                if auth.isSignedIn {
                    HStack {
                        Label("Signed in successfully", systemImage: "checkmark.circle.fill")
                            .foregroundColor(.green)
                        Spacer()
                        if !auth.team.isEmpty { Text(auth.team).foregroundColor(.secondary) }
                    }
                    if ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26 {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Next: Set Up JIT-Less")
                                .font(.subheadline.weight(.semibold))
                            Text("LiveContainer needs a JIT-Less certificate configured before guest apps can launch on iOS 26 and later.")
                                .font(.footnote).foregroundColor(.secondary)
                            Button("Continue to JIT-Less Setup") { openJITLessSetup() }
                                .buttonStyle(.borderedProminent)
                        }
                        .padding(.vertical, 4)
                    }
                }
                if !auth.message.isEmpty {
                    Text(auth.message)
                        .font(.footnote)
                        .foregroundColor(auth.isSignedIn ? .green : .red)
                        .textSelection(.enabled)
                }
                // V3_PROVISIONING_NEEDS_ATTENTION_V1: the authenticated fact above
                // stays green while the provisioning problem is stated separately.
                if auth.hasProvisioningProblem {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Provisioning needs attention")
                            .font(.subheadline.weight(.semibold))
                            .foregroundColor(.orange)
                        Text("Provisioning could not be completed.")
                            .font(.footnote.weight(.medium))
                            .foregroundColor(.orange)
                        Text(auth.provisioningMessage)
                            .font(.footnote)
                            .foregroundColor(.orange)
                            .textSelection(.enabled)
                        if !auth.provisioningTechnical.isEmpty {
                            DisclosureGroup("Technical details") {
                                Text(auth.provisioningTechnical)
                                    .font(.caption2)
                                    .textSelection(.enabled)
                            }
                            HStack {
                                Button("Copy Diagnostics") { UIPasteboard.general.string = auth.provisioningTechnical }
                                    .font(.caption)
                                Spacer(minLength: 0)
                            }
                        }
                    }
                }
                if !auth.deliveryProgressMessage.isEmpty {
                    Text(auth.deliveryProgressMessage)
                        .font(.footnote.weight(.medium))
                        .foregroundColor(.orange)
                } else if let progress = auth.twoFactorTransientStep?.progressLabel {
                    Text(progress)
                        .font(.footnote.weight(.medium))
                        .foregroundColor(.orange)
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
                // V3_PROVISIONING_RECOVERY_ACTIONS_V1: the actions describe the
                // provisioning state, not a failed sign-in. "Retry" re-enters
                // provisioning with the saved session; "Finish Later" keeps the
                // authenticated account and closes this flow.
                if auth.state == "authenticatedProvisioningIncomplete" && !auth.provisioningFinishedLater {
                    Button {
                        auth.retryProvisioning()
                    } label: {
                        Label("Retry Provisioning", systemImage: "arrow.clockwise")
                    }
                    .disabled(!auth.canRetryProvisioning)
                    if !auth.canRetryProvisioning {
                        Text("The saved Apple session is no longer available. Sign in again to retry provisioning.")
                            .font(.caption).foregroundColor(.secondary)
                    }
                    Button("Finish Later") { finishProvisioningLater() }
                }
            }
            if let prompt = auth.prompt {
                if V3AuthPromptFailurePolicy.isVisible(auth.previousFailure, promptKind: prompt["kind"] as? String),
                   let previousFailure = auth.previousFailure {
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
        .task { await auth.reconcile() }
        .onDisappear {
            auth.cancel()
            auth.clearPreviousFailure()
            status.reload()
        }
    }
    private var statusText: String {
        switch auth.state {
        case "completed": return "Signed in"
        case "authenticatedProvisioningIncomplete":
            return auth.provisioningFinishedLater ? "Signed in" : "Signed in, provisioning needs attention"
        case "awaitingPrompt": return "Needs your input"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        case "working": return auth.isSignedIn ? "Finishing provisioning..." : "Working..."
        default: return "Not started"
        }
    }

    // V3_FINISH_LATER_PRESERVES_ACCOUNT_V1: closing the flow reloads the
    // authoritative SideStore snapshot so the account is shown as signed in
    // again. It never signs out and never discards the saved session.
    private func finishProvisioningLater() {
        auth.finishProvisioningLater()
        status.reload()
    }

    private func openJITLessSetup() {
        if status.setupPresented || status.signInPresented {
            status.pendingCanonicalJITLessSetup = true
            status.returnToSetupAfterJITLess = status.setupPresented
            status.setupPresented = false
            status.signInPresented = false
        } else {
            sharedModel.selectedTab = .settings
            sharedModel.deepLink = URL(string: "livecontainer://jitless-setup")
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
            } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
            // V3_PAIRING_PLACEMENT_FIRST_V1: the pairing mechanism works. The
            // normal installation workflow places the pairing file with the tool
            // that installed LC+SS, so that is the recommended path. Manual import
            // remains available as a clearly secondary fallback.
            if pairingMissing {
                Section("Pairing File Required") {
                    Text("Recommended setup")
                        .font(.footnote.weight(.semibold))
                    Text("If you installed with iLoader:")
                        .font(.footnote).foregroundColor(.secondary)
                    ForEach(Array(Self.pairingPlacementSteps.enumerated()), id: \.offset) { index, step in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("\(index + 1).").font(.caption).foregroundColor(.secondary)
                            Text(step).font(.footnote)
                        }
                    }
                    Text("Other installation tools use different menu names, but they all place the pairing file for the installed app.")
                        .font(.caption).foregroundColor(.secondary)
                    Button {
                        recheck()
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                    .disabled(working)
                }
            } else {
                Section("Pairing File Ready") {
                    Text("A valid pairing file is available. Re-check if you replace the file or reset the device.")
                        .font(.footnote).foregroundColor(.secondary)
                    Button {
                        recheck()
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                    .disabled(working)
                }
            }
            // V3_PAIRING_IMPORT_IS_FALLBACK_V1: kept, presented as an alternative.
            Section {
                Text("Alternative: Import Pairing File Manually")
                    .font(.footnote.weight(.semibold))
                Button {
                    pickerPresented = true
                } label: {
                    Label(working ? "Importing..." : "Import Pairing File Manually", systemImage: "doc.badge.plus")
                }
                .disabled(working)
                Text("Use this only if the placement tool did not work. Pick a .mobiledevicepairing or .plist file. This screen owns the picker; the service only validates and stores the file.")
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
    // V3_PAIRING_PLACEMENT_STEPS_V1: the documented iLoader placement flow.
    // It is presented as "If you installed with iLoader", because other
    // third-party installers do not necessarily use the same menu names.
    static let pairingPlacementSteps = [
        "Connect the iPhone to the computer if your installation tool requires it.",
        "Open the tool you used to install LC+SS.",
        "Open Management.",
        "Open Manage Pairing File.",
        "If LC+SS is not listed, use Rescan Installed Apps.",
        "Find the installed LC+SS app.",
        "Choose Place for this app.",
        "Wait for the tool to confirm success.",
        "Return to LC+SS."
    ]

    // V3_REFRESH_PREREQUISITE_POLICY_V1: one interpretation, shared with every
    // refresh entry point.
    private var pairingMissing: Bool {
        V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).blocksRefresh
    }

    private func recheck() {
        status.reload()
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
                message = V3FailureGuidance.message(error)
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
                message = V3FailureGuidance.message(error)
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
                message = V3FailureGuidance.message(error)
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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


private struct V3PKCS12CertificateFacts {
    let teamIdentifier: String
    let identitySHA256: String
}

private enum V3JITLessStatusReader {
    static func read(serviceCertificate: [String: Any]) async -> (V3JITLessReadiness, String) {
        let osMajor = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
        let active = serviceCertificate["active"] as? Bool ?? false
        let activeStatus = serviceCertificate["validation"] as? String ?? "unknown"
        let activeFingerprint = serviceCertificate["certificateIdentitySHA256"] as? String ?? ""
        let data = LCUtils.certificateData() as Data?
        let password = LCSharedUtils.certificatePassword()
        let facts = data.flatMap { bytes in password.flatMap { parse(bytes, password: $0) } }
        let identitiesMatch: Bool? = {
            guard active, !activeFingerprint.isEmpty, let facts else { return nil }
            return activeFingerprint == facts.identitySHA256
        }()
        var validationStatus: Int?
        var validationFailed = false
        if data != nil && password != nil {
            let validation = await validateLocalCopy()
            validationStatus = validation.status
            validationFailed = validation.failed
        }
        let state = V3JITLessReadinessPolicy.evaluate(
            osMajor: osMajor,
            hasCopy: data != nil && password != nil && facts != nil,
            activeCertificateExists: active,
            activeCertificateStatus: activeStatus,
            identitiesMatch: identitiesMatch,
            validationStatus: validationStatus,
            validationFailed: validationFailed)
        if osMajor >= 26 && !active {
            return (state, V3JITLessPresentation.present(.activeCertificateMissing).detail)
        }
        return (state, detail(for: state))
    }

    static func parse(_ data: Data, password: String) -> V3PKCS12CertificateFacts? {
        var importedItems: CFArray?
        let options = [kSecImportExportPassphrase as String: password] as CFDictionary
        guard SecPKCS12Import(data as CFData, options, &importedItems) == errSecSuccess,
              let item = (importedItems as? [[String: Any]])?.first,
              let identityValue = item[kSecImportItemIdentity as String] else { return nil }
        let identityObject = identityValue as AnyObject
        guard CFGetTypeID(identityObject as CFTypeRef) == SecIdentityGetTypeID() else { return nil }
        let identity = unsafeBitCast(identityObject, to: SecIdentity.self)
        var certificate: SecCertificate?
        guard SecIdentityCopyCertificate(identity, &certificate) == errSecSuccess,
              let certificate,
              let team = LCUtils.getCertTeamId(withKeyData: data, password: password) else { return nil }
        let der = SecCertificateCopyData(certificate) as Data
        let fingerprint = SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
        return V3PKCS12CertificateFacts(teamIdentifier: team, identitySHA256: fingerprint)
    }

    private static func validateLocalCopy() async -> (status: Int?, failed: Bool) {
        await withCheckedContinuation { (continuation: CheckedContinuation<(Int?, Bool), Never>) in
            LCUtils.validateCertificate { status, _, _, error in
                continuation.resume(returning: (Int(status), error != nil))
            }
        }
    }

    private static func detail(for state: V3JITLessReadiness) -> String {
        // V3_JITLESS_PRESENTATION_V1: one source of truth for the wording, so
        // Setup Assistant, Health and Settings cannot describe the same state
        // three different ways.
        V3JITLessPresentation.present(state).detail
    }
}

struct V3HealthView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @EnvironmentObject private var sharedModel: SharedModel
    @State private var rows: [(String, String)] = []
    @State private var certRows: [(String, String)] = []
    @State private var message = ""
    @State private var jitlessReadiness: V3JITLessReadiness = .unknown
    @State private var jitlessDetail = "Checking"
    @State private var activeCertificateAvailable = false
    @State private var checking = false

    var body: some View {
        List {
            if status.needsSignIn {
                Section { V3SignInLink(title: "Sign In to Check Account Health") }
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
                Text("SideStore uses its active certificate for signing, refresh, and installation. LiveContainer keeps a separate JIT-Less certificate copy. JIT-Less certificate status does not by itself mean SideStore refresh used that copy.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Section("JIT-Less Mode") {
                HStack {
                    Text("Status")
                    Spacer()
                    Text(jitlessDetail).foregroundColor(.secondary).multilineTextAlignment(.trailing)
                }
                if ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26 {
                    // V3_JITLESS_PRESENTATION_V1: Health renders the same shared
                    // presentation as Setup Assistant, and adds the certificate
                    // action that actually resolves each distinct state.
                    let jitless = V3JITLessPresentation.present(jitlessReadiness)
                    Label(jitless.title, systemImage: jitless.icon)
                        .font(.footnote)
                        .foregroundColor(jitless.tint)
                    if !jitless.isOutstandingSetupTask {
                        Button("Open JIT-Less Diagnose") { openJITLessDiagnose() }
                            .font(.caption)
                    } else {
                        switch jitlessReadiness {
                        case .setupRequired, .needsCertificateRefresh, .certificateMismatch, .revoked:
                            Button(jitlessReadiness == .setupRequired
                                   ? "Set Up JIT-Less" : "Refresh JIT-Less Certificate") {
                                openJITLessSetup()
                            }
                        case .certificateImported, .unknown:
                            // The copy exists but validation is not conclusive, so
                            // the canonical setup flow is still the useful action.
                            if activeCertificateAvailable {
                                Button("Open JIT-Less Setup") { openJITLessSetup() }
                            }
                            Button("Open Certificates") { status.certificatesPresented = true }
                        case .activeCertificateMissing, .activeCertificateRevoked,
                             .activeCertificateExpired:
                            // Refreshing the copy cannot repair SideStore's own
                            // certificate, so only Certificates is offered.
                            Text("Refreshing the JIT-Less copy cannot repair SideStore's active certificate.")
                                .font(.caption).foregroundColor(.secondary)
                            Button("Open Certificates") { status.certificatesPresented = true }
                        case .ready, .notRequired:
                            EmptyView()
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Health Check")
        .task { await reload() }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3CanonicalJITLessCertificateUpdated"))) { _ in
            Task { await reload() }
        }
    }

    private func reload() async {
        guard !checking else { return }
        checking = true
        defer { checking = false }
        jitlessDetail = "Checking"
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
            let certificateState = reply["certificateState"] as? [String: Any] ?? [:]
            activeCertificateAvailable = certificateState["active"] as? Bool == true
            certRows = certComparison(service: certificateState)
            let readiness = await V3JITLessStatusReader.read(serviceCertificate: certificateState)
            jitlessReadiness = readiness.0
            jitlessDetail = readiness.1
            // V3_SHARED_JITLESS_FACT_V1: Health is an observer of the same fact,
            // so visiting Health can also complete Home's outstanding item.
            status.recordJITLessReadiness(readiness.0)
            message = ""
        } catch {
            message = V3FailureGuidance.message(error)
            jitlessReadiness = .unknown
            jitlessDetail = "Could not check JIT-Less status"
            activeCertificateAvailable = false
            status.recordJITLessReadiness(.unknown)
        }
    }

    private func openJITLessSetup() {
        sharedModel.selectedTab = .settings
        sharedModel.deepLink = URL(string: "livecontainer://jitless-setup")
    }

    private func openJITLessDiagnose() {
        sharedModel.selectedTab = .settings
        sharedModel.deepLink = URL(string: "livecontainer://jitless-diagnose")
    }

    private func certComparison(service: [String: Any]) -> [(String, String)] {
        let active = service["active"] as? Bool ?? false
        let serialSuffix = service["serialSuffix"] as? String ?? ""
        let team = service["team"] as? String ?? ""
        let expiry = service["expiry"] as? Date
        var result: [(String, String)] = [("SideStore Active", active ? "Yes" : "No")]
        if active {
            if !serialSuffix.isEmpty { result.append(("Active Serial", "?\(serialSuffix)")) }
            if !team.isEmpty { result.append(("Active Team", "?\(String(team.suffix(4)))")) }
            if let expiry { result.append(("Active Expiry", expiry.formatted(date: .abbreviated, time: .omitted))) }
        }
        let data = LCUtils.certificateData() as Data?
        result.append(("JIT-Less Copy", data == nil ? "Not imported" : "Imported"))
        var localTeam = ""
        var localFingerprint = ""
        if let data, let password = LCSharedUtils.certificatePassword(),
           let facts = V3JITLessStatusReader.parse(data, password: password) {
            localTeam = facts.teamIdentifier
            localFingerprint = facts.identitySHA256
            result.append(("Copy Team", "?\(String(localTeam.suffix(4)))"))
        }
        if let date = LCUtils.appGroupUserDefault.object(forKey: "LCCertificateUpdateDate") as? Date {
            result.append(("Copy Imported", date.formatted(date: .abbreviated, time: .shortened)))
        }
        let teamVerdict: String
        if !active { teamVerdict = "unknown: no active SideStore certificate" }
        else if localTeam.isEmpty || team.isEmpty { teamVerdict = "unknown: team could not be compared" }
        else { teamVerdict = localTeam == team ? "yes: same team" : "no: different teams" }
        result.append(("Team Match", teamVerdict))
        let activeFingerprint = service["certificateIdentitySHA256"] as? String ?? ""
        let identityVerdict: String
        if !active { identityVerdict = "unknown: no active SideStore certificate" }
        else if activeFingerprint.isEmpty || localFingerprint.isEmpty {
            identityVerdict = "unknown: certificate identity could not be compared"
        } else {
            identityVerdict = activeFingerprint == localFingerprint ? "yes: same certificate" : "no: different certificates"
        }
        result.append(("Certificate Identity Match", identityVerdict))
        if active { result.append(("SideStore Certificate Validation", service["validation"] as? String ?? "unknown")) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
        } catch { message = V3FailureGuidance.message(error) }
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
    @Published var jitless = V3SetupStepState()
    @Published var jitlessReadiness: V3JITLessReadiness = .unknown
    @Published var jitlessHasActiveCertificate = false
    @Published var network = V3SetupStepState()
    @Published var tunnel = V3SetupStepState()
    @Published var background = V3SetupStepState()
    @Published var schedule = V3SetupStepState()
    @Published var verification = V3SetupStepState()
    // V3_REFRESH_PREREQUISITE_POLICY_V1: the "What you can do" line for a
    // blocked or failed Test Refresh, kept separate from the row detail so the
    // structured failure fields stay machine-readable.
    @Published var verificationGuidance = ""
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
    // V3_SETUP_COMPLETION_POLICY_V1: the decision comes from the one shared
    // policy that Home also uses, so the two screens cannot disagree.
    var completionInputs: V3SetupCompletionInputs {
        V3SetupCompletionInputs(
            accountComplete: account.state == "complete",
            provisioningIncomplete: statusProvisioningIncomplete,
            pairingSatisfied: pairing.state == "complete",
            // V3_SHARED_JITLESS_FACT_V1: both surfaces ask the shared policy
            // whether JIT-Less is required, and the step state below is derived
            // from the same observed readiness Home reads.
            jitlessRequired: V3JITLessCompletionPolicy.isRequired(
                osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion),
            jitlessComplete: jitless.state == "complete",
            networkComplete: network.state == "complete",
            tunnelComplete: tunnel.state == "complete",
            backgroundRefreshAvailable: background.state == "complete",
            scheduleEnabled: schedule.state == "complete",
            verifiedRefreshPresent: verification.state == "complete")
    }
    var outstandingSetup: [V3SetupOutstandingItem] { completionInputs.outstanding() }
    var isComplete: Bool { completionInputs.isComplete }

    /// Where the JIT-Less row leads when the state still needs work. A ready
    /// state has no required destination; its diagnostic action is separate.
    /// The status store is passed in because the store is not a View and has no
    /// environment of its own.
    func jitlessDestination(status: V3SideStoreStatusStore) -> AnyView? {
        let readiness = jitlessReadiness
        if readiness.isSatisfied { return nil }
        if [.activeCertificateRevoked, .activeCertificateExpired].contains(readiness) || !jitlessHasActiveCertificate {
            return AnyView(V3CertificatesView().environmentObject(status))
        }
        return nil
    }

    // Set from the authoritative snapshot so the shared policy sees the same
    // provisioning fact Home sees, rather than inferring it from step states.
    @Published private(set) var statusProvisioningIncomplete = false

    func recalculate(status: V3SideStoreStatusStore) async {
        NSLog("[V3_SETUP] STATUS recalculating")
        // Recorded from the authoritative snapshot so the shared completion
        // policy sees the same provisioning fact Home sees.
        statusProvisioningIncomplete = status.provisioningIncomplete
        device = V3SetupStepState(state: "complete", detail: "App running")
        // V3_REFRESH_PREREQUISITE_POLICY_V1: shared with Home Refresh All, Test
        // Refresh, and targeted refresh. A known-missing pairing file is
        // "missing"; an unknown status stays unknown rather than being reported
        // as missing.
        switch V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).state {
        case .satisfied:
            pairing = V3SetupStepState(state: "complete", detail: "Pairing file available")
        case .unsatisfied:
            pairing = V3SetupStepState(state: "actionRequired", detail: V3RefreshPrerequisite.pairingRequiredDetail)
        case .unknown:
            pairing = V3SetupStepState(state: "checking", detail: "Checking pairing file status")
        }
        // V3_AUTH_SESSION_SNAPSHOT_V1: an authenticated session with incomplete
        // provisioning is signed in, so the row must not read "Not signed in".
        if status.needsSignIn {
            account = V3SetupStepState(state: "actionRequired", detail: "Not signed in")
        } else if status.provisioningIncomplete {
            account = V3SetupStepState(state: "warning", detail: "Signed in, provisioning needs attention")
        } else if status.team == "No active team" {
            account = V3SetupStepState(state: "warning", detail: "Signed in without an active team")
        } else {
            account = V3SetupStepState(state: "complete", detail: status.account)
        }
        // V3_SHARED_JITLESS_FACT_V1: the requirement itself also comes from the
        // shared policy, so the branch below and the completion input can never
        // disagree about whether JIT-Less applies on this iOS version.
        if !V3JITLessCompletionPolicy.isRequired(
            osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion) {
            jitlessReadiness = .notRequired
            status.recordJITLessReadiness(.notRequired)
            jitless = V3SetupStepState(state: "complete", detail: "Not required on this iOS version")
        } else {
            do {
                let health = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
                let certificate = health["certificateState"] as? [String: Any] ?? [:]
                jitlessHasActiveCertificate = certificate["active"] as? Bool == true
                let readiness = await V3JITLessStatusReader.read(serviceCertificate: certificate)
                jitlessReadiness = readiness.0
                // V3_SHARED_JITLESS_FACT_V1: publish the readiness so Home reads
                // this answer rather than re-deriving a different one.
                status.recordJITLessReadiness(readiness.0)
                // V3_JITLESS_PRESENTATION_V1: the step state is derived from the
                // shared presentation, so a ready state is stored as complete
                // rather than as a permanent "action required" row.
                let presentation = V3JITLessPresentation.present(readiness.0)
                switch presentation.severity {
                case .completed:
                    jitless = V3SetupStepState(state: "complete", detail: presentation.title)
                case .failed:
                    jitless = V3SetupStepState(state: "failed", detail: presentation.title)
                case .unknown:
                    jitless = V3SetupStepState(state: "warning", detail: presentation.title)
                default:
                    jitless = V3SetupStepState(state: "actionRequired", detail: presentation.title)
                }
            } catch {
                jitlessReadiness = .unknown
                jitlessHasActiveCertificate = false
                // Not observed is published as unknown, so Home keeps the item
                // outstanding instead of assuming a certificate exists.
                status.recordJITLessReadiness(.unknown)
                jitless = V3SetupStepState(state: "warning", detail: "Could not verify JIT-Less certificate state")
            }
        }
        network = V3SetupStepState(state: "checking", detail: "Checking Wi-Fi…")
        let wifi = await LiveContainerNetworkPreflight.wifiAvailable()
        // Published so the shared setup-completion policy and the Home banner
        // observe the same authoritative Wi-Fi fact instead of each deciding.
        status.recordWifiAvailability(wifi)
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

    // V3_REFRESH_PREREQUISITE_POLICY_V1: Test Refresh uses the same
    // authoritative prerequisite contract as Home Refresh All. A known-missing
    // pairing file must never post the scheduler notification, so no backend
    // mutation is started, and it must never be reported as an unexplained
    // refresh failure.
    func runTestRefresh(status: V3SideStoreStatusStore) {
        guard !testRunning else { return }
        let requestID = UUID().uuidString
        if let failure = V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing)
            .failure(correlationID: requestID) {
            testRunning = false
            testRequestID = nil
            testRunID = nil
            testTask = nil
            recordFailure(operation: failure.operation, stage: failure.stage.rawValue,
                          code: failure.code.rawValue, correlation: failure.correlationID,
                          retryable: "false")
            verification = V3SetupStepState(state: "actionRequired", detail: failure.safeMessage)
            verificationGuidance = "Place or import a valid pairing file, then try again."
            NSLog("[V3_SETUP] TEST_REFRESH_BLOCKED reason=pairing request_id=%@", requestID)
            return
        }
        testRunning = true
        testRequestID = requestID
        testRunID = nil
        verification = V3SetupStepState(state: "running", detail: "Test refresh running…")
        verificationGuidance = ""
        NSLog("[V3_SETUP] TEST_REFRESH_START request_id=%@ origin=setupAssistant", requestID)
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

    // A human-copyable pairing word for the diagnostics block. The internal
    // policy states are not the vocabulary a person reading a support log wants.
    static func describePairing(_ state: V3RefreshPrerequisiteState) -> String {
        switch state {
        case .satisfied: return "available"
        case .unsatisfied: return "missing"
        case .unknown: return "unknown"
        }
    }

    func buildDiagnostics(status: V3SideStoreStatusStore) {
        var lines: [String] = ["Setup Assistant"]
        lines.append("Product: " + (Bundle.main.object(forInfoDictionaryKey: "LCProductLine") as? String ?? "unknown"))
        lines.append("iOS: " + UIDevice.current.systemVersion)
        lines.append("Pairing: " + V3SetupStore.describePairing(V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).state))
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
    @EnvironmentObject private var sharedModel: SharedModel
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @StateObject private var setup = V3SetupStore()
    @State private var vpnWorking = false
    @State private var copiedDiagnostics = false
    // V3_RECHECK_PAIRING_ON_RETURN_V1: the pairing file is placed by an
    // external installation tool, so returning to a live Quick Setup is the
    // moment a newly placed file can be detected.
    @State private var showPairingSetup = false
    var body: some View {
        List {
            Section("Device") {
                setupRow(icon: "app.badge.checkmark", title: "App Running",
                         state: setup.device, destination: nil)
                setupRow(icon: "graduationcap", title: "Developer Mode",
                         state: V3SetupStepState(state: "warning", detail: "Guidance only: keep Developer Mode on in iOS Settings. Setup continues regardless."),
                         destination: nil)
            }
            // V3_PAIRING_PLACEMENT_FIRST_V1: the pairing mechanism works. The
            // documented normal path is placing the file with the installation
            // tool, so that is what the row explains first. Manual import stays
            // available as the secondary path.
            Section("Pairing") {
                setupRow(icon: "link", title: "Pairing File",
                         state: setup.pairing,
                         destination: AnyView(V3PairingView().environmentObject(status)))
                if setup.pairing.state == "actionRequired" {
                    Button {
                        showPairingSetup = true
                    } label: {
                        Label("Show Pairing Setup", systemImage: "link.badge.plus")
                    }
                    Button {
                        Task {
                            // The pairing file is placed by an external installation tool, so
                            // the authoritative snapshot is the only way to observe it.
                            // It is awaited before the setup steps are recomputed.
                            await status.reloadAndWait()
                            await setup.recalculate(status: status)
                        }
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                }
            }
            .sheet(isPresented: $showPairingSetup) {
                // NavigationView, not NavigationStack: the host target deploys
                // to iOS 15.
                NavigationView { V3PairingView().environmentObject(status) }
                    .navigationViewStyle(StackNavigationViewStyle())
            }
            Section("Apple Account") {
                setupRow(icon: "person.crop.circle", title: "Apple ID",
                         state: setup.account,
                         destination: AnyView(V3SignInView().environmentObject(status)))
            }
            if ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26 {
                // V3_JITLESS_PRESENTATION_V1: a ready JIT-Less state is rendered
                // as a completed result, not as an outstanding setup task. The
                // section only presents required actions while something is
                // actually outstanding.
                let jitless = V3JITLessPresentation.present(setup.jitlessReadiness)
                Section("JIT-Less Mode") {
                    setupRow(icon: jitless.icon, title: "JIT-Less",
                             state: V3SetupStepState(
                                state: jitless.isOutstandingSetupTask ? setup.jitless.state : "complete",
                                detail: jitless.title),
                             destination: jitless.isOutstandingSetupTask
                                ? setup.jitlessDestination(status: status)
                                : nil)
                    if !jitless.isOutstandingSetupTask {
                        // Optional diagnostic only. It must not look like setup.
                        Button {
                            sharedModel.selectedTab = .settings
                            sharedModel.deepLink = URL(string: "livecontainer://jitless-diagnose")
                        } label: {
                            Label("Open JIT-Less Diagnose", systemImage: "stethoscope")
                        }
                        .font(.caption)
                    } else {
                        switch setup.jitlessReadiness {
                        case .setupRequired, .activeCertificateMissing:
                            Button("Set Up JIT-Less") { openCanonicalJITLessSetup() }
                        case .needsCertificateRefresh, .certificateMismatch, .revoked:
                            Button("Refresh JIT-Less Certificate") { openCanonicalJITLessSetup() }
                        case .activeCertificateRevoked, .activeCertificateExpired:
                            Button("Open Certificates") { status.certificatesPresented = true }
                        case .unknown, .certificateImported:
                            Button("Open JIT-Less Setup") { openCanonicalJITLessSetup() }
                        case .ready, .notRequired:
                            EmptyView()
                        }
                    }
                }
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
                if !setup.verificationGuidance.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("What you can do").font(.caption.weight(.semibold))
                        Text(setup.verificationGuidance).font(.footnote)
                    }
                }
                // V3_REFRESH_PREREQUISITE_POLICY_V1: a structured prerequisite
                // failure is shown for failed and action-required states alike,
                // so a known-missing pairing file is never reported as
                // "no safe underlying cause was available".
                if (setup.verification.state == "failed" || setup.verification.state == "actionRequired")
                    && !setup.failureOperation.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("operation=\(setup.failureOperation) stage=\(setup.failureStage) code=\(setup.failureCode)")
                            .font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                        Text("correlation=\(setup.failureCorrelation) retryable=\(setup.failureRetryable)")
                            .font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                    }
                }
                if setup.verification.state == "actionRequired" && setup.failureStage == CombinedFailure.Stage.pairing.rawValue {
                    Button {
                        showPairingSetup = true
                    } label: {
                        Label("Show Pairing Setup", systemImage: "link.badge.plus")
                    }
                    Button {
                        Task {
                            // The pairing file is placed by an external installation tool, so
                            // the authoritative snapshot is the only way to observe it.
                            // It is awaited before the setup steps are recomputed.
                            await status.reloadAndWait()
                            await setup.recalculate(status: status)
                        }
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                }
                if setup.testRunning {
                    Button("Cancel Test", role: .cancel) { setup.cancelTest() }
                } else if setup.verification.state != "complete" {
                    Button {
                        setup.runTestRefresh(status: status)
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
            // V3_SETUP_COMPLETION_POLICY_V1: when setup is not complete, name
            // the outstanding items from the one shared policy, so the assistant
            // and the Home banner can never disagree about what is left.
            if !setup.outstandingSetup.isEmpty {
                Section("Still Needed") {
                    ForEach(setup.outstandingSetup, id: \.self) { item in
                        Label(item.title, systemImage: V3StatusSeverity.warning.icon)
                            .font(.footnote)
                            .foregroundColor(.orange)
                    }
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
        .task {
            // V3_AWAITABLE_RELOAD_V1: the first view of the assistant must be
            // built from an authoritative snapshot, not from whatever was left
            // over from a previous session.
            await status.reloadAndWait()
            await setup.recalculate(status: status)
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active {
                Task {
                    // A pairing file placed by the installation tool while the app
                    // was backgrounded is detected here. The reload must complete
                    // before recalculate reads status, otherwise the setup rows
                    // are computed from the previous snapshot.
                    await status.reloadAndWait()
                    await setup.recalculate(status: status)
                }
            }
        }
        .onChange(of: showPairingSetup) { presented in
            if !presented {
                Task {
                    await status.reloadAndWait()
                    await setup.recalculate(status: status)
                }
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
                // V3_AWAITABLE_RELOAD_V1: returning from a setup destination may
                // follow an action that changed authoritative state, so the
                // snapshot is awaited before the steps are recomputed.
                Task {
                    await status.reloadAndWait()
                    await setup.recalculate(status: status)
                }
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

    private func openCanonicalJITLessSetup() {
        status.pendingCanonicalJITLessSetup = true
        status.returnToSetupAfterJITLess = true
        status.setupPresented = false
    }
}

// V3_STATUS_TINT_V1
// The semantic colour for a status. Declared here rather than beside the model
// because the behavioral primitives are also compiled into the SideStoreSupport
// target, which does not import SwiftUI. The model stays presentation-free and
// testable; only this mapping knows about Color.
extension V3StatusPresentation {
    var tint: Color {
        switch severity {
        case .working: return .blue
        case .completed: return .green
        case .warning: return .orange
        case .failed: return .red
        case .cancelled: return .orange
        case .unknown: return .secondary
        }
    }
}

// The JIT-Less presentation carries a severity of its own, so it reuses the
// same mapping rather than inventing a second colour vocabulary.
extension V3JITLessPresentation {
    var status: V3StatusPresentation {
        V3StatusPresentation(severity: severity, title: title, detail: detail)
    }

    var tint: Color { status.tint }
}

struct V3HomeServiceHeader: View {
    let isConnected: Bool
    let isLoading: Bool
    let updatedAt: Date?
    var onReload: () -> Void = {}

    // V3_RELOAD_STATUS_VISIBILITY_V1
    // isConnected used to win over isLoading, so a reload in progress still
    // rendered a green "Active & Connected" and the only difference was a
    // disabled button, which read as "nothing happened". Loading now has
    // priority, and a successful manual reload is confirmed in place rather
    // than by an intrusive repeated alert.
    private var statusPresentation: V3StatusPresentation {
        V3StatusPresentation.connectionState(connected: isConnected, loading: isLoading)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: "shippingbox.circle.fill")
                    .font(.system(size: 38))
                    .foregroundColor(.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("LiveContainer + SideStore")
                        .font(.headline)
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            if isLoading {
                                ProgressView()
                                    .controlSize(.mini)
                            } else {
                                Circle()
                                    .fill(statusPresentation.tint)
                                    .frame(width: 8, height: 8)
                            }
                            // Icon and text both carry the state, so the meaning
                            // does not depend on colour alone.
                            Label(statusPresentation.title, systemImage: statusPresentation.icon)
                                .font(.caption)
                                .foregroundColor(statusPresentation.tint)
                        }
                        if let updatedAt {
                            Text("Updated " + updatedAt.formatted(date: .omitted, time: .standard))
                                .font(.caption2)
                                .foregroundColor(.secondary)
                                .accessibilityLabel("Status last updated " + updatedAt.formatted(date: .abbreviated, time: .shortened))
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Button(action: onReload) {
                Label {
                    Text(isLoading ? "Reloading Status..." : "Reload Status")
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
        .accessibilityElement(children: .contain)
        .accessibilityLabel("SideStore status: " + statusPresentation.title
                            + ", " + statusPresentation.severityName)
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
    // V3_SETUP_COMPLETION_POLICY_V1: Home consumes the same policy as the Setup
    // Assistant. The banner used to omit JIT-Less, network, and tunnel, so it
    // could disappear while the assistant still considered setup incomplete.
    private var setupIncomplete: Bool { !V3HomeView.completionInputs(status: status, defaults: defaults).isComplete }

    /// Derived from the same authoritative facts the Setup Assistant uses.
    static func completionInputs(status: V3SideStoreStatusStore,
                                 defaults: UserDefaults?) -> V3SetupCompletionInputs {
        let verifiedRunID = defaults?.dictionary(forKey: "liveContainerAutoRefreshVerification")?["run_id"] as? String
        return V3SetupCompletionInputs(
            accountComplete: !status.needsSignIn,
            provisioningIncomplete: status.provisioningIncomplete,
            pairingSatisfied: !V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).blocksRefresh,
            // JIT-Less is only a prerequisite on the platforms that require it.
            jitlessRequired: V3JITLessCompletionPolicy.isRequired(
                osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion),
            // V3_SHARED_JITLESS_FACT_V1: Home reads the same observed readiness the
            // Setup Assistant uses. It previously hard-coded "incomplete wherever
            // JIT-Less is required", so a verified copy left the banner up forever
            // while the assistant showed the item complete.
            jitlessComplete: V3JITLessCompletionPolicy.isComplete(status.jitlessReadiness),
            networkComplete: status.wifiAvailable == true,
            tunnelComplete: LiveContainerNetworkPreflight.hasTunnelInterface(),
            backgroundRefreshAvailable: UIApplication.shared.backgroundRefreshStatus == .available,
            scheduleEnabled: defaults?.bool(forKey: "liveContainerAutoRefreshEnabled") ?? false,
            verifiedRefreshPresent: verifiedRunID?.isEmpty == false)
    }

    var body: some View {
        NavigationView {
            List {
                Section {
                    VStack(alignment: .leading, spacing: 14) {
                        V3HomeServiceHeader(isConnected: status.connected, isLoading: status.loading,
                                            updatedAt: status.updatedAt) {
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
