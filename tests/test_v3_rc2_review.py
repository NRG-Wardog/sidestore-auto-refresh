"""Coverage for the manual v3.0.3 RC review findings.

Each class maps to a numbered finding. Behaviour that can be executed is covered
by the Swift harnesses in tests/test_v3_behavioral_harnesses.py; this module
covers the wiring, routing and structural contracts that live in the host views
and the generated output.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
WIRE = ROOT / "scripts/templates/v3_wire_contract.swift"
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"
FAILURE = ROOT / "scripts/templates/combined_failure.swift"
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
PATCH = ROOT / "scripts/patch_v3_unified_shell.py"


def shell():
    return SHELL.read_text(encoding="utf-8")


def primitives():
    return PRIMITIVES.read_text(encoding="utf-8")


class CatalogPlistSafetyTests(unittest.TestCase):
    """P0: the catalog response must be serializable."""

    def test_rows_use_the_plist_safe_builder_and_omit_absent_optionals(self):
        service = SERVICE.read_text(encoding="utf-8")
        start = service.index('case "catalog":')
        block = service[start:service.index('case "signOut":', start)]
        self.assertIn("V3WireContract.V3PropertyListValue.dictionary([", block)
        self.assertNotIn("as [String: Any]", block)
        # The genuinely optional field is omitted, not faked.
        self.assertIn('"installedVersion": app.installedApp?.version', block)
        self.assertNotIn("installedVersion: app.installedApp?.version ??", block)

    def test_encoder_distinguishes_encoding_failure_from_oversize(self):
        service = SERVICE.read_text(encoding="utf-8")
        start = service.index("private func encode(")
        block = service[start:service.index("private func fallback(", start)]
        self.assertIn('token: "responseTooLarge"', block)
        self.assertIn('token: "responseEncodingFailed"', block)
        # A swallowed try? would re-merge the two failure modes.
        self.assertNotIn("try? PropertyListSerialization.data(fromPropertyList: value", block)

    def test_encoding_failure_has_its_own_safe_cause_and_token(self):
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        self.assertIn('case "responseEncodingFailed":', bridge)
        self.assertIn("safeCause: .responseEncodingFailed", bridge)
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn("case responseEncodingFailed", failure)
        # Non-retryable: retrying the same request cannot fix an encoding bug.
        self.assertIn("case .responseEncodingFailed:\n                return false", failure)

    def test_plist_safe_helper_lives_in_the_shared_wire_contract(self):
        # The invariant must be enforceable from one place, not per call site.
        wire = WIRE.read_text(encoding="utf-8")
        self.assertIn("V3_PROPERTY_LIST_VALUE_V1", wire)
        self.assertIn("enum V3PropertyListValue", wire)
        self.assertIn("static func unwrapOptional", wire)
        self.assertIn("static func dictionary(", wire)
        self.assertIn("static func isEncodable", wire)


class CatalogSourceExistenceTests(unittest.TestCase):
    """P2: a deleted source must not look like an empty catalog."""

    def test_missing_source_returns_a_typed_non_manifest_failure(self):
        service = SERVICE.read_text(encoding="utf-8")
        runtime = RUNTIME.read_text(encoding="utf-8")
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn("throw V3SideStoreServiceError.catalogSourceUnavailable", service)
        self.assertIn("case catalogSourceUnavailable", runtime)
        self.assertIn("This source is no longer in the SideStore source list.", failure)
        self.assertIn("Return to Sources and reload the source list", failure)
        # Never a manifest problem.
        self.assertNotIn("sourceInvalidManifest, sourceStep: .catalogRead", service)


class ReloadOrderingTests(unittest.TestCase):
    """P1: reload must be awaitable and must not race recalculate."""

    def test_awaitable_reload_exists_and_completes_after_state_is_applied(self):
        text = shell()
        self.assertIn("func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome", text)
        self.assertIn("private func beginReload(manual: Bool) -> V3ReloadStart", text)
        self.assertIn("private func performReload() async -> V3ReloadOutcome", text)
        # Every path that ends a loading window must drain the waiters, or an
        # awaiting caller would suspend forever.
        self.assertIn("private func finishLoading(succeeded: Bool = false)", text)
        # Callers resume only after the snapshot is accepted.
        perform = text[text.index("private func performReload()"):]
        perform = perform[:perform.index("\n    private func drainDeferredReload()")]
        self.assertLess(perform.index("accept(try await V3ServiceBridge.shared.request"),
                        perform.index("for waiter in waiting { waiter.resume"))
        self.assertIn("loading = false", perform)
        self.assertLess(perform.index("loading = false"),
                        perform.index("waiter.resume"))

    def test_waiters_are_resumed_with_the_outcome_that_actually_happened(self):
        # Joining an in-flight snapshot used to resume every waiter with the
        # default false, so a caller that had just successfully reloaded was told
        # the snapshot had not been applied.
        text = shell()
        self.assertIn("pendingReloadOutcome = succeeded ? .applied : .snapshotFailed", text)
        self.assertIn("pendingReloadOutcome: V3ReloadOutcome?", text)
        finish = text[text.index("private func finishLoading("):]
        finish = finish[:finish.index("\n    private func drainDeferredReload()")]
        self.assertIn("let outcome = pendingReloadOutcome ?? (succeeded ? .applied : .notObserved)", finish)
        self.assertIn("waiter.resume(returning: outcome)", finish)
        # A window that was not a snapshot must not borrow a success.
        self.assertIn(".notObserved", text)

    def test_a_deferred_reload_is_awaited_instead_of_returning_stale_state(self):
        # beginReload defers while an operation sheet is presented. reloadAndWait
        # used to return `connected` in that case, handing the caller the previous
        # snapshot as if it were fresh.
        text = shell()
        wait = text[text.index("func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome"):]
        wait = wait[:wait.index("/// Synchronous gate shared by both reload paths.")]
        self.assertIn("case .joinedOrDeferred:", wait)
        self.assertIn("reloadWaiters.append(continuation)", wait)
        self.assertNotIn("return .notObserved\n        }\n    }\n\n    /// Synchronous", wait)
        # A deferred snapshot is still drained when the presented operation ends,
        # so the waiter is guaranteed a resumption rather than a permanent hang.
        self.assertIn("if presentation == nil { drainDeferredReload() }", text)
        drain = text[text.index("private func drainDeferredReload()"):]
        drain = drain[:drain.index("\n    }")]
        self.assertIn("guard !loading, presentation == nil", drain)

    def test_reload_and_reload_and_wait_share_one_gate(self):
        text = shell()
        self.assertIn("guard case .startSnapshot = beginReload(manual: manual) else { return }", text)
        self.assertIn("switch beginReload(manual: manual) {", text)
        # The gate rules are the shared, executable policy.
        self.assertIn("V3ReloadGate.begin(loading: loading, presentationActive: presentation != nil", text)
        self.assertIn("enum V3ReloadGate", primitives())
        # The two deferring outcomes are distinguished from the policy skip, so a
        # caller is never told a snapshot was skipped when one is merely pending.
        begin = text[text.index("private func beginReload(manual: Bool) -> V3ReloadStart"):]
        begin = begin[:begin.index("\n    private func performReload()")]
        self.assertIn("case .joinInFlight, .deferUntilIdle:", begin)
        self.assertIn("return .joinedOrDeferred", begin)
        self.assertIn("case .skip:\n            return .skipped", begin)

    def test_a_waiter_joins_an_in_flight_snapshot_instead_of_starting_a_second(self):
        text = shell()
        wait = text[text.index("func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome"):]
        wait = wait[:wait.index("/// Synchronous gate shared by both reload paths.")]
        self.assertIn("withCheckedContinuation", wait)
        self.assertIn("reloadWaiters.append(continuation)", wait)
        self.assertIn("private var reloadWaiters: [CheckedContinuation<V3ReloadOutcome, Never>] = []", text)
        # The gate still collapses a concurrent request rather than issuing one.
        self.assertIn("if loading { return .joinInFlight }", primitives())

    def test_ordering_required_callers_await_the_snapshot(self):
        text = shell()
        # Setup Assistant: first appearance, becoming active, sheet dismissal,
        # returning from a setup destination, and both Re-check Pairing actions.
        self.assertGreaterEqual(text.count("await status.reloadAndWait()"), 6)
        # No fire-and-forget reload immediately followed by a recalculate.
        self.assertNotIn("status.reload()\n                Task { await setup.recalculate", text)
        self.assertNotIn("status.reload()\n                        Task { await setup.recalculate", text)
        # The certificate import path is ordered too.
        marker = text.index('V3CanonicalJITLessCertificateUpdated"')
        block = text[marker:marker + 900]
        self.assertIn("await status.reloadAndWait()", block)
        self.assertLess(block.index("await status.reloadAndWait()"),
                        block.index("status.setupPresented = true"))

    def test_no_arbitrary_delays_were_introduced(self):
        # The race must be solved by ordering, never by sleeping. Existing
        # polling loops legitimately sleep, so the check is scoped to the
        # ordering-sensitive paths.
        text = shell()
        perform = text[text.index("private func performReload()"):]
        perform = perform[:perform.index("\n    private func drainDeferredReload()")]
        self.assertNotIn("Task.sleep", perform)
        wait = text[text.index("func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome"):]
        wait = wait[:wait.index("/// Synchronous gate shared by both reload paths.")]
        self.assertNotIn("Task.sleep", wait)
        for action in ("private func dismissKeyboard()", "private func cancelSourceEditing()",
                       "private func previewSource()"):
            block = text[text.index(action):]
            block = block[:block.index("\n    }\n")]
            self.assertNotIn("Task.sleep", block)

    def test_reload_ordering_does_not_depend_on_timing(self):
        # The join path must be a continuation, not a poll.
        text = shell()
        self.assertNotIn("reloadWaiters.append", text[text.index("func reloadAndWait"):text.index("private func beginReload")]
                         .replace("reloadWaiters.append(continuation)", ""))


class SharedSetupCompletionTests(unittest.TestCase):
    """P1: Home and the Setup Assistant must always agree."""

    def test_one_policy_decides_and_both_consume_it(self):
        text = shell()
        # The decision lives on the inputs value type, which is the single
        # authority both screens consume.
        self.assertIn("struct V3SetupCompletionInputs", primitives())
        self.assertIn("func outstanding() -> [V3SetupOutstandingItem]", primitives())
        self.assertIn("var isComplete: Bool { outstanding().isEmpty }", primitives())
        self.assertIn("var isComplete: Bool { completionInputs.isComplete }", text)
        self.assertIn("completionInputs(status: status, defaults: defaults).isComplete", text)
        # Neither screen owns a private rule any more.
        self.assertNotIn('majorVersion < 26 || jitless.state == "complete"', text)
        self.assertNotIn("if UIApplication.shared.backgroundRefreshStatus != .available { return true }", text)

    def test_jitless_requirement_is_an_input_not_a_local_exception(self):
        text = shell()
        setup = text[text.index("final class V3SetupStore"):text.index("struct V3SetupAssistantView")]
        home = text[text.index("struct V3HomeServiceHeader"):]
        # Both surfaces ask the shared policy whether JIT-Less is required, rather
        # than each hard-coding the OS test locally.
        for region in (setup, home):
            self.assertIn("V3JITLessCompletionPolicy.isRequired(", region)
        self.assertIn("enum V3JITLessCompletionPolicy", primitives())
        # No surface may re-derive the requirement with a bare version check.
        self.assertNotIn("jitlessRequired: ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26", text)

    def test_home_and_setup_read_one_jitless_readiness(self):
        # Home hard-coded "incomplete wherever JIT-Less is required", so on the
        # platforms that require it the banner could never clear while the
        # assistant showed the item complete.
        text = shell()
        self.assertIn("@Published private(set) var jitlessReadiness: V3JITLessReadiness?", text)
        self.assertIn("func recordJITLessReadiness(_ readiness: V3JITLessReadiness)", text)
        self.assertIn("jitlessComplete: V3JITLessCompletionPolicy.isComplete(status.jitlessReadiness)", text)
        # Every observer publishes into the same fact.
        self.assertGreaterEqual(text.count("status.recordJITLessReadiness("), 4)
        # The old always-incomplete answer is gone.
        self.assertNotIn("JITLessRequiredForHome", text)
        self.assertNotIn("jitlessComplete: !JITLess", text)

    def test_wifi_fact_is_shared_rather_than_guessed(self):
        text = shell()
        self.assertIn("@Published private(set) var wifiAvailable: Bool?", text)
        self.assertIn("status.recordWifiAvailability(wifi)", text)
        self.assertIn("func recordWifiAvailability(_ available: Bool)", text)
        self.assertIn("networkComplete: status.wifiAvailable == true", text)


class ReloadStatusVisibilityTests(unittest.TestCase):
    """P1/UX: Reload Status must be visible."""

    def test_loading_wins_over_connected_and_the_time_is_shown(self):
        text = shell()
        header = text[text.index("struct V3HomeServiceHeader"):text.index("private struct V3HomeView")]
        self.assertIn('Text(isLoading ? "Reloading Status..." : "Reload Status")', header)
        self.assertIn("V3StatusPresentation.connectionState(connected: isConnected, loading: isLoading)", header)
        self.assertIn("if isLoading {", header)
        self.assertIn("ProgressView()", header)
        self.assertIn("if let updatedAt {", header)
        # The old ordering is gone.
        self.assertNotIn('isConnected ? "Active & Connected"', header)
        # Meaning never depends on colour alone.
        self.assertIn("Label(statusPresentation.title, systemImage: statusPresentation.icon)", header)

    def test_connection_state_model_puts_loading_first(self):
        primitives_text = primitives()
        block = primitives_text[primitives_text.index("static func connectionState("):]
        block = block[:block.index("\n    }\n")]
        self.assertLess(block.index("if loading"), block.index("if connected"))
        self.assertIn('V3StatusPresentation(severity: .working, title: "Reloading Status...")', block)


class UserFacingIssueRoutingTests(unittest.TestCase):
    """P1/UX: the global alert must offer the right action."""

    def test_issue_model_exists_and_maps_typed_evidence_to_actions(self):
        primitives_text = primitives()
        self.assertIn("enum V3IssueAction", primitives_text)
        self.assertIn("struct V3UserFacingIssue", primitives_text)
        self.assertIn("static func make(operation: String, stage: String", primitives_text)
        # Every requested action has a real title.
        for title in ("Retry Source", "Open Certificates", "Open Account & Signing",
                      "Show Pairing Setup", "Open Connection Check", "Choose IPA Again"):
            self.assertIn(f'return "{title}"', primitives_text)

    def test_retry_connection_is_only_offered_for_connection_evidence(self):
        primitives_text = primitives()
        make = primitives_text[primitives_text.index("static func make(operation: String"):]
        make = make[:make.index("/// Builds an issue from a typed failure")]
        # The connection destination is the only one that maps to a connection action.
        self.assertIn('case "connection": return retryable == true ? .retryConnection : .openConnectionCheck', make)
        # No destination at all must not assume networking.
        self.assertIn("default:\n                // No evidence points anywhere specific. Never assume networking.\n                return .dismiss", make)
        # And no cause is derived from a numeric code.
        self.assertNotIn("underlyingCode", make)
        self.assertNotIn("code ==", make)

    def test_alert_uses_the_structured_primary_action(self):
        text = shell()
        start = text.index('Button(status.issue?.primaryAction.title ?? "OK")')
        block = text[start - 400:start + 900]
        self.assertIn("status.issue?.primaryAction", block)
        self.assertIn("status.performPrimaryIssueAction()", block)
        self.assertIn("status.clearIssue()", block)
        # Copy Diagnostics always remains, and uses the technical line.
        self.assertIn("status.issue?.technicalDetails ?? status.error", block)
        # The unconditional Retry Connection is gone.
        self.assertNotIn('Button("Retry Connection") { status.reload() }', text)

    def test_retry_source_re_requests_the_sources_not_the_status_snapshot(self):
        # A button labelled "Retry Source" that only reloads status leaves the
        # user looking at the same stale catalog while claiming it retried.
        text = shell()
        self.assertIn("func performPrimaryIssueAction()", text)
        action = text[text.index("func performPrimaryIssueAction()"):]
        action = action[:action.index("\n    }")]
        self.assertIn("case .retryConnection:\n            reload()", action)
        self.assertIn("case .retrySource:\n            refreshSources()", action)
        # The two retries are never collapsed into one branch again.
        self.assertNotIn("action == .retryConnection || action == .retrySource", text)
        self.assertIn("func refreshSources()", text)

    def test_failures_are_presented_through_the_issue_model(self):
        text = shell()
        self.assertIn("func present(_ error: Error)", text)
        self.assertIn("func openIssueRecovery()", text)
        self.assertIn("func clearIssue()", text)
        # Caught errors route through the structured presentation.
        self.assertIn("catch { status.present(error) }", text)
        # A raw localizedDescription is never the only thing the user sees.
        self.assertIn("whatToDo", text)

    def test_recovery_destinations_are_all_routable(self):
        text = shell()
        route = text[text.index("func openIssueRecovery()"):]
        route = route[:route.index("\n    }\n")]
        for destination in ("signIn", "certificates", "ipa", "setup", "connection", "pairing", "sources"):
            self.assertIn(f'case "{destination}":', route)


class SemanticStatusTests(unittest.TestCase):
    """P1/UX: failure states must be visually semantic."""

    def test_one_semantic_model_is_defined_and_used(self):
        primitives_text = primitives()
        self.assertIn("enum V3StatusSeverity", primitives_text)
        self.assertIn("struct V3StatusPresentation", primitives_text)
        self.assertIn("V3StatusSeverity.failed.icon", shell())
        self.assertIn("V3StatusSeverity.completed.icon", shell())
        self.assertIn("V3StatusSeverity.failed.icon", SHELL.read_text(encoding="utf-8"))

    def test_every_severity_has_a_distinct_icon(self):
        primitives_text = primitives()
        severity = primitives_text[primitives_text.index("enum V3StatusSeverity"):]
        block = severity[severity.index("var icon: String {"):]
        block = block[:block.index("\n    }\n")]
        for icon in ("arrow.triangle.2.circlepath", "checkmark.circle.fill",
                     "exclamationmark.triangle.fill", "xmark.circle.fill"):
            self.assertIn(f'return "{icon}"', block)

    def test_setup_rows_use_the_shared_severity_mapping(self):
        text = shell()
        # The JIT-Less step state is derived from the shared presentation.
        self.assertIn("let presentation = V3JITLessPresentation.present(readiness.0)", text)
        self.assertIn("switch presentation.severity {", text)
        # The sources view renders failures with the failure icon and success
        # with the success icon.
        self.assertIn("V3StatusSeverity.failed.icon", text)
        self.assertIn("V3StatusSeverity.completed.icon", text)


class JITLessCertificationTests(unittest.TestCase):
    """P1: never conflate the active certificate with the LiveContainer copy."""

    def test_active_certificate_and_copy_have_distinct_states(self):
        primitives_text = primitives()
        self.assertIn("case activeCertificateMissing", primitives_text)
        self.assertIn("case certificateMismatch", primitives_text)
        policy = primitives_text[primitives_text.index("enum V3JITLessReadinessPolicy"):]
        policy = policy[:policy.index("\n}\n")]
        self.assertIn("guard activeCertificateExists else { return .activeCertificateMissing }", policy)
        self.assertIn("return identitiesMatch ? .ready : .certificateMismatch", policy)

    def test_ready_state_is_presented_as_complete_not_as_outstanding_work(self):
        primitives_text = primitives()
        self.assertIn("struct V3JITLessPresentation", primitives_text)
        block = primitives_text[primitives_text.index("static func present(_ readiness:"):]
        block = block[:block.index("\n    }\n")]
        self.assertIn("case .ready:", block)
        ready = block[block.index("case .ready:"):block.index("case .certificateMismatch:")]
        self.assertIn("severity: .completed", ready)
        self.assertIn("isOutstandingSetupTask: false", ready)
        self.assertIn('title: "Configured / Ready"', ready)

    def test_stale_copy_message_names_the_copy_and_does_not_blame_sidestore(self):
        primitives_text = primitives()
        mismatch = primitives_text[primitives_text.index("case .certificateMismatch:"):]
        mismatch = mismatch[:mismatch.index("case .activeCertificateMissing:")]
        self.assertIn("SideStore is using a different or newer signing certificate", mismatch)
        self.assertIn("Refresh the JIT-Less certificate copy.", mismatch)
        self.assertIn("severity: .warning", mismatch)

    def test_setup_assistant_hides_setup_actions_when_ready(self):
        text = shell()
        section = text[text.index('Section("JIT-Less Mode")'):]
        section = section[:section.index('Section("Network")')]
        # The diagnostic action is offered only when nothing is outstanding.
        self.assertIn("if !jitless.isOutstandingSetupTask {", section)
        self.assertIn('Label("Open JIT-Less Diagnose"', section)
        self.assertIn("else {", section)
        self.assertIn('Button("Set Up JIT-Less")', section)
        self.assertIn('Button("Refresh JIT-Less Certificate")', section)
        self.assertIn('Button("Open Certificates")', section)

    def test_every_jitless_switch_is_exhaustive(self):
        # Adding a readiness state silently broke a switch in another view, which
        # is a build failure. Every switch over the enum must now name all cases,
        # or carry an explicit default.
        primitives_text = primitives()
        declared = set(re.findall(r"^\s{4}case ([A-Za-z][A-Za-z0-9]*)", re.search(
            r"enum V3JITLessReadiness: String, Equatable \{(.*?)\n\}", primitives_text,
            re.S).group(1), re.M))
        self.assertIn("certificateMismatch", declared)
        self.assertIn("activeCertificateMissing", declared)
        for path in (SHELL, PRIMITIVES):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"switch ([A-Za-z0-9_.]+) \{\n((?:.*\n)*?)\s{8}\}\n", text):
                subject, body = match.group(1), match.group(2)
                if "jitless" not in subject.lower() and subject != "readiness":
                    continue
                if "default:" in body:
                    continue
                covered = set()
                for group in re.findall(r"case ([^:]+):", body):
                    for name in re.findall(r"\.([A-Za-z][A-Za-z0-9]*)", group):
                        covered.add(name)
                missing = declared - covered
                self.assertEqual(missing, set(),
                                 f"{path.name}: switch on {subject} misses {sorted(missing)}")


class SourceKeyboardTests(unittest.TestCase):
    """P2 / issue #40: Add Source keyboard dismissal and cancel."""

    def test_field_has_focus_state_and_a_done_submit_label(self):
        text = shell()
        sources = text[text.index("struct V3SourcesView"):]
        sources = sources[:sources.index("struct V3CatalogApp")]
        self.assertIn("@FocusState private var sourceFieldFocused: Bool", sources)
        self.assertIn(".focused($sourceFieldFocused)", sources)
        self.assertIn(".submitLabel(.done)", sources)
        self.assertIn("ToolbarItemGroup(placement: .keyboard)", sources)
        self.assertIn('Button("Cancel") { cancelSourceEditing() }', sources)
        self.assertIn('Button("Done") { dismissKeyboard() }', sources)

    def test_return_only_dismisses_the_keyboard(self):
        text = shell()
        sources = text[text.index("struct V3SourcesView"):]
        sources = sources[:sources.index("struct V3CatalogApp")]
        self.assertIn(".onSubmit { dismissKeyboard() }", sources)
        # Return must never preview or add.
        self.assertNotIn(".onSubmit { Task { await previewSource() } }", sources)
        self.assertNotIn(".onSubmit { confirmAdd(", sources)

    def test_cancel_and_done_have_no_side_effects(self):
        text = shell()
        sources = text[text.index("struct V3SourcesView"):]
        sources = sources[:sources.index("struct V3CatalogApp")]
        for action in (sources[sources.index("private func dismissKeyboard()"):],
                       sources[sources.index("private func cancelSourceEditing()"):]):
            block = action[:action.index("\n    }\n")]
            for forbidden in ("previewSource", "confirmAdd", "V3ServiceBridge", "sourceAddConfirmed",
                              "sourcePreview", "status.reload"):
                self.assertNotIn(forbidden, block,
                                 f"a keyboard action must not trigger {forbidden}")

    def test_preview_and_add_remain_separate_explicit_actions(self):
        text = shell()
        sources = text[text.index("struct V3SourcesView"):]
        sources = sources[:sources.index("struct V3CatalogApp")]
        self.assertIn("Task { await previewSource() }", sources)
        self.assertIn('Label(previewBusy ? "Checking Source..." : "Preview and Add Source"', sources)

    def test_cancel_semantics_are_explicit_and_documented(self):
        text = shell()
        self.assertIn("enum V3SourceEditingPolicy", primitives())
        self.assertIn("enum V3SourceEditingOutcome", primitives())
        sources = text[text.index("struct V3SourcesView"):]
        sources = sources[:sources.index("struct V3CatalogApp")]
        self.assertIn("V3SourceEditingPolicy.cancel(typed: status.sourceURL, beforeEditing: sourceURLBeforeEditing)",
                      sources)
        self.assertIn("V3SourceEditingPolicy.resolved(", sources)


