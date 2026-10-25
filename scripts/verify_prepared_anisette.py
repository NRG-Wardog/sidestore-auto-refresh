#!/usr/bin/env python3
"""Verify the exact pinned Anisette transforms and every untouched directory file.

This is a read-only build boundary, not a patch replay: expected bytes always
come from pinned Git objects, never the index, HEAD's working files, or markers.
"""
import argparse
import os
from pathlib import Path
import stat
import subprocess

from patch_combined_refresh_contract import PIN
from patch_embedded_keychain import ANISETTE_PATHS, patch_anisette_config, patch_anisette_provider

DIRECTORY = "SideStore/Core/Anisette"


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *arguments], stderr=subprocess.STDOUT)


def pinned_inventory(root: Path) -> dict[str, tuple[str, str]]:
    if git(root, "rev-parse", "HEAD").decode("ascii").strip() != PIN:
        raise ValueError("Anisette boundary requires the pinned embedded SideStore revision")
    records = git(root, "ls-tree", "-rz", "--full-tree", PIN, "--", DIRECTORY)
    inventory = {}
    for record in records.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Unexpected pinned Anisette entry type: " + path)
        if not path.startswith(DIRECTORY + "/") or path in inventory:
            raise ValueError("Invalid pinned Anisette inventory")
        inventory[path] = (mode, object_id)
    if not inventory or not set(ANISETTE_PATHS).issubset(inventory):
        raise ValueError("Pinned Anisette inventory is incomplete")
    return inventory


def working_inventory(root: Path) -> set[str]:
    directory = root / DIRECTORY
    # Never follow a substituted directory or file outside the prepared tree.
    for path in (root / "SideStore", root / "SideStore/Core", directory):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Missing or substituted Anisette directory")
    files = set()
    def fail_on_walk_error(error):
        raise error
    for current, directories, names in os.walk(directory, followlinks=False, onerror=fail_on_walk_error):
        for name in directories:
            if (Path(current) / name).is_symlink():
                raise ValueError("Unexpected Anisette directory link")
        for name in names:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("Unexpected Anisette file type: " + relative)
            files.add(relative)
    return files


def verify(root: Path) -> int:
    inventory = pinned_inventory(root)
    actual = working_inventory(root)
    if actual != set(inventory):
        extra = sorted(actual - set(inventory))
        missing = sorted(set(inventory) - actual)
        raise ValueError(f"Anisette file inventory drift: added={extra}, missing={missing}")
    transforms = {
        ANISETTE_PATHS[0]: patch_anisette_config,
        ANISETTE_PATHS[1]: lambda text: patch_anisette_provider(text, on_device=True),
        ANISETTE_PATHS[2]: lambda text: patch_anisette_provider(text, on_device=False),
    }
    for relative, (mode, object_id) in inventory.items():
        original = git(root, "cat-file", "blob", object_id)
        transform = transforms.get(relative)
        expected = transform(original.decode("utf-8")).encode("utf-8") if transform else original
        target = root / relative
        actual_mode = "100755" if target.stat().st_mode & 0o111 else "100644"
        if actual_mode != mode or target.read_bytes() != expected:
            raise ValueError("Prepared Anisette source differs from its exact pinned transform: " + relative)
    return len(inventory)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidestore_root", type=Path)
    arguments = parser.parse_args()
    try:
        count = verify(arguments.sidestore_root)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Anisette source boundary failed: {error}\n")
    print(f"ANISETTE_PINNED_BOUNDARY_PASS files={count} exact_transforms={len(ANISETTE_PATHS)}")


if __name__ == "__main__":
    main()
