"""Exercise production decoding of host-selected prompt option IDs."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"


HARNESS = r'''
import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

func requireCancelled(_ body: () throws -> Void, _ message: String) {
    do { try body(); fatalError(message) }
    catch is CancellationError { }
    catch { fatalError("\(message): unexpected error \(error)") }
}

@main
struct PromptSelectionHarness {
    static func main() throws {
        let serials = ["SERIAL-A", "SERIAL-B", "SERIAL-C"]
        let bundleIDs = ["com.example.one", "com.example.two", "com.example.three"]

        if case .keepExisting = try V3PromptSelectionPolicy.revocation(
            choice: "keep", submittedOptionIDs: "", offeredSerials: serials) {} else {
            fatalError("keep must preserve the keep-existing decision")
        }
        requireCancelled({
            _ = try V3PromptSelectionPolicy.revocation(
                choice: "cancel", submittedOptionIDs: "revoke:SERIAL-A", offeredSerials: serials)
        }, "cancel must remain cancellation")

        if case .revoke(let all) = try V3PromptSelectionPolicy.revocation(
            choice: "revoke", submittedOptionIDs: "revoke:SERIAL-A,revoke:SERIAL-B,revoke:SERIAL-C",
            offeredSerials: serials) {
            require(all == Set(serials), "all offered certificate option IDs must map to raw serials")
        } else { fatalError("revoke choice must decode") }
        if case .revoke(let subset) = try V3PromptSelectionPolicy.revocation(
            choice: "revoke", submittedOptionIDs: "revoke:SERIAL-C,revoke:SERIAL-A",
            offeredSerials: serials) {
            require(subset == Set(["SERIAL-A", "SERIAL-C"]), "subset must map to exact raw serials")
        } else { fatalError("revoke subset must decode") }

        if case .keepAll = try V3PromptSelectionPolicy.extensions(
            choice: "keepAll", submittedOptionIDs: "", offeredBundleIDs: bundleIDs) {} else {
            fatalError("keepAll must remain available")
        }
        if case .removeAll = try V3PromptSelectionPolicy.extensions(
            choice: "removeAll", submittedOptionIDs: "", offeredBundleIDs: bundleIDs) {} else {
            fatalError("explicit removeAll branch must remain available")
        }
        if case .remove(let all) = try V3PromptSelectionPolicy.extensions(
            choice: "selected", submittedOptionIDs: "remove:com.example.one,remove:com.example.two,remove:com.example.three",
            offeredBundleIDs: bundleIDs) {
            require(all == Set(bundleIDs), "all extension option IDs must map to raw bundle IDs")
        } else { fatalError("selected extensions must decode") }
        if case .remove(let subset) = try V3PromptSelectionPolicy.extensions(
            choice: "selected", submittedOptionIDs: "remove:com.example.three,remove:com.example.one",
            offeredBundleIDs: bundleIDs) {
            require(subset == Set(["com.example.one", "com.example.three"]),
                    "subset must map to exact raw bundle IDs")
        } else { fatalError("extension subset must decode") }

        requireCancelled({
            _ = try V3PromptSelectionPolicy.revocation(
                choice: "revoke", submittedOptionIDs: "delete:SERIAL-A", offeredSerials: serials)
        }, "unknown prefix must be rejected")
        requireCancelled({
            _ = try V3PromptSelectionPolicy.revocation(
                choice: "revoke", submittedOptionIDs: "revoke:FORGED", offeredSerials: serials)
        }, "unoffered raw serial must be rejected")
        requireCancelled({
            _ = try V3PromptSelectionPolicy.extensions(
                choice: "selected", submittedOptionIDs: "remove:com.example.forged", offeredBundleIDs: bundleIDs)
        }, "unoffered raw bundle ID must be rejected")
        requireCancelled({
            _ = try V3PromptSelectionPolicy.extensions(
                choice: "selected", submittedOptionIDs: "remove:com.example.one,remove:com.example.one",
                offeredBundleIDs: bundleIDs)
        }, "duplicate selected option IDs must be rejected")
        requireCancelled({
            _ = try V3PromptSelectionPolicy.revocation(
                choice: "revoke", submittedOptionIDs: "", offeredSerials: serials)
        }, "empty selection must be rejected")
        requireCancelled({
            _ = try V3PromptSelectionPolicy.extensions(
                choice: "selected", submittedOptionIDs: nil, offeredBundleIDs: bundleIDs)
        }, "missing selection must be rejected")

        print("V3_PROMPT_SELECTION_PASS")
    }
}
'''


class V3PromptSelectionTests(unittest.TestCase):
    def test_runtime_adapters_use_wire_ids_and_current_offers(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('submittedOptionIDs: answer["serials"]', runtime)
        self.assertIn('offeredSerials: certificates.map(\\.serialNumber)', runtime)
        self.assertIn('submittedOptionIDs: answer["ids"]', runtime)
        self.assertIn('offeredBundleIDs: sorted.map(\\.bundleIdentifier)', runtime)
        self.assertIn('case "removeAll":', runtime)

    def test_production_policy_decodes_only_exact_offered_option_ids(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        marker = "// V3_PROMPT_OPTION_IDENTITY_V1:"
        start = runtime.index(marker)
        end = runtime.index("\n// MARK: - Authentication failure classification", start)
        production_policy = runtime[start:end]
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; behavioral harness runs in macOS CI")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "main.swift"
            executable = Path(temporary) / "prompt-selection"
            source.write_text("import Foundation\n" + production_policy + "\n" + HARNESS,
                              encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_PROMPT_SELECTION_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
