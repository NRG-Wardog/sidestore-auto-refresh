#!/usr/bin/env python3
"""Fail closed before executing dependency code, including mirror checkouts.

The commit and tree identities are the existing release pins. A transport URL
change never authorizes a different commit, tree, or recursive gitlink.
"""
import argparse
import json
from pathlib import Path
import subprocess

PINS = {
    "live": ("12377cf3b91d51739a33f14a302e5f522b238593", "06c7c54047734a7e19ec260d04dc073a52a1c872"),
    "side": ("ff25922e5c13ccfafd83bda5092910d848ebd409", "b015ff18eac594382beffd3780a231117573dc6b"),
}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def verify_checkout(root, commit, tree=None):
    root = Path(root)
    actual = git(root, "rev-parse", "HEAD")
    if actual != commit:
        raise ValueError(f"{root.name}: unexpected commit {actual}, expected {commit}")
    actual_tree = git(root, "rev-parse", "HEAD^{tree}")
    if tree is not None and actual_tree != tree:
        raise ValueError(f"{root.name}: unexpected tree")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError(f"{root.name}: checkout is not pristine")
    children = []
    for entry in git(root, "ls-tree", "-r", "HEAD").splitlines():
        metadata, path = entry.split("\t", 1)
        mode, kind, sha = metadata.split()
        if mode != "160000":
            continue
        child = root / path
        # rev-parse in an empty directory can resolve the parent's repository.
        # Require an actual submodule checkout before inspecting its HEAD.
        if kind != "commit" or not (child / ".git").exists():
            raise ValueError(f"{path}: pinned submodule is missing")
        result = verify_checkout(child, sha)
        result["path"] = path
        children.append(result)
    return {"commit": actual, "tree": actual_tree, "submodules": children}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", type=Path, required=True)
    parser.add_argument("--side", type=Path, required=True)
    args = parser.parse_args()
    result = {name: verify_checkout(getattr(args, name), *pin) for name, pin in PINS.items()}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
