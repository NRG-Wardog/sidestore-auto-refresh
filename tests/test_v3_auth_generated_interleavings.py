"""Execute identity-race cases against the generated pinned AuthManager source."""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load production patch module {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service = load_module("patch_v3_service")
PINNED_REF = service.PINS[1]
AUTH_PATH = "SideStore/Core/Auth/AuthManager.swift"
SIGN_IN_PATH = "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift"
COALESCER_PATH = "SideStore/Utils/concurrency/TaskChainCoalescer.swift"


def swift_declaration(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"pinned source declaration missing: {signature}")
    opening = source.find("{", start)
    if opening < 0:
        raise AssertionError(f"pinned source declaration has no body: {signature}")
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(source)):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated pinned source declaration: {signature}")


def pinned_source(checkout: Path, relative: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(checkout), "show", f"{PINNED_REF}:{relative}"],
        text=True,
        encoding="utf-8",
    )


def generated_auth_manager(checkout: Path) -> str:
    source = pinned_source(checkout, AUTH_PATH)
    source = service.headless_auth_manager(source)
    source = service.apply_embedded_credential_snapshot_patch(source, "patch_auth_manager")
    return service.patch_auth_identity_generation(source)


def generated_sign_in_operation(checkout: Path) -> str:
    source = pinned_source(checkout, SIGN_IN_PATH)
    source = service.patch_sign_in_operation(source)
    source = service.apply_embedded_credential_snapshot_patch(source, "patch_sign_in_operation")
    return service.headless_certificate_serial_log_redaction(source, "SignInOperation")


