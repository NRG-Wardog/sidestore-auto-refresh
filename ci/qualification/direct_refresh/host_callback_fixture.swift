
import Foundation
struct CombinedFailure: Error {
 enum Stage { case command, refreshVerification }
 enum Code { case missingResult }
 init(operation:String, stage:Stage, code:Code? = nil, id:String, retryable:Bool? = nil) {}
 static func fromEncodedString(_ s:String, expectedID:String) -> CombinedFailure? { nil }
}
enum V3SharedAppGroup {
 static let defaults = UserDefaults(suiteName: "CallbackTests." + UUID().uuidString)!
 static func sharedUserDefaults() -> UserDefaults? { defaults }
}
enum CombinedVerification {
 static var uncertaintyClearCount = 0
 static func sanitized(_ raw:[String:Any], runID:String) -> [String:Any] { raw }
 static func hasCompleteTerminalResults(_ raw:[String:Any], runID:String)->Bool { raw["complete"] as? Bool == true }
 static func clearUncertainty(_ d:UserDefaults, runID:String) { uncertaintyClearCount += 1 }
}
enum V3RefreshEvidencePolicy {
 static func canClearUncertainty(_ raw: [String:Any]?, runID:String)->Bool { raw?["complete"] as? Bool == true }
}
enum V3RefreshTerminalFailurePolicy {
 struct Preserved { let failure:CombinedFailure; let manifest:[String:Any] }
 static func preservedFailure(outer:CombinedFailure, verification:Data?, runID:String, expectedRunID:String?,activeRunID:String?,activeRequestID:String?,record:[String:Any]?) -> Preserved? { nil }
}
// V3_REFRESH_CALLBACK_ADMISSION_V1: a run may contain multiple safe attempts.
enum V3RefreshCallbackIdentityPolicy {
    static func matches(callbackRunID: String, callbackAdmissionID: String,
                        activeRunID: String?, activeAdmissionID: String?) -> Bool {
        guard UUID(uuidString: callbackRunID)?.uuidString == callbackRunID,
              UUID(uuidString: callbackAdmissionID)?.uuidString == callbackAdmissionID else { return false }
        return callbackRunID == activeRunID && callbackAdmissionID == activeAdmissionID
    }
}


