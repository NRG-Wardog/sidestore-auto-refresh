"""Execute the App Group identity rules and pin the Swift hosts to them.

The combined build has three processes that must agree on one App Group: the
LiveContainer app, the SideStoreSupport framework inside it, and the embedded
SideStore service running in LiveProcess. Each process compiles its own copy of
the resolver, so the rules exist twice: as plain C in
``scripts/templates/LCAppGroupIdentityRules.h`` and as Swift in
``scripts/templates/v3_shared_app_group.swift``.

The C half is executed here for real. The Swift half cannot be executed without
a Swift toolchain, so it is pinned to the C half by rule identifier and by the
behavioural assertions in the Swift fixtures that macOS CI compiles and runs.
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "scripts" / "templates"
RULES = TEMPLATES / "LCAppGroupIdentityRules.h"
SELECTION = TEMPLATES / "LCAppGroupSelectionPolicy.h"
SHARED = TEMPLATES / "v3_shared_app_group.swift"
STAGING = TEMPLATES / "v3_ipa_staging.swift"
HANDOFF = TEMPLATES / "v3_secret_handoff.swift"
KEYCHAIN = TEMPLATES / "embedded_shared_keychain.swift"
HANDLER = TEMPLATES / "combined_refresh_handler.swift"
SERVICE = TEMPLATES / "v3_sidestore_service.swift"
SCHEDULER = TEMPLATES / "livecontainer_refresh_scheduler.swift"
SETTINGS = TEMPLATES / "livecontainer_refresh_settings.swift"
HANDLER = TEMPLATES / "combined_refresh_handler.swift"
SHELL = TEMPLATES / "v3_unified_shell.swift"
C_HARNESS = ROOT / "tests" / "fixtures" / "v3_app_group_identity_rules_harness.c"

RULE_IDENTIFIERS = (
    "LC_RULE_GROUP_VISIBLE_ASCII",
    "LC_RULE_GROUP_NO_SEPARATOR",
    "LC_RULE_GROUP_NO_COLON",
    "LC_RULE_GROUP_NO_TRAVERSAL",
    "LC_RULE_GROUP_BOUNDED_LENGTH",
    "LC_RULE_EXPLICIT_WINS",
    "LC_RULE_EXPLICIT_FAIL_CLOSED",
    "LC_RULE_PACKAGED_FALLBACK_ONLY",
)


def compiler() -> str | None:
    return shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


class AppGroupIdentityRuleExecutionTests(unittest.TestCase):
    def test_rule_set_executes_and_covers_every_selection_case(self):
        """Run the production C rules, not a transcription of them."""
        toolchain = compiler()
        if not toolchain:
            self.skipTest("no C toolchain available; rule execution runs in macOS CI")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / ("v3-app-group-identity" + (".exe" if toolchain.endswith("clang") or "gcc" in toolchain else ""))
            compiled = subprocess.run([toolchain, "-std=c11", "-Wall", "-Wextra",
                                       str(C_HARNESS), "-o", str(binary)],
                                      capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertEqual(compiled.stderr.strip(), "",
                             "the production rule set must compile without warnings")
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("V3_APP_GROUP_IDENTITY_PASS", result.stdout)

    def test_every_required_case_is_actually_exercised(self):
        """A harness that stops asserting is not coverage."""
        harness = C_HARNESS.read_text(encoding="utf-8")
        for case in ("1. A SideStore-suffixed group",
                     "2. An AltStore-owned selected group",
                     "3. Host/service parity",
                     "4. An unavailable runtime group fails",
                     "5. Conflicting Info.plist vs explicit runtime group",
                     "6. Cross-process lock ownership",
                     "7. The packaged order the rule set produces",
                     "9. The re-sign shape",
                     "10. Nothing malformed"):
            self.assertIn(case, harness)
        for label in ("suffixed group must be well formed",
                      "a team-suffixed group must rank as the packaged group",
                      "an AltStore-owned group is not the packaged group",
                      "an empty team suffix must not rank as the packaged group",
                      "a non-alphanumeric team suffix must not rank as the packaged group",
                      "suffixed group must be selected",
                      "altstore group must be well formed",
                      "an explicitly selected AltStore-owned group must win over the packaged one",
                      "host and service must resolve the same runtime group despite different packaged entitlements",
                      "the packaged fallbacks must differ, or the parity case proves nothing",
                      "the packaged fallback must rank the SideStore group first",
                      "an unavailable selected group must be unavailable even with a packaged fallback",
                      "an unavailable selected group must not switch to the packaged group",
                      "a conflicting Info.plist entitlement must not override the runtime group",
                      "the packaged fallback must still be available for a launch that published nothing",
                      "the process-shared lock must resolve one group in both processes",
                      "a process that cannot open the published group must take no lock, not another one",
                      "no published and no packaged group must be unavailable",
                      "an empty packaged list must be unavailable",
                      "a malformed group identifier must be rejected",
                      "an over-long identifier must be rejected",
                      "the maximum permitted length must still be accepted",
                      "a null identifier must be rejected",
                      "the packaged scan must skip malformed entries",
                      "the packaged order contains exactly the three well-formed entries",
                      "the packaged order ranks the team-suffixed SideStore group first",
                      "the packaged order ranks the plain SideStore group before other entries",
                      "an AltStore-owned entry ranks after the SideStore ones but is still used",
                      "a malformed entry never reaches the packaged order",
                      "the packaged shortcut returns the head of the order",
                      "an openable top packaged entry is used",
                      "an unopenable top packaged entry falls through to the next openable one",
                      "a packaged list with nothing openable is an unavailable shared store",
                      "a stale packaged list resolves to nothing after a re-sign",
                      "an inherited team group resolves even when the packaged list is stale",
                      "an AltStore-owned inherited group is authoritative too",
                      "an openable packaged list still cannot outrank an inherited group"):
            self.assertIn(f'"{label}"', harness)
        # The parity case must not be two calls with identical arguments, which
        # cannot fail; it models two processes with different packaged lists.
        parity = harness[harness.index("/* 3. Host/service parity"):harness.index("/* 4. An unavailable")]
        self.assertIn("hostPackaged", parity)
        self.assertIn("servicePackaged", parity)
        self.assertNotEqual(parity.count("resolve(altStore, 1, both, 2)"), 2,
                            "the parity case compares two different packaged lists")


class DedicatedEmbeddedSideStoreLaunchTests(unittest.TestCase):
    """The dedicated service launch is a second way LiveProcess starts.

    That service runs inside LiveProcess, where Bundle.main is LiveProcess.appex.
    A re-sign rewrites ALTAppGroups on the main bundle and signs extensions from
    regenerated provisioning profiles, so LiveProcess is entitled to the
    team-suffixed group while its Info.plist still lists the pre-resign names.
    Without the group in this payload the service resolves nothing, which is the
    reported storageUnavailable with group_selection_source=none.
    """

    def test_the_launch_payload_is_built_through_the_one_resolver(self):
        handler = HANDLER.read_text(encoding="utf-8")
        self.assertIn("enum V3EmbeddedSideStoreLaunchPayload {", handler)
        self.assertIn('static let appGroupKey = "lcAppGroupID"', handler)
        self.assertIn("userInfo[appGroupKey] = identity.identifier", handler)
        launch = handler[handler.index("private func launchEmbeddedSideStore("):]
        launch = launch[:launch.index("\n    private func ")]
        self.assertIn("V3EmbeddedSideStoreLaunchPayload.userInfo(", launch)
        self.assertIn("identity: V3SharedAppGroup.runtimeIdentity()", launch)
        self.assertNotIn("lcAppGroupID", launch,
                         "the launch must not build the key itself")
        self.assertNotIn("group.com.SideStore.SideStore", launch,
                         "the launch must not name a group")
        payload = handler[handler.index("enum V3EmbeddedSideStoreLaunchPayload {"):
                          handler.index("private final class CombinedServiceCallbacks")]
        for forbidden in ("LCSharedUtils.appGroupID", "ALTAppGroups",
                          "UserDefaults(suiteName:", "UserDefaults.standard"):
            self.assertNotIn(forbidden, payload,
                             "the payload builder must not resolve a group by itself")

    def test_the_guest_and_dedicated_launches_use_the_same_key(self):
        service = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        self.assertIn('@"lcAppGroupID"', service,
                      "LiveProcess still reads the key the host sends")
        self.assertIn('forKey:@"lcAppGroupID"', service,
                      "the guest launch still forwards the key")
        self.assertIn("setenv(", service, "LiveProcess still publishes the resolved group")
        self.assertIn("unsetenv(", service,
                      "LiveProcess must clear a stale key when no group was forwarded")
        handler = HANDLER.read_text(encoding="utf-8")
        self.assertEqual(handler.count('static let appGroupKey = "lcAppGroupID"'), 1)


class AppGroupRuleParityTests(unittest.TestCase):
    @staticmethod
    def strip_comments(text: str) -> str:
        """Remove comments only. String literals are load-bearing here."""
        return re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)

    def test_swift_and_c_declare_the_same_rule_set(self):
        # The Swift resolver names each rule it implements with the identifier
        # the C rule set uses, so a rule added on one side and forgotten on the
        # other is a missing identifier here rather than a silent divergence.
        rules = RULES.read_text(encoding="utf-8")
        shared = SHARED.read_text(encoding="utf-8")
        for identifier in RULE_IDENTIFIERS:
            self.assertIn(identifier, rules, f"{identifier} missing from the C rule set")
            self.assertIn(identifier, shared,
                          f"the Swift rule set lost {identifier}; the two must change together")
        self.assertIn("LC_APP_GROUP_RULE_SET_V1", rules)
        self.assertIn("LC_APP_GROUP_RULE_SET_V1", shared)

    def test_each_documented_c_rule_has_predicate_code_that_is_actually_called(self):
        # The C rule set names its rules in prose and expresses them in
        # predicates. A documented rule with no predicate, or a predicate nothing
        # calls, is a rule that does not exist.
        code = self.strip_comments(RULES.read_text(encoding="utf-8"))
        validator = code[code.index("static inline int LCAppGroupIDIsWellFormed("):
                         code.index("static inline int LCAppGroupSelectionSourceFor(")]
        for predicate in ("LCAppGroupRuleIsVisibleASCII", "LCAppGroupRuleIsReserved",
                          "LC_APP_GROUP_IDENTIFIER_MAX_LENGTH"):
            self.assertIn(predicate, validator,
                          f"{predicate} is documented but the well-formedness check does not use it")
        # LC_RULE_GROUP_NO_SEPARATOR is enforced one level down, by the reserved
        # predicate the validator calls, so the chain must exist.
        reserved = code[code.index("static inline int LCAppGroupRuleIsReserved("):
                        code.index("static inline int LCAppGroupIDIsWellFormed(")]
        self.assertIn("LCAppGroupRuleIsSeparator(value)", reserved)
        separator = code[code.index("static inline int LCAppGroupRuleIsSeparator("):
                        code.index("static inline int LCAppGroupRuleIsReserved(")]
        self.assertIn("value == '/' || value == '\\\\'", separator)
        # LC_RULE_GROUP_NO_TRAVERSAL is enforced by the previous-byte comparison
        # and the leading-byte check, both inside the same predicate.
        self.assertIn("previous == (unsigned char)'.' && current == (unsigned char)'.'", validator)
        self.assertIn("if ((unsigned char)bytes[0] == (unsigned char)'.') {", validator)
        # The precedence rules live in one function the resolver must call.
        self.assertIn("LCAppGroupSelectionSourceFor", code)
        self.assertIn("LCAppGroupFirstPackagedGroup", code)
        self.assertIn("LCAppGroupIsPackagedSideStoreGroup(candidate)", code)
        # The rule set produces the ORDER; the wrappers walk it for an openable
        # entry. Both halves must exist, or a caller has to re-rank by hand.
        self.assertIn("static inline size_t LCAppGroupOrderPackaged(", code)
        self.assertIn("static inline const char *LCAppGroupFirstPackagedGroup(", code)
        shortcut = code[code.index("static inline const char *LCAppGroupFirstPackagedGroup("):]
        self.assertIn("return written == 0 ? NULL : ordered[0];", shortcut,
                      "the packaged shortcut must return the head of the order")
        order = code[code.index("static inline size_t LCAppGroupOrderPackaged("):
                           code.index("static inline const char *LCAppGroupFirstPackagedGroup(")]
        self.assertEqual(order.count("ordered[written++] = candidate;"), 2,
                         "the order must have a SideStore pass and a remaining-entries pass")
        self.assertIn("if (LCAppGroupIsPackagedSideStoreGroup(candidate)) {", order)
        self.assertIn("if (!LCAppGroupIsPackagedSideStoreGroup(candidate)) {", order)

    def test_swift_shape_rules_match_the_c_shape_rules(self):
        shared = SHARED.read_text(encoding="utf-8")
        validator = shared[shared.index("static func wellFormedIdentifier("):
                           shared.index("static func isPackagedSideStoreGroup(")]
        code = self.strip_comments(validator)
        for token in ("0x21", "0x7E", 'UInt8(ascii: "/")', 'UInt8(ascii: "\\\\")',
                      'UInt8(ascii: ":")', "maximumIdentifierLength"):
            self.assertIn(token, code, f"the Swift validator lost the C rule for {token}")
        self.assertIn('return candidate.utf8.first == UInt8(ascii: ".") ? nil : candidate', code,
                      "a leading dot must be rejected in Swift exactly as in C")
        self.assertIn('previous == UInt8(ascii: ".") && byte == UInt8(ascii: ".") { return nil }', code,
                      "traversal must be rejected in Swift exactly as in C")
        self.assertLess(shared.index("maximumIdentifierLength = 255"), shared.index("wellFormedIdentifier"),
                        "the C bound of 255 must be the one the Swift validator reads")
        # The Objective-C wrapper must measure bytes, like the C rule, and must
        # not carry a second implementation of the ranking predicate.
        selection = SELECTION.read_text(encoding="utf-8")
        self.assertIn("LCAppGroupIDIsWellFormed(utf8, strlen(utf8))", selection)
        self.assertNotIn("groupID.length", selection)
        # The wrapper asks the rule set for the order and walks it, rather
        # than re-ranking or taking only the head.
        self.assertIn("LCAppGroupOrderPackaged((const char *const *)candidates, count,", selection)
        self.assertIn("for (size_t index = 0; index < written && resolved == nil; index++) {", selection)
        self.assertNotIn("LCIsPackagedSideStoreGroup", selection)
        self.assertNotIn("LCAppGroupFirstPackagedGroup", selection)

    def test_swift_precedence_matches_the_c_precedence(self):
        shared = SHARED.read_text(encoding="utf-8")
        resolver = shared[shared.index("static func identity(selectedGroup:"):
                          shared.index("static func runtimeIdentity(")]
        code = self.strip_comments(resolver)
        # A supplied or inherited group is authoritative and is never replaced.
        self.assertIn("let supplied = selectedGroup.flatMap { $0.isEmpty ? nil : $0 }", code)
        self.assertIn("if let authoritative = supplied ?? inherited {", code)
        # An unusable authoritative group fails instead of falling through.
        self.assertIn("guard let identifier = wellFormedIdentifier(authoritative),", code)
        self.assertIn("let containerRoot = resolveContainer(identifier) else { return nil }", code)
        # The packaged list is reached only when nothing was published, and the
        # ranking the C rule set implements is the same one.
        self.assertIn('(bundleInfo["ALTAppGroups"] as? [String])', code)
        # The same two steps the rule set performs: its order, then the first
        # entry this process can open. A packaged list is a preference, not
        # proof of entitlement.
        self.assertIn("let ordered = wellFormed.filter(isPackagedSideStoreGroup) +", code)
        self.assertIn("wellFormed.filter { !isPackagedSideStoreGroup($0) }", code)
        self.assertIn("for identifier in ordered {", code)
        self.assertIn("if let containerRoot = resolveContainer(identifier) {", code)
        self.assertIn("source: .packaged)", code)

    def test_c_precedence_function_keeps_explicit_wins_and_fails_closed(self):
        rules = RULES.read_text(encoding="utf-8")
        precedence = rules[rules.index("static inline int LCAppGroupSelectionSourceFor("):
                           rules.index("/* The first well-formed entry")]
        code = self.strip_comments(precedence)
        self.assertIn("if (runtimeGroupSupplied) {", code)
        self.assertIn("return LCAppGroupSelectionUnavailable;", code)
        self.assertIn("return LCAppGroupSelectionRuntimeGroup;", code)
        self.assertIn("if (packagedFallbackAvailable) {", code)
        self.assertIn("return LCAppGroupSelectionPackagedFallback;", code)

    def test_publishing_only_an_openable_group_is_what_closes_the_split(self):
        # The host publishes the group it resolved. If it published a group it
        # could not open, the service would reject it and choose its own
        # packaged fallback, which is the split this change exists to remove.
        shared = SHARED.read_text(encoding="utf-8")
        publish = shared[shared.index("static func publishRuntimeGroup("):
                         shared.index("/// Resolve the one authoritative identity.")]
        code = self.strip_comments(publish)
        self.assertIn("unsetenv(runtimeGroupEnvironmentKey)", code)
        self.assertIn("guard let resolved = runtimeIdentity(selectedGroup: group)?.identifier,", code)
        self.assertNotIn("wellFormedIdentifier(group)", code,
                         "publishing a merely well-formed group would let the service reject it")
        # The packaging verifier is what makes an openable group openable in the
        # service, so the guarantee is checkable rather than assumed.
        verifier = (ROOT / "scripts/verify_candidate_ipa.py").read_text(encoding="utf-8")
        self.assertIn("if host != service:", verifier)
        self.assertIn('raise ValueError("host-selectable App Groups differ from LiveProcess service entitlements")',
                      verifier)
        # The host publishes before any shared state is read.
        autorefresh = (ROOT / "scripts/patch_livecontainer_autorefresh.py").read_text(encoding="utf-8")
        self.assertLess(autorefresh.index("V3SharedAppGroup.publishRuntimeGroup("),
                        autorefresh.index("LiveContainerAutoRefreshScheduler.register()"))


class CrossProcessParityTests(unittest.TestCase):
    def test_every_cross_process_consumer_resolves_through_one_identity(self):
        consumers = {
            "IPA staging": (STAGING, "V3SharedAppGroup.identity(selectedGroup: selectedGroup"),
            "secret handoff lock": (HANDOFF, "V3SharedAppGroup.runtimeIdentity(selectedGroup: selectedGroup)"),
            "service Keychain lock": (KEYCHAIN, "V3SharedAppGroup.runtimeIdentity()"),
            "operation recovery journal": (SERVICE, "V3SharedAppGroup.environmentGroup()"),
            "cross-process refresh store": (SCHEDULER, "V3SharedRefreshStore.defaults"),
        }
        for label, (path, token) in consumers.items():
            self.assertIn(token, path.read_text(encoding="utf-8"),
                          f"{label} no longer resolves the shared App Group identity")

    def test_no_production_file_hardcodes_a_fixed_runtime_app_group(self):
        # The packaged name survives only as a fallback ranking constant, a
        # packaged Info.plist assertion in the packager, or a test expectation.
        allowed = {"v3_shared_app_group.swift", "package_livecontainer_combined.py",
                   "verify_candidate_ipa.py", "livecontainer_refresh_policy.swift"}
        offenders = []
        for path in sorted(TEMPLATES.glob("*.swift")):
            if path.name in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"forSecurityApplicationGroupIdentifier:\s*\"([^\"]+)\"", text):
                offenders.append(f"{path.name}: containerURL hardcodes {match.group(1)}")
            for match in re.finditer(r"UserDefaults\(suiteName:\s*\"group\.[^\"]+\"\)", text):
                offenders.append(f"{path.name}: fixed suite {match.group(0)}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_secret_handoff_lock_resolves_the_same_identity_as_staging(self):
        staging = STAGING.read_text(encoding="utf-8")
        handoff = HANDOFF.read_text(encoding="utf-8")
        self.assertIn("sideStoreContainerRoot(bundle: Bundle = .main", staging)
        self.assertIn("V3SharedAppGroup.runtimeIdentity(selectedGroup: selectedGroup)", handoff)
        self.assertNotIn('forSecurityApplicationGroupIdentifier: "group.com.SideStore.SideStore"', handoff)
        # The lock file lives beside staging in the same container, so the two
        # cannot disagree about which directory they are in.
        self.assertIn('"Library", "Application Support", "LiveContainer"', handoff)
        self.assertIn('"Library", "Application Support", "LiveContainer", "V3IPAStaging"', staging)

    def test_service_keychain_lock_no_longer_trusts_its_own_bundle_declaration(self):
        keychain = KEYCHAIN.read_text(encoding="utf-8")
        self.assertNotIn("Bundle.main.altstoreAppGroup == appGroup", keychain)
        self.assertIn("V3SharedAppGroup.environmentGroup()", keychain)
        self.assertIn("let appGroup = V3SharedAppGroup.runtimeIdentity()?.identifier", keychain)

    def test_recovery_journal_reports_the_same_identity_it_uses(self):
        service = SERVICE.read_text(encoding="utf-8")
        journal = service[service.index("private enum V3OperationRecoveryJournal {"):
                          service.index("static func runtimeAppGroupDiagnostic()")]
        self.assertIn("V3SharedAppGroup.environmentGroup()", journal)
        self.assertNotIn("Bundle.main.altstoreAppGroup", journal)
        # The diagnostic must be able to say the group was never published, so a
        # split is observable instead of looking like an ordinary run.
        self.assertIn('source = "none"', service)
        self.assertIn('"containerResolves": available', service)


class SharedRefreshStoreTests(unittest.TestCase):
    def test_no_cross_process_refresh_state_falls_back_to_a_private_store(self):
        for path in (SCHEDULER, SETTINGS, HANDLER, SHELL):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('UserDefaults(suiteName: "group.com.SideStore.SideStore")', text,
                             f"{path.name} still opens a fixed App Group suite")
            self.assertNotIn("?? .standard", text,
                             f"{path.name} still falls back to UserDefaults.standard for shared state")
        for path in (ROOT / "scripts/patch_background_automation.py",
                     ROOT / "scripts/patch_refresh_result_bridge.py",
                     ROOT / "scripts/patch_combined_service_startup.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('UserDefaults(suiteName: "group.com.SideStore.SideStore")', text,
                             f"{path.name} still opens a fixed App Group suite")
            self.assertNotIn('initWithSuiteName:@"group.com.SideStore.SideStore"', text,
                             f"{path.name} still opens a fixed App Group suite")

    def test_scheduler_refuses_cross_process_work_without_a_shared_store(self):
        scheduler = SCHEDULER.read_text(encoding="utf-8")
        self.assertIn("static func requireSharedStore() -> Bool {", scheduler)
        self.assertIn('print("[LIVE_CONTAINER_REFRESH] SHARED_STORE_UNAVAILABLE operation_refused=1")', scheduler)
        # Every entry point that would write cross-process state must consult it.
        for signature in ("private static func execute(", "static func register()",
                          "static func scheduleChanged()", "static func schedule()",
                          "static func recoverAfterLaunchOrResume()"):
            start = scheduler.index(signature)
            end = scheduler.index("\n    static func ", start + 1) if "\n    static func " in scheduler[start + 1:] else len(scheduler)
            self.assertIn("requireSharedStore()", scheduler[start:end],
                          f"{signature} can run without an open shared store")

    def test_settings_screen_reports_an_unavailable_store_instead_of_an_empty_schedule(self):
        settings = SETTINGS.read_text(encoding="utf-8")
        self.assertIn("private var sharedStoreUnavailable: Bool", settings)
        self.assertIn("V3SharedRefreshStore.unavailableMessage", settings)
        self.assertIn(".disabled(sharedStoreUnavailable)", settings)
        self.assertEqual(settings.count("store: V3SharedRefreshStore.defaults"), 7,
                         "every refresh property wrapper must bind to the runtime store")

    def test_host_handler_fails_with_a_structured_error_when_the_store_is_missing(self):
        handler = HANDLER.read_text(encoding="utf-8")
        self.assertIn("try V3SharedAppGroup.requireSharedUserDefaults()", handler)
        self.assertIn("V3SharedAppGroup.sharedUserDefaults()", handler)
        self.assertEqual(handler.count("V3SharedAppGroup.sharedUserDefaults()"), 2,
                         "both terminal-result paths must read the runtime store")
        # The store is resolved once, before any claim, and every read and write
        # that follows uses the resolved value rather than an optional that no
        # longer exists.
        self.assertNotIn("defaults?.", handler)
        claimed = handler[handler.index("sharedDefaults = try V3SharedAppGroup.requireSharedUserDefaults()"):]
        claimed = claimed[:claimed.index('client.refreshAllApps(withIdentifier:')]
        for use in ('sharedDefaults.string(forKey: "liveContainerAutoRefreshExpectedRunID")',
                    'sharedDefaults.string(forKey: "liveContainerAutoRefreshActiveRunID")',
                    'sharedDefaults.set(run, forKey: "liveContainerAutoRefreshExpectedRunID")',
                    'sharedDefaults.removeObject(forKey: "liveContainerAutoRefreshExpectedRunID")',
                    'sharedDefaults.set(run, forKey: "liveContainerAutoRefreshUncertainMutationRunID")',
                    'sharedDefaults.dictionary(forKey: V3DirectRefreshRunClaimPolicy.defaultsKey)'):
            self.assertIn(use, claimed)

    def test_service_verification_store_is_resolved_once_and_fails_the_run(self):
        script = (ROOT / "scripts/patch_background_automation.py").read_text(encoding="utf-8")
        self.assertIn("private func automaticRefreshDefaults() throws -> UserDefaults {", script)
        self.assertIn("try V3SharedAppGroup.requireSharedUserDefaults()", script)
        self.assertIn('debugLog("[AUTO_REFRESH] HOST_HANDOFF_UNAVAILABLE reason=shared_store_unavailable")', script)
        self.assertIn('debugLog("[AUTO_REFRESH] VERIFICATION_UNAVAILABLE reason=shared_store_unavailable")', script)
        # The store is resolved once per run, before any work, and the rest of
        # the run uses the resolved value.
        self.assertEqual(script.count("refreshDefaults = try automaticRefreshDefaults()"), 1)
        self.assertIn("let refreshDefaults: UserDefaults", script)
        self.assertIn("let expectedRunID = refreshDefaults.string(forKey:", script)
        self.assertNotIn('let refreshDefaults = UserDefaults(suiteName: "group.com.SideStore.SideStore")', script)

    def test_refresh_result_bridge_correlates_through_the_runtime_group(self):
        script = (ROOT / "scripts/patch_refresh_result_bridge.py").read_text(encoding="utf-8")
        self.assertIn("RUNTIME_APP_GROUP_ENV_KEY = runtime_app_group_environment_key()", script)
        # The Objective-C side must prove the container is openable before
        # opening the suite: +initWithSuiteName: accepts any name and writes the
        # process's own domain when the suite is not an entitled group, which
        # would stamp the run contract where the service cannot read it.
        self.assertIn("containerURLForSecurityApplicationGroupIdentifier:runtimeAppGroupID] != nil", script)
        self.assertIn("[[NSUserDefaults alloc] initWithSuiteName:runtimeAppGroupID] : nil;", script)
        self.assertIn("RESULT_STORE_UNAVAILABLE reason=runtime_app_group_unresolved", script)
        self.assertIn("V3SharedAppGroup.sharedUserDefaults()", script)
        # The run ID, the manifest and the handoff record stay one contract.
        for key in ("liveContainerAutoRefreshExpectedRunID",
                    "liveContainerAutoRefreshVerification",
                    "liveContainerAutoRefreshHostHandoffRunID"):
            self.assertIn(key, script)

    def test_host_publishes_its_own_selection_before_shared_state_is_touched(self):
        script = (ROOT / "scripts/patch_livecontainer_autorefresh.py").read_text(encoding="utf-8")
        self.assertIn("V3SharedAppGroup.publishRuntimeGroup(LCSharedUtils.appGroupID())", script)
        self.assertLess(script.index("V3SharedAppGroup.publishRuntimeGroup("),
                        script.index("LiveContainerAutoRefreshScheduler.register()"),
                        "the runtime group must be published before the scheduler reads shared state")
        self.assertIn("SHARED_APP_GROUP_FILE", script)


class AppGroupSweepClassificationTests(unittest.TestCase):
    """One focused sweep, with every remaining production hit classified.

    Each entry is either SAFE PROCESS-LOCAL (one process reads and writes it) or
    it routes through the shared runtime group. A new hit that is neither fails
    here, so the classification cannot silently go stale.
    """

    HOST = ROOT / "scripts/templates/v3_unified_shell.swift"
    SETTINGS = ROOT / "scripts/templates/livecontainer_refresh_settings.swift"
    HANDLER = ROOT / "scripts/templates/combined_refresh_handler.swift"
    RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
    SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
    STARTUP = ROOT / "scripts/patch_embedded_sidestore_startup.py"
    AUTOMATION = ROOT / "scripts/patch_background_automation.py"
    APP_LAYOUT = ROOT / "scripts/patch_app_layout.py"
    GUEST_RETURN = ROOT / "scripts/patch_guest_return.py"

    SAFE_PROCESS_LOCAL = [
        (HOST, ["V3NotificationsPromptShown"]),
        # LCBootstrap writes it under `!isLiveProcess`, and the unified shell
        # reads it: both in the host process.
        (HOST, ["V3PendingSideStoreURL"]),
        # Legacy host-local source list, migrated into the service-backed
        # sources; the retired screen that wrote it was host-only too.
        (HOST, ["LCAltStoreSourceURLs"]),
    ]

    def test_classified_hits_are_present_so_the_sweep_cannot_go_stale(self):
        for path, tokens in self.SAFE_PROCESS_LOCAL:
            text = path.read_text(encoding="utf-8")
            for token in tokens:
                self.assertIn(token, text, f"{path.name} lost the classified key {token}")

    def test_no_production_standard_default_carries_a_cross_process_contract(self):
        # Every remaining `UserDefaults.standard` hit in the live host and
        # service templates is one of the classified keys above. The refresh
        # contract is the cross-process family and it no longer uses .standard.
        classified = {}
        for path, tokens in self.SAFE_PROCESS_LOCAL:
            classified.setdefault(path, set()).update(tokens)
        for path in (self.HOST, self.SETTINGS, self.HANDLER):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r'UserDefaults\.standard[^\n]*?forKey: "([^"]+)"', text):
                key = match.group(1)
                self.assertIn(key, classified.get(path, set()),
                              f"{path.name} reads {key} from UserDefaults.standard without a "
                              f"classification; add it or move it to the runtime group")

    def test_service_side_standard_defaults_are_service_owned(self):
        # The service's own backend settings and source blocklist are read and
        # written only by the service. The host names the same keys, but it asks
        # the service for them through V3SettingsStore; it must never read them
        # out of a local store.
        runtime = self.RUNTIME.read_text(encoding="utf-8")
        service = self.SERVICE.read_text(encoding="utf-8")
        host = self.HOST.read_text(encoding="utf-8")
        self.assertIn("for key in boolSettings { bools[key] = UserDefaults.standard.bool(forKey: key) }", runtime)
        self.assertIn("let defaults = UserDefaults.standard\n        let hasCachedBlocklist = defaults.blockedSources != nil", service)
        for key in ("isBetaUpdatesEnabled", "isIdleTimeoutDisableEnabled", "responseCachingDisabled",
                    "isVerboseOperationsLoggingEnabled"):
            self.assertIn(key, service)
            self.assertNotIn(f'UserDefaults.standard.bool(forKey: "{key}")', host)
            self.assertNotIn(f'UserDefaults.standard.set(', host[host.index("struct V3SettingsStore"):]
                             if "struct V3SettingsStore" in host else "")

    def test_app_storage_declarations_name_their_intended_store(self):
        # Every @AppStorage in the live host templates binds an explicit store.
        for path in (self.HOST, self.SETTINGS, ROOT / "scripts/templates/livecontainer_grid_app_cell.swift"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "@AppStorage" not in line:
                    continue
                self.assertIn("store:", line,
                              f"{path.name} declares @AppStorage without an intended store: {line.strip()}")

    def test_diagnostics_report_the_runtime_group_not_the_packaged_one(self):
        startup = self.STARTUP.read_text(encoding="utf-8")
        self.assertIn("let runtimeAppGroup = V3SharedAppGroup.runtimeIdentity()", startup)
        self.assertNotIn('forSecurityApplicationGroupIdentifier: requestedAppGroup', startup)
        self.assertIn("app_group_source=", startup)
        # The identifier itself must never reach a log line.
        for line in startup.splitlines():
            if "app_group" in line and "debugLog" in line:
                self.assertNotIn("group.com.", line)

    def test_upstream_livecontainer_helpers_keep_their_own_runtime_group(self):
        # LiveContainer's own shared-preference helpers already resolve the
        # runtime group; they are not a fixed-suite assumption and must stay.
        for path in (self.GUEST_RETURN, self.APP_LAYOUT):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("UserDefaults(suiteName: \"group.", text)
        self.assertIn("LCUtils.appGroupUserDefault", self.HOST.read_text(encoding="utf-8"))


class TemplateInstallationTests(unittest.TestCase):
    def test_the_shared_identity_reaches_every_process_exactly_once(self):
        service = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        startup = (ROOT / "scripts/patch_combined_service_startup.py").read_text(encoding="utf-8")
        autorefresh = (ROOT / "scripts/patch_livecontainer_autorefresh.py").read_text(encoding="utf-8")
        # Service: concatenated into the embedded SideStore AppDelegate.
        self.assertIn('(TEMPLATES / "v3_shared_app_group.swift").read_text(encoding="utf-8")', service)
        # Host framework: concatenated ahead of the refresh handler that uses it.
        self.assertIn('template("v3_shared_app_group.swift") + template("combined_service_connection.swift")', startup)
        # Host app: its own generated file, because the module may declare it once.
        self.assertIn('SHARED_APP_GROUP_FILE = "LiveContainerSwiftUI/Utilities/V3SharedAppGroup.swift"', autorefresh)
        self.assertIn('template("v3_shared_app_group.swift")', autorefresh)
        # The unified shell shares the host app module, so it must not re-declare.
        shell_patch = (ROOT / "scripts/patch_v3_unified_shell.py").read_text(encoding="utf-8")
        self.assertNotIn("v3_shared_app_group", shell_patch)

    def test_the_c_rule_set_is_installed_next_to_the_selection_policy(self):
        service = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        self.assertIn('changes[live / "LiveContainer/LCAppGroupIdentityRules.h"]', service)
        self.assertIn('template_hashes[group_rules_template.name]', service)
        self.assertIn('#import "LCAppGroupIdentityRules.h"', SELECTION.read_text(encoding="utf-8"))

    def test_the_environment_key_has_one_source_of_truth(self):
        # Generated Objective-C cannot see the Swift constant, so the name is
        # repeated in two emitters. Both derive it from the authoritative
        # template, so a rename cannot leave half the contract on the old key,
        # and the derived value must actually reach the emitted source rather
        # than the Python name that produced it.
        key = re.search(r'static let runtimeGroupEnvironmentKey = "([A-Za-z0-9_]+)"',
                        SHARED.read_text(encoding="utf-8")).group(1)
        for script in ("patch_v3_service.py", "patch_refresh_result_bridge.py"):
            source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
            self.assertIn("def runtime_app_group_environment_key()", source, script)
            self.assertIn("RUNTIME_APP_GROUP_ENV_KEY = runtime_app_group_environment_key()", source, script)
            self.assertNotIn(f'"{key}"', source, script)
        # Nothing else may hardcode it.
        for path in sorted((ROOT / "scripts").glob("*.py")):
            self.assertNotIn(f'"{key}"', path.read_text(encoding="utf-8"),
                             f"{path.name} hardcodes the runtime App Group environment key")
        # The service Keychain lock and the recovery journal read the same key
        # through the one Swift constant, never a literal.
        for template in (KEYCHAIN, SERVICE):
            self.assertNotIn(f'"{key}"', template.read_text(encoding="utf-8"))
        # The emitted Objective-C must carry the key as a string literal. An
        # f-string that forgot to interpolate would leave the Python identifier
        # in the generated source, which compiles as an undeclared identifier.
        import importlib.util

        for script, expected in (("patch_refresh_result_bridge.py", f'getenv("{key}")'),
                                 ("patch_v3_service.py", f'unsetenv("{key}")')):
            spec = importlib.util.spec_from_file_location(
                "emitter_" + script[:-3], ROOT / "scripts" / script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.RUNTIME_APP_GROUP_ENV_KEY, key, script)
        bridge = (ROOT / "scripts/patch_refresh_result_bridge.py").read_text(encoding="utf-8")
        self.assertIn(f'getenv("{{RUNTIME_APP_GROUP_ENV_KEY}}")', bridge,
                      "the bridge must interpolate the derived key into the emitted getenv call")
        service = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        self.assertIn("f'    unsetenv(\"{RUNTIME_APP_GROUP_ENV_KEY}\");\\n'", service)
        self.assertIn("f'        setenv(\"{RUNTIME_APP_GROUP_ENV_KEY}\", inheritedGroupID.UTF8String, 1);\\n'",
                      service)

    def test_typed_recoverable_failure_exists_for_the_shared_store(self):
        shared = SHARED.read_text(encoding="utf-8")
        self.assertIn("enum Unavailable: Error, Equatable, LocalizedError {", shared)
        self.assertIn("case sharedStore", shared)
        self.assertIn("var isRecoverable: Bool { true }", shared)
        self.assertIn("throw Unavailable.sharedStore", shared)
        # A defined safe sentence, so the service can log a cause without
        # reaching for a provider string or a private path.
        self.assertIn("var errorDescription: String?", shared)

    def test_the_unavailable_store_reaches_the_user_inside_the_envelope(self):
        # A bare typed error thrown out of the refresh handler would lose the
        # correlation ID, the stage and the retryable cause on the wire.
        handler = HANDLER.read_text(encoding="utf-8")
        self.assertIn("do {\n            sharedDefaults = try V3SharedAppGroup.requireSharedUserDefaults()", handler)
        self.assertIn('throw CombinedFailure(operation: "refresh", stage: .persistence,', handler)
        self.assertIn("code: .unavailable, id: schedulerRunID ?? UUID().uuidString,", handler)
        self.assertIn("retryable: true, safeCause: .sharedStoreUnavailable)", handler)
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        self.assertIn("case sharedStoreUnavailable", failure)
        for handler_text in (
            "case .sharedStoreUnavailable:\n                return true",
            "case .sharedStoreUnavailable: return \"LiveContainer could not open the shared store",
            "case .sharedStoreUnavailable:\n                return \"Relaunch LiveContainer after reinstalling",
        ):
            self.assertIn(handler_text, failure,
                          "the new safe cause needs a retryability, a message and a recovery line")
        # The service side keeps an honest cause in the log and rethrows rather
        # than substituting a private store.
        script = (ROOT / "scripts/patch_background_automation.py").read_text(encoding="utf-8")
        self.assertIn('self.debugLog("[AUTO_REFRESH] SHARED_STORE_UNAVAILABLE failure_category=sharedStoreUnavailable")',
                      script)
        self.assertIn("refreshDefaults = try automaticRefreshDefaults()\n            } catch {", script)
        # The combined contract composes on top of the throwing helper.
        contract = (ROOT / "scripts/patch_combined_refresh_contract.py").read_text(encoding="utf-8")
        self.assertIn("(try? automaticRefreshDefaults())?.string(forKey:", contract)


if __name__ == "__main__":
    unittest.main()