def declarations_for_harness(auth: str, sign_in: str, coalescer: str) -> str:
    members = [
        next(line.strip() for line in auth.splitlines()
             if "private let v3IdentityStampState = V3AuthIdentityStampState()" in line),
        swift_declaration(auth, "    var v3IdentityGeneration: UInt64"),
        swift_declaration(auth, "    var v3IdentityStamp: String"),
        swift_declaration(auth, "    var v3IdentityIsStable: Bool"),
        swift_declaration(auth, "    func v3BeginIdentityTransition()"),
        swift_declaration(auth, "    func v3CompleteIdentityTransition()"),
        swift_declaration(auth, "    func v3ReplaceSession(_ session: ALTAppleAPISession?)"),
        swift_declaration(auth, "    func v3InstallSessionIfCurrent(_ session: ALTAppleAPISession"),
        swift_declaration(auth, "    func v3CachedSessionMatchesCurrentRoute(_ session: ALTAppleAPISession?)"),
        swift_declaration(auth, "    func v3AdvanceIdentityGeneration()"),
        swift_declaration(auth, "    var authenticationSnapshot: LCEmbeddedAuthenticationSnapshot?"),
        swift_declaration(auth, "    public var currentAppleID: String?"),
        swift_declaration(auth, "    public var password: String?"),
        swift_declaration(auth, "    public var adsid: String?"),
        swift_declaration(auth, "    public var xcodeToken: String?"),
        swift_declaration(auth, "    public func signOut("),
        swift_declaration(auth, "    public func getAuthenticatedSession()"),
    ]
    state = swift_declaration(
        (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8"),
        "final class V3AuthIdentityStampState",
    )
    behavior = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
    production_policies = [
        swift_declaration(behavior, "enum V3AuthReadStampPolicy"),
        swift_declaration(behavior, "enum V3AuthSessionCoalescerKey"),
        swift_declaration(behavior, "enum V3AuthIdentityBindingPolicy"),
        swift_declaration(behavior, "enum V3ProvisioningResumeExecutionPolicy"),
    ]
    runtime_template = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
    provisioning_resume_error = swift_declaration(
        runtime_template, "struct V3ProvisioningResumeUnavailableError",
    )
    keychain_template = (ROOT / "scripts/templates/embedded_shared_keychain.swift").read_text(encoding="utf-8")
    snapshot = swift_declaration(keychain_template, "struct LCEmbeddedAuthenticationSnapshot")
    cached_execute = swift_declaration(sign_in, "    override func execute(parentProgress: Progress?) async throws -> SignInResult")
    sign_in_fields = """
    let v3ForceProvisioningRetry = false
    let skipCertificateProvisioning = true
    var isCancelled = false
    var cachedPathAnisetteCalls = 0
    var startAuthenticationCalls = 0

    func getAnisetteData() async throws -> ALTAnisetteData {
        cachedPathAnisetteCalls += 1
        return ALTAnisetteData(value: "cached-path-anisette")
    }
    func setProgress(_ value: Int64) {}
    func startAuthentication(reportProgress: @escaping @Sendable (Int64) -> Void) async throws -> SignInResult {
        startAuthenticationCalls += 1
        throw HarnessError.authenticationFallback
    }
    func finalizeAuthentication(result: Result<SignInResult, Error>) async throws {}
    func debugLog(_ message: String) {}
    func verboseLog(_ message: String) {}
"""
    return "\n".join([
        "import Foundation\nimport CoreFoundation",
        snapshot,
        *production_policies,
        provisioning_resume_error,
        state,
        coalescer,
        """
        struct ALTAnisetteData: Sendable { let value: String }
        struct ALTAppleAPISession: Sendable {
            let dsid: String?
            let authToken: String?
            var anisetteData: ALTAnisetteData
            let xcodeVersion: String
        }
        struct ALTTeam { let identifier: String; let name: String; let type: String; let account: ALTAccount? = nil }
        struct ALTAccount { let appleID: String }
        struct ALTCertificate: Equatable { let marker: String }
        struct CertificateRecord: Equatable { let certificate: ALTCertificate }
        struct SignInResult { let team: ALTTeam; let certificate: ALTCertificate?; let session: ALTAppleAPISession }
        enum HarnessError: Error { case authenticationFallback }
        enum OperationError: Error { case notAuthenticated; case cancelled }

        actor AnisetteGate {
            static let shared = AnisetteGate()
            private var next = 0
            private var waiters: [Int: CheckedContinuation<ALTAnisetteData, Error>] = [:]
            private var countWaiters: [(Int, CheckedContinuation<Void, Never>)] = []
            func fetch() async throws -> ALTAnisetteData {
                try await withCheckedThrowingContinuation { continuation in
                    let index = next
                    next += 1
                    waiters[index] = continuation
                    let ready = countWaiters.filter { $0.0 <= next }
                    countWaiters.removeAll { $0.0 <= next }
                    ready.forEach { $0.1.resume() }
                }
            }
            func waitForCount(_ target: Int) async {
                if next >= target { return }
                await withCheckedContinuation { countWaiters.append((target, $0)) }
            }
            func release(_ index: Int) {
                guard let waiter = waiters.removeValue(forKey: index) else { fatalError("missing Anisette waiter \\(index)") }
                waiter.resume(returning: ALTAnisetteData(value: "anisette-\\(index)"))
            }
        }
        enum AnisetteProvider {
            static func fetch() async throws -> ALTAnisetteData { try await AnisetteGate.shared.fetch() }
        }
        final class AnisetteConfigManager {
            static let shared = AnisetteConfigManager()
            func resolvedXcodeVersion() async -> String { "Xcode-test" }
            func resetToDefaults() {}
        }
        final class Keychain {
            static let shared = Keychain()
            private let lock = NSLock()
            private var email: String?
            private var passwordValue: String?
            private var dsidValue: String?
            private var tokenValue: String?
            func authenticationSnapshot() throws -> LCEmbeddedAuthenticationSnapshot? {
                lock.lock(); defer { lock.unlock() }
                return LCEmbeddedAuthenticationSnapshot(appleIDEmailAddress: email, appleIDPassword: passwordValue,
                    appleIDAdsid: dsidValue, appleIDXcodeToken: tokenValue)
            }
            func writeAuthenticationCredentials(appleID: String, password: String, dsid: String, authToken: String) throws {
                lock.lock(); defer { lock.unlock() }
                email = appleID; passwordValue = password; dsidValue = dsid; tokenValue = authToken
            }
            func clearSignInInfo(keepAnisetteData: Bool) {
                lock.lock(); defer { lock.unlock() }
                email = nil; passwordValue = nil; dsidValue = nil; tokenValue = nil
            }
            func embeddedAuthenticationFailure(_ error: Error) -> NSError { error as NSError }
            var appleIDEmailAddress: String? { get { snapshot().appleIDEmailAddress } set { lock.lock(); email = newValue; lock.unlock() } }
            var appleIDPassword: String? { get { snapshot().appleIDPassword } set { lock.lock(); passwordValue = newValue; lock.unlock() } }
            var appleIDAdsid: String? { get { snapshot().appleIDAdsid } set { lock.lock(); dsidValue = newValue; lock.unlock() } }
            var appleIDXcodeToken: String? { get { snapshot().appleIDXcodeToken } set { lock.lock(); tokenValue = newValue; lock.unlock() } }
            private func snapshot() -> LCEmbeddedAuthenticationSnapshot {
                lock.lock(); defer { lock.unlock() }
                return LCEmbeddedAuthenticationSnapshot(appleIDEmailAddress: email, appleIDPassword: passwordValue,
                    appleIDAdsid: dsidValue, appleIDXcodeToken: tokenValue)
            }
        }
        final class CertificateManager {
            static let shared = CertificateManager()
            var activeCertificate: CertificateRecord? = nil
            func clearActiveCertificate() {}
        }
        final class DatabaseManager {
            static let shared = DatabaseManager()
            func deactivateActiveAccountAndTeam() {}
        }
        final class AnisetteDataManager {
            static let shared = AnisetteDataManager()
            func clearCache() {}
        }
        final class SideSignConfigManager {
            static let shared = SideSignConfigManager()
            func resetToDefaults() {}
        }
        func debugLog(_ message: String) {}

        final class AuthManager: @unchecked Sendable {
            static let shared = AuthManager()
            private init() {}
            var team: ALTTeam?
            var session: ALTAppleAPISession?
        """,
        *members,
        """
        }

        class BaseStandaloneOperation<Context, Output> {
            func executePreconditionCheck(parentProgress: Progress?) async throws {}
            func execute(parentProgress: Progress?) async throws -> Output { fatalError("abstract") }
        }
        final class SignInOperation: BaseStandaloneOperation<Int, SignInResult> {
            __SIGN_IN_FIELDS__
            // execute() compiles the pinned retry branch, but this harness only
            // exercises the ordinary cached-session fallback. Reaching retry
            // provisioning must fail the harness instead of simulating success.
            func provisioningLoop(account: ALTAccount, session: ALTAppleAPISession,
                                  reportProgress: @escaping @Sendable (Int64) -> Void) async throws -> SignInResult {
                fatalError("unexpected provisioning retry path in cached-session harness")
            }
            __CACHED_EXECUTE__
        }

        @main struct AuthGeneratedInterleavingHarness {
            static func expectNotAuthenticated(_ task: Task<ALTAppleAPISession, Error>, _ label: String) async throws {
                do { _ = try await task.value; fatalError("\\(label): stale operation returned a session") }
                catch is OperationError { }
            }
            static func main() async throws {
                let auth = AuthManager.shared
                auth.currentAppleID = "a@example.test"; auth.adsid = "dsid-A"; auth.xcodeToken = "token-A"
                let a = Task { try await auth.getAuthenticatedSession() }
                await AnisetteGate.shared.waitForCount(1)
                auth.currentAppleID = "b@example.test"; auth.adsid = "dsid-B"; auth.xcodeToken = "token-B"
                let b = Task { try await auth.getAuthenticatedSession() }
                await AnisetteGate.shared.waitForCount(2)
                await AnisetteGate.shared.release(1)
                let sessionB = try await b.value
                precondition(sessionB.dsid == "dsid-B" && sessionB.authToken == "token-B")
                await AnisetteGate.shared.release(0)
                try await expectNotAuthenticated(a, "credential replacement")
                precondition(auth.session?.dsid == "dsid-B" && auth.session?.authToken == "token-B",
                    "late A result overwrote B session")

                auth.currentAppleID = "signout@example.test"; auth.adsid = "dsid-signout"; auth.xcodeToken = "token-signout"
                let beforeSignOut = Task { try await auth.getAuthenticatedSession() }
                await AnisetteGate.shared.waitForCount(3)
                auth.signOut(keepCertificate: true)
                await AnisetteGate.shared.release(2)
                try await expectNotAuthenticated(beforeSignOut, "sign out")
                precondition(auth.session.map { $0.dsid == "dsid-signout" } == nil,
                    "sign out must leave no session")

                auth.currentAppleID = "rotate@example.test"; auth.adsid = "dsid-rotate"; auth.xcodeToken = "token-old"
                let oldToken = Task { try await auth.getAuthenticatedSession() }
                await AnisetteGate.shared.waitForCount(4)
                auth.xcodeToken = "token-new"
                let newToken = Task { try await auth.getAuthenticatedSession() }
                await AnisetteGate.shared.waitForCount(5)
                await AnisetteGate.shared.release(4)
                let rotated = try await newToken.value
                precondition(rotated.dsid == "dsid-rotate" && rotated.authToken == "token-new")
                await AnisetteGate.shared.release(3)
                try await expectNotAuthenticated(oldToken, "same-DSID token rotation")

                auth.team = ALTTeam(identifier: "team", name: "Team", type: "free")
                auth.session = ALTAppleAPISession(dsid: "dsid-rotate", authToken: "token-old",
                    anisetteData: ALTAnisetteData(value: "stale"), xcodeVersion: "Xcode-test")
                let operation = SignInOperation()
                do { _ = try await operation.execute(parentProgress: nil); fatalError("stale cached session succeeded") }
                catch is HarnessError { }
                precondition(operation.cachedPathAnisetteCalls == 0,
                    "generated cached SignInOperation path used a session with a stale token")
                precondition(operation.startAuthenticationCalls == 1,
                    "stale cached session did not fall back to fresh authentication")
                print("PASS: generated AuthManager interleavings and cached SignInOperation rejection")
            }
        }
        """,
    ]).replace("__SIGN_IN_FIELDS__", sign_in_fields).replace("__CACHED_EXECUTE__", cached_execute)


def source_checkout() -> Path:
    return Path(os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or
                os.environ.get("SIDESTORE_TEST_SOURCE") or
                str(ROOT.parents[2] / "v3-side-upstream"))


class GeneratedAuthInterleavingTests(unittest.TestCase):
    def test_generated_sources_are_pinned_and_expose_executable_auth_paths(self):
        checkout = source_checkout()
        if not checkout.is_dir():
            self.skipTest("pinned SideStore checkout unavailable; set EMBEDDED_SIDESTORE_TEST_SOURCE in macOS CI")
        revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                           text=True, encoding="utf-8").strip()
        self.assertEqual(PINNED_REF, revision, "source checkout must be exactly pinned for generated auth execution")
        auth = generated_auth_manager(checkout)
        sign_in = generated_sign_in_operation(checkout)
        coalescer = pinned_source(checkout, COALESCER_PATH)
        self.assertIn("V3_AUTH_IDENTITY_GENERATION_V1", auth)
        self.assertIn("V3AuthSessionCoalescerKey.value(for: identityAtStart.stamp)", auth)
        self.assertIn("V3_PROVISIONING_RETRY_BYPASSES_CACHED_SIGNIN_V1", sign_in)
        self.assertIn("AuthManager.shared.v3CachedSessionMatchesCurrentRoute(session)", sign_in)
        self.assertIn("actor TaskChainCoalescer", coalescer)
        executable_source = declarations_for_harness(auth, sign_in, coalescer)
        self.assertIn("return try await TaskChainCoalescer.shared.coalesce", executable_source)
        self.assertIn("v3InstallSessionIfCurrent", executable_source)
        self.assertIn("override func execute(parentProgress:", executable_source)

    def test_generated_authmanager_interleavings_and_cached_signin_guard(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable; executable generated AuthManager harness runs in macOS CI")
        checkout = source_checkout()
        if not checkout.is_dir():
            self.skipTest("pinned SideStore checkout unavailable; set EMBEDDED_SIDESTORE_TEST_SOURCE in macOS CI")
        revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                           text=True, encoding="utf-8").strip()
        self.assertEqual(PINNED_REF, revision, "source checkout must be exactly pinned for generated auth execution")
        auth = generated_auth_manager(checkout)
        sign_in = generated_sign_in_operation(checkout)
        coalescer = pinned_source(checkout, COALESCER_PATH)
        self.assertIn("V3_AUTH_IDENTITY_GENERATION_V1", auth)
        self.assertIn("V3_PROVISIONING_RETRY_BYPASSES_CACHED_SIGNIN_V1", sign_in)
        harness = declarations_for_harness(auth, sign_in, coalescer)
        with tempfile.TemporaryDirectory() as directory:
            swift = Path(directory) / "AuthGeneratedInterleavingHarness.swift"
            binary = Path(directory) / "auth-generated-interleavings"
            swift.write_text(harness, encoding="utf-8")
            compiled = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library",
                                       str(swift), "-o", str(binary)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(0, compiled.returncode, compiled.stdout + compiled.stderr)
            executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, executed.returncode, executed.stdout + executed.stderr)
            self.assertIn("PASS: generated AuthManager interleavings", executed.stdout)


if __name__ == "__main__":
    unittest.main()
