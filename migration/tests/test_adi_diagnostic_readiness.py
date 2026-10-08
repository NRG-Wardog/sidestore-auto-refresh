"""Synthetic readiness-admission tests; these fixtures are never native evidence.

No external artifact, owner checkout, network or compiler is needed. The real
fixed-path receipt loader runs against temporary bytes. Semantic-negative tests
approve the mutated fixture's hash deliberately, so rejection must come from the
production cross-field checks rather than an unrelated hash mismatch.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import maintained_sources as gate


OWNER_NAMES = ("LiveContainer", "SideStore", "AnisetteKit", "SideSign",
               "minimuxer", "idevice", "jktcp")
SOURCE_PROOF_OWNERS = ("SideSign", "SideStore")
EVIDENCE_NAMES = (
    "provenance/production-phase-status.json",
    "provenance/reviewed-production-inputs.json",
    "provenance/reviewed-toolchain.json",
    "provenance/Package.resolved.before",
    "provenance/Package.resolved.after",
    "provenance/source-proof-before.json",
    "provenance/source-proof-after-resolution.json",
    "provenance/source-proof-after-build.json",
    "provenance/resolution.json",
    "provenance/resolution-after-build.json",
    "provenance/compiler-input-proof.json",
    "provenance/generated-sources-proof.json",
    "provenance/local-framework-before-build.json",
    "provenance/local-framework-after-build.json",
    "provenance/local-ffi-link-hashes.json",
    "provenance/binary-artifacts.json",
    "provenance/sidestore-native-tests.json",
    "provenance/sidestore-historical-native-tests.json",
    "provenance/sidestore-historical-source-before.json",
    "provenance/sidestore-historical-source-after.json",
    "logs/sidestore-production-build.exit-code.txt",
    "logs/livecontainer-production-build.exit-code.txt",
    "logs/sidestore-production-build.log",
    "logs/livecontainer-production-build.log",
)


def synthetic_hash(label, length=64):
    return hashlib.sha256(("SYNTHETIC_NOT_NATIVE_EVIDENCE:" + label).encode()).hexdigest()[:length]


def synthetic_fixture():
    owners = {name: {"commit": synthetic_hash(name + ":commit", 40),
                     "tree": synthetic_hash(name + ":tree", 40)} for name in OWNER_NAMES}
    references = {name: {"basis_sha256": synthetic_hash(name + ":basis"),
                         "resolver_receipt_sha256": synthetic_hash(name + ":resolver")}
                  for name in SOURCE_PROOF_OWNERS}
    children = {"SideSign": {}, "SideStore": {
        "Dependencies/SideSign": owners["SideSign"]["commit"],
        "Dependencies/minimuxer": owners["minimuxer"]["commit"]}}
    locks = {
        "SideSign": {"sha256": synthetic_hash("SideSign:lock"),
                     "origin_hash": {"present": False, "value": None}},
        "SideStore": {"sha256": synthetic_hash("SideStore:lock"),
                      "origin_hash": {"present": True, "value": synthetic_hash("SideStore:origin")}},
    }
    evidence = {name: synthetic_hash(name) for name in EVIDENCE_NAMES}
    receipt = {
        "schema_version": 1, "status": "PASS", "source_basis": gate.DIAGNOSTIC_BASIS,
        "diagnostic_delta_sha256": gate.DIAGNOSTIC_METADATA["provenance/accepted-to-diagnostic-delta.json"],
        "phase": "diagnostic_permanent_graph_iphoneos",
        "run_url": "https://github.com/NRG-Wardog/LiveContainer/actions/runs/900000000001",
        "run_id": 900000000001, "run_attempt": 2,
        "validation_host_commit": synthetic_hash("validation-host:commit", 40),
        "validation_host_tree": synthetic_hash("validation-host:tree", 40),
        "artifact_id": 900000000002, "artifact_zip_sha256": synthetic_hash("artifact-zip"),
        "approved_inputs_sha256": evidence["provenance/reviewed-production-inputs.json"],
        "tested_owners": copy.deepcopy(owners), "tested_children": copy.deepcopy(children),
        "locks": copy.deepcopy(locks),
        "native_builds": {name: "PASS_UNSIGNED_RELEASE_IPHONEOS26.4"
                          for name in ("LiveContainer", "SideStore")},
        "readiness_scope": "eligible_for_gated_full_build",
        "final_exact_ref_ipa_build_required": True, "evidence_sha256": evidence,
    }
    native = {
        "receipt_sha256": synthetic_hash("replaced-by-actual-fixture-bytes"),
        "source_basis": receipt["source_basis"],
        "diagnostic_delta_sha256": receipt["diagnostic_delta_sha256"],
        "readiness_scope": receipt["readiness_scope"],
        "run_url": receipt["run_url"], "run_attempt": receipt["run_attempt"],
        "host_commit": receipt["validation_host_commit"], "host_tree": receipt["validation_host_tree"],
        "artifact_id": receipt["artifact_id"], "artifact_sha256": receipt["artifact_zip_sha256"],
        "approved_inputs_sha256": receipt["approved_inputs_sha256"],
        "tested_owners": {name: copy.deepcopy(owners[name]) for name in SOURCE_PROOF_OWNERS},
        "tested_children": copy.deepcopy(children),
    }
    pins = {"source_basis": gate.DIAGNOSTIC_BASIS,
            "contract_registry_sha256": gate.DIAGNOSTIC_REGISTRY,
            "owners": {name: {"commit": owners[name]["commit"],
                              "repository": "https://github.com/NRG-Wardog/" + name + ".git"}
                       for name in OWNER_NAMES},
            "diagnostic_dependencies": references, "native_validation": native}
    proofs = {name: {
        "owner": name, **copy.deepcopy(owners[name]),
        "status": "diagnostic_dependency_transition_pass", "production_ready": False,
        "readiness_scope": "source_transition_only_requires_separate_native_receipt",
        "source_registry_sha256": gate.DIAGNOSTIC_REGISTRY,
        "diagnostic_basis_sha256": references[name]["basis_sha256"],
        "resolver_receipt_sha256": references[name]["resolver_receipt_sha256"],
        "native_resolver_status": "reviewed_diagnostic_resolution",
        "lock_status": "reviewed_resolver_observed_lock",
        "lock_sha256": locks[name]["sha256"], "origin_hash": copy.deepcopy(locks[name]["origin_hash"]),
    } for name in SOURCE_PROOF_OWNERS}
    focused_owners = ("AnisetteKit", "SideStore", "LiveContainer")
    focused = {
        "SYNTHETIC_NOT_NATIVE_EVIDENCE": True,
        "status": "PASS", "source_snapshots_byte_identical": True,
        "tests": {"AnisetteKit": 7, "SideStore_with_LiveContainer_peer": 6, "failures": 0, "skips": 0},
        "owner_sources": {name: {**copy.deepcopy(owners[name]),
                                  "repository": pins["owners"][name]["repository"]}
                          for name in focused_owners},
    }
    delta = {"diagnostic_source_tuple": {name: copy.deepcopy(owners[name]) for name in focused_owners}}
    return {"pins": pins, "receipt": receipt, "proofs": proofs, "verified_owners": owners,
            "focused": focused, "diagnostic_delta": delta}


class DiagnosticNativeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-readiness-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "provenance/diagnostic-native-readiness.json"
        self.path.parent.mkdir()

    def verify(self, fixture, *, approve_bytes=True, approve_focused_bytes=True, roots=None):
        data = (json.dumps(fixture["receipt"], sort_keys=True, indent=2) + "\n").encode()
        self.path.write_bytes(data)
        if approve_bytes:
            fixture["pins"]["native_validation"]["receipt_sha256"] = hashlib.sha256(data).hexdigest()
        focused = (json.dumps(fixture["focused"], sort_keys=True, indent=2) + "\n").encode()
        (self.directory / "provenance/adi-focused-native-verification.json").write_bytes(focused)
        if approve_focused_bytes:
            fixture["pins"]["native_validation"]["focused_receipt_sha256"] = hashlib.sha256(focused).hexdigest()
        with mock.patch.object(gate, "contract_basis", return_value=(self.directory, gate.DIAGNOSTIC_REGISTRY)), \
             mock.patch.object(gate, "diagnostic_delta", return_value=fixture["diagnostic_delta"]):
            return gate.verify_native_readiness(fixture["proofs"], fixture["pins"], fixture["verified_owners"], roots)

    def fixture_git(self, *arguments, input=None):
        return subprocess.check_output([
            "git", "-C", str(self.git_root), "-c", "user.name=Synthetic readiness fixture",
            "-c", "user.email=fixture@example.invalid", *arguments],
            input=input, text=True, stderr=subprocess.PIPE).strip()

    def git_identity(self, revision="HEAD"):
        return {"commit": self.fixture_git("rev-parse", revision),
                "tree": self.fixture_git("rev-parse", revision + "^{tree}")}

    def update_final_identity(self, fixture):
        final = self.git_identity()
        fixture["verified_owners"]["SideStore"] = dict(final)
        fixture["pins"]["owners"]["SideStore"]["commit"] = final["commit"]
        fixture["proofs"]["SideStore"].update(final)
        return final

    def lock_descendant_fixture(self, *, tested_mode=0o644, final_mode=0o644):
        """Real Git identities; synthetic observed lock bytes and native receipt."""
        self.git_root = self.directory / "synthetic-owner"
        self.git_root.mkdir()
        self.fixture_git("init", "--quiet")
        self.fixture_git("config", "core.fileMode", "true")
        lock = self.git_root / gate.APP_LOCK
        lock.parent.mkdir(parents=True)
        before = b'{"version":3,"pins":[],"synthetic":"before-native-resolution"}\n'
        after = b'{"version":3,"pins":[],"synthetic":"actual-observed-lock"}\n'
        lock.write_bytes(before)
        lock.chmod(tested_mode)
        (self.git_root / "Source.swift").write_text("let syntheticFixture = true\n")
        self.fixture_git("add", ".")
        self.fixture_git("commit", "--quiet", "-m", "Synthetic child object")
        child = self.git_identity()
        self.fixture_git("update-index", "--add", "--cacheinfo", "160000",
                         child["commit"], "Dependencies/SideSign")
        self.fixture_git("commit", "--quiet", "-m", "Synthetic native-tested ancestor")
        tested = self.git_identity()
        lock.write_bytes(after)
        lock.chmod(final_mode)
        self.fixture_git("add", gate.APP_LOCK)
        self.fixture_git("commit", "--quiet", "-m", "Record only synthetic observed lock")
        fixture = synthetic_fixture()
        fixture["receipt"]["tested_owners"]["SideStore"] = dict(tested)
        fixture["pins"]["native_validation"]["tested_owners"]["SideStore"] = dict(tested)
        # Match the fixture's real unchanged child gitlink throughout the graph.
        for owners in (fixture["verified_owners"], fixture["receipt"]["tested_owners"],
                       fixture["pins"]["native_validation"]["tested_owners"]):
            owners["SideSign"] = dict(child)
        fixture["pins"]["owners"]["SideSign"]["commit"] = child["commit"]
        fixture["proofs"]["SideSign"].update(child)
        for children in (fixture["receipt"]["tested_children"],
                         fixture["pins"]["native_validation"]["tested_children"]):
            children["SideStore"]["Dependencies/SideSign"] = child["commit"]
        for label, data in (("before", before), ("after", after)):
            fixture["receipt"]["evidence_sha256"]["provenance/Package.resolved." + label] = hashlib.sha256(data).hexdigest()
        digest = hashlib.sha256(after).hexdigest()
        fixture["receipt"]["locks"]["SideStore"] = {
            "sha256": digest, "origin_hash": {"present": False, "value": None}}
        fixture["proofs"]["SideStore"].update(lock_sha256=digest,
                                              origin_hash={"present": False, "value": None})
        self.update_final_identity(fixture)
        return fixture

    def test_exact_seven_owners_and_source_only_false_proofs_pass_without_mutation(self):
        fixture = synthetic_fixture()
        before = copy.deepcopy(fixture)
        self.assertEqual(len(fixture["receipt"]["evidence_sha256"]), 24)
        self.verify(fixture)
        before["pins"]["native_validation"]["receipt_sha256"] = fixture["pins"]["native_validation"]["receipt_sha256"]
        before["pins"]["native_validation"]["focused_receipt_sha256"] = fixture["pins"]["native_validation"]["focused_receipt_sha256"]
        self.assertEqual(fixture, before)
        self.assertTrue(all(proof["production_ready"] is False for proof in fixture["proofs"].values()))

    def test_receipt_bytes_require_the_independently_approved_digest(self):
        fixture = synthetic_fixture()
        self.verify(fixture)
        fixture["receipt"]["run_attempt"] += 1
        with self.assertRaisesRegex(ValueError, "reviewed hash"):
            self.verify(fixture, approve_bytes=False)

    def test_every_tested_owner_commit_and_tree_are_bound(self):
        for owner in OWNER_NAMES:
            for field in ("commit", "tree"):
                fixture = synthetic_fixture()
                fixture["receipt"]["tested_owners"][owner][field] = synthetic_hash("substitution", 40)
                with self.subTest(owner=owner, field=field), self.assertRaisesRegex(ValueError, "seven acquired owners"):
                    self.verify(fixture)

    def test_matching_receipt_and_acquisition_cannot_override_owner_pin(self):
        fixture = synthetic_fixture()
        for source in (fixture["receipt"]["tested_owners"], fixture["verified_owners"]):
            source["AnisetteKit"]["commit"] = synthetic_hash("unapproved-Anisette", 40)
        with self.assertRaisesRegex(ValueError, "seven acquired owners differ from final pins"):
            self.verify(fixture)

    def test_missing_extra_and_unverified_owners_fail(self):
        for change in ("missing", "extra", "unverified"):
            fixture = synthetic_fixture()
            if change == "missing":
                del fixture["receipt"]["tested_owners"]["jktcp"]
            elif change == "extra":
                fixture["receipt"]["tested_owners"]["unexpected"] = dict(fixture["verified_owners"]["jktcp"])
            else:
                fixture["verified_owners"] = None
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "seven acquired owners"):
                self.verify(fixture)

    def test_both_source_proofs_are_required(self):
        for owner in SOURCE_PROOF_OWNERS:
            fixture = synthetic_fixture()
            del fixture["proofs"][owner]
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, "both native readiness proofs"):
                self.verify(fixture)

    def test_old_or_falsely_ready_source_proofs_are_rejected(self):
        changes = (("status", "exact_dependency_transition_pass"), ("production_ready", True),
                   ("production_ready", 0), ("readiness_scope", "eligible_for_gated_full_build"),
                   ("native_resolver_status", "not_run_for_candidate"),
                   ("lock_status", "accepted_lock_retained_pending_resolution"),
                   ("diagnostic_basis_sha256", synthetic_hash("wrong-basis")),
                   ("resolver_receipt_sha256", synthetic_hash("wrong-resolver")))
        for owner in SOURCE_PROOF_OWNERS:
            for field, value in changes:
                fixture = synthetic_fixture()
                fixture["proofs"][owner][field] = value
                with self.subTest(owner=owner, field=field), self.assertRaisesRegex(ValueError, "source and actual resolver proof"):
                    self.verify(fixture)

    def test_run_url_id_and_attempt_must_match_approved_identity(self):
        for field, value in (("run_id", 900000000003), ("run_attempt", 3),
                             ("run_attempt", True), ("run_attempt", 0),
                             ("run_url", "https://github.com/NRG-Wardog/LiveContainer/actions/runs/900000000001\n")):
            fixture = synthetic_fixture()
            fixture["receipt"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.verify(fixture)

    def test_host_and_artifact_identity_are_independently_bound(self):
        for field, value in (("validation_host_commit", synthetic_hash("wrong-host", 40)),
                             ("validation_host_tree", synthetic_hash("wrong-host-tree", 40)),
                             ("artifact_id", 900000000003),
                             ("artifact_zip_sha256", synthetic_hash("wrong-artifact"))):
            fixture = synthetic_fixture()
            fixture["receipt"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "run/artifact binding"):
                self.verify(fixture)

    def test_native_tested_children_must_match_approved_graph(self):
        fixture = synthetic_fixture()
        fixture["receipt"]["tested_children"]["SideStore"]["Dependencies/SideSign"] = synthetic_hash("wrong-child", 40)
        with self.assertRaisesRegex(ValueError, "owner/child graph"):
            self.verify(fixture)

    def test_lock_hash_or_origin_cannot_differ_from_source_proof(self):
        for owner in SOURCE_PROOF_OWNERS:
            for field, value in (("sha256", synthetic_hash("wrong-lock")),
                                 ("origin_hash", {"present": True, "value": synthetic_hash("different-origin")})):
                fixture = synthetic_fixture()
                fixture["receipt"]["locks"][owner][field] = value
                with self.subTest(owner=owner, field=field), self.assertRaisesRegex(ValueError, "lock differs"):
                    self.verify(fixture)

    def test_matching_but_malformed_origin_state_is_not_accepted(self):
        for origin in ({"present": False, "value": synthetic_hash("unexpected-value")},
                       {"present": True, "value": None}, {"present": 0, "value": None},
                       {"present": True, "value": synthetic_hash("newline") + "\n"}):
            fixture = synthetic_fixture()
            fixture["receipt"]["locks"]["SideSign"]["origin_hash"] = dict(origin)
            fixture["proofs"]["SideSign"]["origin_hash"] = dict(origin)
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "originHash"):
                self.verify(fixture)

    def test_missing_native_test_evidence_and_extra_evidence_are_rejected(self):
        for change in ("missing-native-tests", "extra"):
            fixture = synthetic_fixture()
            if change == "missing-native-tests":
                del fixture["receipt"]["evidence_sha256"]["provenance/sidestore-native-tests.json"]
            else:
                fixture["receipt"]["evidence_sha256"]["provenance/unreviewed.json"] = synthetic_hash("extra")
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "evidence inventory"):
                self.verify(fixture)

    def test_trailing_newline_in_evidence_hash_is_rejected(self):
        fixture = synthetic_fixture()
        fixture["receipt"]["evidence_sha256"]["provenance/sidestore-native-tests.json"] += "\n"
        with self.assertRaisesRegex(ValueError, "evidence inventory or digest"):
            self.verify(fixture)

    def test_approved_input_digest_is_cross_bound_to_pin_and_evidence(self):
        for change in ("receipt", "evidence", "both"):
            fixture = synthetic_fixture()
            if change in ("receipt", "both"):
                fixture["receipt"]["approved_inputs_sha256"] = synthetic_hash("unapproved-inputs")
            if change in ("evidence", "both"):
                fixture["receipt"]["evidence_sha256"]["provenance/reviewed-production-inputs.json"] = synthetic_hash("unapproved-inputs")
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.verify(fixture)

    def test_both_native_app_builds_must_pass(self):
        for owner in ("LiveContainer", "SideStore"):
            for outcome in ("FAILED", "NOT_RUN", "PASS_UNSIGNED_DEBUG_IPHONEOS26.4", None):
                fixture = synthetic_fixture()
                fixture["receipt"]["native_builds"][owner] = outcome
                with self.subTest(owner=owner, outcome=outcome), self.assertRaisesRegex(ValueError, "both diagnostic native app builds"):
                    self.verify(fixture)

    def test_receipt_cannot_drop_final_ipa_requirement_or_claim_wrong_scope(self):
        for field, value in (("status", "PENDING"), ("source_basis", "parity"),
                             ("phase", "sidesign"), ("schema_version", True),
                             ("readiness_scope", "source_transition_only_requires_separate_native_receipt"),
                             ("final_exact_ref_ipa_build_required", False)):
            fixture = synthetic_fixture()
            fixture["receipt"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "identity or eligibility"):
                self.verify(fixture)

    def test_real_git_lock_only_descendant_preserves_actual_tested_identity(self):
        fixture = self.lock_descendant_fixture()
        tested = dict(fixture["receipt"]["tested_owners"]["SideStore"])
        final = dict(fixture["verified_owners"]["SideStore"])
        self.assertNotEqual(tested, final)
        before_status = self.fixture_git("status", "--porcelain")
        self.verify(fixture, roots={"SideStore": self.git_root})
        self.assertEqual(fixture["receipt"]["tested_owners"]["SideStore"], tested)
        self.assertEqual(fixture["pins"]["native_validation"]["tested_owners"]["SideStore"], tested)
        self.assertEqual(fixture["proofs"]["SideStore"]["tree"], final["tree"])
        self.assertEqual(self.fixture_git("status", "--porcelain"), before_status)

    def test_lock_descendant_requires_real_owner_root(self):
        fixture = self.lock_descendant_fixture()
        for roots in (None, {}, {"SideSign": self.git_root}):
            with self.subTest(roots=roots), self.assertRaisesRegex(ValueError, "seven acquired owners"):
                self.verify(fixture, roots=roots)

    def test_lock_descendant_binds_both_observed_snapshots(self):
        fixture = self.lock_descendant_fixture()
        for label in ("before", "after"):
            bad = copy.deepcopy(fixture)
            bad["receipt"]["evidence_sha256"]["provenance/Package.resolved." + label] = synthetic_hash("wrong-" + label)
            with self.subTest(snapshot=label), self.assertRaisesRegex(ValueError, "actual resolver evidence"):
                self.verify(bad, roots={"SideStore": self.git_root})

    def test_wrong_committed_lock_bytes_do_not_pass_with_fresh_final_tree(self):
        fixture = self.lock_descendant_fixture()
        (self.git_root / gate.APP_LOCK).write_bytes(b'{"synthetic":"unobserved replacement"}\n')
        self.fixture_git("add", gate.APP_LOCK)
        self.fixture_git("commit", "--quiet", "-m", "Synthetic wrong lock bytes")
        self.update_final_identity(fixture)
        with self.assertRaisesRegex(ValueError, "actual resolver evidence"):
            self.verify(fixture, roots={"SideStore": self.git_root})

    def test_lock_descendant_after_snapshot_must_match_native_lock_proof(self):
        fixture = self.lock_descendant_fixture()
        wrong = synthetic_hash("wrong-native-lock-proof")
        fixture["receipt"]["locks"]["SideStore"]["sha256"] = wrong
        fixture["proofs"]["SideStore"]["lock_sha256"] = wrong
        with self.assertRaisesRegex(ValueError, "observed after bytes"):
            self.verify(fixture, roots={"SideStore": self.git_root})

    def test_extra_runtime_change_after_native_build_is_rejected(self):
        fixture = self.lock_descendant_fixture()
        (self.git_root / "Source.swift").write_text("let unexpectedRuntimeEdit = true\n")
        self.fixture_git("add", "Source.swift")
        self.fixture_git("commit", "--quiet", "-m", "Synthetic untested runtime change")
        self.update_final_identity(fixture)
        with self.assertRaisesRegex(ValueError, "not exactly the observed app lock"):
            self.verify(fixture, roots={"SideStore": self.git_root})

    def test_changed_child_gitlink_after_native_build_is_rejected(self):
        fixture = self.lock_descendant_fixture()
        different_child = self.fixture_git("rev-parse", "HEAD")
        self.fixture_git("update-index", "--cacheinfo", "160000", different_child, "Dependencies/SideSign")
        self.fixture_git("commit", "--quiet", "-m", "Synthetic untested child change")
        self.update_final_identity(fixture)
        with self.assertRaisesRegex(ValueError, "not exactly the observed app lock"):
            self.verify(fixture, roots={"SideStore": self.git_root})

    def test_native_before_lock_must_have_regular_nonexecutable_mode(self):
        fixture = self.lock_descendant_fixture(tested_mode=0o755)
        with self.assertRaisesRegex(ValueError, "lock type or mode"):
            self.verify(fixture, roots={"SideStore": self.git_root})

    def test_final_lock_must_have_regular_nonexecutable_mode(self):
        fixture = self.lock_descendant_fixture(final_mode=0o755)
        with self.assertRaisesRegex(ValueError, "lock type or mode"):
            self.verify(fixture, roots={"SideStore": self.git_root})

    def test_unrelated_commit_with_identical_tested_tree_is_rejected(self):
        fixture = self.lock_descendant_fixture()
        tested = fixture["receipt"]["tested_owners"]["SideStore"]
        unrelated = self.fixture_git("commit-tree", tested["tree"], input="Synthetic unrelated history\n")
        self.assertNotEqual(unrelated, tested["commit"])
        tested["commit"] = unrelated
        fixture["pins"]["native_validation"]["tested_owners"]["SideStore"]["commit"] = unrelated
        with self.assertRaises(subprocess.CalledProcessError):
            self.verify(fixture, roots={"SideStore": self.git_root})

    def test_wrong_tested_or_final_tree_is_rejected_against_real_git(self):
        fixture = self.lock_descendant_fixture()
        for identity in ("tested", "final"):
            bad = copy.deepcopy(fixture)
            if identity == "tested":
                bad["receipt"]["tested_owners"]["SideStore"]["tree"] = synthetic_hash("invented-tree", 40)
            else:
                bad["verified_owners"]["SideStore"]["tree"] = synthetic_hash("invented-tree", 40)
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "SideStore tree differs"):
                self.verify(bad, roots={"SideStore": self.git_root})

    def test_descendant_with_no_lock_change_is_not_a_lock_transition(self):
        fixture = self.lock_descendant_fixture()
        tested = fixture["receipt"]["tested_owners"]["SideStore"]
        self.fixture_git("checkout", "--quiet", "--detach", tested["commit"])
        self.fixture_git("commit", "--quiet", "--allow-empty", "-m", "Synthetic metadata-only commit")
        self.update_final_identity(fixture)
        with self.assertRaisesRegex(ValueError, "not exactly the observed app lock"):
            self.verify(fixture, roots={"SideStore": self.git_root})

    def test_other_six_owners_cannot_use_lock_descendant_exception(self):
        fixture = self.lock_descendant_fixture()
        for owner in OWNER_NAMES:
            if owner == "SideStore":
                continue
            bad = copy.deepcopy(fixture)
            bad["receipt"]["tested_owners"][owner]["commit"] = synthetic_hash("changed-" + owner, 40)
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, "seven acquired owners"):
                self.verify(bad, roots={name: self.git_root for name in OWNER_NAMES})

    def test_focused_receipt_bytes_require_their_own_approved_digest(self):
        fixture = synthetic_fixture()
        self.verify(fixture)
        fixture["focused"]["source_snapshots_byte_identical"] = False
        with self.assertRaisesRegex(ValueError, "reviewed hash"):
            self.verify(fixture, approve_focused_bytes=False)

    def test_focused_sources_must_match_the_immutable_observer_tuple(self):
        for owner in ("AnisetteKit", "SideStore", "LiveContainer"):
            for field in ("commit", "tree", "repository"):
                fixture = synthetic_fixture()
                fixture["focused"]["owner_sources"][owner][field] = "unapproved synthetic source"
                with self.subTest(owner=owner, field=field), self.assertRaisesRegex(ValueError, "another producer/decoder tuple"):
                    self.verify(fixture)

    def test_focused_wrong_counts_failures_and_skips_are_rejected(self):
        for field, value in (("AnisetteKit", 6), ("SideStore_with_LiveContainer_peer", 5),
                             ("failures", 1), ("skips", 1)):
            fixture = synthetic_fixture()
            fixture["focused"]["tests"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "focused diagnostic native proof"):
                self.verify(fixture)

    def test_focused_counts_must_be_integer_observations(self):
        for field, value in (("AnisetteKit", 7.0), ("SideStore_with_LiveContainer_peer", 6.0),
                             ("failures", False), ("skips", False)):
            fixture = synthetic_fixture()
            fixture["focused"]["tests"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "focused diagnostic native proof"):
                self.verify(fixture)

    def test_focused_pending_or_changed_source_snapshots_are_rejected(self):
        for field, value in (("status", "PENDING"), ("source_snapshots_byte_identical", False),
                             ("source_snapshots_byte_identical", 1)):
            fixture = synthetic_fixture()
            fixture["focused"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "focused diagnostic native proof"):
                self.verify(fixture)


if __name__ == "__main__":
    unittest.main(verbosity=2)
