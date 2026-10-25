#!/usr/bin/env python3
"""Bounded, credential-free Git evidence collection. No remote mutations or checkout."""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
SOURCE_SHA256 = "ac3e56abe8c17bb190c748e967050dc9c871484625f36c4274672dc2c13b7752"
REPOSITORIES = {"NRG-Wardog/" + name for name in (
    "sidestore-auto-refresh", "LiveContainer", "SideStore", "SideSign",
    "AnisetteKit", "idevice", "jktcp", "minimuxer")}
WORKFLOW_REPOSITORY = "NRG-Wardog/sidestore-auto-refresh"
CONTROL_FILES = frozenset({
    ".github/workflows/reviewed-branch-semantic-inventory.yml",
    ".ci/branch-semantic-review/inventory.json",
    ".ci/branch-semantic-review/inventory.py",
    ".ci/branch-semantic-review/test_inventory.py",
    ".ci/branch-semantic-review/source-receipt.json",
    ".ci/branch-semantic-review/README.md",
})
LIMITS = {
    "unique_commits_per_branch": 500,
    "default_tree_history_entries": 50000,
    "merge_bases": 8,
    "merge_parents": 8,
    "diff_bytes": 4 * 1024 * 1024,
    "commit_patch_bytes": 2 * 1024 * 1024,
    "capture_bytes": 8 * 1024 * 1024,
    "patch_artifact_bytes": 384 * 1024 * 1024,
    "repository_bytes": 3 * 1024 * 1024 * 1024,
    "local_seconds": 180,
    "remote_seconds": 900,
    "total_seconds": 7200,
    "remote_spacing_seconds": 3.1,
}
NOTICE = (
    "Evidence only. Patch equivalence ignores some differences and is not semantic, runtime, "
    "dependency, PR, release, or deletion approval. Tree equality proves identical tracked tree "
    "objects only. Missing or truncated evidence must not be treated as absence of unique work. "
    "No remote refs, tags, releases, permissions, or repository files were changed."
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def commit_sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "Invalid commit SHA")
    return value