class SourceSemanticStateTests(unittest.TestCase):
    """P2: source add and failure states must be semantic."""

    def test_success_is_green_and_informational_notice_is_neutral(self):
        text = shell()
        sources = text[text.index("struct V3SourcesView"):]
        sources = sources[:sources.index("struct V3CatalogApp")]
        self.assertIn("if addSucceeded && !notice.isEmpty {", sources)
        self.assertIn("V3StatusSeverity.completed.icon", sources)
        self.assertIn(".foregroundColor(.green)", sources)
        self.assertIn('Label(notice, systemImage: "info.circle.fill")', sources)
        self.assertIn("addSucceeded = true", sources)

    def test_source_failure_is_rendered_as_a_failure(self):
        text = shell()
        sources = text[text.index("struct V3SourcesView"):]
        sources = sources[:sources.index("struct V3CatalogApp")]
        self.assertIn("V3StatusSeverity.failed.icon", sources)
        self.assertIn(".foregroundColor(.red)", sources)
        # Preview, persistence and catalog failures stay distinguishable.
        self.assertIn("Section(\"What happened\")", sources)
        self.assertIn("Section(\"What you can do\")", sources)

    def test_source_failure_recovery_targets_sources_not_connection(self):
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn("case .sourceInvalidManifest:", failure)
        self.assertIn("Check the source provider's manifest format", failure)
        self.assertIn("Reload Sources and check whether the source appears", failure)


