"""Read-only classifier tests. Git transport is redirected to a local bare fixture."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import classify as c


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = self.temp.name
        self.env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
        self.git("init", "--bare", ".")
        tree = self.git("mktree", input="")
        self.old = self.git("commit-tree", tree, input="old\n")
        self.new = self.git("commit-tree", tree, "-p", self.old, input="new\n")
        self.unique = self.git("commit-tree", tree, "-p", self.old, input="unique\n")
        for name, head in (("master", self.new), ("retained", self.old), ("unique", self.unique)):
            self.git("update-ref", "refs/heads/" + name, head)
        self.git("tag", "-a", "v1", self.old, "-m", "Retained annotated tag")
        self.tag = self.git("rev-parse", "refs/tags/v1")
        self.config = {"repository_full_name": "NRG-Wardog/idevice", "actual_default_branch": "master",
                       "audited_default_sha": self.new,
                       "branches": [{"name": n, "sha": s, "protected": False, "inventory_candidate": n != "master"}
                                    for n, s in (("master", self.new), ("retained", self.old), ("unique", self.unique))],
                       "retained_anchors": [{"ref": "refs/heads/master", "sha": self.new},
                                            {"ref": "refs/tags/v1", "sha": None, "object_sha": self.tag}]}
        original_run = subprocess.run
        def local_only(args, **kwargs):
            args = [self.repo if isinstance(a, str) and a.startswith("https://github.com/") else a for a in args]
            return original_run(args, **kwargs)
        transport = patch.object(c.subprocess, "run", side_effect=local_only)
        transport.start(); self.addCleanup(transport.stop)
        sleep = patch.object(c.time, "sleep")
        sleep.start(); self.addCleanup(sleep.stop)

    def git(self, *args, input=None):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, input=input,
                              capture_output=True, text=True, check=True).stdout.strip()

    def classify(self, config=None, default="master"):
        class API:
            def get(self, path):
                return {"full_name": "NRG-Wardog/idevice", "default_branch": default}
        return c.classify_repository(API(), copy.deepcopy(config or self.config),
                                     "NRG-Wardog/sidestore-auto-refresh", "c" * 40)

    def test_retained_unique_and_exact_annotated_tag_peeling(self):
        report = self.classify()
        self.assertEqual(report["counts"], {"nondefault": 2, "retained": 1, "unique_or_not_proven_retained": 1,
                                            "inventory_candidates": 2, "retained_inventory_candidates": 1})
        self.assertEqual(report["retained_anchors"][1]["sha"], self.old)
        self.assertEqual(report["retained_anchors"][1]["object_sha"], self.tag)
        self.assertTrue(all(not b["deletion_authorized_by_this_report"] for b in report["branches"]))
        self.assertEqual(self.git("rev-parse", "refs/heads/retained"), self.old)
        self.assertEqual(self.git("rev-parse", "refs/heads/unique"), self.unique)

    def test_changed_tag_object_rejected_even_with_same_peeled_commit(self):
        config = copy.deepcopy(self.config)
        config["retained_anchors"][1]["object_sha"] = self.old
        with self.assertRaises(ValueError):
            self.classify(config)

    def test_changed_candidate_and_actual_default_rejected(self):
        config = copy.deepcopy(self.config)
        config["branches"][1]["sha"] = self.new
        with self.assertRaises(ValueError): self.classify(config)
        with self.assertRaises(ValueError): self.classify(default="main")

    def test_conservatively_preserved_branch_still_gets_ancestry_report(self):
        config = copy.deepcopy(self.config)
        config["branches"][1].update(inventory_candidate=False, exclusion_reasons=["historical provenance"])
        row = self.classify(config)["branches"][0]
        self.assertEqual(row["classification"], "retained")
        self.assertFalse(row["inventory_candidate"])
        self.assertEqual(row["exclusion_reasons"], ["historical provenance"])

    def test_no_mutation_entrypoint_or_unsafe_ref_names(self):
        self.assertFalse(hasattr(c, "git_delete"))
        for value in ("*", "--all", "../master", "foo:bar", "a\nb"):
            with self.subTest(value=value), self.assertRaises(ValueError): c.branch(value)


if __name__ == "__main__":
    unittest.main()