@MainActor final class Host {
 var launchID: UUID? = UUID()
 var refreshRunID: String?
 var refreshAdmissionID: String?
 var refreshContinuation: CheckedContinuation<Void, Error>?
 var v3RefreshTerminalCallbackRunID: String?
 var progress: Progress? = Progress(totalUnitCount: 100)
    private func finishRefreshContinuation(_ result: Result<Void, Error>) {
        let pending = refreshContinuation; refreshContinuation = nil; refreshRunID = nil
        pending?.resume(with: result)
    }
    fileprivate func updateProgress(_ value: Double, id: UUID) {
        guard refreshAdmissionID == nil else { return }
        applyRefreshProgress(value, id: id)
    }
    fileprivate func updateHostProgress(_ value: Double, runID: String, admissionID: String, id: UUID) {
        guard refreshRunID == runID, refreshAdmissionID == admissionID else { return }
        applyRefreshProgress(value, id: id)
    }
    private func applyRefreshProgress(_ value: Double, id: UUID) {
        guard launchID == id, refreshContinuation != nil, value.isFinite else { return }
        progress?.completedUnitCount = Int64(max(0, min(1, value)) * 100)
    }
    fileprivate func completedRefresh(_ error: String?, runID: String, verification: Data?, id: UUID) {
        // A legacy callback cannot settle a host-owned admission.
        guard refreshAdmissionID == nil else { return }
        acceptRefreshCompletion(error, runID: runID, verification: verification, id: id)
    }
    fileprivate func completedHostRefresh(_ error: String?, runID: String, admissionID: String, verification: Data?, id: UUID) {
        guard V3RefreshCallbackIdentityPolicy.matches(
            callbackRunID: runID, callbackAdmissionID: admissionID,
            activeRunID: refreshRunID, activeAdmissionID: refreshAdmissionID) else { return }
        acceptRefreshCompletion(error, runID: runID, verification: verification, id: id)
    }
    private func acceptRefreshCompletion(_ error: String?, runID: String, verification: Data?, id: UUID) {
        guard launchID == id, refreshContinuation != nil, refreshRunID == runID else { return }
        v3RefreshTerminalCallbackRunID = runID
        if let error {
            let decoded = CombinedFailure.fromEncodedString(error, expectedID: runID)
            var failure = decoded ?? CombinedFailure(operation: "refresh", stage: .command, id: runID)
            if let defaults = V3SharedAppGroup.sharedUserDefaults() {
                if defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") == runID,
                   defaults.string(forKey: "liveContainerAutoRefreshActiveRunID") == runID,
                   let verification, verification.count <= 262144,
                   let raw = try? PropertyListSerialization.propertyList(from: verification, format: nil) as? [String: Any],
                   let manifest = CombinedVerification.sanitized(raw, runID: runID)["liveContainerAutoRefreshVerification"] as? [String: Any],
                   CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID) {
                    defaults.set(manifest, forKey: "liveContainerAutoRefreshVerification")
                }
                if let decoded,
                   let preserved = V3RefreshTerminalFailurePolicy.preservedFailure(
                    outer: decoded, verification: verification, runID: runID,
                    expectedRunID: defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID"),
                    activeRunID: defaults.string(forKey: "liveContainerAutoRefreshActiveRunID"),
                    activeRequestID: defaults.string(forKey: "liveContainerAutoRefreshActiveRequestID"),
                    record: defaults.dictionary(forKey: "liveContainerAutoRefreshRunLedger")?[runID] as? [String: Any]) {
                    failure = preserved.failure
                    defaults.set(preserved.manifest, forKey: "liveContainerAutoRefreshVerification")
                }
                if V3RefreshEvidencePolicy.canClearUncertainty(defaults.dictionary(forKey: "liveContainerAutoRefreshVerification"), runID: runID) {
                    CombinedVerification.clearUncertainty(defaults, runID: runID)
                }
            }
            finishRefreshContinuation(.failure(failure))
            return
        }
        guard let verification, verification.count <= 262144,
              let rawPayload = try? PropertyListSerialization.propertyList(from: verification, format: nil) as? [String: Any],
              let defaults = V3SharedAppGroup.sharedUserDefaults() else {
            finishRefreshContinuation(.failure(CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID)))
            return
        }
        let payload = CombinedVerification.sanitized(rawPayload, runID: runID)
        guard let manifest = payload["liveContainerAutoRefreshVerification"] as? [String: Any] else {
            finishRefreshContinuation(.failure(CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID, retryable: false)))
            return
        }
        defaults.set(manifest, forKey: "liveContainerAutoRefreshVerification")
        if payload["liveContainerAutoRefreshHostHandoffRunID"] as? String == runID {
            for key in ["liveContainerAutoRefreshHostHandoff", "liveContainerAutoRefreshHostHandoffRunID", "liveContainerAutoRefreshHostHandoffStartedAt", "liveContainerAutoRefreshHostPreviousExpiration"] {
                if let value = payload[key] { defaults.set(value, forKey: key) }
            }
        }
        guard CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID) else {
            finishRefreshContinuation(.failure(CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID)))
            return
        }
        if manifest["host_handoff"] as? Bool != true && !defaults.bool(forKey: "liveContainerAutoRefreshHostHandoff") &&
            V3RefreshEvidencePolicy.canClearUncertainty(manifest, runID: runID) {
            CombinedVerification.clearUncertainty(defaults, runID: runID)
        }
        NSLog("[LIVE_CONTAINER_REFRESH] RESULT_RECEIVED run_id=%@", runID)
        // The existing scheduler evaluates the imported installation evidence; this is command completion only.
        finishRefreshContinuation(.success(()))
    }
    fileprivate func legacyCompletion(_ error: String?, id: UUID) {
        guard refreshAdmissionID == nil else { return }
        guard launchID == id, refreshContinuation != nil, let run = refreshRunID else { return }
        // The legacy callback carries no run ID or verification manifest, so it
        // cannot prove which native run settled. Fail the caller conservatively;
        // admission remains held for explicit reconciliation.
        finishRefreshContinuation(.failure(CombinedFailure.fromEncodedString(error ?? "", expectedID: run) ??
            CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: run)))
    }
 func finishForTest() { finishRefreshContinuation(.failure(CancellationError())) }
}

