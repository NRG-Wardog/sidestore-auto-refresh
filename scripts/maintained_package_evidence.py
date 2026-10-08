"""Preserve reviewed maintained-source contracts without inventing patch receipts."""
import hashlib
import json
from pathlib import Path

from maintained_sources import (ROOT, DIAGNOSTIC_BASIS, DIAGNOSTIC_METADATA,
                                contract_basis, diagnostic_receipt_references, git, require, unique_pairs)

CONTRACT_DIRECTORY = "maintained-source-contracts"
EVIDENCE_KIND = "maintained-source-contracts-v1"
LEGACY_HOST_MANIFESTS = {".lc-app-layout.json", ".combined-service-startup.json"}
LEGACY_EMBEDDED_MANIFESTS = {".combined-refresh-contract.json"}


def read_regular(root, name):
    """Read only an ordinary file beneath the selected source/evidence root."""
    root = Path(root)
    relative = Path(name)
    require(not relative.is_absolute() and bool(relative.parts) and
            all(part not in {".", "..", ".git"} for part in relative.parts),
            "unsafe maintained evidence path: " + str(name))
    require(not root.is_symlink() and root.is_dir(), "missing or linked maintained evidence root")
    path = root
    for part in relative.parts:
        path = path / part
        require(not path.is_symlink(), "linked maintained evidence path: " + str(name))
    require(path.is_file(), "missing maintained evidence file: " + str(name))
    return path.read_bytes()


def contract_files(pins, root=None):
    """Derive the complete metadata inventory from the independently approved hash."""
    try:
        selected_root, expected_registry = contract_basis(pins, integration_root=ROOT)
    except ValueError as error:
        raise ValueError("unapproved package contract registry") from error
    root = Path(root) if root is not None else selected_root
    registry_name = "compatibility-registry.json"
    registry_data = read_regular(root, registry_name)
    require(hashlib.sha256(registry_data).hexdigest() == expected_registry, "package contract registry hash mismatch")
    registry = json.loads(registry_data, object_pairs_hook=unique_pairs)
    references = {registry["evidence"]["path"]: registry["evidence"]["sha256"]}
    references.update({item["manifest_path"]: item["manifest_sha256"]
                       for item in registry["owners"].values()})
    if pins.get("source_basis") == DIAGNOSTIC_BASIS:
        references.update(DIAGNOSTIC_METADATA)
        if "diagnostic_dependencies" in pins:
            references.update(diagnostic_receipt_references(pins))
    files = {registry_name: registry_data}
    for name, expected in references.items():
        data = read_regular(root, name)
        require(hashlib.sha256(data).hexdigest() == expected,
                "package contract metadata hash mismatch: " + name)
        files[name] = data
    return files


def collect_contract_evidence(output, pins):
    files = contract_files(pins)
    for name, data in files.items():
        destination = output / CONTRACT_DIRECTORY / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


def verify_contract_evidence(output, hashes, pins):
    root = output / CONTRACT_DIRECTORY
    files = contract_files(pins, root)
    require(hashes == {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            "maintained contract evidence hash inventory mismatch")
    actual = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), "linked maintained contract evidence")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    require(actual == set(files), "maintained contract evidence file inventory mismatch")


def read_pinned_sources(source, side_source, host_names, side_names, pins):
    """Bind each retained compiler input to its actual maintained owner Git blob.

    The workflow separately verifies all seven owners and the entire graph.
    This smaller check also runs before compilation and during collection.
    """
    roots = {"LiveContainer": source, "SideStore": side_source,
             "SideSign": side_source / "Dependencies/SideSign",
             "minimuxer": side_source / "Dependencies/minimuxer"}
    for owner, root in roots.items():
        require(git(root, "rev-parse", "HEAD").decode().strip() == pins["owners"][owner]["commit"],
                "packaging source commit mismatch: " + owner)
    files = {}
    for prefix, names in (("", host_names), ("embedded/", side_names)):
        for name in names:
            owner = "SideStore" if prefix else "LiveContainer"
            relative = name
            if prefix and name.startswith("Dependencies/"):
                _, owner, relative = name.split("/", 2)
                require(owner in {"SideSign", "minimuxer"}, "unexpected packaging source owner")
            data = read_regular(roots[owner], relative)
            expected = git(roots[owner], "show", pins["owners"][owner]["commit"] + ":" + relative)
            require(data == expected, "packaging source differs from pinned blob: " + prefix + name)
            files[prefix + name] = data
    return files
