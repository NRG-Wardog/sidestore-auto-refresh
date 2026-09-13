import Foundation

@MainActor
final class StartupFixture {
    var failAt: CombinedFailure.Stage?
    var launches = 0, retirements = 0
    var events: [String] = []
    lazy var connection = CombinedServiceConnection(dependencies: .init(
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
        for home in [nil, "", "relative", "/", "/missing-" + UUID().uuidString] as [String?] {
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
        catch let failure as CombinedFailure { precondition(failure.code == .timedOut && failure.stage == .xpcConnection) }
        let cancel = StartupFixture()
        let abandoned = Task { try await cancel.connection.ensureConnected() }
        await wait { cancel.launches == 1 }
        let abandonedID = cancel.connection.attemptID!
        abandoned.cancel(); _ = try? await abandoned.value
        await wait { cancel.connection.attemptID == nil }
        cancel.connection.signal(.ready, attempt: abandonedID)
        precondition(!cancel.connection.isReady)
        let id = UUID().uuidString
        for stage in CombinedFailure.Stage.allCases {
            let native = NSError(domain: "Private.Secret.Domain", code: 7,
                userInfo: [NSLocalizedDescriptionKey: "lc_stage=\(stage.rawValue) lc_native_code=77 SECRET"])
            let failure = CombinedFailure.capture(native, operation: "refresh", stage: .command, id: id)
            precondition(failure.stage == stage && failure.underlyingCode == 77)
            let text = failure.encodedString
            precondition(!text.contains("SECRET"))
            precondition(CombinedFailure.fromEncodedString(text, expectedID: id)?.stage == stage)
            precondition(CombinedFailure.fromEncodedString(text, expectedID: UUID().uuidString) == nil)
            var bad = failure.wire; bad["password"] = "SECRET"
            precondition(CombinedFailure.decode(bad, expectedID: id) == nil)
            bad = failure.wire; bad["version"] = true
            precondition(CombinedFailure.decode(bad, expectedID: id) == nil)
        }
        precondition(!f.events.contains("refresh") && !f.events.contains("signIn"), "connecting invoked a mutation")
        print("Combined startup failure, cancellation, concurrency, redaction and reconnect PASS")
    }
}
