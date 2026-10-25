import Foundation

@MainActor
final class StartupFixture {
    var failAt: CombinedFailure.Stage?
    var launches = 0, retirements = 0
    var events: [String] = []
    lazy var connection: CombinedServiceConnection = CombinedServiceConnection(dependencies: .init(
        resolveHost: { try self.step(.hostContainer); return URL(fileURLWithPath: "/fixture") },
        prepareStorage: { _ in try self.step(.storagePreparation); return URL(fileURLWithPath: "/fixture/Documents/SideStore") },
        createBookmark: { _ in try self.step(.bookmarkCreation); return Data([1]) },
        discoverExtension: { try self.step(.extensionDiscovery) },
        launch: { _, _ in try self.step(.extensionLaunch); self.launches += 1 },
        retire: { _ in self.retirements += 1 }), timeout: 0.15)
    func step(_ stage: CombinedFailure.Stage) throws {
        events.append(stage.rawValue)
        if failAt == stage {
            throw NSError(domain: NSCocoaErrorDomain, code: 513,
                userInfo: [NSLocalizedDescriptionKey: "password=SECRET /private/customer-data", "token": "SECRET"])
        }
    }
    func ready() {
        let id = connection.attemptID!
        connection.signal(.ready, attempt: id)
        connection.signal(.connected, attempt: id)
        connection.signal(.launched, attempt: id)
    }
}
@main
struct StartupTests {
    @MainActor static func wait(_ predicate: () -> Bool) async {
        let until = Date().addingTimeInterval(2)
        while !predicate() { precondition(Date() < until); await Task.yield() }
    }
    @MainActor static func main() async throws {
        for home in [nil, "", "relative", "/", "/tmp/..", "/missing-" + UUID().uuidString] as [String?] {
            do { _ = try CombinedServiceConnection.resolveHost(home); preconditionFailure("bad host accepted") } catch {}
        }
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        defer { try! FileManager.default.removeItem(at: home) }
        let resolved = try CombinedServiceConnection.resolveHost(home.path)
        precondition(resolved == home.standardizedFileURL)
        for stage in [CombinedFailure.Stage.hostContainer, .storagePreparation, .bookmarkCreation, .extensionDiscovery, .extensionLaunch] {
            let f = StartupFixture(); f.failAt = stage
            do { try await f.connection.ensureConnected(); preconditionFailure("startup failure swallowed") }
            catch let failure as CombinedFailure {
                precondition(failure.stage == stage && failure.underlyingCode == 513)
                precondition(!failure.localizedDescription.contains("SECRET"))
                precondition(!failure.localizedDescription.contains("/private"))
                if stage == .extensionDiscovery {
                    precondition(failure.launchContext?.sourceStep == .unknown)
                    precondition(failure.launchContext?.errorChain == [.init(domain: NSCocoaErrorDomain, code: 513)])
                    precondition(failure.launchContext?.requestIdentifierObserved == "unknown" &&
                                 failure.launchContext?.pidObserved == "unknown")
                }
            }
            precondition(!f.connection.isReady && f.connection.attemptID == nil)
            f.failAt = nil
            let retry = Task { try await f.connection.ensureConnected() }
            await wait { f.launches > 0 }; f.ready(); try await retry.value
            precondition(f.connection.isReady, "retry after startup failure failed")
        }
        let f = StartupFixture()
        let a = Task { try await f.connection.ensureConnected() }
        let b = Task { try await f.connection.ensureConnected() }
        await wait { f.launches == 1 && f.connection.waitingCount == 2 }
        let old = f.connection.attemptID!
        a.cancel()
        do { try await a.value; preconditionFailure("cancellation ignored") } catch is CancellationError {} 
        precondition(f.connection.attemptID == old, "one cancelled waiter must not cancel another")
        f.ready(); try await b.value
        f.connection.signal(.ready, attempt: old) // repeated callback must not resume twice
        f.connection.stop()
        let c = Task { try await f.connection.ensureConnected() }
        await wait { f.launches == 2 }
        f.connection.fail(old, CombinedFailure(operation: "connect", stage: .xpcConnection, id: old.uuidString))
        precondition(f.connection.attemptID != nil, "stale failure mutated new launch")
        f.ready(); try await c.value
        let timeout = StartupFixture()
        let hung = Task { try await timeout.connection.ensureConnected() }
        await wait { timeout.launches == 1 }
        timeout.connection.signal(.launched, attempt: timeout.connection.attemptID!)
        do { try await hung.value; preconditionFailure("XPC timeout swallowed") }
        catch let failure as CombinedFailure {
            precondition(failure.code == .timedOut && failure.stage == .xpcConnection)
            precondition(failure.launchContext?.sourceStep == .startupTimeout)
            precondition(failure.launchContext?.requestIdentifierObserved == "yes" && failure.launchContext?.pidObserved == "yes")
            precondition(failure.launchContext?.xpcAccepted == "unknown" && failure.launchContext?.peerPIDRejected == "unknown")
            precondition(failure.localizedDescription.contains("launch_observer_role=host"))
            precondition(failure.localizedDescription.contains("launch_target_role=LiveProcess"))
        }
        let stopBeforeCallback = StartupFixture()
        let cancelledLaunch = Task { try await stopBeforeCallback.connection.ensureConnected() }
        await wait { stopBeforeCallback.launches == 1 }
        stopBeforeCallback.connection.stop(code: .cancelled)
        do { try await cancelledLaunch.value; preconditionFailure("startup cancellation swallowed") }
        catch let failure as CombinedFailure {
            precondition(failure.stage == .extensionLaunch && failure.code == .cancelled)
            precondition(failure.launchContext?.sourceStep == .connectionStopped)
            precondition(failure.launchContext?.requestIdentifierObserved == "unknown")
            precondition(failure.launchContext?.pidObserved == "unknown")
            precondition(failure.launchContext?.xpcAccepted == "unknown")
            precondition(failure.launchContext?.peerPIDRejected == "unknown")
        }
        let cancel = StartupFixture()
        let abandoned = Task { try await cancel.connection.ensureConnected() }
        await wait { cancel.launches == 1 }
        let abandonedID = cancel.connection.attemptID!
        abandoned.cancel(); _ = try? await abandoned.value
        await wait { cancel.connection.attemptID == nil }
        cancel.connection.signal(.ready, attempt: abandonedID)
        precondition(!cancel.connection.isReady)
        let id = UUID().uuidString
        for stage in [CombinedFailure.Stage.authentication, .signing, .installation] {
            let native = NSError(domain: NSCocoaErrorDomain, code: 37,
                userInfo: ["LCStructuredFailureStageV1": stage.rawValue,
                    NSLocalizedDescriptionKey: "SECRET", "token": "SECRET"])
            let failure = CombinedFailure.capture(native, operation: "refresh", stage: .command, id: id)
            precondition(failure.stage == stage && failure.underlyingCode == 37)
            precondition(!failure.localizedDescription.contains("SECRET"))
            precondition(CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)?.stage == stage)
        }
        for stage in CombinedFailure.Stage.allCases {
            let native = NSError(domain: "Private.Secret.Domain", code: 7,
                userInfo: [NSLocalizedDescriptionKey: "lc_stage=\(stage.rawValue) lc_native_code=77 SECRET"])
            let failure = CombinedFailure.capture(native, operation: "refresh", stage: .command, id: id)
            precondition(failure.stage == stage && failure.underlyingDomain == "redacted" &&
                         failure.underlyingCode == 0)
            let text = failure.encodedString
            precondition(!text.contains("SECRET"))
            precondition(CombinedFailure.fromEncodedString(text, expectedID: id)?.stage == stage)
            precondition(CombinedFailure.fromEncodedString(text, expectedID: UUID().uuidString) == nil)
            var bad = failure.wire; bad["password"] = "SECRET"
            precondition(CombinedFailure.decode(bad, expectedID: id) == nil)
            bad = failure.wire; bad["version"] = true
            precondition(CombinedFailure.decode(bad, expectedID: id) == nil)
        }

