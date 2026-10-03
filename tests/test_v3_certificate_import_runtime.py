"""Execute the generated canonical certificate importer against bounded mocks."""
from pathlib import Path
import importlib.util
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXISTING_TEST = importlib.util.spec_from_file_location(
    "v3_import_ownership_fixture", ROOT / "tests/test_v3_jitless_import_ownership.py")
fixture_module = importlib.util.module_from_spec(EXISTING_TEST)
EXISTING_TEST.loader.exec_module(fixture_module)


def production_method_parts():
    with tempfile.TemporaryDirectory() as directory:
        settings = fixture_module.generated_settings(Path(directory))
    start = settings.index("    func importCertificateFromSideStore() async {")
    wrapper_start = settings.index(
        "    private func v3CompleteSideStoreCertificateImport", start)
    callback_start = settings.index(
        "    func onSideStoreCertificateCallback(certificateData: Data, password: String)",
        wrapper_start)
    return (settings[start:wrapper_start], settings[wrapper_start:callback_start],
            fixture_module.patch.IMPORT_OWNERSHIP_SWIFT)


@unittest.skipUnless(shutil.which("swiftc"), "swiftc unavailable; executable importer harness runs on macOS CI")
class CertificateImportRuntimeTests(unittest.TestCase):
    def test_generated_importer_routes_only_current_valid_tuple_to_canonical_callback(self):
        importer, completion, ownership = production_method_parts()
        ownership_test_hooks = r'''
    func resetCertificateImportOwner() {
        V3CertificateImportOwnership.invalidate()
    }
    func cancelCurrentCertificateImportOwner() {
        if let id = UserDefaults.standard.string(forKey: "V3PendingCertificateImportRequestID") {
            _ = V3CertificateImportOwnership.cancel(id)
        }
    }
'''
        prelude = r'''
import Foundation
import CoreFoundation

enum ImportHarnessFailure: Error { case unavailable }

extension UserDefaults {
    static func sideStoreExist() -> Bool { true }
}

@MainActor final class ConfirmationBoundary {
    var answer: Bool? = true
    var onOpen: (@MainActor () -> Void)?
    func open() async -> Bool? {
        onOpen?()
        return answer
    }
}

@MainActor final class V3ServiceBridge {
    static let shared = V3ServiceBridge()
    var certificateResult: Result<[String: Any], Error> = .failure(ImportHarnessFailure.unavailable)
    var statusReply: [String: Any] = [:]
    var operations: [String] = []
    var afterCertificateReply: (@MainActor () -> Void)?

    func request(operation: String) async throws -> [String: Any] {
        operations.append(operation)
        if operation == "certExportActive" {
            let result = try certificateResult.get()
            afterCertificateReply?()
            return result
        }
        if operation == "healthSnapshot" { return statusReply }
        throw ImportHarnessFailure.unavailable
    }

    static func strictBool(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }
}

enum LCUtils {
    static var parsedTeam: String?
    static var parsedPassphrases: [String] = []
    static func getCertTeamId(withKeyData data: Data, password: String) -> String? {
        parsedPassphrases.append(password)
        return data == Data([1, 2, 3]) ? parsedTeam : nil
    }
}

@MainActor final class ImporterHarness {
    let certificateImportFromBuiltInSideStoreAlert = ConfirmationBoundary()
    var errorInfo = ""
    var errorShow = false
    var committed: [(Data, String)] = []

    func onSideStoreCertificateCallback(certificateData: Data, password: String) {
        committed.append((certificateData, password))
    }
'''
        runner = r'''
}

@MainActor func reset(_ test: ImporterHarness,
                      certificate: [String: Any]?,
                      statusTeam: String = "TEAM-A",
                      statusFingerprint: String = String(repeating: "a", count: 64)) {
    test.resetCertificateImportOwner()
    V3ServiceBridge.shared.operations = []
    V3ServiceBridge.shared.afterCertificateReply = nil
    if let certificate {
        V3ServiceBridge.shared.certificateResult = .success(certificate)
    } else {
        V3ServiceBridge.shared.certificateResult = .failure(ImportHarnessFailure.unavailable)
    }
    V3ServiceBridge.shared.statusReply = ["certificateState": [
        "active": true, "team": statusTeam,
        "certificateIdentitySHA256": statusFingerprint
    ]]
    LCUtils.parsedTeam = "TEAM-A"
    LCUtils.parsedPassphrases = []
    test.certificateImportFromBuiltInSideStoreAlert.answer = true
    test.certificateImportFromBuiltInSideStoreAlert.onOpen = nil
    test.errorInfo = ""
    test.errorShow = false
    test.committed = []
}

@MainActor func certificateReply(password: String = "p12-password",
                                  team: String = "TEAM-A",
                                  fingerprint: String = String(repeating: "a", count: 64)) -> [String: Any] {
    ["data": Data([1, 2, 3]), "password": password,
     "teamIdentifier": team, "identitySHA256": fingerprint]
}

@main struct TestRunner {
    @MainActor static func main() async {
        // The actual generated method accepts both upstream-supported P12 forms.
        for passphrase in ["p12-password", ""] {
            let test = ImporterHarness()
            reset(test, certificate: certificateReply(password: passphrase))
            await test.importCertificateFromSideStore()
            precondition(test.committed.count == 1)
            precondition(test.committed[0].0 == Data([1, 2, 3]))
            precondition(test.committed[0].1 == passphrase)
            precondition(V3ServiceBridge.shared.operations == ["certExportActive", "healthSnapshot"])
            precondition(LCUtils.parsedPassphrases == [passphrase])
        }

        // Service/export unavailable: one export attempt, no host commit.
        let unavailable = ImporterHarness()
        reset(unavailable, certificate: nil)
        await unavailable.importCertificateFromSideStore()
        precondition(V3ServiceBridge.shared.operations == ["certExportActive"])
        precondition(unavailable.committed.isEmpty)
        precondition(unavailable.errorShow)

        // Cancellation while confirmation is suspended prevents the request.
        let cancelledBeforeReceipt = ImporterHarness()
        reset(cancelledBeforeReceipt, certificate: certificateReply())
        cancelledBeforeReceipt.certificateImportFromBuiltInSideStoreAlert.onOpen = {
            cancelledBeforeReceipt.cancelCurrentCertificateImportOwner()
        }
        await cancelledBeforeReceipt.importCertificateFromSideStore()
        precondition(V3ServiceBridge.shared.operations.isEmpty)
        precondition(cancelledBeforeReceipt.committed.isEmpty)

        // A removal/cancel after the async service receipt cannot commit it.
        let cancelledAfterReceipt = ImporterHarness()
        reset(cancelledAfterReceipt, certificate: certificateReply())
        V3ServiceBridge.shared.afterCertificateReply = {
            cancelledAfterReceipt.cancelCurrentCertificateImportOwner()
        }
        await cancelledAfterReceipt.importCertificateFromSideStore()
        precondition(V3ServiceBridge.shared.operations == ["certExportActive"])
        precondition(cancelledAfterReceipt.committed.isEmpty)

        // Parser/team mismatch is rejected before status read; fingerprint mismatch
        // is rejected against the current SideStore identity after one status read.
        let wrongTeam = ImporterHarness()
        reset(wrongTeam, certificate: certificateReply())
        LCUtils.parsedTeam = "TEAM-B"
        await wrongTeam.importCertificateFromSideStore()
        precondition(V3ServiceBridge.shared.operations == ["certExportActive"])
        precondition(wrongTeam.committed.isEmpty)

        let wrongFingerprint = ImporterHarness()
        let fingerprint = String(repeating: "b", count: 64)
        reset(wrongFingerprint, certificate: certificateReply(fingerprint: fingerprint))
        await wrongFingerprint.importCertificateFromSideStore()
        precondition(V3ServiceBridge.shared.operations == ["certExportActive", "healthSnapshot"])
        precondition(wrongFingerprint.committed.isEmpty)

        print("CERTIFICATE_IMPORT_RUNTIME_PASS")
    }
}
'''
        method = textwrap.dedent(importer).rstrip()
        wrapper = textwrap.dedent(completion).rstrip()
        source_text = (prelude + "\n" + ownership + "\n" + ownership_test_hooks + "\n" +
                       method + "\n" + wrapper + "\n" +
                       runner)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.swift"
            executable = Path(directory) / "certificate-import-runtime"
            source.write_text(source_text, encoding="utf-8")
            compiled = subprocess.run([shutil.which("swiftc"), "-parse-as-library",
                                       str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CERTIFICATE_IMPORT_RUNTIME_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
