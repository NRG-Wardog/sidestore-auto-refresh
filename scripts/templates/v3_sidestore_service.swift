
// V3_SIDESTORE_COMMAND_SERVICE_V1
// Compiled only into SideStore. No managed objects or credentials cross XPC.
import SwiftUI
import Foundation

// LC_STRUCTURED_AUTH_FAILURE_V1: minimal auth failure vocabulary for XPC transport.
private struct V3AuthFailure: Error, LocalizedError {
    enum Stage: String, CaseIterable { case authentication, anisette, network, accountRepair, unknown }
    enum Code: String, CaseIterable { case invalidCredentials, rateLimited, serviceUnavailable, anisetteFailure, networkFailure, accountRepairRequired, unknown }
    let operation: String
    let stage: Stage
    let code: Code
    let correlationID: String
    let underlyingDomain: String
    let underlyingCode: Int
    let retryable: Bool?
    init(operation: String, stage: Stage, code: Code = .unknown, id: String, underlying: Error? = nil, retryable: Bool? = nil) {
        self.operation = operation
        self.stage = stage; self.code = code
        correlationID = UUID(uuidString: id) != nil ? id : UUID().uuidString
        let error = underlying as NSError?
        let domain = error?.domain ?? "none"
        underlyingDomain = ["none", "NSCocoaErrorDomain", "NSPOSIXErrorDomain", "NSURLErrorDomain", "NSOSStatusErrorDomain", "ALTServerErrorDomain", "ALTAppleAPIErrorDomain", "ALTErrorDomain", "GrandSlamErrorDomain", "SideSignErrorDomain"].contains(domain) ? domain : "redacted"
        underlyingCode = error?.code ?? 0
        self.retryable = retryable
    }
    var message: String {
        if code == .invalidCredentials { return "Apple did not accept the Apple ID or password. Check them and try again." }
        if code == .rateLimited { return "Apple is temporarily rate-limiting sign-in attempts. Wait before trying again." }
        if code == .serviceUnavailable { return "Apple's authentication service is temporarily unavailable. Try again later." }
        if code == .anisetteFailure { return "Authentication could not obtain valid Anisette data." }
        if code == .networkFailure { return "Authentication could not reach the required Apple service." }
        if code == .accountRepairRequired { return "Account repair is required. Open the Apple Developer account to resolve." }
        return "Apple ID sign-in failed."
    }
    var recovery: String {
        switch code {
        case .invalidCredentials: return "Verify your Apple ID and password, then retry."
        case .rateLimited: return "Wait a few minutes before attempting to sign in again."
        case .serviceUnavailable: return "Apple's servers are experiencing issues. Try again later."
        case .anisetteFailure: return "Check your Anisette server configuration."
        case .networkFailure: return "Check your network connection and retry."
        case .accountRepairRequired: return "Complete the required account repair steps in the Apple Developer portal."
        default: return "Review the error details and retry explicitly."
        }
    }
    var technicalDetails: String {
        "operation=\(operation) stage=\(stage.rawValue) code=\(code.rawValue) correlation=\(correlationID) underlying_domain=\(underlyingDomain) underlying_code=\(underlyingCode) retryable=\(retryable.map(String.init) ?? "unknown")"
    }
    var errorDescription: String? { message + "\n" + recovery + "\n" + technicalDetails }
    var wire: [String: Any] {
        var result: [String: Any] = ["version": 1, "operation": operation, "stage": stage.rawValue, "code": code.rawValue,
            "correlationID": correlationID, "underlyingDomain": underlyingDomain, "underlyingCode": underlyingCode]
        if let retryable { result["retryable"] = retryable }
        return result
    }
    static func classify(_ error: Error, id: String) -> V3AuthFailure {
        if let known = error as? V3AuthFailure { return known }
        let cause = error as NSError
        var stage: Stage = .unknown
        var code: Code = .unknown
        var retryable: Bool? = nil
        // Check underlying error chain for classification signals
        var current: NSError? = cause
        for _ in 0..<5 {
            guard let err = current else { break }
            // HTTP status codes from SideSign/GrandSlam
            if err.domain == "ALTAppleAPIErrorDomain" || err.domain == "GrandSlamErrorDomain" || err.domain == "SideSignErrorDomain" {
                if err.code == 429 { stage = .authentication; code = .rateLimited; retryable = true }
                else if err.code == 503 { stage = .authentication; code = .serviceUnavailable; retryable = true }
                else if err.code == 401 || err.code == -22406 { stage = .authentication; code = .invalidCredentials; retryable = false }
                else if err.code == -20101 || err.code == -20209 { stage = .authentication; code = .invalidCredentials; retryable = false }
            }
            // Anisette errors
            if err.domain == "AnisetteErrorDomain" || err.localizedDescription.localizedCaseInsensitiveContains("anisette") {
                stage = .anisette; code = .anisetteFailure; retryable = false
            }
            // Network errors
            if err.domain == NSURLErrorDomain || err.domain == NSPOSIXErrorDomain {
                stage = .network; code = .networkFailure; retryable = true
            }
            // Account repair
            if err.domain == "ALTAccountRepairErrorDomain" || err.localizedDescription.localizedCaseInsensitiveContains("account repair") {
                stage = .accountRepair; code = .accountRepairRequired; retryable = false
            }
            // GrandSlam rate limiting codes
            if err.code == -22411 || err.code == -20102 || err.code == -21668 {
                stage = .authentication; code = .rateLimited; retryable = true
            }
            if let next = err.userInfo[NSUnderlyingErrorKey] as? NSError { current = next } else { break }
        }
        return V3AuthFailure(operation: "signIn", stage: stage, code: code, id: id, underlying: cause, retryable: retryable)
    }
}

