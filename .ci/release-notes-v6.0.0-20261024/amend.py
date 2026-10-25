#!/usr/bin/env python3
"""Amend only the body of one already-published, reviewed release."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
REPOSITORY = "NRG-Wardog/sidestore-auto-refresh"
RELEASE_ID = 423006285
TAG = "v6.0.0"
TARGET = "c25dd39e6e06c41523a36d2e4aa34fe747d82db7"
BRANCH = "refs/heads/main"
NOTES = "RELEASE_NOTES_v6.0.0-amended.md"
OLD_BODY_SHA256 = "d1ce7cc6780c0e3168848ebd4e30b1adf53fdc7f8d9ea91427876709cfcd1e88"
ASSETS = {
    "LiveContainer-SideStore-v6.0.0.ipa": {"id": 673432376, "size": 30416579, "digest": "sha256:86db9a6bf4accf7e886d88563f1474063c85865682131a5ba0b13ed55a33f321"},
    "release-provenance.json": {"id": 673432662, "size": 5087, "digest": "sha256:0a3a7b99449baeba95fd40adfc0a3f70b782f651114d4a593d406816b01139aa"},
    "SHA256SUMS": {"id": 673432524, "size": 101, "digest": "sha256:10f9dada8ac061262796b91c552e76896974075987513ab940fee12735e1a5bb"},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reviewed_body():
    receipt = json.loads((ROOT / "amendment-receipt.json").read_text())
    require(receipt.get("repository") == REPOSITORY and receipt.get("release_id") == RELEASE_ID and
            receipt.get("tag") == TAG and receipt.get("target_commit") == TARGET and
            receipt.get("operation") == "update-release-body-only", "Wrong reviewed amendment scope")
    require(receipt.get("old_body_sha256") == OLD_BODY_SHA256, "Wrong original release body binding")
    require(re.fullmatch(r"[0-9a-f]{64}", receipt.get("new_body_sha256", "")), "Missing amended body digest")
    path = ROOT / NOTES
    require(path.is_file() and not path.is_symlink(), "Amended notes missing or linked")
    data = path.read_bytes()
    require(digest(data) == receipt["new_body_sha256"], "Amended notes changed after review")
    require(receipt["new_body_sha256"] != OLD_BODY_SHA256, "Amendment does not change the notes")
    return data.decode("utf-8"), receipt


def api(endpoint, payload=None):
    time.sleep(3)
    args = ["gh", "api", endpoint]
    if payload is None:
        result = subprocess.run(args, capture_output=True, text=True)
    else:
        # The sole mutation is a PATCH whose only JSON member is body.
        require(endpoint == f"repos/{REPOSITORY}/releases/{RELEASE_ID}" and set(payload) == {"body"}, "Unapproved API mutation")
        with tempfile.TemporaryDirectory() as temp:
            request = Path(temp) / "body.json"
            request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([*args, "--method", "PATCH", "--input", str(request)], capture_output=True, text=True)
    require(result.returncode == 0, "GitHub operation failed; do not retry an uncertain PATCH: " + result.stderr.strip())
    return json.loads(result.stdout)


def validate_release(release, body_sha256):
    require(release.get("id") == RELEASE_ID and release.get("tag_name") == TAG and release.get("target_commitish") == TARGET,
            "Published release identity or target changed")
    require(release.get("name") == "Unified LiveContainer + SideStore 6.0" and release.get("draft") is False and
            release.get("prerelease") is False, "Published release state changed")
    require(isinstance(release.get("body"), str) and digest(release["body"].encode("utf-8")) == body_sha256,
            "Release body hash precondition failed")
    assets = release.get("assets", [])
    names = [item.get("name") for item in assets]
    require(len(names) == len(ASSETS) and set(names) == set(ASSETS), "Release asset inventory changed")
    for asset in assets:
        require(asset.get("state") == "uploaded", "Release asset is not fully uploaded")
        require(all(asset.get(key) == value for key, value in ASSETS[asset["name"]].items()), "Release asset identity changed")


def unchanged_metadata(release):
    value = copy.deepcopy(release)
    value.pop("body", None)
    value.pop("updated_at", None)  # A body edit necessarily updates this server timestamp.
    for asset in value.get("assets", []):
        asset.pop("download_count", None)  # Concurrent downloads are not amendment mutations.
    value["assets"] = sorted(value.get("assets", []), key=lambda item: item["id"])
    return value


def verify_tag_and_latest():
    tag = api(f"repos/{REPOSITORY}/git/ref/tags/{TAG}")
    require(tag.get("object", {}).get("type") == "commit" and tag["object"]["sha"] == TARGET, "Release tag target changed")
    require(api(f"repos/{REPOSITORY}/releases/latest").get("id") == RELEASE_ID, "Release latest status changed")


def record_result(receipt, status):
    result = {"operation": "update-release-body-only", "release_id": RELEASE_ID,
              "old_body_sha256": OLD_BODY_SHA256, "new_body_sha256": receipt["new_body_sha256"],
              "tag_target_preserved": TARGET, "asset_identities_preserved": True,
              "other_metadata_preserved": True, "status": status}
    Path("amendment-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


def amend():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY and os.environ.get("GITHUB_REF") == BRANCH and
            os.environ.get("GITHUB_EVENT_NAME") == "push", "Wrong amendment execution context")
    new_body, receipt = reviewed_body()
    endpoint = f"repos/{REPOSITORY}/releases/{RELEASE_ID}"
    before = api(endpoint)
    if isinstance(before.get("body"), str) and digest(before["body"].encode("utf-8")) == receipt["new_body_sha256"]:
        # Identical later runs are read-only: validate identities before returning success.
        validate_release(before, receipt["new_body_sha256"])
        verify_tag_and_latest()
        current = api(endpoint)
        validate_release(current, receipt["new_body_sha256"])
        require(unchanged_metadata(current) == unchanged_metadata(before), "Release metadata changed during idempotent verification")
        record_result(receipt, "verified-already-applied")
        return
    validate_release(before, OLD_BODY_SHA256)
    verify_tag_and_latest()
    # Re-read immediately before mutation: another editor's body change must fail closed.
    current = api(endpoint)
    validate_release(current, OLD_BODY_SHA256)
    require(unchanged_metadata(current) == unchanged_metadata(before), "Release metadata changed before amendment")
    patched = api(endpoint, {"body": new_body})
    validate_release(patched, receipt["new_body_sha256"])
    after = api(endpoint)
    validate_release(after, receipt["new_body_sha256"])
    require(unchanged_metadata(after) == unchanged_metadata(before), "Non-body release metadata changed")
    verify_tag_and_latest()
    record_result(receipt, "verified-applied")


if __name__ == "__main__":
    amend()
