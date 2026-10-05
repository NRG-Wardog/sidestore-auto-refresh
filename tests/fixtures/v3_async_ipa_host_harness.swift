import Foundation

$STATE$

struct CombinedIPAFileError: Error {
    enum Problem { case fileAccess, stagingFailed }
    let problem: Problem
    init(_ problem: Problem) { self.problem = problem }
}
enum V3FailureGuidance {
    static func message(_ error: CombinedIPAFileError) -> String { "Safe staging failure" }
}
enum LCSharedUtils { static func appGroupID() -> String { "group.test" } }

// An intentionally non-cancellable provider returns after UI ownership has
// changed. Actual host methods must reject both its late success and failure.
actor CopyProvider {
    static let shared = CopyProvider()
    var waiting: [String: CheckedContinuation<String, Error>] = [:]
    var cleaned: Set<String> = []
    func copy(_ key: String) async throws -> String {
        try await withCheckedThrowingContinuation { waiting[key] = $0 }
    }
    func isWaiting(_ key: String) -> Bool { waiting[key] != nil }
    func finish(_ key: String, token: String) { waiting.removeValue(forKey: key)!.resume(returning: token) }
    func fail(_ key: String) { waiting.removeValue(forKey: key)!.resume(throwing: CombinedIPAFileError(.stagingFailed)) }
    func cleanup(_ token: String) { cleaned.insert(token) }
    func didClean(_ token: String) -> Bool { cleaned.contains(token) }
}
enum V3IPAStaging {
    static func sideStoreContainerRoot(selectedGroup: String) -> URL? { URL(fileURLWithPath: "/private-test") }
    static func stageOffMainActor(sourceURL: URL, bookmark: Data?, containerRoot: URL) async throws -> String {
        try await CopyProvider.shared.copy(sourceURL.lastPathComponent)
    }
    static func cleanupUnclaimedOffMainActor(token: String, containerRoot: URL) async {
        precondition(containerRoot.path == "/private-test")
        await CopyProvider.shared.cleanup(token)
    }
}
struct V3OperationRequest {
    let id: UUID
    let operation: String
    let target: String
    let title: String
    let installAttemptID: UUID?
}
@MainActor final class Store {
    var installAttempt = V3InstallAttemptState()
    private var pendingPickerError: (attemptID: UUID, message: String)?
    private var ipaStagingTask: (attemptID: UUID, task: Task<Void, Never>)?
    private var dismissedIPAStagingAttemptID: UUID?
    var loading = false
    var presentation: V3OperationRequest?
    var operationRecoveryDestination: String?
    var error: String?
    func rejectForUnresolvedRecovery() -> Bool { false }
    func cleanupStagedIPA(_ token: String, allowLocalFallback: Bool) async -> Bool {
        await CopyProvider.shared.cleanup(token); return true
    }
    $HOST_METHODS$
}

