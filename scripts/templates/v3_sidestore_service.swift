
// V3_SIDESTORE_COMMAND_SERVICE_V1
// Compiled only into SideStore. No managed objects or credentials cross XPC.
// V3_HEADLESS_SERVICE_V2: headless backend. This file owns the command gate,
// snapshots, and non-interactive reads. All interactive work runs through
// V3HeadlessRuntime sessions; no window, presenter, or visible UI exists here.
import SwiftUI

// V3_NATIVE_CALLBACK_GATE_V1: native completions can arrive on arbitrary queues.
// Cancellation does not manufacture a native completion or release the mutation gate.
// The owning service retains it until the real callback returns or the process retires.
final class V3ServiceCallbackGate: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Void, Error>?
    init(_ continuation: CheckedContinuation<Void, Error>) { self.continuation = continuation }
    func settle(_ result: Result<Void, Error>) {
        lock.lock()
        let pending = continuation
        continuation = nil
        lock.unlock()
        pending?.resume(with: result)
    }
}
// V3_NATIVE_CALLBACK_GATE_END

@MainActor
@objc(V3SideStoreService)
final class V3SideStoreService: NSObject {
    static let shared = V3SideStoreService()
    var tasks: [String: Task<Void, Never>] = [:]
    var cancellations: [String: () -> Void] = [:]
    var completed: [String: (data: Data, deadline: Date)] = [:]
    var mutationID: String?

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
            if !V3HeadlessRuntime.shared.cancelSession(target) {
                tasks[target]?.cancel()
                cancellations[target]?()
            }
            reply(encode(["id": id, "version": 1, "ok": true]))
            return
        }
        guard tasks[id] == nil else { reply(encode(["version": 1, "id": id, "error": "busy",
            "failure": CombinedFailure(operation: operation, stage: .command, code: .busy, id: id, retryable: true).wire])); return }
        let mutation = !V3WireContract.readOperations.contains(operation)
        guard !mutation || (mutationID == nil && completed.count < 512) else {
            reply(encode(["version": 1, "id": id, "error": "busy",
                "failure": CombinedFailure(operation: operation, stage: .command, code: .busy, id: id, retryable: true).wire])); return }
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
                response["result"] = try await run(operation, request: request, id: id)
                try Task.checkCancellation()
                response["ok"] = true
            } catch {
                // Raw framework errors can contain URLs, authentication data or server responses.
                // Detailed errors remain inside the SideStore process.
                if let serviceError = error as? ServiceError { response["error"] = serviceError.rawValue }
                else if let headlessError = error as? V3SideStoreServiceError { response["error"] = headlessError.rawValue }
                else if error is CancellationError { response["error"] = "cancelled" }
                else { response["error"] = "operationFailed" }
                let stage: CombinedFailure.Stage
                switch operation {
                case "snapshot": stage = .serviceReadiness
                case "authBegin", "authPoll", "authRespond", "authCancel", "accountExport", "accountImport": stage = .authentication
                case "opStart", "opPoll", "opAnswer", "opCancel": stage = .command
                case "certList", "certSetActive", "certDelete", "certPortalList", "certRevoke", "certCreate": stage = .signing
                case "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles", "syncAppIDs": stage = .authentication
                case "sourcePreview", "sourceAddConfirmed", "sourceRemoveConfirmed": stage = .command
                default: stage = .command
                }
                if let serviceError = error as? ServiceError {
                    let code: CombinedFailure.Code
                    switch serviceError {
                    case .notReady: code = .notReady
                    case .busy: code = .busy
                    case .unsupported: code = .unsupported
                    case .notFound: code = .unavailable
                    case .invalidRequest: code = .invalidConfiguration
                    }
                    response["failure"] = CombinedFailure(operation: operation, stage: stage, code: code, id: id).wire
                } else if let headlessError = error as? V3SideStoreServiceError {
                    let code: CombinedFailure.Code
                    switch headlessError {
                    case .notReady: code = .notReady
                    case .busy: code = .busy
                    case .unsupported: code = .unsupported
                    case .notFound: code = .unavailable
                    case .invalidRequest: code = .invalidConfiguration
                    case .authRequired: code = .notReady
                    case .persistenceUnverified: code = .failed
                    }
                    response["failure"] = CombinedFailure(operation: operation, stage: stage, code: code, id: id).wire
                } else {
                    response["failure"] = CombinedFailure.capture(error, operation: operation, stage: stage, id: id).wire
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
        let payload = request["payload"] as? [String: Any] ?? [:]
        switch operation {
        case "snapshot": return try snapshot()
        case "appIcon":
            let app: InstalledApp = try object(target)
            guard let image = try await app.loadIcon() else { return [:] }
            try Task.checkCancellation()
            let format = UIGraphicsImageRendererFormat()
            format.scale = 1
            let thumbnail = UIGraphicsImageRenderer(size: CGSize(width: 192, height: 192), format: format).image { _ in
                image.draw(in: CGRect(x: 0, y: 0, width: 192, height: 192))
            }
            guard let data = thumbnail.pngData(), data.count <= 262_144 else { return [:] }
            return ["icon": data]
        case "backupResult":
            guard mutationID != nil, ["success", "failure"].contains(target) else { throw ServiceError.invalidRequest }
            let result: Result<Void, Error> = target == "success" ? .success(()) : .failure(ServiceError.unsupported)
            NotificationCenter.default.post(name: AppDelegate.appBackupDidFinish, object: nil,
                userInfo: [AppDelegate.appBackupResultKey: result])
            return [:]
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
        case "signOut":
            // Preserve reusable certificate and anisette state, matching upgrade preservation.
            AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: true)
            return try snapshot()
        case "syncAppIDs":
            if !AuthManager.shared.isAuthenticated {
                throw V3SideStoreServiceError.authRequired
            }
            try await callback { done in AppManager.shared.syncAppIDs(completionHandler: done) }
            return try snapshot()
        case "clearCache":
            try await callback { done in AppManager.shared.clearAppCache(completion: done) }
            return try snapshot()
        case "refreshSources":
            try await callback { done in AppManager.shared.updateAllSources(completion: done) }
            return try snapshot()
        case "jit":
            let app: InstalledApp = try object(target)
            try await callback { done in AppManager.shared.enableJIT(for: app, completionHandler: done) }
            return try snapshot()
        case "authBegin":
            guard let deadline = request["deadline"] as? Date else { throw ServiceError.invalidRequest }
            return await V3HeadlessRuntime.shared.auth.begin(deadline: deadline)
        case "authPoll":
            guard let reply = V3HeadlessRuntime.shared.auth.poll(id: target) else { throw ServiceError.invalidRequest }
            return reply
        case "authRespond":
            guard let answer = payload["answer"] as? [String: String],
                  let promptID = payload["prompt"] as? String,
                  let reply = V3HeadlessRuntime.shared.auth.respond(id: target, promptID: promptID, answer: answer) else {
                throw ServiceError.invalidRequest
            }
            return reply
        case "authCancel":
            guard await V3HeadlessRuntime.shared.auth.cancelAndWait(id: target) else { throw ServiceError.invalidRequest }
            return [:]
        case "opStart":
            guard let kind = payload["kind"] as? String,
                  let session = payload["session"] as? String,
                  let deadline = request["deadline"] as? Date else { throw ServiceError.invalidRequest }
            let opTarget = payload["target"] as? String ?? target
            return await V3HeadlessRuntime.shared.operations.start(kind: kind, target: opTarget,
                value: payload["value"] as? Bool, sessionID: session, deadline: deadline)
        case "opPoll":
            guard let reply = V3HeadlessRuntime.shared.operations.poll(id: target) else { throw ServiceError.invalidRequest }
            return reply
        case "opAnswer":
            guard let answer = payload["answer"] as? [String: String],
                  let promptID = payload["prompt"] as? String,
                  let reply = V3HeadlessRuntime.shared.operations.answer(id: target, promptID: promptID, answer: answer) else {
                throw ServiceError.invalidRequest
            }
            return reply
        case "opCancel":
            guard await V3HeadlessRuntime.shared.operations.cancelAndWait(id: target) else { throw ServiceError.invalidRequest }
            return [:]
        case "ipaCleanup":
            try V3HeadlessRuntime.shared.operations.cleanupIPA(token: target)
            return [:]
        case "certList":
            return ["certificates": V3BackendCommands.certificates()]
        case "certSetActive":
            guard let certificate = CertificateManager.shared.getLocalCertificate(serialNumber: target) else {
                throw ServiceError.notFound
            }
            try CertificateManager.shared.setActiveCertificate(certificate)
            return try snapshot()
        case "certDelete":
            CertificateManager.shared.deleteCertificate(serialNumber: target)
            return try snapshot()
        case "certPortalList":
            return ["certificates": try await V3BackendCommands.portalCertificates()]
        case "certRevoke":
            _ = try await AuthManager.shared.getAuthenticatedSession()
            let team = try await AuthManager.shared.getAuthenticatedTeam()
            let certificates = try await DeveloperPortalProxy.shared.fetchCertificates(team: team)
            guard let certificate = certificates.first(where: { $0.serialNumber == target }) else {
                throw ServiceError.notFound
            }
            _ = try await DeveloperPortalProxy.shared.revokeCertificate(certificate, team: team)
            return try snapshot()
        case "certCreate":
            _ = try await AuthManager.shared.getAuthenticatedSession()
            let team = try await AuthManager.shared.getAuthenticatedTeam()
            let name = UIDevice.current.name
            let created = try await DeveloperPortalProxy.shared.createCertificate(
                machineName: "SideStore - \(team.name)'s \(name)", team: team)
            CertificateManager.shared.saveCertificate(created)
            if let local = CertificateManager.shared.getLocalCertificate(serialNumber: created.serialNumber) {
                try? CertificateManager.shared.setActiveCertificate(local)
            }
            return try snapshot()
        case "devTeams":
            return ["teams": try await V3BackendCommands.developerTeams()]
        case "devDevices":
            return ["devices": try await V3BackendCommands.developerDevices()]
        case "devAppIDs":
            return ["appIDs": try await V3BackendCommands.developerAppIDs()]
        case "devGroups":
            return ["groups": try await V3BackendCommands.developerGroups()]
        case "devProfiles":
            return ["profiles": try await V3BackendCommands.developerProfiles()]
        case "sourcePreview":
            return try await V3BackendCommands.sourcePreview(urlString: target)
        case "sourceAddConfirmed":
            let addResult = try await V3BackendCommands.sourceAddConfirmed(urlString: target)
            var updated = try snapshot()
            let persistedSources = try await V3BackendCommands.authoritativeSourceRows()
            let sourceID = addResult["identifier"] as? String ?? ""
            guard persistedSources.contains(where: { $0["identifier"] as? String == sourceID }) else {
                throw V3SideStoreServiceError.persistenceUnverified
            }
            updated["sources"] = persistedSources
            return updated.merging(addResult) { _, authoritative in authoritative }
        case "sourceRemoveConfirmed":
            try await V3BackendCommands.sourceRemoveConfirmed(identifier: target)
            return try snapshot()
        case "pairingImportData":
            try V3BackendCommands.pairingImportData(token: target)
            return try snapshot()
        case "settingsGet":
            return V3BackendCommands.settingsGet()
        case "settingsSet":
            try V3BackendCommands.settingsSet(payload: payload)
            return try snapshot()
        case "anisetteList":
            return ["servers": await V3BackendCommands.anisetteList()]
        case "anisetteReset":
            _ = try await AnisetteServersManager.shared.resetToOriginalState()
            return ["servers": await V3BackendCommands.anisetteList()]
        case "anisetteSync":
            _ = try await AnisetteServersManager.shared.syncWithRemote()
            return ["servers": await V3BackendCommands.anisetteList()]
        case "sidesignGet":
            return ["config": await V3BackendCommands.sidesignJSON()]
        case "sidesignSet":
            guard let json = payload["config"] as? String else { throw ServiceError.invalidRequest }
            try await V3BackendCommands.sidesignSet(json: json)
            return ["config": await V3BackendCommands.sidesignJSON()]
        case "sidesignReset":
            _ = SideSignConfigManager.shared.resetToDefaults()
            return ["config": await V3BackendCommands.sidesignJSON()]
        case "sidesignImport":
            try await V3BackendCommands.sidesignImport(token: target)
            return ["config": await V3BackendCommands.sidesignJSON()]
        case "sidesignExport":
            return ["config": await V3BackendCommands.sidesignExport()]
        case "logTail":
            return V3BackendCommands.logTail()
        case "healthSnapshot":
            return await V3BackendCommands.health()
        case "accountExport":
            guard let password = payload["password"] as? String, !password.isEmpty else {
                throw ServiceError.invalidRequest
            }
            let includeApple = payload["includeApple"] as? Bool ?? false
            return ["backup": try V3BackendCommands.accountExport(password: password, includeApplePassword: includeApple)]
        case "accountImport":
            guard let password = payload["password"] as? String else { throw ServiceError.invalidRequest }
            return try V3BackendCommands.accountImport(token: target, password: password)
        default: throw ServiceError.invalidRequest
        }
    }

    private func object<T: NSManagedObject>(_ identifier: String) throws -> T {
        guard let url = URL(string: identifier),
              let id = DatabaseManager.shared.persistentContainer.persistentStoreCoordinator.managedObjectID(forURIRepresentation: url),
              let object = try DatabaseManager.shared.viewContext.existingObject(with: id) as? T else { throw ServiceError.notFound }
        return object
    }

    // Native callbacks may fire more than once or race on arbitrary queues.
    // The first terminal result wins; late callbacks are ignored. Cancellation
    // never releases the continuation early: the task keeps awaiting the
    // native terminal callback so the service mutation gate is not freed early.
    final class V3ServiceCallbackGate {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<Void, Error>?
        private var settled = false
        init(_ continuation: CheckedContinuation<Void, Error>) {
            self.continuation = continuation
        }
        func settle(_ result: Result<Void, Error>) {
            lock.lock()
            let pending = continuation
            let first = !settled
            settled = true
            continuation = nil
            lock.unlock()
            if first, let pending = pending {
                pending.resume(with: result)
            }
        }
    }
    // V3_NATIVE_CALLBACK_GATE_END

    private func callback(_ start: (@escaping (Result<Void, Error>) -> Void) -> Void) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let gate = V3ServiceCallbackGate(continuation)
            start { result in gate.settle(result) }
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
