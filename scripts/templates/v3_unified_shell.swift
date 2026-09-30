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

private final class V3AuthReadinessSequenceStorage: @unchecked Sendable {
    private let lock = NSLock()
    private var value: UInt64 = 0

    func next() -> UInt64 {
        lock.lock()
        defer { lock.unlock() }
        value &+= 1
        if value == 0 { value = 1 }
        return value
    }
}

// V3_AUTH_READINESS_REFRESH_EVENT_V1: auth tasks can outlive the sign-in view.
// Send terminal certificate-state changes to the app-owned root observer.
enum V3AuthReadinessRefreshEvent {
    private static let sequenceStorage = V3AuthReadinessSequenceStorage()
    static let notificationName = Notification.Name("V3AuthReadinessRefresh")
    static let sessionIDKey = "sessionID"
    static let attemptSequenceKey = "attemptSequence"

    static func nextAttemptSequence() -> UInt64 {
        sequenceStorage.next()
    }

    static func post(sessionID: String?, attemptSequence: UInt64?) {
        guard let sessionID, UUID(uuidString: sessionID)?.uuidString == sessionID,
              let attemptSequence, attemptSequence > 0 else { return }
        NotificationCenter.default.post(name: notificationName, object: nil,
            userInfo: [sessionIDKey: sessionID, attemptSequenceKey: attemptSequence])
    }

    static func sessionID(from notification: Notification) -> String? {
        guard let sessionID = notification.userInfo?[sessionIDKey] as? String,
              UUID(uuidString: sessionID)?.uuidString == sessionID else { return nil }
        return sessionID
    }

    static func attemptSequence(from notification: Notification) -> UInt64? {
        guard let number = notification.userInfo?[attemptSequenceKey] as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        // NSNumber.uint64Value wraps negatives and truncates floating-point
        // inputs. Accept only Objective-C integer encodings, then parse their
        // exact decimal representation so overflow and negative values fail.
        let type = String(cString: number.objCType)
        guard ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(type),
              let sequence = UInt64(number.stringValue) else { return nil }
        return sequence
    }
}

struct V3AuthReadinessRefreshEventLedger {
    private(set) var highestConsumedAttemptSequence: UInt64 = 0

    mutating func claim(attemptSequence: UInt64?) -> Bool {
        guard let attemptSequence, attemptSequence > highestConsumedAttemptSequence else { return false }
        highestConsumedAttemptSequence = attemptSequence
        return true
    }
}

// V3_SETUP_READINESS_SNAPSHOT_POLICY_V1: a cached readiness belongs to the
// exact setup-fact revision that observed it. Keep the active-certificate fact
// in the same value so consumers cannot combine facts from different reads.
struct V3SetupReadinessObservation: Equatable {
    let readiness: V3JITLessReadiness
    let sourceFactRevision: UInt64
    let activeCertificateAvailable: Bool?
}

enum V3SetupReadinessObservationPolicy {
    static func shouldFetchLocalReadiness(_ observation: V3SetupReadinessObservation?) -> Bool {
        observation == nil
    }

    static func shouldFetchLocalReadiness(_ observation: V3SetupReadinessObservation?,
                                          currentFactRevision: UInt64) -> Bool {
        guard let observation else { return true }
        return observation.sourceFactRevision != currentFactRevision
    }

    static func mayApplyFreshObservation(sourceFactRevision: UInt64,
                                         currentFactRevision: UInt64) -> Bool {
        sourceFactRevision == currentFactRevision
    }
}

// V3_MULTISELECT_PROMPT_ANSWER_POLICY_V1: option IDs representing actions must
// be routed as choices, while only member IDs may enter the selected-ID list.
enum V3MultiSelectPromptAnswerPolicy {
    static func isMemberOption(kind: String, optionID: String) -> Bool {
        if ["keep", "keepAll"].contains(optionID) { return false }
        if kind == "extensions" && optionID == "removeAll" { return false }
        return true
    }

    static func actionAnswer(_ actionID: String, fields: [String: String]) -> [String: String] {
        var answer = fields
        answer["choice"] = actionID
        answer.removeValue(forKey: "ids")
        answer.removeValue(forKey: "serials")
        return answer
    }

    static func selectedMembersAnswer(kind: String, selectedIDs: Set<String>,
                                      fields: [String: String]) -> [String: String] {
        var answer = fields
        answer["choice"] = kind == "revocation" ? "revoke" : "selected"
        answer["ids"] = selectedIDs.sorted().joined(separator: ",")
        answer["serials"] = selectedIDs.sorted().joined(separator: ",")
        return answer
    }
}