        // Launch diagnostics exercise the exact CombinedFailure helper used by
        // the host adapter. The nil-ID bridge marker is project-owned and must
        // stay distinct from a genuine Cocoa executable-load error.
        let noIdentifier = NSError(domain: CombinedFailure.LaunchContext.bridgeErrorDomain,
            code: CombinedFailure.LaunchContext.bridgeNoIdentifierCode,
            userInfo: [NSFilePathErrorKey: "/private/customer/data"])
        let noIdentifierFailure = CombinedFailure.LaunchContext.launchFailure(noIdentifier,
            stage: .extensionLaunch, id: id, sourceStep: .requestCallbackNoIdentifier,
            requestIdentifierObserved: false)
        precondition(noIdentifierFailure.underlyingDomain == CombinedFailure.LaunchContext.bridgeErrorDomain &&
                     noIdentifierFailure.underlyingCode == 1)
        precondition(noIdentifierFailure.launchContext?.kind == .unknown)
        precondition(noIdentifierFailure.launchContext?.pidObserved == "unknown" &&
                     noIdentifierFailure.launchContext?.xpcAccepted == "unknown")
        precondition(noIdentifierFailure.technicalDetails.contains("launch_error_chain=io.sidestore.LiveContainer.ExtensionLaunch:1"))
        precondition(noIdentifierFailure.localizedDescription.contains("launch_observer_role=host"))
        precondition(noIdentifierFailure.localizedDescription.contains("launch_target_role=LiveProcess"))
        precondition(noIdentifierFailure.localizedDescription.contains("launch_source_step=requestCallbackNoIdentifier"))
        precondition(!noIdentifierFailure.localizedDescription.contains("3587"))
        precondition(!noIdentifierFailure.localizedDescription.contains("/private"))
        precondition(noIdentifierFailure.wire["launchContext"] == nil, "host-only details entered the service wire")
        precondition(CombinedFailure.decode(noIdentifierFailure.wire, expectedID: id)?.launchContext == nil,
            "a structured wire decode fabricated host observations")
        precondition(noIdentifierFailure.correlating(to: UUID().uuidString).launchContext == noIdentifierFailure.launchContext)
        precondition(CombinedFailure.preserving(noIdentifierFailure, operation: "connect",
            stage: .extensionLaunch, id: id).launchContext == noIdentifierFailure.launchContext)

