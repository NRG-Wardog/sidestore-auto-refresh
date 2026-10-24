import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("amendment", Path(__file__).with_name("amend.py"))
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


class AmendmentTests(unittest.TestCase):
    def setUp(self):
        self.old = "Original synthetic release notes.\n"
        self.new = "Amended synthetic release notes with size comparison.\n"
        self.old_hash = A.digest(self.old.encode())
        self.new_hash = A.digest(self.new.encode())
        self.before = {"id": A.RELEASE_ID, "tag_name": A.TAG, "target_commitish": A.TARGET,
                       "name": "Unified LiveContainer + SideStore 6.0", "draft": False, "prerelease": False,
                       "immutable": False, "body": self.old, "updated_at": "before", "published_at": "original",
                       "assets": [dict(value, name=name, state="uploaded", download_count=1) for name, value in A.ASSETS.items()]}
        self.after = copy.deepcopy(self.before)
        self.after.update(body=self.new, updated_at="after")
        self.after["assets"][0]["download_count"] = 2
        self.tag = {"object": {"type": "commit", "sha": A.TARGET}}
        self.latest = {"id": A.RELEASE_ID}
        self.receipt = {"repository": A.REPOSITORY, "release_id": A.RELEASE_ID, "tag": A.TAG,
                        "target_commit": A.TARGET, "operation": "update-release-body-only",
                        "old_body_sha256": self.old_hash, "new_body_sha256": self.new_hash}

    def run_fake(self, responses):
        with tempfile.TemporaryDirectory() as temp:
            old_cwd = os.getcwd()
            try:
                os.chdir(temp)
                with patch.object(A, "OLD_BODY_SHA256", self.old_hash), \
                     patch.dict(os.environ, {"GITHUB_REPOSITORY": A.REPOSITORY, "GITHUB_REF": A.BRANCH, "GITHUB_EVENT_NAME": "push"}), \
                     patch.object(A, "reviewed_body", return_value=(self.new, self.receipt)), \
                     patch.object(A, "api", side_effect=responses) as api:
                    A.amend()
                    return api.call_args_list, json.loads(Path("amendment-result.json").read_text())
            finally:
                os.chdir(old_cwd)

    def test_exactly_one_body_only_patch_and_assets_preserved(self):
        calls, result = self.run_fake([self.before, self.tag, self.latest, self.before, self.after, self.after, self.tag, self.latest])
        writes = [call for call in calls if len(call.args) == 2]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].args, (f"repos/{A.REPOSITORY}/releases/{A.RELEASE_ID}", {"body": self.new}))
        self.assertEqual(result["status"], "verified-applied")
        self.assertTrue(result["asset_identities_preserved"])

    def test_wrong_old_body_fails_before_patch(self):
        changed = dict(self.before, body="Other editor's change")
        with self.assertRaisesRegex(ValueError, "body hash"):
            self.run_fake([changed])

    def test_identical_later_run_verifies_without_mutation(self):
        calls, result = self.run_fake([self.after, self.tag, self.latest, self.after])
        self.assertEqual(result["status"], "verified-already-applied")
        self.assertFalse(any(len(call.args) == 2 for call in calls))

    def test_identical_body_with_changed_asset_is_not_idempotent_success(self):
        changed = copy.deepcopy(self.after)
        changed["assets"][0]["digest"] = "wrong"
        with self.assertRaisesRegex(ValueError, "asset identity"):
            self.run_fake([changed])

    def test_concurrent_body_change_fails_before_patch(self):
        changed = dict(self.before, body="Other editor's concurrent change")
        with self.assertRaisesRegex(ValueError, "body hash"):
            self.run_fake([self.before, self.tag, self.latest, changed])

    def test_wrong_tag_fails_before_patch(self):
        with self.assertRaisesRegex(ValueError, "tag target"):
            self.run_fake([self.before, {"object": {"type": "commit", "sha": "b" * 40}}])

    def test_asset_digest_id_and_inventory_changes_rejected(self):
        for change in ("digest", "id", "extra"):
            changed = copy.deepcopy(self.before)
            if change == "extra":
                changed["assets"].append({"name": "extra.json"})
            else:
                changed["assets"][0][change] = "wrong"
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "asset"):
                A.validate_release(changed, self.old_hash)

    def test_post_patch_metadata_change_is_not_reported_successful(self):
        changed = dict(self.after, immutable=True)
        with self.assertRaisesRegex(ValueError, "Non-body release metadata"):
            self.run_fake([self.before, self.tag, self.latest, self.before, self.after, changed])

    def test_new_body_must_match_reviewed_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "amendment-receipt.json").write_text(json.dumps(self.receipt))
            (root / A.NOTES).write_text(self.new)
            with patch.object(A, "ROOT", root), patch.object(A, "OLD_BODY_SHA256", self.old_hash):
                self.assertEqual(A.reviewed_body()[0], self.new)
                (root / A.NOTES).write_text("Unexpected changed content")
                with self.assertRaisesRegex(ValueError, "changed after review"):
                    A.reviewed_body()

    def test_api_refuses_any_non_body_mutation(self):
        with patch.object(A.time, "sleep"), patch.object(A.subprocess, "run") as command:
            for payload in ({"body": self.new, "draft": True}, {"name": "new title"}, {}):
                with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "Unapproved API mutation"):
                    A.api(f"repos/{A.REPOSITORY}/releases/{A.RELEASE_ID}", payload)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