enum V3AuthRetryReadinessReconciliationPolicy {
    static func shouldPublish(sessionID: String?, expectedSessionID: String?, ownsRetry: Bool,
                              authenticated: Bool, authenticationActive: Bool,
                              provisioningIncomplete: Bool, attemptSequence: UInt64?) -> Bool {
        guard let sessionID, UUID(uuidString: sessionID)?.uuidString == sessionID,
              sessionID == expectedSessionID, ownsRetry, authenticated,
              !authenticationActive, !provisioningIncomplete,
              let attemptSequence else { return false }
        return attemptSequence > 0
    }
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
    @State private var showOperationDeviceCheck = false
    @State private var showRefreshDeviceCheck = false
    @State private var showUnreadableRecoveryDeviceCheck = false
    @State private var showDirectRecoveryDeviceCheck = false
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
            // Orphan pruning needs a SideStore ownership query and can wait for
            // a cold service start. Launch it only after first status and any
            // incoming install/setup route have been admitted.
            await status.cleanupOrphanedStagedIPAs()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            if !status.setupPresented { status.invalidateSetupFacts() }
            status.reload(manual: false)
            routePendingSetup()
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3StatusMutationReserved"))) { _ in
            status.statusAuthorityInvalidated()
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3StatusAuthorityChanged"))) { _ in
            status.statusAuthorityInvalidated()
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3CanonicalJITLessCertificateUpdated"))) { _ in
            // V3_AWAITABLE_RELOAD_V1: a certificate import just changed
            // authoritative state. The snapshot is awaited before Setup is
            // reopened, so the assistant never recomputes JIT-Less from the
            // pre-import snapshot.
            Task {
                status.invalidateSetupFacts()
                let outcome = await status.reloadAndWait()
                guard V3SetupReloadRecomputePolicy.mayRecompute(
                    outcome: outcome.setupSnapshotOutcome) else {
                    status.invalidateSetupFacts()
                    status.notice = "Setup status was not refreshed. Reload Status before continuing."
                    return
                }
                if status.returnToSetupAfterJITLess {
                    status.returnToSetupAfterJITLess = false
                    status.setupPresented = true
                }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: V3AuthReadinessRefreshEvent.notificationName)) { notification in
            guard let sessionID = V3AuthReadinessRefreshEvent.sessionID(from: notification),
                  let attemptSequence = V3AuthReadinessRefreshEvent.attemptSequence(from: notification) else { return }
            status.refreshSetupFactsAfterAuthentication(sessionID: sessionID,
                attemptSequence: attemptSequence)
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
        .overlay(alignment: .top) {
            if let recovery = status.unresolvedOperationRecovery {
                VStack(alignment: .leading, spacing: 8) {
                    Text("A previous \(recovery.kind) may still be running")
                        .font(.subheadline.weight(.semibold))
                    Text(recovery.phase == .dispatched
                        ? "Check its status or confirm on the device that the operation has stopped."
                        : "The service prepared an operation but did not confirm dispatch. Check the device before clearing this hold.")
                        .font(.caption)
                    if recovery.phase == .dispatched {
                        Button("Resume Status Check") { status.resumeOperationRecovery() }
                            .font(.caption.weight(.semibold))
                    }
                    Button("I checked; device operation has stopped") {
                        showOperationDeviceCheck = true
                    }
                    .font(.caption.weight(.semibold))
                    .confirmationDialog("Reconcile the previous operation?", isPresented: $showOperationDeviceCheck,
                        titleVisibility: .visible) {
                        Button("I checked; clear the recovery hold", role: .destructive) {
                            status.reconcileDurableOperationAfterDeviceCheck()
                        }
                        Button("Keep waiting", role: .cancel) {}
                    } message: {
                        Text("Only continue after confirming the device is no longer installing, updating, refreshing, or deleting the app.")
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal, 12)
                .padding(.top, 4)
            } else if let direct = status.unresolvedDirectRecovery {
                VStack(alignment: .leading, spacing: 8) {
                    Text("A previous \(V3DirectRecoveryPresentationPolicy.operationName(direct.operation)) has an unresolved result")
                        .font(.subheadline.weight(.semibold))
                    Text(V3DirectRecoveryPresentationPolicy.explanation(
                        record: direct, postcondition: status.directRecoveryPostcondition))
                        .font(.caption)
                    Button(status.directRecoveryInspecting ? "Inspecting..." : "Inspect result") {
                        status.inspectDirectRecovery()
                    }
                    .disabled(status.directRecoveryInspecting)
                        .font(.caption.weight(.semibold))
                    if V3DirectRecoveryHostPolicy.mayOfferUserConfirmation(
                        direct, postcondition: status.directRecoveryPostcondition) {
                        Button("I checked the account or device", role: .destructive) {
                            showDirectRecoveryDeviceCheck = true
                        }
                        .font(.caption.weight(.semibold))
                        .confirmationDialog("Reconcile the previous request?",
                            isPresented: $showDirectRecoveryDeviceCheck, titleVisibility: .visible) {
                            Button("Confirm after checking", role: .destructive) {
                                status.reconcileDirectRecoveryAfterUserCheck()
                            }
                            Button("Keep recovery hold", role: .cancel) {}
                        } message: {
                            Text("Only clear this request after checking the account, source, certificate, pairing, or setting it may have changed.")
                        }
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal, 12)
                .padding(.top, 4)
            } else if status.unresolvedRefreshRecoveryRunID != nil {
                VStack(alignment: .leading, spacing: 8) {
                    Text("A previous refresh may still be running")
                        .font(.subheadline.weight(.semibold))
                    Text("The service lost its refresh owner after the wait limit. Check the device before clearing the hold.")
                        .font(.caption)
                    Button("I checked; refresh has stopped") { showRefreshDeviceCheck = true }
                        .font(.caption.weight(.semibold))
                        .confirmationDialog("Reconcile the previous refresh?", isPresented: $showRefreshDeviceCheck,
                            titleVisibility: .visible) {
                            Button("I checked; clear the refresh hold", role: .destructive) {
                                status.reconcileLostRefreshAfterDeviceCheck()
                            }
                            Button("Keep waiting", role: .cancel) {}
                        } message: {
                            Text("Only continue after confirming SideStore is no longer refreshing apps on the device.")
                        }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal, 12)
                .padding(.top, 4)
            } else if status.unresolvedRecoveryJournalUnreadable {
                VStack(alignment: .leading, spacing: 8) {
                    Text(status.recoveryStorageTitle)
                        .font(.subheadline.weight(.semibold))
                    Text(status.recoveryStorageGuidance)
                        .font(.caption)
                    if status.canDiscardUnreadableRecovery {
                        Button("I checked; no SideStore operation is running") {
                            showUnreadableRecoveryDeviceCheck = true
                        }
                        .font(.caption.weight(.semibold))
                        .confirmationDialog("Clear the unreadable recovery record?",
                            isPresented: $showUnreadableRecoveryDeviceCheck, titleVisibility: .visible) {
                            Button("Clear after device check", role: .destructive) {
                                status.discardUnreadableRecoveryAfterDeviceCheck()
                            }
                            Button("Keep waiting", role: .cancel) {}
                        } message: {
                            Text("Only continue after confirming the device is no longer installing, updating, refreshing, or deleting an app.")
                        }
                    }
                    Button("Copy Diagnostics") {
                        UIPasteboard.general.string = status.recoveryStorageDiagnostics
                    }
                    .font(.caption.weight(.semibold))
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal, 12)
                .padding(.top, 4)
            }
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
            // evidence supports. Connection failures open Connection Settings,
            // and "Retry Source" re-requests sources rather than reloading status.
            //
            // A plain message with no structured issue has no action to offer.
            // It used to render a button labelled "OK" that then did nothing,
            // alongside a second "OK" that dismissed, so a refusal to work
            // looked like a choice.
                if let action = status.issue?.primaryAction, action != .dismiss {
                Button(action.title) {
                    if status.performPrimaryIssueAction() {
                        status.clearIssue()
                    }
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
                Task { await LiveContainerAutoRefreshScheduler.requestNotificationPermissionFromUserAction() }
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
        case "sources": sharedModel.selectedTab = .sources
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
            let source = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "url" })?.value
            status.requestSourceForm(prefilledURL: source)
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
            if V3RefreshAllButtonPresentationPolicy.explainsConcurrentRun(
                phase: attempt.phase, activeRunID: activeRun) {
                Text("Another refresh is already running. Refresh All will be available when it finishes.")
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }
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
                                    ? "Retry (retryability unknown)" : "Retry") {
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
        V3RefreshAllButtonPresentationPolicy.title(phase: attempt.phase, activeRunID: activeRun)
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
        if let failure = V3RefreshPrerequisite.evaluate(statusConnected: status.connected,
                pairingStatus: status.pairing)
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
            let manifest = record["manifest"] as? [String: Any]
                ?? record["manifest_summary"] as? [String: Any] ?? [:]
            let verifiedCount = (manifest["results"] as? [[String: Any]])?.count ??
                V3RefreshAllTerminalEvidencePolicy.count("result_count", in: manifest) ?? 0
            let skippedCount = (manifest["skipped_ids"] as? [String])?.count ??
                V3RefreshAllTerminalEvidencePolicy.count("skipped_count", in: manifest) ?? 0
            message = attempt.terminalMessage
            terminalFailure = nil
            diagnostics = "manual_refresh_request=\(requestID)\nrun_id=\(runID)\nstate=completed\nverified_app_count=\(verifiedCount)\nskipped_app_count=\(skippedCount)"
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
            diagnostics = V3RefreshAllFailureDiagnostics.withoutRunRecord(
                requestID: requestID, runID: runID.isEmpty ? nil : runID,
                message: message, health: health) ?? "schema=1\noperation=refresh\nstage=unknown\ncode=unknown"
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
    let recoverySessionID: String?

    init(id: UUID = UUID(), operation: String, target: String, title: String,
         installAttemptID: UUID? = nil, recoverySessionID: String? = nil) {
        self.id = id
        self.operation = operation
        self.target = target
        self.title = title
        self.installAttemptID = installAttemptID
        self.recoverySessionID = recoverySessionID
    }
}

struct V3PromptAnswer {
    var fields: [String: String] = [:]
    var choice = ""
    var selected: Set<String> = []
}

@MainActor
final class V3SideStoreStatusStore: ObservableObject {
    init() {
        Task.detached(priority: .utility) {
            V3SecretHandoff.cleanupExpiredItems()
            V3SharedFileRecord.removeLegacyDefaultsRecords(LCUtils.appGroupUserDefault)
        }
        if let containerRoot = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) {
            Task.detached(priority: .utility) {
                _ = V3SharedFileRecord.sweep(containerRoot: containerRoot)
            }
        }
    }

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
    @Published private(set) var identityStamp: String?
    @Published private(set) var authenticationActive = false
    // V3_AUTH_LOCAL_STATE_SNAPSHOT_V1: persisted account/team/certificate
    // presence is separate from credential readability and display strings.
    @Published private(set) var activeAccountPresent = false
    @Published private(set) var activeTeamPresent = false
    @Published private(set) var activeCertificatePresent = false
    @Published private(set) var provisioningIncomplete = false
    // V3_SETUP_COMPLETION_POLICY_V1: the last authoritative Wi-Fi observation.
    // Probing Wi-Fi is async, so Home reads this cache instead of guessing.
    // nil means "not observed yet", which counts as outstanding. It is written
    // only through recordWifiAvailability so the cached fact has one owner.
    @Published private(set) var wifiAvailable: Bool?

    /// Records the authoritative Wi-Fi observation for the shared setup policy.
    private var setupFactRevision: UInt64 = 0

    func beginSetupFactObservation() -> UInt64 {
        setupFactRevision &+= 1
        return setupFactRevision
    }

    var currentSetupFactRevision: UInt64 { setupFactRevision }

    func isSetupFactRevisionCurrent(_ revision: UInt64) -> Bool {
        V3SetupReadinessObservationPolicy.mayApplyFreshObservation(
            sourceFactRevision: revision, currentFactRevision: setupFactRevision)
    }

    func recordWifiAvailability(_ available: Bool, revision: UInt64? = nil) {
        if let revision, !isSetupFactRevisionCurrent(revision) { return }
        wifiAvailable = available
    }

    func invalidateSetupFacts() {
        setupFactRevision &+= 1
        setupFactObservation = .pending
        setupFactLastAttemptAt = nil
        wifiAvailable = nil
        jitlessReadinessObservation = nil
        jitlessReadiness = nil
        jitlessActiveCertificateAvailable = nil
    }

    func markSetupFactsObserved(revision: UInt64? = nil) {
        if let revision, !isSetupFactRevisionCurrent(revision) { return }
        setupFactObservation = .observed
        setupFactLastAttemptAt = Date()
    }

    // V3_SHARED_JITLESS_FACT_V1: the last authoritative JIT-Less readiness.
    // Health, the Setup Assistant and the store's own setup-fact observation all
    // publish it, and Home reads it, so no surface can claim a different
    // completion answer. nil means "not observed yet" and counts as
    // outstanding, so a fact nothing observes can never read as satisfied.
    @Published private(set) var jitlessReadinessObservation: V3SetupReadinessObservation?
    // Compatibility projections for existing UI consumers. The observation is
    // authoritative; all three values are updated together in this owner.
    @Published private(set) var jitlessReadiness: V3JITLessReadiness?
    @Published private(set) var jitlessActiveCertificateAvailable: Bool?

    /// Publishes an observed JIT-Less readiness for every setup surface to share.
    func recordJITLessReadiness(_ readiness: V3JITLessReadiness,
                                activeCertificateAvailable: Bool? = nil,
                                revision: UInt64? = nil) {
        if let revision, !isSetupFactRevisionCurrent(revision) { return }
        jitlessReadinessObservation = V3SetupReadinessObservation(
            readiness: readiness,
            sourceFactRevision: revision ?? setupFactRevision,
            activeCertificateAvailable: activeCertificateAvailable)
        jitlessReadiness = readiness
        jitlessActiveCertificateAvailable = activeCertificateAvailable
    }

    // V3_SETUP_FACT_OBSERVATION_V1
    // The two facts above were only ever observed by the Setup Assistant and by
    // Health. A user who opened neither left them nil, and nil is outstanding by
    // policy, so on a platform where JIT-Less is required the Home banner could
    // never clear no matter how correct the underlying state was. The store
    // observes them after an authoritative snapshot and refreshes them on a
    // bounded cadence or explicit lifecycle/certificate invalidation.
    //
    // The attempt is tri-state rather than a boolean so a failure cannot become
    // an unbounded retry loop against a service that is not answering, and so a
    // deliberate reload can still ask again.
    private enum SetupFactObservation: Equatable {
        /// Not attempted yet.
        case pending
        /// Both facts have been observed for the current cache interval.
        case observed
        /// Attempted and the service did not answer. Retried only after the cache interval.
        case deferred
    }
    private var setupFactObservation: SetupFactObservation = .pending
    private var setupFactLastAttemptAt: Date?
    private var authReadinessRefreshEventLedger = V3AuthReadinessRefreshEventLedger()
    private var setupFactObservationGeneration: UInt64 = 0
    private var setupFactObservationTask: Task<Void, Never>?
    private var authReadinessEventGeneration: UInt64 = 0
    private var authReadinessObservationTask: Task<Void, Never>?

    /// Observes the shared setup facts once, when they are the only thing
    /// standing between the user and a cleared setup banner.
    private func observeSetupFactsIfNeeded() {
        let osMajor = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
        guard V3SetupFactObservationPolicy.shouldObserve(
            connected: connected, setupPresented: setupPresented,
            operationPresented: presentation != nil, loading: loadActivity != .idle,
            returnToSetupPending: returnToSetupAfterJITLess,
            lastAttemptAt: setupFactLastAttemptAt) else { return }
        // On legacy iOS, Wi-Fi is the only setup fact owned here. Do not call
        // the certificate/OCSP health endpoint when JIT-Less is not required.
        if !V3JITLessCompletionPolicy.isRequired(osMajor: osMajor), wifiAvailable != nil {
            setupFactObservation = .observed
            setupFactLastAttemptAt = Date()
            return
        }
        setupFactObservation = .deferred
        setupFactLastAttemptAt = Date()
        startSetupFactObservation()
    }

    /// The app-owned root view consumes auth events so sign-in presentation
    /// lifetime cannot drop a committed certificate change. A session event is
    /// claimed once before it invalidates any in-flight observation.
    func refreshSetupFactsAfterAuthentication(sessionID: String, attemptSequence: UInt64) {
        guard UUID(uuidString: sessionID)?.uuidString == sessionID,
              authReadinessRefreshEventLedger.claim(attemptSequence: attemptSequence) else { return }
        invalidateSetupFacts()
        setupFactObservation = .deferred
        setupFactLastAttemptAt = Date()
        authReadinessEventGeneration &+= 1
        startSetupFactObservation()
        authReadinessObservationTask = setupFactObservationTask
    }

    private func startSetupFactObservation() {
        setupFactObservationGeneration &+= 1
        setupFactObservationTask = Task { await observeSetupFacts() }
    }

    /// Quick Setup reuses the latest shared observation when it is already
    /// running or completed, instead of issuing a second health snapshot.
    func awaitAuthReadinessRefresh() async -> V3SetupReadinessObservation? {
        while let task = authReadinessObservationTask {
            let generation = authReadinessEventGeneration
            await task.value
            if generation == authReadinessEventGeneration {
                return jitlessReadinessObservation
            }
        }
        return nil
    }

    func awaitSharedSetupJITLessReadiness() async -> V3SetupReadinessObservation? {
        if let authReadiness = await awaitAuthReadinessRefresh() {
            return authReadiness
        }
        while let task = setupFactObservationTask {
            let generation = setupFactObservationGeneration
            await task.value
            if generation == setupFactObservationGeneration {
                return jitlessReadinessObservation
            }
        }
        return jitlessReadinessObservation
    }

    private func observeSetupFacts() async {
        let revision = beginSetupFactObservation()
        // Wi-Fi is a host-side fact, so it is probed here rather than asked of
        // the service. An unavailable answer is recorded as unavailable, not as
        // unknown, because the probe is the authority for it.
        let wifi = await LiveContainerNetworkPreflight.wifiAvailable()
        guard isSetupFactRevisionCurrent(revision) else { return }
        recordWifiAvailability(wifi, revision: revision)
        guard V3JITLessCompletionPolicy.isRequired(
            osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion) else {
            recordJITLessReadiness(.notRequired, revision: revision)
            markSetupFactsObserved(revision: revision)
            return
        }
        do {
            let health = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
            guard isSetupFactRevisionCurrent(revision) else { return }
            let certificate = health["certificateState"] as? [String: Any] ?? [:]
            let readiness = await V3JITLessStatusReader.read(serviceCertificate: certificate)
            guard isSetupFactRevisionCurrent(revision) else { return }
            recordJITLessReadiness(readiness.readiness,
                activeCertificateAvailable: V3ServiceBridge.strictBool(certificate["active"]),
                revision: revision)
            markSetupFactsObserved(revision: revision)
        } catch {
            guard isSetupFactRevisionCurrent(revision) else { return }
            // Unobserved is published as unknown, so the item stays outstanding
            // rather than the banner claiming a certificate exists.
            recordJITLessReadiness(.unknown, revision: revision)
        }
    }
    @Published private(set) var updatedAt: Date?
    @Published private(set) var installedApps: [V3SideStoreApp] = []
    @Published private(set) var sources: [V3SideStoreSource] = []
    @Published private(set) var settings: [String: Bool] = [:]
    @Published var error: String?
    @Published private(set) var unresolvedOperationRecovery: V3OperationRecoveryRecord?
    @Published private(set) var unresolvedRefreshRecoveryRunID: String?
    @Published private(set) var unresolvedRecoveryJournalUnreadable = false
    @Published private(set) var recoveryStorageKind: String?
    @Published private(set) var recoveryStorageClearEligible = false
    @Published private(set) var recoveryStorageDiagnostics = "schema=1 operation=status stage=persistence recovery_storage_kind=unknown"

    var canDiscardUnreadableRecovery: Bool {
        V3RecoveryStoragePresentationPolicy.mayOfferClear(
            connected: connected, unresolved: unresolvedRecoveryJournalUnreadable,
            kind: recoveryStorageKind, serverClearEligible: recoveryStorageClearEligible)
    }

    var recoveryStorageTitle: String {
        switch recoveryStorageKind {
        case "malformedRecord": return "SideStore paused changes because its recovery record is malformed"
        case "incompatibleRecord": return "SideStore paused changes because its recovery record uses an incompatible format"
        case "storageUnavailable": return "SideStore cannot access shared recovery storage"
        case "lockUnavailable": return "SideStore cannot acquire its recovery storage lock"
        case "readFailure": return "SideStore could not read recovery storage"
        case "deleteFailure": return "SideStore could not remove its recovery record"
        default: return "SideStore recovery status is uncertain"
        }
    }

    var recoveryStorageGuidance: String {
        if canDiscardUnreadableRecovery {
            return "Check the device before clearing this saved record. An earlier operation may still be running."
        }
        return "Changes remain paused. This is not proof of a corrupt record. Check shared storage and service status; copy Diagnostics."
    }
    @Published private(set) var unresolvedDirectRecovery: V3HostDirectRecoveryRecord?
    @Published private(set) var directRecoveryPostcondition: V3DirectRecoveryPostcondition?
    @Published private(set) var directRecoveryInspecting = false
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

    func reconcileDurableOperationAfterDeviceCheck() {
        guard let record = unresolvedOperationRecovery else { return }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "opRecoveryReconcile",
                    target: record.sessionID, payload: ["userConfirmed": true])
                guard reply["session"] as? String == record.sessionID,
                      V3ServiceBridge.strictBool(reply["reconciled"]) == true else {
                    throw CombinedFailure(operation: "opRecoveryReconcile", stage: .command,
                        code: .staleResult, id: record.sessionID, retryable: false)
                }
                V3ServiceBridge.shared.retireReconciledOperationService(sessionID: record.sessionID)
                unresolvedOperationRecovery = nil
                notice = "The recovery hold was cleared after your device check."
                reload()
            } catch {
                self.error = "SideStore could not clear the recovery hold. Reconnect and try again."
            }
        }
    }

    func resumeOperationRecovery() {
        guard let record = unresolvedOperationRecovery, record.phase == .dispatched,
              presentation == nil, !installAttempt.hasActiveAttempt else { return }
        let title = "Recover \(record.kind)"
        presentation = V3OperationRequest(operation: record.kind,
            target: record.stagedIPAToken ?? "", title: title,
            recoverySessionID: record.sessionID)
    }

    func reconcileLostRefreshAfterDeviceCheck() {
        guard let runID = unresolvedRefreshRecoveryRunID else { return }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "refreshAdmissionReconcile",
                    target: runID, payload: ["userConfirmed": true])
                guard reply["runID"] as? String == runID,
                      V3ServiceBridge.strictBool(reply["released"]) == true,
                      V3ServiceBridge.strictBool(reply["reconciled"]) == true else {
                    throw CombinedFailure(operation: "refresh", stage: .command,
                        code: .staleResult, id: runID, retryable: false)
                }
                V3ServiceBridge.shared.retireReconciledRefreshService(runID: runID)
                unresolvedRefreshRecoveryRunID = nil
                notice = "The refresh hold was cleared after your device check."
                reload()
            } catch {
                self.error = "SideStore could not clear the refresh hold. Reconnect and try again."
            }
        }
    }

    func inspectDirectRecovery() {
        guard !directRecoveryInspecting, let record = unresolvedDirectRecovery else { return }
        directRecoveryInspecting = true
        Task {
            defer { directRecoveryInspecting = false }
            do {
                let reply = try await V3ServiceBridge.shared.request(
                    operation: "directRecoveryInspect", target: record.requestID)
                guard let inspected = V3HostDirectRecoveryRecord(inspectionReply: reply),
                      inspected.requestID == record.requestID,
                      inspected.operation == record.operation,
                      let postcondition = inspected.postcondition,
                      unresolvedDirectRecovery?.requestID == record.requestID else {
                    throw CombinedFailure(operation: "directRecoveryInspect", stage: .command,
                        code: .staleResult, id: record.requestID, retryable: false)
                }
                unresolvedDirectRecovery = inspected
                directRecoveryPostcondition = postcondition
                if V3DirectRecoveryHostPolicy.mayAcknowledgeInspectedTerminal(
                    inspected, postcondition: postcondition) {
                    _ = await reconcileDirectRecovery(inspected, userConfirmed: false)
                }
            } catch {
                present(error)
                reload()
            }
        }
    }

    func reconcileDirectRecoveryAfterUserCheck() {
        guard let record = unresolvedDirectRecovery,
              V3DirectRecoveryHostPolicy.mayOfferUserConfirmation(
                record, postcondition: directRecoveryPostcondition) else { return }
        Task { _ = await reconcileDirectRecovery(record, userConfirmed: true) }
    }

    private func reconcileDirectRecovery(_ record: V3HostDirectRecoveryRecord,
                                         userConfirmed: Bool) async -> Bool {
        let payload: [String: Any] = record.phase == .terminal
            ? ["ackTerminal": true] : ["userConfirmed": userConfirmed]
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "directRecoveryReconcile",
                target: record.requestID, payload: payload)
            guard reply["requestID"] as? String == record.requestID,
                  V3ServiceBridge.strictBool(reply["reconciled"]) == true else {
                throw CombinedFailure(operation: "directRecoveryReconcile", stage: .command,
                    code: .staleResult, id: record.requestID, retryable: false)
            }
            if unresolvedDirectRecovery?.requestID == record.requestID {
                unresolvedDirectRecovery = nil
                directRecoveryPostcondition = nil
            }
            notice = "The previous request was reconciled."
            reload()
            return true
        } catch {
            present(error)
            return false
        }
    }

    func discardUnreadableRecoveryAfterDeviceCheck() {
        guard canDiscardUnreadableRecovery else { return }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(
                    operation: "recoveryDiscardUnreadable", payload: ["userConfirmed": true])
                guard V3ServiceBridge.strictBool(reply["discardedUnreadable"]) == true else {
                    throw CombinedFailure(operation: "status", stage: .persistence,
                        code: .staleResult, id: UUID().uuidString, retryable: false)
                }
                // The service reread under its lock, then this independent
                // authoritative snapshot must confirm the host hold is gone.
                let outcome = await reloadAndWait()
                guard V3RecoveryStoragePresentationPolicy.confirmsCleared(
                    snapshotApplied: outcome == .applied,
                    unreadable: unresolvedRecoveryJournalUnreadable,
                    operationRecovery: unresolvedOperationRecovery != nil,
                    directRecovery: unresolvedDirectRecovery != nil,
                    refreshRecovery: unresolvedRefreshRecoveryRunID != nil) else {
                    throw CombinedFailure(operation: "status", stage: .persistence,
                        code: .staleResult, id: UUID().uuidString, retryable: true,
                        safeCause: .recoveryReadFailure)
                }
                notice = "The recovery record is absent and SideStore status was verified."
                await cleanupOrphanedStagedIPAs()
            } catch {
                present(error)
            }
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
    func performPrimaryIssueAction() -> Bool {
        guard let action = issue?.primaryAction else { return true }
        switch action {
        case .retrySource:
            return V3IssueActionOutcomePolicy.shouldDismiss(
                action: action, didStart: refreshSources())
        case .reloadSources:
            reload()
            return V3IssueActionOutcomePolicy.shouldDismiss(action: action, didStart: true)
        case .reloadStatus:
            reload()
            return V3IssueActionOutcomePolicy.shouldDismiss(action: action, didStart: true)
        default:
            openIssueRecovery()
            return V3IssueActionOutcomePolicy.shouldDismiss(action: action, didStart: true)
        }
    }

    private func presentUnconfirmedSignOut(_ outcome: V3SignOutOutcome) {
        guard let whatHappened = V3SignOutOutcomePolicy.whatHappened(for: outcome),
              let whatToDo = V3SignOutOutcomePolicy.whatToDo(for: outcome) else { return }
        let issue = V3UserFacingIssue(
            title: "Sign Out",
            severity: .failed,
            whatHappened: whatHappened,
            whatToDo: whatToDo,
            technicalDetails: "schema=1\noperation=signOut\nresult=notConfirmed\noutcome=\(outcome)",
            primaryAction: .reloadStatus,
            secondaryAction: .dismiss,
            recoveryDestination: nil,
            retryDisposition: .unknown)
        self.issue = issue
        error = issue.summary
        notice = nil
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
    @Published private(set) var sourceFormOpenRequestID: UUID?
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
    private var snapshotOwedIntent = V3SnapshotOwedIntent()
    // Monotonic identity for each authoritative snapshot started by this store.
    // Awaiters parked behind a mutation or presentation require a later epoch,
    // even if an older in-flight snapshot finishes after the blocker.
    private var snapshotGeneration: UInt64 = 0
    private var externalMutationCount = 0
    private var directMutationOwners: Set<UUID> = []
    private var coordinatedMutationActive = false
    // Callers awaiting an authoritative snapshot. The registry tracks each
    // caller's minimum generation and manual requirement, because a shared
    // drain must not satisfy a deferred caller with an older in-flight fetch.
    private struct SnapshotWaiter {
        let continuation: CheckedContinuation<V3ReloadOutcome, Never>
    }
    private var snapshotWaiters: [UUID: SnapshotWaiter] = [:]
    private var snapshotWaiterRegistry = V3SnapshotWaiterRegistry()
    private var pendingPickerError: (attemptID: UUID, message: String)?
    @Published private(set) var installAttempt = V3InstallAttemptState()
    var installedAppCount: Int { installedApps.count }
    var hasUncertainInstallCancellation: Bool {
        installAttempt.backendSessionID != nil &&
            (installAttempt.phase == .operationStarted || installAttempt.phase == .operationPresented)
    }
    var isStale: Bool { !connected || (updatedAt.map { Date().timeIntervalSince($0) > 120 } ?? true) }
    var needsSignIn: Bool { V3AuthSnapshotAuthorityPolicy.needsSignIn(authenticated: authenticated) }
    func requestSourceForm(prefilledURL: String?) {
        if let prefilledURL { sourceURL = prefilledURL }
        sourceFormOpenRequestID = UUID()
    }
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
        case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
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

        var setupSnapshotOutcome: V3SetupSnapshotOutcome {
            switch self {
            case .applied: return .applied
            case .snapshotFailed: return .snapshotFailed
            case .notObserved: return .notObserved
            }
        }
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
        guard !Task.isCancelled else { return .notObserved }
        let decision = beginSnapshot(manual: manual, waiterWillBeInstalled: true)
        switch decision {
        case .performSnapshot, .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
            let requiredGeneration = V3SnapshotWaiterEpochPolicy.requiredGeneration(
                for: decision, currentGeneration: snapshotGeneration)
            // The service request belongs to the store, not to the first caller.
            // Cancellation removes only this caller's continuation and cannot
            // cancel the shared request for other waiters.
            let waiterID = UUID()
            return await withTaskCancellationHandler {
                await withCheckedContinuation { continuation in
                    if case .performSnapshot = decision {
                        Task { _ = await performSnapshot() }
                    }
                    guard !Task.isCancelled else {
                        continuation.resume(returning: .notObserved)
                        return
                    }
                    snapshotWaiters[waiterID] = SnapshotWaiter(continuation: continuation)
                    snapshotWaiterRegistry.insert(waiterID, manual: manual,
                        requiredSnapshotGeneration: requiredGeneration)
                }
            } onCancel: {
                Task { @MainActor [weak self] in
                    self?.cancelSnapshotWaiter(waiterID)
                }
            }
        case .doNotObserve:
            // Nothing ran and nothing is owed, so no continuation is parked.
            return .notObserved
        }
    }

    /// Resolves a canceled caller immediately while leaving a shared snapshot
    /// alive for its other waiters and the store's authoritative cache.
    private func cancelSnapshotWaiter(_ id: UUID) {
        guard snapshotWaiterRegistry.remove(id),
              let waiter = snapshotWaiters.removeValue(forKey: id) else { return }
        waiter.continuation.resume(returning: .notObserved)
    }

    /// The shared synchronous gate. It names the activity instead of inferring
    /// one from a shared busy flag, and it is the only place a snapshot is
    /// started or a waiter is parked.
    private func beginSnapshot(manual: Bool,
                               waiterWillBeInstalled: Bool = false) -> V3SnapshotDecision {
        let decision = V3SnapshotGate.decide(
            activity: loadActivity, presentationActive: presentation != nil,
            manual: manual, requiresConnectionRetry: requiresConnectionRetry)
        switch decision {
        case .performSnapshot:
            startSnapshot(manual: manual)
        case .joinSnapshot:
            break
        case .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
            // A snapshot is owed. It is owed once, not once per requester, so a
            // burst of requests cannot queue a burst of fetches.
            // Awaiting callers carry manual intent in the waiter registry so a
            // canceled last waiter cannot force a later retry. Fire-and-forget
            // reloads have no waiter and retain their manual intent here.
            snapshotOwedIntent.record(manual: manual && !waiterWillBeInstalled)
        case .doNotObserve:
            break
        }
        return decision
    }

    /// Claims the service for a snapshot. Starting any snapshot discharges the
    /// owed intent, which is what stops an unrelated reload from leaving a stale
    /// request behind to cause a second fetch.
    private func startSnapshot(manual: Bool) {
        snapshotOwedIntent.clear()
        snapshotGeneration &+= 1
        if manual {
            requiresConnectionRetry = false
            // A deliberate reload is also a deliberate request to try the
            // shared setup facts again, so a previous service failure is not
            // permanent.
            if setupFactObservation == .deferred {
                setupFactObservation = .pending
                setupFactLastAttemptAt = nil
            }
        }
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
        coordinatedMutationActive = true
        loadActivity = .mutation
        loading = true
    }

    /// Begins a view-owned mutation outside runMutation before its first await.
    func beginDirectMutation() -> UUID {
        let owner = UUID()
        directMutationOwners.insert(owner)
        externalMutationCount += 1
        if loadActivity == .snapshot {
            snapshotOwedIntent.record(manual: true)
        } else if loadActivity == .idle {
            loadActivity = .mutation
            loading = true
        }
        return owner
    }

    func finishDirectMutation(ticket: UUID, reply: [String: Any]? = nil,
                              requestReload: Bool = false) {
        guard directMutationOwners.remove(ticket) != nil else { return }
        externalMutationCount = directMutationOwners.count
        if let reply { _ = accept(reply) }
        if requestReload { snapshotOwedIntent.record(manual: true) }
        if externalMutationCount == 0 && !coordinatedMutationActive && loadActivity == .mutation {
            loadActivity = .idle
            loading = false
            drainOwedSnapshot()
        }
    }

    /// A bridge write intent may invalidate a snapshot already in flight or
    /// originate outside a Store-owned mutation wrapper.
    func statusAuthorityInvalidated() {
        if loadActivity == .idle {
            reload(manual: false)
        } else {
            snapshotOwedIntent.record(manual: true)
        }
    }

    private func performSnapshot() async -> V3ReloadOutcome {
        // Only one snapshot may own the service activity at a time, so this
        // generation remains the request's identity until its completion.
        let completedGeneration = snapshotGeneration
        var succeeded = false
        var cancelled = false
        var rejectedAsStale = false
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "snapshot")
            succeeded = accept(reply)
            rejectedAsStale = !succeeded
        } catch {
            if rejectedAsStale {
                rejectedAsStale = true
            } else if V3SnapshotErrorPolicy.shouldMarkDisconnected(error) {
                connected = false
                requiresConnectionRetry = true
                present(error)
            } else {
                cancelled = true
            }
        }
        let outcome: V3ReloadOutcome = succeeded ? .applied :
            ((cancelled || rejectedAsStale) ? .notObserved : .snapshotFailed)
        // The only place a snapshot waiter is ever resumed. State is fully
        // applied first, so no caller can observe a partially updated snapshot.
        finishSnapshot(outcome: outcome, generation: completedGeneration)
        if installAttempt.hasActiveAttempt {
            NSLog("[V3_INSTALL_STATE] attempt=%@ event=snapshot_finished phase=%@",
                  installAttempt.attemptID?.uuidString ?? "none", installAttempt.phase.rawValue)
        }
        drainInstallPresentation(trigger: "snapshot_finished")
        // V3_SETUP_FACT_OBSERVATION_V1: only after an authoritative snapshot has
        // landed, so observation never runs against a disconnected store.
        observeSetupFactsIfNeeded()
        return outcome
    }

    /// V3_AWAITABLE_RELOAD_V1: the single place a snapshot activity ends.
    private func finishSnapshot(outcome: V3ReloadOutcome, generation: UInt64) {
        if externalMutationCount > 0 {
            loadActivity = .mutation
            loading = true
        } else {
            loadActivity = .idle
            loading = false
        }
        // The install presentation gate is snapshot-scoped: it advances only once
        // authoritative state has landed. A mutation advancing it would claim a
        // snapshot had happened.
        installAttempt.reloadFinished()
        for id in snapshotWaiterRegistry.take(throughSnapshotGeneration: generation) {
            guard let waiter = snapshotWaiters.removeValue(forKey: id) else { continue }
            waiter.continuation.resume(returning: outcome)
        }
        drainOwedSnapshot()
    }

    /// V3_LOAD_ACTIVITY_OWNERSHIP_V1: the single place a mutation activity ends.
    /// It resolves nothing. A caller awaiting authoritative status stays parked
    /// until a real snapshot completes, because a mutation's reply says nothing
    /// about the state a snapshot reports.
    private func finishMutation() {
        coordinatedMutationActive = false
        if externalMutationCount > 0 {
            loadActivity = .mutation
            loading = true
        } else {
            loadActivity = .idle
            loading = false
            drainOwedSnapshot()
        }
    }

    /// Runs the single owed snapshot once nothing blocks it.
    ///
    /// The decision is total. If policy refuses the snapshot, every parked
    /// continuation is resumed with `.notObserved` rather than left suspended,
    /// which is what let a non-manual deferred reload hang a task forever.
    private func drainOwedSnapshot() {
        let needsManual = snapshotWaiterRegistry.anyManualWaiter
        switch V3SnapshotGate.drain(activity: loadActivity, presentationActive: presentation != nil,
                                    owed: snapshotOwedIntent.isOwed, anyWaiterNeedsManual: needsManual,
                                    explicitManualOwed: snapshotOwedIntent.requiresManualSnapshot,
                                    requiresConnectionRetry: requiresConnectionRetry) {
        case .performSnapshot:
            startSnapshot(manual: snapshotOwedIntent.requiresManualSnapshot ||
                needsManual || !requiresConnectionRetry)
            Task { _ = await performSnapshot() }
        case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
            // Still blocked. The owed intent is kept for whoever ends it.
            break
        case .doNotObserve:
            guard snapshotOwedIntent.isOwed else { return }
            snapshotOwedIntent.clear()
            for id in snapshotWaiterRegistry.takeAll() {
                guard let waiter = snapshotWaiters.removeValue(forKey: id) else { continue }
                waiter.continuation.resume(returning: .notObserved)
            }
        }
    }
    @discardableResult
    func accept(_ snapshot: [String: Any]) -> Bool {
        guard V3ServiceBridge.shared.statusReplyMayApply(snapshot) else {
            _ = acceptRecoveryEvidence(snapshot)
            return false
        }
        guard let incomingIdentityStamp = snapshot["identityStamp"] as? String,
              !incomingIdentityStamp.isEmpty,
              V3ServiceBridge.strictBool(snapshot["identityStable"]) != nil else { return false }
        identityStamp = incomingIdentityStamp
        account = snapshot["account"] as? String ?? "Not signed in"
        team = snapshot["team"] as? String ?? "No active team"
        signing = snapshot["signing"] as? String ?? "Unknown"
        certificate = snapshot["certificate"] as? String ?? "Unknown"
        certificateExpiration = (snapshot["certificateExpiration"] as? Date).flatMap { $0 == .distantPast ? nil : $0 }
        pairing = snapshot["pairing"] as? String ?? "Unknown"
        authenticated = V3ServiceBridge.strictBool(snapshot["authenticated"]) ?? false
        authenticationActive = V3ServiceBridge.strictBool(snapshot["authenticationActive"]) ?? false
        activeAccountPresent = V3ServiceBridge.strictBool(snapshot["activeAccountPresent"]) ?? false
        activeTeamPresent = V3ServiceBridge.strictBool(snapshot["activeTeamPresent"]) ?? false
        activeCertificatePresent = V3ServiceBridge.strictBool(snapshot["activeCertificatePresent"]) ?? false
        provisioningIncomplete = V3ServiceBridge.strictBool(snapshot["provisioningIncomplete"]) ?? false
        updatedAt = snapshot["updatedAt"] as? Date
        installedApps = (snapshot["installedApps"] as? [[String: Any]] ?? []).compactMap(V3SideStoreApp.init)
        sources = (snapshot["sources"] as? [[String: Any]] ?? []).compactMap(V3SideStoreSource.init)
        settings = snapshot["settings"] as? [String: Bool] ?? [:]
        connected = true
        applyFullRecoveryEvidence(snapshot)
        return true
    }

    private func acceptRecoveryEvidence(_ snapshot: [String: Any]) -> Bool {
        guard V3ServiceBridge.shared.statusReplyMayApplyRecoveryEvidence(snapshot) else { return false }
        V3ServiceBridge.shared.setHostRecoveryHold(true)
        observeRecoveryStorageFailure(snapshot)
        let recoveryHold = V3ServiceBridge.strictBool(snapshot["recoveryHold"]) == true
        var foundValidEvidence = false
        if snapshot.keys.contains("operationRecovery"),
           let recovery = snapshot["operationRecovery"] as? [String: Any],
           let session = recovery["session"] as? String,
           UUID(uuidString: session) != nil,
           let kind = recovery["kind"] as? String, !kind.isEmpty,
           let phaseText = recovery["phase"] as? String,
           let phase = V3OperationRecoveryRecord.Phase(rawValue: phaseText) {
            unresolvedOperationRecovery = V3OperationRecoveryRecord(sessionID: session, kind: kind,
                phase: phase, stagedIPAToken: recovery["stagedIPAToken"] as? String)
            foundValidEvidence = true
        } else if snapshot.keys.contains("operationRecovery") {
            unresolvedOperationRecovery = nil
            unresolvedRecoveryJournalUnreadable = true
        }
        if snapshot.keys.contains("refreshRecovery"),
           let refresh = snapshot["refreshRecovery"] as? [String: Any],
           let runID = refresh["runID"] as? String,
           let ownerLost = V3ServiceBridge.strictBool(refresh["ownerLost"]),
           UUID(uuidString: runID)?.uuidString == runID {
            if ownerLost { unresolvedRefreshRecoveryRunID = runID }
            foundValidEvidence = true
        } else if snapshot.keys.contains("refreshRecovery") {
            unresolvedRefreshRecoveryRunID = nil
            unresolvedRecoveryJournalUnreadable = true
        }
        if V3ServiceBridge.strictBool(snapshot["recoveryJournalUnreadable"]) == true {
            unresolvedRecoveryJournalUnreadable = true
        }
        if snapshot.keys.contains("directRecovery") {
            if recoveryHold,
               let direct = V3HostDirectRecoveryRecord(snapshotValue: snapshot["directRecovery"]) {
                unresolvedDirectRecovery = direct
                directRecoveryPostcondition = nil
                foundValidEvidence = true
            } else {
                unresolvedRecoveryJournalUnreadable = true
            }
        }
        if recoveryHold && !foundValidEvidence && !unresolvedRecoveryJournalUnreadable {
            unresolvedRecoveryJournalUnreadable = true
        }
        return true
    }

    private func observeRecoveryStorageFailure(_ snapshot: [String: Any]) {
        let known: Set<String> = ["malformedRecord", "incompatibleRecord", "storageUnavailable",
                                  "lockUnavailable", "readFailure", "deleteFailure"]
        let knownSteps: Set<String> = ["unknown", "appGroup", "directory", "open", "permissions",
                                       "flock", "metadata", "fileType", "readData", "parse", "schema"]
        guard let details = snapshot["recoveryStorageFailure"] as? [String: Any],
              let kind = details["kind"] as? String, known.contains(kind),
              let step = details["sourceStep"] as? String, knownSteps.contains(step),
              let present = V3ServiceBridge.strictBool(details["recordPresent"]),
              let eligible = V3ServiceBridge.strictBool(details["clearEligible"]),
              let retryable = V3ServiceBridge.strictBool(details["retryable"]),
              let domain = details["underlyingDomain"] as? String,
              let code = V3ServiceBridge.strictInt(details["underlyingCode"]) else {
            recoveryStorageKind = nil
            recoveryStorageClearEligible = false
            recoveryStorageDiagnostics = "schema=1 operation=status stage=persistence recovery_storage_kind=unknown"
            return
        }
        recoveryStorageKind = kind
        recoveryStorageClearEligible = present && eligible &&
            ["malformedRecord", "incompatibleRecord"].contains(kind)
        let native = CombinedFailure.safeDiagnosticUnderlying(domain: domain, code: code)
        var appGroupLine = ""
        if let group = snapshot["recoveryAppGroup"] as? [String: Any],
           let hash = group["groupHash"] as? String,
           (hash == "none" || (hash.count == 12 && hash.utf8.allSatisfy {
               (48...57).contains($0) || (97...102).contains($0)
           })),
           let source = group["selectionSource"] as? String,
           ["none", "inherited", "runtimeSelected"].contains(source),
           let entitled = group["signedEntitled"] as? String,
           ["yes", "no", "unknown"].contains(entitled),
           let resolves = V3ServiceBridge.strictBool(group["containerResolves"]) {
            appGroupLine = " group_hash=\(hash) group_selection_source=\(source) " +
                "signed_group_entitled=\(entitled) group_container_resolves=\(resolves)"
        }
        recoveryStorageDiagnostics = "schema=1 operation=status stage=persistence " +
            "recovery_storage_kind=\(kind) source_step=\(step) record_present=\(present) clear_eligible=\(recoveryStorageClearEligible) " +
            "underlying_domain=\(native.domain) underlying_code=\(native.code) retryable=\(retryable)" + appGroupLine
    }

    private func applyFullRecoveryEvidence(_ snapshot: [String: Any]) {
        observeRecoveryStorageFailure(snapshot)
        let recoveryHold = V3ServiceBridge.strictBool(snapshot["recoveryHold"]) == true
        var foundValidEvidence = false
        var unreadable = V3ServiceBridge.strictBool(snapshot["recoveryJournalUnreadable"]) == true
        if snapshot.keys.contains("operationRecovery"),
           let recovery = snapshot["operationRecovery"] as? [String: Any],
           let session = recovery["session"] as? String,
           UUID(uuidString: session) != nil,
           let kind = recovery["kind"] as? String, !kind.isEmpty,
           let phaseText = recovery["phase"] as? String,
           let phase = V3OperationRecoveryRecord.Phase(rawValue: phaseText) {
            unresolvedOperationRecovery = V3OperationRecoveryRecord(sessionID: session, kind: kind,
                phase: phase, stagedIPAToken: recovery["stagedIPAToken"] as? String)
            foundValidEvidence = true
        } else {
            unresolvedOperationRecovery = nil
            if snapshot.keys.contains("operationRecovery") { unreadable = true }
        }
        if snapshot.keys.contains("refreshRecovery"),
           let refresh = snapshot["refreshRecovery"] as? [String: Any],
           let runID = refresh["runID"] as? String,
           let ownerLost = V3ServiceBridge.strictBool(refresh["ownerLost"]),
           UUID(uuidString: runID)?.uuidString == runID {
            unresolvedRefreshRecoveryRunID = ownerLost ? runID : nil
            foundValidEvidence = true
        } else {
            unresolvedRefreshRecoveryRunID = nil
            if snapshot.keys.contains("refreshRecovery") { unreadable = true }
        }
        if snapshot.keys.contains("directRecovery") {
            if recoveryHold,
               let direct = V3HostDirectRecoveryRecord(snapshotValue: snapshot["directRecovery"]) {
                unresolvedDirectRecovery = direct
                foundValidEvidence = true
            } else {
                unreadable = true
            }
        }
        if recoveryHold && !foundValidEvidence && !unreadable { unreadable = true }
        unresolvedRecoveryJournalUnreadable = unreadable
        directRecoveryPostcondition = nil
        // Only a full, current-ticket snapshot with no durable hold can clear
        // the bridge admission fence. Locally retained unknown records keep it
        // closed even if an unrelated snapshot omits their journal entry.
        let locallyUnresolved = unresolvedOperationRecovery != nil ||
            unresolvedRefreshRecoveryRunID != nil || unresolvedRecoveryJournalUnreadable ||
            unresolvedDirectRecovery != nil
        V3ServiceBridge.shared.setHostRecoveryHold(recoveryHold || locallyUnresolved)
    }
    private func rejectForUnresolvedRecovery() -> Bool {
        guard unresolvedOperationRecovery != nil || unresolvedRefreshRecoveryRunID != nil ||
              unresolvedRecoveryJournalUnreadable || unresolvedDirectRecovery != nil else { return false }
        error = unresolvedRecoveryJournalUnreadable
            ? "The recovery record cannot be read. Use the recovery banner after checking the device before starting another operation."
            : unresolvedDirectRecovery != nil
                ? "A previous request has an unresolved result. Inspect it in the recovery banner before repeating the action."
                : "A previous operation is unresolved. Use the recovery banner at the top of SideStore to resume its status check or reconcile after checking the device."
        return true
    }

    func perform(_ operation: String, target: String = "", title: String, value: Bool? = nil) {
        // A second operation while one is presented must explain itself
        // instead of silently doing nothing (which looks like the first tap
        // was ignored and invites blind retries).
        guard presentation == nil, !installAttempt.hasActiveAttempt else {
            self.error = "Another operation is already running. Finish or cancel it before starting a new one."
            return
        }
        guard !rejectForUnresolvedRecovery() else { return }
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
        guard let failure = error as? CombinedFailure else { return false }
        return V3SignInFailureRoutingPolicy.shouldOpenSignIn(
            stage: failure.stage, safeCause: failure.safeCause)
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
        // If a retry action was rejected, replace its original failure instead
        // of leaving an alert whose old text hides the new busy explanation.
        issue = nil
        self.error = "SideStore is still loading. Wait for the current request to finish, then try again."
    }

    /// Runs one service mutation under an explicit mutation activity.
    ///
    /// A mutation owns the service but is not a snapshot, so it must never
    /// resolve a caller awaiting authoritative status. A snapshot is requested
    /// after it; a caller already parked is released by that snapshot, not by
    /// this one. The trailing reload is a plain request, so if the drain already
    /// started the owed snapshot this joins it instead of fetching twice.
    @discardableResult
    private func runMutation(_ operation: String, target: String = "", successNotice: String) -> Bool {
        guard !rejectForUnresolvedRecovery() else { return false }
        guard loadActivity == .idle else {
            presentBusy()
            return false
        }
        beginMutation()
        Task {
            do {
                let snapshot = try await V3ServiceBridge.shared.request(operation: operation, target: target)
                guard accept(snapshot) else {
                    finishMutation()
                    reload()
                    return
                }
                let signOutOutcome = operation == "signOut"
                    ? V3SignOutOutcomePolicy.resolve(
                        authenticated: V3ServiceBridge.strictBool(snapshot["authenticated"]),
                        activeAccountPresent: V3ServiceBridge.strictBool(snapshot["activeAccountPresent"]),
                        activeTeamPresent: V3ServiceBridge.strictBool(snapshot["activeTeamPresent"]))
                    : nil
                finishMutation()
                if let signOutOutcome {
                    if let verifiedNotice = V3SignOutOutcomePolicy.successNotice(for: signOutOutcome) {
                        notice = verifiedNotice
                    } else {
                        presentUnconfirmedSignOut(signOutOutcome)
                    }
                } else {
                    notice = successNotice
                }
                reload()
            } catch {
                finishMutation()
                failed(error)
                reload()
            }
        }
        return true
    }
    func signOut() { runMutation("signOut", successNotice: "Signed out successfully.") }
    func jit(target: String) { runMutation("jit", target: target, successNotice: "JIT enabled.") }
    func syncAppIDs() { runMutation("syncAppIDs", successNotice: "App IDs synced.") }
    func clearCache() { runMutation("clearCache", successNotice: "Download cache cleared.") }
    @discardableResult
    func refreshSources() -> Bool {
        runMutation("refreshSources", successNotice: "Sources updated.")
    }
    func stageSharedFile(_ data: Data, purpose: String) async -> String? {
        guard !data.isEmpty, data.count <= 4_194_304 else {
            self.error = "The selected file is empty or too large to hand to the SideStore service."
            return nil
        }
        guard let containerRoot = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else {
            self.error = "The SideStore shared file container is unavailable. Check Connection and try again."
            return nil
        }
        let stagedToken = await Task.detached(priority: .utility) {
            V3SharedFileRecord.stage(data, purpose: purpose, containerRoot: containerRoot)
        }.value
        guard let token = stagedToken else {
            self.error = "Secure file staging is full or unavailable. Finish the pending import and try again."
            return nil
        }
        return token
    }
    func discardSharedFile(_ token: String) {
        guard let containerRoot = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else { return }
        V3SharedFileRecord.discard(token, containerRoot: containerRoot)
    }
    func beginInstallPicker() {
        NSLog("[V3_INSTALL_UI] tap")
        guard !rejectForUnresolvedRecovery() else {
            NSLog("[V3_INSTALL_UI] tap_rejected reason=operation_recovery_required")
            return
        }
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
        if let token { Task { _ = await cleanupStagedIPA(token, allowLocalFallback: true) } }
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
        if let token { Task { _ = await cleanupStagedIPA(token, allowLocalFallback: true) } }
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
        guard !rejectForUnresolvedRecovery() else { return nil }
        guard presentation == nil, !installAttempt.hasActiveAttempt,
              let attemptID = installAttempt.beginDirectStaging() else {
            error = "Another operation is already running. Finish or cancel it before installing another app."
            return nil
        }
        return stageIPA(url, attemptID: attemptID, bookmark: bookmark, title: title,
                        waitsForPickerDismissal: false)
    }

    func cleanupOrphanedStagedIPAs() async {
        guard let container = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else { return }
        var protectedTokens = Set<String>()
        if let token = unresolvedOperationRecovery?.stagedIPAToken {
            protectedTokens.insert(token)
        }
        if let hostToken = installAttempt.token,
           let canonical = try? V3IPAStaging.canonicalToken(hostToken) {
            protectedTokens.insert(canonical)
        }
        do {
            // Age alone cannot prove a staged file is unused: native callbacks
            // may leave a backend mutation alive past its request deadline.
            // Ask the service for active token ownership and fail closed if the
            // service cannot give an authoritative answer.
            let reply = try await V3ServiceBridge.shared.request(operation: "ipaActiveTokens")
            guard let tokens = reply["tokens"] as? [String], tokens.count <= 512 else { return }
            for token in tokens {
                guard let canonical = try? V3IPAStaging.canonicalToken(token) else { return }
                protectedTokens.insert(canonical)
            }
            _ = try V3IPAStaging.cleanupOrphans(containerRoot: container,
                preservingTokens: protectedTokens)
        } catch {
            // Unavailable ownership means skip pruning. Do not expose paths or
            // filenames in user-copyable logs.
        }
    }

    private func stageIPA(_ url: URL, attemptID: UUID, bookmark: Data?, title: String,
                          waitsForPickerDismissal: Bool) -> String? {
        do {
            guard let container = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else {
                throw CombinedIPAFileError(.fileAccess)
            }
            let token = try V3IPAStaging.stage(sourceURL: url, bookmark: bookmark, containerRoot: container)
            guard installAttempt.staged(attemptID: attemptID, token: token, title: title,
                                        waitsForPickerDismissal: waitsForPickerDismissal,
                                        isLoading: loading) else {
                _ = resetInstallUI(attemptID: attemptID, outcome: "stage_handoff_failed")
                Task { _ = await cleanupStagedIPA(token, allowLocalFallback: true) }
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
        if let token { Task { _ = await cleanupStagedIPA(token, allowLocalFallback: true) } }
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
                let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: sessionID)
                guard let terminalState = V3OperationCancellationOutcomePolicy.terminalState(
                    expectedSessionID: sessionID,
                    replySessionID: reply["session"] as? String,
                    state: reply["state"] as? String,
                    backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                    stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                    outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])) else {
                    self.error = "SideStore has not confirmed that the device operation stopped. The IPA and operation session were kept; retry cancellation or check device state before another install."
                    return
                }
                _ = installAttempt.recordTerminal(attemptID: attemptID,
                    operationID: operationID, outcome: terminalState)
                self.error = terminalState == "cancelled" ? nil :
                    (terminalState == "completed"
                        ? "The install completed before cancellation was confirmed."
                        : "The install ended with a failure before cancellation was confirmed.")
                let token = resetInstallUI(attemptID: attemptID, outcome: terminalState)
                if let token { _ = await cleanupStagedIPA(token, allowLocalFallback: true) }
                reload()
            } catch {
                NSLog("[V3_INSTALL_UI] cancellation_unconfirmed attempt=%@ session=%@",
                      attemptID.uuidString, sessionID)
                self.error = "SideStore still cannot confirm that the install stopped. No new install was started. Reconnect, then retry cancellation."
            }
        }
    }

    func cleanupStagedIPA(_ token: String, allowLocalFallback: Bool = false) async -> Bool {
        do {
            _ = try await V3ServiceBridge.shared.request(operation: "ipaCleanup", target: token)
            return true
        } catch {
            let serviceReportsBusy = (error as? CombinedFailure).map {
                $0.code == .busy ||
                    $0.safeCause == .operationInProgress
            } ?? false
            guard V3StagedIPACleanupFallbackPolicy.mayDeleteLocally(
                serviceReportsBusy: serviceReportsBusy,
                callerConfirmsNeverStartedOrSettled: allowLocalFallback) else {
                NSLog("[V3_INSTALL_UI] staged_cleanup_deferred reason=%@",
                      serviceReportsBusy ? "service_busy" : "backend_state_unconfirmed")
                return false
            }
            // The host uses the same canonical UUID-only staging helper for a
            // local fallback only when no backend can still use the token. It
            // never accepts or constructs a caller path.
            do {
                guard let container = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else {
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
    @State private var failedSourceInput: String?
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
    @State private var sourceURLBeforeOpening: String = ""
    @State private var isAddSourcePresented = false
    @State private var sourceOpenRequestLedger = V3SourceFormOpenRequestLedger()
    @State private var sourcePreviewSession = V3SourcePreviewSession()
    @State private var sourcePreviewTask: Task<Void, Never>?
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
                if isAddSourcePresented {
                    Section("Add Source") {
                        HStack {
                            Image(systemName: "link")
                                .foregroundColor(.secondary)
                            TextField("https://example.com/source.json", text: $status.sourceURL)
                                .keyboardType(.URL)
                                .autocapitalization(.none)
                                .disableAutocorrection(true)
                                .disabled(addBusy)
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
                        .onChange(of: status.sourceURL) { newURL in
                            if let activeRequest = sourcePreviewSession.activeRequest,
                               activeRequest.targetURL != newURL {
                                invalidateSourcePreview()
                            }
                            if failedSourceInput != newURL {
                                sourceFailure = nil
                                failedSourceInput = nil
                            }
                            if let previewURL = preview?["url"] as? String, previewURL != newURL {
                                preview = nil
                            }
                        }
                        }
                        // Explicit keyboard dismissal, with an explicit Cancel that
                        // performs no preview, no network request and no persistence.
                        .toolbar {
                            ToolbarItemGroup(placement: .keyboard) {
                                Spacer()
                                Button("Cancel") { cancelSourceEditing() }
                                    .disabled(!V3SourceEditingPolicy.canCancelForm(isAdding: addBusy))
                                Button("Done") { dismissKeyboard() }
                            }
                        }
                        Button {
                            startPreviewSource()
                        } label: {
                            Label(previewBusy ? "Checking Source..." : "Preview and Add Source", systemImage: "plus.circle.fill")
                        }
                        .disabled(status.sourceURL.isEmpty || previewBusy || isSubmissionBlocked(for: status.sourceURL))
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
                            .disabled((preview["alreadyAdded"] as? Bool ?? false) || addBusy ||
                                isSubmissionBlocked(for: preview["url"] as? String ?? status.sourceURL))
                        }
                    }
                } else {
                    Section {
                        Button(action: openSourceForm) {
                            Label("Add Source", systemImage: "plus.circle.fill")
                        }
                        .accessibilityHint("Opens the source URL form. Nothing is added until you confirm.")
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
                                openSourceForm()
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
                ToolbarItem(placement: .cancellationAction) {
                    Group {
                        if isAddSourcePresented {
                            Button("Cancel", action: cancelSourceForm)
                                .disabled(!V3SourceEditingPolicy.canCancelForm(isAdding: addBusy))
                                .accessibilityHint("Closes Add Source without starting an add request. An in-flight preview read may be cancelled.")
                        }
                    }
                }
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
        .onAppear {
            receiveSourceOpenRequest(status.sourceFormOpenRequestID)
        }
        .onChange(of: status.sourceFormOpenRequestID) { requestID in
            // Handles source routes delivered while this tab is already mounted.
            receiveSourceOpenRequest(requestID)
        }
    }
    private func openSourceForm() {
        invalidateSourcePreview()
        sourceURLBeforeOpening = status.sourceURL
        isAddSourcePresented = true
        preview = nil
        sourceFailure = nil
        failedSourceInput = nil
        notice = ""
        addSucceeded = false
    }
    private func receiveSourceOpenRequest(_ requestID: UUID?) {
        guard sourceOpenRequestLedger.claim(requestID) else { return }
        openSourceForm()
    }
    // V3_SOURCE_KEYBOARD_DISMISS_V1: dismissing the keyboard is a pure UI action.
    // It previews nothing, requests nothing and persists nothing.
    /// Done keeps the typed value and dismisses the keyboard. It is a pure UI
    /// dismissal: it never previews, requests, or persists anything.
    private func dismissKeyboard() {
        sourceFieldFocused = false
        _ = V3SourceEditingPolicy.done(typed: status.sourceURL)
    }

    /// Cancel restores the URL that was present when editing began and dismisses
    /// the keyboard.
    ///
    /// It performs no network request, no preview and no source mutation. It
    /// deliberately does not discard an already-rendered preview either: a
    /// preview the user just spent a request on used to vanish silently,
    /// together with its Confirm action, which is a source mutation the user
    /// never asked to lose.
    private func cancelSourceEditing() {
        status.sourceURL = V3SourceEditingPolicy.resolved(
            V3SourceEditingPolicy.cancel(typed: status.sourceURL, beforeEditing: sourceURLBeforeEditing),
            typed: status.sourceURL)
        sourceFieldFocused = false
    }

    /// The visible navigation Cancel works with the keyboard shown or hidden.
    /// The production transition starts no backend work. It closes the form,
    /// restores its opening value, discards the local preview, and invalidates
    /// any in-flight read-only preview request so its late reply cannot apply.
    private func cancelSourceForm() {
        invalidateSourcePreview()
        let transition = V3SourceEditingPolicy.closeForm(V3SourceFormState(
            isPresented: isAddSourcePresented,
            url: status.sourceURL,
            originalURL: sourceURLBeforeOpening,
            isFocused: sourceFieldFocused,
            hasPreview: preview != nil))
        status.sourceURL = transition.state.url
        sourceFieldFocused = transition.state.isFocused
        preview = nil
        isAddSourcePresented = transition.state.isPresented
    }

    private func invalidateSourcePreview() {
        sourcePreviewSession.invalidate()
        sourcePreviewTask?.cancel()
        sourcePreviewTask = nil
        previewBusy = false
    }

    private func startPreviewSource() {
        guard isAddSourcePresented else { return }
        invalidateSourcePreview()
        guard let request = sourcePreviewSession.begin(targetURL: status.sourceURL) else { return }
        previewBusy = true
        sourceFailure = nil
        failedSourceInput = nil
        notice = ""
        addSucceeded = false
        sourcePreviewTask = Task { await previewSource(request) }
    }

    private func previewSource(_ request: V3SourcePreviewRequest) async {
        defer {
            if sourcePreviewSession.activeRequest == request {
                previewBusy = false
                sourcePreviewTask = nil
            }
        }
        do {
            let payload = try await V3ServiceBridge.shared.request(operation: "sourcePreview", target: request.targetURL)
            guard sourcePreviewSession.mayApply(request, currentURL: status.sourceURL,
                                                formPresented: isAddSourcePresented) else { return }
            let row = V3SourcePreviewSession.responseRow(payload, for: request)
            preview = row
            // Previewing is an explicit action, so the keyboard has served its
            // purpose once the preview is on screen.
            dismissKeyboard()
        } catch {
            guard sourcePreviewSession.mayApply(request, currentURL: status.sourceURL,
                                                formPresented: isAddSourcePresented) else { return }
            sourceFailure = V3SourceAddFailure(error)
            failedSourceInput = request.targetURL
        }
    }
    private func confirmAdd(url: String) async {
        addBusy = true
        notice = ""
        sourceFailure = nil
        failedSourceInput = nil
        defer { addBusy = false }
        let mutationTicket = status.beginDirectMutation()
        var acceptedSnapshot: [String: Any]?
        var reloadAfterFailure = false
        defer {
            status.finishDirectMutation(ticket: mutationTicket, reply: acceptedSnapshot,
                requestReload: reloadAfterFailure)
        }
        do {
            let result = try await V3ServiceBridge.shared.request(operation: "sourceAddConfirmed", target: url)
            guard let message = V3SourceAddPersistencePolicy.confirmationMessage(result),
                  let identifier = result["identifier"] as? String,
                  let sources = result["sources"] as? [[String: Any]],
                  sources.contains(where: { $0["identifier"] as? String == identifier }) else {
                throw V3SourceAddPersistencePolicy.unverifiedPersistenceFailure(
                    correlationID: UUID().uuidString)
            }
            acceptedSnapshot = result
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                result, operation: "sourceAddConfirmed")
            preview = nil
            status.sourceURL = ""
            isAddSourcePresented = false
            failedSourceInput = nil
            notice = message
            addSucceeded = true
        } catch {
            reloadAfterFailure = true
            sourceFailure = V3SourceAddFailure(error)
            failedSourceInput = url
        }
    }

    private func isSubmissionBlocked(for input: String) -> Bool {
        guard let sourceFailure else { return false }
        return !V3SourceSubmissionPolicy.mayResubmit(retryable: sourceFailure.retryable,
            safeCause: sourceFailure.safeCause,
            failedInput: failedSourceInput, currentInput: input)
    }
    private func confirmRemove(id: String) async {
        removeCandidate = nil
        removeBusy = true
        notice = "Removing source..."
        addSucceeded = false
        defer { removeBusy = false }
        let mutationTicket = status.beginDirectMutation()
        do {
            let result = try await V3ServiceBridge.shared.request(operation: "sourceRemoveConfirmed", target: id)
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                result, operation: "sourceRemoveConfirmed")
            status.finishDirectMutation(ticket: mutationTicket, reply: result)
            notice = "Source removed."
        } catch {
            status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
            notice = ""
            status.present(error)
        }
    }
}

struct V3CatalogApp: Identifiable {
    let id: String, name: String, version: String, developer: String, description: String, installedID: String
    let canInstall: Bool
    let downloadURL: String
    let installedVersion: String?
    init?(_ row: [String: Any]) {
        guard V3CatalogRowPolicy.isDisplayable(row),
              let id = row["identifier"] as? String,
              let name = row["name"] as? String else { return nil }
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
    @Environment(\.dismiss) private var dismiss
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
                    if failure?.safeCause == CombinedFailure.SafeCause.catalogSourceUnavailable.rawValue {
                        Button("Return to Sources") { dismiss() }
                    }
                    switch V3CatalogRetryPresentationPolicy.action(
                        for: failure?.retryDisposition ?? .unknown,
                        safeCause: failure?.safeCause) {
                    case .retry:
                        Button("Retry Catalog") { Task { await load() } }.disabled(loadInFlight)
                    case .retryWithUnknownDisposition:
                        Button("Try Catalog Again (retryability unknown)") { Task { await load() } }
                            .disabled(loadInFlight)
                    case .reloadCatalog:
                        Button("Reload Catalog") { Task { await load() } }.disabled(loadInFlight)
                    case .noRetry:
                        EmptyView()
                    }
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
        apps.removeAll(keepingCapacity: true)
        error = nil
        failure = nil
        defer { loading = false }
        do {
            var cursor = 0
            // V3_CATALOG_ROW_POLICY_V1: retain the raw rows and a persistent
            // identifier set across pages, then map once. Re-deduplicating the
            // full accumulated array for every page made large catalogs quadratic.
            var accumulated = V3CatalogRowsAccumulator()
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
                guard rawApps.allSatisfy(V3CatalogRowPolicy.isDisplayable) else {
                    throw catalogResponseFailure(cursor: cursor)
                }
                guard let number = result["nextCursor"] as? NSNumber,
                      CFGetTypeID(number) != CFBooleanGetTypeID(),
                      let next = number as? Int else {
                    throw catalogResponseFailure(cursor: cursor)
                }
                // The real deduplication rule: duplicates are removed within a
                // page and across pages, first-seen order preserved.
                accumulated.append(rawApps)
                guard next == -1 || next > cursor else { throw catalogResponseFailure(cursor: cursor) }
                cursor = next
            } while cursor >= 0
            let mappedApps = accumulated.rows.compactMap(V3CatalogApp.init)
            guard mappedApps.count == accumulated.rows.count else { throw catalogResponseFailure(cursor: cursor) }
            apps = mappedApps
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
    private var identityPresentation: V3AccountSessionPresentation {
        V3AccountSessionPresentationPolicy.resolve(authenticated: status.authenticated,
            activeAccountPresent: status.activeAccountPresent,
            activeTeamPresent: status.activeTeamPresent,
            activeCertificatePresent: status.activeCertificatePresent)
    }

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
            if identityPresentation.showSignIn {
                V3SignInLink(title: "Sign In with Apple ID")
                if identityPresentation.showSavedAppleID {
                    HStack {
                        Label("Saved Apple ID", systemImage: "person.crop.circle.fill")
                        Spacer()
                        Text(status.account)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                }
                if identityPresentation.showUnverifiedSavedState {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Saved account state is not verified",
                              systemImage: "exclamationmark.triangle.fill")
                            .foregroundColor(.orange)
                        Text("SideStore still has a saved account or team, but cannot confirm an active Apple sign-in. Sign in again or explicitly sign out of the saved account state.")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
                }
                if identityPresentation.showRetainedCertificateGuidance {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Saved certificate is retained separately",
                              systemImage: "doc.text.magnifyingglass")
                        Text("Sign Out does not remove signing certificates. Open Certificates to review or remove a saved certificate.")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                        Button("Open Certificates") { status.certificatesPresented = true }
                    }
                }
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
            // Account status is available only for a signed-in account. When
            // signed out, the section above already offers Sign In.
            if !status.needsSignIn {
                NavigationLink {
                    V3SignInView().environmentObject(status)
                } label: {
                    Label("Sign-In Status", systemImage: "person.badge.key.fill")
                }
                .accessibilityHint("Review the current Apple sign-in state")
            }
            Button {
                status.syncAppIDs()
            } label: {
                Label("Sync App IDs", systemImage: "arrow.triangle.2.circlepath")
            }
            .disabled(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
                authenticated: status.authenticated, isLoading: status.loading))
            .accessibilityHint("Sign in with Apple ID before syncing App IDs.")
            link("Certificates", icon: "doc.text") { V3CertificatesView().environmentObject(status) }
            link("Developer Services", icon: "wrench.and.screwdriver") { V3DeveloperServicesView().environmentObject(status) }
            if identityPresentation.showSignOut {
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
                Text(V3PairingPresentationPolicy.displayText(statusConnected: status.connected,
                    pairingStatus: status.pairing))
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
            let mutationTicket = status.beginDirectMutation()
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "bool", "bool": newValue])
                _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                    reply, operation: "settingsSet")
                status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
                if writeGenerations.isCurrent(generation, for: key) {
                    confirmedValue = newValue
                    status.reload()
                } else {
                    _ = await reloadAuthoritative(generation: writeGenerations.current(for: key))
                }
            } catch {
                status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
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
    let retryable: Bool?
    let safeCause: String?

    init(_ error: Error) {
        let failure: CombinedFailure
        if let combined = error as? CombinedFailure {
            failure = V3SourceAddFailurePolicy.normalized(combined)
        } else {
            failure = CombinedFailure.capture(error,
                operation: "source", stage: .source, id: UUID().uuidString)
        }
        whatHappened = failure.safeMessage
        whatToDo = failure.recovery
        technicalDetails = failure.technicalDetails
        retryable = failure.retryable
        safeCause = failure.safeCause?.rawValue
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
                if V3RefreshPrerequisite.evaluate(statusConnected: status.connected,
                    pairingStatus: status.pairing).blocksTargetedRefresh {
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
                .disabled(V3RefreshPrerequisite.evaluate(statusConnected: status.connected,
                    pairingStatus: status.pairing).blocksTargetedRefresh)
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
    @State private var terminalBackendSettled: Bool?
    @State private var deviceCheckConfirmedForCompletion = false
    @State private var operationPhase = V3OperationPhase.working
    @State private var prompt: [String: Any]?
    @State private var sourceOffer: [String: String]?
    @State private var promptResponseBlocked = false
    @State private var sourceAddFailure: V3OperationFailureDetails?
    @State private var sourceAddRetryBlocked = false
    @State private var message = ""
    @State private var task: Task<Void, Never>?
    @State private var startedGeneration: UUID?
    @State private var isDismissing = false
    @State private var promptSubmitting = false
    @State private var userRequestedCancellation = false
    @State private var failureContext = V3OperationRetryContext()
    @State private var whatToDo = ""
    @State private var technicalDetails = ""
    @State private var recoveryDestination: String?
    @State private var retryBlocked = false
    @State private var needsDeviceConfirmation = false
    @State private var confirmUncertainRetirement = false
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
        V3OperationRetryButtonPolicy.title(state: state,
            retryDisposition: failureContext.retryDisposition)
    }
    private var sourceAddButtonTitle: String {
        guard let sourceAddFailure else { return "Add Source and Retry" }
        if sourceAddRetryBlocked { return "Resolve Source Issue" }
        return sourceAddFailure.retryable == nil
            ? "Try Source Add Again (retryability unknown)" : "Try Source Add Again"
    }
    private func recoveryActionTitle(for destination: String) -> String? {
        switch destination {
        case "signIn": return "Open Account & Signing"
        case "certificates": return "Open Certificates"
        case "ipa": return "Choose IPA Again"
        case "connection": return "Open Connection Settings"
        case "sources": return "Open Sources"
        // This destination opens the Setup Assistant, so it must say so. It read
        // "Open Connection Settings" while routing to the assistant, which is the
        // same class of mislabel as offering a connection retry for a source
        // failure.
        case "setup": return "Open Setup Assistant"
        default: return nil
        }
    }
    private var isTransitioning: Bool { attempt.transitionInFlight }
    private var isRunning: Bool { ["working", "awaitingPrompt", "cancelling"].contains(state) }
    private var displayProgress: Double { V3NormalizedProgress.displayValue(progress, state: state) }
    private var progressPercent: Int { V3NormalizedProgress.percent(progress, state: state) }
    private var completionAwaitingSettlement: Bool {
        V3OperationCompletionPolicy.requiresDeviceCheck(state: state,
            backendSettled: terminalBackendSettled,
            deviceCheckConfirmed: deviceCheckConfirmedForCompletion,
            outcomeUnknown: needsDeviceConfirmation)
    }
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
                    V3PromptSection(prompt: prompt, isSubmitting: $promptSubmitting,
                                    isSubmissionBlocked: promptResponseBlocked) { answer in
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
                            Label(sourceAddButtonTitle, systemImage: "plus.circle.fill")
                        }
                        .disabled(isTransitioning || sourceAddRetryBlocked)
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
                            if needsDeviceConfirmation, uncertainSessionID != nil {
                                Button("Reconcile After Checking Device") {
                                    confirmUncertainRetirement = true
                                }
                            }
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
                    Button(isRunning ? (state == "cancelling" ? "Cancelling..." : "Cancel") :
                        (completionAwaitingSettlement ? "Reconcile" : "Done")) {
                        if isRunning {
                            cancelAttempt()
                        } else if completionAwaitingSettlement {
                            confirmUncertainRetirement = true
                        } else {
                            acknowledgeAndDismiss()
                        }
                    }
                    .disabled(isTransitioning || state == "cancelling")
                }
            }
        }
        .navigationViewStyle(StackNavigationViewStyle())
        .interactiveDismissDisabled(true)
        .confirmationDialog("Check the device before restarting SideStore?", isPresented: $confirmUncertainRetirement,
                            titleVisibility: .visible) {
            Button("I checked; restart SideStore", role: .destructive) {
                confirmUncertainOperationAfterDeviceCheck()
            }
            Button("Keep Waiting", role: .cancel) {}
        } message: {
            Text("Use this only after confirming the device is no longer installing, updating, refreshing, or deleting the app.")
        }
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
                let wasTerminal = attempt.isTerminal
                let terminalOutcome = wasTerminal &&
                    ["completed", "failed", "cancelled", "timedOut"].contains(state)
                    ? state : "cancelled"
                let mustConfirmCancel = V3OperationCoverDismissalPolicy.mustConfirmBackendStop(
                    isRunning: isRunning, hasSession: attempt.sessionID != nil,
                    sessionIsTerminal: wasTerminal,
                    hasUncertainSession: uncertainSessionID != nil,
                    transitionInFlight: attempt.transitionInFlight)
                let oldSession = uncertainSessionID ?? (mustConfirmCancel ? attempt.supersede() : nil)
                oldTask?.cancel()
                Task { @MainActor in
                    var cancellationConfirmed = !mustConfirmCancel
                    var confirmedOutcome = terminalOutcome
                    if let oldSession {
                        do {
                            let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                            let terminalState = reply["state"] as? String
                            cancellationConfirmed = V3OperationTerminalAcceptancePolicy.isSettledTerminal(
                                state: terminalState,
                                backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                                stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                                outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
                            if cancellationConfirmed { confirmedOutcome = terminalState ?? "cancelled" }
                        } catch { cancellationConfirmed = false }
                    }
                    await oldTask?.value
                    if request.operation == "installSharedIPA", cancellationConfirmed {
                        status.installTerminal(attemptID: request.installAttemptID,
                            operationID: request.id, outcome: confirmedOutcome)
                        let token = request.installAttemptID.flatMap {
                            status.resetInstallUI(attemptID: $0, outcome: "unexpected_cover_dismissal")
                        }
                        if let token { stagedIPACleaned = await status.cleanupStagedIPA(token,
                            allowLocalFallback: true) }
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
        case "reconciling": return "Checking result..."
        case "working" where isTransitioning: return "Waiting for previous attempt..."
        case "completed": return "Completed"
        case "awaitingPrompt": return "Needs your input"
        case "promptExpired": return "Verification expired"
        case "timedOut": return "Sign-in timed out"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        case "requiresSource": return "Source required"
        default: return isTransitioning ? "Waiting for previous attempt..." : operationPhase.label
        }
    }
    private func start() {
        guard startedGeneration == nil, !attempt.transitionInFlight else { return }
        if let recoverySessionID = request.recoverySessionID {
            guard let generation = attempt.attach(sessionID: recoverySessionID) else {
                message = "The saved operation identity is invalid. Check the device before continuing."
                needsDeviceConfirmation = true
                uncertainSessionID = recoverySessionID
                return
            }
            startAttempt(generation: generation)
        } else {
            startAttempt(generation: attempt.begin())
        }
    }
    private func startAttempt(generation: UUID) {
        guard attempt.generation == generation, startedGeneration != generation else { return }
        startedGeneration = generation
        userRequestedCancellation = false
        progress = 0
        hasProgress = false
        operationPhase = .working
        task = Task { await run(generation: generation) }
    }
    private func run(generation: UUID) async {
        var backendSessionStarted = false
        do {
            let recovering = request.recoverySessionID != nil
            let reply: [String: Any]
            if recovering {
                reply = try await V3ServiceBridge.shared.request(operation: "opPoll",
                    target: generation.uuidString)
            } else {
                status.installBackendStartRequested(attemptID: request.installAttemptID,
                    operationID: request.id, sessionID: generation.uuidString)
                let prepared = try await V3ServiceBridge.shared.request(operation: "opRecoveryPrepare",
                    payload: ["kind": request.operation, "target": request.target,
                              "session": generation.uuidString])
                guard prepared["session"] as? String == generation.uuidString,
                      prepared["phase"] as? String == "prepared" else {
                    throw CombinedFailure(operation: request.operation, stage: .command,
                        code: .staleResult, id: generation.uuidString, retryable: false)
                }
                reply = try await V3ServiceBridge.shared.request(operation: "opStart",
                    payload: ["kind": request.operation, "target": request.target,
                              "session": generation.uuidString])
            }
            if !recovering, reply["failedToStart"] as? Bool == true {
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
                if !recovering { _ = try? await V3ServiceBridge.shared.request(operation: "opCancel", target: id) }
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
            if error is CancellationError, attempt.transitionInFlight { return }
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
            if V3ServiceBridge.shared.hasUncertainOperationSession(generation.uuidString) ||
                request.recoverySessionID == generation.uuidString {
                needsDeviceConfirmation = true
                uncertainSessionID = generation.uuidString
                retryBlocked = true
                message = "SideStore could not confirm the current operation result."
                whatToDo = "Check the device before starting another mutation. If no operation is running, use Reconcile After Checking Device."
                technicalDetails += " backend_settled=no outcome=unknown"
            }
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
    private func confirmUncertainOperationAfterDeviceCheck() {
        guard let sessionID = uncertainSessionID else { return }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "opRecoveryReconcile",
                    target: sessionID, payload: ["userConfirmed": true])
                guard reply["session"] as? String == sessionID,
                      V3ServiceBridge.strictBool(reply["reconciled"]) == true else {
                    throw CombinedFailure(operation: request.operation, stage: .command,
                        code: .staleResult, id: sessionID, retryable: false)
                }
            } catch {
                message = "SideStore could not clear the recovery hold. Keep waiting and check the device again."
                whatToDo = "No new operation was started. Reconnect before trying reconciliation again."
                return
            }
            V3ServiceBridge.shared.retireReconciledOperationService(sessionID: sessionID)
            uncertainSessionID = nil
            needsDeviceConfirmation = false
            terminalBackendSettled = true
            retryBlocked = true
            deviceCheckConfirmedForCompletion = false
            finishReconciliationAfterRetirement()
            status.reload()
            message = "The previous operation result remains unknown."
            whatToDo = "Reload app status, then verify the installed app before starting another operation."
            technicalDetails += " service_retired_after_user_confirmation=yes outcome=unknown"
        }
    }
    private func finishReconciliationAfterRetirement() {
        guard state == "reconciling", let sessionID = attempt.sessionID else { return }
        _ = attempt.accept(state: "failed", generation: attempt.generation, sessionID: sessionID)
        state = "failed"
        status.installTerminal(attemptID: request.installAttemptID,
            operationID: request.id, outcome: "unknown")
    }
    private func pollLoop(id: String, generation: UUID) async throws {
        var settlementRetryDelay: TimeInterval?
        while !Task.isCancelled {
            let interval = settlementRetryDelay ?? V3OperationCompletionPolicy.pollInterval(
                state: state, backendSettled: terminalBackendSettled,
                outcomeUnknown: needsDeviceConfirmation)
            settlementRetryDelay = nil
            try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            try Task.checkCancellation()
            guard attempt.owns(generation: generation, sessionID: id),
                  !attempt.isTerminal || completionAwaitingSettlement else { return }
            let reply: [String: Any]
            do {
                reply = try await V3ServiceBridge.shared.request(operation: "opPoll", target: id)
            } catch {
                guard attempt.owns(generation: generation, sessionID: id),
                      V3OperationCompletionPolicy.shouldRetrySettlementPollFailure(
                        state: state, backendSettled: terminalBackendSettled,
                        outcomeUnknown: needsDeviceConfirmation,
                        cancellationRequested: userRequestedCancellation),
                      !Task.isCancelled else { throw error }
                settlementRetryDelay = V3OperationCompletionPolicy.nextSettlementPollRetryDelay(
                    current: interval)
                NSLog("[V3_OPERATION_UI] settlement_poll_retry session=%@ delay=%.0f",
                      id, settlementRetryDelay ?? 0)
                continue
            }
            settlementRetryDelay = nil
            guard let current = reply["state"] as? String else {
                throw NSError(domain: "V3Operation", code: 2,
                              userInfo: [NSLocalizedDescriptionKey: "The service returned an unreadable operation state."])
            }
            guard reply["session"] as? String == id else { return }
            apply(reply, generation: generation, sessionID: id)
            if V3OperationCompletionPolicy.shouldContinuePolling(state: current,
                backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])) {
                continue
            }
            guard current == "working" || current == "awaitingPrompt" || current == "cancelling" ||
                    current == "reconciling" else { return }
        }
    }
    private func apply(_ reply: [String: Any], generation: UUID, sessionID: String) {
        guard let nextState = reply["state"] as? String else { return }
        guard V3OperationCancellationReplyPolicy.shouldApplyPollState(
            userRequestedCancellation: userRequestedCancellation, nextState: nextState) else { return }
        if nextState == "completed", state == "completed",
           attempt.owns(generation: generation, sessionID: sessionID) {
            let wasAwaitingSettlement = completionAwaitingSettlement
            applyCompletionSettlement(reply, sessionID: sessionID)
            if wasAwaitingSettlement && !completionAwaitingSettlement { status.reload() }
            return
        }
        let replyBackendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
        let replyOutcomeUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])
        let resolvesProvisionalUnknown = attempt.ownsProvisionalResolution(
            generation: generation, sessionID: sessionID, currentState: state,
            currentBackendSettled: terminalBackendSettled,
            currentOutcomeUnknown: needsDeviceConfirmation, nextState: nextState,
            nextBackendSettled: replyBackendSettled, nextOutcomeUnknown: replyOutcomeUnknown,
            nextOperation: reply["operation"] as? String,
            verifiedDeleteCompletion: V3OperationReplyFieldPolicy.strictBoolean(
                reply["verifiedDeleteCompletion"]) == true)
        guard resolvesProvisionalUnknown ||
              attempt.accept(state: nextState, generation: generation, sessionID: sessionID) else { return }
        state = nextState
        if !["working", "awaitingPrompt", "cancelling", "reconciling"].contains(nextState) {
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
        if oldPromptID != (nextPrompt?["id"] as? String) { promptResponseBlocked = false }
        switch state {
        case "completed":
            progress = 1
            hasProgress = true
            // Terminal success stays visible until the user presses Done.
            // Auto-dismissing here made successful fast operations look like
            // nothing happened.
            applyCompletionSettlement(reply, sessionID: sessionID)
            recordRefresh("completed", "The operation completed. Reload the app list to confirm the result.")
            status.reload()
        case "cancelled":
            // Keep cancellation visible and distinguish the user's Cancel
            // action from a backend cancellation that arrived independently.
            terminalBackendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
            let outcomeUnknown = V3OperationCancellationResolutionPolicy.requiresReconciliation(
                backendSettled: terminalBackendSettled,
                outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
            needsDeviceConfirmation = outcomeUnknown
            uncertainSessionID = outcomeUnknown ? sessionID : nil
            retryBlocked = outcomeUnknown
            recoveryDestination = nil
            if outcomeUnknown {
                message = request.operation == "delete"
                    ? "SideStore has not confirmed that the app was removed or that deletion stopped."
                    : "SideStore has not confirmed that the device operation stopped."
                whatToDo = "Keep this screen open while SideStore reconciles the result. Do not start another operation yet."
                technicalDetails = "backend_settled=no outcome=unknown"
            } else {
                let cancellation = V3OperationCancellationPresentationPolicy.resolve(
                    userRequested: userRequestedCancellation)
                message = cancellation.message
                whatToDo = cancellation.whatToDo
                technicalDetails = ""
                recoveryDestination = nil
                deviceCheckConfirmedForCompletion = false
            }
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
        case "reconciling":
            let failure = (reply["failure"] as? [String: Any]).flatMap {
                CombinedFailure.decode($0, expectedID: sessionID)
            }
            if let failure { failureContext.recordPipelineFailure(failure) }
            terminalBackendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
            needsDeviceConfirmation = true
            uncertainSessionID = sessionID
            retryBlocked = true
            message = "SideStore is still checking whether the app was removed."
            whatToDo = "Keep this screen open while SideStore waits for the delete callback. Do not retry until the result is confirmed."
            technicalDetails = (failure?.technicalDetails ??
                "schema=1 operation=delete stage=command code=timedOut correlation=\(sessionID) underlying_domain=redacted underlying_code=redacted retryable=unknown") +
                " backend_settled=no outcome=unknown"
            recoveryDestination = nil
        case "failed":
            let failure = (reply["failure"] as? [String: Any]).flatMap {
                CombinedFailure.decode($0, expectedID: sessionID)
            } ?? CombinedFailure(operation: request.operation,
                stage: CombinedFailure.Stage(rawValue: reply["stage"] as? String ?? "") ?? .command,
                code: CombinedFailure.Code(rawValue: reply["code"] as? String ?? "") ?? .failed,
                id: sessionID, retryable: reply["retryable"] as? Bool)
            failureContext.recordPipelineFailure(failure)
            let backendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
            terminalBackendSettled = backendSettled
            let outcomeUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]) ||
                backendSettled != true
            needsDeviceConfirmation = outcomeUnknown
            uncertainSessionID = outcomeUnknown ? sessionID : nil
            retryBlocked = !V3OperationRetrySafetyPolicy.canRetry(
                backendSettled: backendSettled,
                outcomeUnknown: outcomeUnknown)
            message = failureContext.whatHappened
            whatToDo = outcomeUnknown
                ? "The backend has not confirmed that the operation stopped. Do not retry yet; wait for status reconciliation and reload before starting another mutation."
                : failureContext.whatToDo
            technicalDetails = failureContext.technicalDetails +
                (outcomeUnknown ? " backend_settled=no outcome=unknown" : "")
            recoveryDestination = failureContext.currentFailure?.recoveryDestination
            if !outcomeUnknown { recordRefresh("failed", message) }
        default: break
        }
    }
    private func applyCompletionSettlement(_ reply: [String: Any], sessionID: String) {
        terminalBackendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
        let outcomeUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])
        switch V3OperationCompletionPolicy.disposition(state: "completed",
            backendSettled: terminalBackendSettled, outcomeUnknown: outcomeUnknown) {
        case .completedAwaitingBackendSettlement, .outcomeUnknownAwaitingBackendSettlement:
            needsDeviceConfirmation = true
            uncertainSessionID = sessionID
            retryBlocked = true
            message = request.operation == "delete"
                ? "The app was removed from the device, but SideStore is still waiting for the delete callback to settle."
                : request.title + " completed, but SideStore has not confirmed that its backend session settled."
            whatToDo = "Keep this screen open while SideStore finishes cleanup. If it remains here, check the device, then use Reconcile After Checking Device before starting another mutation."
            technicalDetails = "backend_settled=no outcome=verified_completion_pending_callback"
            recoveryDestination = nil
        case .completed:
            needsDeviceConfirmation = false
            uncertainSessionID = nil
            deviceCheckConfirmedForCompletion = false
            retryBlocked = false
            message = request.title + " completed successfully."
            whatToDo = "Reload app status to confirm the installed app and signing state."
            technicalDetails = ""
            recoveryDestination = nil
            failureContext.reset()
        case .notCompleted:
            break
        }
    }
    private func answerPrompt(id: String, answer: [String: String]) async {
        guard !id.isEmpty, let session = attempt.sessionID else { return }
        let generation = attempt.generation
        do {
            let secretToken = try V3SecretHandoff.storeStringDictionary(answer)
            defer { V3SecretHandoff.discard(secretToken) }
            let reply = try await V3ServiceBridge.shared.request(operation: "opAnswer", target: session,
                payload: ["prompt": id, "secretToken": secretToken])
            guard attempt.matches(generation: generation, sessionID: session),
                  reply["session"] as? String == session else { return }
            if V3ServiceBridge.strictBool(reply["responsePending"]) == true {
                guard prompt?["id"] as? String == id else { return }
                promptSubmitting = true
                message = "Your response is being processed..."
                return
            }
            apply(reply, generation: generation, sessionID: session)
        } catch {
            guard attempt.matches(generation: generation, sessionID: session) else { return }
            promptSubmitting = false
            let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                operation: request.operation, stage: .command, id: session)
            let promptFailure = V3OperationPromptFailureDetails(failure)
            message = promptFailure.failure.whatHappened
            whatToDo = promptFailure.failure.recommendedAction
            technicalDetails = promptFailure.failure.technical
            recoveryDestination = promptFailure.failure.recoveryDestination
            promptResponseBlocked = promptFailure.blocksResubmission
        }
    }
    private func addSourceAndRetry(id: String) async {
        guard !id.isEmpty, attempt.beginTransition() else { return }
        do {
            let preview = try await V3ServiceBridge.shared.request(operation: "sourcePreview", target: id)
            let added = try await V3ServiceBridge.shared.request(operation: "sourceAddConfirmed",
                target: preview["identifier"] as? String ?? id)
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                added, operation: "sourceAddConfirmed")
            sourceAddFailure = nil
            sourceAddRetryBlocked = false
            retryBlocked = false
            status.reload()
            attempt.endTransition()
            retry()
        } catch {
            attempt.endTransition()
            let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                operation: "source", stage: .source,
                id: attempt.sessionID ?? UUID().uuidString)
            let details = V3OperationFailureDetails(failure)
            sourceAddFailure = details
            sourceAddRetryBlocked = details.retryDisposition == .blocked ||
                details.retryDisposition == .prerequisite
            retryBlocked = true
            message = details.whatHappened
            whatToDo = details.recommendedAction
            technicalDetails = details.technical
            recoveryDestination = details.recoveryDestination
        }
    }
    private func retry() {
        guard retryAllowed, attempt.beginTransition() else { return }
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
        promptResponseBlocked = false
        sourceAddFailure = nil
        sourceAddRetryBlocked = false
        state = "working"
        Task { @MainActor in
            oldTask?.cancel()
            do {
                if let oldSession {
                    let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                    let disposition = V3OperationRetrySafetyPolicy.disposition(
                        state: reply["state"] as? String,
                        backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"])
                            ?? V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                        outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
                    if disposition == .alreadyCompleted {
                        await oldTask?.value
                        guard attempt.transitionInFlight, attempt.generation == transitionGeneration else { return }
                        attempt.endTransition()
                        uncertainSessionID = nil
                        needsDeviceConfirmation = false
                        retryBlocked = true
                        state = "completed"
                        progress = 1
                        hasProgress = true
                        message = request.title + " completed successfully."
                        whatToDo = "Reload app status to confirm the installed app and signing state."
                        technicalDetails = ""
                        failureContext.reset()
                        status.installTerminal(attemptID: request.installAttemptID,
                            operationID: request.id, outcome: "completed")
                        status.reload()
                        recordRefresh("completed", message)
                        V3ServiceBridge.shared.forgetSettledOperationSession(oldSession)
                        return
                    }
                    if disposition != .retry {
                        await oldTask?.value
                        guard attempt.transitionInFlight, attempt.generation == transitionGeneration else { return }
                        let failure = CombinedFailure(operation: request.operation, stage: .command,
                            code: .timedOut, id: transitionGeneration.uuidString)
                        failureContext.recordStartFailure(failure)
                        attempt.endTransition()
                        retryBlocked = true
                        needsDeviceConfirmation = true
                        uncertainSessionID = oldSession
                        state = "failed"
                        message = failureContext.whatHappened
                        whatToDo = "The previous operation has not settled. Check the device; do not start another mutation until the result is reconciled."
                        technicalDetails = failureContext.technicalDetails + " backend_settled=no outcome=unknown"
                        return
                    }
                    V3ServiceBridge.shared.forgetSettledOperationSession(oldSession)
                }
                await oldTask?.value
                guard attempt.transitionInFlight, attempt.generation == transitionGeneration else { return }
                uncertainSessionID = nil
                status.prepareInstallRetry(attemptID: request.installAttemptID)
                failureContext.beginRetry()
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
                needsDeviceConfirmation = oldSession != nil
                if oldSession != nil { uncertainSessionID = oldSession }
                presentCurrentFailure()
            }
        }
    }
    private func cancelAttempt() {
        guard isRunning, state != "cancelling", attempt.beginTransition() else { return }
        let keepDeletePoller = V3DeleteCancellationPolicy.keepsHostPollMonitor(
            operation: request.operation)
        userRequestedCancellation = true
        state = "cancelling"
        message = ""
        let oldTask = task
        let transitionGeneration = attempt.generation
        let oldSession = attempt.sessionID ?? transitionGeneration.uuidString
        uncertainSessionID = oldSession
        startedGeneration = nil
        prompt = nil
        Task { @MainActor in
            if !keepDeletePoller { oldTask?.cancel() }
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                if !keepDeletePoller { await oldTask?.value }
                guard attempt.generation == transitionGeneration else { return }
                if attempt.sessionID == nil {
                    _ = attempt.bind(sessionID: oldSession, generation: transitionGeneration)
                }
                let settledCancellationAcknowledgement = V3OperationCancellationOutcomePolicy.terminalState(
                    expectedSessionID: oldSession,
                    replySessionID: reply["session"] as? String,
                    state: reply["state"] as? String,
                    backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                    stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                    outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
                let cancellationReplyIsCorrelated = V3OperationCancellationOutcomePolicy.isCorrelated(
                    expectedSessionID: oldSession, replySessionID: reply["session"] as? String)
                if (keepDeletePoller && settledCancellationAcknowledgement != nil) ||
                   (!keepDeletePoller && cancellationReplyIsCorrelated) {
                    apply(reply, generation: transitionGeneration, sessionID: oldSession)
                    if let settledCancellationAcknowledgement {
                        if V3OperationCancellationOutcomePolicy.shouldClearSessionHandle(
                            currentSessionID: uncertainSessionID, expectedSessionID: oldSession,
                            replySessionID: reply["session"] as? String,
                            state: reply["state"] as? String,
                            backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                            stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                            outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])) {
                            // A delayed unsettled acknowledgment cannot discard
                            // the handle established by a newer delete poll.
                            state = settledCancellationAcknowledgement
                            terminalBackendSettled = true
                            needsDeviceConfirmation = false
                            uncertainSessionID = nil
                        }
                    }
                } else if !keepDeletePoller {
                    state = "failed"
                    needsDeviceConfirmation = true
                    uncertainSessionID = oldSession
                    retryBlocked = true
                    message = "SideStore could not confirm that this operation stopped."
                    whatToDo = "Reload operation status before starting another mutation."
                    technicalDetails = "operation_session_response_mismatch=yes backend_settled=no"
                }
                if keepDeletePoller && state == "cancelling" {
                    message = "Cancellation was requested. SideStore is waiting for the delete result."
                    whatToDo = "Keep this screen open. Do not start another operation until the delete result is confirmed."
                    retryBlocked = true
                } else if ["working", "cancelling"].contains(state) {
                    state = "reconciling"
                    terminalBackendSettled = false
                    needsDeviceConfirmation = true
                    uncertainSessionID = oldSession
                    retryBlocked = true
                    message = "SideStore has not confirmed that the operation stopped."
                    whatToDo = "Reload operation status before starting another mutation."
                    technicalDetails = "backend_settled=no outcome=unknown"
                }
            } catch {
                if !keepDeletePoller { await oldTask?.value }
                guard attempt.generation == transitionGeneration else { return }
                state = "failed"
                let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                    operation: request.operation, stage: .xpcConnection,
                    id: transitionGeneration.uuidString)
                failureContext.recordPipelineFailure(failure)
                retryBlocked = true
                needsDeviceConfirmation = true
                uncertainSessionID = oldSession
                message = "SideStore could not confirm that the operation stopped. It may still be running."
                whatToDo = "Reconnect and reload operation status before trying another mutation."
                technicalDetails = failure.technicalDetails
            }
            attempt.endTransition()
        }
    }
    private func acknowledgeAndDismiss() {
        guard V3OperationCompletionPolicy.mayDismiss(state: state,
            backendSettled: terminalBackendSettled,
            deviceCheckConfirmed: deviceCheckConfirmedForCompletion) else {
            confirmUncertainRetirement = true
            return
        }
        guard attempt.beginTransition() else { return }
        isDismissing = true
        let oldTask = task
        let oldSession = attempt.supersede()
        oldTask?.cancel()
        Task { @MainActor in
            await oldTask?.value
            let cancellationTarget = uncertainSessionID ?? oldSession
            var confirmedOutcome = "cancelled"
            if let cancellationTarget {
                do {
                    let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: cancellationTarget)
                    let backendSettled = V3OperationTerminalAcceptancePolicy.isSettledTerminal(
                        state: reply["state"] as? String,
                        backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                        stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                        outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
                    guard backendSettled else {
                        isDismissing = false
                        attempt.endTransition()
                        needsDeviceConfirmation = true
                        uncertainSessionID = cancellationTarget
                        retryBlocked = true
                        message = "SideStore has not confirmed that the operation stopped."
                        whatToDo = "Check the device. If no operation is still running, use Reconcile After Checking Device."
                        return
                    }
                    uncertainSessionID = nil
                    if ["completed", "failed", "cancelled", "requiresSource", "waitingForAuthentication"]
                        .contains(reply["state"] as? String ?? "") {
                        V3ServiceBridge.shared.forgetSettledOperationSession(cancellationTarget)
                    }
                    if reply["state"] as? String == "completed" {
                        confirmedOutcome = "completed"
                        state = "completed"
                        progress = 1
                        hasProgress = true
                        message = request.title + " completed successfully."
                        whatToDo = "Reload app status to confirm the installed app and signing state."
                        recordRefresh("completed", message)
                    } else if reply["state"] as? String == "failed" {
                        confirmedOutcome = "failed"
                    }
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
                operationID: request.id, outcome: confirmedOutcome)
            if request.operation == "installSharedIPA" {
                let token = request.installAttemptID.flatMap {
                    status.resetInstallUI(attemptID: $0, outcome: "acknowledged",
                        preserveRecoveryDestination: status.operationRecoveryDestination != nil)
                }
                if let token, !stagedIPACleaned {
                    stagedIPACleaned = await status.cleanupStagedIPA(token, allowLocalFallback: true)
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
    var isSubmissionBlocked = false
    let onAnswer: ([String: String]) -> Void
    @State private var fields: [String: String] = [:]
    @State private var selected: Set<String> = []
    @State private var copiedDetails = false
    @State private var repairURL: URL?
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
                    Button("Change Verification Method", systemImage: "arrow.uturn.backward") {
                        var answer = fields
                        answer["action"] = "changeMethod"
                        answer["choice"] = "changeMethod"
                        respond(answer)
                    }
                    .disabled(isSubmitting)
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
                    .disabled((fields["code"] ?? "").isEmpty || isSubmitting || isSubmissionBlocked)
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
                if field["key"] == "step" || field["key"] == "mode" || field["key"] == "activeID" || field["key"] == "phoneID" || field["key"] == "url" || field["key"] == "urlToken" || field["key"] == "serials" || field["key"] == "technical" {
                    if field["key"] == "urlToken" {
                        if let repairURL {
                            Link("Open Apple Account Repair", destination: repairURL)
                                .font(.caption)
                        } else {
                            Text("Apple account repair link is unavailable.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    } else if let value = field["value"], !value.isEmpty, field["key"] == "url" {
                        if let repairURL = V3AuthRepairURLPolicy.openableURL(value) {
                            Link("Open Apple Account Repair", destination: repairURL)
                                .font(.caption)
                        }
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
                ForEach(options.filter {
                    V3MultiSelectPromptAnswerPolicy.isMemberOption(
                        kind: kind, optionID: $0["id"] ?? "")
                }, id: \.self) { option in
                    Button {
                        toggle(option["id"] ?? "")
                    } label: {
                        HStack {
                            Image(systemName: selected.contains(option["id"] ?? "") ? "checkmark.circle.fill" : "circle")
                                .foregroundColor(.accentColor)
                            Text(option["label"] ?? "")
                        }
                    }
                    .disabled(isSubmitting || isSubmissionBlocked)
                }
                if kind == "revocation" {
                    Button("Keep Existing") {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("keep", fields: fields))
                    }
                        .disabled(isSubmitting || isSubmissionBlocked)
                } else {
                    Button("Keep All") {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("keepAll", fields: fields))
                    }
                        .disabled(isSubmitting || isSubmissionBlocked)
                    Button("Remove All", role: .destructive) {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("removeAll", fields: fields))
                    }
                    .disabled(isSubmitting || isSubmissionBlocked)
                }
                Button(kind == "revocation" ? "Revoke Selected" : "Remove Selected", role: .destructive) {
                    respond(V3MultiSelectPromptAnswerPolicy.selectedMembersAnswer(
                        kind: kind, selectedIDs: selected, fields: fields))
                }
                .disabled(selected.isEmpty || isSubmitting || isSubmissionBlocked)
            } else {
                ForEach(options, id: \.self) { option in
                    Button(option["label"] ?? "", role: (option["id"] == "cancel" || option["id"] == "deny") ? .cancel : .none) {
                        var answer = fields
                        answer["choice"] = option["id"] ?? ""
                        answer["action"] = option["id"] ?? ""
                        respond(answer)
                    }
                    .disabled(isSubmitting || (isSubmissionBlocked &&
                        !["cancel", "changeMethod"].contains(option["id"] ?? "")))
                }
            }
            }
        }
        .onAppear {
            loadFields()
            Task { await loadRepairURL() }
        }
        .onChange(of: prompt["id"] as? String ?? "") { _ in
            loadFields()
            Task { await loadRepairURL() }
        }
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
        .disabled(isSubmitting || (isSubmissionBlocked &&
            !["cancel", "changeMethod"].contains(option["id"] ?? "")))
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
    private func loadRepairURL() async {
        repairURL = nil
        guard kind == "accountRepair",
              let token = fieldDefs.first(where: { $0["key"] == "urlToken" })?["value"],
              V3SecretHandoff.isValidToken(token),
              let rawURL = try? V3SecretHandoff.consumeString(token) else { return }
        repairURL = V3AuthRepairURLPolicy.openableURL(rawURL)
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
    @Published private(set) var revision = 0
    @Published var message = ""
    @Published private(set) var currentAttemptFailure = V3AuthAttemptFailureNotice()
    @Published private(set) var promptResponseDiagnostics = ""
    @Published private(set) var promptResponseBlocked = false
    @Published var deliveryProgressMessage = ""
    @Published var twoFactorTransientStep: V3TwoFactorStep?
    @Published var team = ""
    @Published var promptSubmitting = false
    @Published private(set) var isCancelling = false
    @Published private(set) var cancellationConfirmed = true
    @Published private(set) var cancellationWasAttempted = false
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
    @Published private(set) var provisioningSessionUnavailable = false
    @Published private(set) var provisioningRetryBlockedByActiveSession = false
    @Published private(set) var provisioningFinishedLater = false
    @Published private(set) var provisioningIncomplete = false
    // Sticky once the service reports an authenticated terminal. It survives a
    // provisioning retry so the screen keeps saying the sign-in succeeded while
    // provisioning is running again.
    @Published private(set) var signedIn = false
    // Retry identity outlives a Sign In view because the app root observes its
    // terminal event after a superseded poll monitor takes ownership.
    private var provisioningRetryReadinessOwnership = V3ProvisioningRetryReadinessOwnership()
    private var pendingAuthenticationReadinessSessionID: String?
    private var pendingAuthenticationReadinessAttemptSequence: UInt64?
    private var provisioningRetryReadinessAttemptSequence: UInt64?
    private var reconciledProvisioningRetryReadinessSessionID: String?
    private var session: String?
    private var authoritativeActiveAuthenticationSessionID: String?
    private var task: Task<Void, Never>?
    private var reconciliationGate = V3AuthReconciliationGate()
    private var promptResponseGeneration: UInt64 = 0

    // V3_AUTH_SUCCESS_IS_NOT_PROVISIONING_SUCCESS_V1: authentication succeeded
    // whenever the service reports an authenticated terminal, regardless of
    // whether provisioning then failed.
    var isSignedIn: Bool { signedIn }
    var hasSession: Bool { session != nil }
    private var terminalFailureKind: String? { previousFailure?["kind"] as? String }
    private var terminalFailureRetryable: Bool? {
        V3ServiceBridge.strictBool(previousFailure?["retryable"])
    }
    var terminalFailureAction: V3AuthTerminalFailureAction {
        V3AuthTerminalFailureActionPolicy.resolve(kind: terminalFailureKind,
            retryable: terminalFailureRetryable)
    }
    var terminalFailureGuidance: String? {
        V3AuthTerminalFailureActionPolicy.guidance(kind: terminalFailureKind,
            retryable: terminalFailureRetryable)
    }
    // The provisioning problem is only present when a classified failure arrived.
    var hasProvisioningProblem: Bool {
        provisioningIncomplete && !provisioningMessage.isEmpty
            && !provisioningFinishedLater
    }

    func begin() {
        guard canBegin else { return }
        task?.cancel()
        provisioningRetryReadinessOwnership.clear()
        provisioningRetryReadinessAttemptSequence = nil
        reconciledProvisioningRetryReadinessSessionID = nil
        let requestedSession = UUID().uuidString
        reconciliationGate.invalidate()
        session = requestedSession
        pendingAuthenticationReadinessSessionID = requestedSession
        pendingAuthenticationReadinessAttemptSequence = V3AuthReadinessRefreshEvent.nextAttemptSequence()
        attempts = 0
        revision = 0
        state = "working"
        message = ""
        currentAttemptFailure.clear()
        promptResponseDiagnostics = ""
        promptResponseBlocked = false
        deliveryProgressMessage = ""
        twoFactorTransientStep = nil
        prompt = nil
        previousFailure = nil
        promptSubmitting = false
        cancellationConfirmed = true
        cancellationWasAttempted = false
        signedIn = false
        clearProvisioningOutcome()
        task = Task { await run(sessionID: requestedSession) }
    }
    var canBegin: Bool {
        !isCancelling && cancellationConfirmed && !provisioningRetryBlockedByActiveSession &&
            !["working", "awaitingPrompt", "resultUnknown"].contains(state)
    }

    private func clearProvisioningOutcome() {
        provisioningMessage = ""
        provisioningTechnical = ""
        provisioningCode = ""
        provisioningStage = ""
        provisioningCorrelation = ""
        provisioningRetryAvailable = false
        provisioningSessionUnavailable = false
        provisioningRetryBlockedByActiveSession = false
        provisioningFinishedLater = false
        provisioningIncomplete = false
    }

    // V3_RETRY_PROVISIONING_REUSES_SESSION_V1: the Apple session is already
    // authenticated, so the retry is a distinct operation. It deliberately does
    // not reuse the interactive begin operation, which would ask for credentials
    // and 2FA again.
    func retryProvisioning() {
        guard canRetryProvisioning else { return }
        let previouslyAvailable = provisioningRetryAvailable
        task?.cancel()
        pendingAuthenticationReadinessSessionID = nil
        pendingAuthenticationReadinessAttemptSequence = nil
        let requestedSession = UUID().uuidString
        provisioningRetryReadinessAttemptSequence = V3AuthReadinessRefreshEvent.nextAttemptSequence()
        reconciledProvisioningRetryReadinessSessionID = nil
        reconciliationGate.invalidate()
        session = requestedSession
        attempts = 0
        revision = 0
        state = "working"
        message = ""
        currentAttemptFailure.clear()
        prompt = nil
        previousFailure = nil
        promptSubmitting = false
        promptResponseDiagnostics = ""
        promptResponseBlocked = false
        clearProvisioningOutcome()
        provisioningRetryReadinessOwnership.begin(sessionID: requestedSession)
        task = Task { await runProvisioningRetry(previouslyAvailable: previouslyAvailable) }
    }
    var canRetryProvisioning: Bool {
        V3AuthProvisioningRecoveryPolicy.resolve(state: state, hasSession: session != nil,
            signedIn: signedIn, provisioningRetryAvailable: provisioningRetryAvailable,
            isCancelling: isCancelling, cancellationConfirmed: cancellationConfirmed,
            authenticationActive: provisioningRetryBlockedByActiveSession)
            .showRetryProvisioning
    }

    private func releaseProvisioningRetryReadiness(sessionID: String) {
        guard provisioningRetryReadinessOwnership.owns(sessionID: sessionID) else { return }
        provisioningRetryReadinessOwnership.release(sessionID: sessionID)
        provisioningRetryReadinessAttemptSequence = nil
        reconciledProvisioningRetryReadinessSessionID = nil
    }

    private func runProvisioningRetry(previouslyAvailable: Bool) async {
        guard let requestedSession = session else { return }
        guard provisioningRetryReadinessOwnership.owns(sessionID: requestedSession) else { return }
        let sessionDeadline = Date().addingTimeInterval(V3ServiceBridge.authSessionLifetime)
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "authRetryProvisioning",
                target: requestedSession,
                payload: ["session": requestedSession, "sessionDeadline": sessionDeadline])
            guard V3AuthSessionResponsePolicy.mayAcceptStartedSession(
                expectedSessionID: requestedSession, replySessionID: reply["session"] as? String,
                currentSessionID: session, cancellationInProgress: isCancelling) else {
                // A local Cancel can race the begin reply after the backend
                // accepted this exact session. Keep its ownership for the
                // terminal authCancel reply; only retire mismatched/replaced
                // starts here.
                if session != requestedSession ||
                   (reply["session"] as? String) != requestedSession {
                    releaseProvisioningRetryReadiness(sessionID: requestedSession)
                }
                _ = try? await V3ServiceBridge.shared.request(operation: "authCancel", target: requestedSession)
                guard !Task.isCancelled, session == requestedSession else { return }
                await reconcile(force: true, expectedSession: requestedSession)
                return
            }
            guard let id = reply["session"] as? String,
                  reply["state"] as? String != "failed" else {
                releaseProvisioningRetryReadiness(sessionID: requestedSession)
                // The saved session is gone. Fall back to a full, honest sign-in
                // instead of silently claiming provisioning was retried.
                await reconcile(force: true, expectedSession: requestedSession)
                guard !Task.isCancelled, session == requestedSession else { return }
                if signedIn {
                    if state == "completed" { return }
                    state = "authenticatedProvisioningIncomplete"
                    message = "Apple ID signed in successfully."
                    provisioningMessage = "The saved provisioning session is no longer available. Open Account & Signing to reauthenticate before retrying setup."
                    provisioningRetryAvailable = false
                    provisioningSessionUnavailable = true
                } else {
                    state = "failed"
                    message = reply["message"] as? String
                        ?? "The saved Apple session is no longer valid. Sign in again with this Apple ID."
                    provisioningMessage = ""
                    provisioningSessionUnavailable = true
                }
                return
            }
            session = id
            try await pollLoop(id: id, sessionDeadline: sessionDeadline)
        } catch {
            if isCancelling || Task.isCancelled { return }
            let pollFailure = (error as? V3AuthPollFailure).flatMap {
                $0.sessionID == requestedSession ? $0 : nil
            }
            let failureResponseGeneration = pollFailure?.promptResponseGeneration ?? promptResponseGeneration
            if let notDispatched = error as? CombinedFailure,
               V3AuthProvisioningRetryDispatchPolicy.isConfirmedNotDispatched(notDispatched) {
                releaseProvisioningRetryReadiness(sessionID: requestedSession)
                let reconciliationGenerationBefore = reconciliationGate.generation
                let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession)
                guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                    requestedSessionID: requestedSession, currentSessionID: session,
                    capturedPromptResponseGeneration: failureResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    reconciliationGenerationBefore: reconciliationGenerationBefore,
                    currentReconciliationGeneration: reconciliationGate.generation,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else { return }
                provisioningTechnical = notDispatched.technicalDetails
                if signedIn {
                    if state == "completed" {
                        message = V3AuthProvisioningRetryDispatchPolicy.whatHappened(notDispatched) +
                            " " + notDispatched.recovery
                        return
                    }
                    state = "authenticatedProvisioningIncomplete"
                    provisioningIncomplete = true
                    message = "Apple ID signed in successfully."
                    provisioningMessage = V3AuthProvisioningRetryDispatchPolicy.whatHappened(notDispatched) +
                        " " + notDispatched.recovery
                    if snapshotConfirmed {
                        provisioningSessionUnavailable = !provisioningRetryAvailable &&
                            !provisioningRetryBlockedByActiveSession
                        if provisioningRetryBlockedByActiveSession {
                            provisioningMessage = "Another sign-in or provisioning attempt is still active. Wait for it to finish, then reload status before retrying provisioning."
                        }
                        if provisioningSessionUnavailable {
                            provisioningMessage = "Apple ID is signed in, but the saved provisioning session is unavailable. Finish Later, then reopen Account & Signing to sign in again before retrying provisioning."
                        }
                    } else {
                        provisioningRetryAvailable = previouslyAvailable
                        provisioningRetryBlockedByActiveSession =
                            notDispatched.safeCause == .operationInProgress
                        if provisioningRetryBlockedByActiveSession {
                            provisioningRetryAvailable = false
                            provisioningMessage = "Another sign-in or provisioning attempt is already active. Reload status after it finishes before retrying."
                        }
                        provisioningSessionUnavailable = false
                    }
                } else {
                    state = "failed"
                    message = V3AuthProvisioningRetryDispatchPolicy.whatHappened(notDispatched) +
                        " " + notDispatched.recovery
                    provisioningMessage = ""
                    provisioningRetryAvailable = false
                }
                return
            }
            let reconciliationGenerationBefore = reconciliationGate.generation
            let sessionUnavailable = ((pollFailure?.underlying ?? error) as? CombinedFailure)?.safeCause == .authSessionUnavailable
            let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession,
                retireInactiveAuthSession: !sessionUnavailable)
            if let sessionFailure = (pollFailure?.underlying ?? error) as? CombinedFailure,
               sessionFailure.safeCause == .authSessionUnavailable {
                releaseProvisioningRetryReadiness(sessionID: requestedSession)
                resolveUnavailableAuthSession(sessionFailure, expectedSessionID: requestedSession,
                    snapshotConfirmed: snapshotConfirmed)
                return
            }
            if let pollFailure,
               restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                    sessionDeadline: sessionDeadline,
                    failedPromptRevision: pollFailure.promptRevision,
                    failedPromptResponseGeneration: pollFailure.promptResponseGeneration,
                    pollFailureIsTransient: (pollFailure.underlying as? CombinedFailure)
                        .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                    provisioningRetry: true,
                    reconciliationWasSuperseded: reconciliationGate.generation !=
                        (reconciliationGenerationBefore &+ 1)) {
                return
            }
            if pollFailure == nil,
               V3AuthPollMonitorRecoveryPolicy.shouldResumeAfterAmbiguousStart(
                    requestedSessionID: requestedSession, currentSessionID: session,
                    activeSessionID: authoritativeActiveAuthenticationSessionID,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled),
               restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                    sessionDeadline: sessionDeadline, failedPromptRevision: revision,
                    failedPromptResponseGeneration: promptResponseGeneration,
                    pollFailureIsTransient: false, provisioningRetry: true,
                    reconciliationWasSuperseded: false) {
                return
            }
            guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                requestedSessionID: requestedSession, currentSessionID: session,
                capturedPromptResponseGeneration: failureResponseGeneration,
                currentPromptResponseGeneration: promptResponseGeneration,
                reconciliationGenerationBefore: reconciliationGenerationBefore,
                currentReconciliationGeneration: reconciliationGate.generation,
                cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else {
                if let pollFailure {
                    _ = restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                        sessionDeadline: sessionDeadline,
                        failedPromptRevision: pollFailure.promptRevision,
                        failedPromptResponseGeneration: pollFailure.promptResponseGeneration,
                        pollFailureIsTransient: (pollFailure.underlying as? CombinedFailure)
                            .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                        provisioningRetry: true,
                        reconciliationWasSuperseded: reconciliationGate.generation !=
                            (reconciliationGenerationBefore &+ 1))
                }
                return
            }
            if let pollFailure, prompt != nil {
                let underlying = pollFailure.underlying
                state = "resultUnknown"
                prompt = nil
                promptSubmitting = false
                deliveryProgressMessage = ""
                twoFactorTransientStep = nil
                cancellationConfirmed = false
                message = "Apple ID is signed in, but SideStore could not confirm the current verification or provisioning response. Cancel the unconfirmed session before starting another attempt."
                provisioningMessage = V3FailureGuidance.message(underlying)
                provisioningTechnical = (underlying as? CombinedFailure)?.technicalDetails ?? ""
                provisioningIncomplete = true
                provisioningRetryAvailable = false
                provisioningSessionUnavailable = false
                currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                    authenticated: signedIn, failureMessage: V3FailureGuidance.message(underlying),
                    technicalDetails: provisioningTechnical)
                return
            }
            provisioningTechnical = ((pollFailure?.underlying ?? error) as? CombinedFailure)?.technicalDetails ?? ""
            if signedIn {
                if state == "completed" { return }
                if snapshotConfirmed && provisioningSessionUnavailable {
                    provisioningMessage = "Apple ID is signed in, but the saved provisioning session is unavailable. Open Account & Signing to sign in again before retrying setup."
                    provisioningRetryAvailable = false
                    return
                }
                state = "authenticatedProvisioningIncomplete"
                provisioningMessage = pollFailure.map {
                    "Apple ID is signed in, but SideStore could not confirm the provisioning result. " +
                        V3FailureGuidance.message($0.underlying)
                } ?? "Retry Provisioning could not be confirmed. Your last confirmed state is still signed in. Reload status, then try again."
                provisioningRetryAvailable = V3ProvisioningRetryRecoveryPolicy.availabilityAfterFailure(
                    snapshotConfirmed: snapshotConfirmed,
                    snapshotAllowsRetry: provisioningRetryAvailable,
                    previouslyConfirmedAvailable: previouslyAvailable)
                provisioningSessionUnavailable = snapshotConfirmed ? provisioningSessionUnavailable : false
            } else {
                state = "failed"
                message = "The provisioning retry could not be started, and SideStore could not confirm the account state. Check Account & Signing, then reload status."
                provisioningMessage = ""
                provisioningRetryAvailable = false
            }
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

    @discardableResult
    func reconcile(force: Bool = false, expectedSession: String? = nil,
                   retireInactiveAuthSession: Bool = true) async -> Bool {
        guard force || !["working", "awaitingPrompt"].contains(state) else { return false }
        guard V3AuthReconciliationSessionPolicy.mayStart(
            expectedSessionID: expectedSession, currentSessionID: session) else { return false }
        let ticket = reconciliationGate.begin(sessionID: session, state: state, revision: revision)
        authoritativeActiveAuthenticationSessionID = nil
        let reportedTerminalState = state
        do {
            let snapshot = try await V3ServiceBridge.shared.request(operation: "snapshot")
            guard reconciliationGate.mayApply(ticket, sessionID: session,
                state: state, revision: revision) else { return false }
            guard let authSnapshot = V3ServiceBridge.authSnapshot(snapshot) else {
                message = "SideStore returned account status that could not be validated. Reload status and try again."
                return false
            }
            let accountFacts = V3AuthSnapshotAuthorityPolicy.facts(authSnapshot)
            authoritativeActiveAuthenticationSessionID = accountFacts.authenticationSessionID
            let ownerSessionID = expectedSession ?? session
            let authenticationActiveForCurrentSession = V3AuthSessionCorrelationPolicy.isActive(
                sessionID: ownerSessionID, authenticationActive: accountFacts.authenticationActive,
                activeSessionID: accountFacts.authenticationSessionID)
            let anotherSessionActive = V3AuthSessionCorrelationPolicy.hasOtherActiveSession(
                sessionID: ownerSessionID, authenticationActive: accountFacts.authenticationActive,
                activeSessionID: accountFacts.authenticationSessionID)
            if V3AuthReconciliationPresentationPolicy.shouldPreserveActivePrompt(
                reportedState: reportedTerminalState, hasPrompt: prompt != nil,
                activeSessionMatches: session != nil && (expectedSession == nil || expectedSession == session),
                cancellationInProgress: isCancelling) && authenticationActiveForCurrentSession {
                // The exact SideSign prompt/session is the current authority
                // while credentials, 2FA, or team selection are in flight. A
                // separate account snapshot can observe authenticated=true
                // before provisioning activates its account row; it must not
                // replace an answerable prompt with a terminal UI state.
                signedIn = accountFacts.authenticated
                provisioningIncomplete = accountFacts.provisioningIncomplete
                if let snapshotTeam = snapshot["team"] as? String { team = snapshotTeam }
                return true
            }
            let readinessOwnerSessionID = expectedSession ?? session
            let retrySessionID = readinessOwnerSessionID
            let retryAttemptSequence = provisioningRetryReadinessAttemptSequence
            if V3AuthRetryReadinessReconciliationPolicy.shouldPublish(
                sessionID: retrySessionID, expectedSessionID: readinessOwnerSessionID,
                ownsRetry: retrySessionID.map { provisioningRetryReadinessOwnership.owns(sessionID: $0) } ?? false,
                authenticated: accountFacts.authenticated,
                authenticationActive: accountFacts.authenticationActive,
                provisioningIncomplete: accountFacts.provisioningIncomplete,
                attemptSequence: retryAttemptSequence),
               let retrySessionID, let retryAttemptSequence {
                // A lost terminal poll can still be confirmed by the
                // authoritative account snapshot. Keep the retry attempt
                // sequence so a later terminal delivery is deduped as the
                // same operation by the root observer.
                V3AuthReadinessRefreshEvent.post(sessionID: retrySessionID,
                    attemptSequence: retryAttemptSequence)
                reconciledProvisioningRetryReadinessSessionID = retrySessionID
                provisioningRetryReadinessOwnership.release(sessionID: retrySessionID)
            } else if accountFacts.authenticated, !accountFacts.authenticationActive,
               let pendingReadinessSessionID = pendingAuthenticationReadinessSessionID,
               (expectedSession ?? session) == pendingReadinessSessionID,
               let pendingAttemptSequence = pendingAuthenticationReadinessAttemptSequence {
                // The correlated poll terminal may have been lost. Once the
                // authoritative snapshot confirms authentication and no auth
                // owner remains, the app root can still refresh JIT-Less facts.
                V3AuthReadinessRefreshEvent.post(sessionID: pendingReadinessSessionID,
                    attemptSequence: pendingAttemptSequence)
                pendingAuthenticationReadinessSessionID = nil
                pendingAuthenticationReadinessAttemptSequence = nil
            }
            // A persisted account row can outlive an authenticated Apple
            // session. Only SideStore's explicit session fact proves that
            // authentication is currently active.
            let authoritative = accountFacts.authenticated
            let incomplete = accountFacts.provisioningIncomplete
            let canRetryProvisioning = accountFacts.provisioningRetryAvailable
            let authenticationActive = accountFacts.authenticationActive
            if retireInactiveAuthSession, !authenticationActiveForCurrentSession,
               let ownerSessionID {
                V3ServiceBridge.shared.reconcileAuthSessionOwnership(
                    sessionID: ownerSessionID, authenticationActive: false)
                if session == ownerSessionID { session = nil }
                cancellationConfirmed = true
                cancellationWasAttempted = false
            }
            let reconciliationState = V3AuthUnknownResultReconciliationPolicy.reportedState(
                originalState: reportedTerminalState, hasSession: session != nil,
                authenticated: accountFacts.authenticated)
            let resolvesUnknownAttempt = reportedTerminalState == "resultUnknown" && session == nil &&
                !accountFacts.authenticated
            if authoritative {
                // V3_FINISH_LATER_RECONCILES_AS_SIGNED_IN_V1: authoritative state
                // wins. A finished-later provisioning attempt still reconciles as
                // signed in, never back to "sign in again".
                signedIn = true
                if reportedTerminalState == "resultUnknown", session == nil {
                    // The original attempt remains unconfirmed, but no host-owned
                    // SideSign session remains. A separately admitted provisioning
                    // retry is safe when the authoritative snapshot permits it.
                    cancellationConfirmed = true
                    cancellationWasAttempted = false
                }
                let previousFailureMessage = reportedTerminalState == "failed"
                    ? previousFailure.map { Self.failureMessage(from: $0) } : nil
                let presentation = V3AuthReconciliationPresentationPolicy.resolve(
                    reportedState: reconciliationState, authenticated: true,
                    provisioningIncomplete: incomplete,
                    previousFailureMessage: previousFailureMessage,
                    authenticationActive: authenticationActiveForCurrentSession)
                state = presentation.state
                message = presentation.message
                team = snapshot["team"] as? String ?? ""
                if incomplete || authenticationActiveForCurrentSession {
                    provisioningIncomplete = true
                    // The service still reports an authenticated session whose
                    // provisioning never activated an account. Preserve any
                    // terminal attempt state while showing provisioning recovery
                    // as a separate account-state fact.
                    if provisioningMessage.isEmpty {
                        provisioningMessage = "Device provisioning did not complete. Retry provisioning, or finish later and come back."
                    }
                    // The account snapshot alone does not prove the process-local
                    // authenticated session needed to resume provisioning survived.
                    provisioningRetryAvailable = authenticationActive ? false : canRetryProvisioning
                    provisioningRetryBlockedByActiveSession = authenticationActive
                    provisioningSessionUnavailable = !canRetryProvisioning && !authenticationActive
                    if canRetryProvisioning && reportedTerminalState == "resultUnknown" {
                        provisioningMessage = "Apple ID is signed in, but provisioning is incomplete. The previous sign-in attempt remains unconfirmed; you can retry provisioning in a new session."
                    } else if authenticationActiveForCurrentSession {
                        provisioningMessage = "Another sign-in or provisioning attempt is still active. Wait for it to finish, then reload status before retrying provisioning."
                    } else if !canRetryProvisioning {
                        provisioningMessage = "Apple ID is signed in, but the saved provisioning session is unavailable. Finish Later, then reopen Account & Signing to sign in again before retrying provisioning."
                    }
                } else {
                    clearProvisioningOutcome()
                }
            } else {
                signedIn = false
                team = ""
                provisioningRetryBlockedByActiveSession = authenticationActive
                let inactiveSessionPresentation = V3AuthInactiveSessionResolutionPolicy.resolve(
                    reportedState: reportedTerminalState, authenticated: false,
                    authenticationActive: authenticationActiveForCurrentSession,
                    anotherSessionActive: anotherSessionActive)
                let signedOutPresentation = inactiveSessionPresentation ??
                    V3AuthReconciliationPresentationPolicy.resolve(
                        reportedState: reconciliationState, authenticated: false,
                        provisioningIncomplete: false)
                if let inactiveSessionPresentation {
                    state = inactiveSessionPresentation.state
                    message = inactiveSessionPresentation.message
                    prompt = nil
                    promptSubmitting = false
                    deliveryProgressMessage = ""
                    twoFactorTransientStep = nil
                } else if resolvesUnknownAttempt {
                    state = "failed"
                    cancellationConfirmed = true
                    cancellationWasAttempted = false
                    currentAttemptFailure.clear()
                    message = "SideStore confirmed that no account is signed in. You can start a new sign-in."
                } else if state == "idle" || state == "completed" || state == "authenticatedProvisioningIncomplete" {
                    state = "idle"
                    prompt = nil
                    clearProvisioningOutcome()
                }
                if !resolvesUnknownAttempt && !signedOutPresentation.message.isEmpty {
                    message = signedOutPresentation.message
                }
            }
            if anotherSessionActive {
                provisioningRetryBlockedByActiveSession = true
                if let presentation = V3AuthOtherSessionReconciliationPolicy.resolve(
                    reportedState: reportedTerminalState, authenticated: authoritative,
                    anotherSessionActive: true) {
                    state = presentation.state
                    message = presentation.message
                    if presentation.clearPrompt {
                        prompt = nil
                        promptSubmitting = false
                        deliveryProgressMessage = ""
                        twoFactorTransientStep = nil
                        cancellationConfirmed = true
                    }
                } else if state == "completed" {
                    message = "Apple ID is signed in. Another sign-in or provisioning session is active; wait for it to finish before starting another account action."
                }
            }
            return true
        } catch {
            guard reconciliationGate.mayApply(ticket, sessionID: session,
                state: state, revision: revision) else { return false }
            if state == "idle" { message = "Could not confirm the current SideStore account. Reload status and try again." }
            return false
        }
    }

    private func resolveUnavailableAuthSession(_ failure: CombinedFailure,
                                               expectedSessionID: String,
                                               snapshotConfirmed: Bool) {
        guard V3AuthSessionUnavailablePolicy.shouldRetireOwnership(
            sessionID: expectedSessionID, currentSessionID: session, failure: failure) else { return }
        V3ServiceBridge.shared.confirmAuthSessionUnavailable(sessionID: expectedSessionID)
        let anotherSessionActive = snapshotConfirmed && authoritativeActiveAuthenticationSessionID != nil &&
            authoritativeActiveAuthenticationSessionID != expectedSessionID
        let presentation = V3AuthSessionUnavailablePolicy.resolve(
            authenticated: signedIn, provisioningIncomplete: provisioningIncomplete,
            snapshotConfirmed: snapshotConfirmed, safeMessage: failure.safeMessage,
            recovery: failure.recovery, anotherSessionActive: anotherSessionActive)
        session = nil
        cancellationConfirmed = presentation.cancellationConfirmed
        cancellationWasAttempted = false
        prompt = nil
        promptSubmitting = false
        deliveryProgressMessage = ""
        twoFactorTransientStep = nil
        state = presentation.state
        message = presentation.message
        if anotherSessionActive { provisioningRetryBlockedByActiveSession = true }
        if let provisioningMessage = presentation.provisioningMessage {
            provisioningIncomplete = true
            self.provisioningMessage = provisioningMessage
            provisioningTechnical = failure.technicalDetails
            provisioningStage = failure.stage.rawValue
            provisioningCode = failure.code.rawValue
            provisioningCorrelation = failure.correlationID
            provisioningRetryAvailable = false
            provisioningSessionUnavailable = true
            provisioningFinishedLater = false
        } else {
            clearProvisioningOutcome()
            if presentation.state == "resultUnknown" {
                currentAttemptFailure.record(snapshotConfirmed: false,
                    authenticated: false, failureMessage: failure.safeMessage,
                    technicalDetails: failure.technicalDetails)
            } else if signedIn {
                currentAttemptFailure.clear()
            } else {
                currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                    authenticated: false, failureMessage: failure.safeMessage,
                    technicalDetails: failure.technicalDetails)
            }
        }
        task = nil
        NSLog("[V3_AUTH_UI] SESSION_UNAVAILABLE state=%@ signed_in=%d snapshot_confirmed=%d",
              presentation.state, signedIn ? 1 : 0, snapshotConfirmed ? 1 : 0)
    }

    private func run(sessionID requestedSession: String) async {
        let sessionDeadline = Date().addingTimeInterval(V3ServiceBridge.authSessionLifetime)
        do {
            state = "working"
            message = ""
            prompt = nil
            let reply = try await V3ServiceBridge.shared.request(operation: "authBegin",
                target: requestedSession,
                payload: ["session": requestedSession, "sessionDeadline": sessionDeadline])
            guard V3AuthSessionResponsePolicy.mayAcceptStartedSession(
                expectedSessionID: requestedSession, replySessionID: reply["session"] as? String,
                currentSessionID: session, cancellationInProgress: isCancelling) else {
                _ = try? await V3ServiceBridge.shared.request(operation: "authCancel", target: requestedSession)
                guard !Task.isCancelled, session == requestedSession else { return }
                await reconcile(force: true, expectedSession: requestedSession)
                return
            }
            guard let id = reply["session"] as? String else {
                throw NSError(domain: "V3Auth", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "The service did not start sign-in."])
            }
            session = id
            try await pollLoop(id: id, sessionDeadline: sessionDeadline)
        } catch {
            if isCancelling || Task.isCancelled { return }
            let pollFailure = (error as? V3AuthPollFailure).flatMap {
                $0.sessionID == requestedSession ? $0 : nil
            }
            let failureResponseGeneration = pollFailure?.promptResponseGeneration ?? promptResponseGeneration
            if let notDispatched = error as? CombinedFailure,
               V3AuthAttemptStartFailurePolicy.isConfirmedNotDispatched(notDispatched) {
                if pendingAuthenticationReadinessSessionID == requestedSession {
                    pendingAuthenticationReadinessSessionID = nil
                }
                let reconciliationGenerationBefore = reconciliationGate.generation
                let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession)
                if V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                    snapshotConfirmed: snapshotConfirmed, authenticated: signedIn, state: state) {
                    session = nil
                    cancellationConfirmed = true
                    cancellationWasAttempted = false
                    currentAttemptFailure.clear()
                    return
                }
                guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                    requestedSessionID: requestedSession, currentSessionID: session,
                    capturedPromptResponseGeneration: failureResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    reconciliationGenerationBefore: reconciliationGenerationBefore,
                    currentReconciliationGeneration: reconciliationGate.generation,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else { return }
                state = "failed"
                cancellationConfirmed = true
                session = nil
                message = notDispatched.safeMessage + " " + notDispatched.recovery
                currentAttemptFailure.clear()
                return
            }
            let underlyingError = pollFailure?.underlying ?? error
            let failureMessage = V3FailureGuidance.message(underlyingError)
            let failureTechnical = (underlyingError as? CombinedFailure)?.technicalDetails ?? ""
            // A thrown start/poll request does not prove the authentication
            // attempt reached a terminal result. Reconcile account state for
            // display, but keep the attempt outcome unknown until its session
            // is cancelled or a correlated terminal reply arrives.
            let reconciliationGenerationBefore = reconciliationGate.generation
            let sessionUnavailable = ((pollFailure?.underlying ?? error) as? CombinedFailure)?.safeCause == .authSessionUnavailable
            let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession,
                retireInactiveAuthSession: !sessionUnavailable)
            if let sessionFailure = (pollFailure?.underlying ?? error) as? CombinedFailure,
               sessionFailure.safeCause == .authSessionUnavailable {
                resolveUnavailableAuthSession(sessionFailure, expectedSessionID: requestedSession,
                    snapshotConfirmed: snapshotConfirmed)
                return
            }
            if let pollFailure,
               restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                    sessionDeadline: sessionDeadline,
                    failedPromptRevision: pollFailure.promptRevision,
                    failedPromptResponseGeneration: pollFailure.promptResponseGeneration,
                    pollFailureIsTransient: (pollFailure.underlying as? CombinedFailure)
                        .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                    provisioningRetry: false,
                    reconciliationWasSuperseded: reconciliationGate.generation !=
                        (reconciliationGenerationBefore &+ 1)) {
                return
            }
            if pollFailure == nil,
               V3AuthPollMonitorRecoveryPolicy.shouldResumeAfterAmbiguousStart(
                    requestedSessionID: requestedSession, currentSessionID: session,
                    activeSessionID: authoritativeActiveAuthenticationSessionID,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled),
               restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                    sessionDeadline: sessionDeadline, failedPromptRevision: revision,
                    failedPromptResponseGeneration: promptResponseGeneration,
                    pollFailureIsTransient: false, provisioningRetry: false,
                    reconciliationWasSuperseded: false) {
                return
            }
            if V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                snapshotConfirmed: snapshotConfirmed, authenticated: signedIn, state: state) {
                return
            }
            if V3AuthAttemptFailureCommitPolicy.shouldCommitConfirmedSignedOutFailure(
                snapshotConfirmed: snapshotConfirmed, authenticated: signedIn,
                hasSession: session != nil, cancellationConfirmed: cancellationConfirmed,
                state: state) {
                currentAttemptFailure.record(snapshotConfirmed: true, authenticated: false,
                    failureMessage: failureMessage, technicalDetails: failureTechnical)
                return
            }
            guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                requestedSessionID: requestedSession, currentSessionID: session,
                capturedPromptResponseGeneration: failureResponseGeneration,
                currentPromptResponseGeneration: promptResponseGeneration,
                reconciliationGenerationBefore: reconciliationGenerationBefore,
                currentReconciliationGeneration: reconciliationGate.generation,
                cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else {
                if let pollFailure {
                    _ = restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                        sessionDeadline: sessionDeadline,
                        failedPromptRevision: pollFailure.promptRevision,
                        failedPromptResponseGeneration: pollFailure.promptResponseGeneration,
                        pollFailureIsTransient: (pollFailure.underlying as? CombinedFailure)
                            .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                        provisioningRetry: false,
                        reconciliationWasSuperseded: reconciliationGate.generation !=
                            (reconciliationGenerationBefore &+ 1))
                }
                return
            }
            state = "resultUnknown"
            prompt = nil
            promptSubmitting = false
            deliveryProgressMessage = ""
            twoFactorTransientStep = nil
            cancellationConfirmed = false
            message = snapshotConfirmed
                ? "SideStore confirmed account status but could not confirm whether this sign-in attempt finished. Cancel the unconfirmed session before starting another attempt."
                : "SideStore could not confirm the sign-in result. Cancel the unconfirmed session before starting another attempt."
            currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                authenticated: signedIn, failureMessage: failureMessage,
                technicalDetails: failureTechnical)
        }
    }

    private func pollLoop(id: String, sessionDeadline: Date) async throws {
        var pollFailureCount = 0
        while !Task.isCancelled {
            try await Task.sleep(nanoseconds: 1_000_000_000)
            try Task.checkCancellation()
            guard !isCancelling, session == id else { throw CancellationError() }
            if ["completed", "failed", "timedOut", "promptExpired", "cancelled"].contains(state) ||
               (state == "authenticatedProvisioningIncomplete" && !provisioningRetryBlockedByActiveSession) {
                return
            }
            guard Date() < sessionDeadline else {
                state = "timedOut"
                message = "Sign-in timed out. Checking the current SideStore account..."
                prompt = nil
                await reconcile(force: true, expectedSession: id)
                return
            }
            let pollRevision = revision
            let pollPromptResponseGeneration = promptResponseGeneration
            let reply: [String: Any]
            do {
                reply = try await V3ServiceBridge.shared.request(operation: "authPoll", target: id,
                    requestDeadline: sessionDeadline)
                if message == "Connection to SideStore was interrupted. Waiting for the current sign-in result..." {
                    message = ""
                }
                pollFailureCount = 0
            } catch let failure as CombinedFailure
                where V3AuthPollRecoveryPolicy.shouldRetry(failure, sessionDeadline: sessionDeadline) {
                if V3AuthPollFailureRacePolicy.shouldIgnore(
                    requestedSessionID: id, currentSessionID: session,
                    requestedRevision: pollRevision, currentRevision: revision,
                    requestedPromptResponseGeneration: pollPromptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    promptSubmissionInProgress: promptSubmitting) {
                    continue
                }
                pollFailureCount += 1
                message = "Connection to SideStore was interrupted. Waiting for the current sign-in result..."
                let delay = V3AuthPollRecoveryPolicy.retryDelay(attempt: pollFailureCount - 1,
                    remaining: sessionDeadline.timeIntervalSinceNow)
                guard delay > 0 else {
                    state = "timedOut"
                    message = "Sign-in timed out. Checking the current SideStore account..."
                    prompt = nil
                    await reconcile(force: true, expectedSession: id)
                    return
                }
                try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                continue
            } catch let failure as CombinedFailure
                where V3AuthPollRecoveryPolicy.shouldFinishTimedOut(
                    failure, sessionDeadline: sessionDeadline) {
                if V3AuthPollFailureRacePolicy.shouldIgnore(
                    requestedSessionID: id, currentSessionID: session,
                    requestedRevision: pollRevision, currentRevision: revision,
                    requestedPromptResponseGeneration: pollPromptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    promptSubmissionInProgress: promptSubmitting) {
                    continue
                }
                state = "timedOut"
                message = "Sign-in timed out. Checking the current SideStore account..."
                prompt = nil
                await reconcile(force: true, expectedSession: id)
                return
            } catch {
                if V3AuthPollFailureRacePolicy.shouldIgnore(
                    requestedSessionID: id, currentSessionID: session,
                    requestedRevision: pollRevision, currentRevision: revision,
                    requestedPromptResponseGeneration: pollPromptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    promptSubmissionInProgress: promptSubmitting) {
                    continue
                }
                if error is CancellationError { throw error }
                throw V3AuthPollFailure(underlying: error, sessionID: id,
                    promptResponseGeneration: pollPromptResponseGeneration,
                    promptRevision: pollRevision)
            }
            guard V3AuthPollResponsePolicy.mayApply(
                currentSessionID: session, replySessionID: reply["session"] as? String ?? "",
                cancellationInProgress: isCancelling,
                currentRevision: revision,
                replyRevision: V3ServiceBridge.strictInt(reply["revision"]),
                currentPromptID: prompt?["id"] as? String,
                replyPromptID: (reply["prompt"] as? [String: Any])?["id"] as? String) else {
                continue
            }
            guard !Task.isCancelled, !isCancelling, session == id else { throw CancellationError() }
            apply(reply)
            guard let current = reply["state"] as? String else { return }
            if V3AuthTimeoutReconciliationPolicy.shouldReconcileAfterTerminal(current) {
                await reconcile(force: true, expectedSession: id)
                return
            }
            if Date() >= sessionDeadline && (current == "working" || current == "awaitingPrompt") {
                state = "timedOut"
                message = "Sign-in timed out. Checking the current SideStore account..."
                prompt = nil
                await reconcile(force: true, expectedSession: id)
                return
            }
            guard current == "working" || current == "awaitingPrompt" else { return }
        }
    }

    func reloadAuthoritativeAccountStatus() {
        guard !isCancelling else { return }
        Task { @MainActor in
            let confirmed = await reconcile(force: true)
            if !confirmed && state == "resultUnknown" {
                cancellationConfirmed = false
                message = "SideStore could not confirm the account state. Reload status again before starting a new sign-in."
            }
        }
    }

    // A poll can fail while an answer is being submitted. The old monitor then
    // unwinds, and its catch awaits an account snapshot. If that answer wins
    // during reconciliation, the failure is stale and must hand ownership to a
    // replacement monitor for the same bounded session.
    private func restartPollMonitorAfterSupersededFailure(sessionID: String,
        sessionDeadline: Date, failedPromptRevision: Int,
        failedPromptResponseGeneration: UInt64,
        pollFailureIsTransient: Bool,
        provisioningRetry: Bool,
        reconciliationWasSuperseded: Bool) -> Bool {
        guard V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: sessionID, currentSessionID: session,
            failedPromptRevision: failedPromptRevision, currentPromptRevision: revision,
            failedPromptResponseGeneration: failedPromptResponseGeneration,
            currentPromptResponseGeneration: promptResponseGeneration, state: state,
            promptSubmissionInProgress: promptSubmitting,
            activeSessionID: authoritativeActiveAuthenticationSessionID,
            pollFailureIsTransient: pollFailureIsTransient, cancellationInProgress: isCancelling,
            taskCancelled: Task.isCancelled,
            reconciliationWasSuperseded: reconciliationWasSuperseded,
            sessionDeadline: sessionDeadline) else { return false }
        if provisioningRetry &&
           !provisioningRetryReadinessOwnership.handoffAfterSupersededPollFailure(sessionID: sessionID) {
            return false
        }
        task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.continuePollingAfterSupersededFailure(sessionID: sessionID,
                sessionDeadline: sessionDeadline, provisioningRetry: provisioningRetry)
        }
        return true
    }

    private func continuePollingAfterSupersededFailure(sessionID: String,
        sessionDeadline: Date, provisioningRetry: Bool) async {
        var monitorFailureCount = 0
        while !Task.isCancelled, !isCancelling, session == sessionID,
              (!provisioningRetry || provisioningRetryReadinessOwnership.owns(sessionID: sessionID)) {
            do {
                try await pollLoop(id: sessionID, sessionDeadline: sessionDeadline)
                return
            } catch let failure as V3AuthPollFailure where failure.sessionID == sessionID {
                let reconciliationGenerationBefore = reconciliationGate.generation
                let sessionUnavailable = (failure.underlying as? CombinedFailure)?.safeCause == .authSessionUnavailable
                let snapshotConfirmed = await reconcile(force: true, expectedSession: sessionID,
                    retireInactiveAuthSession: !sessionUnavailable)
                if let combined = failure.underlying as? CombinedFailure,
                   combined.safeCause == .authSessionUnavailable {
                    resolveUnavailableAuthSession(combined, expectedSessionID: sessionID,
                        snapshotConfirmed: snapshotConfirmed)
                    return
                }
                if V3AuthPollMonitorRecoveryPolicy.shouldResume(
                    requestedSessionID: sessionID, currentSessionID: session,
                    failedPromptRevision: failure.promptRevision, currentPromptRevision: revision,
                    failedPromptResponseGeneration: failure.promptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration, state: state,
                    promptSubmissionInProgress: promptSubmitting,
                    activeSessionID: authoritativeActiveAuthenticationSessionID,
                    pollFailureIsTransient: (failure.underlying as? CombinedFailure)
                        .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                    cancellationInProgress: isCancelling,
                    taskCancelled: Task.isCancelled,
                    reconciliationWasSuperseded: reconciliationGate.generation !=
                        (reconciliationGenerationBefore &+ 1),
                    sessionDeadline: sessionDeadline) {
                    if let combined = failure.underlying as? CombinedFailure,
                       V3AuthPollRecoveryPolicy.isTransientTransportFailure(combined),
                       prompt != nil &&
                       failure.promptResponseGeneration == promptResponseGeneration &&
                       failure.promptRevision == revision && !promptSubmitting {
                        message = "Connection to SideStore was interrupted while checking this verification request. The current response is still available."
                    }
                    monitorFailureCount += 1
                    let delay = V3AuthPollRecoveryPolicy.retryDelay(attempt: monitorFailureCount - 1,
                        remaining: sessionDeadline.timeIntervalSinceNow)
                    if delay > 0 {
                        try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    }
                    if Task.isCancelled { return }
                    continue
                }
                if V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                    snapshotConfirmed: snapshotConfirmed, authenticated: signedIn, state: state) {
                    return
                }
                let failedMessage = V3FailureGuidance.message(failure.underlying)
                let failedTechnical = (failure.underlying as? CombinedFailure)?.technicalDetails ?? ""
                if V3AuthAttemptFailureCommitPolicy.shouldCommitConfirmedSignedOutFailure(
                    snapshotConfirmed: snapshotConfirmed, authenticated: signedIn,
                    hasSession: session != nil, cancellationConfirmed: cancellationConfirmed,
                    state: state) {
                    currentAttemptFailure.record(snapshotConfirmed: true, authenticated: false,
                        failureMessage: failedMessage, technicalDetails: failedTechnical)
                    return
                }
                guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                    requestedSessionID: sessionID, currentSessionID: session,
                    capturedPromptResponseGeneration: failure.promptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    reconciliationGenerationBefore: reconciliationGenerationBefore,
                    currentReconciliationGeneration: reconciliationGate.generation,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else { return }
                let underlying = failure.underlying
                if provisioningRetry, signedIn, state != "completed", prompt != nil {
                    state = "resultUnknown"
                    prompt = nil
                    promptSubmitting = false
                    deliveryProgressMessage = ""
                    twoFactorTransientStep = nil
                    cancellationConfirmed = false
                    message = "Apple ID is signed in, but SideStore could not confirm the current verification or provisioning response. Cancel the unconfirmed session before starting another attempt."
                    provisioningMessage = V3FailureGuidance.message(underlying)
                    provisioningTechnical = (underlying as? CombinedFailure)?.technicalDetails ?? ""
                    provisioningIncomplete = true
                    provisioningRetryAvailable = false
                    provisioningSessionUnavailable = false
                    currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                        authenticated: signedIn, failureMessage: V3FailureGuidance.message(underlying),
                        technicalDetails: provisioningTechnical)
                    return
                }
                if provisioningRetry, signedIn, state != "completed" {
                    state = "authenticatedProvisioningIncomplete"
                    provisioningIncomplete = true
                    message = "Apple ID signed in successfully."
                    provisioningMessage = "SideStore could not confirm the provisioning result. " +
                        V3FailureGuidance.message(underlying)
                    provisioningTechnical = (underlying as? CombinedFailure)?.technicalDetails ?? ""
                    provisioningSessionUnavailable = false
                    return
                }
                state = "resultUnknown"
                prompt = nil
                promptSubmitting = false
                deliveryProgressMessage = ""
                twoFactorTransientStep = nil
                cancellationConfirmed = false
                message = snapshotConfirmed
                    ? "SideStore confirmed account status but could not confirm whether this sign-in attempt finished. Cancel the unconfirmed session before starting another attempt."
                    : "SideStore could not confirm the sign-in result. Cancel the unconfirmed session before starting another attempt."
                currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                    authenticated: signedIn, failureMessage: V3FailureGuidance.message(underlying),
                    technicalDetails: (underlying as? CombinedFailure)?.technicalDetails ?? "")
                return
            } catch {
                return
            }
        }
    }

    private func apply(_ reply: [String: Any]) {
        let oldPromptID = prompt?["id"] as? String
        let replyState = reply["state"] as? String ?? state
        let retryReadinessSettlement = provisioningRetryReadinessOwnership.settle(
            currentSessionID: session, replySessionID: reply["session"] as? String,
            replyState: replyState,
            authenticated: V3ServiceBridge.strictBool(reply["authenticated"]) == true,
            cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled)
        let terminalAuthenticationSucceeded =
            ["completed", "authenticatedProvisioningIncomplete"].contains(replyState) &&
            V3ServiceBridge.strictBool(reply["authenticated"]) == true
        let replySessionID = reply["session"] as? String
        let replyBelongsToCurrentSession = replySessionID != nil && replySessionID == session
        let isTerminal = ["completed", "authenticatedProvisioningIncomplete", "failed",
                          "timedOut", "promptExpired", "cancelled"].contains(replyState)
        var readinessAttemptSequence: UInt64?
        let readinessEventSessionID = replySessionID
        let retrySequenceForReply = provisioningRetryReadinessAttemptSequence
        if retryReadinessSettlement == .committed {
            readinessAttemptSequence = retrySequenceForReply
            reconciledProvisioningRetryReadinessSessionID = replySessionID
        } else if retryReadinessSettlement == .finishedWithoutCommit {
            provisioningRetryReadinessAttemptSequence = nil
            reconciledProvisioningRetryReadinessSessionID = nil
        } else if retryReadinessSettlement == .notRetry && terminalAuthenticationSucceeded {
            if reconciledProvisioningRetryReadinessSessionID == replySessionID {
                readinessAttemptSequence = provisioningRetryReadinessAttemptSequence
            } else if pendingAuthenticationReadinessSessionID == replySessionID {
                readinessAttemptSequence = pendingAuthenticationReadinessAttemptSequence
            }
        }
        if isTerminal {
            if pendingAuthenticationReadinessSessionID == replySessionID {
                pendingAuthenticationReadinessSessionID = nil
                pendingAuthenticationReadinessAttemptSequence = nil
            }
        }
        let belongsToCurrentOrReconciledRetry = replyBelongsToCurrentSession ||
            (reconciledProvisioningRetryReadinessSessionID == replySessionID &&
             provisioningRetryReadinessAttemptSequence != nil)
        if belongsToCurrentOrReconciledRetry, terminalAuthenticationSucceeded,
           (retryReadinessSettlement == .committed || retryReadinessSettlement == .notRetry) {
            V3AuthReadinessRefreshEvent.post(sessionID: readinessEventSessionID,
                attemptSequence: readinessAttemptSequence)
        }
        state = replyState
        if ["completed", "authenticatedProvisioningIncomplete", "failed", "timedOut", "promptExpired", "cancelled"].contains(state) {
            currentAttemptFailure.clear()
            provisioningRetryBlockedByActiveSession = false
        }
        attempts = V3ServiceBridge.strictInt(reply["attempts"]) ?? attempts
        revision = V3ServiceBridge.strictInt(reply["revision"]) ?? revision
        prompt = reply["prompt"] as? [String: Any]
        previousFailure = V3AuthPromptFailurePolicy.applying(reply: reply, current: previousFailure)
        if V3ServiceBridge.strictBool(reply["authenticated"]) == true { signedIn = true }
        if oldPromptID != (prompt?["id"] as? String) {
            promptSubmitting = false
            promptResponseDiagnostics = ""
            promptResponseBlocked = false
            if state == "awaitingPrompt" {
                if V3AuthPromptResponsePolicy.shouldClearSubmissionFailure(
                    oldPromptID: oldPromptID, newPromptID: prompt?["id"] as? String,
                    state: state) { message = "" }
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
            provisioningIncomplete = true
            team = reply["team"] as? String ?? team
            message = "Apple ID signed in successfully."
            prompt = nil
            deliveryProgressMessage = ""
            if let failureKind = reply["failureKind"] as? String {
                provisioningMessage = V3AuthStore.failureMessage(from: ["kind": failureKind])
            } else {
                provisioningMessage = reply["message"] as? String ?? "Provisioning could not be completed."
            }
            provisioningStage = reply["stage"] as? String ?? ""
            provisioningCode = reply["code"] as? String ?? ""
            provisioningTechnical = reply["technicalDetails"] as? String ?? ""
            provisioningCorrelation = (reply["failure"] as? [String: Any])?["correlationID"] as? String ?? ""
            provisioningRetryAvailable = V3ServiceBridge.strictBool(reply["resumable"]) ?? false
            provisioningSessionUnavailable = false
            if !provisioningRetryAvailable {
                provisioningMessage += " The saved provisioning session is not ready to retry yet. Finish Later, then reopen Account & Signing to reload status."
            }
            provisioningFinishedLater = false
        } else if state == "failed" {
            message = reply["message"] as? String ?? "The sign-in request failed for an unknown reason."
            if let failure = reply["failure"] as? [String: Any] { previousFailure = failure }
            prompt = nil
            deliveryProgressMessage = ""
            clearProvisioningOutcome()
        } else if state == "timedOut" {
            message = reply["message"] as? String ?? "Sign-in timed out. Start a new sign-in when you are ready."
            prompt = nil
            deliveryProgressMessage = ""
            twoFactorTransientStep = nil
        } else if state == "promptExpired" {
            message = reply["message"] as? String
                ?? "That verification session expired. Start a new sign-in to request another verification code."
            task?.cancel()
            prompt = nil
            promptSubmitting = false
            deliveryProgressMessage = ""
            twoFactorTransientStep = nil
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
        V3AuthFailureDiagnosticsPolicy.render(failure,
            underlyingCode: V3ServiceBridge.strictInt(failure["underlyingCode"]),
            retryableValue: V3ServiceBridge.strictBool(failure["retryable"]))
    }

    func answer(promptID: String, answer: [String: String]) {
        guard !promptID.isEmpty, let session,
              V3AuthPromptResponsePolicy.maySubmit(state: state,
                currentPromptID: prompt?["id"] as? String, submittedPromptID: promptID,
                isSubmitting: promptSubmitting, cancellationInProgress: isCancelling),
              provisioningRetryReadinessOwnership.allowsPromptResponse(sessionID: session) else { return }
        if promptResponseBlocked && !["cancel", "changeMethod"].contains(answer["action"] ?? "") { return }
        promptResponseDiagnostics = ""
        promptResponseBlocked = false
        promptResponseGeneration &+= 1
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
                let secretToken = try V3SecretHandoff.storeStringDictionary(answer)
                defer { V3SecretHandoff.discard(secretToken) }
                let reply = try await V3ServiceBridge.shared.request(operation: "authRespond", target: session,
                    payload: ["prompt": promptID, "secretToken": secretToken])
                if V3ServiceBridge.strictBool(reply["responsePending"]) == true {
                    let replyRevision = V3ServiceBridge.strictInt(reply["revision"])
                    let replyPromptID = (reply["prompt"] as? [String: Any])?["id"] as? String
                    if replyPromptID != promptID {
                        guard V3AuthSessionResponsePolicy.mayApplyReply(
                            currentSessionID: self.session,
                            replySessionID: reply["session"] as? String ?? "",
                            cancellationInProgress: self.isCancelling,
                            currentRevision: self.revision,
                            replyRevision: replyRevision) else { return }
                        self.promptResponseGeneration &+= 1
                        apply(reply)
                        return
                    }
                    guard V3AuthSessionResponsePolicy.mayApplyReply(
                        currentSessionID: self.session,
                        replySessionID: reply["session"] as? String ?? "",
                        cancellationInProgress: self.isCancelling,
                        submittedPromptID: promptID,
                        currentPromptID: self.prompt?["id"] as? String,
                        currentRevision: self.revision,
                        replyRevision: replyRevision) else { return }
                    self.promptResponseGeneration &+= 1
                    self.revision = replyRevision ?? self.revision
                    self.promptSubmitting = true
                    self.deliveryProgressMessage = "Your response is being processed..."
                    return
                }
                if V3ServiceBridge.strictBool(reply["promptExpired"]) == true ||
                    reply["state"] as? String == "promptExpired" {
                    guard self.session == session, !self.isCancelling,
                          self.prompt?["id"] as? String == promptID else { return }
                    if let replyRevision = V3ServiceBridge.strictInt(reply["revision"]), replyRevision >= revision {
                        revision = replyRevision
                    }
                    state = "promptExpired"
                    task?.cancel()
                    promptResponseGeneration &+= 1
                    promptSubmitting = false
                    prompt = nil
                    deliveryProgressMessage = ""
                    twoFactorTransientStep = nil
                    message = "That verification session expired. Start a new sign-in to request another verification code."
                    await reconcile(force: true, expectedSession: session)
                    return
                }
                guard V3AuthSessionResponsePolicy.mayApplyReply(
                    currentSessionID: self.session,
                    replySessionID: reply["session"] as? String ?? "",
                    cancellationInProgress: self.isCancelling,
                    submittedPromptID: promptID,
                    currentPromptID: self.prompt?["id"] as? String,
                    currentRevision: self.revision,
                    replyRevision: V3ServiceBridge.strictInt(reply["revision"])) else { return }
                promptResponseGeneration &+= 1
                apply(reply)
            } catch {
                guard V3AuthPromptSubmissionPolicy.mayShowFailure(
                    currentSessionID: self.session, submittedSessionID: session,
                    currentPromptID: self.prompt?["id"] as? String,
                    submittedPromptID: promptID, cancellationInProgress: self.isCancelling) else { return }
                promptResponseGeneration &+= 1
                promptSubmitting = false
                message = V3AuthPromptResponsePolicy.failureMessage(error)
                promptResponseDiagnostics = V3AuthPromptResponsePolicy.diagnostics(error)
                promptResponseBlocked = V3AuthPromptResponsePolicy.blocksResubmission(error)
            }
        }
    }

    func clearPreviousFailure() {
        previousFailure = V3AuthPromptFailurePolicy.clearingOnDismiss(previousFailure)
    }

    func cancel() {
        guard !(state == "resultUnknown" && session == nil) else {
            reloadAuthoritativeAccountStatus()
            return
        }
        let hasActiveAttempt = ["working", "awaitingPrompt", "promptExpired", "resultUnknown"].contains(state)
        let canRetryCancellation = V3AuthCancellationRetryPolicy.canRetry(
            isCancelling: isCancelling, cancellationConfirmed: cancellationConfirmed,
            hasSession: session != nil)
        guard !isCancelling, hasActiveAttempt || canRetryCancellation else { return }
        reconciliationGate.invalidate()
        cancellationWasAttempted = true
        isCancelling = true
        cancellationConfirmed = false
        if let cancellationMessage = V3AuthCancellationFeedbackPolicy.message(isCancelling: true) {
            message = cancellationMessage
        }
        let oldTask = task
        let oldSession = session
        task?.cancel()
        Task { @MainActor in
            do {
                var terminalReply: [String: Any]?
                if let oldSession {
                    terminalReply = try await V3ServiceBridge.shared.request(operation: "authCancel", target: oldSession)
                }
                await oldTask?.value
                if let terminalReply { apply(terminalReply) }
                await reconcile(force: true)
                cancellationConfirmed = true
                cancellationWasAttempted = false
                if !signedIn, terminalReply == nil {
                    state = "cancelled"
                    message = "Sign-in was cancelled before an authentication session was confirmed."
                }
            } catch {
                await oldTask?.value
                await reconcile(force: true)
                state = "resultUnknown"
                message = signedIn
                    ? "SideStore currently reports an account as signed in, but could not confirm that the sign-in request stopped. Retry Cancellation before starting another attempt."
                    : "SideStore could not confirm that the sign-in request stopped. Retry Cancellation before starting another attempt."
            }
            session = cancellationConfirmed ? nil : oldSession
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
                        Label(V3AuthStatusTextPolicy.accountLabel(state: auth.state, isSignedIn: auth.isSignedIn),
                              systemImage: "checkmark.circle.fill")
                            .foregroundColor(.green)
                        Spacer()
                        if !auth.team.isEmpty { Text(auth.team).foregroundColor(.secondary) }
                    }
                    if let jitless = jitlessGuidance {
                        VStack(alignment: .leading, spacing: 8) {
                            Label(jitless.presentation.title, systemImage: jitless.presentation.icon)
                                .font(.subheadline.weight(.semibold))
                                .foregroundColor(jitless.presentation.tint)
                            Text(jitless.presentation.detail)
                                .font(.footnote).foregroundColor(.secondary)
                            if jitless.presentation.isOutstandingSetupTask {
                                switch jitless.action {
                                case .setUp:
                                    Button("Continue to JIT-Less Setup") { openJITLessSetup() }
                                        .buttonStyle(.borderedProminent)
                                case .refreshCertificate:
                                    Button("Refresh JIT-Less Certificate") { openJITLessSetup() }
                                        .buttonStyle(.borderedProminent)
                                case .openCertificates:
                                    NavigationLink {
                                        V3CertificatesView().environmentObject(status)
                                    } label: {
                                        Label("Open Certificates", systemImage: "doc.text")
                                    }
                                case .openSetup:
                                    Button("Open JIT-Less Setup") { openJITLessSetup() }
                                        .buttonStyle(.borderedProminent)
                                case .none:
                                    EmptyView()
                                }
                            } else if jitless.readiness == .ready {
                                NavigationLink {
                                    V3HealthView().environmentObject(status).environmentObject(sharedModel)
                                } label: {
                                    Label("Review JIT-Less Status", systemImage: "stethoscope")
                                }
                                .font(.caption)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
                if !auth.message.isEmpty {
                    Text(auth.message)
                        .font(.footnote)
                        .foregroundColor(auth.state == "resultUnknown" || auth.state == "timedOut" ||
                            auth.state == "cancelled" ? .orange : (auth.isSignedIn ? .green : .red))
                        .textSelection(.enabled)
                }
                if V3AuthFailureDiagnosticsPolicy.shouldShowTerminalDetails(
                    state: auth.state, hasPrompt: auth.prompt != nil,
                    hasFailure: auth.previousFailure != nil),
                   let failure = auth.previousFailure {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Sign-in diagnostics")
                            .font(.subheadline.weight(.semibold))
                        DisclosureGroup("Technical details") {
                            Text(V3AuthStore.failureDetails(from: failure))
                                .font(.caption2)
                                .textSelection(.enabled)
                        }
                        Button("Copy Diagnostics", systemImage: "doc.on.doc") {
                            UIPasteboard.general.string = V3AuthStore.failureDetails(from: failure)
                        }
                        .font(.caption)
                    }
                    .padding(.vertical, 4)
                }
                if !auth.currentAttemptFailure.message.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Sign-in attempt could not be confirmed")
                            .font(.subheadline.weight(.semibold))
                            .foregroundColor(.orange)
                        Text(auth.currentAttemptFailure.message)
                            .font(.footnote)
                            .foregroundColor(.orange)
                            .textSelection(.enabled)
                        if !auth.currentAttemptFailure.technicalDetails.isEmpty {
                            DisclosureGroup("Technical details") {
                                Text(auth.currentAttemptFailure.technicalDetails)
                                    .font(.caption2)
                                    .textSelection(.enabled)
                            }
                            Button("Copy Diagnostics") {
                                UIPasteboard.general.string = auth.currentAttemptFailure.technicalDetails
                            }
                            .font(.caption)
                        }
                    }
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
                if auth.state == "idle" {
                    Button { auth.begin() } label: {
                        Label("Begin Sign In", systemImage: "person.badge.key.fill")
                    }
                    .disabled(!auth.canBegin)
                } else if auth.state == "failed" || auth.state == "cancelled" ||
                    auth.state == "timedOut" || auth.state == "promptExpired" {
                    switch auth.terminalFailureAction {
                    case .beginNewSignIn(let title):
                        Button { auth.begin() } label: {
                            Label(title, systemImage: "person.badge.key.fill")
                        }
                        .disabled(!auth.canBegin)
                        if let guidance = auth.terminalFailureGuidance {
                            Text(guidance).font(.caption).foregroundColor(.secondary)
                        }
                    case .repairAppleAccount:
                        VStack(alignment: .leading, spacing: 8) {
                            Text(auth.terminalFailureGuidance ?? "Resolve the account issue shown by Apple before signing in again.")
                                .font(.footnote).foregroundColor(.orange)
                            Link("Open Apple Account", destination: URL(string: "https://account.apple.com")!)
                            Button("Begin Sign-In After Repair") { auth.begin() }
                                .disabled(!auth.canBegin)
                        }
                    case .useAppSpecificPassword:
                        VStack(alignment: .leading, spacing: 8) {
                            Text(auth.terminalFailureGuidance ?? "Apple requires an app-specific password for this authentication path.")
                                .font(.footnote).foregroundColor(.orange)
                            Link("Open Apple Account", destination: URL(string: "https://account.apple.com")!)
                            Button("Use App-Specific Password") { auth.begin() }
                                .disabled(!auth.canBegin)
                        }
                    case .blocked:
                        Text(auth.terminalFailureGuidance ?? "This failure is not marked safe to retry. Review Diagnostics before another attempt.")
                            .font(.footnote).foregroundColor(.orange)
                    }
                }
                if auth.state != "resultUnknown" && V3AuthCancellationRetryPolicy.canRetry(isCancelling: auth.isCancelling,
                    cancellationConfirmed: auth.cancellationConfirmed,
                    hasSession: auth.hasSession) {
                    Button(auth.cancellationWasAttempted ? "Retry Cancellation" : "Cancel Unconfirmed Sign-In",
                           role: .cancel) { auth.cancel() }
                }
                if auth.state == "resultUnknown" {
                    switch V3AuthUnknownResultRecoveryPolicy.action(
                        isCancelling: auth.isCancelling,
                        cancellationConfirmed: auth.cancellationConfirmed,
                        hasSession: auth.hasSession) {
                    case .cancelSession:
                        Button(auth.cancellationWasAttempted ? "Retry Cancellation" : "Cancel Unconfirmed Sign-In",
                               role: .cancel) { auth.cancel() }
                            .disabled(auth.isCancelling)
                    case .reloadStatus:
                        Button(auth.isCancelling ? "Checking..." : "Reload Status") {
                            auth.reloadAuthoritativeAccountStatus()
                        }
                        .disabled(auth.isCancelling)
                    case .none:
                        EmptyView()
                    }
                }
                if !V3AuthCancellationRetryPolicy.canRetry(isCancelling: auth.isCancelling,
                    cancellationConfirmed: auth.cancellationConfirmed,
                    hasSession: auth.hasSession) &&
                    (auth.state == "working" || auth.state == "awaitingPrompt" ||
                     auth.state == "promptExpired") {
                    Button(auth.isCancelling ? "Cancelling..." : "Cancel Sign In",
                        role: .cancel) { auth.cancel() }
                        .disabled(auth.isCancelling)
                }
                // V3_PROVISIONING_RECOVERY_ACTIONS_V1: the actions describe the
                // provisioning state, not a failed sign-in. "Retry" re-enters
                // provisioning with the saved session; "Finish Later" keeps the
                // authenticated account and closes this flow.
                if auth.hasProvisioningProblem {
                    let recovery = V3AuthProvisioningRecoveryPolicy.resolve(
                        state: auth.state, hasSession: auth.hasSession, signedIn: auth.isSignedIn,
                        provisioningRetryAvailable: auth.provisioningRetryAvailable,
                        isCancelling: auth.isCancelling,
                        cancellationConfirmed: auth.cancellationConfirmed,
                        authenticationActive: auth.provisioningRetryBlockedByActiveSession)
                    if recovery.showCancellationInstruction {
                        Text("Cancel the unconfirmed sign-in before retrying provisioning.")
                            .font(.caption).foregroundColor(.secondary)
                    }
                    if recovery.showRetryProvisioning || auth.state != "resultUnknown" {
                        Button {
                            auth.retryProvisioning()
                        } label: {
                            Label("Retry Provisioning", systemImage: "arrow.clockwise")
                        }
                        .disabled(!auth.canRetryProvisioning)
                    }
                    if auth.provisioningSessionUnavailable {
                        Text("Finish Later, then reopen Account & Signing to sign in again before retrying provisioning.")
                            .font(.caption).foregroundColor(.secondary)
                    }
                    if recovery.blockedByActiveSession {
                        Text("Another sign-in or provisioning attempt is still active. Wait for it to finish, then reload status.")
                            .font(.caption).foregroundColor(.secondary)
                    }
                    if recovery.showFinishLater {
                        Button("Finish Later") { finishProvisioningLater() }
                    }
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
                V3PromptSection(prompt: prompt, isSubmitting: $auth.promptSubmitting,
                    isSubmissionBlocked: auth.promptResponseBlocked && (prompt["kind"] as? String == "twoFactor")) { answer in
                    auth.answer(promptID: prompt["id"] as? String ?? "", answer: answer)
                }
                if !auth.promptResponseDiagnostics.isEmpty {
                    Section("Verification response") {
                        DisclosureGroup("Technical details") {
                            Text(auth.promptResponseDiagnostics)
                                .font(.caption2)
                                .textSelection(.enabled)
                        }
                        Button("Copy Diagnostics", systemImage: "doc.on.doc") {
                            UIPasteboard.general.string = auth.promptResponseDiagnostics
                        }
                        .font(.caption)
                    }
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
        .onChange(of: auth.isSignedIn) { isSignedIn in
            guard isSignedIn else { return }
            // The app-owned root observer invalidates certificate-derived
            // readiness from correlated auth terminal events. This snapshot
            // updates the account presentation only.
            status.reload()
        }
        .onDisappear {
            auth.cancel()
            auth.clearPreviousFailure()
            // The root auth-event observer owns certificate-readiness
            // invalidation even after this presentation disappears.
            status.reload()
        }
    }
    private var statusText: String {
        V3AuthCancellationFeedbackPolicy.statusLabel(isCancelling: auth.isCancelling,
            normalLabel: V3AuthStatusTextPolicy.label(state: auth.state, isSignedIn: auth.isSignedIn,
                provisioningFinishedLater: auth.provisioningFinishedLater))
    }
    private var jitlessGuidance: V3SignInJITLessGuidance? {
        V3SignInJITLessGuidancePolicy.resolve(
            osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion,
            readiness: status.jitlessReadiness)
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

// V3_CERTIFICATE_CREATE_PRESENTATION_V1: keep the partial remote-success
// case explicit so users are not prompted to create a duplicate certificate.
enum V3CertificateCreatePresentation {
    static func message(for outcome: String?) -> String {
        if isVerified(outcome) { return "Certificate created and saved." }
        switch outcome {
        case "remoteCreatedLocalStorageUnverified":
            return "Apple created the certificate, but its local signing copy could not be verified. Open Certificates and reload before creating another certificate."
        default:
            return "Certificate creation finished, but its local signing copy could not be confirmed. Open Certificates and reload before creating another certificate."
        }
    }

    static func isVerified(_ outcome: String?) -> Bool {
        outcome == "createdAndStored"
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
    @State private var portalTicket: UInt64 = 0
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
        .onChange(of: status.identityStamp) { _ in
            portalTicket &+= 1
            portal = []
            portalLoaded = false
        }
        .onChange(of: status.authenticated) { authenticated in
            portalTicket &+= 1
            portal = []
            portalLoaded = false
            if !authenticated { busy = "" }
        }
        .onChange(of: status.authenticationActive) { active in
            portalTicket &+= 1
            if active {
                portal = []
                portalLoaded = false
                busy = ""
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3AuthIdentityTransition"))) { _ in
                portalTicket &+= 1
                portal = []
                portalLoaded = false
            }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3AuthIdentityTransitionFinished"))) { _ in
                if status.authenticated { Task { await reload() } }
            }
        .onDisappear {
            portalTicket &+= 1
        }
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
        portalTicket &+= 1
        let ticket = portalTicket
        let stamp = status.identityStamp
        guard status.authenticated, let stamp else { return }
        busy = "portal"
        notice = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "certPortalList")
            let currentIdentity = try await V3ServiceBridge.shared.request(operation: "snapshot")
            guard V3AuthReadStampPolicy.ownsTicket(captured: ticket, current: portalTicket) else { return }
            guard V3ServiceBridge.strictBool(currentIdentity["authenticated"]) == true,
                  V3ServiceBridge.strictBool(currentIdentity["identityStable"]) == true,
                  V3ServiceBridge.strictBool(currentIdentity["authenticationActive"]) != true,
                  currentIdentity["identityStamp"] as? String == stamp else {
                busy = ""
                return
            }
            guard V3AuthReadStampPolicy.mayCommit(capturedTicket: ticket,
                  currentTicket: portalTicket, capturedStamp: stamp,
                  currentStamp: status.identityStamp,
                  stable: V3ServiceBridge.strictBool(reply["identityStable"]) == true,
                  resultStamps: [reply["identityStamp"] as? String],
                  authenticationActive: status.authenticationActive) else {
                busy = ""
                return
            }
            portal = (reply["certificates"] as? [[String: Any]] ?? []).compactMap(V3CertificateRow.init)
            portalLoaded = true
            message = ""
            busy = ""
        } catch {
            guard ticket == portalTicket else { return }
            busy = ""
            message = V3FailureGuidance.message(error)
        }
    }
    private func setActive(serial: String) {
        busy = serial
        notice = ""
        Task {
            defer { busy = "" }
            let mutationTicket = status.beginDirectMutation()
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "certSetActive", target: serial)
                status.finishDirectMutation(ticket: mutationTicket, reply: reply)
                status.invalidateSetupFacts()
                status.reload()
                await reload()
                notice = "Active certificate updated."
            } catch {
                status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
                message = V3FailureGuidance.message(error)
            }
        }
    }
    private func runConfirmed(action: String, serial: String) async {
        confirm = nil
        busy = action + serial
        notice = ""
        defer { busy = "" }
        let mutationTicket = status.beginDirectMutation()
        do {
            var certificateCreateOutcome: String?
            var mutationSnapshot: [String: Any]?
            switch action {
            case "delete": mutationSnapshot = try await V3ServiceBridge.shared.request(operation: "certDelete", target: serial)
            case "revoke":
                mutationSnapshot = try await V3ServiceBridge.shared.request(operation: "certRevoke", target: serial)
                if let mutationSnapshot {
                    _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                        mutationSnapshot, operation: "certRevoke")
                }
            default:
                let reply = try await V3ServiceBridge.shared.request(operation: "certCreate")
                certificateCreateOutcome = reply["outcome"] as? String
                _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                    reply, operation: "certCreate")
            }
            status.finishDirectMutation(ticket: mutationTicket, reply: mutationSnapshot)
            // Certificate mutations can invalidate the cached JIT-Less
            // comparison. Ordinary snapshots do not carry that private fact,
            // so force a fresh authoritative observation before setup reuses it.
            status.invalidateSetupFacts()
            status.reload()
            await reload()
            portalLoaded = false
            message = ""
            switch action {
            case "delete": notice = "Certificate deleted."
            case "revoke": notice = "Certificate revoked."
            default:
                notice = V3CertificateCreatePresentation.message(for: certificateCreateOutcome)
            }
        } catch {
            status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
            message = V3FailureGuidance.message(error)
        }
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
    @State private var reloadTicket: UInt64 = 0
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
                    .disabled(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
                        authenticated: status.authenticated, isLoading: status.loading || loading))
                    .accessibilityHint("Sign in with Apple ID before syncing App IDs.")
                Button(loading ? "Loading Developer Data..." : "Reload Developer Data") { Task { await reload() } }
                    .disabled(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
                        authenticated: status.authenticated, isLoading: loading || status.loading))
                    .accessibilityHint("Sign in with Apple ID before loading developer data.")
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
        .onChange(of: status.identityStamp) { _ in
            invalidateScopedRows()
            if status.authenticated { Task { await reload() } }
        }
        .onChange(of: status.authenticated) { authenticated in
            invalidateScopedRows()
            if authenticated { Task { await reload() } }
        }
        .onChange(of: status.authenticationActive) { active in
            if active { invalidateScopedRows() }
            else if status.authenticated { Task { await reload() } }
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3AuthIdentityTransition"))) { _ in
                invalidateScopedRows()
            }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3AuthIdentityTransitionFinished"))) { _ in
                if status.authenticated { Task { await reload() } }
            }
        .onDisappear {
            reloadTicket &+= 1
        }
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
        reloadTicket &+= 1
        let ticket = reloadTicket
        let capturedStamp = status.identityStamp
        guard status.authenticated else {
            loading = false
            return
        }
        guard let capturedStamp else { loading = false; return }
        loading = true
        do {
            async let teamsReply = V3ServiceBridge.shared.request(operation: "devTeams")
            async let devicesReply = V3ServiceBridge.shared.request(operation: "devDevices")
            async let appIDsReply = V3ServiceBridge.shared.request(operation: "devAppIDs")
            async let groupsReply = V3ServiceBridge.shared.request(operation: "devGroups")
            async let profilesReply = V3ServiceBridge.shared.request(operation: "devProfiles")
            let (teamsResult, devicesResult, appIDsResult, groupsResult, profilesResult) =
                try await (teamsReply, devicesReply, appIDsReply, groupsReply, profilesReply)
            let currentIdentity = try await V3ServiceBridge.shared.request(operation: "snapshot")
            let replies = [teamsResult, devicesResult, appIDsResult, groupsResult, profilesResult]
            guard V3AuthReadStampPolicy.ownsTicket(captured: ticket, current: reloadTicket) else { return }
            guard status.authenticated,
                  V3ServiceBridge.strictBool(currentIdentity["authenticated"]) == true,
                  V3ServiceBridge.strictBool(currentIdentity["identityStable"]) == true,
                  V3ServiceBridge.strictBool(currentIdentity["authenticationActive"]) != true,
                  currentIdentity["identityStamp"] as? String == capturedStamp,
                  V3AuthReadStampPolicy.mayCommit(
                  capturedTicket: ticket, currentTicket: reloadTicket,
                  capturedStamp: capturedStamp, currentStamp: status.identityStamp,
                  stable: replies.allSatisfy({ V3ServiceBridge.strictBool($0["identityStable"]) == true }),
                  resultStamps: replies.map { $0["identityStamp"] as? String },
                  authenticationActive: status.authenticationActive) else {
                if V3AuthReadStampPolicy.ownsTicket(captured: ticket, current: reloadTicket) {
                    loading = false
                }
                return
            }
            teams = strings(teamsResult, key: "teams")
            devices = strings(devicesResult, key: "devices")
            appIDs = strings(appIDsResult, key: "appIDs")
            groups = strings(groupsResult, key: "groups")
            profiles = profilesResult["profiles"] as? [[String: Any]] ?? []
            message = ""
            loading = false
        } catch {
            guard ticket == reloadTicket else { return }
            message = V3FailureGuidance.message(error)
            loading = false
        }
    }
    private func invalidateScopedRows() {
        reloadTicket &+= 1
        teams = []
        devices = []
        appIDs = []
        groups = []
        profiles = []
        message = ""
        loading = false
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
    @State private var pairingFailure: CombinedFailure?
    @State private var working = false
    var body: some View {
        List {
            Section("Status") {
                HStack {
                    Text("Pairing Status")
                    Spacer()
                    Text(V3PairingPresentationPolicy.displayText(statusConnected: status.connected,
                        pairingStatus: status.pairing)).foregroundColor(.secondary)
                }
                if !message.isEmpty {
                    Text(message).font(.footnote).foregroundColor(.red).textSelection(.enabled)
                }
            }
            if let failure = pairingFailure {
                Section("Pairing file could not be read or validated") {
                    Text(failure.safeMessage)
                        .font(.footnote)
                        .foregroundColor(.red)
                        .textSelection(.enabled)
                    Text("What you can do")
                        .font(.caption.weight(.semibold))
                    Text(failure.recovery)
                        .font(.footnote)
                    DisclosureGroup("Technical details") {
                        Text(failure.technicalDetails)
                            .font(.caption2)
                            .textSelection(.enabled)
                    }
                    Button("Copy Diagnostics", systemImage: "doc.on.doc") {
                        UIPasteboard.general.string = failure.technicalDetails
                    }
                    if V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: failure.operation,
                            stage: failure.stage.rawValue, safeCause: failure.safeCause?.rawValue) {
                        Button("Choose Pairing File Again") { pickerPresented = true }
                    }
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
            } else if pairingState == .unknown {
                Section("Pairing Status Unknown") {
                    Text("LiveContainer could not confirm the pairing-file state. Reload status before treating setup as complete.")
                        .font(.footnote).foregroundColor(.secondary)
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
        pairingState == .unsatisfied
    }

    private var pairingState: V3RefreshPrerequisiteState {
        V3PairingPresentationPolicy.state(statusConnected: status.connected, pairingStatus: status.pairing)
    }

    private func recheck() {
        pairingFailure = nil
        message = ""
        status.reload()
    }

    private func importFile(_ url: URL) async {
        working = true
        message = ""
        pairingFailure = nil
        defer { working = false }
        var mutationTicket: UUID?
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data: Data
            do {
                data = try await V3SharedFileInput.readBoundedAsync(url)
            } catch {
                pairingFailure = CombinedFailure(operation: "pairingImportData", stage: .filePreparation,
                    code: .fileAccess, id: UUID().uuidString, underlying: error,
                    retryable: false, safeCause: .pairingFilePreparationFailed)
                return
            }
            guard let token = await status.stageSharedFile(data, purpose: "pairing") else {
                // stageSharedFile presents its own bounded capacity/preparation
                // failure. Do not relabel that prerequisite as a bad pairing file.
                return
            }
            defer { status.discardSharedFile(token) }
            mutationTicket = status.beginDirectMutation()
            let result = try await V3ServiceBridge.shared.request(operation: "pairingImportData", target: token)
            status.finishDirectMutation(ticket: mutationTicket!, reply: result)
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                result, operation: "pairingImportData")
            mutationTicket = nil
            message = ""
        } catch {
            // A failed import may have crossed the service mutation boundary.
            if let mutationTicket {
                status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
            }
            if let failure = error as? CombinedFailure {
                if V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: failure.operation,
                        stage: failure.stage.rawValue, safeCause: failure.safeCause?.rawValue) {
                    pairingFailure = failure
                } else {
                    status.present(failure)
                }
            } else {
                message = V3FailureGuidance.message(error)
            }
        }
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
                let reply = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "bool", "bool": value])
                _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                    reply, operation: "settingsSet")
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
        Task { await setStringAndWait(key, value) }
    }
    func setStringAndWait(_ key: String, _ value: String) async {
        let generation = writeGenerations.begin(key)
        strings[key] = value
        message = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                payload: ["key": key, "type": "string", "string": value])
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                reply, operation: "settingsSet")
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
    func setInt(_ key: String, _ value: Int) {
        let generation = writeGenerations.begin(key)
        ints[key] = value
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "int", "int": value])
                _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                    reply, operation: "settingsSet")
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
    @State private var serverRequestOwners = V3AsyncRequestOwnerState()
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
                                guard !remoteBusy else { return }
                                remoteBusy = true
                                let owner = serverRequestOwners.begin(bindingID: "anisette-servers")
                                Task {
                                    defer { remoteBusy = false }
                                    await store.setStringAndWait("menuAnisetteURL", server.address)
                                    guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
                                    if !store.message.isEmpty {
                                        message = store.message
                                        return
                                    }
                                    await reload()
                                }
                            }
                            .font(.caption)
                            .disabled(remoteBusy)
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
        .onDisappear { serverRequestOwners.invalidate() }
    }
    private func reload() async {
        let owner = serverRequestOwners.begin(bindingID: "anisette-servers")
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "anisetteList")
            guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
            servers = (reply["servers"] as? [[String: Any]] ?? []).compactMap(V3AnisetteServerRow.init)
            message = ""
        } catch {
            guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func remote(_ operation: String) async {
        guard !remoteBusy else { return }
        let owner = serverRequestOwners.begin(bindingID: "anisette-servers")
        remoteBusy = true
        notice = ""
        defer { remoteBusy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: operation)
            guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
            servers = (reply["servers"] as? [[String: Any]] ?? []).compactMap(V3AnisetteServerRow.init)
            message = ""
            notice = operation == "anisetteReset" ? "Anisette servers reset." : "Anisette servers synced."
        } catch {
            guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
            if let failure = error as? CombinedFailure,
               let anisetteGuidance = V3AnisetteFailureGuidance.message(failure) {
                message = anisetteGuidance
            } else {
                message = V3FailureGuidance.message(error)
            }
        }
    }
}