        let cocoaLoad = NSError(domain: NSCocoaErrorDomain, code: NSExecutableLoadError)
        let realLoadContext = CombinedFailure.LaunchContext(error: cocoaLoad,
            sourceStep: .requestCancellation)
        precondition(realLoadContext.kind == .executableLoadFailure)
        precondition(realLoadContext.errorChain == [.init(domain: NSCocoaErrorDomain, code: NSExecutableLoadError)])

        let innerLoad = NSError(domain: NSCocoaErrorDomain, code: NSExecutableLoadError)
        let outerThree = NSError(domain: NSCocoaErrorDomain, code: 3,
            userInfo: [NSUnderlyingErrorKey: innerLoad, NSFilePathErrorKey: "/private/wrapper"])
        let outerContext = CombinedFailure.LaunchContext(error: outerThree,
            sourceStep: .requestCancellation)
        precondition(outerContext.errorChain.map(\.code) == [3, NSExecutableLoadError])
        precondition(outerContext.kind == .executableLoadFailure)
        let innerThree = NSError(domain: NSCocoaErrorDomain, code: 3)
        let outerLoad = NSError(domain: NSCocoaErrorDomain, code: NSExecutableLoadError,
            userInfo: [NSUnderlyingErrorKey: innerThree])
        let reverseContext = CombinedFailure.LaunchContext(error: outerLoad,
            sourceStep: .requestCancellation)
        precondition(reverseContext.errorChain.map(\.code) == [NSExecutableLoadError, 3])
        precondition(reverseContext.technicalDetails.contains("NSCocoaErrorDomain:3587>NSCocoaErrorDomain:3"))

        let noMetadataContext = CombinedFailure.LaunchContext(error: NSError(domain: NSCocoaErrorDomain, code: 3),
            sourceStep: .extensionFactory)
        precondition(noMetadataContext.kind == .unknown)
        let missingPlugin = CombinedFailure.LaunchContext(sourceStep: .liveProcessBundleMissing)
        precondition(missingPlugin.kind == .extensionNotFound)
        let missingExecutable = CombinedFailure.LaunchContext(sourceStep: .executableFileMissing)
        precondition(missingExecutable.kind == .executableLoadFailure)

        let timeoutFailure = CombinedFailure(operation: "connect", stage: .xpcConnection,
            code: .timedOut, id: id)
        precondition(timeoutFailure.launchContext == nil, "generic construction inferred host observations")
        let peerFailure = CombinedFailure.LaunchContext.launchFailure(NSError(domain: "Private.Peer.Domain", code: 9),
            stage: .xpcConnection, id: id, sourceStep: .xpcRemoteObjectError,
            requestIdentifierObserved: true, pidObserved: true, xpcAccepted: true,
            peerPIDRejected: true)
        precondition(peerFailure.launchContext?.kind == .xpcConnectionFailure)
        precondition(peerFailure.technicalDetails.contains("launch_peer_pid_rejected=yes"))
        let noPeerObservation = CombinedFailure.LaunchContext(sourceStep: .startupTimeout)
        precondition(noPeerObservation.peerPIDRejected == "unknown")

