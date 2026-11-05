import copy
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("promotion", Path(__file__).with_name("promote.py"))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.config = P.settings()
        self.ipa = b"synthetic unchanged IPA test bytes"
        self.config["ipa_size"] = len(self.ipa)
        self.config["ipa_sha256"] = P.digest(self.ipa)
        self.commit = "a" * 40
        self.files = {
            P.SOURCE_IPA: self.ipa,
            P.SOURCE_IPA[:-4] + ".verification.json": b"{}",
            "SHA256SUMS": (self.config["ipa_sha256"] + "  diagnostic/" + P.SOURCE_IPA + "\n").encode(),
            "size-bytes.txt": (str(len(self.ipa)) + "\n").encode(),
            "build-label.json": P.json_bytes({"product_version": "6.0", "channel": "experimental",
                "native_workflow_commit": self.config["native"]["head_sha"],
                "native_compile_run": str(self.config["native"]["id"]), "native_compile_attempt": "1"}),
        }

    def archive(self, files=None):
        path = self.root / "input.zip"
        with zipfile.ZipFile(path, "w") as zipped:
            for name, contents in (files or self.files).items():
                zipped.writestr(name, contents)
        self.config["artifact"]["size_in_bytes"] = path.stat().st_size
        self.config["artifact"]["digest"] = "sha256:" + P.digest(path.read_bytes())
        return path

    def local_bundle(self):
        metadata = self.root / "reviewed"
        metadata.mkdir()
        for name in self.config["approved_public_files"]:
            raw = (P.ROOT / name).read_bytes()
            if name == "SHA256SUMS":
                raw = (self.config["ipa_sha256"] + "  " + P.IPA + "\n").encode()
            (metadata / name).write_bytes(raw)
            self.config["public_file_sha256"][name] = P.digest(raw)
        archive = self.archive()
        bundle = self.root / "bundle"
        with patch.object(P, "ROOT", metadata):
            digest = P.make_bundle(archive, bundle, self.config, self.commit)
        return bundle, digest

    def test_exact_unchanged_bytes(self):
        archive = self.archive()
        target = self.root / P.IPA
        P.extract_reviewed_ipa(archive, target, self.config)
        self.assertEqual(target.read_bytes(), self.ipa)

    def test_zip_digest_tamper_rejected(self):
        archive = self.archive()
        self.config["artifact"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "ZIP digest"):
            P.extract_reviewed_ipa(archive, self.root / P.IPA, self.config)

    def test_ipa_digest_tamper_rejected_even_with_rebound_zip(self):
        files = dict(self.files)
        files[P.SOURCE_IPA] = b"x" * len(self.ipa)
        archive = self.archive(files)
        with self.assertRaisesRegex(ValueError, "Raw IPA identity"):
            P.extract_reviewed_ipa(archive, self.root / P.IPA, self.config)

    def test_zip_traversal_and_extra_inventory_rejected(self):
        for name in ("../personal.mobiledevicepairing", "/secret.p12", "extra.json"):
            files = dict(self.files)
            files[name] = b"synthetic rejected input"
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "ZIP inventory"):
                P.extract_reviewed_ipa(self.archive(files), self.root / P.IPA, self.config)

    def test_duplicate_archive_name_rejected(self):
        archive = self.archive()
        with zipfile.ZipFile(archive, "a") as zipped:
            zipped.writestr(P.SOURCE_IPA, self.ipa)
        self.config["artifact"]["size_in_bytes"] = archive.stat().st_size
        self.config["artifact"]["digest"] = "sha256:" + P.digest(archive.read_bytes())
        with self.assertRaisesRegex(ValueError, "ZIP inventory"):
            P.extract_reviewed_ipa(archive, self.root / P.IPA, self.config)

    def test_verified_public_bundle(self):
        folder, digest = self.local_bundle()
        inventory = P.verify_bundle(folder, digest, self.config, self.commit)
        self.assertEqual(set(inventory["files"]), set((*P.PUBLIC_ASSETS, P.NOTES)))

    def test_personal_file_cannot_enter_public_bundle(self):
        folder, digest = self.local_bundle()
        (folder / "personal.mobiledevicepairing").write_text("synthetic-only")
        with self.assertRaisesRegex(ValueError, "publication inventory"):
            P.verify_bundle(folder, digest, self.config, self.commit)

    def test_personal_manifest_field_cannot_replace_reviewed_manifest(self):
        folder, digest = self.local_bundle()
        (folder / "release-provenance.json").write_text('{"private_email":"synthetic@example.invalid"}')
        with self.assertRaisesRegex(ValueError, "file identity"):
            P.verify_bundle(folder, digest, self.config, self.commit)

    def test_metadata_change_cannot_be_rebound_by_transfer_inventory(self):
        folder, digest = self.local_bundle()
        bad = folder / "release-provenance.json"
        bad.write_text('{"private_email":"synthetic@example.invalid"}')
        inventory = json.loads((folder / P.INVENTORY).read_text())
        inventory["files"][bad.name] = {"sha256": P.digest(bad.read_bytes()), "size": bad.stat().st_size}
        (folder / P.INVENTORY).write_bytes(P.json_bytes(inventory))
        with self.assertRaisesRegex(ValueError, "Unreviewed public metadata"):
            P.verify_bundle(folder, P.digest((folder / P.INVENTORY).read_bytes()), self.config, self.commit)

    def test_run_must_match_exact_successful_identity(self):
        expected = self.config["package"]
        value = dict(expected, repository={"full_name": P.SOURCE}, status="completed", conclusion="success",
                     head_commit={"tree_id": expected["tree_id"]})
        P.verify_run(value, expected)
        for key, changed in (("head_sha", "b" * 40), ("run_attempt", 2), ("conclusion", "failure"), ("path", "wrong.yml")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                P.verify_run(dict(value, **{key: changed}), expected)

    def test_existing_published_release_is_not_overwritten(self):
        folder, digest = self.local_bundle()
        inventory = P.verify_bundle(folder, digest, self.config, self.commit)
        release = {"tag_name": P.TAG, "target_commitish": self.commit, "name": "Unified LiveContainer + SideStore 6.0",
                   "body": "notes", "draft": False, "prerelease": False, "assets": []}
        with self.assertRaisesRegex(ValueError, "draft/public state"):
            P.validate_release(release, inventory, "notes", self.commit, draft=True, complete=False)

    def test_resume_only_matching_draft_assets(self):
        folder, digest = self.local_bundle()
        inventory = P.verify_bundle(folder, digest, self.config, self.commit)
        expected = inventory["files"][P.IPA]
        release = {"tag_name": P.TAG, "target_commitish": self.commit, "name": "Unified LiveContainer + SideStore 6.0",
                   "body": "notes", "draft": True, "prerelease": False,
                   "assets": [{"name": P.IPA, "state": "uploaded", "size": expected["size"], "digest": "sha256:" + expected["sha256"]}]}
        self.assertEqual(P.validate_release(release, inventory, "notes", self.commit, draft=True, complete=False), {P.IPA})
        release["assets"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "uploaded asset"):
            P.validate_release(release, inventory, "notes", self.commit, draft=True, complete=False)

    def test_draft_lookup_uses_authenticated_collection_and_pinned_id(self):
        draft = {"id": self.config["reviewed_draft_id"], "tag_name": P.TAG, "draft": True}
        with patch.object(P, "github_json", side_effect=[[draft], draft]) as api:
            self.assertEqual(P.lookup_reviewed_draft(self.config), draft)
        self.assertEqual(api.call_args_list[0].args[0], f"repos/{P.DESTINATION}/releases?per_page=100&page=1")
        self.assertEqual(api.call_args_list[1].args[0], f"repos/{P.DESTINATION}/releases/{draft['id']}")
        self.assertFalse(any("/releases/tags/" in call.args[0] for call in api.call_args_list))

    def test_missing_duplicate_or_different_draft_never_creates_release(self):
        draft = {"id": self.config["reviewed_draft_id"], "tag_name": P.TAG}
        for collection in ([], [draft, draft], [dict(draft, id=123)]):
            with self.subTest(collection=collection), patch.object(P, "github_json", return_value=collection), self.assertRaises(ValueError):
                P.lookup_reviewed_draft(self.config)

    def test_draft_target_notes_or_unexpected_assets_rejected(self):
        folder, digest = self.local_bundle()
        inventory = P.verify_bundle(folder, digest, self.config, self.commit)
        target = self.config["publication_target"]
        draft = {"tag_name": P.TAG, "target_commitish": target, "name": "Unified LiveContainer + SideStore 6.0",
                 "body": "notes", "draft": True, "prerelease": False, "assets": []}
        for change in ({"target_commitish": "b" * 40}, {"body": "other notes"}, {"assets": [{"name": "personal.json"}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                P.validate_release(dict(draft, **change), inventory, "notes", target, draft=True, complete=False)

    def test_exact_empty_draft_resumes_by_id_without_create_or_delete(self):
        folder, digest = self.local_bundle()
        inventory = P.verify_bundle(folder, digest, self.config, self.commit)
        target, release_id = self.config["publication_target"], self.config["reviewed_draft_id"]
        draft = {"id": release_id, "tag_name": P.TAG, "target_commitish": target,
                 "name": "Unified LiveContainer + SideStore 6.0", "body": (folder / P.NOTES).read_text(),
                 "draft": True, "prerelease": False, "assets": [], "html_url": "https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.0"}
        complete = copy.deepcopy(draft)
        complete["assets"] = [{"name": name, "state": "uploaded", "size": inventory["files"][name]["size"],
                               "digest": "sha256:" + inventory["files"][name]["sha256"]} for name in P.PUBLIC_ASSETS]
        published = dict(complete, draft=False)
        with patch.object(P, "context", return_value=self.commit), patch.object(P, "settings", return_value=self.config), \
             patch.object(P, "lookup_reviewed_draft", return_value=draft), patch.object(P, "tag_commit", side_effect=[None, target]), \
             patch.object(P, "github_json", side_effect=[complete, published, published]), patch.object(P, "gh") as command:
            P.publish(folder, digest)
        self.assertEqual(len(command.call_args_list), 4)
        for call in command.call_args_list[:3]:
            self.assertEqual(call.args[:3], ("api", "--method", "POST"))
            self.assertIn(f"/releases/{release_id}/assets?name=", call.args[3])
        self.assertEqual(command.call_args_list[3].args[:4], ("api", "--method", "PATCH", f"repos/{P.DESTINATION}/releases/{release_id}"))
        self.assertNotEqual(inventory["publication_commit"], inventory["release_target"])

    def test_retry_target_ancestry_and_original_public_hashes_are_required(self):
        target = self.config["publication_target"]
        compare = {"status": "ahead", "base_commit": {"sha": target}, "merge_base_commit": {"sha": target}}
        original = {"type": "file", "path": ".ci/release-v6.0.0/reviewed-inputs.json", "encoding": "base64",
                    "content": base64.b64encode(P.json_bytes(self.config)).decode()}
        with patch.object(P, "github_json", side_effect=[compare, original]):
            P.verify_publication_target(self.config, self.commit)
        with patch.object(P, "github_json", return_value=dict(compare, status="diverged")), self.assertRaisesRegex(ValueError, "ancestor"):
            P.verify_publication_target(self.config, self.commit)
        changed = copy.deepcopy(self.config)
        changed["public_file_sha256"][P.NOTES] = "0" * 64
        original["content"] = base64.b64encode(P.json_bytes(changed)).decode()
        with patch.object(P, "github_json", side_effect=[compare, original]), self.assertRaisesRegex(ValueError, "original reviewed publication inputs"):
            P.verify_publication_target(self.config, self.commit)


if __name__ == "__main__":
    unittest.main()