@main struct Main {
 @MainActor static func main() async throws {
  let run = UUID().uuidString, old = UUID().uuidString, current = UUID().uuidString
  let h = Host(); let launch = h.launchID!
  let data = try PropertyListSerialization.data(fromPropertyList:["liveContainerAutoRefreshVerification":["complete":true]],format:.binary,options:0)
  var resumes = 0
  let task = Task { @MainActor in
   do {
    try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
     h.refreshRunID=run; h.refreshAdmissionID=current; h.refreshContinuation=c
     // Stale same-run old-attempt callbacks and uncorrelated legacy messages cannot settle current attempt.
     h.completedHostRefresh("old",runID:run,admissionID:old,verification:nil,id:launch)
     precondition(h.refreshContinuation != nil && h.v3RefreshTerminalCallbackRunID == nil)
     h.completedRefresh("legacy",runID:run,verification:nil,id:launch)
     h.legacyCompletion("legacy",id:launch)
     precondition(h.refreshContinuation != nil && h.v3RefreshTerminalCallbackRunID == nil)
     h.completedHostRefresh("wrong launch",runID:run,admissionID:current,verification:nil,id:UUID())
     precondition(h.refreshContinuation != nil)
     h.completedHostRefresh("wrong run",runID:UUID().uuidString,admissionID:current,verification:nil,id:launch)
     precondition(h.refreshContinuation != nil)
     h.updateHostProgress(0.75,runID:run,admissionID:old,id:launch)
     precondition(h.progress!.completedUnitCount == 0)
     h.updateHostProgress(0.25,runID:run,admissionID:current,id:launch)
     precondition(h.progress!.completedUnitCount == 25)
     h.completedHostRefresh(nil,runID:run,admissionID:current,verification:data,id:launch)
     precondition(h.refreshContinuation == nil && h.refreshRunID == nil)
     let clears = CombinedVerification.uncertaintyClearCount
     h.completedHostRefresh("late duplicate",runID:run,admissionID:current,verification:nil,id:launch)
     precondition(CombinedVerification.uncertaintyClearCount == clears)
    }
    resumes += 1
   } catch { fatalError("unexpected failure") }
  }
  await task.value; precondition(resumes == 1)
  // Same run can legitimately be retried only under new admission; old callbacks remain inert.
  let next = UUID().uuidString
  let retry = Task { @MainActor in
   do {
    try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
     h.refreshRunID=run; h.refreshAdmissionID=next; h.refreshContinuation=c
     h.v3RefreshTerminalCallbackRunID=nil
     h.completedHostRefresh("late previous",runID:run,admissionID:current,verification:nil,id:launch)
     precondition(h.refreshContinuation != nil && h.v3RefreshTerminalCallbackRunID == nil)
     h.completedHostRefresh("real failure",runID:run,admissionID:next,verification:nil,id:launch)
     precondition(h.refreshContinuation == nil)
    }
    fatalError("failure swallowed")
   } catch { resumes += 1 }
  }
  await retry.value; precondition(resumes == 2)
  // Timeout/cancel removes continuation before a late matching callback arrives.
  let timeout = Task { @MainActor in
   do {
    try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
     h.refreshRunID=run; h.refreshAdmissionID=UUID().uuidString; h.refreshContinuation=c
     h.finishForTest()
     h.completedHostRefresh(nil,runID:run,admissionID:h.refreshAdmissionID!,verification:data,id:launch)
    }
    fatalError("cancel swallowed")
   } catch { resumes += 1 }
  }
  await timeout.value; precondition(resumes == 3)
  precondition(!V3RefreshCallbackIdentityPolicy.matches(callbackRunID:run,callbackAdmissionID:"",activeRunID:run,activeAdmissionID:""))
  print("Production host callback boundary cases passed")
 }
}