        let priorContext = CombinedFailure.LaunchContext(error: outerThree, sourceStep: .requestCallbackError)
        let priorFailure = CombinedFailure.LaunchContext.launchFailure(outerThree,
            stage: .serviceReadiness, id: id, sourceStep: .requestCallbackError,
            requestIdentifierObserved: true, pidObserved: true, xpcAccepted: true)
        let readinessOwner = CombinedFailure.LaunchContext(error: NSError(domain: NSCocoaErrorDomain, code: 3),
            sourceStep: .readinessProbe,
            requestIdentifierObserved: true, pidObserved: true, xpcAccepted: true,
            applicationReadyObserved: true)
        let enriched = CombinedFailure.preserving(priorFailure, operation: "connect",
            stage: .serviceReadiness, id: id, launchContext: readinessOwner)
        precondition(enriched.launchContext?.sourceStep == .readinessProbe)
        precondition(enriched.launchContext?.applicationReadyObserved == "yes")
        precondition(enriched.launchContext?.errorChain == priorContext.errorChain,
            "owner observations discarded the existing safe cause chain")

        var deep: NSError = NSError(domain: "Private.Path.Domain", code: 99,
            userInfo: [NSFilePathErrorKey: "/private/leaf"])
        for code in stride(from: 5, through: 1, by: -1) {
            deep = NSError(domain: NSCocoaErrorDomain, code: code,
                userInfo: [NSUnderlyingErrorKey: deep, NSFilePathErrorKey: "/private/\(code)"])
        }
        let boundedContext = CombinedFailure.LaunchContext(error: deep,
            sourceStep: .unknown)
        precondition(boundedContext.errorChain.count == 5)
        precondition(boundedContext.errorChain.map(\.code) == [1, 2, 3, 4, 5])
        precondition(boundedContext.technicalDetails.contains(
            "launch_error_chain=NSCocoaErrorDomain:1>NSCocoaErrorDomain:2>NSCocoaErrorDomain:3>NSCocoaErrorDomain:4>NSCocoaErrorDomain:5"),
            "the five-cause bound must retain exactly the observed prefix")
        // The private sixth cause lies outside the bounded traversal. Exercise
        // redaction separately with a private cause inside the observed prefix.
        let privateCause = NSError(domain: "Private.Path.Domain", code: 99,
            userInfo: [NSFilePathErrorKey: "/private/leaf"])
        let redactable = NSError(domain: NSCocoaErrorDomain, code: 3,
            userInfo: [NSUnderlyingErrorKey: privateCause])
        let redactedContext = CombinedFailure.LaunchContext(error: redactable, sourceStep: .unknown)
        precondition(redactedContext.errorChain == [.init(domain: NSCocoaErrorDomain, code: 3),
            .init(domain: "redacted", code: nil)])
        precondition(redactedContext.technicalDetails.contains("NSCocoaErrorDomain:3>redacted:unknown"))
        precondition(!redactedContext.technicalDetails.contains("Private.Path.Domain"))
        precondition(!redactedContext.technicalDetails.contains("/private"))
        precondition(!boundedContext.technicalDetails.contains("Private.Path.Domain"))
        precondition(!boundedContext.technicalDetails.contains("/private"))

        let ordinary = CombinedFailure(operation: "refresh", stage: .serviceReadiness,
            id: id, underlying: outerThree)
        precondition(ordinary.launchContext == nil && !ordinary.technicalDetails.contains("launch_observer_role"))
        precondition(!f.events.contains("refresh") && !f.events.contains("signIn"), "connecting invoked a mutation")
        let unsafe: [String: Any] = ["liveContainerAutoRefreshVerification": ["run_id": id, "expected_ids": ["test.app"],
            "password": "SECRET", "results": [["bundle_id": "test.app", "success": false, "token": "SECRET",
                "error_domain": "SECRET", "error_code": 7, "error": "lc_stage=uniqueDeviceID SECRET"]]], "secret": "SECRET"]
        let safe = CombinedVerification.sanitized(unsafe, runID: id)
        let encoded = try PropertyListSerialization.data(fromPropertyList: safe, format: .xml, options: 0)
        precondition(!String(decoding: encoded, as: UTF8.self).contains("SECRET"))
        precondition(CombinedVerification.sanitized(unsafe, runID: UUID().uuidString).isEmpty)
        print("Combined startup failure, cancellation, concurrency, redaction and reconnect PASS")
    }
}
