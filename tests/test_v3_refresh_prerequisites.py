"""Coverage for the shared refresh prerequisite contract and pairing guidance.

The physical defect this file locks down: Quick Setup already displayed
"Pairing File: Action Required", yet Run Test Refresh still started the
scheduler mutation and later reported a generic "no safe underlying cause was
available" for a prerequisite the host already knew was missing.

Rules enforced here:
- Exactly one authoritative prerequisite policy interprets the pairing status.
- Every refresh entry point consults it before starting a mutation.
- Test Refresh with known-missing pairing must not post the scheduler
  notification at all.
- Pairing guidance explains placement with the installation tool first and keeps
  manual import as a secondary fallback.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
SETTINGS = ROOT / "scripts/templates/livecontainer_refresh_settings.swift"

RUN_NOW = 'NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow")'


def primitives():
    return PRIMITIVES.read_text(encoding="utf-8")


def shell():
    return SHELL.read_text(encoding="utf-8")


def test_refresh() -> str:
    text = shell()
    start = text.index("func runTestRefresh(")
    end = text.index("private func checkTestResult", start)
    return text[start:end]


def manual_refresh() -> str:
    text = SETTINGS.read_text(encoding="utf-8")
    start = text.index("private func notifyManualRefresh()")
    return text[start:start + 900]


def targeted_refresh() -> str:
    text = shell()
    start = text.index("struct V3TargetedRefreshSection")
    return text[start:start + 2200]


class SharedPolicyTests(unittest.TestCase):
    """Item 10: one authoritative refresh prerequisite policy."""

    def test_policy_exists_and_interprets_the_snapshot_string_once(self):
        text = primitives()
        self.assertIn("V3_REFRESH_PREREQUISITE_POLICY_V1", text)
        self.assertIn("struct V3RefreshPrerequisite", text)
        self.assertIn('case "Pairing file available": return .satisfied', text)
        self.assertIn('case "Pairing file required": return .pairingRequired', text)
        self.assertIn("default: return .unknown", text)
        # No call site may re-derive the rule.
        for view in (shell(), SETTINGS.read_text(encoding="utf-8")):
            self.assertNotIn('status.pairing == "Pairing file required"', view)
            self.assertNotIn('status.pairing == "Pairing file available"', view)

    def test_policy_mints_the_canonical_pairing_failure_once(self):
        text = primitives()
        self.assertIn("safeCause: .pairingRequired", text)
        self.assertIn('operation: "refresh", stage: .pairing, code: .notReady', text)
        self.assertIn("retryable: false, safeCause: .pairingRequired", text)
        for view in (shell(), SETTINGS.read_text(encoding="utf-8")):
            self.assertNotIn("safeCause: .pairingRequired", view,
                             "the pairing failure identity must be minted by the policy only")

    def test_only_a_proven_prerequisite_blocks(self):
        text = primitives()
        self.assertIn("var blocksRefresh: Bool { state == .unsatisfied }", text)
        # An unknown status must never block, or a host restart would
        # permanently disable a correctly configured device.
        self.assertIn("case .unknown: return .unknown", text.replace(
            "        case \"Pairing file available\": return .satisfied\n"
            "        case \"Pairing file required\": return .pairingRequired\n"
            "        default: return .unknown", "        case .unknown: return .unknown"))
        # Nothing else is claimed to be a refresh prerequisite.
        self.assertNotIn("blocksOnWiFi", text)
        self.assertNotIn("blocksOnVPN", text)
        self.assertNotIn("blocksOnAccount", text)

    def test_policy_exposes_the_requested_recovery_copy(self):
        text = primitives()
        self.assertIn('recoveryActionTitle: String? { kind == .pairing ? "Show Pairing Setup" : nil }', text)
        self.assertIn('"Place or import a valid pairing file, then try again."', text)
        self.assertIn('"Place or import a valid pairing file, then try again."', text)
        # The visible "what happened" line is the failure message vocabulary.
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        self.assertIn("A pairing file is required before this device can be refreshed.", failure)


class TestRefreshShortCircuitTests(unittest.TestCase):
    """Items 9 and 11: Test Refresh must not start a mutation."""

    def test_gate_runs_before_the_scheduler_notification(self):
        body = test_refresh()
        gate = body.index("V3RefreshPrerequisite.evaluate")
        post = body.index(RUN_NOW)
        self.assertLess(gate, post)
        # The blocked path ends in a return that is reached before the mutation
        # is posted, so no scheduler request is ever created.
        self.assertIn("TEST_REFRESH_BLOCKED", body[gate:post])
        self.assertIn("return", body[gate:post])
        self.assertLess(body.index("TEST_REFRESH_BLOCKED"), body.index("return", gate))
        self.assertLess(body.index("return", gate), post)
        # The blocked path must not arm the poller or the running flag.
        blocked = body[gate:body.index("return", gate)]
        for absent in ("testTask = Task {", "testRunning = true", "testRunID = requestID"):
            self.assertNotIn(absent, blocked)
        self.assertIn("testRunning = false", blocked)

    def test_blocked_path_records_the_typed_prerequisite_failure(self):
        body = test_refresh()
        self.assertIn('"actionRequired"', body)
        self.assertIn("recordFailure(operation: failure.operation", body)
        self.assertIn("stage: failure.stage.rawValue", body)
        self.assertIn("code: failure.code.rawValue", body)
        self.assertIn('"Place or import a valid pairing file, then try again."', body)
        self.assertIn("TEST_REFRESH_BLOCKED reason=pairing", body)

    def test_blocked_path_never_reports_an_unknown_cause(self):
        text = shell()
        # The generic message must not be reachable for a known prerequisite.
        self.assertIn("verificationGuidance", text)
        body = test_refresh()
        self.assertNotIn("no safe underlying cause was available", body)
        self.assertIn("verificationGuidance = \"Place or import a valid pairing file, then try again.\"", body)

    def test_verification_section_offers_pairing_recovery(self):
        text = shell()
        start = text.index('Section("Verification")')
        end = text.index("if setup.isComplete {", start)
        section = text[start:end]
        self.assertIn("setup.failureStage == CombinedFailure.Stage.pairing.rawValue", section)
        self.assertIn('Label("Show Pairing Setup"', section)
        self.assertIn('Label("Re-check Pairing"', section)
        self.assertIn("runTestRefresh(status: status)", section)
        # A structured prerequisite failure is shown for action-required too.
        self.assertIn('setup.verification.state == "actionRequired"', section)

    def test_only_one_run_now_call_site_remains_in_the_store(self):
        body = test_refresh()
        self.assertEqual(body.count(RUN_NOW), 1)
        # The store never calls the scheduler directly.
        for absent in ("runNow(", "beginRun(", "execute(source:"):
            self.assertNotIn(absent, body)


class EveryEntryPointUsesThePolicyTests(unittest.TestCase):
    """Item 10: no entry point keeps a private preflight."""

    def test_home_refresh_all(self):
        text = shell()
        start = text.index("private func start()", text.index("struct V3RefreshAllButton"))
        body = text[start:text.index("private func monitorRun", start)]
        self.assertIn("V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing)", body)
        self.assertLess(body.index("V3RefreshPrerequisite.evaluate"), body.index(RUN_NOW))

    def test_manual_refresh_in_the_refresh_manager(self):
        view = SETTINGS.read_text(encoding="utf-8")
        # The decision is evaluated once in a computed property, then enforced
        # both by disabling the control and by guarding the mutation.
        self.assertIn("private var manualRefreshBlocked: Bool {", view)
        start = view.index("private var manualRefreshBlocked: Bool {")
        self.assertIn("V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).blocksRefresh", view[start:start + 400])
        body = manual_refresh()
        self.assertIn("guard !manualRefreshBlocked else { return }", body)
        self.assertLess(body.index("guard !manualRefreshBlocked"), body.index(RUN_NOW))
        self.assertIn("A pairing file is required before this device can be refreshed.", view)
        self.assertIn(".disabled(manualRefreshBlocked)", view)

    def test_targeted_refresh(self):
        body = targeted_refresh()
        self.assertIn("V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing)", body)
        self.assertIn("blocksTargetedRefresh", body)
        self.assertIn('Button("Show Pairing Setup")', body)
        self.assertIn("status.pairingPresented = true", body)
        # The mutation call must be disabled, not merely intercepted.
        action = body[body.index('Label("Refresh " + app.name'):]
        self.assertIn(".disabled(V3RefreshPrerequisite.evaluate", action)

    def test_setup_rows_and_diagnostics_use_the_policy(self):
        text = shell()
        start = text.index("func recalculate(status: V3SideStoreStatusStore)")
        recalc = text[start:start + 2600]
        self.assertIn("V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).state", recalc)
        self.assertIn("case .unsatisfied:", recalc)
        self.assertIn("V3RefreshPrerequisite.pairingRequiredDetail", recalc)
        # The copied diagnostics line keeps the human vocabulary a support log
        # needs, not the internal policy state names.
        self.assertIn("V3SetupStore.describePairing(V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).state)", text)
        self.assertIn('case .satisfied: return "available"', text)
        self.assertIn('case .unsatisfied: return "missing"', text)
        self.assertIn('case .unknown: return "unknown"', text)
        home = text[text.index("private var setupIncomplete"):]
        home = home[:home.index("var body: some View")]
        self.assertIn("V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing).blocksRefresh", home)


class PairingGuidanceTests(unittest.TestCase):
    """Items 6, 7 and 8: pairing storage is untouched; guidance is fixed."""

    def test_placement_with_the_installation_tool_is_the_recommended_path(self):
        text = shell()
        start = text.index("struct V3PairingView")
        end = text.index("struct V3ToggleRow", start)
        view = text[start:end]
        self.assertIn('Section("Pairing File Required")', view)
        self.assertIn('Text("Recommended setup")', view)
        # The tool-specific menu names are scoped, not asserted for every tool.
        self.assertIn('Text("If you installed with iLoader:")', view)
        self.assertIn("Other installation tools use different menu names", view)
        self.assertIn("static let pairingPlacementSteps", view)

    def test_documented_iloader_steps_are_listed_in_order(self):
        text = shell()
        start = text.index("static let pairingPlacementSteps")
        block = text[start:text.index("]", start)]
        steps = [line.strip().strip('",') for line in block.splitlines()[1:] if line.strip().startswith('"')]
        self.assertEqual(steps, [
            "Connect the iPhone to the computer if your installation tool requires it.",
            "Open the tool you used to install LC+SS.",
            "Open Management.",
            "Open Manage Pairing File.",
            "If LC+SS is not listed, use Rescan Installed Apps.",
            "Find the installed LC+SS app.",
            "Choose Place for this app.",
            "Wait for the tool to confirm success.",
            "Return to LC+SS.",
        ])
        # A static member of a View cannot be referenced bare from an instance
        # body, so the call site must be qualified.
        self.assertIn("Array(Self.pairingPlacementSteps.enumerated())", text)

    def test_manual_import_is_kept_as_a_secondary_fallback(self):
        text = shell()
        start = text.index("struct V3PairingView")
        end = text.index("struct V3ToggleRow", start)
        view = text[start:end]
        self.assertIn("Alternative: Import Pairing File Manually", view)
        self.assertIn('operation: "pairingImportData"', view)
        self.assertIn("V3FilePicker", view)
        self.assertIn("Use this only if the placement tool did not work.", view)

    def test_recheck_pairing_is_offered_and_reloads_authoritative_state(self):
        text = shell()
        start = text.index("struct V3PairingView")
        end = text.index("struct V3ToggleRow", start)
        view = text[start:end]
        self.assertIn('Label("Re-check Pairing"', view)
        self.assertIn("private func recheck()", view)
        recheck = view[view.index("private func recheck()"):]
        self.assertIn("status.reload()", recheck[:recheck.index("\n    }")])
        quick = text[text.index("struct V3SetupAssistantView"):]
        self.assertIn('Label("Re-check Pairing"', quick)
        self.assertIn('Label("Show Pairing Setup"', quick)

    def test_returning_to_a_live_quick_setup_detects_a_newly_placed_file(self):
        text = shell()
        start = text.index("struct V3SetupAssistantView")
        quick = text[start:start + 14000]
        change = quick[quick.index(".onChange(of: scenePhase)"):]
        change = change[:change.index("\n    }")]
        # V3_AWAITABLE_RELOAD_V1: the reload is awaited, so recalculate can never
        # read the previous snapshot. A fire-and-forget reload followed by an
        # immediate recalculate was the race.
        self.assertIn("await status.reloadAndWait()", change)
        self.assertIn("await setup.recalculate(status: status)", change)
        self.assertLess(change.index("await status.reloadAndWait()"),
                        change.index("await setup.recalculate(status: status)"))
        self.assertNotIn("status.reload()\n", change)
        # First appearance, the sheet dismissal, and returning from a setup
        # destination all use the ordered path.
        self.assertIn("await status.reloadAndWait()", quick[:quick.index(".onChange(of: scenePhase)")])
        self.assertIn(".onChange(of: showPairingSetup)", quick)
        self.assertEqual(quick.count("await status.reloadAndWait()") >= 3, True)

    def test_pairing_storage_and_transport_are_untouched(self):
        # The working pairing mechanism must not be redesigned: no new pairing
        # request, no new store, no changed import operation.
        text = shell()
        self.assertEqual(text.count('operation: "pairingImportData"'), 1)
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        self.assertIn('PairingFileManager.shared.fetchPairingFile() == nil ? "Pairing file required" : "Pairing file available"',
                      service)
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        self.assertIn('"pairingImportData"', wire)


if __name__ == "__main__":
    unittest.main()
