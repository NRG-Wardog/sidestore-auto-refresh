#!/usr/bin/env python3
"""Delete only reviewed, recoverable branch refs, with atomic expected-head leases."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
REPOS = {"NRG-Wardog/" + name for name in (
    "sidestore-auto-refresh", "LiveContainer", "SideStore", "SideSign",
    "AnisetteKit", "idevice", "jktcp", "minimuxer")}
CONTROL_FILES = {".github/workflows/reviewed-branch-cleanup.yml", ".github/workflows/reviewed-branch-classify.yml"} | {
    ".ci/branch-cleanup/" + name for name in
    ("cleanup.py", "allowlist.json", "test_cleanup.py", "classify.py", "inventory.json")}
RUNTIME_CHECKS = {"actual_default", "branch_heads", "retained_ancestry", "protected_branches",
                  "open_pull_requests", "active_workflow_runs", "release_targets"}


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


def validate_manifest(m, env):
    repo = m["repository_full_name"]
    require(repo in REPOS and env["GITHUB_REPOSITORY"] == repo, "Wrong repository")
    require(env["GITHUB_EVENT_NAME"] == "push", "Exact reviewed allowlist push only")
    default = branch(m["actual_default_branch"])
    require(env["GITHUB_REF"] == "refs/heads/" + default, "Run only on the reviewed actual default")
    sha(env["GITHUB_SHA"])
    sha(m["audited_default_sha"])
    require(re.fullmatch(r"[0-9a-f]{64}", m["reviewed_inventory_sha256"]), "Unreviewed inventory")
    require(m["source_review_complete"] is True, "Incomplete source/dependency review")
    require(set(m["required_runtime_checks"]) == RUNTIME_CHECKS, "Missing mandatory runtime checks")
    require(repo in m["pr_repositories"], "Missing local PR audit")
    for name in m["pr_repositories"]:
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name), "Invalid PR repository")
    candidates = m["candidates"]
    require(isinstance(candidates, list) and 0 < len(candidates) <= 500, "Expected 1–500 reviewed candidates")
    names = set()
    for c in candidates:
        name = branch(c["branch_name"])
        require(name not in names and name != default, "Duplicate/default candidate")
        names.add(name)
        require(c["repository_full_name"] == repo and c["actual_default_branch"] == default,
                "Candidate scope mismatch")
        require(name not in m["preserved_branch_names"], "Excluded branch")
        require(c["disposition"] == "eligible" and c["protected"] is False, "Ineligible candidate")
        require(c["functional_dependency_checks_complete"] is True, "Incomplete functional dependency review")
        head = sha(c["expected_branch_sha"])
        anchor = c["retained_anchor_ref"]
        require(anchor == "refs/heads/" + default or anchor.startswith("refs/tags/"), "Anchor must be retained default or existing tag")
        require(subprocess.run(["git", "check-ref-format", anchor], capture_output=True).returncode == 0,
                "Invalid retained ref")
        sha(c["retained_anchor_sha"])
        if anchor.startswith("refs/tags/"):
            sha(c["retained_anchor_object_sha"])
        a = c["ancestry"]
        require(a["base_sha"] == head and a["head_sha"] == c["retained_anchor_sha"] and
                a["status"] in ("ahead", "identical") and a["behind_by"] == 0 and
                a["merge_base_sha"] == head, "Missing retained ancestry proof")
    for snap in m["dependency_snapshots"]:
        require(snap["repository_full_name"] in REPOS and snap["ref"].startswith("refs/heads/"), "Bad dependency snapshot")
        require(snap["kind"] in ("actual_default", "immutable_branch"), "Unknown dependency snapshot role")
        branch(snap["ref"][11:])
        sha(snap["expected_sha"])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected API redirect; refusing to forward credentials")


def validate_route(path):
    require(isinstance(path, str) and all(ord(char) >= 32 and ord(char) != 127 for char in path), "Invalid API route")
    parsed = urlsplit(path)
    require(not parsed.scheme and not parsed.netloc and not parsed.fragment and "\\" not in path,
            "Invalid API route")
    raw = parsed.path.split("/")
    require(all(part and not re.search(r"%(?![0-9A-Fa-f]{2})", part) for part in raw), "Invalid API route")
    parts = [unquote(part, errors="strict") for part in raw]
    require(all(part not in (".", "..") and not any(char in part for char in ("/", "\\", "%")) for part in parts),
            "Invalid API route")
    require(len(parts) >= 3 and parts[0] == "repos" and
            raw[:3] == parts[:3] and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts[1:3]), "Invalid API route")
    tail = parts[3:]
    required_query = {}
    if not tail:
        pass
    elif len(tail) == 2 and tail[0] == "compare":
        require(re.fullmatch(r"[0-9a-f]{40}\.\.\.[0-9a-f]{40}", tail[1]), "Invalid API comparison")
    elif len(tail) == 3 and tail[:2] in (["git", "trees"], ["git", "tags"]):
        sha(tail[2])
        if tail[1] == "trees": required_query = {"recursive": "1"}
    elif len(tail) >= 4 and tail[:2] == ["git", "ref"] and tail[2] in ("heads", "tags"):
        require(subprocess.run(["git", "check-ref-format", "refs/" + "/".join(tail[2:])], capture_output=True).returncode == 0,
                "Invalid API ref")
    elif tail in (["branches"], ["releases"], ["pulls"], ["actions", "runs"]):
        required_query = {"per_page": "100", "page": None}
        if tail == ["pulls"]: required_query["state"] = "open"
        if tail == ["actions", "runs"]: required_query["status"] = None
    else:
        raise ValueError("Invalid API route")
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    query = dict(pairs)
    require("%" not in parsed.query and len(pairs) == len(query) and set(query) == set(required_query), "Invalid API query")
    for key, value in required_query.items():
        if value is not None: require(query[key] == value, "Invalid API query")
    if "page" in query: require(re.fullmatch(r"[1-9][0-9]*", query["page"]) and int(query["page"]) <= 100, "Invalid API page")
    if "status" in query: require(query["status"] in ("queued", "in_progress", "waiting", "pending", "requested"), "Invalid API run status")


class GitHub:
    """GET-only API client; the only mutation is the separately leased Git push."""
    def __init__(self, token):
        require(bool(token), "Ephemeral job token is missing")
        self.token = token

    def get(self, path, absent=False):
        validate_route(path)
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


def ref(api, repo, name, absent=False):
    result = api.get(f"repos/{repo}/git/ref/{quote(name[5:], safe='/')}", absent=absent)
    if result is None:
        return None
    require(isinstance(result, dict) and result.get("ref") == name, "Non-exact ref response")
    obj = result["object"]
    # Peel annotated tags; the reviewed anchor SHA denotes the retained commit.
    for _ in range(10):
        if obj["type"] == "commit":
            return sha(obj["sha"])
        require(name.startswith("refs/tags/") and obj["type"] == "tag", "Ref is not a commit")
        obj = api.get(f"repos/{repo}/git/tags/{sha(obj['sha'])}")["object"]
    raise ValueError("Too many annotated-tag indirections")


def proves_ancestry(api, repo, base, head):
    value = api.get(f"repos/{repo}/compare/{base}...{head}")
    require(value.get("status") in ("ahead", "identical") and value.get("behind_by") == 0 and
            value.get("base_commit", {}).get("sha") == base and
            value.get("merge_base_commit", {}).get("sha") == base, "Unretained unique work")


def tree(api, repo, commit):
    value = api.get(f"repos/{repo}/git/trees/{commit}?recursive=1")
    require(value.get("truncated") is False, "Truncated dependency tree")
    return {v["path"]: (v["mode"], v["type"], v["sha"]) for v in value["tree"] if v["type"] != "tree"}


def verify_default(api, m, commit):
    repo, default = m["repository_full_name"], m["actual_default_branch"]
    metadata = api.get(f"repos/{repo}")
    require(metadata["full_name"] == repo and metadata["default_branch"] == default, "Actual default changed")
    require(ref(api, repo, "refs/heads/" + default) == commit, "Default moved since this workflow commit")
    if metadata.get("fork"):
        require(metadata.get("parent", {}).get("full_name") in m["pr_repositories"], "Missing upstream PR check")
    original = m["audited_default_sha"]
    proves_ancestry(api, repo, original, commit)
    old, new = tree(api, repo, original), tree(api, repo, commit)
    changes = {p for p in old.keys() | new.keys() if old.get(p) != new.get(p)}
    require(changes <= CONTROL_FILES, "Dependency audit is stale: non-cleanup files changed")


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


def local_ancestor(folder, base, head):
    result = subprocess.run(["git", "merge-base", "--is-ancestor", base, head], cwd=folder, capture_output=True)
    require(result.returncode == 0, "Unique work or incomplete local retained ancestry")


def anchor_expectation(candidate, workflow_sha):
    if candidate["retained_anchor_ref"].startswith("refs/heads/"):
        return workflow_sha
    return {"object_sha": candidate["retained_anchor_object_sha"], "commit_sha": candidate["retained_anchor_sha"]}


def anchor_commit(value):
    return value["commit_sha"] if isinstance(value, dict) else value


def verify_dependency_snapshot(api, snapshot):
    repo, refname, original = snapshot["repository_full_name"], snapshot["ref"], snapshot["expected_sha"]
    if snapshot["kind"] == "actual_default":
        require(api.get(f"repos/{repo}")["default_branch"] == refname[11:], "Dependency default changed")
    current = ref(api, repo, refname)
    if current != original:
        require(snapshot["kind"] == "actual_default", "Immutable dependency branch moved")
        proves_ancestry(api, repo, original, current)
        old, new = tree(api, repo, original), tree(api, repo, current)
        changed = {p for p in old.keys() | new.keys() if old.get(p) != new.get(p)}
        require(changed <= CONTROL_FILES, "Dependency source changed outside cleanup control files")


def preflight(api, m, commit):
    repo = m["repository_full_name"]
    verify_default(api, m, commit)
    for snap in m["dependency_snapshots"]:
        if (snap["repository_full_name"], snap["ref"]) == (repo, "refs/heads/" + m["actual_default_branch"]):
            require(snap["expected_sha"] == m["audited_default_sha"], "Own dependency snapshot mismatch")
        else:
            verify_dependency_snapshot(api, snap)
    names = {c["branch_name"] for c in m["candidates"]}
    for destination in m["pr_repositories"]:
        for pr in api.collection(f"repos/{destination}/pulls?state=open"):
            for side in ("head", "base"):
                item = pr.get(side, {})
                require(not (item.get("repo", {}).get("full_name") == repo and item.get("ref") in names),
                        "Open PR depends on a candidate")
    for release in api.collection(f"repos/{repo}/releases"):
        require(release.get("target_commitish") not in names, "Release targets a candidate")
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        for run in api.collection(f"repos/{repo}/actions/runs?status={status}", "workflow_runs"):
            require(run.get("head_branch") not in names, "Active workflow uses a candidate")
    branches = {v["name"]: v for v in api.collection(f"repos/{repo}/branches")}
    expected = {}
    for c in m["candidates"]:
        value = branches.get(c["branch_name"], {})
        require(value.get("protected") is False, "Candidate is protected or missing")
        require(value.get("commit", {}).get("sha") == c["expected_branch_sha"], "Candidate head changed")
        expected["refs/heads/" + c["branch_name"]] = c["expected_branch_sha"]
        anchor = c["retained_anchor_ref"]
        target = anchor_expectation(c, commit)
        require(anchor not in expected or expected[anchor] == target, "Conflicting retained anchor")
        expected[anchor] = target
    with fetch_refs(repo, expected) as folder:
        for c in m["candidates"]:
            anchor = anchor_commit(expected[c["retained_anchor_ref"]])
            local_ancestor(folder, c["retained_anchor_sha"], anchor)
            local_ancestor(folder, c["expected_branch_sha"], anchor)


def push_command(m, dry_run):
    candidates = m["candidates"]
    return ["git", "-c", "credential.helper=", "push", "--porcelain", "--atomic",
            *( ["--dry-run"] if dry_run else []),
            *("--force-with-lease=refs/heads/" + c["branch_name"] + ":" + c["expected_branch_sha"] for c in candidates),
            "https://github.com/" + m["repository_full_name"] + ".git",
            *(":refs/heads/" + c["branch_name"] for c in candidates)]


def git_delete(m, dry_run):
    # Askpass contains no secret. Token is inherited from the ephemeral job environment.
    with tempfile.TemporaryDirectory(prefix="reviewed-cleanup-") as folder:
        askpass = Path(folder) / "askpass"
        askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" x-access-token;; *Password*) printf "%s\\n" "$GITHUB_TOKEN";; *) exit 1;; esac\n')
        askpass.chmod(0o700)
        env = dict(os.environ, GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT="0")
        time.sleep(3.1)
        # One Git operation; its internal smart-HTTP exchanges are controlled by Git.
        result = subprocess.run(push_command(m, dry_run), env=env, capture_output=True, text=True, timeout=180)
        return result.returncode, result.stdout, result.stderr


def readback(api, m, commit):
    repo = m["repository_full_name"]
    verify_default(api, m, commit)
    branches = {v["name"]: v["commit"]["sha"] for v in api.collection(f"repos/{repo}/branches")}
    results, anchors = [], {}
    for c in m["candidates"]:
        head = branches.get(c["branch_name"])
        results.append({"branch": c["branch_name"], "expected_sha": c["expected_branch_sha"], "observed_sha": head})
        anchor = c["retained_anchor_ref"]
        anchors[anchor] = anchor_expectation(c, commit)
    with fetch_refs(repo, anchors) as folder:
        for c in m["candidates"]:
            local_ancestor(folder, c["expected_branch_sha"], anchor_commit(anchors[c["retained_anchor_ref"]]))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("verify", "apply"))
    args = parser.parse_args()
    raw = (ROOT / "allowlist.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == os.environ.get("REVIEWED_MANIFEST_SHA256"), "Manifest hash is not reviewed")
    m = json.loads(raw)
    validate_manifest(m, os.environ)
    api = GitHub(os.environ.get("GITHUB_TOKEN"))
    commit = os.environ["GITHUB_SHA"]
    preflight(api, m, commit)
    report = {"mode": args.mode, "repository": m["repository_full_name"], "workflow_sha": commit,
              "manifest_sha256": digest, "inventory_sha256": m["reviewed_inventory_sha256"], "validated": True}
    if args.mode == "apply":
        code, _, _ = git_delete(m, True)
        require(code == 0, "Leased atomic deletion dry-run failed; nothing deleted")
        # Recheck mutable exclusions and retained ancestry after the transport dry-run.
        preflight(api, m, commit)
        error = None
        try:
            code, out, err = git_delete(m, False)
            report["git_exit_code"] = code
            report["git_output"] = out + err
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = type(exc).__name__
            report["git_uncertain_error"] = error
        # Never retry mutation, even on timeout or nonzero exit; observe exact refs first.
        report["readback"] = readback(api, m, commit)
        report["deleted"] = all(row["observed_sha"] is None for row in report["readback"])
        print(json.dumps(report, indent=2), flush=True)
        require(report["deleted"], "Deletion incomplete; inspect readback, do not blindly retry")
    else:
        print(json.dumps(report, indent=2), flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("```json\n" + json.dumps(report, indent=2) + "\n```\n")


if __name__ == "__main__":
    main()
