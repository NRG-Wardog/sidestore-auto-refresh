#!/usr/bin/env python3
"""Promote reviewed bytes only. Never rebuild, repackage, sign, or execute an IPA."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parent
DESTINATION = "NRG-Wardog/sidestore-auto-refresh"
SOURCE = "NRG-Wardog/LiveContainer"
TAG = "v6.0.0"
BRANCH = "refs/heads/validation/publication-v6.0.0"
IPA = "LiveContainer-SideStore-v6.0.0.ipa"
NOTES = "RELEASE_NOTES_v6.0.0.md"
PUBLIC_ASSETS = (IPA, "SHA256SUMS", "release-provenance.json")
INVENTORY = "publication-inventory.json"
SOURCE_IPA = "LiveContainer-SideStore-6.0-refresh-diagnostics-experimental.ipa"
SOURCE_FILES = {SOURCE_IPA, SOURCE_IPA[:-4] + ".verification.json", "SHA256SUMS", "build-label.json", "size-bytes.txt"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def settings():
    value = json.loads((ROOT / "reviewed-inputs.json").read_text())
    require(value.get("publication_target") == "c25dd39e6e06c41523a36d2e4aa34fe747d82db7", "Wrong reviewed publication target")
    require(value.get("reviewed_draft_id") == 423006285, "Wrong reviewed draft identity")
    require(value["approved_public_files"] == ["SHA256SUMS", NOTES, "release-provenance.json"], "Wrong reviewed public-file allowlist")
    for name in value["approved_public_files"]:
        require(re.fullmatch(r"[A-Za-z0-9_.-]+", name), "Unsafe public filename")
        path = ROOT / name
        require(path.is_file() and not path.is_symlink(), "Reviewed public file missing or linked")
        require(digest(path.read_bytes()) == value["public_file_sha256"][name], "Reviewed public file digest changed: " + name)
    return value


def context():
    require(os.environ.get("GITHUB_REPOSITORY") == DESTINATION, "Wrong destination repository")
    require(os.environ.get("GITHUB_REF") == BRANCH, "Wrong publication branch")
    require(os.environ.get("GITHUB_EVENT_NAME") == "push", "Only the exact branch push is admitted")
    commit = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "Invalid publication commit")
    return commit


def gh(*args):
    time.sleep(3)
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    require(result.returncode == 0, "GitHub operation failed: " + " ".join(args[:3]) + "; " + result.stderr.strip())
    return result.stdout


def github_json(endpoint, optional=False):
    time.sleep(3)
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
    if result.returncode and optional and "HTTP 404" in result.stderr:
        return None
    require(result.returncode == 0, "GitHub read failed: " + endpoint + "; " + result.stderr.strip())
    return json.loads(result.stdout)


def verify_publication_target(config, commit):
    target = config["publication_target"]
    comparison = github_json(f"repos/{DESTINATION}/compare/{target}...{commit}")
    require(comparison.get("status") in ("ahead", "identical") and
            comparison.get("base_commit", {}).get("sha") == target and
            comparison.get("merge_base_commit", {}).get("sha") == target,
            "Reviewed release target is not an ancestor of this retry")
    path = ".ci/release-v6.0.0/reviewed-inputs.json"
    original = github_json(f"repos/{DESTINATION}/contents/{path}?ref={target}")
    require(original.get("type") == "file" and original.get("path") == path and original.get("encoding") == "base64",
            "Original reviewed inputs are unavailable")
    bound = json.loads(base64.b64decode(original["content"], validate=False))
    for key in ("native", "package", "artifact", "ipa_size", "ipa_sha256", "approved_public_files", "public_file_sha256"):
        require(bound.get(key) == config[key], "Retry changed original reviewed publication inputs: " + key)


def lookup_reviewed_draft(config):
    # The tag endpoint does not expose this draft. Search authenticated releases,
    # require one exact match, then read the pinned ID. Never create a replacement.
    matches = []
    for page in range(1, 11):
        releases = github_json(f"repos/{DESTINATION}/releases?per_page=100&page={page}")
        require(isinstance(releases, list), "Invalid authenticated releases collection")
        matches.extend(item for item in releases if item.get("tag_name") == TAG)
        if len(releases) < 100:
            break
    else:
        raise ValueError("Release lookup exceeded its bounded collection scan")
    require(len(matches) == 1, "Expected exactly one existing reviewed release; refusing to create another")
    require(matches[0].get("id") == config["reviewed_draft_id"], "Release collection matched an unreviewed ID")
    release = github_json(f"repos/{DESTINATION}/releases/{config['reviewed_draft_id']}")
    require(release.get("id") == config["reviewed_draft_id"] and release.get("tag_name") == TAG, "Reviewed release ID changed")
    return release


def verify_run(value, expected):
    require(value.get("id") == expected["id"], "Wrong run identity")
    require(value.get("repository", {}).get("full_name") == SOURCE, "Wrong run repository")
    require(value.get("status") == "completed" and value.get("conclusion") == "success", "Matching run has not passed")
    for key in ("head_sha", "head_branch", "workflow_id", "path", "run_attempt"):
        require(value.get(key) == expected[key], "Wrong run " + key)
    require(value.get("head_commit", {}).get("tree_id") == expected["tree_id"], "Wrong run tree")


def verify_artifact(value, config):
    expected = config["artifact"]
    for key in ("id", "name", "size_in_bytes", "digest"):
        require(value.get(key) == expected[key], "Wrong artifact " + key)
    require(value.get("expired") is False, "Artifact expired or expiration state unknown")
    run = value.get("workflow_run", {})
    require(run.get("id") == config["package"]["id"], "Artifact belongs to another run")
    require(run.get("head_sha") == config["package"]["head_sha"], "Artifact belongs to another commit")
    require(run.get("head_branch") == config["package"]["head_branch"], "Artifact belongs to another branch")
    require(run.get("repository_id") == 1366314274 and run.get("head_repository_id") == 1366314274, "Artifact repository identity mismatch")


def extract_reviewed_ipa(archive, output, config):
    raw = Path(archive).read_bytes()
    require(len(raw) == config["artifact"]["size_in_bytes"], "Artifact ZIP size mismatch")
    require("sha256:" + digest(raw) == config["artifact"]["digest"], "Artifact ZIP digest mismatch")
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        names = [item.filename for item in entries]
        require(len(entries) == len(SOURCE_FILES) and set(names) == SOURCE_FILES, "Unexpected artifact ZIP inventory")
        for item in entries:
            parts = PurePosixPath(item.filename).parts
            require(len(parts) == 1 and item.filename not in (".", "..") and "\\" not in item.filename, "Unsafe artifact path")
            require(stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG), "Non-regular artifact entry")
            require(not item.flag_bits & 1, "Encrypted artifact entry")
        require(sum(item.file_size for item in entries) < 32 * 1024 * 1024, "Oversized artifact inventory")
        ipa = zipped.read(SOURCE_IPA)
        require(len(ipa) == config["ipa_size"] and digest(ipa) == config["ipa_sha256"], "Raw IPA identity mismatch")
        label = json.loads(zipped.read("build-label.json"))
        require(label.get("product_version") == "6.0" and label.get("channel") == "experimental", "Unexpected retained product metadata")
        require(label.get("native_workflow_commit") == config["native"]["head_sha"], "Wrong retained native builder")
        require(label.get("native_compile_run") == str(config["native"]["id"]), "Wrong retained native run")
        require(label.get("native_compile_attempt") == str(config["native"]["run_attempt"]), "Wrong retained native attempt")
        require(zipped.read("size-bytes.txt").decode().strip() == str(len(ipa)), "Recorded IPA size mismatch")
        checksum = zipped.read("SHA256SUMS").decode().split()
        require(checksum == [config["ipa_sha256"], "diagnostic/" + SOURCE_IPA], "Recorded IPA checksum mismatch")
        # Write only the reviewed IPA. Never extract arbitrary archive paths or evidence.
        Path(output).write_bytes(ipa)


def make_bundle(archive, output, config, commit):
    output = Path(output)
    require(not output.exists(), "Bundle destination already exists")
    output.mkdir(parents=True)
    extract_reviewed_ipa(archive, output / IPA, config)
    for name in config["approved_public_files"]:
        shutil.copyfile(ROOT / name, output / name)
    checksum = (output / "SHA256SUMS").read_text().split()
    require(checksum == [config["ipa_sha256"], IPA], "Public checksum does not name the unchanged IPA")
    inventory = {"repository": DESTINATION, "tag": TAG, "publication_commit": commit,
                 "release_target": config["publication_target"],
                 "files": {name: {"sha256": digest((output / name).read_bytes()), "size": (output / name).stat().st_size}
                           for name in (*PUBLIC_ASSETS, NOTES)}}
    (output / INVENTORY).write_bytes(json_bytes(inventory))
    return digest((output / INVENTORY).read_bytes())


def verify_bundle(folder, expected_digest, config, commit):
    folder = Path(folder)
    require({path.name for path in folder.iterdir()} == set((*PUBLIC_ASSETS, NOTES, INVENTORY)), "Unexpected publication inventory")
    for path in folder.iterdir():
        require(path.is_file() and not path.is_symlink(), "Non-regular publication file")
    require(digest((folder / INVENTORY).read_bytes()) == expected_digest, "Publication inventory digest mismatch")
    inventory = json.loads((folder / INVENTORY).read_text())
    require(set(inventory) == {"repository", "tag", "publication_commit", "release_target", "files"}, "Unexpected inventory metadata")
    require(inventory["repository"] == DESTINATION and inventory["tag"] == TAG and inventory["publication_commit"] == commit, "Wrong publication binding")
    require(inventory["release_target"] == config["publication_target"], "Wrong reviewed release target")
    require(set(inventory["files"]) == set((*PUBLIC_ASSETS, NOTES)), "Wrong public file allowlist")
    for name, expected in inventory["files"].items():
        raw = (folder / name).read_bytes()
        require(expected == {"sha256": digest(raw), "size": len(raw)}, "Publication file identity mismatch")
    require(inventory["files"][IPA] == {"sha256": config["ipa_sha256"], "size": config["ipa_size"]}, "Wrong published IPA")
    for name in config["approved_public_files"]:
        require(inventory["files"][name]["sha256"] == config["public_file_sha256"][name], "Unreviewed public metadata")
    return inventory


def tag_commit():
    value = github_json(f"repos/{DESTINATION}/git/ref/tags/{TAG}", optional=True)
    if value is None:
        return None
    obj = value["object"]
    for _ in range(4):
        if obj["type"] == "commit":
            return obj["sha"]
        require(obj["type"] == "tag", "Unsupported release tag type")
        obj = github_json(f"repos/{DESTINATION}/git/tags/{obj['sha']}")["object"]
    raise ValueError("Excessive annotated tag depth")


def validate_release(release, inventory, notes, commit, draft, complete):
    require(release.get("tag_name") == TAG and release.get("target_commitish") == commit, "Existing release target differs")
    require(release.get("name") == "Unified LiveContainer + SideStore 6.0", "Existing release title differs")
    require(release.get("body", "").strip() == notes.strip(), "Existing release notes differ")
    require(release.get("draft") is draft and release.get("prerelease") is False, "Existing release is not the expected draft/public state")
    assets = release.get("assets", [])
    names = [asset["name"] for asset in assets]
    require(len(names) == len(set(names)) and set(names) <= set(PUBLIC_ASSETS), "Unexpected existing release assets")
    if complete:
        require(set(names) == set(PUBLIC_ASSETS), "Release assets incomplete")
    for asset in assets:
        expected = inventory["files"][asset["name"]]
        require(asset.get("state") == "uploaded" and asset.get("size") == expected["size"] and
                asset.get("digest") == "sha256:" + expected["sha256"], "Existing uploaded asset does not match reviewed bytes")
    return set(names)


def prepare(output):
    commit, config = context(), settings()
    verify_publication_target(config, commit)
    for key in ("native", "package"):
        verify_run(github_json(f"repos/{SOURCE}/actions/runs/{config[key]['id']}"), config[key])
    verify_artifact(github_json(f"repos/{SOURCE}/actions/artifacts/{config['artifact']['id']}"), config)
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "reviewed-artifact.zip"
        with archive.open("wb") as destination:
            time.sleep(3)
            subprocess.run(["gh", "api", f"repos/{SOURCE}/actions/artifacts/{config['artifact']['id']}/zip"], stdout=destination, check=True)
        manifest_digest = make_bundle(archive, output, config, commit)
    verify_bundle(output, manifest_digest, config, commit)
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        handle.write("inventory_sha256=" + manifest_digest + "\n")
    print("Verified original CI runs, artifact, unchanged IPA and reviewed publication allowlist.")


def publish(folder, expected_digest):
    commit, config = context(), settings()
    inventory = verify_bundle(folder, expected_digest, config, commit)
    notes = (Path(folder) / NOTES).read_text()
    target = config["publication_target"]
    endpoint = f"repos/{DESTINATION}/releases/{config['reviewed_draft_id']}"
    release = lookup_reviewed_draft(config)
    existing_tag = tag_commit()
    require(existing_tag in (None, target), "Existing draft tag points elsewhere")
    present = validate_release(release, inventory, notes, target, draft=True, complete=False)
    for name in PUBLIC_ASSETS:
        if name not in present:
            content_type = "application/json" if name.endswith(".json") else "application/octet-stream"
            gh("api", "--method", "POST",
               f"https://uploads.github.com/repos/{DESTINATION}/releases/{config['reviewed_draft_id']}/assets?name={name}",
               "--header", "Content-Type: " + content_type, "--input", str(Path(folder) / name))
    validate_release(github_json(endpoint), inventory, notes, target, draft=True, complete=True)
    gh("api", "--method", "PATCH", endpoint, "-F", "draft=false", "-F", "prerelease=false", "-f", "make_latest=true")
    published = github_json(endpoint)
    validate_release(published, inventory, notes, target, draft=False, complete=True)
    require(tag_commit() == target, "Published tag target mismatch")
    require(github_json(f"repos/{DESTINATION}/releases/latest")["id"] == published["id"], "6.0 is not the latest release")
    print(published["html_url"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "publish"))
    parser.add_argument("folder")
    parser.add_argument("--inventory-sha256")
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare(args.folder)
    else:
        require(re.fullmatch(r"[0-9a-f]{64}", args.inventory_sha256 or ""), "Missing verification-job inventory digest")
        publish(args.folder, args.inventory_sha256)
