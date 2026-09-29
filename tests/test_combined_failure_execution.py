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
        self.assertIn('lc_native_code=\\(code)', query,
                      'native Lockdown query codes must reach CombinedFailure classification')
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
    if value == 0 {
      precondition(frees == 1 && failure.stage == .uniqueDeviceID,
                   "native Lockdown failure must survive the gateway adapter")
      precondition(failure.underlyingDomain == "IdeviceGatewayError" && failure.underlyingCode == 77,
                   "known gateway diagnostics retain their typed native code")
    }
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

    def test_capture_preserves_honest_domains_and_classifies_ppq(self):
        common = (ROOT / 'scripts/templates/combined_failure.swift').read_text(encoding='utf-8')
        self.execute(common + '''
func debugLog(_ value: String) {}
@main struct Tests {
 static func main() throws {
  let id = UUID().uuidString
  // 1. A bare POSIX errno keeps the caller stage; its domain does not prove socket I/O.
  do {
   let error = NSError(domain: "NSPOSIXErrorDomain", code: 20,
       userInfo: [NSLocalizedDescriptionKey: "not a directory"])
    let failure = CombinedFailure.capture(error, operation: "install", stage: .installation, id: id)
   precondition(failure.stage == .installation, "bare POSIX error must not be guessed as network")
   precondition(failure.underlyingDomain == "NSPOSIXErrorDomain", "posix domain: \\\\(failure.underlyingDomain)")
   precondition(failure.underlyingCode == 20, "posix code")
   precondition(failure.safeCause == nil, "bare POSIX error must not imply network recovery")
  }
  // A wrapped local POSIX error still cannot override its caller's file stage.
  do {
   let posix = NSError(domain: "NSPOSIXErrorDomain", code: 20,
       userInfo: [NSLocalizedDescriptionKey: "not a directory"])
   let cocoa = NSError(domain: "NSCocoaErrorDomain", code: 4,
       userInfo: [NSLocalizedDescriptionKey: "file operation failed", NSUnderlyingErrorKey: posix])
   let failure = CombinedFailure.capture(cocoa, operation: "install", stage: .filePreparation, id: id)
   precondition(failure.stage == .filePreparation, "wrapped local POSIX error keeps file stage")
   precondition(failure.underlyingDomain == "NSPOSIXErrorDomain", "wrapped POSIX domain preserved")
   precondition(failure.underlyingCode == 20, "wrapped POSIX code preserved")
  }
  // Explicit transport context supplied by the caller remains authoritative.
  do {
   let error = NSError(domain: "NSPOSIXErrorDomain", code: 61,
       userInfo: [NSLocalizedDescriptionKey: "connection refused"])
   let failure = CombinedFailure.capture(error, operation: "refresh", stage: .network, id: id)
   precondition(failure.stage == .network, "caller-proven network stage is preserved")
  }
  // A typed URL-loading error is sufficient evidence for network classification.
  do {
   let error = NSError(domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost,
       userInfo: [NSLocalizedDescriptionKey: "connection lost"])
   let failure = CombinedFailure.capture(error, operation: "install", stage: .installation, id: id)
   precondition(failure.stage == .network, "typed URL network failure")
   precondition(failure.safeCause == .networkConnectionLost, "typed network cause")
   precondition(failure.safeCause == .networkConnectionLost)
   precondition(failure.recovery.localizedCaseInsensitiveContains("network used by this request"))
   precondition(failure.recovery.localizedCaseInsensitiveContains("then retry when the connection is stable"))
   precondition(!failure.recovery.localizedCaseInsensitiveContains("localdevvpn"))
  }
  // URLSession uses URL error domains for local temporary-file I/O too.
  for localCode in [URLError.Code.cannotCreateFile, .cannotOpenFile, .cannotWriteToFile,
                    .cannotMoveFile, .cannotCloseFile, .cannotRemoveFile] {
   let error = NSError(domain: NSURLErrorDomain, code: localCode.rawValue,
       userInfo: [NSLocalizedDescriptionKey: "download temporary-file operation failed"])
   let failure = CombinedFailure.capture(error, operation: "installURL", stage: .installation, id: id)
   precondition(failure.stage == .installation, "local URL file error keeps installation stage: \\(localCode)")
   precondition(failure.safeCause == nil, "local URL file error has no network cause: \\(localCode)")
   precondition(!failure.message.contains("Network error"), "local URL file error is not presented as network")
   precondition(!failure.recovery.contains("LocalDevVPN"), "local URL file error does not recommend LocalDevVPN")
  }
  // URL cancellation remains a terminal cancellation rather than a network retry.
  do {
   let error = NSError(domain: NSURLErrorDomain, code: NSURLErrorCancelled,
       userInfo: [NSLocalizedDescriptionKey: "cancelled"])
   let failure = CombinedFailure.capture(error, operation: "installURL", stage: .installation, id: id)
   precondition(failure.code == .cancelled, "typed URL cancellation")
   precondition(failure.retryable == false, "URL cancellation is not retryable")
  }
  // Provisioning transport keeps signing-specific guidance, while explicit
  // pipeline stage markers remain authoritative over URL error domains.
  do {
   let timedOut = NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut,
       userInfo: [NSLocalizedDescriptionKey: "provisioning request timed out"])
   let signing = CombinedFailure.capture(timedOut, operation: "install", stage: .signing, id: id)
   precondition(signing.stage == .signing, "signing stage remains specific")
   precondition(signing.safeCause == .signingNetworkTimedOut, "signing timeout keeps typed cause")
   let marked = NSError(domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost,
       userInfo: ["LCStructuredFailureStageV1": "installation"])
   let installation = CombinedFailure.capture(marked, operation: "install", stage: .command, id: id)
   precondition(installation.stage == .installation, "explicit stage marker remains authoritative")
   precondition(installation.safeCause == nil, "explicit installation stage does not inherit network cause")
  }
  // Native CFNetwork domains survive the structured failure wire allowlist.
  do {
   let error = NSError(domain: "kCFErrorDomainCFNetwork", code: -1003,
       userInfo: [NSLocalizedDescriptionKey: "host not found"])
   let failure = CombinedFailure.capture(error, operation: "source", stage: .network, id: id)
   let decoded = CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)!
   precondition(decoded.stage == .network, "CFNetwork source stage")
   precondition(decoded.underlyingDomain == "kCFErrorDomainCFNetwork", "CFNetwork domain survives wire")
   precondition(decoded.underlyingCode == -1003, "CFNetwork code survives wire")
  }
  // 2. An HTTP status is never relabelled as a gateway error.
  do {
   let error = NSError(domain: "NSURLErrorDomain", code: -1001,
       userInfo: [NSLocalizedDescriptionKey: "HTTP 503 Service Unavailable"])
   let failure = CombinedFailure.capture(error, operation: "install", stage: .installation, id: id)
   precondition(failure.stage == .network, "http stage")
   precondition(failure.underlyingDomain == "HTTPStatus", "http domain: \\\\(failure.underlyingDomain)")
   precondition(failure.underlyingCode == 503, "http code")
  }
  // 3. A real gateway native code stays in its gateway domain.
  do {
   let error = NSError(domain: "DeviceGatewayError", code: 1,
       userInfo: [NSLocalizedDescriptionKey: "transport failed lc_native_code=77"])
   let failure = CombinedFailure.capture(error, operation: "install", stage: .installation, id: id)
   precondition(failure.stage == .command, "gateway stage")
   precondition(failure.underlyingDomain == "DeviceGatewayError", "allowlisted gateway domain")
   precondition(failure.underlyingCode == 77, "allowlisted gateway code")
  }
  // 4. An unknown error gains no fake domain and keeps the caller stage.
  do {
   let error = NSError(domain: "com.example.mystery", code: 20,
       userInfo: [NSLocalizedDescriptionKey: "mystery failure"])
   let failure = CombinedFailure.capture(error, operation: "refresh", stage: .command, id: id)
   precondition(failure.stage == .command, "unknown stage")
   precondition(failure.underlyingDomain == "redacted", "unknown domain: \\\\(failure.underlyingDomain)")
   precondition(failure.technicalDetails.contains("underlying_code=unknown"),
                "unknown private error codes are redacted in diagnostics")
  }
  // 5. ApplicationVerificationFailed 0xE8008024 is an installation failure
  // describing profile rejection, not pairing/network/CoreDevice trouble.
  do {
   let error = NSError(domain: "IdeviceGatewayError", code: 0,
       userInfo: [NSLocalizedDescriptionKey: "ApplicationVerificationFailed: Failed to verify code signature of /Payload/App.app : 0xE8008024 (The provisioning profile is banned.)"])
   let failure = CombinedFailure.capture(error, operation: "install", stage: .command, id: id)
   precondition(failure.stage == .installation, "ppq stage")
   precondition(failure.stage != .pairing && failure.stage != .network && failure.stage != .coreDevice, "ppq not misclassified")
   precondition(failure.underlyingDomain == "IdeviceGatewayError", "allowlisted gateway domain")
   precondition(failure.underlyingCode == 0xE8008024, "typed verification code")
   precondition(failure.message.contains("provisioning profile is banned"), "ppq message")
   precondition(!failure.message.lowercased().contains("account"), "no account-ban claim")
   let decoded = CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)!
   precondition(decoded.stage == .installation && decoded.underlyingCode == 0xE8008024,
                "typed verification evidence survives the wire")
  }
  // 6. ApplicationVerificationFailed 0xE8008018 is a signing-identity rejection
  // from an allowlisted installd domain, so its typed native code is retained.
  do {
   let error = NSError(domain: "com.apple.installd", code: 0,
       userInfo: [NSLocalizedDescriptionKey: "ApplicationVerificationFailed: 0xE8008018 (The identity used to sign the executable is no longer valid.)"])
   let failure = CombinedFailure.capture(error, operation: "install", stage: .command, id: id)
   precondition(failure.stage == .installation, "ppq8018 stage")
   precondition(failure.stage != .pairing && failure.stage != .network && failure.stage != .coreDevice, "ppq8018 not misclassified")
   precondition(failure.underlyingDomain == "com.apple.installd", "allowlisted installd domain")
   precondition(failure.underlyingCode == 0xE8008018, "typed signing identity code")
   precondition(failure.message.contains("signing identity"), "ppq8018 message")
  }
  // 7. An unknown install error stays an honest generic installation
  // failure: caller installation stage, generic message, redacted domain.
  do {
   let error = NSError(domain: "com.example.mystery", code: 20,
       userInfo: [NSLocalizedDescriptionKey: "mystery install failure"])
   let failure = CombinedFailure.capture(error, operation: "install", stage: .installation, id: id)
   precondition(failure.stage == .installation, "unknown install stage")
   precondition(failure.message == "SideStore could not complete the application installation.", "unknown install message")
   precondition(failure.underlyingDomain == "redacted", "unknown install domain")
   precondition(!failure.message.contains("pairing"), "unknown install message has no pairing claim")
  }
  // 8. Verification tokens are not enough without a typed installer domain
  // and install/update context. Refresh failure codes stay refresh failures.
  do {
   let error = NSError(domain: "com.example.mystery", code: 20,
       userInfo: [NSLocalizedDescriptionKey: "ApplicationVerificationFailed 0xE8008024 unrelated text"])
   let failure = CombinedFailure.capture(error, operation: "refresh", stage: .refreshVerification, id: id)
   precondition(failure.stage == .refreshVerification, "unrelated refresh was reclassified")
   precondition(!failure.technicalDetails.contains("installVerdict="), "unrelated refresh got PPQ verdict")
  }
  do {
   let error = NSError(domain: "IdeviceGatewayError", code: 0,
       userInfo: [NSLocalizedDescriptionKey: "ApplicationVerificationFailed 0xE8008024"])
   let failure = CombinedFailure.capture(error, operation: "install", stage: .command, id: id)
   precondition(failure.stage == .command, "non-install stage was reclassified")
   precondition(!failure.technicalDetails.contains("installVerdict="), "non-install stage got PPQ verdict")
  }
  // 9. Broad SideSign/server domains preserve the caller's signing/install context.
  do {
   let signing = CombinedFailure.capture(NSError(domain: "SideSignErrorDomain", code: 20),
       operation: "install", stage: .signing, id: id)
   precondition(signing.stage == .signing, "SideSign provisioning was sent to credentials")
   let install = CombinedFailure.capture(NSError(domain: "ALTServerErrorDomain", code: 20),
       operation: "install", stage: .installation, id: id)
   precondition(install.stage == .installation, "ALTServer error was sent to credentials")
  }
  print("Capture honesty and PPQ PASS")
 }
}
''')