struct V3SideSignView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var config = ""
    @State private var editorRevision: UInt64 = 0
    @State private var configRequestOwners = V3AsyncRequestOwnerState()
    @State private var editorVisible = false
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
        .onAppear { editorVisible = true }
        .onChange(of: config) { _ in invalidateConfigRequestOwner() }
        .onDisappear {
            editorVisible = false
            invalidateConfigRequestOwner()
        }
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
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let capturedConfig = config
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignGet")
            let loadedConfig = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            config = loadedConfig
            message = ""
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func save() async {
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let submittedConfig = config
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let secretToken = try V3SecretHandoff.storeString(config)
            defer { V3SecretHandoff.discard(secretToken) }
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignSet",
                payload: ["secretToken": secretToken])
            let savedConfig = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == submittedConfig else {
                reportStaleMutationIfDraftChanged(from: submittedConfig,
                    capturedEditorRevision: capturedRevision)
                return
            }
            config = savedConfig
            message = ""
            notice = "Configuration saved."
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == submittedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func remote(_ operation: String) async {
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let capturedConfig = config
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: operation)
            let resetConfig = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else {
                reportStaleMutationIfDraftChanged(from: capturedConfig,
                    capturedEditorRevision: capturedRevision)
                return
            }
            config = resetConfig
            message = ""
            notice = "Configuration reset."
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func importFile(_ url: URL) async {
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let capturedConfig = config
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try await V3SharedFileInput.readBoundedAsync(url)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            guard let token = await status.stageSharedFile(data, purpose: "sidesign") else { return }
            defer { status.discardSharedFile(token) }
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignImport", target: token)
            let importedConfig = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else {
                reportStaleMutationIfDraftChanged(from: capturedConfig,
                    capturedEditorRevision: capturedRevision)
                return
            }
            config = importedConfig
            message = ""
            notice = "Configuration imported."
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func exportConfig() async {
        guard !busy, !exporting else { return }
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let capturedConfig = config
        exporting = true
        defer { exporting = false }
        message = ""
        notice = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignExport")
            let text = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("sidesign-config.json")
            try text.write(to: url, atomically: true, encoding: .utf8)
            shareItems = [url]
            notice = "Configuration exported."
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }

    private func invalidateConfigRequestOwner() {
        editorRevision &+= 1
        configRequestOwners.invalidate()
    }

    private func mayApply(_ owner: V3AsyncRequestOwner,
                          capturedEditorRevision: UInt64) -> Bool {
        configRequestOwners.owns(owner, bindingID: "sidesign-config-editor") &&
            capturedEditorRevision == editorRevision
    }

    private func reportStaleMutationIfDraftChanged(from capturedConfig: String,
                                                   capturedEditorRevision: UInt64) {
        guard editorVisible,
              config != capturedConfig || editorRevision != capturedEditorRevision else { return }
        notice = "SideStore accepted an earlier configuration change. Review your current draft before saving it."
    }

    private func consumeConfigToken(in reply: [String: Any]) throws -> String {
        guard let token = reply["secretToken"] as? String else { throw V3SecretHandoffError.malformed }
        defer { V3SecretHandoff.discard(token) }
        return try V3SecretHandoff.consumeString(token)
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

private struct V3JITLessStatusResult {
    let readiness: V3JITLessReadiness
    let detail: String
    let hasImportedCopy: Bool
    let certificateFacts: V3PKCS12CertificateFacts?
}

enum V3ProvisioningRetryReadinessPolicy {
    static func mayComplete(replyState: String?, authenticated: Bool,
                            cancellationInProgress: Bool, taskCancelled: Bool) -> Bool {
        guard replyState == "completed", authenticated, !taskCancelled else { return false }
        // A cancel request can lose the race after provisioning already
        // committed. Accept that exact completed terminal from the owner.
        let normalCommit = !cancellationInProgress
        let committedBeforeCancel = cancellationInProgress && replyState == "completed"
        return normalCommit || committedBeforeCancel
    }
}

struct V3ProvisioningRetryReadinessOwnership {
    private(set) var sessionID: String?

    mutating func begin(sessionID: String) {
        self.sessionID = sessionID
    }

    mutating func clear() {
        sessionID = nil
    }

    func owns(sessionID: String) -> Bool {
        self.sessionID == sessionID
    }

    func allowsPromptResponse(sessionID: String?) -> Bool {
        guard let owner = self.sessionID else { return true }
        guard let sessionID else { return false }
        return owner == sessionID
    }

    // A superseded poll monitor is a handoff of the same retry ownership, not
    // completion of that retry. The continuation must still be able to settle
    // the exact session that the original task started.
    func handoffAfterSupersededPollFailure(sessionID: String) -> Bool {
        owns(sessionID: sessionID)
    }

    mutating func release(sessionID: String) {
        guard owns(sessionID: sessionID) else { return }
        self.sessionID = nil
    }

    mutating func settle(currentSessionID: String?, replySessionID: String?,
                         replyState: String?, authenticated: Bool,
                         cancellationInProgress: Bool, taskCancelled: Bool)
        -> V3ProvisioningRetryReadinessSettlement {
        guard let owner = sessionID else { return .notRetry }
        guard currentSessionID == owner, replySessionID == owner,
              ["completed", "authenticatedProvisioningIncomplete", "failed", "timedOut",
               "promptExpired", "cancelled"].contains(replyState ?? "") else { return .unmatched }
        sessionID = nil
        return V3ProvisioningRetryReadinessPolicy.mayComplete(
            replyState: replyState, authenticated: authenticated,
            cancellationInProgress: cancellationInProgress,
            taskCancelled: taskCancelled) ? .committed : .finishedWithoutCommit
    }
}

enum V3ProvisioningRetryReadinessSettlement: Equatable {
    case notRetry
    case unmatched
    case finishedWithoutCommit
    case committed
}

private enum V3JITLessStatusReader {
    static func read(serviceCertificate: [String: Any]) async -> V3JITLessStatusResult {
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
            return V3JITLessStatusResult(readiness: state,
                detail: V3JITLessPresentation.present(.activeCertificateMissing).detail,
                hasImportedCopy: data != nil, certificateFacts: facts)
        }
        return V3JITLessStatusResult(readiness: state, detail: detail(for: state),
            hasImportedCopy: data != nil, certificateFacts: facts)
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
    @State private var reloadQueue = V3HealthReloadQueue()

    private var jitlessReadiness: V3JITLessReadiness {
        status.jitlessReadiness ?? .unknown
    }
    private var jitlessDetail: String {
        guard let readiness = status.jitlessReadiness else { return "Checking" }
        return V3JITLessPresentation.present(readiness).detail
    }
    private var activeCertificateAvailable: Bool {
        status.jitlessActiveCertificateAvailable ?? false
    }

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
                Button(reloadQueue.isChecking ? "Checking..." : "Re-check") { Task { await reload() } }
                    .disabled(reloadQueue.isChecking)
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
                if V3JITLessCompletionPolicy.isRequired(osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion) {
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
                        case .certificateImported:
                            // The copy exists but validation is not conclusive, so
                            // the canonical setup flow is still the useful action.
                            if activeCertificateAvailable {
                                Button("Open JIT-Less Setup") { openJITLessSetup() }
                            }
                            Button("Open Certificates") { status.certificatesPresented = true }
                        case .unknown:
                            if V3JITLessHealthRecoveryPolicy.shouldOfferCanonicalSetup(
                                for: .unknown, activeCertificateAvailable: activeCertificateAvailable) {
                                Button("Open JIT-Less Setup") { openJITLessSetup() }
                            }
                            Button(reloadQueue.isChecking ? "Checking..." : "Re-check") { Task { await reload() } }
                                .disabled(reloadQueue.isChecking)
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
            // Invalidate immediately, even if this view already has a health
            // request in flight. Its result is stale as soon as the certificate
            // changes, and reload() queues one follow-up request.
            status.invalidateSetupFacts()
            Task { await reload() }
        }
    }

    private func reload() async {
        guard reloadQueue.request() else { return }
        repeat {
            let factRevision = status.beginSetupFactObservation()
            await reloadOnce(factRevision: factRevision)
        } while reloadQueue.finishIteration()
    }

    private func reloadOnce(factRevision: UInt64) async {
        rows.removeAll(keepingCapacity: true)
        certRows.removeAll(keepingCapacity: true)
        message = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
            guard healthRevisionIsCurrent(factRevision) else { return }
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
            let activeCertificateAvailable = V3ServiceBridge.strictBool(certificateState["active"])
            let readiness = await V3JITLessStatusReader.read(serviceCertificate: certificateState)
            guard healthRevisionIsCurrent(factRevision) else { return }
            certRows = certComparison(service: certificateState,
                hasImportedCopy: readiness.hasImportedCopy, localFacts: readiness.certificateFacts)
            // V3_SHARED_JITLESS_FACT_V1: Health is an observer of the same fact,
            // so visiting Health can also complete Home's outstanding item.
            status.recordJITLessReadiness(readiness.readiness,
                activeCertificateAvailable: activeCertificateAvailable, revision: factRevision)
            message = ""
        } catch {
            guard healthRevisionIsCurrent(factRevision) else { return }
            message = V3FailureGuidance.message(error)
            status.recordJITLessReadiness(.unknown,
                activeCertificateAvailable: nil, revision: factRevision)
        }
    }

    private func healthRevisionIsCurrent(_ revision: UInt64) -> Bool {
        guard !status.isSetupFactRevisionCurrent(revision) else { return true }
        // A host lifecycle or another authoritative observer invalidated this
        // request while it was running. Recheck after it finishes so Health
        // does not stay on a stale result or an empty "Checking" state.
        _ = reloadQueue.request()
        return false
    }

    private func openJITLessSetup() {
        sharedModel.selectedTab = .settings
        sharedModel.deepLink = URL(string: "livecontainer://jitless-setup")
    }

    private func openJITLessDiagnose() {
        sharedModel.selectedTab = .settings
        sharedModel.deepLink = URL(string: "livecontainer://jitless-diagnose")
    }

    private func certComparison(service: [String: Any], hasImportedCopy: Bool,
                                localFacts: V3PKCS12CertificateFacts?) -> [(String, String)] {
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
        result.append(("JIT-Less Copy", hasImportedCopy ? "Imported" : "Not imported"))
        let localTeam = localFacts?.teamIdentifier ?? ""
        let localFingerprint = localFacts?.identitySHA256 ?? ""
        if !localTeam.isEmpty {
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
            let secretToken = try V3SecretHandoff.storeString(exportPassword)
            defer { V3SecretHandoff.discard(secretToken) }
            let reply = try await V3ServiceBridge.shared.request(operation: "accountExport",
                payload: ["secretToken": secretToken, "includeApple": includeApple])
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
            let data = try await V3SharedFileInput.readBoundedAsync(url)
            guard let token = await status.stageSharedFile(data, purpose: "accountImport") else { return }
            defer { status.discardSharedFile(token) }
            let secretToken: String
            do { secretToken = try V3SecretHandoff.storeString(importPassword) }
            catch {
                throw error
            }
            defer { V3SecretHandoff.discard(secretToken) }
            let reply = try await V3ServiceBridge.shared.request(operation: "accountImport", target: token,
                payload: ["secretToken": secretToken])
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
                ping = V3SideJITReachabilityFeedback.reachable(
                    httpStatusCode: (response as? HTTPURLResponse)?.statusCode)
            } catch {
                ping = V3SideJITReachabilityFeedback.unreachable
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
        tail = ""
        message = ""
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
    @Published var testLedgerState = ""
    @Published var testSummarySchema = ""
    @Published var testRunning = false
    @Published var lastVerified: Date?
    @Published var diagnostics = ""
    private var testTask: Task<Void, Never>?
    private var testRequestID: String?
    private var testRunID: String?
    private var testAttemptID: String?

    private var groupDefaults: UserDefaults? {
        UserDefaults(suiteName: "group.com.SideStore.SideStore")
    }
    private static let pendingTestRequestIDKey = "V3SetupPendingRefreshRequestID"
    private static let pendingTestRequestDateKey = "V3SetupPendingRefreshRequestDate"
    private var testRequestStartedAt: Date?

    private func testRequestDisposition() -> V3SetupTestRequestDisposition {
        let defaults = groupDefaults
        let ledger = defaults?.dictionary(forKey: "liveContainerAutoRefreshRunLedger") ?? [:]
        let pendingID = defaults?.string(forKey: Self.pendingTestRequestIDKey) ?? testRequestID
        let pendingRecord = pendingID.flatMap {
            V3RefreshAllAttemptState.record(in: ledger, requestID: $0)
        }
        let activeRunID = defaults?.string(forKey: "liveContainerAutoRefreshActiveRunID")
        let activeRecord = activeRunID.flatMap { ledger[$0] as? [String: Any] }
        let storedDate = defaults?.object(forKey: Self.pendingTestRequestDateKey) as? Date
        let startedAt = storedDate ?? testRequestStartedAt
        let age = startedAt.map { max(0, Date().timeIntervalSince($0)) } ?? .infinity
        return V3SetupTestRequestPolicy.select(
            pendingRequestID: pendingID, pendingAge: age,
            pendingState: pendingRecord?["state"] as? String,
            activeRunID: activeRunID,
            activeRunRequestID: activeRecord?["request_id"] as? String)
    }

    // Setup Complete requires every required item: pairing, signed-in account
    // with team, acceptable network and tunnel, available Background App
    // Refresh, an enabled schedule, and a test verified in this assistant
    // session. Developer Mode stays advisory and never gates.
    // V3_SETUP_COMPLETION_POLICY_V1: the decision comes from the one shared
    // policy that Home also uses, so the two screens cannot disagree.
    /// V3_SHARED_JITLESS_FACT_V1: the store is passed in because the fact the
    /// decision needs is published on the status store, and this type is not a
    /// View. Reading the published fact rather than a local copy is what stops
    /// the assistant and Home from holding two answers.
    func completionInputs(status: V3SideStoreStatusStore) -> V3SetupCompletionInputs {
        V3SetupCompletionInputs(
            accountComplete: account.state == "complete",
            provisioningIncomplete: statusProvisioningIncomplete,
            pairingSatisfied: pairing.state == "complete",
            // V3_SHARED_JITLESS_FACT_V1: both surfaces ask the same shared
            // policy the same question, of the same published fact. The
            // assistant previously answered from its own step-state string,
            // which is a second authority: Health could publish a ready
            // readiness the assistant had not yet observed, and the two would
            // disagree about whether the item was outstanding.
            jitlessRequired: V3JITLessCompletionPolicy.isRequired(
                osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion),
            jitlessComplete: V3JITLessCompletionPolicy.isComplete(status.jitlessReadiness),
            networkComplete: network.state == "complete",
            tunnelComplete: tunnel.state == "complete",
            backgroundRefreshAvailable: background.state == "complete",
            scheduleEnabled: schedule.state == "complete",
            verifiedRefreshPresent: verification.state == "complete")
    }
    func outstandingSetup(status: V3SideStoreStatusStore) -> [V3SetupOutstandingItem] {
        completionInputs(status: status).outstanding()
    }
    func isComplete(status: V3SideStoreStatusStore) -> Bool {
        completionInputs(status: status).isComplete
    }

    /// Where the JIT-Less row leads when the state still needs work. A ready
    /// state has no required destination; its diagnostic action is separate.
    /// The status store is passed in because the store is not a View and has no
    /// environment of its own.
    func jitlessDestination(status: V3SideStoreStatusStore) -> AnyView? {
        let readiness = status.jitlessReadiness ?? .unknown
        if readiness.isSatisfied { return nil }
        if [.activeCertificateRevoked, .activeCertificateExpired].contains(readiness) || !jitlessHasActiveCertificate {
            return AnyView(V3CertificatesView().environmentObject(status))
        }
        return nil
    }

    // Set from the authoritative snapshot so the shared policy sees the same
    // provisioning fact Home sees, rather than inferring it from step states.
    @Published private(set) var statusProvisioningIncomplete = false

    private func publishJITLessReadiness(_ readiness: V3JITLessReadiness,
                                        activeCertificateAvailable: Bool?,
                                        status: V3SideStoreStatusStore,
                                        factRevision: UInt64) {
        jitlessHasActiveCertificate = activeCertificateAvailable == true
        status.recordJITLessReadiness(readiness,
            activeCertificateAvailable: activeCertificateAvailable, revision: factRevision)
        let presentation = V3JITLessPresentation.present(readiness)
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
    }

    @discardableResult
    func reloadAndRecalculate(status: V3SideStoreStatusStore) async -> Bool {
        let outcome = await status.reloadAndWait()
        guard V3SetupReloadRecomputePolicy.mayRecompute(outcome: outcome.setupSnapshotOutcome) else {
            preserveUnknownStatusFacts(status: status)
            return false
        }
        await recalculate(status: status)
        return true
    }

    private func preserveUnknownStatusFacts(status: V3SideStoreStatusStore) {
        status.invalidateSetupFacts()
        statusProvisioningIncomplete = true
        account = V3SetupStepState(state: "checking", detail: "Sign-in status was not refreshed. Retry to check.")
        pairing = V3SetupStepState(state: "checking", detail: "Pairing status was not refreshed. Retry to check.")
        jitless = V3SetupStepState(state: "checking", detail: "Certificate status was not refreshed. Retry to check.")
    }

    func recalculate(status: V3SideStoreStatusStore) async {
        // If the root auth observer already started a fresh readiness read,
        // join it before taking a new fact revision or opening another health
        // snapshot. This is the Setup Assistant's return-from-sign-in path.
        let sharedReadiness = await status.awaitSharedSetupJITLessReadiness()
        // Validate the observation against the status owner's current revision
        // after the await. Reuse keeps its source revision; a missing or stale
        // snapshot starts a fresh revision before the authoritative health read.
        let currentFactRevision = status.currentSetupFactRevision
        let observationToReuse: V3SetupReadinessObservation?
        if !V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(sharedReadiness),
           let sharedReadiness,
           !V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(
                sharedReadiness, currentFactRevision: currentFactRevision),
           status.jitlessReadiness == sharedReadiness.readiness,
           status.jitlessActiveCertificateAvailable == sharedReadiness.activeCertificateAvailable {
            observationToReuse = sharedReadiness
        } else {
            observationToReuse = nil
        }
        let factRevision: UInt64
        if let observationToReuse {
            factRevision = observationToReuse.sourceFactRevision
        } else {
            factRevision = status.beginSetupFactObservation()
        }
        NSLog("[V3_SETUP] STATUS recalculating")
        // Recorded from the authoritative snapshot so the shared completion
        // policy sees the same provisioning fact Home sees.
        statusProvisioningIncomplete = status.provisioningIncomplete
        device = V3SetupStepState(state: "complete", detail: "App running")
        // V3_REFRESH_PREREQUISITE_POLICY_V1: shared with Home Refresh All, Test
        // Refresh, and targeted refresh. A known-missing pairing file is
        // "missing"; an unknown status stays unknown rather than being reported
        // as missing.
        switch V3PairingPresentationPolicy.state(statusConnected: status.connected,
            pairingStatus: status.pairing) {
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
            status.recordJITLessReadiness(.notRequired, revision: factRevision)
            jitless = V3SetupStepState(state: "complete", detail: "Not required on this iOS version")
        } else if let observationToReuse {
            guard status.isSetupFactRevisionCurrent(factRevision) else { return }
            publishJITLessReadiness(observationToReuse.readiness,
                activeCertificateAvailable: observationToReuse.activeCertificateAvailable,
                status: status, factRevision: factRevision)
        } else {
            do {
                let health = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
                guard status.isSetupFactRevisionCurrent(factRevision) else { return }
                let certificate = health["certificateState"] as? [String: Any] ?? [:]
                let activeCertificateAvailable = V3ServiceBridge.strictBool(certificate["active"]) == true
                let readiness = await V3JITLessStatusReader.read(serviceCertificate: certificate)
                guard status.isSetupFactRevisionCurrent(factRevision) else { return }
                publishJITLessReadiness(readiness.readiness,
                    activeCertificateAvailable: activeCertificateAvailable,
                    status: status, factRevision: factRevision)
            } catch {
                guard status.isSetupFactRevisionCurrent(factRevision) else { return }
                jitlessHasActiveCertificate = false
                // Not observed is published as unknown, so Home keeps the item
                // outstanding instead of assuming a certificate exists.
                status.recordJITLessReadiness(.unknown, revision: factRevision)
                jitless = V3SetupStepState(state: "warning", detail: "Could not verify JIT-Less certificate state")
            }
        }
        network = V3SetupStepState(state: "checking", detail: "Checking Wi-Fi…")
        let wifi = await LiveContainerNetworkPreflight.wifiAvailable()
        guard status.isSetupFactRevisionCurrent(factRevision) else { return }
        // Published so the shared setup-completion policy and the Home banner
        // observe the same authoritative Wi-Fi fact instead of each deciding.
        status.recordWifiAvailability(wifi, revision: factRevision)
        status.markSetupFactsObserved(revision: factRevision)
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

    /// Records a Test Refresh failure for the Setup Assistant.
    ///
    /// V3_FAILURE_GUIDANCE_V1: the row caption is product copy, so it shows
    /// guidance. It previously appended the numeric NSError domain and code,
    /// which is the practice the failure policy exists to remove, and the branch
    /// that did it also made the final `else` unreachable: every Swift Error
    /// bridges to `NSError?`, so that arm could never run. The domain and code
    /// remain available in the diagnostics block, which is where a support reader
    /// looks for them.
    func recordError(_ error: Error, operation: String) {
        if let failure = error as? CombinedFailure {
            recordFailure(operation: operation, stage: failure.stage.rawValue, code: failure.code.rawValue,
                          correlation: failure.correlationID,
                          retryable: failure.retryable.map { $0 ? "true" : "false" } ?? "")
            verification = V3SetupStepState(state: "failed", detail: failure.safeMessage)
            verificationGuidance = failure.recovery
            return
        }
        // No stage or code is invented for an untyped error. Nothing claimed a
        // cause, so nothing is asserted about one.
        recordFailure(operation: operation, stage: "", code: "", correlation: "", retryable: "")
        verification = V3SetupStepState(state: "warning",
            detail: "Test refresh could not be completed, and the cause is not known.")
        verificationGuidance = V3FailureGuidance.message(error)
    }

    // V3_REFRESH_PREREQUISITE_POLICY_V1: Test Refresh uses the same
    // authoritative prerequisite contract as Home Refresh All. A known-missing
    // pairing file must never post the scheduler notification, so no backend
    // mutation is started, and it must never be reported as an unexplained
    // refresh failure.
    func runTestRefresh(status: V3SideStoreStatusStore) {
        guard !testRunning else { return }
        let disposition = testRequestDisposition()
        let requestID: String
        let shouldPostRequest: Bool
        switch disposition {
        case .waitForActiveRun:
            let defaults = groupDefaults
            // We cannot safely attach an uncorrelated pending request to this
            // different active run. It did not create a second scheduler run.
            defaults?.removeObject(forKey: Self.pendingTestRequestIDKey)
            defaults?.removeObject(forKey: Self.pendingTestRequestDateKey)
            testRequestStartedAt = nil
            testRequestID = nil
            testRunID = nil
            testAttemptID = nil
            testRunning = false
            failureOperation = ""
            failureStage = ""
            failureCode = ""
            failureCorrelation = ""
            failureRetryable = ""
            verification = V3SetupStepState(state: "warning",
                detail: "Another refresh is already running. Test Refresh did not start.")
            verificationGuidance = "Wait for the current refresh to finish, then start Test Refresh again."
            NSLog("[V3_SETUP] TEST_REFRESH_BLOCKED reason=activeRun")
            return
        case .resumeExisting(let existingRequestID):
            requestID = existingRequestID
            shouldPostRequest = false
            testRequestID = existingRequestID
            testRequestStartedAt = groupDefaults?.object(
                forKey: Self.pendingTestRequestDateKey) as? Date ?? Date()
            let ledger = groupDefaults?.dictionary(forKey: "liveContainerAutoRefreshRunLedger") ?? [:]
            testRunID = V3RefreshAllAttemptState.record(in: ledger,
                requestID: existingRequestID)?["run_id"] as? String
        case .startNew:
            requestID = UUID().uuidString
            shouldPostRequest = true
            if let failure = V3RefreshPrerequisite.evaluate(statusConnected: status.connected,
                    pairingStatus: status.pairing)
                .failure(correlationID: requestID) {
                testRunning = false
                testRequestID = nil
                testRunID = nil
                testAttemptID = nil
                testTask = nil
                testRequestStartedAt = nil
                recordFailure(operation: failure.operation, stage: failure.stage.rawValue,
                              code: failure.code.rawValue, correlation: failure.correlationID,
                              retryable: "false")
                verification = V3SetupStepState(state: "actionRequired", detail: failure.safeMessage)
                verificationGuidance = "Place or import a valid pairing file, then try again."
                NSLog("[V3_SETUP] TEST_REFRESH_BLOCKED reason=pairing request_id=%@", requestID)
                return
            }
            let startedAt = Date()
            testRequestStartedAt = startedAt
            groupDefaults?.set(requestID, forKey: Self.pendingTestRequestIDKey)
            groupDefaults?.set(startedAt, forKey: Self.pendingTestRequestDateKey)
            testRequestID = requestID
            testRunID = nil
        }
        testRunning = true
        let attemptID = UUID().uuidString
        testAttemptID = attemptID
        testLedgerState = ""
        testSummarySchema = ""
        failureOperation = ""
        failureStage = ""
        failureCode = ""
        failureCorrelation = ""
        failureRetryable = ""
        verification = V3SetupStepState(state: "running",
            detail: shouldPostRequest ? "Test refresh running…" : "Resuming the current Test Refresh…")
        verificationGuidance = ""
        NSLog("[V3_SETUP] TEST_REFRESH_START request_id=%@ origin=setupAssistant resumed=%@",
              requestID, shouldPostRequest ? "false" : "true")
        if shouldPostRequest {
            NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil,
                                            userInfo: ["requestID": requestID, "origin": "setupAssistant"])
        }
        startTestMonitor(requestID: requestID, attemptID: attemptID)
    }

    private func startTestMonitor(requestID: String, attemptID: String) {
        testTask = Task {
            do {
                let deadline = Date().addingTimeInterval(600)
                while !Task.isCancelled && Date() < deadline {
                    try await Task.sleep(nanoseconds: 2_000_000_000)
                    try Task.checkCancellation()
                    if await checkTestResult(attemptID: attemptID) { return }
                }
                if V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: attemptID,
                    currentAttemptID: testAttemptID, taskCancelled: Task.isCancelled) {
                    verification = V3SetupStepState(state: "warning", detail: "No verified result yet. Check Refresh Manager for progress.")
                    NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=timeout")
                    testRunning = false
                    testAttemptID = nil
                    testTask = nil
                }
            } catch is CancellationError {
                return
            } catch {
                guard V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: attemptID,
                    currentAttemptID: testAttemptID, taskCancelled: Task.isCancelled) else { return }
                recordError(error, operation: "refresh")
                NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=error")
                testRunning = false
                testAttemptID = nil
                testTask = nil
            }
        }
    }

    private func checkTestResult(attemptID: String) async -> Bool {
        guard V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: attemptID,
            currentAttemptID: testAttemptID, taskCancelled: Task.isCancelled) else { return false }
        guard let requestID = testRequestID else { return false }
        let ledger = groupDefaults?.dictionary(forKey: "liveContainerAutoRefreshRunLedger") ?? [:]
        guard let runRecord = V3RefreshAllAttemptState.record(in: ledger, requestID: requestID),
              let runID = runRecord["run_id"] as? String else {
            let age = testRequestStartedAt.map { Date().timeIntervalSince($0) } ?? 0
            let activeRunID = groupDefaults?.string(forKey: "liveContainerAutoRefreshActiveRunID") ?? ""
            let activeRecord = ledger[activeRunID] as? [String: Any]
            let activeRequestID = activeRecord?["request_id"] as? String
            if age >= V3SetupTestRequestPolicy.startGracePeriod,
               activeRunID.isEmpty || activeRequestID != requestID {
                recordFailure(operation: "refresh", stage: CombinedFailure.Stage.command.rawValue,
                    code: CombinedFailure.Code.busy.rawValue, correlation: requestID, retryable: "false")
                verification = V3SetupStepState(state: "failed", detail: "Refresh did not start.")
                verificationGuidance = activeRunID.isEmpty
                    ? "Check Refresh Manager, then try Test Refresh again."
                    : "Another refresh took the scheduler first. Wait for it to finish, then start Test Refresh again."
                finishTestAttempt(requestID: requestID, attemptID: attemptID)
                NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=not_started request_id=%@", requestID)
                return true
            }
            return false
        }
        if let testRunID, testRunID != runID { return false }
        testRunID = runID
        let runState = runRecord["state"] as? String ?? ""
        testLedgerState = runState
        guard runState == "completed" || runState == "failed" else { return false }
        let manifest = runRecord["manifest"] as? [String: Any]
        let verifiedManifest = manifest.map {
            $0["run_id"] as? String == runID &&
                CombinedVerification.hasCompleteTerminalResults($0, runID: runID)
        } ?? false
        let verifiedSummary = V3RefreshAllTerminalEvidencePolicy.verifiedSummary(
            runRecord["manifest_summary"] as? [String: Any], record: runRecord, runID: runID)
        let summary = runRecord["manifest_summary"] as? [String: Any]
        testSummarySchema = summary?["schema"] as? String ?? "missing"
        switch V3SetupRefreshTerminalEvidencePolicy.outcome(state: runState,
            hasVerifiedManifest: verifiedManifest, hasVerifiedSummary: verifiedSummary) {
        case .pending:
            return false
        case .failed:
            let wire = runRecord["failure"] as? [String: Any]
            if let failure = wire.flatMap({ CombinedFailure.decode($0, expectedID: runID) }),
               failure.operation == "refresh" {
                recordFailure(operation: failure.operation, stage: failure.stage.rawValue,
                    code: failure.code.rawValue, correlation: failure.correlationID,
                    retryable: failure.retryable.map { $0 ? "true" : "false" } ?? "")
                verification = V3SetupStepState(state: "failed",
                    detail: runRecord["message"] as? String ?? failure.safeMessage)
                verificationGuidance = failure.recovery
            } else {
                recordFailure(operation: "refresh", stage: CombinedFailure.Stage.refreshVerification.rawValue,
                    code: "unknown", correlation: runID, retryable: "unknown")
                verification = V3SetupStepState(state: "failed",
                    detail: runRecord["message"] as? String ?? "Refresh failed, but no safe underlying cause was available.")
                verificationGuidance = "Open Refresh Manager to inspect this run, then try Test Refresh again. Copy Diagnostics if the result remains unclear."
            }
            finishTestAttempt(requestID: requestID, attemptID: attemptID)
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=failed run_id=%@", runID)
            return true
        case .completedUnverified:
            recordFailure(operation: "refresh", stage: CombinedFailure.Stage.refreshVerification.rawValue,
                code: "invalidResponse", correlation: runID, retryable: "false")
            verification = V3SetupStepState(state: "failed",
                detail: "SideStore reported that refresh completed, but this run's result could not be verified.")
            verificationGuidance = "Open Refresh Manager to reconcile the run, then run Test Refresh again. Copy Diagnostics if the result remains missing."
            finishTestAttempt(requestID: requestID, attemptID: attemptID)
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=unverified_completion run_id=%@", runID)
            return true
        case .verified:
            break
        }
        let results = manifest?["results"] as? [[String: Any]] ?? []
        if runState == "completed" && (verifiedSummary || results.allSatisfy({ $0["success"] as? Bool == true })) {
            verification = V3SetupStepState(state: "complete", detail: "Refresh verified")
            if let date = manifest?["date"] as? Date {
                lastVerified = date
            } else if let verifiedAt = (runRecord["manifest_summary"] as? [String: Any])?["verified_at"] as? Date {
                lastVerified = verifiedAt
            } else if let terminalAt = runRecord["terminal_at"] as? TimeInterval {
                lastVerified = Date(timeIntervalSince1970: terminalAt)
            }
        } else {
            var detail = "Refresh reported failures"
            if let failed = results.first(where: { $0["success"] as? Bool != true }) {
                recordFailure(operation: "refresh", stage: "", code: "", correlation: runID, retryable: "")
                // V3_FAILURE_GUIDANCE_V1: the manifest's "error" field is a
                // diagnostic block of message, recovery and technical details
                // joined by newlines. It was used verbatim as the row caption, so
                // a diagnostics line was rendered as product copy. The caption
                // takes the structured failure's safe message when the manifest
                // carries one, and the full text is still available below in the
                // diagnostics block.
                let wire = failed["failure"] as? [String: Any]
                let decoded = wire.flatMap { CombinedFailure.decode($0, expectedID: runID) }
                if let decoded {
                    detail = decoded.safeMessage
                    verificationGuidance = decoded.recovery
                } else if let message = failed["error"] as? String, !message.isEmpty,
                          let firstLine = message.split(separator: "\n").first {
                    detail = String(firstLine)
                }
                if let failure = wire {
                    recordFailure(operation: failure["operation"] as? String ?? "refresh",
                                  stage: failure["stage"] as? String ?? "",
                                  code: failure["code"] as? String ?? "",
                                  correlation: failure["correlationID"] as? String ?? runID,
                                  retryable: (failure["retryable"] as? Bool).map { $0 ? "true" : "false" } ?? "")
                }
            }
            verification = V3SetupStepState(state: "failed", detail: detail)
        }
        finishTestAttempt(requestID: requestID, attemptID: attemptID)
        if verification.state == "complete" {
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=verified")
        } else {
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=failed")
        }
        return true
    }

    private func finishTestAttempt(requestID: String, attemptID: String) {
        guard V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: attemptID,
            currentAttemptID: testAttemptID, taskCancelled: Task.isCancelled) else { return }
        testRunning = false
        testAttemptID = nil
        testTask = nil
        testRequestID = nil
        testRunID = nil
        testRequestStartedAt = nil
        if groupDefaults?.string(forKey: Self.pendingTestRequestIDKey) == requestID {
            groupDefaults?.removeObject(forKey: Self.pendingTestRequestIDKey)
            groupDefaults?.removeObject(forKey: Self.pendingTestRequestDateKey)
        }
    }

    func cancelTest() {
        guard testRunning else { return }
        let requestID = testRequestID
        testAttemptID = nil
        testTask?.cancel()
        testTask = nil
        testRunning = false
        verification = V3SetupStepState(state: "warning",
            detail: "Stopped waiting. The current refresh continues in the background.")
        verificationGuidance = requestID == nil
            ? "Check Refresh Manager before starting another Test Refresh."
            : "Tap Test Refresh again to resume this same request; it will not start a duplicate refresh."
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
        lines.append("Pairing: " + V3SetupStore.describePairing(V3PairingPresentationPolicy.state(
            statusConnected: status.connected, pairingStatus: status.pairing)))
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
        if !testLedgerState.isEmpty {
            lines.append("Test refresh terminal state: \(testLedgerState)")
            lines.append("Verification summary schema: \(testSummarySchema.isEmpty ? "none" : testSummarySchema)")
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
                            await setup.reloadAndRecalculate(status: status)
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
            if V3JITLessCompletionPolicy.isRequired(osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion) {
                // V3_JITLESS_PRESENTATION_V1: a ready JIT-Less state is rendered
                // as a completed result, not as an outstanding setup task. The
                // section only presents required actions while something is
                // actually outstanding.
                let jitless = V3JITLessPresentation.present(status.jitlessReadiness ?? .unknown)
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
                        switch V3JITLessSetupActionPolicy.action(
                            for: status.jitlessReadiness ?? .unknown) {
                        case .setUp:
                            Button("Set Up JIT-Less") { openCanonicalJITLessSetup() }
                        case .refreshCertificate:
                            Button("Refresh JIT-Less Certificate") { openCanonicalJITLessSetup() }
                        case .openCertificates:
                            Button("Open Certificates") { status.certificatesPresented = true }
                        case .openSetup:
                            Button("Open JIT-Less Setup") { openCanonicalJITLessSetup() }
                        case .none:
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
                    Task { await LiveContainerAutoRefreshScheduler.requestNotificationPermissionFromUserAction() }
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
                            await setup.reloadAndRecalculate(status: status)
                        }
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                }
                if setup.testRunning {
                    Button("Stop Waiting", role: .cancel) { setup.cancelTest() }
                } else if setup.verification.state != "complete" && setup.pairing.state == "actionRequired" {
                    Text("Complete Pairing Setup before testing refresh.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
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
            if !setup.outstandingSetup(status: status).isEmpty {
                Section("Still Needed") {
                    ForEach(setup.outstandingSetup(status: status), id: \.self) { item in
                        Label(item.title, systemImage: V3StatusSeverity.warning.icon)
                            .font(.footnote)
                            .foregroundColor(.orange)
                    }
                }
            }
            if setup.isComplete(status: status) {
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
            await setup.reloadAndRecalculate(status: status)
        }
        .onChange(of: status.jitlessReadiness) { readiness in
            // A host-owned auth event may invalidate or refresh this fact while
            // Sign In is being dismissed. Recompute from the shared observation.
            guard readiness != nil else { return }
            Task { await setup.recalculate(status: status) }
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active {
                Task {
                    // A pairing file placed by the installation tool while the app
                    // was backgrounded is detected here. The reload must complete
                    // before recalculate reads status, otherwise the setup rows
                    // are computed from the previous snapshot.
                    await setup.reloadAndRecalculate(status: status)
                }
            }
        }
        .onChange(of: showPairingSetup) { presented in
            if !presented {
                Task {
                    await setup.reloadAndRecalculate(status: status)
                }
            }
        }
        .onDisappear {
            if setup.testRunning { setup.cancelTest() }
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
                    await setup.reloadAndRecalculate(status: status)
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
            pairingSatisfied: V3PairingPresentationPolicy.isConfirmed(
                statusConnected: status.connected, pairingStatus: status.pairing),
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
                            Text(V3PairingPresentationPolicy.displayText(statusConnected: status.connected,
                                pairingStatus: status.pairing))
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