@main struct AsyncIPAHostHarness {
    @MainActor static func waitUntil(_ predicate: @escaping @MainActor () async -> Bool) async throws {
        for _ in 0..<500 {
            if await predicate() { return }
            try await Task.sleep(nanoseconds: 2_000_000)
        }
        preconditionFailure("condition did not settle")
    }
    @MainActor static func main() async throws {
        let provider = CopyProvider.shared
        let store = Store()
        let input = URL(fileURLWithPath: "/chosen.ipa")
        let old = store.installAttempt.beginPicker()!
        precondition(store.stagePickerIPA(input, attemptID: old))
        store.installPickerDidDisappear(attemptID: old)
        try await waitUntil { await provider.isWaiting("chosen.ipa") }
        // Executed on MainActor while copy is suspended: UI cancellation is
        // immediately available and a replacement same-file attempt can begin.
        precondition(store.installAttempt.phase == .staging)
        store.cancelIPAStaging()
        precondition(store.installAttempt.isIdle)
        let next = store.installAttempt.beginPicker()!
        let oldToken = UUID().uuidString.lowercased()
        await provider.finish("chosen.ipa", token: oldToken)
        try await waitUntil { await provider.didClean(oldToken) }
        precondition(store.installAttempt.attemptID == next && store.error == nil && store.presentation == nil)
        // Old picker callbacks must not reset the current owner.
        store.cancelInstallPicker(attemptID: old)
        store.installPickerDidDisappear(attemptID: old)
        precondition(store.installAttempt.attemptID == next)
        precondition(store.stagePickerIPA(input, attemptID: next))
        store.installPickerDidDisappear(attemptID: next)
        try await waitUntil { await provider.isWaiting("chosen.ipa") }
        let nextToken = UUID().uuidString.lowercased()
        await provider.finish("chosen.ipa", token: nextToken)
        try await waitUntil { store.presentation?.target == nextToken }
        let cleanedActive = await provider.didClean(nextToken)
        precondition(!cleanedActive, "accepted token belongs to backend, never late-result cleanup")

        // Reverse order: a quick copy must wait for the real dismissal signal.
        let fast = Store(); let fastID = fast.installAttempt.beginPicker()!
        precondition(fast.stagePickerIPA(input, attemptID: fastID))
        try await waitUntil { await provider.isWaiting("chosen.ipa") }
        await provider.finish("chosen.ipa", token: UUID().uuidString.lowercased())
        try await waitUntil { fast.installAttempt.phase == .waitingForPickerDismissal }
        precondition(fast.presentation == nil)
        fast.loading = true
        fast.installPickerDidDisappear(attemptID: fastID)
        precondition(fast.installAttempt.phase == .waitingForReload && fast.presentation == nil)
        fast.loading = false; fast.installAttempt.reloadFinished()
        // drain executes normally when the actual store finishes its reload.
        fast.installPickerDidDisappear(attemptID: fastID)
        precondition(fast.installAttempt.phase == .readyToPresentOperation)

        // Provider failure after dismissal is visible, and before dismissal is
        // deferred only until that same picker's dismissal, never forever.
        for dismissFirst in [false, true] {
            let failed = Store(); let id = failed.installAttempt.beginPicker()!
            precondition(failed.stagePickerIPA(input, attemptID: id))
            if dismissFirst { failed.installPickerDidDisappear(attemptID: id) }
            try await waitUntil { await provider.isWaiting("chosen.ipa") }
            await provider.fail("chosen.ipa")
            try await waitUntil { failed.installAttempt.isIdle }
            if !dismissFirst {
                precondition(failed.error == nil)
                failed.installPickerDidDisappear(attemptID: id)
            }
            precondition(failed.error == "Safe staging failure" && failed.presentation == nil)
        }

        let failedOld = Store(); let failedID = failedOld.installAttempt.beginPicker()!
        precondition(failedOld.stagePickerIPA(input, attemptID: failedID))
        try await waitUntil { await provider.isWaiting("chosen.ipa") }
        failedOld.cancelInstallPicker(attemptID: failedID)
        let freshID = failedOld.installAttempt.beginPicker()!
        await provider.fail("chosen.ipa")
        try await Task.sleep(nanoseconds: 20_000_000)
        precondition(failedOld.installAttempt.attemptID == freshID && failedOld.error == nil)

        // A disappearing store also leaves no owner for a late token.
        var gone: Store? = Store(); weak var weakStore = gone
        precondition(gone!.stageSharedIPA(input, title: "Shared IPA"))
        try await waitUntil { await provider.isWaiting("chosen.ipa") }
        gone = nil
        precondition(weakStore == nil, "copy must not retain its UI store")
        let unowned = UUID().uuidString.lowercased()
        await provider.finish("chosen.ipa", token: unowned)
        try await waitUntil { await provider.didClean(unowned) }
        print("V3_ASYNC_IPA_HOST_PASS")
    }
}
