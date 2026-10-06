"""The auth prompt must never claim an unperformed host re-sign succeeded."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"


def adapter():
    source = RUNTIME.read_text(encoding="utf-8")
    start = source.index("    func resolveResign(mismatchReason:")
    end = source.index("    func complete()", start)
    return source[start:end]


HARNESS = r'''
import Foundation

enum CodeSignValidationReason { case revoked }
struct StandaloneOperationContext {}

@MainActor
final class FixtureHandler {
    var answer: [String: String] = ["choice": "continue"]
    var cancelParkedPrompt = false
    var options: [[String: String]] = []
    var message = ""

    func ask(kind: String, title: String, message: String,
             options: [[String: String]]) async throws -> [String: String] {
        precondition(kind == "resign")
        self.message = message
        self.options = options
        if cancelParkedPrompt { throw CancellationError() }
        return answer
    }

    // PRODUCTION_ADAPTER
}

@main
struct ResignAcknowledgmentHarness {
    @MainActor
    static func main() async throws {
        let handler = FixtureHandler()
        let didResign = try await handler.resolveResign(mismatchReason: .revoked,
                                                       context: StandaloneOperationContext())
        precondition(!didResign, "Acknowledging setup must never claim that the host was re-signed")
        precondition(handler.options == [["id": "continue", "label": "Finish Sign-In"]])
        precondition(handler.message.contains("Refresh All") && handler.message.contains("Test Refresh"))
        precondition(handler.message.contains("Sign-in alone does not re-sign"))

        for answer in [["choice": "cancel"], ["choice": "proceed"], ["choice": "forged"], [:]] {
            handler.answer = answer
            do {
                _ = try await handler.resolveResign(mismatchReason: .revoked,
                                                   context: StandaloneOperationContext())
                fatalError("Only an explicit acknowledgment can advance this prompt")
            } catch is CancellationError {} catch { throw error }
        }

        handler.answer = ["choice": "continue"]
        handler.cancelParkedPrompt = true
        do {
            _ = try await handler.resolveResign(mismatchReason: .revoked,
                                               context: StandaloneOperationContext())
            fatalError("Session-owned cancellation must propagate out of the prompt adapter")
        } catch is CancellationError {} catch { throw error }
        print("V3_RESIGN_ACKNOWLEDGMENT_PASS")
    }
}
'''


class ResignAcknowledgmentTests(unittest.TestCase):
    def test_adapter_returns_completion_fact_not_user_permission(self):
        method = adapter()
        self.assertIn('"label": "Finish Sign-In"', method)
        self.assertIn("Refresh All or Test Refresh", method)
        self.assertIn('guard answer["choice"] == "continue" else { throw CancellationError() }', method)
        self.assertIn("return false", method)
        self.assertNotIn('"label": "Re-sign"', method)
        self.assertNotIn('return answer["choice"] == "proceed"', method)
        self.assertNotIn("AppManager.shared", method)

    def test_production_adapter_preserves_cancellation_and_never_reports_resign(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; production resign acknowledgment runs in macOS CI")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.swift"
            executable = Path(folder) / "resign-acknowledgment"
            source.write_text(HARNESS.replace("    // PRODUCTION_ADAPTER", adapter()), encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_RESIGN_ACKNOWLEDGMENT_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
