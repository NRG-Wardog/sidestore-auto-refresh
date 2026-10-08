"""Isolated metadata/negative tests. No fork, OLD tree, git, build or network access.

Test-only reapproval lets structural tests get past the authenticated metadata
layer; it never occurs in the validator. Runtime files here are synthetic bytes.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).absolute().parents[1]
spec = importlib.util.spec_from_file_location("contract_gate", ROOT / "validate_contracts.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return sha(raw)


class ContractGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="isolated-contract-test-", dir=ROOT / "evidence")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.regpath = self.base / "metadata/compatibility-registry.json"
        self.registry = json.loads((ROOT / "compatibility-registry.json").read_text())
        self.evidence = json.loads((ROOT / self.registry["evidence"]["path"]).read_text())
        self.manifests = {}
        self.roots = {}
        for owner in sorted(gate.OWNERS):
            self.roots[owner] = str(self.base / "sources" / owner)
            pin = self.registry["owners"][owner]
            manifest = json.loads((ROOT / pin["manifest_path"]).read_text())
            for source in manifest["sources"]:
                path = Path(self.roots[owner]) / source["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = f"Synthetic contract fixture, never runtime code: {owner}:{source['path']}\n".encode()
                path.write_bytes(raw)
                source["sha256"] = sha(raw)
                os.chmod(path, 0o755 if source["mode"] == "100755" else 0o644)
            self.evidence["owners"][owner]["sources"] = copy.deepcopy(manifest["sources"])
            self.manifests[owner] = manifest
        self.reapprove()

    def manifest_path(self, owner="LiveContainer"):
        return self.regpath.parent / self.registry["owners"][owner]["manifest_path"]

    def source_path(self, owner="LiveContainer"):
        return Path(self.roots[owner]) / self.manifests[owner]["sources"][0]["path"]

    def reapprove(self):
        """Test-only trust reset, deliberately absent from the production gate."""
        for owner, manifest in self.manifests.items():
            if owner in self.registry["owners"]:
                self.registry["owners"][owner]["manifest_sha256"] = dump(self.manifest_path(owner), manifest)
        ev = self.regpath.parent / self.registry["evidence"]["path"]
        self.registry["evidence"]["sha256"] = dump(ev, self.evidence)
        self.trusted = dump(self.regpath, self.registry)

    def run_gate(self, **kwargs):
        return gate.validate(self.regpath, kwargs.get("trusted", self.trusted),
                             kwargs.get("roots", self.roots), kwargs.get("manifest_paths"))

    def rejected(self, pattern, **kwargs):
        with self.assertRaisesRegex((gate.ContractError, OSError), pattern):
            self.run_gate(**kwargs)

    def test_accepts_complete_exact_set(self):
        result = self.run_gate()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["source_files"], 88)
        self.assertEqual(result["contracts"], 10)
        self.assertEqual(result["edges"], 22)

    def test_independent_owner_manifests(self):
        mapping = {}
        for owner in gate.OWNERS:
            dst = self.base / "supplied-manifests" / owner / "reviewed.json"
            dst.parent.mkdir(parents=True)
            shutil.copyfile(self.manifest_path(owner), dst)
            mapping[owner] = dst
        self.assertEqual(self.run_gate(manifest_paths=mapping)["status"], "pass")

    def test_read_only(self):
        def snapshot():
            return {str(p.relative_to(self.base)): (sha(p.read_bytes()), p.stat().st_mode, p.stat().st_mtime_ns)
                    for p in self.base.rglob("*") if p.is_file()}
        before = snapshot()
        self.run_gate()
        self.assertEqual(snapshot(), before)

    def test_missing_source_owner(self):
        for owner in gate.OWNERS:
            with self.subTest(owner=owner):
                self.rejected("missing or extra owners", roots={o: p for o, p in self.roots.items() if o != owner})

    def test_extra_source_owner(self):
        self.rejected("missing or extra owners", roots={**self.roots, "Other": str(self.base)})

    def test_missing_manifest_owner(self):
        self.rejected("manifest paths: missing or extra owners", manifest_paths={})

    def test_extra_manifest_owner(self):
        mapping = {o: self.manifest_path(o) for o in gate.OWNERS}
        self.rejected("manifest paths: missing or extra owners", manifest_paths={**mapping, "Extra": self.regpath})

    def test_duplicate_cli_owner(self):
        with self.assertRaisesRegex(gate.ContractError, "duplicate owner"):
            gate.mappings(["SideStore=/source", "SideStore=/other"], "roots")

    def test_cli_requires_separate_trust_anchor(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "validate_contracts.py"),
                                 "--registry", str(self.regpath), "--owner", "LiveContainer=/source"],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--registry-sha256", result.stderr)

    def test_cli_duplicate_owner_exits_nonzero(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "validate_contracts.py"),
                                 "--registry", str(self.regpath), "--registry-sha256", self.trusted,
                                 "--owner", "SideStore=/source", "--owner", "SideStore=/other"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["status"], "fail")

    def test_missing_registry_owner(self):
        del self.registry["owners"]["SideSign"]
        self.reapprove()
        self.rejected("registry: missing or extra owners")

    def test_extra_registry_owner(self):
        self.registry["owners"]["Unexpected"] = {}
        self.reapprove()
        self.rejected("registry: missing or extra owners")

    def test_missing_evidence_owner(self):
        del self.evidence["owners"]["SideSign"]
        self.reapprove()
        self.rejected("evidence: missing or extra owners")

    def test_missing_manifest_file(self):
        self.manifest_path().unlink()
        self.rejected("No such file")

    def test_wrong_owner(self):
        self.manifests["LiveContainer"]["owner"] = "SideStore"
        self.reapprove()
        self.rejected("wrong manifest owner")

    def test_wrong_owner_version(self):
        self.manifests["SideStore"]["version"] = "2.0.0"
        self.reapprove()
        self.rejected("wrong manifest version")

    def test_version_ranges_forbidden(self):
        self.registry["owners"]["AnisetteKit"]["version"] = "^1.0.0"
        self.reapprove()
        self.rejected("exact numeric version required")

    def test_wrong_contract_set(self):
        self.manifests["LiveContainer"]["contract_set"] += ".other"
        self.reapprove()
        self.rejected("wrong contract set")

    def test_wrong_baseline(self):
        self.registry["integration_baseline"] = "0" * 40
        self.reapprove()
        self.rejected("wrong integration baseline")

    def test_format_boolean_is_not_integer_one(self):
        self.registry["format_version"] = True
        self.reapprove()
        self.rejected("unsupported format")

    def test_wrong_consumer_contract_version(self):
        self.manifests["LiveContainer"]["requires"][0]["version"] = "2.0.0"
        self.reapprove()
        self.rejected("incompatible contract version")

    def test_wrong_provider_contract_version(self):
        self.manifests["AnisetteKit"]["provides"][0]["version"] = "2.0.0"
        self.reapprove()
        self.rejected("incompatible contract version")

    def test_unknown_consumer_contract(self):
        self.manifests["LiveContainer"]["requires"][0]["contract"] = "unknown"
        self.reapprove()
        self.rejected("incompatible contract version")

    def test_wrong_provider_owner(self):
        self.manifests["LiveContainer"]["requires"][0]["provider"] = "jktcp"
        self.reapprove()
        self.rejected("unapproved consumer/provider contract")

    def test_missing_consumer_requirement(self):
        self.manifests["LiveContainer"]["requires"].pop()
        self.reapprove()
        self.rejected("missing or extra consumer requirements")

    def test_missing_provider_declaration(self):
        self.manifests["AnisetteKit"]["provides"].pop()
        self.reapprove()
        self.rejected("missing or extra provider declarations")

    def test_duplicate_declaration(self):
        m = self.manifests["LiveContainer"]
        m["requires"].append(copy.deepcopy(m["requires"][0]))
        self.reapprove()
        self.rejected("duplicate requires declaration")

    def test_duplicate_edge(self):
        self.registry["edges"].append(copy.deepcopy(self.registry["edges"][0]))
        self.reapprove()
        self.rejected("duplicate edge")

    def test_incompatible_registry_edge(self):
        self.registry["edges"][0]["version"] = "2.0.0"
        self.reapprove()
        self.rejected("incompatible provider contract")

    def test_source_drift(self):
        for owner in gate.OWNERS:
            with self.subTest(owner=owner):
                path = self.source_path(owner)
                original = path.read_bytes()
                path.write_bytes(original + b"drift\n")
                self.rejected("source drift")
                path.write_bytes(original)

    def test_missing_source(self):
        self.source_path().unlink()
        self.rejected("No such file")

    def test_source_mode_drift(self):
        self.source_path().chmod(0o755)
        self.rejected("executable mode drift")

    def test_special_mode_bits(self):
        self.source_path().chmod(0o4644)
        self.rejected("unsafe mode bits")

    def test_executable_metadata(self):
        self.manifest_path().chmod(0o755)
        self.rejected("metadata must be non-executable")

    def test_changed_manifest(self):
        path = self.manifest_path()
        path.write_bytes(path.read_bytes() + b"\n")
        self.rejected("trusted digest mismatch")

    def test_forged_manifest_and_local_registry(self):
        trusted = self.trusted
        self.manifests["LiveContainer"]["requires"] = []
        self.reapprove()
        self.rejected("trusted digest mismatch", trusted=trusted)

    def test_forged_source_and_manifest_disagree_with_evidence(self):
        source = self.manifests["LiveContainer"]["sources"][0]
        raw = b"forged source\n"
        self.source_path().write_bytes(raw)
        source["sha256"] = sha(raw)
        self.reapprove()
        self.rejected("sources differ from reviewed evidence")

    def test_changed_evidence(self):
        path = self.regpath.parent / self.registry["evidence"]["path"]
        path.write_bytes(path.read_bytes() + b"\n")
        self.rejected("trusted digest mismatch")

    def test_wrong_provenance(self):
        self.manifests["LiveContainer"]["provenance"]["upstream_commit"] = "0" * 40
        self.reapprove()
        self.rejected("provenance differs from reviewed evidence")

    def test_unbound_source(self):
        owner = "LiveContainer"
        source = self.manifests[owner]["sources"][0]
        for declarations in (self.manifests[owner]["provides"], self.manifests[owner]["requires"]):
            for d in declarations:
                d["source_paths"] = [p for p in d["source_paths"] if p != source["path"]]
        self.reapprove()
        self.rejected("source not bound to a contract")

    def test_duplicate_source_entry(self):
        owner = "LiveContainer"
        self.manifests[owner]["sources"].append(copy.deepcopy(self.manifests[owner]["sources"][0]))
        self.evidence["owners"][owner]["sources"] = copy.deepcopy(self.manifests[owner]["sources"])
        self.reapprove()
        self.rejected("duplicate source path")

    def test_unsafe_source_paths(self):
        owner = "LiveContainer"
        original = self.manifests[owner]["sources"][0]["path"]
        for path in ["../escape", "/absolute", "a/../escape", "a/./file", "a//file", "a\\file", "C:escape", ".git/config", ""]:
            with self.subTest(path=path):
                self.manifests[owner]["sources"][0]["path"] = path
                self.evidence["owners"][owner]["sources"] = copy.deepcopy(self.manifests[owner]["sources"])
                self.reapprove()
                self.rejected("unsafe relative path|nonempty printable string")
        self.manifests[owner]["sources"][0]["path"] = original

    def test_unsafe_manifest_path(self):
        self.registry["owners"]["LiveContainer"]["manifest_path"] = "../escape.json"
        self.trusted = dump(self.regpath, self.registry)
        self.rejected("unsafe relative path")

    def test_unsafe_evidence_path(self):
        self.registry["evidence"]["path"] = "../escape.json"
        self.trusted = dump(self.regpath, self.registry)
        self.rejected("unsafe relative path")

    def test_relative_root_rejected(self):
        self.rejected("absolute POSIX path", roots={**self.roots, "LiveContainer": "relative/root"})

    def test_root_dotdot_rejected(self):
        self.rejected("unsafe absolute path", roots={**self.roots, "LiveContainer": self.roots["LiveContainer"] + "/../LiveContainer"})

    def test_source_symlink_rejected_even_when_inside_root(self):
        path = self.source_path()
        target = path.with_name("same-bytes.txt")
        path.rename(target)
        path.symlink_to(target.name)
        self.rejected("Too many levels|symbolic")

    def test_source_hardlink_rejected(self):
        path = self.source_path()
        os.link(path, path.with_name("alias.txt"))
        self.rejected("hardlinks are forbidden")

    def test_source_directory_symlink_rejected(self):
        path = self.source_path().parent
        target = path.with_name("actual-directory")
        path.rename(target)
        path.symlink_to(target.name, target_is_directory=True)
        self.rejected("Not a directory|Too many levels")

    def test_source_root_symlink_rejected(self):
        link = self.base / "root-link"
        link.symlink_to(self.roots["LiveContainer"], target_is_directory=True)
        self.rejected("Not a directory|Too many levels", roots={**self.roots, "LiveContainer": str(link)})

    def test_registry_symlink_rejected(self):
        target = self.regpath.with_name("actual-registry.json")
        self.regpath.rename(target)
        self.regpath.symlink_to(target.name)
        self.rejected("Too many levels|symbolic")

    def test_manifest_symlink_rejected(self):
        path = self.manifest_path()
        target = path.with_name("actual-manifest.json")
        path.rename(target)
        path.symlink_to(target.name)
        self.rejected("Too many levels|symbolic")

    def test_fifo_rejected_without_blocking(self):
        path = self.source_path()
        path.unlink()
        os.mkfifo(path)
        self.rejected("not a regular file")

    def test_duplicate_json_key_rejected(self):
        raw = self.regpath.read_bytes().replace(b'{', b'{"format_version":1,', 1)
        self.regpath.write_bytes(raw)
        self.trusted = sha(raw)
        self.rejected("duplicate key")

    def test_nonfinite_json_rejected(self):
        raw = self.regpath.read_bytes().replace(b'"format_version": 1', b'"format_version": NaN', 1)
        self.regpath.write_bytes(raw)
        self.trusted = sha(raw)
        self.rejected("non-finite value")

    def test_unknown_metadata_field_rejected(self):
        self.manifests["LiveContainer"]["silently_accept_new_versions"] = True
        self.reapprove()
        self.rejected("unexpected or missing fields")

    def test_oversized_file_rejected(self):
        path = self.source_path()
        with path.open("wb") as f:
            f.truncate(gate.MAX_FILE_SIZE + 1)
        self.rejected("file exceeds size limit")

    def test_invalid_registry_digest_rejected(self):
        self.rejected("lowercase SHA-256", trusted="AUTO")


if __name__ == "__main__":
    unittest.main()
