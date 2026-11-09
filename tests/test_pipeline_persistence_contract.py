"""Execute the current owned SideStore persistence policy and its v3 error mapping."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
from tests.owner_source import owner_source
from tests.support.product_sources import read_product_source


def swift_declaration(source: str, signature: str) -> str:
    if source.count(signature) != 1:
        raise AssertionError(f"expected one Swift declaration {signature!r}")
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced Swift declaration {signature!r}")


class PipelinePersistenceContractTests(unittest.TestCase):
    def pinned(self, relative: str) -> str:
        return owner_source(relative, "SideStore")

    def test_pinned_pipeline_runner_calls_durable_policy_before_success_callback(self):
        source = self.pinned("SideStore/Core/Operations/PipelineRunner.swift")
        method = swift_declaration(source, "func performOperation(for operation:")
        marker_at = method.index("V3_POST_MUTATION_PERSISTENCE_CONTRACT_V1")
        self.assertLess(method.index("V3MutationPersistencePolicy.persistResult"), method.index("group.set(.success(result)", marker_at))
        self.assertNotIn("Failed to save InstalledApp to database. \\(error.localizedDescription)", method)
        self.assertIn("try dbContext.save()", method)

    def test_persistence_failure_survives_upstream_error_mapping_and_refresh_verification(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Executable Swift behavior runs in macOS CI")

        combined = read_product_source("combined_failure.swift")
        primitives = read_product_source("v3_behavioral_primitives.swift")
        verifier = swift_declaration(primitives, "enum V3RefreshResultVerifier {")
        delete_contract = swift_declaration(primitives, "struct V3DeleteCompletionContract {")

        wrapped_error = self.pinned("Shared/Errors/ALTWrappedError.swift")
        error_extensions = self.pinned("Shared/Extensions/NSError+AltStore.swift")
        title_method = swift_declaration(error_extensions, "func withLocalizedTitle(_ title: String) -> NSError")
        app_manager = self.pinned("AltStore/Managing Apps/AppManager.swift")
        get_mapped_error = swift_declaration(
            app_manager, "func getMappedError(for operation: AppOperation, error: Error) -> Error")

        source = """
import Foundation
import CoreData
let ALTLocalizedTitleErrorKey = "ALTLocalizedTitleErrorKey"
protocol ALTLocalizedError: Error { var errorTitle: String? { get set } }
""" + wrapped_error + """
extension NSError {
""" + title_method + """
}
""" + combined + """
""" + verifier + """
""" + delete_contract + """
protocol AppProtocol { var name: String { get }; var bundleIdentifier: String { get } }
struct FakeApp: AppProtocol { var name = "Example"; var bundleIdentifier = "example.bundle" }
enum AppOperation {
    case install, refresh, update, activate, deactivate, deleteApp, backup, restore, resign, removeApp, removeDeactivatedApp
    var app: AppProtocol { FakeApp() }
}
struct ProductionErrorMapper {
""" + get_mapped_error + """
}
struct Item { let bundleIdentifier: String }

func callbackResult(hasChanges: Bool, save: () throws -> Void) -> Result<Item, Error> {
    do {
        try V3MutationPersistencePolicy.persistResult(hasChanges: hasChanges, save: save)
        return .success(Item(bundleIdentifier: "example.bundle"))
    } catch {
        let mapped = ProductionErrorMapper().getMappedError(for: .install, error: error)
        return .failure(mapped)
    }
}

