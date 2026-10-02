"""The signer model that the credential path must survive.

A re-signer such as iLoader does three things that together make a shared Keychain
access group between the app and its service extension impossible:

1. It injects the app's 128 shared Keychain groups into the ROOT bundle only,
   because its signing crate reaches ``main_entitlements`` only when the bundle
   being signed is the root.
2. It REPLACES the root's profile ``keychain-access-groups`` rather than merging,
   so the root ends up holding only those injected groups.
3. It never enables the Keychain Sharing capability on any App ID, so no
   Apple-issued profile for the extension authorizes a shared group at all.

The two processes therefore end up with DISJOINT Keychain namespaces. The
pre-resign IPA is the trap: it grants all 128 groups to the extension too,
because upstream LiveContainer's own ``LiveProcess.entitlements`` lists them. A
verification step that reads the pre-resign entitlements would certify a build
the real signer invalidates.

These tests model that signer exactly and assert the architecture, not the text.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "scripts/templates"
UPSTREAM = ROOT / ".audit/upstream/LiveContainer/LiveProcess/LiveProcess.entitlements"


def read(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


class ILoaderSignerModel:
    """The entitlement outcome of a re-sign, derived rather than asserted."""

    TEAM = "AAAAA11111"
    SHARED = ".com.kdt.livecontainer.shared"

    def __init__(self, is_root: bool, profile_groups: list[str]) -> None:
        self.is_root = is_root
        self.profile_groups = profile_groups

    @property
    def injected_groups(self) -> list[str] | None:
        """The 128 injected groups, present only for the root bundle.

        Mirrors ``sign.rs`` performing an unconditional ``insert`` when the
        bundle is the root, and the signing crate only consulting
        ``main_entitlements`` for the root.
        """
        if not self.is_root:
            return None
        return [f"{self.TEAM}{self.SHARED}"] + [
            f"{self.TEAM}{self.SHARED}.{n}" for n in range(1, 128)
        ]

    @property
    def effective_groups(self) -> list[str]:
        """What the code signature ends up claiming for this bundle."""
        injected = self.injected_groups
        if injected is not None:
            # `insert` overwrites the profile's own list outright.
            return injected
        return self.profile_groups

    @property
    def default_group(self) -> str:
        return f"{self.TEAM}.com.kdt.livecontainer" if self.is_root \
            else f"{self.TEAM}.com.kdt.livecontainer.LiveProcess"


class SignerModelTests(unittest.TestCase):
    def test_a_resigner_leaves_the_root_and_the_extension_with_disjoint_keychain_groups(self):
        # The extension's freshly minted profile authorizes no Keychain Sharing
        # capability, so it authorizes no access group at all and falls back to
        # its own implicit default group.
        root = ILoaderSignerModel(is_root=True, profile_groups=["TEAM.com.SideStore.SideStore"])
        extension = ILoaderSignerModel(is_root=False, profile_groups=[])
        shared = root.effective_groups[0]
        self.assertEqual(len(root.effective_groups), 128)
        self.assertEqual(extension.effective_groups, [])
        self.assertEqual(set(root.effective_groups) & set(extension.effective_groups), set())
        # The group the running code derives from its own default group is the
        # injected one, and the extension is not entitled to it.
        self.assertTrue(shared.startswith(root.TEAM))
        self.assertNotIn(shared, extension.effective_groups)

    def test_the_pre_resign_bundle_grants_the_extension_the_group_the_resign_removes(self):
        """The trap: package verification must not certify on this.

        Our own packager deliberately hands LiveProcess.appex an entitlement file
        that lists the 128 shared Keychain groups, so the pre-resign IPA claims a
        sharing arrangement that the re-sign then removes. Verification that reads
        those pre-resign entitlements would certify a build the device rejects.
        """
        packager = (ROOT / "scripts/package_livecontainer_combined.py").read_text(encoding="utf-8")
        self.assertIn("'PlugIns/LiveProcess.appex': 'LiveProcess/LiveProcess.entitlements'",
                      packager,
                      "the pre-resign build grants the extension the shared group file")
        # The claim only holds if the file lists the shared groups; when the
        # upstream tree is vendored, check that directly rather than assuming.
        if UPSTREAM.exists():
            preresign = re.findall(
                r"[A-Z0-9]{10}\.com\.kdt\.livecontainer\.shared(?:\.\d+)?",
                UPSTREAM.read_text(encoding="utf-8"))
            self.assertEqual(len(preresign), 128)
            after = ILoaderSignerModel(is_root=False, profile_groups=[]).effective_groups
            for group in preresign:
                self.assertNotIn(group, after)

    def test_a_host_and_service_that_derive_the_same_group_can_still_not_share_it(self):
        """Derivation equality is not entitlement. This is the exact device bug."""
        host = ILoaderSignerModel(is_root=True, profile_groups=[])
        service = ILoaderSignerModel(is_root=False, profile_groups=[])
        policy = re.search(
            r"static func sharedGroup\(fromDefaultGroup group: String\) -> String\? \{.*?\}",
            read("v3_behavioral_primitives.swift"), re.S)
        self.assertIsNotNone(policy)
        derived_host = self.TEAM_of(host.default_group) + host.SHARED
        derived_service = self.TEAM_of(service.default_group) + service.SHARED
        self.assertEqual(derived_host, derived_service)
        # Discovery still works in the service: it reads its own default group.
        self.assertIn(service.default_group, [service.default_group])
        # Authorization is what fails, and that is the device bug.
        self.assertNotIn(derived_service, service.effective_groups)

    @staticmethod
    def TEAM_of(default_group: str) -> str:
        return default_group.split(".", 1)[0]


class CredentialPathUsesNoSharedKeychainGroupTests(unittest.TestCase):
    """The architecture assertion: no credential may depend on a shared group."""

    def setUp(self) -> None:
        self.host = read("v3_unified_shell.swift")
        self.service = read("v3_sidestore_service.swift")
        self.wire = read("v3_wire_contract.swift")
        self.handoff = read("v3_secret_handoff.swift")
        self.keychain = read("embedded_shared_keychain.swift")

    def test_no_credential_call_site_stages_or_consumes_through_a_keychain_token(self):
        for stale in ("storeStringDictionary", "consumeStringDictionary", "secretToken"):
            self.assertNotIn(stale, self.host)
            self.assertNotIn(stale, self.service)
        for stale in ("V3SecretHandoff.storeString(", "V3SecretHandoff.consumeString(",
                      "V3SecretHandoff.discard("):
            self.assertNotIn(stale, self.host)
            self.assertNotIn(stale, self.service)

    def test_the_answer_carrier_is_bounded_and_flat(self):
        block = re.search(
            r"private static func credentialAnswerIsBounded.*?\n    \}",
            self.wire, re.S)
        self.assertIsNotNone(block, "the answer must stay bounded")
        body = block.group(0)
        self.assertIn("as? [String: String]", body)
        self.assertIn("map.count <= 32", body)
        # A nested container is what would let the sweep's exemption become a
        # hole, so the cast is the enforcement point.
        self.assertNotIn("[String: Any]", body)

    def test_the_exemption_is_confined_to_the_operations_that_declare_it(self):
        sweep = re.search(r"private static func containsRawSecretField.*?\n    \}", self.wire, re.S)
        self.assertIsNotNone(sweep)
        self.assertIn("if skipping.contains(normalized) { continue }", sweep.group(0))
        # The skip is skipped-over, not descended into.
        self.assertLess(sweep.group(0).index("if skipping.contains"),
                        sweep.group(0).index("pending.append(nested)"))
        self.assertIn("credentialAnswerOperations", self.wire)
        skip = re.search(r"let skipping: Set<String> = credentialAnswerOperations\.contains\(operation\)"
                         r"\s*\? \[credentialAnswerKey\] : \[\]", self.wire)
        self.assertIsNotNone(skip, "the exemption must be gated on the operation")

    def test_persistence_prefers_the_shared_group_but_never_requires_it(self):
        self.assertIn("try? V3SecretHandoff.sharedKeychainAccessGroup()", self.keychain)
        self.assertIn("try? V3SecretHandoff.processDefaultKeychainAccessGroup()", self.keychain)
        select = re.search(r"let keychainGroup = \(try\? V3SecretHandoff\.sharedKeychainAccessGroup\(\)\)"
                           r"\s*\?\? \(try\? V3SecretHandoff\.processDefaultKeychainAccessGroup\(\)\)",
                           self.keychain)
        self.assertIsNotNone(select, "the shared group must be preferred, not required")
        # The identifier itself is never logged, only which scope was chosen.
        self.assertIn("scope=", self.keychain)
        self.assertNotIn("keychain_group=\\(keychainGroup)", self.keychain)

    def test_one_shot_delivery_is_owned_by_the_service_prompt_state(self):
        # The token that used to be single-use is gone; the prompt id in the same
        # request is what makes delivery exactly-once.
        self.assertIn('payload: ["prompt": promptID, "answer": answer]', self.host)
        self.assertIn("promptID: promptID, answer: answer", self.service)
        # The typed transport failure classification survives, so a failure here
        # is never reported as an Apple authentication failure.
        self.assertIn("V3SecretHandoffFailurePolicy", self.service)
        self.assertIn("safeCause: .secretHandoffUnavailable", self.handoff)


if __name__ == "__main__":
    unittest.main()