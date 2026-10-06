"""Home must require actual scheduler verification, not any result dictionary."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"


HARNESS = r'''
@main
struct HomeVerifiedRefreshHarness {
    static func main() throws {
        let runID = "00000000-0000-0000-0000-000000000001"
        let otherRunID = "00000000-0000-0000-0000-000000000002"
        let expected = ["com.example.host", "com.example.app"]
        let manifest: [String: Any] = [
            "version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": runID,
            "expected_ids": expected, "requested_ids": expected, "skipped_ids": [String](),
            "host_handoff": true,
            "results": expected.map { ["bundle_id": $0, "success": true] as [String: Any] }
        ]
        let summary: [String: Any] = [
            "version": 2, "schema": "LiveContainerRefreshManifestSummaryV2", "run_id": runID,
            "verified": true, "expected_count": 2, "result_count": 2, "failed_count": 0,
            "skipped_count": 0, "requested_count": 2
        ]
        let record: [String: Any] = [
            "run_id": runID, "state": "completed", "terminal_intent": "verified",
            "health": "REFRESH_SUCCEEDED", "manifest_run_id": runID, "manifest_summary": summary
        ]
        func accepts(_ value: [String: Any]?, _ entry: [String: Any],
                     active: String? = nil, handoff: Bool = false, uncertain: String? = nil) -> Bool {
            V3HomeRefreshVerificationPolicy.isVerified(manifest: value, ledger: [runID: entry],
                activeRunID: active, hostHandoffPending: handoff, uncertainMutationRunID: uncertain)
        }
        precondition(accepts(manifest, record), "A settled, covered, successful run must satisfy Home")
        precondition(accepts(manifest, record),
                     "Historical host_handoff=true remains valid once the scheduler settled and cleared pending ownership")
        precondition(!accepts(nil, record), "Missing evidence is never success")
        precondition(!accepts(["run_id": runID], record), "A run ID alone must never count as verified")
        precondition(!accepts(manifest, [:]), "A manifest without scheduler verification cannot count")
        precondition(!accepts(manifest, record, active: runID) && !accepts(manifest, record, active: otherRunID))
        precondition(!accepts(manifest, record, handoff: true), "Pending host replacement is not verified")
        precondition(!accepts(manifest, record, uncertain: runID) && !accepts(manifest, record, uncertain: otherRunID))

        for (key, value) in [
            ("state", "failed"), ("state", "verifying"), ("terminal_intent", "failed"),
            ("health", "REFRESH_FAILED"), ("run_id", otherRunID), ("manifest_run_id", otherRunID)
        ] {
            var bad = record; bad[key] = value
            precondition(!accepts(manifest, bad), "Mismatched or non-successful ledger state must stay incomplete")
        }
        for key in ["manifest_summary", "health", "terminal_intent", "manifest_run_id"] {
            var bad = record; bad.removeValue(forKey: key)
            precondition(!accepts(manifest, bad), "Missing scheduler evidence must fail closed")
        }
        var badSummary = summary
        badSummary["verified"] = false
        var badRecord = record; badRecord["manifest_summary"] = badSummary
        precondition(!accepts(manifest, badRecord))

        var failed = manifest
        failed["results"] = [["bundle_id": expected[0], "success": true],
                             ["bundle_id": expected[1], "success": false]]
        precondition(!accepts(failed, record), "Even a forged successful ledger cannot turn a failed app result into success")
        var partial = manifest
        partial["results"] = [["bundle_id": expected[0], "success": true]]
        precondition(!accepts(partial, record), "Every expected app must have a terminal result")
        var duplicate = manifest
        duplicate["results"] = [["bundle_id": expected[0], "success": true],
                                ["bundle_id": expected[0], "success": true]]
        precondition(!accepts(duplicate, record))
        var numericSuccess = manifest
        numericSuccess["results"] = expected.map { ["bundle_id": $0, "success": 1] as [String: Any] }
        precondition(!accepts(numericSuccess, record), "Integer one is not a plist success boolean")

        var wrongRun = manifest; wrongRun["run_id"] = otherRunID
        precondition(!accepts(wrongRun, record))
        var differentCounts = manifest
        differentCounts["skipped_ids"] = ["com.example.skipped"]
        differentCounts["requested_ids"] = expected + ["com.example.skipped"]
        precondition(!accepts(differentCounts, record), "Manifest and scheduler summary counts must agree")
        let data = try PropertyListSerialization.data(fromPropertyList: manifest, format: .binary, options: 0)
        let decoded = try PropertyListSerialization.propertyList(from: data, format: nil) as! [String: Any]
        precondition(accepts(decoded, record), "Real plist bridging must preserve verified evidence")
        print("V3_HOME_VERIFIED_REFRESH_PASS")
    }
}
'''


class HomeVerifiedRefreshTests(unittest.TestCase):
    def test_home_uses_settled_verification_policy_and_current_ownership(self):
        shell = SHELL.read_text(encoding="utf-8")
        source = shell[shell.index("struct V3HomeView"):]
        start = source.index("static func completionInputs(")
        end = source.index("    var body:", start)
        method = source[start:end]
        self.assertIn("V3HomeRefreshVerificationPolicy.isVerified(", method)
        for key in ("liveContainerAutoRefreshVerification", "liveContainerAutoRefreshRunLedger",
                    "liveContainerAutoRefreshActiveRunID", "liveContainerAutoRefreshHostHandoff",
                    "liveContainerAutoRefreshUncertainMutationRunID"):
            self.assertIn(key, method)
        self.assertIn("verifiedRefreshPresent: verifiedRefresh", method)
        self.assertNotIn("verifiedRunID?.isEmpty", method)

    def test_production_policy_rejects_failed_partial_pending_and_mismatched_evidence(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; Home refresh evidence harness runs in macOS CI")
        primitives = PRIMITIVES.read_text(encoding="utf-8")
        start = primitives.index("enum V3RefreshAllTerminalEvidencePolicy {")
        end = primitives.index("enum V3SetupRefreshTerminalOutcome:", start)
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.swift"
            executable = Path(folder) / "home-verified-refresh"
            source.write_text(failure + "\n" + primitives[start:end] + "\n" + HARNESS, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_HOME_VERIFIED_REFRESH_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
