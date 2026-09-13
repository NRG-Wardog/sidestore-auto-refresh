"""Execute the generated transport/query error path, not a parallel Python model."""
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch as mock
from test_combined_transport import SourceFixture, function
from patch_combined_transport import patch
from patch_combined_service_startup import patch_transport

ROOT = Path(__file__).resolve().parents[1]


class ErrorExecutionTests(SourceFixture):
    def execute(self, source):
        compiler = shutil.which('swiftc')
        if not compiler: self.skipTest('Swift execution runs in macOS CI')
        path = Path(self.temp.name) / 'failure.swift'
        path.write_text(source, encoding='utf-8')
        exe = path.with_suffix('')
        result = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(exe)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS', result.stdout)

    def test_actual_gateway_mapping_retains_categories(self):
        patch(self.mux)
        method = function(self.read('Sources/MinimuxerImpl.swift'), 'runIdeviceCheckingVPN').replace('private func', 'func')
        native = self.read('DeviceGateway/DeviceGatewayError.swift')
        errors = self.read('Sources/MinimuxerError.swift')
        errors = errors[errors.index('public enum MinimuxerError:'):]
        source = native + '\npublic enum PairingProtocol: Equatable { case lockdown }\n' + errors + '''
struct Gateway { var pairingFileType = PairingProtocol.lockdown; var coreDeviceTransportEnabled = false }
struct Adapter { let gateway = Gateway()
''' + method + '''
}
@main struct Tests {
 static func main() async throws {
  let adapter = Adapter()
  let cases: [(DeviceGatewayError.Code, String, MinimuxerError)] = [
   (.invalidPairingFile, "parse", .invalidPairing(protocol: .lockdown, reason: "parse")),
   (.connectionFailed, "CoreDevice failed", .createCoreDevice("CoreDevice failed")),
   (.connectionFailed, "heartbeat inactive", .connect("heartbeat inactive")),
   (.connectionFailed, "transport failed", .noDevice("transport failed")),
   (.serviceError, "stage=rsd_service failed", .noService("stage=rsd_service failed")),
   (.serviceError, "Lockdown failed", .createLockdown("Lockdown failed")),
   (.serviceError, "UniqueDeviceID failed", .getLockdownValue("UniqueDeviceID failed"))]
  for (code, reason, expected) in cases {
   do { let _: String? = try await adapter.runIdeviceCheckingVPN("fetching device UDID", fallback: nil) {
      throw DeviceGatewayError(code, reason: reason)
    }; preconditionFailure("error became nil")
   } catch { precondition(error == expected, "wrong category: \\(error)") }
  }
  let success: String? = try await adapter.runIdeviceCheckingVPN("fetching device UDID", fallback: nil) { "live-response" }
  precondition(success == "live-response")
  print("Actual gateway mapping PASS")
 }
}
'''
        self.execute(source)

    def test_actual_query_captures_error_before_free_and_sanitizes(self):
        patch(self.mux)
        with mock('patch_combined_service_startup.subprocess.check_output', return_value='98c3c79982f813878e922ab42f9545314a700f0c'):
            patch_transport(self.mux)
        source = self.gateway.read_text(encoding='utf-8')
        query = function(source, 'syncFetchUDID')
        query = query[query.index('            var plistVal:'):query.index('            return udid') + len('            return udid')]
        common = (ROOT / 'scripts/templates/combined_failure.swift').read_text(encoding='utf-8')
        native = self.read('DeviceGateway/DeviceGatewayError.swift')
        self.execute(common + native + '''
typealias IdeviceGatewayError = DeviceGatewayError
typealias plist_t = OpaquePointer
struct NativeError { var code: Int32; var sub_code: Int32 }
var mode = 0, frees = 0, plistFrees = 0
func debugLog(_ value: String) { precondition(!value.contains("SECRET")) }
func verboseLog(_ value: String) {}
func lockdownd_get_value(_ client: OpaquePointer, _ key: String, _ domain: String?, _ value: inout plist_t?) -> UnsafeMutablePointer<NativeError>? {
 if mode == 0 { let p = UnsafeMutablePointer<NativeError>.allocate(capacity: 1); p.initialize(to: .init(code: 77, sub_code: 9)); return p }
 if mode != 1 { value = OpaquePointer(bitPattern: 3) }; return nil
}
func getErrorMessage(from pointer: UnsafeMutablePointer<NativeError>) -> String { precondition(frees == 0); return "SECRET" }
func safeFreeError(_ pointer: UnsafeMutablePointer<NativeError>) { frees += 1; pointer.deinitialize(count: 1); pointer.deallocate() }
func safeFreePlist(_ value: plist_t) { plistFrees += 1 }
func getRustPlistString(_ value: plist_t) -> String? { mode == 2 ? "" : "live-response" }
func query() throws -> String {
 let client = OpaquePointer(bitPattern: 1)!
''' + query + '''
}
@main struct Tests {
 static func main() throws {
  let id = UUID().uuidString
  for value in 0...2 {
   mode = value
   do { _ = try query(); preconditionFailure("failed query succeeded") }
   catch {
    let failure = CombinedFailure.capture(error, operation: "refresh", stage: .command, id: id)
    precondition(failure.stage == .uniqueDeviceID)
    if value == 0 { precondition(frees == 1 && failure.underlyingCode == 77) }
    precondition(!failure.localizedDescription.contains("SECRET"))
    let decoded = CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)!
    precondition(decoded.stage == .uniqueDeviceID)
   }
  }
  mode = 3; let result = try query(); precondition(result == "live-response")
  precondition(plistFrees == 2)
  print("Actual query ownership and redaction PASS")
 }
}
''')
