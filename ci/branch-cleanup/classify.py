#!/usr/bin/env python3
"""Read-only ancestry classification. Never creates or populates a deletion allowlist."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from contextlib import contextmanager
import re
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent

REPOS = {"NRG-Wardog/" + name for name in (
    "sidestore-auto-refresh", "LiveContainer", "SideStore", "SideSign",
    "AnisetteKit", "idevice", "jktcp", "minimuxer")}

CONTROL_FILES = {".github/workflows/reviewed-branch-cleanup.yml", ".github/workflows/reviewed-branch-classify.yml"} | {
    ".ci/branch-cleanup/" + name for name in
    ("cleanup.py", "allowlist.json", "test_cleanup.py", "classify.py", "inventory.json", "test_classify.py")}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "Invalid commit SHA")
    return value

def branch(value):
    require(isinstance(value, str) and value and not value.startswith("-"), "Invalid branch")
    p = subprocess.run(["git", "check-ref-format", "refs/heads/" + value], capture_output=True)
    require(p.returncode == 0, "Invalid branch name")
    return value

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected API redirect; refusing to forward credentials")

class GitHub:
    """GET-only API client; the only mutation is the separately leased Git push."""
    def __init__(self, token):
        require(bool(token), "Ephemeral job token is missing")
        self.token = token

    def get(self, path, absent=False):
        require(path.startswith("repos/") and ".." not in path, "Invalid API route")
        time.sleep(3.1)
        req = Request("https://api.github.com/" + path, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "reviewed-branch-cleanup"})
        try:
            with build_opener(NoRedirect()).open(req, timeout=60) as response:
                return json.load(response)
        except HTTPError as exc:
            if absent and exc.code == 404:
                return None
            raise ValueError(f"GitHub GET failed ({exc.code}): {path}") from None

    def collection(self, path, key=None):
        result = []
        for page in range(1, 101):
            data = self.get(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            entries = data[key] if key else data
            require(isinstance(entries, list), "Invalid collection response")
            result.extend(entries)
            if len(entries) < 100:
                return result
        raise ValueError("Incomplete collection; refusing cleanup")

@contextmanager
def fetch_refs(repo, expected):
    """Fetch only exact reviewed source refs into temporary local refs, never remote writes."""
    with tempfile.TemporaryDirectory(prefix="cleanup-proof-") as folder:
        subprocess.run(["git", "init", "--bare", folder], check=True, capture_output=True)
        askpass = Path(folder) / "askpass"
        askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" x-access-token;; *Password*) printf "%s\\n" "$GITHUB_TOKEN";; *) exit 1;; esac\n')
        askpass.chmod(0o700)
        env = dict(os.environ, GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT="0")
        mappings = [(source, "refs/cleanup-review/" + str(i), value) for i, (source, value) in enumerate(expected.items())]
        time.sleep(3.1)
        result = subprocess.run(["git", "-c", "credential.helper=", "fetch", "--atomic", "--no-tags", "--no-write-fetch-head",
                                 "https://github.com/" + repo + ".git",
                                 *(source + ":" + target for source, target, _ in mappings)],
                                cwd=folder, env=env, capture_output=True, text=True, timeout=900)
        require(result.returncode == 0, "Exact-ref ancestry fetch failed")
        for source, target, value in mappings:
            if isinstance(value, dict):
                obj = subprocess.run(["git", "rev-parse", target], cwd=folder, capture_output=True, text=True)
                require(obj.returncode == 0 and obj.stdout.strip() == value["object_sha"], "Tag object changed: " + source)
            result = subprocess.run(["git", "rev-parse", target + "^{commit}"], cwd=folder, capture_output=True, text=True)
            expected_commit = value.get("commit_sha") if isinstance(value, dict) else value
            require(result.returncode == 0, "Retained ref cannot be peeled to a commit: " + source)
            sha(result.stdout.strip())
            require(expected_commit is None or result.stdout.strip() == expected_commit, "Ref changed during fetch: " + source)
        yield folder


def git(folder, *args, env=None, ok=(0,)):
    result = subprocess.run(["git", *args], cwd=folder, env=env, capture_output=True, text=True, timeout=900)
    require(result.returncode in ok, "Local Git operation failed: " + args[0])
    return result


def ancestors(folder, head, anchors):
    for anchor in anchors:
        target = anchor["sha"]
        if git(folder, "merge-base", "--is-ancestor", head, target, ok=(0, 1)).returncode == 0:
            ahead = int(git(folder, "rev-list", "--count", head + ".." + target).stdout.strip())
            return {"retained_anchor_ref": anchor["ref"], "retained_anchor_sha": target,
                    "retained_anchor_object_sha": anchor.get("object_sha", target),
                    "ancestry": {"base_sha": head, "head_sha": target,
                                 "status": "identical" if head == target else "ahead",
                                 "ahead_by": ahead, "behind_by": 0, "merge_base_sha": head}}
    return None


def classify(folder, config):
    rows = []
    for item in config["branches"]:
        if item["name"] == config["actual_default_branch"]:
            continue
        proof = ancestors(folder, item["sha"], config["retained_anchors"])
        row = {"repository_full_name": config["repository_full_name"], "branch_name": item["name"],
               "expected_branch_sha": item["sha"], "protected": item["protected"],
               "classification": "retained" if proof else "unique_or_not_proven_retained",
               "inventory_candidate": item.get("inventory_candidate", False),
               "exclusion_reasons": item.get("exclusion_reasons", []),
               "deletion_authorized_by_this_report": False}
        if proof:
            row.update(proof)
        rows.append(row)
    return {"repository": config["repository_full_name"], "actual_default_branch": config["actual_default_branch"],
            "audited_default_sha": config["audited_default_sha"], "branches": rows,
            "retained_anchors": config["retained_anchors"],
            "counts": {"nondefault": len(rows), "retained": sum(r["classification"] == "retained" for r in rows),
                       "unique_or_not_proven_retained": sum(r["classification"] != "retained" for r in rows),
                       "inventory_candidates": sum(r["inventory_candidate"] for r in rows),
                       "retained_inventory_candidates": sum(r["inventory_candidate"] and r["classification"] == "retained" for r in rows)},
            "notice": "Ancestry only. Coordinator must combine PR, protection, release and dependency exclusions, review exact candidates, and publish a separate immutable deletion allowlist. No remote refs changed."}


def classify_repository(api, config, workflow_repo, workflow_sha):
    repo = config["repository_full_name"]
    default = branch(config["actual_default_branch"])
    require(repo in REPOS, "Repository is outside reviewed scope")
    sha(config["audited_default_sha"])
    metadata = api.get(f"repos/{repo}")
    require(metadata.get("full_name") == repo and metadata.get("default_branch") == default, "Actual default changed")
    require(0 < len(config["branches"]) <= 500, "Invalid bounded branch inventory")
    require(0 < len(config["retained_anchors"]) <= 1000, "Invalid bounded retained inventory")
    refs = {}
    for item in config["branches"]:
        name = branch(item["name"])
        require(type(item["protected"]) is bool, "Unknown branch protection")
        require("refs/heads/" + name not in refs, "Duplicate branch")
        refs["refs/heads/" + name] = sha(item["sha"])
    require(refs.get("refs/heads/" + default) == config["audited_default_sha"], "Default inventory mismatch")
    for anchor in config["retained_anchors"]:
        ref = anchor["ref"]
        require(ref == "refs/heads/" + default or ref.startswith("refs/tags/"), "Unretained anchor")
        require(subprocess.run(["git", "check-ref-format", ref], capture_output=True).returncode == 0, "Bad anchor ref")
        if ref.startswith("refs/tags/"):
            expected = {"object_sha": sha(anchor["object_sha"]), "commit_sha": anchor.get("sha")}
            if expected["commit_sha"] is not None:
                sha(expected["commit_sha"])
        else:
            expected = sha(anchor["sha"])
        require(ref not in refs or refs[ref] == expected, "Conflicting anchor SHA")
        refs[ref] = expected
    require(any(a["ref"] == "refs/heads/" + default for a in config["retained_anchors"]), "Missing retained default")
    commit = workflow_sha if repo == workflow_repo else config["audited_default_sha"]
    refs["refs/heads/" + default] = commit
    with fetch_refs(repo, refs) as folder:
        for anchor in config["retained_anchors"]:
            if anchor["ref"].startswith("refs/tags/"):
                local_ref = "refs/cleanup-review/" + str(list(refs).index(anchor["ref"]))
                anchor["sha"] = sha(git(folder, "rev-parse", local_ref + "^{commit}").stdout.strip())
        require(git(folder, "merge-base", "--is-ancestor", config["audited_default_sha"], commit, ok=(0, 1)).returncode == 0,
                "Audited default is no longer retained")
        changed = set(filter(None, git(folder, "diff", "--name-only", config["audited_default_sha"], commit).stdout.splitlines()))
        require(changed <= CONTROL_FILES, "Default changed outside reviewed cleanup files")
        return classify(folder, config)


def main():
    raw = (ROOT / "inventory.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == os.environ.get("REVIEWED_INVENTORY_SHA256"), "Inventory hash is not reviewed")
    inventory = json.loads(raw)
    repo = inventory["workflow_repository_full_name"]
    default = branch(inventory["workflow_actual_default_branch"])
    commit = sha(os.environ.get("GITHUB_SHA"))
    require(repo == "NRG-Wardog/sidestore-auto-refresh" and os.environ.get("GITHUB_REPOSITORY") == repo,
            "Aggregate classifier runs only in reviewed SAR repository")
    require(os.environ.get("GITHUB_EVENT_NAME") == "push" and
            os.environ.get("GITHUB_REF") == "refs/heads/" + default, "Use exact inventory update on actual default")
    configs = inventory["repositories"]
    require(len(configs) == len(REPOS) and {r["repository_full_name"] for r in configs} == REPOS,
            "Expected complete exact eight-repository inventory")
    own = next(r for r in configs if r["repository_full_name"] == repo)
    require(own["actual_default_branch"] == default and
            own["audited_default_sha"] == inventory["workflow_audited_default_sha"], "Workflow baseline mismatch")
    api = GitHub(os.environ.get("GITHUB_TOKEN"))
    report = {"inventory_sha256": digest, "workflow_sha": commit, "repositories": [],
              "notice": "Ancestry only. Coordinator review and a separate exact per-repository deletion allowlist are required. No remote refs changed."}
    for config in configs:
        result = classify_repository(api, config, repo, commit)
        report["repositories"].append(result)
        print(json.dumps({"repository": result["repository"], "counts": result["counts"]}), flush=True)
    report["counts"] = {key: sum(r["counts"][key] for r in report["repositories"])
                        for key in ("nondefault", "retained", "unique_or_not_proven_retained", "inventory_candidates", "retained_inventory_candidates")}
    Path("branch-classification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("Ancestry-only classification: " + json.dumps(report["counts"]) + "\n\n" + report["notice"] + "\n")


if __name__ == "__main__":
    main()
