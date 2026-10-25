"""Execute user recovery copy from the production CombinedFailure helper."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CombinedFailureRecoveryTests(unittest.TestCase):
    def execute(self, body):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift execution runs in macOS CI")
        common = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recovery.swift"
            binary = Path(directory) / "recovery-test"
            source.write_text(common + body, encoding="utf-8")
            result = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(binary)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)

    def test_unknown_command_failure_uses_neutral_status_and_diagnostics_guidance(self):
        self.execute(r'''
func debugLog(_ value: String) {}
@main struct Tests {
 static func main() {
  let failure = CombinedFailure.capture(
      NSError(domain: "com.example.unknown", code: 73),
      operation: "refresh", stage: .command, id: UUID().uuidString)
  precondition(failure.stage == .command)
  precondition(failure.safeCause == nil)
  precondition(failure.recovery.contains("Reload the current status"))
  precondition(failure.recovery.contains("copy Diagnostics"))
  precondition(!failure.recovery.localizedCaseInsensitiveContains("reconnect"))
  precondition(!failure.recovery.contains("LocalDevVPN"))
  print("Unknown failure guidance PASS")
 }
}
''')

    def test_typed_network_and_reply_encoding_failures_keep_their_specific_guidance(self):
        self.execute(r'''
func debugLog(_ value: String) {}
@main struct Tests {
 static func main() {
  let id = UUID().uuidString
  let network = CombinedFailure.capture(
      NSError(domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost),
      operation: "refresh", stage: .command, id: id)
  precondition(network.stage == .network)
  precondition(network.safeCause == .networkConnectionLost)
  precondition(network.recovery.localizedCaseInsensitiveContains("network used by this request"))
  precondition(network.recovery.localizedCaseInsensitiveContains("then retry when the connection is stable"))
  precondition(!network.recovery.localizedCaseInsensitiveContains("localdevvpn"),
      "a transient request failure does not claim persistent tunnel failure")

  let coreDevice = CombinedFailure(operation: "refresh", stage: .coreDevice,
      code: .failed, id: id)
  precondition(coreDevice.recovery.contains("device connection"))
  precondition(coreDevice.recovery.contains("LocalDevVPN"))
  let tunnel = CombinedFailure(operation: "refresh", stage: .cdTunnel,
      code: .failed, id: id)
  precondition(tunnel.recovery.contains("device connection"))
  precondition(tunnel.recovery.contains("LocalDevVPN"))

  let encoding = CombinedFailure(operation: "catalog", stage: .replyEncoding,
      code: .invalidResponse, id: id, retryable: false, safeCause: .responseEncodingFailed)
  precondition(encoding.recovery.contains("same request cannot fix"))
  precondition(!encoding.recovery.localizedCaseInsensitiveContains("reconnect"))
  let oversized = CombinedFailure(operation: "catalog", stage: .replyEncoding,
      code: .invalidResponse, id: id, retryable: false, safeCause: .responseTooLarge)
  precondition(oversized.recovery.contains("exceeded the transfer limit"))
  precondition(!oversized.recovery.localizedCaseInsensitiveContains("reconnect"))
  print("Typed recovery guidance PASS")
 }
}
''')
