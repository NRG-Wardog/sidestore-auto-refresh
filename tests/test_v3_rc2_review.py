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
        # The encoder moved into the shared wire contract as a pure enum so the
        # real encoder and the real host classifier can be executed together.
        helper = primitives()
        start = helper.index("enum V3ResponseEncoder {")
        block = " ".join(re.sub(r"//.*$", "", line) for line in
                         helper[start:helper.index("static func fallback(", start)].splitlines())
        block = re.sub(r"\s+", " ", block)
        self.assertIn("V3ResponseClassifier.Token.tooLarge", block)
        self.assertIn("V3ResponseClassifier.Token.encodingFailed", block)
        # A swallowed try? in encode() would re-merge the two failure modes.
        # The fallback's own try? is different: that dictionary is always
        # serializable and must still return Data rather than trap.
        self.assertNotIn("try? PropertyListSerialization.data(fromPropertyList: value", block)
        # The shared limit, not a duplicated literal.
        self.assertIn("guard data.count <= V3WireContract.responseLimit else", block)
        self.assertNotIn("4_194_304", helper[helper.index("enum V3ResponseEncoder {"):])

    def test_encoding_failure_has_its_own_safe_cause_and_token(self):
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        self.assertIn('case "responseEncodingFailed":', bridge)
        self.assertIn("safeCause: .responseEncodingFailed", bridge)
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn("case responseEncodingFailed", failure)
        # Non-retryable: retrying the same request cannot fix an encoding bug.
        self.assertIn("case .responseEncodingFailed:\n                return false", failure)
        # V3_RESPONSE_CLASSIFICATION_CARRIER_V1: the classification must be
        # carried by the structured envelope, because the host throws that one
        # and discards the legacy token. A cause that only the legacy token can
        # produce is unreachable in production. The encoder and the token table
        # live in the behavioural primitives, beside CombinedFailure: the wire
        # contract is compiled independently in each process and stays free of
        # the error model.
        helper_text = primitives()
        self.assertIn("enum V3ResponseClassifier", helper_text)
        self.assertIn("case Token.encodingFailed: return .responseEncodingFailed", helper_text)
        self.assertIn("id: id, safeCause: safeCause).wire", helper_text)
        self.assertNotIn("CombinedFailure", WIRE.read_text(encoding="utf-8"),
                         "the wire contract must stay independently compilable")

    def test_plist_safe_helper_lives_in_the_shared_wire_contract(self):
        # The invariant must be enforceable from one place, not per call site.
        wire = WIRE.read_text(encoding="utf-8")
        self.assertIn("V3_PROPERTY_LIST_VALUE_V1", wire)
        self.assertIn("enum V3PropertyListValue", wire)
        self.assertIn("static func unwrapOptional", wire)
        self.assertIn("static func dictionary(", wire)
        self.assertIn("static func isEncodable", wire)

    def test_plist_leaf_set_is_foundation_derived_and_rejects_url(self):
        # V3_PLIST_LEAF_CONTRACT_V1: URL is not a property-list leaf. The
        # previous hand-written list accepted it, which licensed a future
        # serialization failure, and rejected Float and the narrow integer types,
        # which do serialize. The set is now Foundation's, so it cannot drift.
        wire = WIRE.read_text(encoding="utf-8")
        start = wire.index("static func isEncodable")
        block = wire[start:wire.index("\n    }", start)]
        self.assertIn("is NSNumber", block, "one bridged case must cover the whole numeric family")
        self.assertIn("is String", block)
        self.assertIn("is Date", block)
        self.assertIn("is Data", block)
        self.assertNotIn("is URL", block, "CoreFoundation rejects CFURL for every plist format but OpenStep")
        self.assertNotIn("is Int,", block, "a remembered type list drifts from Foundation")
        self.assertNotIn("is Double,", block)
        # An unknown object is rejected, never stringified into the wire.
        self.assertNotIn("String(describing:", block)
        self.assertNotIn("String(describing: unwrapped)", block)


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
    """P1: an authoritative snapshot must be awaitable, and must not be faked.

    V3_LOAD_ACTIVITY_OWNERSHIP_V1: one `loading` flag used to cover both an
    authoritative snapshot and a mutation. The gate read it as "a snapshot is in
    flight", so a caller awaiting authoritative status could join a mutation and
    be released by the mutation's completion before any snapshot had run. The
    activity is now named, and only a snapshot completion resolves a waiter.

    The interleavings themselves are executed in
    tests/fixtures/v3_snapshot_ownership_harness.swift. These assertions pin the
    production wiring that the harness models.
    """

    def test_snapshot_and_mutation_are_named_activities(self):
        text = shell()
        primitives_text = primitives()
        self.assertIn("enum V3LoadActivity", primitives_text)
        for case in ("case idle", "case snapshot", "case mutation"):
            self.assertIn(case, primitives_text)
        # The store tracks the activity instead of a bare busy flag, and the
        # user-facing meaning of `loading` is derived from it.
        self.assertIn("private var loadActivity: V3LoadActivity = .idle", text)
        self.assertIn("loadActivity = .snapshot", text)
        self.assertIn("loadActivity = .mutation", text)
        # No shared busy flag may stand in for the activity again.
        self.assertNotIn("V3ReloadGate.begin(loading:", text)
        self.assertNotIn("enum V3ReloadGate", primitives_text)

    def test_only_a_snapshot_completion_resolves_a_waiter(self):
        text = shell()
        self.assertIn("private func finishSnapshot(outcome: V3ReloadOutcome)", text)
        self.assertIn("private func finishMutation()", text)
        # The resumption lives in exactly one function.
        self.assertEqual(text.count("waiter.continuation.resume(returning:"), 2,
                         "only finishSnapshot and the refused drain may resume a waiter")
        finish_mutation = text[text.index("private func finishMutation()"):]
        finish_mutation = finish_mutation[:finish_mutation.index("\n    }")]
        self.assertNotIn("continuation.resume", finish_mutation,
                         "a mutation completion must never resolve a snapshot waiter")
        self.assertNotIn("snapshotWaiters", finish_mutation)
        # The one generic window-ender that could not tell them apart is gone.
        self.assertNotIn("finishLoading", text)
        self.assertNotIn("pendingReloadOutcome", text)

    def test_a_waiter_waits_for_a_post_mutation_snapshot(self):
        text = shell()
        primitives_text = primitives()
        # The gate can express "a mutation is running, snapshot afterwards",
        # which the old boolean input could not.
        self.assertIn("case awaitMutationThenSnapshot", primitives_text)
        self.assertIn("case .mutation: return .awaitMutationThenSnapshot", primitives_text)
        self.assertIn("enum V3SnapshotGate", primitives_text)
        self.assertIn("static func decide(activity: V3LoadActivity", primitives_text)
        self.assertIn("static func drain(activity: V3LoadActivity", primitives_text)
        wait = text[text.index("func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome"):]
        wait = wait[:wait.index("    /// The shared synchronous gate.")]
        self.assertIn("case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation:", wait)
        self.assertIn("snapshotWaiters.append(SnapshotWaiter(manual: manual, continuation: continuation))", wait)

    def test_only_one_snapshot_can_be_owed_and_starting_one_discharges_it(self):
        # A single owed intent, not one per requester, so a burst of requests
        # cannot queue a burst of fetches.
        text = shell()
        self.assertIn("private var snapshotOwed = false", text)
        start = text[text.index("private func startSnapshot(manual: Bool)"):]
        start = start[:start.index("\n    }")]
        self.assertIn("snapshotOwed = false", start,
                      "starting any snapshot must discharge the owed intent")
        begin = text[text.index("private func beginSnapshot(manual: Bool) -> V3SnapshotDecision"):]
        begin = begin[:begin.index("\n    private func startSnapshot")]
        self.assertIn("snapshotOwed = true", begin)
        self.assertEqual(begin.count("snapshotOwed = true"), 1,
                         "only the deferring branches may set the owed intent")

    def test_no_continuation_can_be_stranded(self):
        text = shell()
        primitives_text = primitives()
        # The drain is total: policy refusal resumes the waiters instead of
        # abandoning them, which is what let a non-manual deferred reload hang a
        # task forever.
        drain = text[text.index("private func drainOwedSnapshot()"):]
        drain = drain[:drain.index("\n    }")]
        self.assertIn("case .doNotObserve:", drain)
        self.assertIn("guard snapshotOwed else { return }", drain)
        self.assertIn("for waiter in waiting { waiter.continuation.resume(returning: .notObserved) }", drain)
        self.assertIn("snapshotOwed = false", drain)
        # Each waiter carries its own manual requirement, so a non-manual monitor
        # tick cannot discharge a caller's manual request.
        self.assertIn("private struct SnapshotWaiter {", text)
        self.assertIn("let manual: Bool", text)
        self.assertIn("let continuation: CheckedContinuation<V3ReloadOutcome, Never>", text)
        self.assertIn("anyWaiterNeedsManual", primitives_text)
        self.assertIn("let needsManual = snapshotWaiters.contains { $0.manual }", text)

    def test_presentation_dismissal_drains_the_deferred_snapshot(self):
        text = shell()
        self.assertIn("if presentation == nil { drainOwedSnapshot() }", text)
        drain = text[text.index("private func drainOwedSnapshot()"):]
        drain = drain[:drain.index("\n    }")]
        self.assertIn("presentationActive: presentation != nil", drain)

    def test_awaitable_reload_completes_after_state_is_applied(self):
        text = shell()
        self.assertIn("func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome", text)
        self.assertIn("private func beginSnapshot(manual: Bool) -> V3SnapshotDecision", text)
        self.assertIn("private func performSnapshot() async -> V3ReloadOutcome", text)
        perform = text[text.index("private func performSnapshot()"):]
        perform = perform[:perform.index("\n    /// V3_AWAITABLE_RELOAD_V1: the single place a snapshot")]
        # State is accepted before anyone is resumed.
        self.assertLess(perform.index("accept(try await V3ServiceBridge.shared.request"),
                        perform.index("finishSnapshot(outcome: outcome)"))
        finish = text[text.index("private func finishSnapshot(outcome: V3ReloadOutcome)"):]
        finish = finish[:finish.index("\n    /// V3_LOAD_ACTIVITY_OWNERSHIP_V1: the single place a mutation")]
        self.assertLess(finish.index("loading = false"), finish.index("waiter.continuation.resume"))

    def test_snapshot_failure_reports_a_failed_outcome(self):
        text = shell()
        perform = text[text.index("private func performSnapshot()"):]
        perform = perform[:perform.index("\n    /// V3_AWAITABLE_RELOAD_V1: the single place a snapshot")]
        self.assertIn("let outcome: V3ReloadOutcome = succeeded ? .applied : .snapshotFailed", perform)
        self.assertIn("requiresConnectionRetry = true", perform)
        self.assertIn("present(error)", perform)

    def test_mutations_are_named_and_explain_a_busy_store(self):
        text = shell()
        self.assertIn("private func beginMutation() {", text)
        self.assertIn("private func runMutation(", text)
        for operation in ("signOut", "jit", "syncAppIDs", "clearCache", "refreshSources"):
            self.assertIn(f'runMutation("{operation}"', text)
        run = text[text.index("private func runMutation("):]
        run = run[:run.index("\n    func signOut()")]
        # A busy store says so. refreshSources() was reachable from the global
        # alert's Retry Source action, where a silent guard produced no work, no
        # message, and a dismissed alert.
        self.assertIn("guard loadActivity == .idle else {", run)
        self.assertIn("presentBusy()", run)
        self.assertIn("private func presentBusy() {", text)
        self.assertIn("beginMutation()", run)
        self.assertIn("finishMutation()", run)
        # The mutation's own trailing reload joins an owed snapshot rather than
        # starting a second one.
        self.assertIn("reload()", run)
        self.assertNotIn("loading = true", run)

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
        perform = text[text.index("private func performSnapshot()"):]
        perform = perform[:perform.index("\n    /// V3_AWAITABLE_RELOAD_V1: the single place a snapshot")]
        self.assertNotIn("Task.sleep", perform)
        wait = text[text.index("func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome"):]
        wait = wait[:wait.index("    /// The shared synchronous gate.")]
        self.assertNotIn("Task.sleep", wait)
        for action in ("private func presentBusy()", "private func drainOwedSnapshot()",
                       "private func beginSnapshot(manual: Bool)",
                       "private func dismissKeyboard()", "private func cancelSourceEditing()",
                       "private func previewSource()"):
            block = text[text.index(action):]
            block = block[:block.index("\n    }\n")]
            self.assertNotIn("Task.sleep", block)

    def test_reload_ordering_does_not_depend_on_timing(self):
        # The join path must be a continuation, not a poll.
        text = shell()
        wait = text[text.index("func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome"):]
        wait = wait[:wait.index("    /// The shared synchronous gate.")]
        self.assertIn("withCheckedContinuation", wait)
        self.assertNotIn("while", wait)
        self.assertNotIn("Task.sleep", wait)


