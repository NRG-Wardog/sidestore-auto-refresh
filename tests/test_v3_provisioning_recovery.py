"""Behavioural coverage for the post-authentication provisioning path.

The physical defect this file locks down: Apple authentication succeeded, 2FA
succeeded, the account persisted, and then provisioning failed. The UI presented
that as a provisioning error with no classified cause, and the recovery actions
were a misleading generic Retry / Cancel.

Rules enforced here:
- An authenticated terminal is never collapsed into a sign-in failure.
- A successful Apple sign-in and a failed provisioning step are two separate
  visible facts.
- SideStore.OperationError is classified by its typed case. The bridged NSError
  integer is never used as a semantic API, and no associated value is forwarded.
- Retry Provisioning reuses the authenticated session; Finish Later keeps the
  account and reconciles against authoritative SideStore state.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
WIRE = ROOT / "scripts/templates/v3_wire_contract.swift"
FAILURE = ROOT / "scripts/templates/combined_failure.swift"
PINNED = ROOT / ".audit/v103-sources/SideStore/SideStore/Core/Operations/Errors/OperationError.swift"

# Every typed case the physical failure can realistically surface after Apple
# authentication has already succeeded, per the defect report.
REQUIRED_TYPED_CASES = (
    "noConnection", "noVPN", "invalidVPN", "noDevice", "notReachable",
    "invalidPairingFile", "minimuxerNotStarted", "pairingNotComplete",
    "unknownUDID", "notAuthenticated", "certificateRevoked",
    "customCertificateRevoked", "customCertificateExpired", "certificateExpired",
    "certificateChanged", "missingProvisioningProfile", "provisioningError",
    "missingAppGroup", "forbidden", "timedOut", "connectionFailed",
)


def runtime():
    return RUNTIME.read_text(encoding="utf-8")


def shell():
    return SHELL.read_text(encoding="utf-8")


def guidance_function() -> str:
    text = runtime()
    start = text.index("func v3OperationErrorGuidance(")
    end = text.index("\n}", start)
    return text[start:end]


def auth_store() -> str:
    text = shell()
    start = text.index("final class V3AuthStore")
    end = text.index("struct V3SignInLink")
    return text[start:end]


def sign_in_view() -> str:
    text = shell()
    start = text.index("struct V3SignInView")
    end = text.index("struct V3CertificateRow")
    return text[start:end]


class TypedOperationErrorClassificationTests(unittest.TestCase):
    """Item 2 / 5E: OperationError gets typed provisioning guidance."""

    def test_typed_operation_error_branch_precedes_the_generic_fallthrough(self):
        body = runtime()
        start = body.index("func resolveProvisioningError(")
        end = body.index("private func askProvisioningRetry", start)
        classifier = body[start:end]
        self.assertIn("if error is CancellationError { return .cancel }", classifier)
        self.assertIn("if let operation = error as? OperationError {", classifier)
        self.assertIn("v3OperationErrorGuidance(operation)", classifier)
        # The typed branch must be reached before the unclassified fallthrough.
        self.assertLess(classifier.index("as? OperationError"),
                        classifier.index("unexpected failure"))

    def test_developer_portal_guidance_is_preserved(self):
        # Item 5D: the existing typed portal guidance must not be replaced.
        body = runtime()
        start = body.index("func resolveProvisioningError(")
        end = body.index("private func askProvisioningRetry", start)
        classifier = body[start:end]
        self.assertIn("if let portal = error as? DeveloperPortalError {", classifier)
        self.assertIn("v3ProvisioningGuidance(portal)", classifier)
        self.assertIn("if case .userCancelled = portal { return .cancel }", classifier)
        self.assertIn("func v3ProvisioningGuidance(_ error: DeveloperPortalError)", body)
        self.assertIn("@unknown default:", body)

    def test_every_required_operation_error_case_has_typed_guidance(self):
        function = guidance_function()
        for name in REQUIRED_TYPED_CASES:
            self.assertIn(f"case .{name}:", function,
                          f"OperationError.{name} must have typed provisioning guidance")
        self.assertIn("default:", function)

    def test_no_numeric_code_is_used_as_a_semantic_api(self):
        # A bridged NSError integer is not a stable semantic identifier:
        # OperationError conforms to CustomNSError but implements neither
        # errorCode nor errorDomain, so every case bridges to code 0.
        function = guidance_function()
        for forbidden in ("native.code", "native.domain", "underlyingCode ==",
                          "underlyingCode)", "rawValue", "errorCode", "NSError("):
            self.assertNotIn(forbidden, function,
                             f"{forbidden} would reintroduce a numeric classification")
        self.assertNotIn("case 29", function)
        self.assertNotIn("== 29", function)
        classifier = runtime()
        start = classifier.index("func resolveProvisioningError(")
        end = classifier.index("private func askProvisioningRetry", start)
        for forbidden in ("native.code", "native.domain", "as NSError).code"):
            self.assertNotIn(forbidden, classifier[start:end])

    def test_typed_cases_match_the_pinned_upstream_enum(self):
        # Guards against a case label that does not exist upstream, which is a
        # hard build failure in the SideStore target.
        if not PINNED.exists():
            self.skipTest("pinned SideStore sources unavailable")
        pinned = PINNED.read_text(encoding="utf-8")
        declared = set(re.findall(r"^\s{4}case ([A-Za-z_][A-Za-z0-9_]*)", pinned, re.M))
        used = set(re.findall(r"case \.([A-Za-z_][A-Za-z0-9_]*):", guidance_function()))
        unknown = used - declared
        self.assertEqual(unknown, set(),
                         f"guidance references cases absent from OperationError: {sorted(unknown)}")

    def test_no_associated_value_is_forwarded(self):
        # unknown/forbidden embed #fileID and #line; provisioningError embeds the
        # raw portal result; cacheClearError embeds upstream strings.
        function = guidance_function()
        for forbidden in ("localizedDescription", "rawDescription", "failureReason",
                          "let reason", "let result", "let message", "let errors",
                          "let appName", "let activeTeam", "let file", "let line",
                          "let name:", "let error"):
            self.assertNotIn(forbidden, function,
                             f"{forbidden} would forward a private associated value")
        # No case may bind a payload at all.
        for match in re.finditer(r"case \.[A-Za-z_][A-Za-z0-9_]*\(([^)]*)\)", function):
            self.assertEqual(match.group(1).strip(), "",
                             "a provisioning guidance case must not bind an associated value")

    def test_guidance_never_claims_a_credential_problem(self):
        function = guidance_function()
        for forbidden in ("password", "two-factor", "verification code", "credentials"):
            self.assertNotIn(forbidden, function.lower(),
                             f"provisioning guidance must not blame {forbidden}")

    def test_unknown_cases_stay_honestly_unclassified(self):
        function = guidance_function()
        self.assertIn("SideStore could not finish provisioning for a reason it does not classify.", function)

    def test_technical_details_remain_diagnostic_only(self):
        # domain=/code=/area=provisioning/correlation stay, and no payload is
        # interpolated into the copied technical line.
        body = runtime()
        start = body.index("private func askProvisioningRetry")
        end = body.index("func resolvePostAuth", start)
        prompt = body[start:end]
        for token in ("domain=", "code=", "area=provisioning", "correlation=", '"technical"'):
            self.assertIn(token, prompt)
        for forbidden in ("reason", "failureReason", "localizedDescription", "errors"):
            self.assertNotIn(forbidden, prompt)


class RecoveryActionLabelTests(unittest.TestCase):
    """Item 4: the recovery actions describe provisioning, not a failed sign-in."""

    def test_prompt_offers_retry_provisioning_and_finish_later(self):
        body = runtime()
        start = body.index("private func askProvisioningRetry")
        end = body.index("func resolvePostAuth", start)
        prompt = body[start:end]
        self.assertIn('["id": "retry", "label": "Retry Provisioning"]', prompt)
        self.assertIn('["id": "cancel", "label": "Finish Later"]', prompt)
        # Finish Later is still an upstream .cancel; a third decision case is
        # never fabricated.
        self.assertIn('return answer["choice"] == "retry" ? .retry : .cancel', prompt)
        self.assertNotIn('"label": "Cancel"', prompt)

    def test_host_offers_retry_provisioning_and_finish_later(self):
        view = sign_in_view()
        self.assertIn('Label("Retry Provisioning"', view)
        self.assertIn('Button("Finish Later")', view)
        self.assertIn("auth.retryProvisioning()", view)
        # The generic pair must be gone from the provisioning recovery path.
        recovery = view[view.index("Retry Provisioning") - 400:]
        self.assertNotIn('Label("Retry", systemImage:', recovery)

    def test_retry_provisioning_uses_a_distinct_operation(self):
        store = auth_store()
        self.assertIn('request(operation: "authRetryProvisioning")', store)
        # A second interactive begin would re-request credentials and 2FA.
        self.assertEqual(shell().count('request(operation: "authBegin")'), 1)
        self.assertIn("func retryProvisioning()", store)
        self.assertIn("func runProvisioningRetry()", store)
        self.assertIn("func finishProvisioningLater()", store)

    def test_finish_later_preserves_the_account_and_reloads(self):
        view = sign_in_view()
        self.assertIn("private func finishProvisioningLater()", view)
        self.assertIn("auth.finishProvisioningLater()", view)
        self.assertIn("status.reload()", view[view.index("private func finishProvisioningLater()"):])
        # Finishing later must never sign out or cancel the saved session.
        finish = auth_store()
        finish = finish[finish.index("func finishProvisioningLater()"):]
        finish = finish[:finish.index("\n    }")]
        for forbidden in ("signOut", "authCancel", '"authCancel"'):
            self.assertNotIn(forbidden, finish)

    def test_retry_is_blocked_when_no_session_can_be_reused(self):
        body = runtime()
        start = body.index("if mode == .resumeProvisioning {")
        end = body.index("if let current = activeID {", start)
        guard = body[start:end]
        # Both the keychain session and its ownership by the account whose
        # provisioning failed must hold, otherwise credentials would be skipped
        # for a session that cannot actually resume.
        self.assertIn("let sessionAppleID = AuthManager.shared.currentAppleID?.lowercased()", guard)
        self.assertIn("guard AuthManager.shared.isAuthenticated, let resumable, !resumable.appleID.isEmpty,", guard)
        self.assertIn("resumable.appleID == sessionAppleID else {", guard)
        self.assertIn("Sign in again with this Apple ID", guard)
        # The refusal is explicit rather than a silent interactive fallback.
        self.assertIn('"state": "failed"', guard)


class AuthSuccessIsNotProvisioningSuccessTests(unittest.TestCase):
    """Items 1 / 3 / 5A / 5B: the two facts are presented separately."""

    def test_authenticated_terminal_never_becomes_a_sign_in_failure(self):
        store = auth_store()
        apply_start = store.index("private func apply(")
        apply = store[apply_start:store.index("static func failureMessage", apply_start)]
        self.assertIn('if state == "authenticatedProvisioningIncomplete" {', apply)
        self.assertIn('message = "Apple ID signed in successfully."', apply)
        # The classified provisioning payload must be retained, not dropped.
        for token in ('reply["message"]', 'reply["stage"]', 'reply["code"]',
                      'reply["technicalDetails"]', 'reply["resumable"]'):
            self.assertIn(token, apply)
        # The generic Sign-in Failed state is reserved for a real auth failure.
        self.assertIn('} else if state == "failed" {', apply)

    def test_visible_state_shows_both_facts(self):
        view = sign_in_view()
        self.assertIn('Label("Signed in successfully"', view)
        self.assertIn('Text("Provisioning needs attention")', view)
        self.assertIn('Text("Provisioning could not be completed.")', view)
        self.assertIn("auth.hasProvisioningProblem", view)
        # The green signed-in fact is not conditional on provisioning failing.
        self.assertIn("if auth.isSignedIn {", view)

    def test_sign_in_view_never_offers_a_plain_retry_for_provisioning(self):
        view = sign_in_view()
        recovery = view[view.index("V3_PROVISIONING_RECOVERY_ACTIONS_V1"):]
        self.assertIn("Retry Provisioning", recovery)
        self.assertIn("Finish Later", recovery)
        self.assertNotIn('Label("Try Again"', recovery)

    def test_provisioning_terminal_has_one_uniform_wire_shape(self):
        body = runtime()
        start = body.index('if authenticatedOutcome == "authenticatedProvisioningIncomplete" {')
        end = body.index("} else if cancelled {", start)
        terminal = body[start:end]
        for token in ('"outcome"', '"resumable"', '"stage"', '"code"',
                      '"failure"', '"technicalDetails"'):
            self.assertIn(token, terminal,
                          f"{token} must be present on both the cancelled and failed paths")
        self.assertIn("provisioningCancelled", terminal)
        self.assertIn("provisioningFailed", terminal)
        # The failure payload is no longer conditionally omitted.
        self.assertNotIn("if !cancelled {", terminal)

    def test_snapshot_reports_the_authenticated_session_separately(self):
        service = SERVICE.read_text(encoding="utf-8")
        start = service.index("private func snapshot()")
        snapshot = service[start:]
        self.assertIn('"authenticated": authenticated', snapshot)
        self.assertIn('"provisioningIncomplete": authenticated && activeAccount == nil', snapshot)
        self.assertIn("AuthManager.shared.currentAppleID", snapshot)
        # The old unconditional "Not signed in" shortcut is gone.
        self.assertNotIn('DatabaseManager.shared.activeAccount()?.appleID ?? "Not signed in"', snapshot)

    def test_host_treats_an_authenticated_session_as_signed_in(self):
        text = shell()
        self.assertIn("var needsSignIn: Bool { account == \"Not signed in\" && !authenticated }", text)
        self.assertIn("authenticated = snapshot[\"authenticated\"] as? Bool ?? false", text)
        self.assertIn("provisioningIncomplete = snapshot[\"provisioningIncomplete\"] as? Bool ?? false", text)

    def test_reconcile_resolves_finish_later_as_signed_in(self):
        store = auth_store()
        start = store.index("func reconcile()")
        reconcile = store[start:store.index("private func run()", start)]
        self.assertIn("let authoritative =", reconcile)
        self.assertIn("signedIn = true", reconcile)
        self.assertIn("if incomplete {", reconcile)
        # A finished-later attempt must not fall back to "idle" / sign in again.
        self.assertNotIn('state = "idle"\n                team = ""\n            } else if', reconcile)
        self.assertIn('if authoritative {', reconcile)


class ResumableProvisioningOperationTests(unittest.TestCase):
    """Item 5C: the retry path is a real wire operation, not a second sign-in."""

    def test_operation_is_allowed_on_the_wire_and_is_a_mutation(self):
        wire = WIRE.read_text(encoding="utf-8")
        self.assertIn('"authRetryProvisioning"', wire)
        start = wire.index("static let operations")
        read = wire[wire.index("static let readOperations"):]
        self.assertNotIn('"authRetryProvisioning"', read,
                         "a provisioning retry mutates backend state and must hold the gate")

    def test_service_routes_the_operation_to_the_resume_mode(self):
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn('case "authRetryProvisioning":', service)
        self.assertIn("begin(deadline: deadline, mode: .resumeProvisioning)", service)
        self.assertIn('"authRetryProvisioning", "accountExport"', service)

    def test_operation_normalizes_to_the_sign_in_failure_vocabulary(self):
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn('"authRetryProvisioning": "signIn"', failure)


    def test_pinned_cases_with_defaulted_payloads_are_supplied_explicitly(self):
        # A SideStore.OperationError case that declares a default for its
        # associated value is exposed as a synthesised static function. Outside a
        # switch, a bare `.case` reference therefore names that function and does
        # not compile. The typed harness must always supply the payload, so this
        # is caught without a Swift round-trip.
        if not PINNED.exists():
            self.skipTest("pinned SideStore sources unavailable")
        pinned = PINNED.read_text(encoding="utf-8")
        defaulted = set()
        for line in pinned.splitlines():
            match = re.match(r"^\s{4}case ([A-Za-z_][A-Za-z0-9_]*)\((.*)\)\s*$", line)
            if match and "=" in match.group(2):
                defaulted.add(match.group(1))
        self.assertIn("noConnection", defaulted, "pinned enum shape changed")
        harness = (ROOT / "tests/fixtures/v3_provisioning_typed_guidance_harness.swift").read_text(encoding="utf-8")
        offenders = []
        for name in sorted(defaulted):
            for match in re.finditer(rf"v3OperationErrorGuidance\(\s*\.{name}\s*\)", harness):
                offenders.append(name)
        self.assertEqual(offenders, [],
                         f"a defaulted-payload case is referenced bare: {offenders}")
        # Every reference is explicitly qualified or supplies its payload.
        for match in re.finditer(r"v3OperationErrorGuidance\(\s*\.([A-Za-z_][A-Za-z0-9_]*)", harness):
            name = match.group(1)
            if name in defaulted:
                self.fail(f".{name} needs an explicit payload in the harness")
        # The production switch may use bare patterns; only expression position
        # is affected, so the guidance function is expected to keep them.
        for name in ("noConnection", "noVPN", "invalidPairingFile"):
            self.assertIn(f"case .{name}:", guidance_function())


if __name__ == "__main__":
    unittest.main()
