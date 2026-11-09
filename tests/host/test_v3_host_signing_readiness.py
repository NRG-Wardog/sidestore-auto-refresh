from tests.support.product_sources import ProductSource
"""Current installed-host compatibility stays distinct from successful history."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ProductSource('v3_headless_runtime.swift')
PRIMITIVES = ProductSource('v3_behavioral_primitives.swift')
SHELL = ProductSource('v3_unified_shell.swift')


def declaration(source, signature):
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
    raise AssertionError("Unterminated declaration: " + signature)


from tests.owner_source import owner_source
from tests.support.certificate_log_privacy import swift_log_call_ranges


def pinned_validator(test):
    return owner_source("SideStore/Core/Certificates/CodeSignValidator.swift", "SideStore")


class HostSigningReadinessTests(unittest.TestCase):
    def test_local_only_health_is_allowlisted_and_does_not_use_network(self):
        service = (ProductSource('v3_sidestore_service.swift')).read_text()
        wire = ProductSource("combined_failure.swift").read_text() + "\n" + ProductSource("v3_wire_contract.swift").read_text()
        runtime = RUNTIME.read_text()
        command = service[service.index('        case "healthSnapshot":'):service.index('        case "accountExport":')]
        self.assertIn('if target == "hostSigningOnly"', command)
        self.assertIn('return await V3BackendCommands.hostSigningHealth()', command)
        self.assertIn('if operation == "healthSnapshot", !["", "hostSigningOnly"].contains(target) { return nil }', wire)
        local = declaration(runtime, "    static func hostSigningHealth() async")
        for forbidden in ("OCSP", "certificateState", "DeveloperPortal", "Anisette"):
            self.assertNotIn(forbidden, local)
        snapshot = declaration(service, "    private func snapshot() throws")
        self.assertIn('"hostSigningContext": identityReadStable ? v3CurrentHostSigningContext()?.digest', snapshot)
        for forbidden in ("CodeSignValidator", "getSigningCertificate", "V3InstalledHostSigningReader"):
            self.assertNotIn(forbidden, snapshot)
        observation = declaration(SHELL.read_text(), "    private func observeSetupFacts() async")
        self.assertIn('target: jitlessRequired ? "" : "hostSigningOnly"', observation)

    def test_context_and_async_reply_remain_owned_by_current_identity(self):
        runtime = RUNTIME.read_text()
        context = declaration(runtime, "func v3CurrentHostSigningContext()")
        for expected in ("auth.v3IdentityIsStable", "credentials.isAuthenticated", "team.account?.identifier == account.identifier",
                         "V3AuthIdentityBindingPolicy.mayUseTeam", "certificate.x509.data", "account.identifier", "team.identifier"):
            self.assertIn(expected, context)
        for forbidden in ("appleIDXcodeToken", "appleIDAdsid", "p12Data", "password", "debugLog"):
            self.assertNotIn(forbidden, context)
        observation = declaration(runtime, "    static func hostSigningHealth() async")
        self.assertLess(observation.index("await V3InstalledHostSigningReader"),
                        observation.index("AuthManager.shared.v3IdentityStamp == stamp"))
        self.assertIn("v3CurrentHostSigningContext()?.digest == context.digest", observation)
        shell = SHELL.read_text()
        self.assertIn("invalidateSetupFacts()", declaration(shell, "    func statusAuthorityInvalidated()"))
        self.assertIn("installedHostSigning = V3HostSigningObservation()", declaration(shell, "    func invalidateSetupFacts()"))
        self.assertIn('identityStamp != incomingIdentityStamp || hostSigningContext != incomingSigningContext', shell)
        disconnect = shell[shell.index('                connected = false\n'):]
        self.assertIn("invalidateSetupFacts()", disconnect[:150])

    def test_readiness_only_does_not_replace_full_identity_and_full_reload_repairs(self):
        service = (ProductSource('v3_sidestore_service.swift')).read_text()
        shell = SHELL.read_text()
        snapshot_dispatch = service[service.index('        case "snapshot":'):service.index('        case "directRecoveryInspect":')]
        self.assertIn('return ["ready": DatabaseManager.shared.isStarted]', snapshot_dispatch)
        self.assertIn('return try snapshot()', snapshot_dispatch)
        accept = declaration(shell, "    func accept(_ snapshot:")
        self.assertLess(accept.index('guard let incomingIdentityStamp'), accept.index('hostSigningContext = incomingSigningContext'))
        self.assertIn('V3ServiceBridge.shared.statusReplyMayApply(snapshot)', accept)
        self.assertIn('hostSigningContext != incomingSigningContext', accept)
        self.assertIn('invalidateSetupFacts()', accept)
        reload = declaration(shell, "    func reloadAndRecalculate(status:")
        self.assertLess(reload.index('await status.reloadAndWait()'), reload.index('await recalculate(status: status)'))
        recalculate = declaration(shell, "    func recalculate(status:")
        self.assertIn('if status.installedHostSigningState == .unknown', recalculate)
        self.assertIn('target: "hostSigningOnly"', recalculate)
        # The long-lived Home owner starts a new observation after a full status
        # applies, including when a prior auth event's health reply arrived early.
        perform = declaration(shell, "    private func performSnapshot() async")
        self.assertLess(perform.index('succeeded = accept(reply)'), perform.index('observeSetupFactsIfNeeded()'))
        self.assertIn('status.reload()', shell[shell.index('.onChange(of: auth.isSignedIn)'):])

    def test_changed_validator_is_required_in_collected_package_source_evidence(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import combined_build_evidence as evidence
            import verify_candidate_ipa as verifier
            name = "SideStore/Core/Certificates/CodeSignValidator.swift"
            self.assertIn(name, evidence.EMBEDDED_SOURCE_PATHS)
            self.assertIn(name, verifier.REQUIRED_GENERATED_EMBEDDED_SOURCES)
            self.assertEqual(set(evidence.EMBEDDED_SOURCE_PATHS), verifier.REQUIRED_GENERATED_EMBEDDED_SOURCES)
        finally:
            sys.path.pop(0)

    def test_completion_and_repair_action_are_separate_from_history(self):
        shell = SHELL.read_text()
        self.assertEqual(shell.count("installedHostSigningCompatible: status.installedHostSigningState == .compatible"), 2)
        self.assertIn('setup.verification.state != "complete" || status.installedHostSigningState != .compatible', shell)
        self.assertIn('result.append(("Installed Host Signing", status.installedHostSigningState.detail))', shell)
        self.assertIn('.onChange(of: setup.testRunning) { running in', shell)
        post_test = shell[shell.index('.onChange(of: setup.testRunning)'):]
        post_test = post_test[:post_test.index('.onChange(of: status.jitlessReadiness)')]
        self.assertIn('await setup.reloadAndRecalculate(status: status)', post_test)
        self.assertNotIn('runTestRefresh', post_test)
        observation = declaration(RUNTIME.read_text(), "actor V3InstalledHostSigningReader")
        self.assertNotIn("AppManager", observation)
        self.assertNotIn("refreshAll", observation)
        self.assertNotIn("UserDefaults", observation)
        self.assertIn("portalCertificates: nil", observation)
        self.assertIn(".paidSignerUnverified", observation)
        self.assertIn("static let maximumCacheAge: TimeInterval = 60", observation)
        for expected in (".systemFileNumber", ".systemNumber", ".size", ".modificationDate", ".creationDate",
                         "runningCertificate.expiryDate", "profile.expirationDate"):
            self.assertIn(expected, observation)

    def test_current_validator_is_quiet_and_owns_observed_certificate_decisions(self):
        source = pinned_validator(self)
        self.assertEqual(swift_log_call_ranges(source), [])
        self.assertIn("observedRunningCertificate: ALTX509Certificate? = nil", source)
        self.assertIn("observedRunningCertificate ?? CertificateManager.shared.getSigningCertificate", source)
        self.assertTrue(swift_log_call_ranges(source + '\ndebugLog("private signing detail")\n'))

    def test_production_validator_cache_observation_and_completion_execute(self):
        validator = pinned_validator(self)
        validator = validator.replace("import SideSign", "")
        runtime, primitives, shell = RUNTIME.read_text(), PRIMITIVES.read_text(), SHELL.read_text()
        declarations = "\n\n".join([
            declaration(primitives, "enum V3HostSigningState:"),
            declaration(primitives, "struct V3HostSigningObservation:"),
            declaration(primitives, "enum V3SetupOutstandingItem:"),
            declaration(primitives, "struct V3SetupCompletionInputs:"),
            declaration(runtime, "struct V3HostSigningContext:"),
            declaration(runtime, "actor V3InstalledHostSigningReader"),
        ])
        fixture = (ROOT / "tests/fixtures/v3_host_signing_readiness_harness.swift").read_text()
        program = fixture.replace("// PRODUCTION_DECLARATIONS", validator + "\n" + declarations)
        program = program.replace("    // PRODUCTION_OBSERVATION_COMMIT", declaration(shell, "    func recordInstalledHostSigning("))
        self.assertNotIn("// PRODUCTION_", program)
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; upstream host-signing/cache harness requires macOS CI")
        with tempfile.TemporaryDirectory() as folder:
            source, executable = Path(folder) / "main.swift", Path(folder) / "host-signing"
            source.write_text(program)
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("V3_HOST_SIGNING_READINESS_PASS", result.stdout)

    def test_wire_local_only_target_executes(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; local health wire harness requires macOS CI")
        wire = ProductSource("combined_failure.swift").read_text() + "\n" + ProductSource("v3_wire_contract.swift").read_text()
        harness = r'''
let now = Date()
for target in ["", "hostSigningOnly", "unexpected", "HOSTSIGNINGONLY"] {
    let request: [String: Any] = ["version": 1, "id": UUID().uuidString,
        "operation": "healthSnapshot", "target": target, "deadline": now.addingTimeInterval(30)]
    let data = try PropertyListSerialization.data(fromPropertyList: request, format: .binary, options: 0)
    precondition((V3WireContract.decodeRequest(data, now: now) != nil) == ["", "hostSigningOnly"].contains(target))
}
print("LOCAL_HOST_HEALTH_WIRE_PASS")
'''
        with tempfile.TemporaryDirectory() as folder:
            source, executable = Path(folder) / "main.swift", Path(folder) / "host-health-wire"
            source.write_text(wire + "\n" + harness)
            compiled = subprocess.run([compiler, str(source), "-o", str(executable)], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("LOCAL_HOST_HEALTH_WIRE_PASS", result.stdout)