@MainActor
@objc(V3SideStoreService)
final class V3SideStoreService: NSObject {
    static let shared = V3SideStoreService()
    private var tasks: [String: Task<Void, Never>] = [:]
    private var cancellations: [String: () -> Void] = [:]
    private var completed: [String: (data: Data, deadline: Date)] = [:]
    private var mutationID: String?
    private var finishPanel: (() -> Void)?
    static let presenter = UIViewController()

    @objc(execute:reply:)
    nonisolated static func execute(_ data: Data, reply: @escaping (Data) -> Void) {
        Task { @MainActor in shared.receive(data, reply: reply) }
    }

    private func receive(_ data: Data, reply: @escaping (Data) -> Void) {
        guard let request = V3WireContract.decodeRequest(data),
              let id = request["id"] as? String,
              let operation = request["operation"] as? String,
              let deadline = request["deadline"] as? Date,
              deadline > Date(), deadline.timeIntervalSinceNow <= 610 else {
            reply(encode(["error": "invalidRequest"]))
            return
        }
        completed = completed.filter { $0.value.deadline > Date() }
        if let previous = completed[id] { reply(previous.data); return }
        if operation == "cancel" {
            let target = request["target"] as? String ?? ""
            tasks[target]?.cancel()
            cancellations[target]?()
            if mutationID == target { Self.presenter.dismiss(animated: true) }
            reply(encode(["id": id, "version": 1, "ok": true]))
            return
        }
        guard tasks[id] == nil else { reply(encode(["id": id, "error": "busy"])); return }
        let mutation = !["snapshot", "catalog", "backupResult"].contains(operation)
        guard !mutation || (mutationID == nil && completed.count < 512) else {
            reply(encode(["id": id, "error": "busy"])); return
        }
        if mutation { mutationID = id }
        tasks[id] = Task { @MainActor in
            defer {
                tasks[id] = nil
                cancellations[id] = nil
                if mutationID == id { mutationID = nil }
            }
            var response: [String: Any] = ["version": 1, "id": id]
            do {
                guard DatabaseManager.shared.isStarted else { throw ServiceError.notReady }
                try Task.checkCancellation()
                let result = try await run(operation, request: request, id: id)
                try Task.checkCancellation()
                response["result"] = result
                response["ok"] = true
            } catch {
                // Raw framework errors can contain URLs, authentication data or server responses.
                // Detailed errors remain inside the SideStore process.
                if let serviceError = error as? ServiceError {
                    response["error"] = serviceError.rawValue
                } else if error is CancellationError {
                    response["error"] = "cancelled"
                } else {
                    response["error"] = "operationFailed"
                    // LC_STRUCTURED_AUTH_FAILURE_V1: include structured auth failure for signIn
                    if operation == "signIn" {
                        let failure = V3AuthFailure.classify(error, id: id)
                        response["failure"] = failure.wire
                        debugLog("[V3_AUTH] ATTEMPT_FAILED session=\(id) stage=\(failure.stage.rawValue) code=\(failure.code.rawValue) correlation=\(failure.correlationID) underlying=\(failure.underlyingDomain)/\(failure.underlyingCode)")
                    }
                }
            }
            let encoded = encode(response)
            if mutation { completed[id] = (encoded, deadline) }
            reply(encoded)
        }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(max(0, deadline.timeIntervalSinceNow) * 1_000_000_000))
            if tasks[id] != nil { tasks[id]?.cancel(); cancellations[id]?() }
        }
    }

    enum ServiceError: String, Error { case notReady, invalidRequest, notFound, unsupported, busy }

    private func encode(_ value: [String: Any]) -> Data {
        guard let data = try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0),
              data.count <= 4_194_304 else {
            return try! PropertyListSerialization.data(fromPropertyList: ["id": value["id"] ?? "", "error": "responseTooLarge"], format: .binary, options: 0)
        }
        return data
    }

    private func run(_ operation: String, request: [String: Any], id: String) async throws -> [String: Any] {
        let context = DatabaseManager.shared.viewContext
        let target = request["target"] as? String ?? ""
        switch operation {
        case "snapshot": return try snapshot()
        case "backupResult":
            guard mutationID != nil, ["success", "failure"].contains(target) else { throw ServiceError.invalidRequest }
            let result: Result<Void, Error> = target == "success" ? .success(()) : .failure(ServiceError.unsupported)
            NotificationCenter.default.post(name: AppDelegate.appBackupDidFinish, object: nil,
                userInfo: [AppDelegate.appBackupResultKey: result])
            return [:]
        case "panel":
            let controller = UIHostingController(rootView: AnyView(EmptyView()))
            let content: AnyView
            switch target {
            case "certificates": content = AnyView(CertificatesView(presentingViewController: controller))
            case "developerServices": content = AnyView(DeveloperServicesView(presentingViewController: controller))
            case "connection": content = AnyView(ConnectionConfigView())
            case "anisette": content = AnyView(AnisetteServersView(selected: UserDefaults.standard.menuAnisetteURL, onResetAdiPb: {}))
            case "sideSign": content = AnyView(SideSignConfigurationView())
            case "health": content = AnyView(HealthCheckView())
            case "backups": content = AnyView(BackupAndRestoreView())
            case "sideJIT": content = AnyView(SideJITServerConfigView())
            case "customizations": content = AnyView(UserCustomizationsView())
            case "diagnostics": content = AnyView(DeveloperOptionsView())
            case "experimental": content = AnyView(ExperimentalFeaturesView())
            case "releaseTrack": content = AnyView(V3ReleaseTrackView())
            case "logs":
                guard let delegate = UIApplication.shared.delegate as? AppDelegate else { throw ServiceError.notReady }
                content = AnyView(ConsoleLogView(logURL: delegate.consoleLog.logFileURL))
            default: throw ServiceError.invalidRequest
            }
            // SwiftUI links need navigation; UIKit certificate pushes need the
            // actual hosting controller's navigation controller.
            controller.rootView = AnyView(NavigationView { content }.navigationViewStyle(StackNavigationViewStyle()))
            try await callback { done in
                controller.navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .done, target: self, action: #selector(closePanel))
                let navigation = UINavigationController(rootViewController: controller)
                navigation.isModalInPresentation = true
                self.finishPanel = { done(.success(())) }
                self.cancellations[id] = { self.closePanel() }
                Self.presenter.present(navigation, animated: true)
            }
        case "importPairing":
            _ = try await PairingFileManager.shared.importPairingFile(presentingVC: Self.presenter, title: "Pairing File", message: "Select a pairing file")
        case "catalog":
            let query = NSFetchRequest<StoreApp>(entityName: "StoreApp")
            query.predicate = NSCompoundPredicate(andPredicateWithSubpredicates: [
                StoreApp.visibleAppsPredicate, NSPredicate(format: "sourceIdentifier == %@", target)])
            let offset = request["cursor"] as? Int ?? 0
            query.sortDescriptors = [NSSortDescriptor(key: "name", ascending: true),
                                     NSSortDescriptor(key: "bundleIdentifier", ascending: true)]
            query.fetchOffset = offset
            query.fetchLimit = 51
            let fetched = try context.fetch(query)
            let apps = Array(fetched.prefix(50))
            return ["apps": apps.map { app in
                ["identifier": app.objectID.uriRepresentation().absoluteString,
                 "bundleID": app.bundleIdentifier, "name": app.name,
                 "version": app.latestSupportedVersion?.version ?? "Unavailable",
                 "developer": app.developerName, "description": app.localizedDescription,
                 "iconURL": app.iconURL.absoluteString,
                 "downloadURL": app.latestSupportedVersion?.downloadURL.absoluteString ?? "",
                 "canInstall": app.latestSupportedVersion != nil,
                 "installedID": app.installedApp?.objectID.uriRepresentation().absoluteString ?? "",
                 "installedVersion": app.installedApp?.version] as [String: Any]
            }, "nextCursor": fetched.count > 50 ? offset + 50 : -1]
        case "refreshSources":
            try await callback { done in AppManager.shared.updateAllSources(completion: done) }
        case "addSource":
            guard let url = URL(string: target), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
                  url.host != nil, url.user == nil, url.password == nil else { throw ServiceError.invalidRequest }
            let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
            let source = try await AppManager.shared.fetchSource(sourceURL: url, managedObjectContext: background)
            try await AppManager.shared.add(source, presentingViewController: Self.presenter)
        case "removeSource":
            let query = NSFetchRequest<Source>(entityName: "Source")
            query.predicate = NSPredicate(format: "identifier == %@", target)
            guard let source = try context.fetch(query).first else { throw ServiceError.notFound }
            try await AppManager.shared.remove(source, presentingViewController: Self.presenter)
        case "signIn":
            try await callback { done in
                AppManager.shared.signIn(presentingViewController: Self.presenter) { result in done(result.map { _ in () }) }
            }
        case "signOut":
            // Preserve reusable certificate and anisette state, matching upgrade preservation.
            AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: true)
        case "syncAppIDs":
            if !AuthManager.shared.isAuthenticated {
                _ = try await AuthManager.shared.signIn(presentingViewController: Self.presenter)
            }
            try await callback { done in AppManager.shared.syncAppIDs(completionHandler: done) }
        case "clearCache":
            try await callback { done in AppManager.shared.clearAppCache(completion: done) }
        case "setSetting":
            guard let value = request["value"] as? Bool else { throw ServiceError.invalidRequest }
            switch target {
            case "betaUpdates": UserDefaults.standard.isBetaUpdatesEnabled = value
            case "idleTimeoutDisabled": UserDefaults.standard.isIdleTimeoutDisableEnabled = value
            case "responseCachingDisabled": UserDefaults.standard.responseCachingDisabled = value
            case "verboseOperations": UserDefaults.standard.isVerboseOperationsLoggingEnabled = value
            default: throw ServiceError.invalidRequest
            }
        case "install", "installURL", "installSharedIPA":
            let installTarget: InstallTarget
            var scopedURL: URL?
            defer { scopedURL?.stopAccessingSecurityScopedResource() }
            if operation == "install" {
                let app: StoreApp = try object(target)
                guard app.latestSupportedVersion != nil else { throw ServiceError.unsupported }
                installTarget = .app(app)
            } else if operation == "installSharedIPA" {
                guard UUID(uuidString: target) != nil, let group = Bundle.main.altstoreAppGroup,
                      let defaults = UserDefaults(suiteName: group),
                      let bookmark = defaults.data(forKey: "V3SharedIPA." + target) else { throw ServiceError.invalidRequest }
                defaults.removeObject(forKey: "V3SharedIPA." + target)
                var stale = false
                let url = try URL(resolvingBookmarkData: bookmark, options: .withoutUI, relativeTo: nil, bookmarkDataIsStale: &stale)
                guard !stale, url.isFileURL, url.pathExtension.lowercased() == "ipa" else { throw ServiceError.invalidRequest }
                if url.startAccessingSecurityScopedResource() { scopedURL = url }
                installTarget = .url(url)
            } else {
                guard let url = URL(string: target), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
                      url.host != nil, url.user == nil, url.password == nil else { throw ServiceError.invalidRequest }
                installTarget = .url(url)
            }
            try await callback { done in
                let group = AppManager.shared.install(installTarget, presentingViewController: Self.presenter) { result in done(result.map { _ in () }) }
                cancellations[id] = { group.cancel(); group.progress.cancel() }
            }
        case "refreshApp":
            let app: InstalledApp = try object(target)
            guard app.isActive, app.bundleIdentifier != StoreApp.altstoreAppID else { throw ServiceError.unsupported }
            let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
            let group = RefreshGroup(context: StandaloneOperationContext(steps: .signIn, dbBackgroundContext: background))
            try await callback { done in
                group.completionHandler = { results in
                    guard let result = results[app.bundleIdentifier] else { done(.failure(ServiceError.notFound)); return }
                    done(result.map { _ in () })
                }
                cancellations[id] = { group.cancel(); group.progress.cancel() }
                AppManager.shared.refresh([app], presentingViewController: Self.presenter, group: group)
            }
        case "opStart":
            try await callback { done in
                AppManager.shared.install(.url(URL(string: target)!), presentingViewController: Self.presenter) { result in done(result.map { _ in () }) }
            }
        case "authBegin":
            try await callback { done in
                AppManager.shared.signIn(presentingViewController: Self.presenter) { result in done(result.map { _ in () }) }
            }
        case "authPoll":
            // Poll for auth status - just return current auth state
            let snapshot = try await snapshot()
            return ["state": snapshot["account"] as? String ?? "Not signed in", "team": snapshot["team"] ?? ""]
        case "authRespond":
            // Auth response handling is done via signIn callback
            return [:]
        case "update", "activate", "deactivate", "remove", "delete", "backup", "restore", "jit":
            let app: InstalledApp = try object(target)
            if ["deactivate", "remove", "delete"].contains(operation), app.bundleIdentifier == StoreApp.altstoreAppID { throw ServiceError.unsupported }
            try await callback { done in
                let finished: (Result<InstalledApp, Error>) -> Void = { result in done(result.map { _ in () }) }
                switch operation {
                case "update":
                    let progress = AppManager.shared.update(app, presentingViewController: Self.presenter, completionHandler: finished)
                    cancellations[id] = { progress.cancel() }
                case "activate": AppManager.shared.activate(app, presentingViewController: Self.presenter, completionHandler: finished)
                case "deactivate": AppManager.shared.deactivate(app, presentingViewController: Self.presenter, completionHandler: finished)
                case "remove": AppManager.shared.removeApp(app, presentingViewController: Self.presenter, completionHandler: done)
                case "delete": AppManager.shared.deleteApp(app, presentingViewController: Self.presenter, completionHandler: finished)
                case "backup": AppManager.shared.backup(app, presentingViewController: Self.presenter, completionHandler: finished)
                case "restore": AppManager.shared.restore(app, presentingViewController: Self.presenter, completionHandler: finished)
                default: AppManager.shared.enableJIT(for: app, completionHandler: done)
                }
            }
        default: throw ServiceError.invalidRequest
        }
        return try snapshot()
    }

    private func object<T: NSManagedObject>(_ identifier: String) throws -> T {
        guard let url = URL(string: identifier),
              let id = DatabaseManager.shared.persistentContainer.persistentStoreCoordinator.managedObjectID(forURIRepresentation: url),
              let object = try DatabaseManager.shared.viewContext.existingObject(with: id) as? T else { throw ServiceError.notFound }
        return object
    }

    @objc private func closePanel() {
        let finish = finishPanel
        finishPanel = nil
        Self.presenter.dismiss(animated: true) { finish?() }
    }

    private func callback(_ start: (@escaping (Result<Void, Error>) -> Void) -> Void) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            start { result in continuation.resume(with: result) }
        }
    }

    private func snapshot() throws -> [String: Any] {
        let context = DatabaseManager.shared.viewContext
        let apps = InstalledApp.all(in: context)
        let sources = try context.fetch(NSFetchRequest<Source>(entityName: "Source"))
        let team = DatabaseManager.shared.activeTeam()
        let certificate = CertificateManager.shared.activeCertificate?.certificate.x509
        return ["updatedAt": Date(), "busy": mutationID != nil,
                "account": DatabaseManager.shared.activeAccount()?.appleID ?? "Not signed in",
                "team": team?.name ?? "No active team", "teamID": team?.identifier ?? "",
                "signing": team == nil ? "Sign in required" : "Team selected",
                "certificate": CertificateManager.shared.activeCertificate == nil ? "No active certificate" : "Active certificate available",
                "certificateExpiration": certificate?.expiryDate ?? Date.distantPast,
                "pairing": PairingFileManager.shared.fetchPairingFile() == nil ? "Pairing file required" : "Pairing file available",
                "installedApps": apps.map { app in
                    ["identifier": app.objectID.uriRepresentation().absoluteString,
                     "bundleID": app.bundleIdentifier, "name": app.name, "version": app.version,
                     "isActive": app.isActive, "expirationDate": app.expirationDate,
                     "refreshedDate": app.refreshedDate, "hasUpdate": app.hasUpdate,
                     "certificateStatus": app.certificateStatusRaw ?? "unknown",
                     "openURL": app.openAppURL.absoluteString,
                     "isHost": app.bundleIdentifier == StoreApp.altstoreAppID] as [String: Any]
                },
                "sources": sources.map { source in
                    ["identifier": source.identifier, "name": source.name, "subtitle": source.subtitle ?? "",
                     "url": source.sourceURL.absoluteString, "appCount": source.apps.count,
                     "canRemove": source.identifier != Source.altStoreIdentifier] as [String: Any]
                },
                "settings": ["betaUpdates": UserDefaults.standard.isBetaUpdatesEnabled,
                             "idleTimeoutDisabled": UserDefaults.standard.isIdleTimeoutDisableEnabled,
                             "responseCachingDisabled": UserDefaults.standard.responseCachingDisabled,
                             "verboseOperations": UserDefaults.standard.isVerboseOperationsLoggingEnabled]]
    }
}

private struct V3ReleaseTrackView: View {
    @State private var track = UserDefaults.standard.betaUdpatesTrack ?? UserDefaults.defaultBetaUpdatesTrack
    private var tracks: [String] {
        [track] + ReleaseTrackType.betaTracks.map(\.rawValue).filter { $0 != track }
    }
    var body: some View {
        Form {
            Picker("Beta update channel", selection: $track) {
                ForEach(tracks, id: \.self) { Text($0).tag($0) }
            }
        }.navigationTitle("Update Channel")
            .onChange(of: track) { UserDefaults.standard.betaUdpatesTrack = $0 }
    }
}