@main struct Tests {
 static func main() async throws {
   var saveCalls = 0
   let noChanges = callbackResult(hasChanges: false) { saveCalls += 1 }
   guard case .success = noChanges, saveCalls == 0 else { fatalError("no-change result must not save") }

   let saved = callbackResult(hasChanges: true) { saveCalls += 1 }
   guard case .success = saved, saveCalls == 1 else { fatalError("durable save must preserve success") }

   let result = callbackResult(hasChanges: true) {
     throw NSError(domain: "CoreData", code: 773,
                   userInfo: [NSLocalizedDescriptionKey: "SECRET_DATABASE_DETAIL"])
   }
   guard case .failure(let mappedError) = result else { fatalError("failed persistence must not publish success") }
   let id = UUID().uuidString
   let failure = CombinedFailure.capture(mappedError, operation: "install", stage: .installation, id: id)
   precondition(failure.stage == .persistence, "upstream title wrapper must preserve the persistence stage")
   precondition(failure.safeCause == .operationPersistenceFailed)
   precondition(failure.retryable == false, "post-mutation failure cannot be blindly retried")
   precondition(failure.recovery.contains("Do not repeat"))
   precondition(!failure.technicalDetails.contains("SECRET_DATABASE_DETAIL"))
   let bytes = try PropertyListSerialization.data(fromPropertyList: failure.wire, format: .binary, options: 0)
   let decoded = try PropertyListSerialization.propertyList(from: bytes, format: nil) as! [String: Any]
   let roundTrip = CombinedFailure.decode(decoded, expectedID: id)
   precondition(roundTrip?.stage == .persistence && roundTrip?.safeCause == .operationPersistenceFailed)
   precondition(roundTrip?.retryable == false)

   do {
     _ = try V3RefreshResultVerifier.verified(expectedBundleID: "example.bundle",
       results: ["example.bundle": Result<Item, Error>.failure(mappedError)],
       bundleIdentifier: { $0.bundleIdentifier })
     fatalError("persistence failure cannot pass refresh verification")
   } catch {
     let refreshFailure = CombinedFailure.capture(error, operation: "refresh", stage: .refreshVerification, id: id)
     precondition(refreshFailure.stage == .persistence &&
                  refreshFailure.safeCause == .operationPersistenceFailed)
   }

   // PipelineRunner reaches the persistence boundary only after `try await
   // performPipeline` has returned. A cancellation thrown before native success
   // therefore bypasses this helper and cannot save or claim success.
   var cancelledSaveCalls = 0
   let cancelledPipeline: Result<Item, Error> = .failure(CancellationError())
   do {
     let nativeResult = try cancelledPipeline.get()
     try V3MutationPersistencePolicy.persistResult(hasChanges: true) { cancelledSaveCalls += 1 }
     _ = nativeResult
     fatalError("cancelled pre-mutation result unexpectedly completed")
   } catch is CancellationError {
     precondition(cancelledSaveCalls == 0)
   }

   // Once native success exists, cancellation cannot turn a failed durable save into success.
   let lateResult = callbackResult(hasChanges: true) {
     throw NSError(domain: "CoreData", code: 774,
                   userInfo: [NSLocalizedDescriptionKey: "SECRET_AFTER_CANCEL"])
   }
   guard case .failure(let lateError) = lateResult else { fatalError("late persistence failure was lost") }
   let late = CombinedFailure.capture(lateError, operation: "delete", stage: .installation, id: id)
   precondition(late.stage == .persistence && late.retryable == false)
   precondition(!late.technicalDetails.contains("SECRET_AFTER_CANCEL"))

   // A native uninstall signal alone cannot bypass the authoritative library absence check.
   var deletion = V3DeleteCompletionContract()
   let deleteState = deletion.resolve(backend: .failed, nativeUninstallSucceeded: true,
       appStillInAuthoritativeLibrary: true, deadlineExpired: true, progress: 1.0)
   precondition(deleteState == .failed)
   var absentDeletion = V3DeleteCompletionContract()
   precondition(absentDeletion.resolve(backend: .succeeded, nativeUninstallSucceeded: true,
       appStillInAuthoritativeLibrary: false, deadlineExpired: false, progress: 0.02) == .completed)

   print("Pipeline persistence contract PASS")
 }
}
"""
        with tempfile.TemporaryDirectory() as name:
            swift = Path(name) / "pipeline_persistence.swift"
            executable = swift.with_suffix("")
            swift.write_text(source, encoding="utf-8")
            build = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("PASS", run.stdout)


if __name__ == "__main__":
    unittest.main()