class SharedSetupCompletionTests(unittest.TestCase):
    """P1: Home and the Setup Assistant must always agree."""

    def test_one_policy_decides_and_both_consume_it(self):
        text = shell()
        # The decision lives on the inputs value type, which is the single
        # authority both screens consume.
        self.assertIn("struct V3SetupCompletionInputs", primitives())
        self.assertIn("func outstanding() -> [V3SetupOutstandingItem]", primitives())
        self.assertIn("var isComplete: Bool { outstanding().isEmpty }", primitives())
        self.assertIn("func isComplete(status: V3SideStoreStatusStore) -> Bool", text)
        self.assertIn("completionInputs(status: status, defaults: defaults).isComplete", text)
        # Neither screen owns a private rule any more.
        self.assertNotIn('majorVersion < 26 || jitless.state == "complete"', text)
        self.assertNotIn("if UIApplication.shared.backgroundRefreshStatus != .available { return true }", text)
        # V3_SHARED_JITLESS_FACT_V1: the assistant must not keep a private copy of
        # the readiness. It used to answer from a local step-state string while
        # Home answered from the published fact, so Health could publish a ready
        # readiness the assistant had not observed and the two would disagree.
        setup = text[text.index("final class V3SetupStore"):text.index("struct V3SetupAssistantView")]
        self.assertNotIn("@Published var jitlessReadiness", setup,
                         "the setup store must not keep a second copy of the shared fact")
        self.assertNotIn("setup.jitlessReadiness", text)
        # Both surfaces ask the same policy of the same published fact.
        self.assertEqual(text.count("jitlessComplete: V3JITLessCompletionPolicy.isComplete(status.jitlessReadiness)"), 2)
        # Home, the assistant, the assistant's own platform branch, and the three
        # sections that gate JIT-Less UI on the platform all ask the shared
        # requirement policy rather than testing the OS version locally.
        self.assertEqual(text.count("V3JITLessCompletionPolicy.isRequired("), 6)
        self.assertNotIn("ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26", text)
        self.assertNotIn("ProcessInfo.processInfo.operatingSystemVersion.majorVersion < 26", text)

    def test_home_setup_facts_are_observed_without_opening_a_screen(self):
        # V3_SETUP_FACT_OBSERVATION_V1: the published facts were only ever
        # written by the Setup Assistant and Health. A user who opened neither
        # left them nil, and nil is outstanding by policy, so on a platform where
        # JIT-Less is required the Home banner could never clear however correct
        # the underlying state was. The store now observes them itself.
        text = shell()
        self.assertIn("private func observeSetupFactsIfNeeded()", text)
        self.assertIn("private func observeSetupFacts() async", text)
        self.assertIn("observeSetupFactsIfNeeded()", text[text.index("private func performSnapshot()"):])
        self.assertIn("V3ServiceBridge.shared.request(operation: \"healthSnapshot\")", text)
        self.assertIn("recordWifiAvailability(wifi)", text)
        self.assertIn("recordJITLessReadiness(readiness.0)", text)
        # A failure is published as unknown, never as an assumed-good fact.
        observation = text[text.index("private func observeSetupFacts() async"):]
        observation = observation[:observation.index("\n    @Published private(set) var updatedAt")]
        self.assertIn("recordJITLessReadiness(.unknown)", observation)
        self.assertNotIn("recordJITLessReadiness(.ready)", observation,
                         "an unanswered observation must never assert readiness")
        # The attempt is bounded, so a silent service cannot cause a retry loop.
        self.assertIn("private enum SetupFactObservation: Equatable {", text)
        self.assertIn("case pending", text)
        self.assertIn("case observed", text)
        self.assertIn("case deferred", text)
        # Only a deliberate reload asks again.
        self.assertIn("if setupFactObservation == .deferred { setupFactObservation = .pending }", text)

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

    def test_source_cancel_restores_the_value_and_changes_nothing_else(self):
        # Issue #40. Cancel performs no network request, no preview, no
        # persistence and no source mutation. It previously also cleared an
        # already-rendered preview, which silently discarded a request the user
        # had just paid for together with its Confirm action.
        text = shell()
        cancel = text[text.index("private func cancelSourceEditing() {"):]
        cancel = cancel[:cancel.index("\n    }")]
        code = "\n".join(line for line in cancel.splitlines()
                         if not line.strip().startswith("//"))
        self.assertIn("V3SourceEditingPolicy.cancel(typed: status.sourceURL, beforeEditing: sourceURLBeforeEditing)", code)
        self.assertIn("sourceFieldFocused = false", code)
        for forbidden in ("preview = nil", "V3ServiceBridge", "previewSource", "addSource",
                          "sourceAddConfirmed", "status.reload", "sourceFailure =",
                          "addSucceeded =", "notice ="):
            self.assertNotIn(forbidden, code,
                             f"Cancel must not touch {forbidden}")

    def test_done_dismisses_only_and_uses_the_shared_policy(self):
        # The Done path is a pure UI dismissal. It previously bypassed the shared
        # policy entirely, so the harness was certifying a function production
        # never called.
        text = shell()
        primitives_text = primitives()
        self.assertIn("static func done(typed: String) -> V3SourceEditingOutcome", primitives_text)
        done = text[text.index("private func dismissKeyboard() {"):]
        done = done[:done.index("\n    }")]
        code = "\n".join(line for line in done.splitlines()
                         if not line.strip().startswith("//"))
        self.assertIn("sourceFieldFocused = false", code)
        self.assertIn("V3SourceEditingPolicy.done(typed: status.sourceURL)", code)
        for forbidden in ("status.sourceURL =", "V3ServiceBridge", "preview"):
            self.assertNotIn(forbidden, code)

    def test_pre_edit_value_is_captured_on_the_focus_rising_edge_only(self):
        text = shell()
        capture = text[text.index(".onChange(of: sourceFieldFocused)"):]
        capture = capture[:capture.index("\n                            }")]
        code = "\n".join(line for line in capture.splitlines()
                         if not line.strip().startswith("//"))
        self.assertIn("if focused { sourceURLBeforeEditing = status.sourceURL }", code)
        # Only the rising edge may capture, because Cancel itself drops focus and
        # must not overwrite the value it is about to restore.
        self.assertNotIn("else", code)
        # And the capture must not also happen anywhere else, such as when a
        # preview is requested, which is what made Cancel restore the wrong value.
        self.assertEqual(text.count("sourceURLBeforeEditing = status.sourceURL"), 1)
        preview = text[text.index("private func previewSource() async {"):]
        preview = preview[:preview.index("\n    }")]
        self.assertNotIn("sourceURLBeforeEditing", preview)

    def test_return_dismisses_the_keyboard_and_submits_nothing(self):
        text = shell()
        field = text[text.index('TextField("https://example.com/source.json"'):]
        field = field[:field.index("\n                    }")]
        code = "\n".join(line for line in field.splitlines()
                         if not line.strip().startswith("//"))
        self.assertIn(".submitLabel(.done)", code)
        self.assertIn(".onSubmit { dismissKeyboard() }", code)
        self.assertNotIn("previewSource", code)
        self.assertNotIn("addSource", code)

    def test_operation_recovery_action_names_where_it_actually_goes(self):
        # The "setup" destination opens the Setup Assistant but read "Open
        # Connection Check", which is the same class of mislabel as offering a
        # connection retry for a source failure.
        text = shell()
        titles = text[text.index("private func recoveryActionTitle(for destination: String)"):]
        titles = titles[:titles.index("\n    }")]
        self.assertIn('case "setup": return "Open Setup Assistant"', titles)
        self.assertNotIn('case "setup": return "Open Connection Check"', titles)
        # And the route it names must be the one the destination is handled by.
        self.assertIn('case "setup": status.setupPresented = true', text)

    def test_alert_uses_the_structured_primary_action(self):
        text = shell()
        start = text.index('if let action = status.issue?.primaryAction, action != .dismiss {')
        block = text[start - 400:start + 900]
        self.assertIn("status.issue?.primaryAction", block)
        self.assertIn("status.performPrimaryIssueAction()", block)
        self.assertIn("status.clearIssue()", block)
        # Copy Diagnostics always remains, and uses the technical line.
        self.assertIn("status.issue?.technicalDetails ?? status.error", block)
        # The unconditional Retry Connection is gone.
        self.assertNotIn('Button("Retry Connection") { status.reload() }', text)
        # A plain message with no structured issue offers no action. It used to
        # render a button labelled "OK" that then did nothing, next to a second
        # "OK" that dismissed, so a refusal to work looked like a choice.
        self.assertIn("action != .dismiss", block)
        self.assertNotIn('Button(status.issue?.primaryAction.title ?? "OK")', text)
        self.assertNotIn('Button("OK") {', text)

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
