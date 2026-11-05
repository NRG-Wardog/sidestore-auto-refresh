#!/usr/bin/env python3
"""Offline collector safety and Git evidence fixtures. No network access in tests."""
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("inventory", Path(__file__).with_name("inventory.py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.cmd("init", "-b", "main")
        self.cmd("config", "user.email", "fixture@example.invalid")
        self.cmd("config", "user.name", "Offline fixture")
        self.write("base.txt", "base\n")
        self.base = self.commit("base")
        limits = dict(m.LIMITS, total_seconds=120, local_seconds=15)
        self.runtime = m.Runtime(self.root / "runtime", limits)
        self.git = m.Git(self.runtime, self.repo)
        self.evidence = m.Evidence(self.root / "evidence", self.runtime)

    def tearDown(self):
        self.temp.cleanup()

    def cmd(self, *args):
        p = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=self.repo,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return p.stdout.decode().strip()

    def write(self, path, value):
        dest = self.repo / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(value)

    def commit(self, subject):
        self.cmd("add", ".")
        self.cmd("commit", "-m", subject)
        return self.cmd("rev-parse", "HEAD")

    def config(self, default, rows):
        return {"repository": "NRG-Wardog/SideSign", "default_branch": "main", "default_sha": default,
                "branches": [{"name": name, "sha": sha, "protected_at_receipt": False} for name, sha in rows]}

    def test_cherry_pick_equivalence_with_distinct_history(self):
        self.cmd("checkout", "-b", "feature")
        self.write("feature.txt", "feature change\n")
        branch = self.commit("feature work")
        self.cmd("checkout", "main")
        self.write("default.txt", "default work\n")
        self.commit("advance default")
        self.cmd("cherry-pick", branch)
        default = self.cmd("rev-parse", "HEAD")
        result = m.analyze_repository(self.git, self.config(default, [("feature", branch)]), self.evidence)
        row = result["branches"][0]
        self.assertFalse(row["head_is_ancestor_of_default"])
        self.assertFalse(row["tree_equals_default_tip"])
        self.assertEqual(row["unique_commit_count"], 1)
        self.assertEqual(row["unique_commits"][0]["git_cherry_sign"], "-")
        self.assertEqual(len(row["unique_commits"][0]["patch_id_stable"]), 40)
        self.assertTrue(row["evidence_complete_within_selected_scope"])
        self.assertFalse(row["deletion_authorized"])
        diff = self.evidence.root / row["merge_base_diffs"][0]["path"]
        self.assertIn(b"+feature change", diff.read_bytes())
        self.assertIn(b"-default work", (self.evidence.root / row["default_to_branch_diff"]["path"]).read_bytes())

    def test_unchanged_tree_and_same_tree_reviewed_branch(self):
        self.cmd("checkout", "-b", "feature")
        self.write("temporary.txt", "temporary\n")
        change = self.commit("temporary work")
        self.cmd("revert", "--no-edit", change)
        head = self.cmd("rev-parse", "HEAD")
        result = m.analyze_repository(self.git, self.config(self.base, [("feature", head), ("same-tree", self.base)]), self.evidence)
        row = result["branches"][0]
        self.assertTrue(row["tree_equals_default_tip"])
        self.assertEqual(row["same_tree_commits_on_default_history"], [self.base])
        self.assertEqual(row["same_tree_reviewed_branch_tips"][0]["name"], "same-tree")
        self.assertEqual(row["unique_commit_count"], 2)
        self.assertTrue(row["merge_base_diffs"][0]["tree_equals_branch"])
        self.assertEqual(row["semantic_conclusion"], "requires-independent-review")
        self.assertFalse(row["deletion_authorized"])

    def test_merge_commits_receive_parent_diffs_not_false_patch_ids(self):
        self.cmd("checkout", "-b", "left")
        self.write("left.txt", "left\n")
        self.commit("left")
        self.cmd("checkout", "-b", "right", self.base)
        self.write("right.txt", "right\n")
        right = self.commit("right")
        self.cmd("checkout", "left")
        self.cmd("merge", "--no-ff", "-m", "merge right", right)
        head = self.cmd("rev-parse", "HEAD")
        result = m.analyze_repository(self.git, self.config(self.base, [("left", head)]), self.evidence)
        row = result["branches"][0]
        merge = next(c for c in row["unique_commits"] if c["sha"] == head)
        self.assertEqual(merge["git_cherry_interpretation"], "not-applicable-merge")
        self.assertIsNone(merge["patch_id_stable"])
        catalog = json.loads((self.evidence.root / "SideSign/commit-evidence.json").read_text())
        self.assertEqual(len(catalog[head]["patches"]), 2)

    def test_empty_commit_is_explicit_and_not_patch_equivalence(self):
        self.cmd("commit", "--allow-empty", "-m", "empty")
        head = self.cmd("rev-parse", "HEAD")
        cache = {}
        detail = m.collect_commit(self.git, head, self.evidence, "SideSign", cache)
        self.assertEqual(detail["patch_id_status"], "empty-tree-change")
        self.assertIsNone(detail["patch_id_stable"])

    def test_unrelated_histories_are_explicit(self):
        self.cmd("checkout", "--orphan", "unrelated")
        self.cmd("rm", "-rf", ".")
        self.write("unrelated.txt", "unrelated\n")
        head = self.commit("unrelated")
        result = m.analyze_repository(self.git, self.config(self.base, [("unrelated", head)]), self.evidence)
        row = result["branches"][0]
        self.assertEqual(row["merge_bases"], [])
        self.assertFalse(row["head_is_ancestor_of_default"])
        self.assertFalse(row["default_is_ancestor_of_head"])
        self.assertEqual(row["unique_commit_count"], 1)

    def test_truncation_never_generates_patch_id_or_complete_evidence(self):
        self.write("large.txt", "x" * 2000)
        head = self.commit("larger than reviewed test bound")
        self.runtime.limits["commit_patch_bytes"] = 64
        self.runtime.limits["diff_bytes"] = 64
        result = m.analyze_repository(self.git, self.config(self.base, [("large", head)]), self.evidence)
        row = result["branches"][0]
        self.assertFalse(row["evidence_complete_within_selected_scope"])
        self.assertTrue(row["default_to_branch_diff"]["truncated"])
        self.assertIsNone(row["unique_commits"][0]["patch_id_stable"])
        self.assertEqual(row["unique_commits"][0]["patch_id_status"], "incomplete-patch-evidence")

    def test_aggregate_patch_budget_reports_omissions(self):
        self.write("change.txt", "some change\n")
        head = self.commit("change")
        self.runtime.limits["patch_artifact_bytes"] = 1
        result = m.analyze_repository(self.git, self.config(self.base, [("change", head)]), self.evidence)
        self.assertFalse(result["branches"][0]["evidence_complete_within_selected_scope"])
        self.assertLessEqual(self.evidence.patch_bytes, 1)
        self.assertTrue(any(x["omitted"] for x in self.evidence.artifacts))

    def test_unique_commit_and_default_history_limits_are_explicit(self):
        self.write("one.txt", "one\n")
        default = self.commit("one")
        self.write("two.txt", "two\n")
        self.commit("two")
        self.write("three.txt", "three\n")
        head = self.commit("three")
        self.runtime.limits["unique_commits_per_branch"] = 1
        self.runtime.limits["default_tree_history_entries"] = 1
        result = m.analyze_repository(self.git, self.config(default, [("many", head)]), self.evidence)
        row = result["branches"][0]
        self.assertTrue(row["unique_commits_truncated"])
        self.assertFalse(row["default_tree_history_search_complete"])
        self.assertEqual(row["unique_commit_count"], 2)

    def test_exact_control_successor_only(self):
        path = ".ci/branch-semantic-review/inventory.json"
        self.write(path, "{}\n")
        head = self.commit("review controls")
        self.assertEqual(m.verify_control_successor(self.git, self.base, head), [path])
        self.write("src/code.py", "print('not allowed')\n")
        bad = self.commit("code change")
        with self.assertRaisesRegex(ValueError, "outside exact"):
            m.verify_control_successor(self.git, self.base, bad)
        self.cmd("revert", "--no-edit", bad)
        with self.assertRaisesRegex(ValueError, "touched non-control"):
            m.verify_control_successor(self.git, self.base, self.cmd("rev-parse", "HEAD"))

    def test_exact_control_paths_do_not_allow_arbitrary_prefix_files(self):
        self.write(".ci/branch-semantic-review/evil.py", "raise Exception()\n")
        head = self.commit("unreviewed prefix file")
        with self.assertRaisesRegex(ValueError, "outside exact"):
            m.verify_control_successor(self.git, self.base, head)

    def test_minimal_maximal_head_cover_is_structural_only(self):
        self.write("one.txt", "one\n")
        one = self.commit("one")
        self.write("two.txt", "two\n")
        two = self.commit("two")
        self.cmd("checkout", "-b", "divergent", self.base)
        self.write("other.txt", "other\n")
        other = self.commit("other")
        config = self.config(self.base, [("one", one), ("two", two), ("two-alias", two), ("other", other)])
        cover = m.ancestry_cover(self.git, config)
        self.assertEqual(cover["minimal_distinct_retaining_head_count"], 2)
        self.assertEqual(cover["distinct_head_count"], 3)
        self.assertEqual(cover["reviewed_branch_count"], 4)
        self.assertTrue(cover["all_reviewed_heads_structurally_covered"])
        self.assertFalse(cover["deletion_authorized"])
        self.assertFalse(cover["tag_creation_authorized"])
        main_group = next(r for r in cover["maximal_heads"] if r["retaining_head"] == two)
        self.assertEqual(set(main_group["retaining_branches"]), {"two", "two-alias"})
        self.assertEqual({r["head"] for r in main_group["covered_reviewed_heads"]}, {one, two})

    def test_same_patch_ids_link_distinct_branch_commits(self):
        self.cmd("checkout", "-b", "a")
        self.write("same.txt", "same\n")
        a = self.commit("a")
        self.cmd("checkout", "-b", "b", self.base)
        self.write("same.txt", "same\n")
        b = self.commit("b")
        result = m.analyze_repository(self.git, self.config(self.base, [("a", a), ("b", b)]), self.evidence)
        ids = [r["unique_commits"][0]["patch_id_stable"] for r in result["branches"]]
        self.assertEqual(ids[0], ids[1])
        index = json.loads((self.evidence.root / "SideSign/patch-id-index.json").read_text())
        self.assertEqual(set(index["patch_id_stable_to_commits"][ids[0]]), {a, b})


class SafetyTests(unittest.TestCase):
    def test_manifest_bound_to_verified_source(self):
        root = Path(__file__).parent
        expected = m.digest((root / "inventory.json").read_bytes())
        manifest, actual = m.load_manifest(root, expected)
        self.assertEqual(actual, expected)
        self.assertEqual(sum(len(r["branches"]) for r in manifest["repositories"]), 97)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            m.load_manifest(root, "0" * 64)

    def test_manifest_rejects_scope_change_even_with_updated_manifest_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ["inventory.json", "source-receipt.json", "inventory.py", "test_inventory.py"]:
                (root / name).write_bytes((Path(__file__).parent / name).read_bytes())
            manifest = json.loads((root / "inventory.json").read_text())
            manifest["repositories"][0]["branches"][0]["sha"] = "0" * 40
            (root / "inventory.json").write_bytes(m.json_bytes(manifest))
            with self.assertRaisesRegex(ValueError, "exact receipt"):
                m.load_manifest(root, m.digest((root / "inventory.json").read_bytes()))

    def test_remote_parser_requires_current_default_and_all_exact_heads(self):
        config = {"repository": "NRG-Wardog/SideSign", "default_branch": "main"}
        expected = {"refs/heads/main": "a" * 40, "refs/heads/feature": "b" * 40}
        raw = ("ref: refs/heads/main\tHEAD\n" + "a" * 40 + "\tHEAD\n" +
               "a" * 40 + "\trefs/heads/main\n" + "b" * 40 + "\trefs/heads/feature\n").encode()
        self.assertEqual(m.parse_remote(raw, config, expected)["exact_refs_verified"], 2)
        for bad in [raw.replace(b"refs/heads/main\tHEAD", b"refs/heads/other\tHEAD"),
                    raw.replace(b"b" * 40, b"c" * 40), raw.splitlines(keepends=True)[:-1]]:
            if isinstance(bad, list):
                bad = b"".join(bad)
            with self.assertRaises(ValueError):
                m.parse_remote(bad, config, expected)

    def test_credentials_are_not_in_collector_environment(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ,
                {"GITHUB_TOKEN": "never-pass-this", "GIT_ASKPASS": "/bad", "GIT_CONFIG_COUNT": "1"}):
            rt = m.Runtime(folder)
            self.assertNotIn("GITHUB_TOKEN", rt.env)
            self.assertNotIn("GIT_ASKPASS", rt.env)
            self.assertNotIn("GIT_CONFIG_COUNT", rt.env)
            self.assertEqual(rt.env["GIT_TERMINAL_PROMPT"], "0")

    def test_remote_operations_wait_after_previous_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            rt = m.Runtime(folder)
            with mock.patch.object(rt, "capture", return_value=m.Result(b"", 0)), mock.patch.object(m.time, "sleep") as sleep:
                rt.remote("NRG-Wardog/SideSign", "fixture", ["not-executed"])
                rt.remote("NRG-Wardog/SideSign", "fixture", ["not-executed"])
                self.assertEqual(sleep.call_count, 2)
                self.assertTrue(all(3.0 < c.args[0] <= 3.1 for c in sleep.call_args_list))

    def test_partial_cherry_is_never_interpreted_as_complete(self):
        result = m.Result(b"- " + b"a" * 40 + b"\n", -9, truncated=True)
        self.assertEqual(m.cherry_map(result), ({}, False))

    def test_bounded_capture_kills_and_marks_large_output(self):
        with tempfile.TemporaryDirectory() as folder:
            rt = m.Runtime(folder)
            result = rt.capture([sys.executable, "-c", "print('x'*10000)"], cap=20)
            self.assertTrue(result.truncated)
            self.assertFalse(result.complete)
            self.assertEqual(len(result.data), 20)

    def test_workflow_env_matches_manifest_and_runs_exact_verifier(self):
        root = Path(__file__).parent
        workflow = root.resolve().parents[1] / ".github/workflows/reviewed-branch-semantic-inventory.yml"
        env_block = workflow.read_text().split("\nenv:\n", 1)[1].split("\njobs:", 1)[0]
        entries = re.findall(r"^  ([A-Z0-9_]+): ([0-9a-f]{64})$", env_block, re.MULTILINE)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0], "REVIEWED_MANIFEST_SHA256")
        parsed_env = dict(entries)
        self.assertEqual(parsed_env["REVIEWED_MANIFEST_SHA256"], m.digest((root / "inventory.json").read_bytes()))
        result = subprocess.run([sys.executable, str(root / "inventory.py"), "--verify-manifest-only"],
                                env={**os.environ, **parsed_env}, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["manifest_sha256"], parsed_env["REVIEWED_MANIFEST_SHA256"])
        self.assertEqual(json.loads(result.stdout)["network_calls"], 0)

    def test_no_workflow_write_permissions_or_mutating_job(self):
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/reviewed-branch-semantic-inventory.yml"
        text = workflow.read_text()
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests:", text)
        self.assertNotIn("GITHUB_TOKEN:", text)
        self.assertNotIn("workflow_dispatch", text)
        self.assertNotIn("pull_request", text)
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("ea165f8d65b6e75b540449e92b4886f43607fa02", text)


if __name__ == "__main__":
    unittest.main()