class HiddenNavigationRowTests(unittest.TestCase):
    """P2: the programmatic route must not create a Form row."""

    def test_route_is_attached_outside_the_row_structure(self):
        patch = PATCH.read_text(encoding="utf-8")
        self.assertIn("V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1", patch)
        # A background, not a Form child.
        self.assertIn(".background(", patch)
        block = patch[patch.index("V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1"):]
        block = block[:block.index('"canonical JIT-Less diagnose navigation")')]
        self.assertIn("NavigationLink(destination: LCJITLessDiagnoseView()", block)
        # The old Form-child injection is gone.
        self.assertNotIn("'            Form {\\n'\n", block)
        # The verifier enforces the stronger contract.
        self.assertIn('die("canonical JIT-Less route is not row-neutralized', patch)


class NoRegressionOfWorkingSystemsTests(unittest.TestCase):
    """Item 18: nothing that already worked may regress."""

    def test_pairing_storage_and_transport_are_untouched(self):
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn('PairingFileManager.shared.fetchPairingFile() == nil ? "Pairing file required" : "Pairing file available"',
                      service)
        wire = WIRE.read_text(encoding="utf-8")
        self.assertIn('"pairingImportData"', wire)
        self.assertEqual(shell().count('operation: "pairingImportData"'), 1)

    def test_source_add_persistence_is_untouched(self):
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("sourcePersistenceUnverified", service)
        primitives_text = primitives()
        self.assertIn("V3SourceAddPersistencePolicy", primitives_text)
        shell_text = shell()
        self.assertIn("V3SourceAddPersistencePolicy.confirmationMessage", shell_text)

    def test_wire_contract_operations_are_preserved(self):
        wire = WIRE.read_text(encoding="utf-8")
        for operation in ("authBegin", "authPoll", "authRespond", "authCancel",
                          "authRetryProvisioning", "opStart", "opPoll", "opAnswer", "opCancel",
                          "catalog", "snapshot", "sourcePreview", "sourceAddConfirmed"):
            self.assertIn(f'"{operation}"', wire)

    def test_localdevvpn_transport_is_not_gated_by_the_new_policy(self):
        # Only pairing blocks a refresh. Network and LocalDevVPN remain the
        # scheduler's preflight, not the host's.
        primitives_text = primitives()
        policy = primitives_text[primitives_text.index("struct V3RefreshPrerequisite"):]
        policy = policy[:policy.index("\n}\n")]
        self.assertNotIn("noVPN", policy)
        self.assertNotIn("wifi", policy.lower())
        self.assertIn("var blocksRefresh: Bool { state == .unsatisfied }", policy)


if __name__ == "__main__":
    unittest.main()
