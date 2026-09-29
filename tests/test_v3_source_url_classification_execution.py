import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FAILURE = ROOT / "scripts/templates/combined_failure.swift"
HEADLESS = ROOT / "scripts/templates/v3_headless_runtime.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
SIDESTORE_SOURCE_SHA = "ff25922e5c13ccfafd83bda5092910d848ebd409"


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

        side_store_root = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE")
        if not side_store_root:
            self.skipTest("Pinned SideStore source is supplied by macOS CI")
        side_store_root = Path(side_store_root)
        source_error_path = side_store_root / "SideStore/Core/Operations/Errors/SourceError.swift"
        if not source_error_path.is_file():
            self.skipTest("Pinned SideStore SourceError.swift is unavailable")
        revision = subprocess.run(
            ["git", "-C", str(side_store_root), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        self.assertEqual(revision.returncode, 0, revision.stderr)
        self.assertEqual(revision.stdout.strip(), SIDESTORE_SOURCE_SHA,
                         "SourceError.Code must come from the pinned SideStore revision")
        source_error_text = read(source_error_path)
        code_start = source_error_text.index("    enum Code: Int, ALTErrorCode")
        code_end = source_error_text.index("    static func unsupported", code_start)
        pinned_code_declaration = source_error_text[code_start:code_end].rstrip()
        pinned_case_names = re.findall(r"^\s*case\s+([A-Za-z_]\w*)", pinned_code_declaration, re.MULTILINE)
        self.assertTrue(pinned_case_names, "the pinned SourceError.Code enum must expose cases")
        pinned_case_literals = ", ".join(f".{name}" for name in pinned_case_names)

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
        primitives = read(ROOT / "scripts/templates/v3_behavioral_primitives.swift")
        harness = r'''
import Foundation

protocol ALTErrorCode: RawRepresentable where RawValue == Int {
    associatedtype Error
}

struct SourceError: Error, LocalizedError {
''' + pinned_code_declaration + r'''
    let code: Code
    let privateDetails: String
    var errorDescription: String? { privateDetails }
}

''' + failure + "\n" + primitives + "\n" + source_classifier + "\n" + policy_classifier + r'''

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
                guard let wrappedPolicy = V3KnownSourcePolicyFailure.preservingCancellation(localFileError)
                    as? V3KnownSourcePolicyFailure else {
                    preconditionFailure("local file errors still enter known-source error classification")
                }
                precondition(wrappedPolicy.kind == .invalidResponse)

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

        let pinnedSourceCodes: [SourceError.Code] = [PINNED_CASES]
        for code in pinnedSourceCodes {
            let sourceError = SourceError(code: code, privateDetails: "PRIVATE_SOURCE_APP_URL")
            guard let classified = V3SourceCommandError.classify(sourceError),
                  case .validation = classified.kind else {
                preconditionFailure("pinned SourceError.Code case is unclassified: \(code)")
            }
            precondition(classified.sourceStep == .sourceValidation)
            precondition(classified.domain != NSURLErrorDomain)
            precondition(classified.safeCause != .sourceNetworkFailure)

            let failure = CombinedFailure(operation: "source", stage: .source,
                code: .invalidResponse, id: UUID().uuidString,
                underlying: NSError(domain: classified.domain, code: classified.code),
                retryable: false, safeCause: classified.safeCause, sourceStep: classified.sourceStep)
            precondition(!failure.message.contains("PRIVATE_SOURCE_APP_URL"))
            precondition(!failure.recovery.contains("PRIVATE_SOURCE_APP_URL"))
            precondition(!failure.technicalDetails.contains("PRIVATE_SOURCE_APP_URL"))
            precondition(!String(describing: failure.wire).contains("PRIVATE_SOURCE_APP_URL"))
            let details = V3OperationFailureDetails(failure)
            precondition(details.recoveryDestination == "sources")
            precondition(details.retryDisposition == .blocked)
            precondition(!details.recommendedAction.contains("PRIVATE_SOURCE_APP_URL"))
            let decoded = CombinedFailure.decode(failure.wire, expectedID: failure.correlationID)
            precondition(decoded?.safeCause == classified.safeCause &&
                         decoded?.sourceStep == .sourceValidation)
        }
        precondition(V3SourceCommandError.classify(SourceError(
            code: .blocked, privateDetails: "PRIVATE_SOURCE"))?.safeCause == .sourceBlocked)
        precondition(V3SourceCommandError.classify(SourceError(
            code: .changedID, privateDetails: "PRIVATE_SOURCE"))?.safeCause == .sourceChangedID)
        precondition(V3SourceCommandError.classify(SourceError(
            code: .duplicate, privateDetails: "PRIVATE_SOURCE"))?.safeCause == .sourceDuplicate)
        precondition(V3SourceCommandError.classify(SourceError(
            code: .unsupported, privateDetails: "PRIVATE_SOURCE"))?.safeCause == .sourceUnsupported)
        // The marketplace cases are close in name but have different pinned
        // meanings: unsupported notarized apps require a SideStore update, while
        // a missing marketplaceID is source metadata the provider must fix.
        let marketplaceUnsupported = V3SourceCommandError.classify(SourceError(
            code: .marketplaceNotSupported, privateDetails: "PRIVATE_MARKETPLACE_SOURCE"))!
        precondition(marketplaceUnsupported.safeCause == .sourceUnsupported)
        let unsupportedFailure = CombinedFailure(operation: "source", stage: .source,
            code: .invalidResponse, id: UUID().uuidString, retryable: false,
            safeCause: marketplaceUnsupported.safeCause, sourceStep: marketplaceUnsupported.sourceStep)
        precondition(unsupportedFailure.recovery.contains("Update SideStore"))
        precondition(!unsupportedFailure.recovery.contains("source provider"))
        precondition(!unsupportedFailure.recovery.contains("PRIVATE_MARKETPLACE_SOURCE"))
        let marketplaceRequired = V3SourceCommandError.classify(SourceError(
            code: .marketplaceRequired, privateDetails: "PRIVATE_MARKETPLACE_SOURCE"))!
        precondition(marketplaceRequired.safeCause == .sourceValidationFailed)
        let metadataFailure = CombinedFailure(operation: "source", stage: .source,
            code: .invalidResponse, id: UUID().uuidString, retryable: false,
            safeCause: marketplaceRequired.safeCause, sourceStep: marketplaceRequired.sourceStep)
        precondition(metadataFailure.recovery.contains("source provider"))
        precondition(!metadataFailure.recovery.contains("Update SideStore"))
        precondition(!metadataFailure.recovery.contains("PRIVATE_MARKETPLACE_SOURCE"))

        let unknownSourceError = NSError(domain: "UnreviewedSourceFailure", code: 123456)
        precondition(V3SourceCommandError.classify(unknownSourceError) == nil,
            "unreviewed source errors stay unknown instead of inheriting a nearby mapping")
        let unknownSourceFailure = CombinedFailure.capture(unknownSourceError,
            operation: "sourcePreview", stage: .source, id: UUID().uuidString)
        precondition(unknownSourceFailure.stage == .source && unknownSourceFailure.safeCause == nil)
        precondition(!unknownSourceFailure.recovery.contains("LocalDevVPN"))

        // The known-source preflight uses this production wrapping policy at
        // both its shared-task and direct-task catch sites. Cancellation must
        // reach CombinedFailure as lifecycle evidence, not as policy parsing.
        let cancellationErrors: [Error] = [
            CancellationError(), URLError(.cancelled),
            NSError(domain: "kCFErrorDomainCFNetwork", code: URLError.Code.cancelled.rawValue)
        ]
        for cancellation in cancellationErrors {
            let preserved = V3KnownSourcePolicyFailure.preservingCancellation(cancellation)
            let failure = CombinedFailure.capture(preserved,
                operation: "sourcePreview", stage: .source, id: UUID().uuidString)
            precondition(failure.code == .cancelled && failure.retryable == false)
            precondition(failure.safeCause == nil)
            precondition(!failure.recovery.contains("LocalDevVPN"))
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
        harness = harness.replace("[PINNED_CASES]", "[" + pinned_case_literals + "]")
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
        preflight = extracted(
            service, "private func ensureKnownSourcesUpdated()", "private func snapshot()"
        )
        self.assertEqual(preflight.count("V3KnownSourcePolicyFailure.preservingCancellation(error)"), 2)
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
        self.assertIn("case .validation:", command)
        self.assertIn("safeCause: sourceError.safeCause", command)
        self.assertIn("sourceStep: sourceError.sourceStep", command)
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
