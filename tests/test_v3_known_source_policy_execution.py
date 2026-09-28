import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"


def source_text():
    return SERVICE.read_text(encoding="utf-8")


class KnownSourcePolicyExecutionTests(unittest.TestCase):
    def test_posix_is_preserved_for_diagnostics_but_not_used_as_network_proof(self):
        source = source_text()
        start = source.index("private struct V3KnownSourcePolicyFailure: Error {")
        end = source.index("\n}\n\n@MainActor", start)
        classifier = source[start:end]
        kind_rule = classifier[classifier.index("kind = "):classifier.index("underlyingDomain = ")]
        self.assertIn("NSURLErrorDomain", kind_rule)
        self.assertIn('"CFNetwork"', kind_rule)
        self.assertNotIn("NSPOSIXErrorDomain", kind_rule)
        self.assertIn("NSPOSIXErrorDomain", classifier[classifier.index("underlyingDomain = "):])

    def test_actual_classifier_keeps_enotdir_local_and_url_error_network(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift execution runs in macOS CI")

        source = source_text()
        start = source.index("private struct V3KnownSourcePolicyFailure: Error {")
        end = source.index("\n}\n\n@MainActor", start) + 2
        production_classifier = source[start:end]
        harness = """
import Foundation

""" + production_classifier + """

@main struct Tests {
    static func main() {
        // Darwin ENOTDIR: a local path error, even if a message mentions I/O.
        let local = NSError(domain: NSPOSIXErrorDomain, code: 20,
            userInfo: [NSLocalizedDescriptionKey: "not a directory"])
        let localFailure = V3KnownSourcePolicyFailure(local)
        precondition(localFailure.kind == .invalidResponse)
        precondition(localFailure.underlyingDomain == NSPOSIXErrorDomain)
        precondition(localFailure.underlyingCode == 20)

        // A typed URL-loading failure remains a network failure.
        let urlFailure = V3KnownSourcePolicyFailure(URLError(.timedOut))
        precondition(urlFailure.kind == .network)
        precondition(urlFailure.underlyingDomain == NSURLErrorDomain)
        precondition(urlFailure.underlyingCode == URLError.timedOut.rawValue)
        print("Known source policy classification PASS")
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            swift = Path(directory) / "known_source_policy.swift"
            executable = Path(directory) / "known_source_policy"
            swift.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(
                [compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=15
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Known source policy classification PASS", result.stdout)

    def test_service_maps_classifier_kind_to_fetch_or_parsing_guidance(self):
        source = source_text()
        start = source.index("} else if let policyError = error as? V3KnownSourcePolicyFailure {")
        block = source[start:source.index("} else if let sourceError", start)]
        self.assertIn("let network = policyError.kind == .network", block)
        self.assertIn("network ? .knownSourcePolicyNetworkFailure : .knownSourcePolicyInvalidResponse", block)
        self.assertIn("network ? .knownSourcePolicyFetch : .knownSourcePolicyParsing", block)


if __name__ == "__main__":
    unittest.main()
