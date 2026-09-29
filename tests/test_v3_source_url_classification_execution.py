import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FAILURE = ROOT / "scripts/templates/combined_failure.swift"
HEADLESS = ROOT / "scripts/templates/v3_headless_runtime.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"


def read(path):
    return path.read_text(encoding="utf-8")


def extracted(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


class SourceURLClassificationExecutionTests(unittest.TestCase):
    def test_production_helper_and_both_source_classifiers(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift execution runs in macOS CI")

        failure = read(FAILURE)
        headless = read(HEADLESS)
        service = read(SERVICE)
        source_classifier = extracted(
            headless,
            "struct V3SourceCommandError: Error {",
            "\nfunc v3Resolve<T: NSManagedObject>",
        )
        policy_classifier = extracted(
            service,
            "private struct V3KnownSourcePolicyFailure: Error {",
            "\n@MainActor\n@objc(V3SideStoreService)",
        )
        harness = r'''
import Foundation

enum SourceError: Error {
    enum Code: Equatable { case unsupported }
    let code: Code
}

''' + failure + "\n" + source_classifier + "\n" + policy_classifier + r'''

@main struct Tests {
    static func main() {
        let fileCodes: [URLError.Code] = [
            .cannotCreateFile, .cannotOpenFile, .cannotWriteToFile,
            .cannotMoveFile, .cannotCloseFile, .cannotRemoveFile
        ]
        let domains = [NSURLErrorDomain, "kCFErrorDomainCFNetwork"]
        for domain in domains {
            for fileCode in fileCodes {
                let localFileError = NSError(domain: domain, code: fileCode.rawValue,
                    userInfo: [NSLocalizedDescriptionKey: "PRIVATE_LOCAL_PATH"])
                precondition(CombinedFailure.knownURLTransportCause(
                    domain: domain, code: fileCode.rawValue) == nil)
                if let _ = V3SourceCommandError.classify(localFileError) {
                    preconditionFailure("local URL file I/O must not become source network guidance")
                }
                let policy = V3KnownSourcePolicyFailure(localFileError)
                precondition(policy.kind == .invalidResponse)
                precondition(policy.underlyingDomain == domain && policy.underlyingCode == fileCode.rawValue)

                // A generic source failure keeps its caller stage and has no
                // connectivity or LocalDevVPN recovery advice.
                let generic = CombinedFailure.capture(localFileError,
                    operation: "sourcePreview", stage: .source, id: UUID().uuidString)
                precondition(generic.stage == .source && generic.safeCause == nil)
                precondition(!generic.recovery.contains("LocalDevVPN"))
                precondition(!generic.recovery.contains("network connection"))
                precondition(!generic.technicalDetails.contains("PRIVATE_LOCAL_PATH"))
            }
        }

        let knownTransport: [(URLError.Code, CombinedFailure.SafeCause)] = [
            (.networkConnectionLost, .networkConnectionLost),
            (.timedOut, .networkTimedOut),
            (.notConnectedToInternet, .networkUnavailable),
            (.cannotConnectToHost, .networkUnavailable),
            (.cannotFindHost, .networkUnavailable),
            (.dnsLookupFailed, .networkUnavailable)
        ]
        for domain in domains {
            for (code, safeCause) in knownTransport {
                let transportError = NSError(domain: domain, code: code.rawValue)
                precondition(CombinedFailure.knownURLTransportCause(
                    domain: domain, code: code.rawValue) == safeCause)
                guard let sourceFailure = V3SourceCommandError.classify(transportError),
                      case .network = sourceFailure.kind else {
                    preconditionFailure("known URL transport code must classify as network")
                }
                precondition(sourceFailure.domain == domain && sourceFailure.code == code.rawValue)
                let policy = V3KnownSourcePolicyFailure(transportError)
                precondition(policy.kind == .network)
                precondition(policy.underlyingDomain == domain && policy.underlyingCode == code.rawValue)
            }
        }

        let urlDownloadTimeout = CombinedFailure.capture(
            URLError(.timedOut), operation: "installURL", stage: .installation, id: UUID().uuidString)
        precondition(urlDownloadTimeout.stage == .network)
        precondition(urlDownloadTimeout.safeCause == .networkTimedOut)
        precondition(urlDownloadTimeout.recovery.contains("network used by this request"))
        precondition(urlDownloadTimeout.recovery.contains("Connection Check"))
        precondition(!urlDownloadTimeout.recovery.contains("LocalDevVPN"))

        let explicitVPN = CombinedFailure(operation: "refresh", stage: .network,
            code: .failed, id: UUID().uuidString, retryable: true, safeCause: .localDevVPNUnavailable)
        precondition(explicitVPN.message.contains("LocalDevVPN"))
        precondition(explicitVPN.recovery.contains("Restore LocalDevVPN"))
        precondition(CombinedFailure(operation: "refresh", stage: .network,
            code: .failed, id: UUID().uuidString, retryable: true, safeCause: .wifiUnavailable)
            .recovery.contains("Restore Wi-Fi"))

        // The CFNetwork NSError domain remains a safe, preserved diagnostic.
        let cfNetworkFailure = CombinedFailure.capture(
            NSError(domain: "kCFErrorDomainCFNetwork", code: URLError.Code.timedOut.rawValue),
            operation: "sourcePreview", stage: .source, id: UUID().uuidString)
        precondition(cfNetworkFailure.underlyingDomain == "kCFErrorDomainCFNetwork")
        let roundTrip = CombinedFailure.decode(cfNetworkFailure.wire, expectedID: cfNetworkFailure.correlationID)
        precondition(roundTrip?.underlyingDomain == "kCFErrorDomainCFNetwork")
        print("V3_SOURCE_URL_CLASSIFICATION_PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            swift = Path(directory) / "source_url_classification.swift"
            executable = Path(directory) / "source_url_classification"
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
            self.assertIn("V3_SOURCE_URL_CLASSIFICATION_PASS", result.stdout)

    def test_service_response_keeps_network_and_non_network_source_causes_distinct(self):
        service = read(SERVICE)
        block = extracted(
            service,
            "} else if let policyError = error as? V3KnownSourcePolicyFailure {",
            "} else if let sourceError = error as? V3SourceCommandError {",
        )
        self.assertIn("let network = policyError.kind == .network", block)
        self.assertIn(".knownSourcePolicyNetworkFailure : .knownSourcePolicyInvalidResponse", block)
        self.assertIn(".knownSourcePolicyFetch : .knownSourcePolicyParsing", block)
        command = extracted(
            service,
            "} else if let sourceError = error as? V3SourceCommandError {",
            "} else if operation == \"catalog\"",
        )
        self.assertIn("case .network:", command)
        self.assertIn("case .invalidManifest:", command)
        headless = read(HEADLESS)
        classifier = extracted(
            headless,
            "struct V3SourceCommandError: Error {",
            "\nfunc v3Resolve<T: NSManagedObject>",
        )
        self.assertIn("CombinedFailure.knownURLTransportCause", classifier)
        self.assertNotIn("native.domain == NSURLErrorDomain", classifier)


if __name__ == "__main__":
    unittest.main()