def branch_name(value):
    require(isinstance(value, str) and value and len(value) <= 240 and not value.startswith("-"),
            "Invalid branch name")
    p = subprocess.run(["git", "check-ref-format", "refs/heads/" + value],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    require(p.returncode == 0, "Invalid branch name")
    return value


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()


def load_manifest(root=ROOT, expected_digest=None):
    raw = (root / "inventory.json").read_bytes()
    require(len(raw) <= 256 * 1024, "Manifest exceeds bound")
    require(digest(raw) == expected_digest, "Manifest SHA-256 does not match exact reviewed value")
    data = json.loads(raw)
    receipt_raw = (root / "source-receipt.json").read_bytes()
    require(digest(receipt_raw) == SOURCE_SHA256, "Source receipt SHA-256 mismatch")
    receipt = json.loads(receipt_raw)
    require(data.get("schema_version") == 1 and data.get("source_receipt_sha256") == SOURCE_SHA256,
            "Unsupported manifest or receipt binding")
    require(data.get("limits") == LIMITS, "Reviewed hard limits changed")
    require(set(data.get("control_files", [])) == CONTROL_FILES, "Control path allowlist changed")
    require(data.get("workflow_repository") == WORKFLOW_REPOSITORY and
            data.get("workflow_branch") == "main", "Wrong workflow scope")
    configs = data.get("repositories", [])
    require(len(configs) == 8 and {r["repository"] for r in configs} == REPOSITORIES,
            "Expected the exact eight repositories")
    baseline = {r["repo"]: r for r in receipt["repositories"]}
    total = 0
    for config in configs:
        repo = config["repository"]
        source = baseline[repo]
        require(config["default_branch"] == source["default_branch"] and
                config["default_sha"] == source["default_head"], "Default differs from source receipt: " + repo)
        branch_name(config["default_branch"])
        commit_sha(config["default_sha"])
        seen = set()
        actual = []
        for row in config["branches"]:
            name, sha = branch_name(row["name"]), commit_sha(row["sha"])
            require(name != config["default_branch"] and name not in seen, "Duplicate or default review branch")
            require(type(row["protected_at_receipt"]) is bool, "Missing receipt protection observation")
            seen.add(name)
            actual.append((name, sha, row["protected_at_receipt"]))
        expected = [(b["name"], b["commit"]["sha"], b["protected"]) for b in source["retained_nondefault"]]
        require(actual == expected, "Branch scope differs from exact receipt: " + repo)
        total += len(actual)
    require(total == 97 and data.get("expected_nondefault_branches") == 97, "Expected exactly 97 branches")
    require(data.get("manual_holds") == [
        {"repository": WORKFLOW_REPOSITORY, "branch": "research/cellular-refresh-feasibility-20261022",
         "reason": "Preserve open PR 49 research pending separate review."},
        {"repository": "NRG-Wardog/AnisetteKit", "branch": "migration/runtime-source-141776ba",
         "reason": "Preserve runtime-source migration dependency pending separate review."},
    ], "Required explicit preservation holds changed")
    require(set(data.get("reviewed_code_sha256", {})) == {"inventory.py", "test_inventory.py"},
            "Expected exact reviewed collector and test hashes")
    for filename, expected in data["reviewed_code_sha256"].items():
        require(digest((root / filename).read_bytes()) == expected, "Reviewed code hash mismatch: " + filename)
    return data, digest(raw)


@dataclass
class Result:
    data: bytes
    returncode: int
    truncated: bool = False
    timed_out: bool = False
    storage_exceeded: bool = False
    observed_bytes: int = 0

    @property
    def complete(self):
        return self.returncode == 0 and not (self.truncated or self.timed_out or self.storage_exceeded)


class Runtime:
    def __init__(self, work, limits=None):
        self.work = Path(work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.limits = dict(LIMITS if limits is None else limits)
        self.started = time.monotonic()
        self.last_remote_finished = time.monotonic()
        self.network_operations = []
        home = self.work / "sterile-home"
        home.mkdir(exist_ok=True)
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
            "LANG": "C", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
            "GIT_LFS_SKIP_SMUDGE": "1", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }

    def check_time(self):
        require(time.monotonic() - self.started < self.limits["total_seconds"], "Total review time limit reached")

    def capture(self, argv, cwd=None, cap=None, timeout=None, input_data=None, storage_path=None):
        self.check_time()
        cap = self.limits["capture_bytes"] if cap is None else cap
        timeout = self.limits["local_seconds"] if timeout is None else timeout
        timeout = min(timeout, self.limits["total_seconds"] - (time.monotonic() - self.started))
        output = bytearray()
        observed = 0
        truncated = timed_out = storage_exceeded = False
        with tempfile.TemporaryFile() as stdin:
            if input_data is not None:
                stdin.write(input_data)
                stdin.seek(0)
            proc = subprocess.Popen(argv, cwd=cwd, env=self.env, stdin=stdin,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            end = time.monotonic() + timeout
            last_storage_check = 0
            try:
                while selector.get_map():
                    now = time.monotonic()
                    if now > end:
                        timed_out = True
                        os.killpg(proc.pid, signal.SIGKILL)
                        break
                    if storage_path is not None and now - last_storage_check >= 1:
                        last_storage_check = now
                        size = sum(p.stat().st_size for p in Path(storage_path).rglob("*") if p.is_file())
                        if size > self.limits["repository_bytes"]:
                            storage_exceeded = True
                            os.killpg(proc.pid, signal.SIGKILL)
                            break
                    for key, _ in selector.select(timeout=min(0.2, max(0, end - now))):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        observed += len(block)
                        output.extend(block[:max(0, cap - len(output))])
                        if observed > cap:
                            truncated = True
                            os.killpg(proc.pid, signal.SIGKILL)
                            selector.unregister(key.fileobj)
                            break
                try:
                    returncode = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(proc.pid, signal.SIGKILL)
                    returncode = proc.wait(timeout=10)
            finally:
                selector.close()
                proc.stdout.close()
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        return Result(bytes(output), returncode, truncated, timed_out, storage_exceeded, observed)

    def remote(self, repo, operation, argv, cwd=None, storage_path=None):
        require(repo in REPOSITORIES, "Remote repository outside exact scope")
        self.check_time()
        if self.last_remote_finished is not None:
            wait = self.limits["remote_spacing_seconds"] - (time.monotonic() - self.last_remote_finished)
            if wait > 0:
                time.sleep(wait)
        entry = {"repository": repo, "operation": operation,
                 "started_unix": time.time(), "public_read_only": True}
        try:
            result = self.capture(argv, cwd=cwd, timeout=self.limits["remote_seconds"], storage_path=storage_path)
            entry.update({"complete": result.complete, "returncode": result.returncode,
                          "truncated": result.truncated, "timed_out": result.timed_out,
                          "storage_exceeded": result.storage_exceeded})
            return result
        finally:
            self.last_remote_finished = time.monotonic()
            entry["finished_unix"] = time.time()
            self.network_operations.append(entry)


GIT_OPTIONS = [
    "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
    "-c", "http.followRedirects=false", "-c", "submodule.recurse=false",
    "-c", "fetch.recurseSubmodules=false", "-c", "maintenance.auto=false",
    "-c", "gc.auto=0", "-c", "core.quotePath=true", "-c", "color.ui=false",
]
DIFF_OPTIONS = ["--no-ext-diff", "--no-textconv", "--no-renames", "--binary", "--full-index"]


class Git:
    def __init__(self, runtime, folder):
        self.runtime, self.folder = runtime, Path(folder)

    def run(self, *args, **kwargs):
        return self.runtime.capture(["git", *GIT_OPTIONS, *args], cwd=self.folder, **kwargs)

    def text(self, *args):
        r = self.run(*args)
        require(r.complete, "Local Git evidence incomplete: " + args[0])
        return r.data.decode("utf-8", errors="replace").strip()

    def ancestor(self, a, b):
        r = self.run("merge-base", "--is-ancestor", a, b)
        require(not r.truncated and not r.timed_out and r.returncode in (0, 1), "Ancestry check failed")
        return r.returncode == 0


def expected_refs(config, workflow_sha):
    expected = {"refs/heads/" + config["default_branch"]:
                workflow_sha if config["repository"] == WORKFLOW_REPOSITORY else config["default_sha"]}
    expected.update({"refs/heads/" + b["name"]: b["sha"] for b in config["branches"]})
    return expected


def parse_remote(data, config, expected):
    observed = {}
    symref = None
    for line in data.decode("ascii").splitlines():
        value, ref = line.split("\t", 1)
        if value.startswith("ref: "):
            require(ref == "HEAD" and symref is None, "Unexpected symbolic remote ref")
            symref = value[5:]
        else:
            require(ref in {"HEAD", *expected}, "Unrequested remote ref returned")
            require(ref not in observed, "Duplicate remote ref")
            observed[ref] = commit_sha(value)
    default_ref = "refs/heads/" + config["default_branch"]
    require(symref == default_ref, "Remote default branch changed: " + config["repository"])
    require(observed.get("HEAD") == expected[default_ref], "Remote HEAD hash changed: " + config["repository"])
    require({k: v for k, v in observed.items() if k != "HEAD"} == expected,
            "A reviewed branch is missing or moved: " + config["repository"])
    return {"default_ref": symref, "head": observed["HEAD"], "exact_refs_verified": len(expected)}


def check_remote(runtime, config, expected, operation):
    url = "https://github.com/" + config["repository"] + ".git"
    args = ["git", *GIT_OPTIONS, "ls-remote", "--symref", "--exit-code", url, "HEAD", *expected]
    result = runtime.remote(config["repository"], operation, args)
    require(result.complete, "Public exact-ref verification failed: " + config["repository"])
    return parse_remote(result.data, config, expected)


def verify_control_successor(git, baseline, observed):
    require(git.ancestor(baseline, observed), "Workflow baseline is not retained by current default")
    raw = git.run("diff", "--name-only", "-z", baseline, observed)
    require(raw.complete, "Control successor diff incomplete")
    changed = set(raw.data.decode("utf-8").rstrip("\0").split("\0")) if raw.data else set()
    require(changed <= CONTROL_FILES, "Workflow successor changed files outside exact reviewed control paths")
    # Check every intervening commit too, not only net tree changes. Reverted code changes are not accepted.
    commits = git.text("rev-list", "--max-count=101", baseline + ".." + observed).splitlines()
    require(len(commits) <= 100, "Too many workflow control successor commits")
    for sha in commits:
        parents = git.text("show", "-s", "--format=%P", sha).split()
        require(len(parents) == 1, "Workflow control successor must be linear, with no merges")
        r = git.run("diff", "--name-only", "-z", parents[0], sha)
        require(r.complete, "Control successor commit diff incomplete")
        paths = set(r.data.decode("utf-8").rstrip("\0").split("\0")) if r.data else set()
        require(paths <= CONTROL_FILES, "A successor commit touched non-control files")
    return sorted(changed)


def fetch_repository(runtime, config, workflow_sha):
    repo = config["repository"]
    expected = expected_refs(config, workflow_sha)
    pre = check_remote(runtime, config, expected, "verify-exact-refs-before-fetch")
    folder = runtime.work / repo.split("/")[1]
    require(not folder.exists(), "Refusing to reuse repository storage")
    init = runtime.capture(["git", *GIT_OPTIONS, "init", "--bare", "--template=", str(folder)])
    require(init.complete, "Bare repository initialization failed")
    mappings = [(source, "refs/semantic-review/" + str(i), sha)
                for i, (source, sha) in enumerate(expected.items())]
    args = ["git", *GIT_OPTIONS, "fetch", "--atomic", "--no-tags", "--no-recurse-submodules",
            "--no-write-fetch-head", "https://github.com/" + repo + ".git",
            *(source + ":" + target for source, target, _ in mappings)]
    result = runtime.remote(repo, "fetch-exact-reviewed-refs-full-history", args, cwd=folder, storage_path=folder)
    require(result.complete, "Exact public ref fetch failed, timed out, or exceeded storage bound: " + repo)
    git = Git(runtime, folder)
    require(git.text("rev-parse", "--is-shallow-repository") == "false", "Shallow history is forbidden")
    for source, target, sha in mappings:
        require(git.text("rev-parse", target + "^{commit}") == sha, "Ref moved during fetch: " + source)
    checked = git.run("fsck", "--connectivity-only", "--no-dangling")
    require(checked.complete, "Fetched source history connectivity check failed")
    changed = []
    if repo == WORKFLOW_REPOSITORY:
        changed = verify_control_successor(git, config["default_sha"], workflow_sha)
    post = check_remote(runtime, config, expected, "verify-exact-refs-after-fetch")
    return git, {"before_fetch": pre, "after_fetch": post,
                 "comparison_default_sha": config["default_sha"],
                 "fetched_default_sha": expected["refs/heads/" + config["default_branch"]],
                 "workflow_control_successor_changed_paths": changed,
                 "full_history": True, "repository_scripts_executed": False,
                 "remote_refs": expected}


class Evidence:
    def __init__(self, root, runtime):
        self.root, self.runtime = Path(root), runtime
        self.root.mkdir(parents=True, exist_ok=False)
        self.patch_bytes = 0
        self.artifacts = []

    def json(self, relative, data):
        raw = json_bytes(data)
        require(len(raw) <= 16 * 1024 * 1024, "JSON report exceeds per-file bound")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def patch(self, git, relative, args, per_file_cap):
        remaining = self.runtime.limits["patch_artifact_bytes"] - self.patch_bytes
        if remaining <= 0:
            record = {"path": None, "requested_path": relative, "complete": False,
                      "truncated": False, "omitted": True, "reason": "Aggregate patch artifact budget exhausted"}
            self.artifacts.append(record)
            return record, None
        result = git.run(*args, cap=min(remaining, per_file_cap))
        raw = result.data
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.patch_bytes += len(raw)
        record = {"path": relative, "stored_bytes": len(raw), "sha256": digest(raw),
                  "complete": result.complete, "truncated": result.truncated,
                  "timed_out": result.timed_out, "observed_bytes_at_least": result.observed_bytes,
                  "returncode": result.returncode, "omitted": False,
                  "notice": "A truncated patch is a byte prefix, not an applyable or complete patch." if result.truncated else None}
        self.artifacts.append(record)
        return record, raw if result.complete else None


def cherry_map(result):
    if not result.complete:
        return {}, False
    answer = {}
    for line in result.data.decode("ascii").splitlines():
        sign, sha = line.split(" ", 1)
        require(sign in ("+", "-") and sha not in answer, "Malformed git cherry evidence")
        answer[commit_sha(sha)] = sign
    return answer, True


def patch_id(git, raw):
    if not raw:
        return None
    result = git.run("patch-id", "--stable", input_data=raw, cap=256)
    require(result.complete, "Stable patch-id calculation failed")
    lines = result.data.decode("ascii").splitlines()
    require(len(lines) <= 1, "Expected a single commit patch-id")
    return commit_sha(lines[0].split()[0]) if lines else None


def collect_commit(git, sha, evidence, prefix, cache):
    if sha in cache:
        return cache[sha]
    meta = git.text("show", "-s", "--format=%H%x00%P%x00%T%x00%s%x00%cI", sha).split("\0")
    require(len(meta) == 5 and meta[0] == sha, "Malformed commit metadata")
    parents = [commit_sha(x) for x in meta[1].split()]
    row = {"sha": sha, "parents": parents, "tree": commit_sha(meta[2]),
           "subject": meta[3][:500], "subject_truncated": len(meta[3]) > 500,
           "committer_date": meta[4], "merge_commit": len(parents) > 1,
           "patch_id_stable": None, "patches": []}
    if len(parents) <= 1:
        record, raw = evidence.patch(git, prefix + "/commits/" + sha + ".patch",
                                     ["show", "--format=", *DIFF_OPTIONS, sha, "--"],
                                     git.runtime.limits["commit_patch_bytes"])
        row["patches"].append(record)
        if raw is not None:
            row["patch_id_stable"] = patch_id(git, raw)
            row["patch_id_status"] = "computed" if row["patch_id_stable"] else "empty-tree-change"
        else:
            row["patch_id_status"] = "incomplete-patch-evidence"
    else:
        row["patch_id_status"] = "not-defined-for-merge-commit"
        row["parents_truncated"] = len(parents) > git.runtime.limits["merge_parents"]
        for index, parent in enumerate(parents[:git.runtime.limits["merge_parents"]]):
            record, _ = evidence.patch(git, prefix + "/commits/" + sha + "-parent-" + str(index + 1) + ".patch",
                                       ["diff", *DIFF_OPTIONS, parent, sha, "--"],
                                       git.runtime.limits["commit_patch_bytes"])
            row["patches"].append(dict(record, parent_sha=parent))
    cache[sha] = row
    return row


def analyze_branch(git, config, branch, index, evidence, tree_index, tree_index_complete, branch_trees, cache):
    base, head = config["default_sha"], branch["sha"]
    prefix = config["repository"].split("/")[1]
    folder = prefix + "/branches/" + str(index).zfill(3)
    unique_count = int(git.text("rev-list", "--count", base + ".." + head))
    default_only_count = int(git.text("rev-list", "--count", head + ".." + base))
    tree = git.text("rev-parse", head + "^{tree}")
    default_tree = git.text("rev-parse", base + "^{tree}")
    bases = git.run("merge-base", "--all", base, head)
    require(bases.returncode in (0, 1) and not bases.truncated and not bases.timed_out, "Merge-base computation incomplete")
    all_bases = [commit_sha(x) for x in bases.data.decode().splitlines()]
    selected_bases = all_bases[:git.runtime.limits["merge_bases"]]
    row = {"repository": config["repository"], "branch": branch["name"], "head": head,
           "comparison_default_sha": base, "protected_at_receipt": branch["protected_at_receipt"],
           "summary_path": folder + "/summary.json", "unique_commit_count": unique_count,
           "default_only_commit_count": default_only_count,
           "head_is_ancestor_of_default": git.ancestor(head, base),
           "default_is_ancestor_of_head": git.ancestor(base, head),
           "merge_bases": selected_bases, "merge_base_count": len(all_bases),
           "merge_bases_truncated": len(all_bases) > len(selected_bases),
           "tree": tree, "tree_equals_default_tip": tree == default_tree,
           "same_tree_commits_on_default_history": tree_index.get(tree, []),
           "default_tree_history_search_complete": tree_index_complete,
           "same_tree_reviewed_branch_tips": [b for b in branch_trees.get(tree, []) if b["name"] != branch["name"]],
           "merge_base_diffs": [], "deletion_authorized": False, "semantic_conclusion": "requires-independent-review"}
    row["default_to_branch_diff"], _ = evidence.patch(git, folder + "/default-to-branch.patch",
        ["diff", *DIFF_OPTIONS, base, head, "--"], git.runtime.limits["diff_bytes"])
    for number, merge_base in enumerate(selected_bases):
        record, _ = evidence.patch(git, folder + "/merge-base-" + str(number + 1) + "-to-branch.patch",
            ["diff", *DIFF_OPTIONS, merge_base, head, "--"], git.runtime.limits["diff_bytes"])
        row["merge_base_diffs"].append(dict(record, merge_base=merge_base,
            tree_equals_branch=git.text("rev-parse", merge_base + "^{tree}") == tree))
    changes = git.run("diff", "--name-status", "-z", "--no-renames", base, head, "--")
    row["changed_paths_complete"] = changes.complete
    if changes.complete:
        parts = changes.data.decode("utf-8", errors="replace").rstrip("\0").split("\0") if changes.data else []
        require(len(parts) % 2 == 0, "Malformed changed path listing")
        row["changed_paths"] = [{"status": parts[i], "path": parts[i + 1]} for i in range(0, len(parts), 2)]
    else:
        row["changed_paths"] = []
    cherry = git.run("cherry", base, head)
    statuses, complete = cherry_map(cherry)
    row["git_cherry_complete"] = complete
    row["git_cherry_counts"] = {"patch_equivalent": sum(v == "-" for v in statuses.values()),
                                "not_patch_equivalent": sum(v == "+" for v in statuses.values())} if complete else None
    row["git_cherry_scope"] = "Non-merge branch-exclusive commits against default-exclusive commits; not a runtime proof."
    evidence.json(folder + "/git-cherry.json", {"complete": complete, "truncated": cherry.truncated,
        "timed_out": cherry.timed_out, "returncode": cherry.returncode, "sign_by_commit": statuses})
    selected = git.text("rev-list", "--topo-order", "--max-count=" + str(git.runtime.limits["unique_commits_per_branch"]),
                        base + ".." + head).splitlines()
    row["unique_commits_listed"] = len(selected)
    row["unique_commits_truncated"] = len(selected) < unique_count
    row["unique_commits_order"] = "Reverse topological (newest first), bounded by reviewed limit."
    commits = []
    for sha in selected:
        detail = collect_commit(git, commit_sha(sha), evidence, prefix, cache)
        sign = statuses.get(sha) if complete else None
        state = ("patch-equivalent-to-default-exclusive-commit" if sign == "-" else
                 "no-equivalent-in-default-exclusive-commits" if sign == "+" else
                 "not-applicable-merge" if detail["merge_commit"] else "unknown")
        commits.append({"sha": sha, "git_cherry_sign": sign, "git_cherry_interpretation": state,
                        "patch_id_stable": detail["patch_id_stable"], "patch_id_status": detail["patch_id_status"],
                        "commit_evidence_path": prefix + "/commit-evidence.json"})
    row["unique_commits"] = commits
    row["evidence_complete_within_selected_scope"] = (
        not row["unique_commits_truncated"] and not row["merge_bases_truncated"] and
        row["changed_paths_complete"] and complete and row["default_to_branch_diff"]["complete"] and
        all(x["complete"] for x in row["merge_base_diffs"]) and
        all(not cache[c["sha"]].get("parents_truncated", False) and
            all(p["complete"] for p in cache[c["sha"]]["patches"]) for c in commits))
    evidence.json(row["summary_path"], row)
    return row


def ancestry_cover(git, config):
    """Minimal maximal-head structural cover, never a merge or deletion decision."""
    names = {}
    for branch in config["branches"]:
        names.setdefault(branch["sha"], []).append(branch["name"])
    require(bool(names), "Expected at least one reviewed nondefault head")
    maximal = [commit_sha(x) for x in git.text("merge-base", "--independent", *names).splitlines()]
    require(set(maximal) <= set(names) and bool(maximal), "Invalid maximal-head cover")
    cache = {}
    groups = []
    covered = set()
    for anchor in maximal:
        members = []
        for head, branches in names.items():
            pair = (head, anchor)
            if pair not in cache:
                cache[pair] = head == anchor or git.ancestor(head, anchor)
            if cache[pair]:
                covered.add(head)
                members.append({"head": head, "branches": branches})
        groups.append({"retaining_head": anchor, "retaining_branches": names[anchor],
                       "covered_reviewed_heads": members,
                       "head_is_already_ancestor_of_default": git.ancestor(anchor, config["default_sha"])})
    require(covered == set(names), "Maximal-head cover did not cover every reviewed head")
    return {"repository": config["repository"], "scope": "Only reviewed nondefault branch heads in this repository.",
            "reviewed_branch_count": len(config["branches"]), "distinct_head_count": len(names),
            "minimal_distinct_retaining_head_count": len(maximal), "maximal_heads": groups,
            "all_reviewed_heads_structurally_covered": True,
            "cached_ancestry_pairs": len(cache), "tag_creation_authorized": False,
            "deletion_authorized": False,
            "notice": "Structural history coverage only. Equal-head branch aliases are interchangeable for ancestry, not necessarily for workflows, PRs or dependencies. No preservation refs were created."}


def analyze_repository(git, config, evidence):
    prefix = config["repository"].split("/")[1]
    base = config["default_sha"]
    history_count = int(git.text("rev-list", "--count", base))
    history = git.text("log", "--format=%H %T", "--max-count=" + str(git.runtime.limits["default_tree_history_entries"]), base)
    tree_index = {}
    for line in history.splitlines():
        sha, tree = line.split()
        tree_index.setdefault(commit_sha(tree), []).append(commit_sha(sha))
    complete = sum(map(len, tree_index.values())) == history_count
    evidence.json(prefix + "/default-tree-history.json", {"complete": complete,
        "total_commits": history_count, "tree_to_commits": tree_index})
    branch_trees = {}
    for branch in config["branches"]:
        tree = git.text("rev-parse", branch["sha"] + "^{tree}")
        branch_trees.setdefault(tree, []).append({"name": branch["name"], "sha": branch["sha"]})
    cover = ancestry_cover(git, config)
    evidence.json(prefix + "/ancestry-cover.json", cover)
    cache = {}
    rows = [analyze_branch(git, config, branch, index, evidence, tree_index, complete, branch_trees, cache)
            for index, branch in enumerate(config["branches"], start=1)]
    patch_index = {}
    for sha, commit in cache.items():
        if commit["patch_id_stable"]:
            patch_index.setdefault(commit["patch_id_stable"], []).append(sha)
    evidence.json(prefix + "/commit-evidence.json", cache)
    evidence.json(prefix + "/patch-id-index.json", {"scope": "Collected non-merge commits from reviewed branches only.",
        "not_a_default_history_negative_test": True, "patch_id_stable_to_commits": patch_index})
    return {"repository": config["repository"], "default_branch": config["default_branch"],
            "comparison_default_sha": base, "branches": rows,
            "default_history_tree_search_complete": complete, "unique_commit_objects_collected": len(cache),
            "ancestry_cover_path": prefix + "/ancestry-cover.json",
            "minimal_distinct_retaining_head_count": cover["minimal_distinct_retaining_head_count"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-manifest-only", action="store_true")
    parser.add_argument("--output", default="branch-semantic-evidence")
    parser.add_argument("--work", default="branch-semantic-git")
    args = parser.parse_args(argv)
    config, manifest_hash = load_manifest(expected_digest=os.environ.get("REVIEWED_MANIFEST_SHA256"))
    if args.verify_manifest_only:
        print(json.dumps({"manifest_sha256": manifest_hash, "repositories": 8, "branches": 97, "network_calls": 0}))
        return 0
    require(os.environ.get("GITHUB_REPOSITORY") == WORKFLOW_REPOSITORY and
            os.environ.get("GITHUB_EVENT_NAME") == "push" and
            os.environ.get("GITHUB_REF") == "refs/heads/main", "Collector runs only on exact SAR main push")
    workflow_sha = commit_sha(os.environ.get("GITHUB_SHA"))
    runtime = Runtime(args.work)
    evidence = Evidence(args.output, runtime)
    evidence.json("reviewed-manifest.json", config)
    report = {"schema_version": 1, "manifest_sha256": manifest_hash,
              "source_receipt_sha256": SOURCE_SHA256, "workflow_sha": workflow_sha,
              "notice": NOTICE, "remote_mutations": 0, "manual_holds": config["manual_holds"],
              "repositories": [], "status": "in-progress", "errors": []}
    try:
        for repo_config in config["repositories"]:
            git, verification = fetch_repository(runtime, repo_config, workflow_sha)
            row = analyze_repository(git, repo_config, evidence)
            # Validate the snapshot again after analysis, not just after fetching.
            verification["after_analysis"] = check_remote(runtime, repo_config,
                expected_refs(repo_config, workflow_sha), "verify-exact-refs-after-analysis")
            row["verification"] = verification
            report_keys = {"repository", "branch", "head", "comparison_default_sha", "summary_path",
                "unique_commit_count", "default_only_commit_count", "head_is_ancestor_of_default",
                "tree_equals_default_tip", "git_cherry_counts", "unique_commits_truncated",
                "evidence_complete_within_selected_scope", "semantic_conclusion", "deletion_authorized"}
            report["repositories"].append(dict(row, branches=[
                {k: v for k, v in branch.items() if k in report_keys} for branch in row["branches"]]))
            evidence.json("inventory-report.json", report)
            print(json.dumps({"repository": row["repository"], "branches_analyzed": len(row["branches"]),
                              "unique_commit_objects": row["unique_commit_objects_collected"]}), flush=True)
        rows = [b for repo in report["repositories"] for b in repo["branches"]]
        report["counts"] = {"repositories": len(report["repositories"]), "branches": len(rows),
            "branches_with_bounded_evidence_complete": sum(b["evidence_complete_within_selected_scope"] for b in rows),
            "branches_with_incomplete_evidence": sum(not b["evidence_complete_within_selected_scope"] for b in rows)}
        require(len(rows) == 97, "Did not analyze all 97 reviewed branches")
        report["status"] = "collected" if report["counts"]["branches_with_incomplete_evidence"] == 0 else "collected-with-explicit-incompleteness"
    except Exception as exc:
        report["status"] = "blocked"
        report["errors"].append({"type": type(exc).__name__, "message": str(exc)[:1000],
                                  "remote_mutation_attempted": False})
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - runtime.started, 2)
        report["patch_artifact_bytes"] = evidence.patch_bytes
        evidence.json("inventory-report.json", report)
        evidence.json("network-operations.json", runtime.network_operations)
        evidence.json("artifact-index.json", evidence.artifacts)
        # Bare source repositories remain in --work for the job lifetime. No pruning or history rewriting occurs.
        print(json.dumps({"status": report["status"], "counts": report.get("counts"),
                          "errors": report["errors"], "remote_mutations": 0}), flush=True)
    return 0 if report["status"] == "collected" else 2


if __name__ == "__main__":
    raise SystemExit(main())
