import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import cleanup as c

REPO = "NRG-Wardog/idevice"
A, B, C = "a" * 40, "b" * 40, "c" * 40


def fixture():
    item = {"repository_full_name": REPO, "branch_name": "validation/old", "expected_branch_sha": A,
            "actual_default_branch": "master", "retained_anchor_ref": "refs/heads/master",
            "retained_anchor_sha": B, "disposition": "eligible", "protected": False,
            "open_pr_checks_complete": False, "active_run_checks_complete": False, "release_checks_complete": False,
            "functional_dependency_checks_complete": True,
            "ancestry": {"base_sha": A, "head_sha": B, "status": "ahead", "behind_by": 0,
                         "merge_base_sha": A}}
    return {"repository_full_name": REPO, "actual_default_branch": "master", "audited_default_sha": B,
            "reviewed_inventory_sha256": "e" * 64, "source_review_complete": True,
            "required_runtime_checks": sorted(c.RUNTIME_CHECKS),
            "pr_repositories": [REPO], "dependency_snapshots": [], "preserved_branch_names": [],
            "candidates": [item]}


def environment():
    return {"GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/master", "GITHUB_SHA": C}


class FakeAPI:
    def __init__(self):
        self.overrides = {}
        self.collections = {}
        self.calls = []

    def get(self, path, absent=False):
        self.calls.append(path)
        if path in self.overrides:
            return self.overrides[path]
        if path == f"repos/{REPO}":
            return {"full_name": REPO, "default_branch": "master", "fork": False}
        if "/git/ref/" in path:
            ref = "refs/" + path.split("/git/ref/")[1]
            return {"ref": ref, "object": {"type": "commit", "sha": C if ref == "refs/heads/master" else A}}
        if "/compare/" in path:
            base = path.split("/compare/")[1].split("...")[0]
            return {"status": "ahead", "behind_by": 0, "base_commit": {"sha": base},
                    "merge_base_commit": {"sha": base}}
        if "/git/trees/" in path:
            return {"truncated": False, "tree": []}
        if "/branches/" in path:
            return {"name": "validation/old", "protected": False, "commit": {"sha": A}}
        raise AssertionError("Unexpected fake GET: " + path)

    def collection(self, path, key=None):
        self.calls.append(path)
        if path == f"repos/{REPO}/branches" and path not in self.collections:
            return [{"name": "master", "protected": False, "commit": {"sha": C}},
                    {"name": "validation/old", "protected": False, "commit": {"sha": A}}]
        return self.collections.get(path, [])


