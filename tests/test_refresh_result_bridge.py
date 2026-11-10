"""Current XPC result admission and import, never the retired standalone fragment."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.owner_source import owner_source
from tests.support.product_sources import declaration_end, read_product_source


def declaration(source, signature):
    if source.count(signature) != 1:
        raise AssertionError("Missing or ambiguous production declaration: " + signature)
    start = source.index(signature)
    return source[start:declaration_end(source, start)]


class RefreshResultBridgeTests(unittest.TestCase):
    def test_current_host_import_executes(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift execution requires macOS CI")
        owner = owner_source("SideStoreSupport/SideStore.swift")
        helpers = "\n".join(declaration(owner, signature) for signature in (
            "enum V3RefreshTerminalFailurePolicy {", "enum V3RefreshCallbackIdentityPolicy {"))
        methods = "\n".join(declaration(owner, signature) for signature in (
            "fileprivate func completedRefresh(", "fileprivate func completedHostRefresh(",
            "private func acceptRefreshCompletion("))
        # Only the storage boundary and continuation effect are doubles. The
        # shipped admission, decoding, sanitization and terminal gates run intact.
        source = read_product_source("combined_failure.swift") + "\n" + helpers + r'''
@MainActor enum V3SharedAppGroup { static var defaults: UserDefaults?
    static func sharedUserDefaults() -> UserDefaults? { defaults }
}
@MainActor final class Host {
    let launchID = UUID()
    var refreshRunID: String?
    var refreshAdmissionID: String?
    var refreshContinuation: Bool? = true
    var v3RefreshTerminalCallbackRunID: String?
    var completions = 0
    var lastFailure: Error?
    init(run: String, admission: String) { refreshRunID = run; refreshAdmissionID = admission }
    func finishRefreshContinuation(_ result: Result<Void, Error>) {
        completions += 1; refreshContinuation = nil
        if case .failure(let error) = result { lastFailure = error }
    }
''' + methods + r'''
}
@main struct ResultBridgeHarness {
    @MainActor static func main() throws {
        let suite = "owner-bridge-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        V3SharedAppGroup.defaults = defaults
        let run = UUID().uuidString, admission = UUID().uuidString
        defaults.set(run, forKey: "liveContainerAutoRefreshExpectedRunID")
        defaults.set(run, forKey: "liveContainerAutoRefreshActiveRunID")
        func payload(_ id: String, complete: Bool = true) throws -> Data {
            let manifest: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2",
                "run_id": id, "expected_ids": ["fixture.app"],
                "results": complete ? [["bundle_id": "fixture.app", "success": true]] : []]
            return try PropertyListSerialization.data(fromPropertyList:
                ["liveContainerAutoRefreshVerification": manifest], format: .binary, options: 0)
        }
        let valid = Host(run: run, admission: admission)
        let data = try payload(run)
        valid.completedHostRefresh(nil, runID: run, admissionID: admission, verification: data, id: valid.launchID)
        precondition(valid.completions == 1 && valid.lastFailure == nil)
        precondition(defaults.dictionary(forKey: "liveContainerAutoRefreshVerification")?["run_id"] as? String == run)
        valid.completedHostRefresh(nil, runID: run, admissionID: admission, verification: data, id: valid.launchID)
        precondition(valid.completions == 1)
        for wrong in ["run", "admission", "launch", "legacy"] {
            let host = Host(run: run, admission: admission)
            if wrong == "legacy" { host.completedRefresh(nil, runID: run, verification: data, id: host.launchID) }
            else { host.completedHostRefresh(nil, runID: wrong == "run" ? UUID().uuidString : run,
                admissionID: wrong == "admission" ? UUID().uuidString : admission,
                verification: data, id: wrong == "launch" ? UUID() : host.launchID) }
            precondition(host.completions == 0 && host.refreshContinuation != nil)
        }
        for bad in [try payload(UUID().uuidString), try payload(run, complete: false), Data([0,1,2]), Data(repeating: 0, count: 262145)] {
            let host = Host(run: run, admission: admission)
            host.completedHostRefresh(nil, runID: run, admissionID: admission, verification: bad, id: host.launchID)
            precondition(host.completions == 1 && host.lastFailure != nil)
        }
        let failed = Host(run: run, admission: admission)
        let failure = CombinedFailure(operation: "refresh", stage: .signing, id: run)
        failed.completedHostRefresh(failure.encodedString, runID: run, admissionID: admission, verification: nil, id: failed.launchID)
        precondition((failed.lastFailure as? CombinedFailure)?.stage == .signing)
        V3SharedAppGroup.defaults = nil
        let unavailable = Host(run: run, admission: admission)
        unavailable.completedHostRefresh(nil, runID: run, admissionID: admission, verification: data, id: unavailable.launchID)
        precondition(unavailable.completions == 1 && unavailable.lastFailure != nil)
        print("OWNER_RESULT_BRIDGE_PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "main.swift"
            executable = Path(directory) / "result-tests"
            file.write_text(source)
            built = subprocess.run([compiler, "-parse-as-library", str(file), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("OWNER_RESULT_BRIDGE_PASS", run.stdout)

    def test_result_import_and_dispatch_keep_current_contracts(self):
        host = owner_source("SideStoreSupport/SideStore.swift")
        body = declaration(host, "private func acceptRefreshCompletion(")
        for required in ("guard launchID == id, refreshContinuation != nil, refreshRunID == runID else { return }",
                         "verification.count <= 262144", "V3SharedAppGroup.sharedUserDefaults()",
                         "CombinedVerification.sanitized(rawPayload, runID: runID)",
                         "CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID)",
                         "CombinedVerification.clearUncertainty(defaults, runID: runID)"):
            self.assertIn(required, body)
        self.assertLess(body.index("guard launchID == id"), body.index("defaults.set(manifest"))
        self.assertLess(body.index("defaults.set(manifest"), body.rindex("finishRefreshContinuation(.success"))
        client = owner_source("SideStoreSupport/SideStoreClient.swift")
        self.assertIn("data.count <= 262144", client)
        self.assertIn("verification: nil", client)
        dispatch = owner_source("SideStoreSupport/XPCClient.m")
        self.assertIn("[refreshRunID copy]", dispatch)
        self.assertIn("[server finishHostRefresh:error runID:runID admissionID:dispatchAdmissionID verification:verification]", dispatch)
        self.assertIn("[server updateHostProgress:value runID:runID admissionID:dispatchAdmissionID]", dispatch)
        method = dispatch.split("- (void)refreshAllAppsWithIdentifier:", 1)[1]
        self.assertNotIn("NSUserDefaults", method)
        self.assertNotIn("performRefreshForRealWithIdentifier:", method)


if __name__ == "__main__": unittest.main()
