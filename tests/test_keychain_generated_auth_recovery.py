"""Execute generated silentSignIn/signIn against Apple and Keychain doubles."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
import test_embedded_keychain as existing

AUTH_DOUBLES = r'''
struct ALTAccount { let appleID: String; let identifier: String }
struct ALTAppleAPISession { let dsid: String; let authToken: String }
enum OperationError: Error { case notAuthenticated, cancelled }
enum V3AccountOperationStep { case credentialCommit }
struct V3AccountOperationError: Error { let underlying: Error }
func v3AccountOperationFailure(_ error: Error, step: V3AccountOperationStep) -> V3AccountOperationError {
    (error as? V3AccountOperationError) ?? V3AccountOperationError(underlying: error)
}
enum AnisetteConfigManager {
    static let shared = AnisetteConfigManagerValue()
    struct AnisetteConfigManagerValue { func resolvedXcodeVersion() async -> String { "test" } }
}
struct SignInHandler {
    func accountRepair(url: URL, message: String) async {}
    func verificationCode(for request: String) async throws -> String { "test" }
}
final class AuthManager {
    static let shared = AuthManager()
    var session: ALTAppleAPISession?
    var v3IdentityStamp = "initial"
    var v3IdentityIsStable = true
    var onAppleResponse: (() -> Void)?
    var tokenError: Error?
    var tokenCalls = 0
    var passwordCalls = 0
    var response = (ALTAccount(appleID: "test@example.com", identifier: "id"),
                    ALTAppleAPISession(dsid: "id", authToken: "verified-token"))
    func v3BeginIdentityTransition() { v3IdentityIsStable = false; v3IdentityStamp += ":begin" }
    func v3CompleteIdentityTransition() { v3IdentityIsStable = true; v3IdentityStamp += ":complete" }
    func authenticateWithToken(adsid: String, xcodeToken: String, anisetteData: String,
                               xcodeVersion: String) async throws -> (ALTAccount, ALTAppleAPISession) {
        tokenCalls += 1; onAppleResponse?()
        if let tokenError { throw tokenError }
        return response
    }
    func signIn(appleID: String, password: String, anisetteData: String, xcodeVersion: String,
                accountRepairHandler: (URL, String) async -> Void,
                verificationHandler: (String) async throws -> String) async throws -> (ALTAccount, ALTAppleAPISession) {
        passwordCalls += 1; onAppleResponse?(); return response
    }
}
final class SignInOperation {
    var isCancelled = false
    var appleIDEmailAddress: String?
    let signInHandler = SignInHandler()
    // These cases are ordinary legacy recovery, with no explicit same-owner
    // reauthentication request. The recovery suite exercises that policy;
    // its production helper returns immediately when the owner is nil.
    func v3ValidateReauthenticationIdentity(submittedAppleID: String, returnedAppleID: String? = nil, returnedDSID: String? = nil) throws {}
    func getAnisetteData() async throws -> String { "anisette-double" }
    func verboseLog(_ value: String) {}
    func debugLog(_ value: String) {}
    func run() async throws -> (ALTAccount, ALTAppleAPISession)? { try await silentSignIn() }
    func runInteractive() async throws -> (ALTAccount, ALTAppleAPISession) {
        try await signIn(appleID: "test@example.com", password: "password")
    }
'''

MAIN = r'''
}
@main struct GeneratedAuthenticationTests {
    static func main() async throws {
        LCEmbeddedSharedKeychain.transactionOverride = { try $0() }
        Keychain.shared = Keychain(LCEmbeddedSharedKeychain.makeClient())
        let group = Store.keychainGroup
        let scenario = CommandLine.arguments[1]
        let operation = SignInOperation()
        let auth = AuthManager.shared
        let tokenOnly = ["appleIDAdsid": Data("id".utf8), "appleIDXcodeToken": Data("token".utf8)]
        Store.data[group] = tokenOnly
        if scenario == "password_only" {
            Store.data[group] = ["appleIDEmailAddress": Data("test@example.com".utf8), "appleIDPassword": Data("password".utf8)]
        }
        if scenario == "email_tokens" || scenario == "email_mismatch" {
            Store.data[group]!["appleIDEmailAddress"] = Data("test@example.com".utf8)
        }
        if scenario == "expired_token" { auth.tokenError = NSError(domain: "AppleTest", code: 1) }
        if scenario == "generation_changed" { auth.onAppleResponse = { auth.v3IdentityStamp = "different-generation" } }
        if scenario == "cancelled" || scenario == "interactive_cancelled" {
            auth.onAppleResponse = { operation.isCancelled = true }
        }
        if scenario == "different_account" {
            auth.response = (ALTAccount(appleID: "other@example.com", identifier: "different-id"),
                             ALTAppleAPISession(dsid: "different-id", authToken: "other-token"))
        }
        if scenario == "account_session_mismatch" {
            auth.response = (ALTAccount(appleID: "other@example.com", identifier: "different-id"),
                             ALTAppleAPISession(dsid: "id", authToken: "other-token"))
        }
        if scenario == "email_mismatch" {
            auth.response = (ALTAccount(appleID: "other@example.com", identifier: "id"),
                             ALTAppleAPISession(dsid: "id", authToken: "other-token"))
        }
        if scenario == "writeback_failure" {
            Store.failSetKey = "appleIDEmailAddress"; Store.failSetKeyCount = 1
        }
        let before = Store.data
        if scenario == "interactive_cancelled" {
            do { _ = try await operation.runInteractive(); preconditionFailure("late interactive response committed") }
            catch OperationError.cancelled {}
            precondition(Store.data == before && auth.passwordCalls == 1 && auth.tokenCalls == 0)
            print("PASSED: " + scenario)
            return
        }
        if ["token_only", "email_tokens", "password_only"].contains(scenario) {
            let result = try await operation.run()
            let snapshot = try Keychain.shared.authenticationSnapshot()
            precondition(result?.0.appleID == "test@example.com" && snapshot?.isAuthenticated == true)
            precondition(snapshot?.appleIDPassword == (scenario == "password_only" ? "password" : nil))
            precondition(auth.tokenCalls == (scenario == "password_only" ? 0 : 1))
            precondition(auth.passwordCalls == (scenario == "password_only" ? 1 : 0))
        } else if scenario == "expired_token" {
            let result = try await operation.run()
            precondition(result == nil && Store.data == before && auth.passwordCalls == 0)
        } else {
            do { _ = try await operation.run(); preconditionFailure("stale/failed response accepted") }
            catch {
                if scenario != "cancelled" { precondition(error is V3AccountOperationError) }
            }
            precondition(Store.data == before && auth.passwordCalls == 0,
                "failure preserves stored state and never replays password auth")
        }
        print("PASSED: " + scenario)
    }
}
'''


class GeneratedAuthenticationRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_root = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        compiler = shutil.which("swiftc")
        if not source_root or not compiler:
            raise unittest.SkipTest("pinned SideStore and Swift required; executed in combined macOS CI")
        original = existing.read_pinned_source(source_root, "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift")
        generated = existing.module.patch_sign_in_operation(existing.service_module.patch_sign_in_operation(original))
        silent = generated[generated.index("    private func silentSignIn()") : generated.index("    private func authenticationLoop()")]
        password = generated[generated.index("    private func signIn(appleID:") : generated.index("    private func finalizeAuthentication(")]
        doubles = existing.DOUBLES.replace("final class Keychain {", "final class Keychain {\n    static var shared: Keychain!")
        swift = doubles + existing.TEMPLATE.read_text() + existing.module.KEYCHAIN_ACCESS_ADAPTER + AUTH_DOUBLES + silent + password + MAIN
        cls.temp = tempfile.TemporaryDirectory(prefix="lc-generated-auth-recovery-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "Generated.swift"
        source.write_text(swift)
        cls.executable = Path(cls.temp.name) / "generated"
        result = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", str(source), "-o", str(cls.executable)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_generated_apple_verification_boundary(self):
        for scenario in ("token_only", "email_tokens", "password_only", "expired_token", "different_account",
                         "account_session_mismatch", "email_mismatch", "generation_changed", "writeback_failure", "cancelled", "interactive_cancelled"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.executable), scenario], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASSED: " + scenario, result.stdout)