class SafetyTests(unittest.TestCase):
    def setUp(self):
        fetch = patch.object(c, "fetch_refs")
        self.fetch = fetch.start()
        self.fetch.return_value.__enter__.return_value = "/unused-local-fixture"
        self.addCleanup(fetch.stop)
        ancestor = patch.object(c, "local_ancestor")
        self.ancestor = ancestor.start()
        self.addCleanup(ancestor.stop)

    def run_main(self, mode):
        with tempfile.TemporaryDirectory() as folder:
            raw = json.dumps(fixture()).encode()
            (Path(folder) / "allowlist.json").write_bytes(raw)
            env = dict(environment(), GITHUB_TOKEN="ephemeral-test-placeholder",
                       REVIEWED_MANIFEST_SHA256=hashlib.sha256(raw).hexdigest(), GITHUB_STEP_SUMMARY="")
            with patch.object(c, "ROOT", Path(folder)), patch.dict(os.environ, env), \
                 patch("sys.argv", ["cleanup.py", mode]), redirect_stdout(io.StringIO()):
                c.main()

    def test_verify_never_reaches_mutation(self):
        with patch.object(c, "preflight") as preflight, patch.object(c, "git_delete") as mutate:
            self.run_main("verify")
            preflight.assert_called_once()
            mutate.assert_not_called()

    def test_failed_dry_run_never_attempts_actual_delete(self):
        with patch.object(c, "preflight"), patch.object(c, "git_delete", return_value=(1, "", "rejected")) as mutate:
            with self.assertRaises(ValueError): self.run_main("apply")
            self.assertEqual(mutate.call_count, 1)
            self.assertTrue(mutate.call_args.args[1])

    def test_uncertain_delete_reads_back_once_and_never_retries(self):
        with patch.object(c, "preflight") as preflight, \
             patch.object(c, "git_delete", side_effect=[(0, "", ""), subprocess.TimeoutExpired("git", 180)]) as mutate, \
             patch.object(c, "readback", return_value=[{"observed_sha": A}]) as observe:
            with self.assertRaises(ValueError): self.run_main("apply")
            self.assertEqual(preflight.call_count, 2)
            self.assertEqual(mutate.call_count, 2)
            self.assertTrue(mutate.call_args_list[0].args[1])
            self.assertFalse(mutate.call_args_list[1].args[1])
            observe.assert_called_once()

    def test_reviewed_manifest_accepts_actual_non_main_default(self):
        c.validate_manifest(fixture(), environment())

    def test_rejects_default_duplicate_protected_incomplete_unreviewed(self):
        variants = []
        m = fixture(); m["candidates"][0]["branch_name"] = "master"; variants.append(m)
        m = fixture(); m["candidates"].append(copy.deepcopy(m["candidates"][0])); variants.append(m)
        m = fixture(); m["candidates"][0]["protected"] = True; variants.append(m)
        m = fixture(); m["candidates"][0]["functional_dependency_checks_complete"] = False; variants.append(m)
        m = fixture(); m["source_review_complete"] = False; variants.append(m)
        m = fixture(); m["required_runtime_checks"].remove("active_workflow_runs"); variants.append(m)
        m = fixture(); m["reviewed_inventory_sha256"] = "REVIEW_REQUIRED"; variants.append(m)
        m = fixture(); m["preserved_branch_names"] = ["validation/old"]; variants.append(m)
        for m in variants:
            with self.subTest(manifest=m), self.assertRaises(ValueError):
                c.validate_manifest(m, environment())

    def test_no_arbitrary_branch_patterns_or_execution_context(self):
        for name in ("*", "--all", "../master", "foo:bar", "a\nb"):
            m = fixture(); m["candidates"][0]["branch_name"] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                c.validate_manifest(m, environment())
        for key, value in (("GITHUB_REPOSITORY", "another/repo"), ("GITHUB_REF", "refs/heads/main"),
                           ("GITHUB_EVENT_NAME", "workflow_dispatch")):
            env = environment(); env[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                c.validate_manifest(fixture(), env)

    def test_complete_preflight_is_read_only(self):
        api = FakeAPI()
        c.preflight(api, fixture(), C)
        self.assertGreater(len(api.calls), 8)

    def test_changed_candidate_default_protection_and_unique_work_fail(self):
        overrides = [
            (f"repos/{REPO}/git/ref/heads/master", {"ref": "refs/heads/master", "object": {"type": "commit", "sha": B}}),
            (f"repos/{REPO}/compare/{B}...{C}", {"status": "diverged", "behind_by": 1}),
            (f"repos/{REPO}", {"full_name": REPO, "default_branch": "main"}),
        ]
        for key, value in overrides:
            api = FakeAPI(); api.overrides[key] = value
            with self.subTest(path=key), self.assertRaises(ValueError):
                c.preflight(api, fixture(), C)
        for protected, head in ((False, B), (True, A)):
            api = FakeAPI()
            api.collections[f"repos/{REPO}/branches"] = [{"name": "validation/old", "protected": protected, "commit": {"sha": head}}]
            with self.subTest(protected=protected, head=head), self.assertRaises(ValueError):
                c.preflight(api, fixture(), C)
        self.ancestor.side_effect = ValueError("Unique work")
        with self.assertRaises(ValueError):
            c.preflight(FakeAPI(), fixture(), C)

    def test_open_pr_source_and_base_are_excluded(self):
        for side in ("head", "base"):
            api = FakeAPI()
            api.collections[f"repos/{REPO}/pulls?state=open"] = [{side: {"ref": "validation/old", "repo": {"full_name": REPO}}}]
            with self.subTest(side=side), self.assertRaises(ValueError):
                c.preflight(api, fixture(), C)

    def test_release_and_active_run_are_excluded(self):
        for path, item in ((f"repos/{REPO}/releases", {"target_commitish": "validation/old"}),
                           (f"repos/{REPO}/actions/runs?status=waiting", {"head_branch": "validation/old"})):
            api = FakeAPI(); api.collections[path] = [item]
            with self.subTest(path=path), self.assertRaises(ValueError):
                c.preflight(api, fixture(), C)

    def test_stale_dependency_and_non_control_change_fail(self):
        m = fixture()
        m["dependency_snapshots"] = [{"repository_full_name": REPO, "ref": "refs/heads/dependency", "expected_sha": B, "kind": "immutable_branch"}]
        with self.assertRaises(ValueError):
            c.preflight(FakeAPI(), m, C)
        api = FakeAPI()
        api.overrides[f"repos/{REPO}/git/trees/{C}?recursive=1"] = {"truncated": False, "tree": [
            {"path": ".github/workflows/release.yml", "mode": "100644", "type": "blob", "sha": A}]}
        with self.assertRaises(ValueError):
            c.preflight(api, fixture(), C)

    def test_other_default_may_advance_only_by_cleanup_control_files(self):
        repo = "NRG-Wardog/SideSign"
        api = FakeAPI()
        api.overrides[f"repos/{repo}"] = {"default_branch": "main"}
        api.overrides[f"repos/{repo}/git/ref/heads/main"] = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": C}}
        snapshot = {"repository_full_name": repo, "ref": "refs/heads/main", "expected_sha": B, "kind": "actual_default"}
        c.verify_dependency_snapshot(api, snapshot)
        api.overrides[f"repos/{repo}/git/trees/{C}?recursive=1"] = {"truncated": False, "tree": [
            {"path": ".ci/branch-cleanup/allowlist.json", "mode": "100644", "type": "blob", "sha": A}]}
        c.verify_dependency_snapshot(api, snapshot)
        api.overrides[f"repos/{repo}/git/trees/{C}?recursive=1"]["tree"][0]["path"] = "Package.swift"
        with self.assertRaises(ValueError): c.verify_dependency_snapshot(api, snapshot)

    def test_retained_tag_is_peeled_and_exact_sha_is_fetched(self):
        api = FakeAPI()
        api.overrides[f"repos/{REPO}/git/ref/tags/v1"] = {"ref": "refs/tags/v1", "object": {"type": "tag", "sha": B}}
        api.overrides[f"repos/{REPO}/git/tags/{B}"] = {"object": {"type": "commit", "sha": A}}
        self.assertEqual(c.ref(api, REPO, "refs/tags/v1"), A)
        m = fixture(); m["candidates"][0]["retained_anchor_ref"] = "refs/tags/v1"
        m["candidates"][0]["retained_anchor_object_sha"] = C
        c.preflight(api, m, C)
        self.assertEqual(self.fetch.call_args.args[1]["refs/tags/v1"], {"object_sha": C, "commit_sha": B})

    def test_exact_readback_requires_absence_and_retained_ancestry(self):
        api = FakeAPI()
        self.assertEqual(c.readback(api, fixture(), C)[0]["observed_sha"], A)
        api.collections[f"repos/{REPO}/branches"] = [{"name": "master", "commit": {"sha": C}}]
        self.assertIsNone(c.readback(api, fixture(), C)[0]["observed_sha"])
        self.ancestor.side_effect = ValueError("Unique work")
        with self.assertRaises(ValueError):
            c.readback(api, fixture(), C)

    def test_command_has_only_exact_leased_head_deletions(self):
        command = c.push_command(fixture(), False)
        self.assertIn("--atomic", command)
        self.assertIn("--force-with-lease=refs/heads/validation/old:" + A, command)
        self.assertEqual(command[-1], ":refs/heads/validation/old")
        self.assertNotIn("--force", command)
        self.assertNotIn("--mirror", command)
        self.assertNotIn("--all", command)
        self.assertIn("--dry-run", c.push_command(fixture(), True))

    def test_api_operation_spacing_and_no_mutation_method(self):
        with patch.object(c.time, "sleep") as sleep, patch.object(c, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{}'
            c.GitHub("ephemeral-test-placeholder").get(f"repos/{REPO}")
            sleep.assert_called_once_with(3.1)
            req = opener.return_value.open.call_args.args[0]
            self.assertEqual(req.get_method(), "GET")

    def test_real_get_adapter_accepts_compare_and_exact_read_routes(self):
        paths = [f"repos/{REPO}/compare/{A}...{B}", f"repos/{REPO}/git/trees/{A}?recursive=1",
                 f"repos/{REPO}/git/ref/heads/validation/old", f"repos/{REPO}/git/tags/{A}",
                 f"repos/{REPO}/branches?per_page=100&page=1", f"repos/{REPO}/releases?per_page=100&page=1",
                 f"repos/{REPO}/pulls?state=open&per_page=100&page=1",
                 f"repos/{REPO}/actions/runs?status=waiting&per_page=100&page=1"]
        with patch.object(c.time, "sleep"), patch.object(c, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{}'
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(c.GitHub("ephemeral-test-placeholder").get(path), {})
                    request = opener.return_value.open.call_args.args[0]
                    self.assertEqual(request.full_url, "https://api.github.com/" + path)
                    self.assertEqual(request.get_method(), "GET")

    def test_real_get_adapter_rejects_traversal_encoding_and_unlisted_routes(self):
        paths = [f"repos/{REPO}/../branches", f"repos/{REPO}/git/ref/heads/../../main",
                 f"repos/{REPO}/git/ref/heads/%2e%2e/main", f"repos/{REPO}/git/ref/heads/%252e%252e/main",
                 f"repos/{REPO}/git/ref/heads/a%2fb", f"repos/{REPO}/git/ref/heads/a%5cb",
                 f"repos/{REPO}/git/ref/heads/%00", f"repos/{REPO}/git/ref/heads/%zz",
                 f"repos/{REPO}/compare/{A}...{B}/../branches", f"repos/{REPO}/compare/main...master",
                 f"repos/{REPO}/branches?per_page=100&page=1&page=2", f"repos/{REPO}/branches?per_page=100&page=%31",
                 f"repos/{REPO}/issues", f"repos/{REPO}/git/refs", f"repos/{REPO}#fragment",
                 f"https://evil.example/repos/{REPO}", f"//evil.example/repos/{REPO}",
                 f"\nrepos/{REPO}", f"repos/{REPO}/git/ref/heads/a\\b"]
        with patch.object(c.time, "sleep") as sleep, patch.object(c, "build_opener") as opener:
            for path in paths:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    c.GitHub("ephemeral-test-placeholder").get(path)
            opener.assert_not_called()
            sleep.assert_not_called()


class LocalGitLeaseTests(unittest.TestCase):
    """Real Git against a local temporary bare repository; no network or credentials."""
    def test_atomic_delete_rejects_stale_sha_and_retains_default(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                       GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
            remote = str(Path(d) / "remote.git")
            client = str(Path(d) / "client")
            def git(*args, cwd=None, input=None, check=True):
                return subprocess.run(["git", *args], cwd=cwd or remote, input=input,
                                      env=env, capture_output=True, text=True, check=check)
            git("init", "--bare", remote, cwd=d)
            git("init", client, cwd=d)
            empty = git("mktree", input="").stdout.strip()
            old = git("commit-tree", empty, input="old\n").stdout.strip()
            new = git("commit-tree", empty, "-p", old, input="new\n").stdout.strip()
            unique = git("commit-tree", empty, "-p", old, input="unique\n").stdout.strip()
            c.local_ancestor(remote, old, new)
            with self.assertRaises(ValueError):
                c.local_ancestor(remote, unique, new)
            git("tag", "-a", "retained-tag", old, "-m", "Retained annotated tag")
            tag_object = git("rev-parse", "refs/tags/retained-tag").stdout.strip()
            for refname, head in (("master", new), ("one", old), ("two", old)):
                git("update-ref", "refs/heads/" + refname, head)
            real_run = subprocess.run
            def local_transport(args, **kwargs):
                args = [remote if isinstance(a, str) and a.startswith("https://github.com/") else a for a in args]
                return real_run(args, **kwargs)
            with patch.object(c.subprocess, "run", side_effect=local_transport), patch.object(c.time, "sleep"):
                with c.fetch_refs(REPO, {"refs/tags/retained-tag": {"object_sha": tag_object, "commit_sha": None}}) as fetched:
                    self.assertEqual(git("rev-parse", "refs/cleanup-review/0^{commit}", cwd=fetched).stdout.strip(), old)
                with self.assertRaises(ValueError):
                    with c.fetch_refs(REPO, {"refs/tags/retained-tag": {"object_sha": old, "commit_sha": old}}):
                        self.fail("Wrong tag object must not pass even if peeled commit matches")
            command = ["push", "--atomic", "--force-with-lease=refs/heads/one:" + old,
                       "--force-with-lease=refs/heads/two:" + old, remote, ":refs/heads/one", ":refs/heads/two"]
            self.assertEqual(git(*command[:1], "--dry-run", *command[1:], cwd=client).returncode, 0)
            git("update-ref", "refs/heads/two", new)
            self.assertNotEqual(git(*command, cwd=client, check=False).returncode, 0)
            self.assertEqual(git("rev-parse", "refs/heads/one").stdout.strip(), old)
            self.assertEqual(git("rev-parse", "refs/heads/two").stdout.strip(), new)
            git("update-ref", "refs/heads/two", old)
            self.assertEqual(git(*command, cwd=client).returncode, 0)
            self.assertNotEqual(git("show-ref", "--verify", "refs/heads/one", check=False).returncode, 0)
            self.assertNotEqual(git("show-ref", "--verify", "refs/heads/two", check=False).returncode, 0)
            self.assertEqual(git("rev-parse", "refs/heads/master").stdout.strip(), new)

    def test_exact_fetch_rejects_changed_sha_and_uses_no_wildcards(self):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout=B + "\n", stderr="")
        with patch.object(c.subprocess, "run", side_effect=run) as calls, patch.object(c.time, "sleep"):
            with self.assertRaises(ValueError):
                with c.fetch_refs(REPO, {"refs/heads/validation/old": A}):
                    self.fail("Changed SHA must be rejected")
        fetch = next(call.args[0] for call in calls.call_args_list if "fetch" in call.args[0])
        self.assertIn("refs/heads/validation/old:refs/cleanup-review/0", fetch)
        self.assertNotIn("*", " ".join(fetch))
        self.assertNotIn("push", fetch)


if __name__ == "__main__":
    unittest.main()
