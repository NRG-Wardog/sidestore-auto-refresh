#!/usr/bin/env python3
"""Read-only, fail-closed compatibility gate; Python 3.10+, POSIX dir_fd required.

No git commands, source rewriting, network, subprocesses, imports from source trees,
or automatically accepted checksums. The caller supplies a trusted registry digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


OWNERS = frozenset({"LiveContainer", "SideStore", "AnisetteKit", "SideSign", "minimuxer", "idevice", "jktcp"})
BASELINE = "141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee"
FORMAT = 1
MAX_FILE_SIZE = 16 * 1024 * 1024


class ContractError(ValueError):
    """An untrusted or incompatible input was rejected."""


def require(condition, message):
    if not condition:
        raise ContractError(message)


def keys(value, expected, label):
    require(isinstance(value, dict), f"{label}: expected an object")
    require(set(value) == set(expected), f"{label}: unexpected or missing fields")


def string(value, label):
    require(isinstance(value, str) and bool(value) and all(ord(c) >= 32 for c in value),
            f"{label}: expected a nonempty printable string")
    return value


def digest(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            f"{label}: expected a lowercase SHA-256")
    return value


def version(value, label):
    require(isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value) is not None,
            f"{label}: exact numeric version required; ranges and wildcards are forbidden")
    return value


def relative(value, label):
    string(value, label)
    require(not value.startswith("/") and "\\" not in value and ":" not in value,
            f"{label}: unsafe relative path")
    require(all(part not in {"", ".", "..", ".git"} for part in value.split("/")),
            f"{label}: unsafe relative path")
    return value


def absolute_parts(value):
    value = os.fspath(value)
    string(value, "absolute path")
    require(value.startswith("/") and not value.startswith("//") and "\\" not in value,
            "an absolute POSIX path is required")
    parts = value[1:].split("/")
    require(parts and all(p not in {"", ".", ".."} for p in parts), "unsafe absolute path")
    return parts


def open_directory(value):
    """Traverse every component using directory descriptors, never following links."""
    require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd,
            "safe POSIX O_NOFOLLOW/dir_fd file access is unavailable")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute_parts(value):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def safe_read(path):
    """Only read a stable regular file, with no symlink or hardlink indirection."""
    parts = absolute_parts(path)
    require(len(parts) > 1, "file must have a parent directory")
    parent = open_directory("/" + "/".join(parts[:-1]))
    fd = None
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), f"{path}: not a regular file")
        require(before.st_nlink == 1, f"{path}: hardlinks are forbidden")
        require(before.st_size <= MAX_FILE_SIZE, f"{path}: file exceeds size limit")
        require(not before.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX), f"{path}: unsafe mode bits")
        chunks = []
        size = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, MAX_FILE_SIZE + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            require(size <= MAX_FILE_SIZE, f"{path}: file exceeds size limit")
        after = os.fstat(fd)
        fields = ("st_ino", "st_dev", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        require(all(getattr(before, f) == getattr(after, f) for f in fields) and size == after.st_size,
                f"{path}: file changed during validation")
        return b"".join(chunks), "100755" if after.st_mode & 0o111 else "100644"
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def duplicate_free(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"JSON: duplicate key {key}")
        result[key] = value
    return result


def load_json(path, expected_hash=None):
    raw, mode = safe_read(path)
    require(mode == "100644", f"{path}: metadata must be non-executable")
    if expected_hash is not None:
        digest(expected_hash, "expected hash")
        require(sha256(raw) == expected_hash, f"{path}: trusted digest mismatch")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate_free,
                          parse_constant=lambda s: (_ for _ in ()).throw(ContractError("JSON: non-finite value")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError(f"{path}: invalid JSON") from exc


def sorted_unique_list(value, label, nonempty=True):
    require(isinstance(value, list) and (bool(value) or not nonempty), f"{label}: expected a list")
    require(all(isinstance(v, str) for v in value), f"{label}: expected string list")
    require(value == sorted(set(value)), f"{label}: list must be sorted and unique")


def validate(registry_path, trusted_registry_sha256, roots, manifest_paths=None):
    """Validate exactly seven independent source roots against one trusted set.

    The trusted digest must originate outside the candidate checkout. This function
    cannot decide who approved it and does not authenticate a registry's author.
    """
    require(set(roots) == OWNERS, "source roots: missing or extra owners")
    if manifest_paths is not None:
        require(set(manifest_paths) == OWNERS, "manifest paths: missing or extra owners")
    for root in roots.values():
        fd = open_directory(root)
        os.close(fd)
    registry_path = os.fspath(registry_path)
    reg = load_json(registry_path, digest(trusted_registry_sha256, "trusted registry hash"))
    keys(reg, {"format_version", "contract_set", "integration_baseline", "owners", "contracts", "edges", "evidence"}, "registry")
    require(type(reg["format_version"]) is int and reg["format_version"] == FORMAT, "registry: unsupported format")
    require(reg["integration_baseline"] == BASELINE, "registry: wrong integration baseline")
    string(reg["contract_set"], "contract set")
    require(isinstance(reg["owners"], dict) and set(reg["owners"]) == OWNERS, "registry: missing or extra owners")
    keys(reg["evidence"], {"path", "sha256"}, "evidence reference")
    base = str(Path(registry_path).parent)
    evidence = load_json(base + "/" + relative(reg["evidence"]["path"], "evidence path"), digest(reg["evidence"]["sha256"], "evidence hash"))
    keys(evidence, {"format_version", "integration_baseline", "inventory_digests", "owners"}, "evidence")
    require(type(evidence["format_version"]) is int and evidence["format_version"] == FORMAT, "evidence: unsupported format")
    require(evidence["integration_baseline"] == BASELINE, "evidence: wrong baseline")
    require(isinstance(evidence["inventory_digests"], dict) and bool(evidence["inventory_digests"]), "evidence: no inventory provenance")
    for filename, value in evidence["inventory_digests"].items():
        relative(filename, "inventory name")
        digest(value, "inventory digest")
    require(isinstance(evidence["owners"], dict) and set(evidence["owners"]) == OWNERS, "evidence: missing or extra owners")
    contracts = reg["contracts"]
    require(isinstance(contracts, dict) and bool(contracts), "registry: empty contracts")
    for name, entry in contracts.items():
        require(re.fullmatch(r"[a-z][a-z0-9-]*", name) is not None, "registry: invalid contract identifier")
        keys(entry, {"version", "description", "providers"}, f"contract {name}")
        version(entry["version"], f"contract {name}")
        string(entry["description"], f"contract {name} description")
        sorted_unique_list(entry["providers"], f"contract {name} providers")
        require(set(entry["providers"]) <= OWNERS, f"contract {name}: unknown provider")
    require(isinstance(reg["edges"], list) and bool(reg["edges"]), "registry: empty consumer/provider edges")
    edges = set()
    for e in reg["edges"]:
        keys(e, {"consumer", "provider", "contract", "version"}, "edge")
        require(e["consumer"] in OWNERS and e["provider"] in OWNERS and e["consumer"] != e["provider"], "edge: invalid owners")
        require(e["contract"] in contracts, "edge: unknown contract")
        c = contracts[e["contract"]]
        require(e["provider"] in c["providers"] and e["version"] == c["version"], "edge: incompatible provider contract")
        edge = tuple(e[k] for k in ("consumer", "provider", "contract", "version"))
        require(edge not in edges, "registry: duplicate edge")
        edges.add(edge)
    provided = set()
    required = set()
    checked = {}
    for owner in sorted(OWNERS):
        pin = reg["owners"][owner]
        keys(pin, {"manifest_path", "manifest_sha256", "version"}, f"owner {owner}")
        relative(pin["manifest_path"], f"{owner} manifest path")
        version(pin["version"], f"{owner} version")
        mp = manifest_paths[owner] if manifest_paths is not None else base + "/" + pin["manifest_path"]
        m = load_json(mp, digest(pin["manifest_sha256"], f"{owner} manifest hash"))
        keys(m, {"format_version", "owner", "contract_set", "version", "provenance", "sources", "provides", "requires"}, f"{owner} manifest")
        require(type(m["format_version"]) is int and m["format_version"] == FORMAT, f"{owner}: unsupported format")
        require(m["owner"] == owner, f"{owner}: wrong manifest owner")
        require(m["contract_set"] == reg["contract_set"], f"{owner}: wrong contract set")
        require(m["version"] == pin["version"], f"{owner}: wrong manifest version")
        ev = evidence["owners"][owner]
        keys(ev, {"provenance", "sources"}, f"{owner} evidence")
        keys(ev["provenance"], {"integration_baseline", "upstream_repository", "upstream_commit", "source_basis"}, f"{owner} provenance")
        prov = ev["provenance"]
        require(prov["integration_baseline"] == BASELINE, f"{owner}: wrong provenance baseline")
        require(isinstance(prov["upstream_commit"], str) and re.fullmatch(r"[0-9a-f]{40}", prov["upstream_commit"]) is not None,
                f"{owner}: invalid upstream commit")
        require(isinstance(prov["upstream_repository"], str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", prov["upstream_repository"]) is not None,
                f"{owner}: invalid upstream repository")
        string(prov["source_basis"], f"{owner} source basis")
        require(m["provenance"] == prov, f"{owner}: provenance differs from reviewed evidence")
        require(isinstance(m["sources"], list) and bool(m["sources"]), f"{owner}: no sources")
        require(m["sources"] == ev["sources"], f"{owner}: sources differ from reviewed evidence")
        files = {}
        for record in m["sources"]:
            keys(record, {"path", "sha256", "mode", "basis"}, f"{owner} source")
            path = relative(record["path"], f"{owner} source path")
            require(path not in files, f"{owner}: duplicate source path")
            digest(record["sha256"], f"{owner}:{path} hash")
            require(record["mode"] in {"100644", "100755"}, f"{owner}:{path}: invalid mode")
            string(record["basis"], f"{owner}:{path} basis")
            files[path] = record
        seen_bindings = set()
        require(isinstance(m["provides"], list) and isinstance(m["requires"], list), f"{owner}: invalid declarations")
        for role in ("provides", "requires"):
            seen = set()
            for declaration in m[role]:
                fields = {"contract", "version", "source_paths"} | ({"provider"} if role == "requires" else set())
                keys(declaration, fields, f"{owner} {role}")
                name = declaration["contract"]
                require(name in contracts and declaration["version"] == contracts[name]["version"], f"{owner}: incompatible contract version")
                paths = declaration["source_paths"]
                sorted_unique_list(paths, f"{owner}:{name} source bindings")
                require(set(paths) <= set(files), f"{owner}:{name}: unbound source path")
                seen_bindings.update(paths)
                if role == "provides":
                    key = (owner, name, declaration["version"])
                    require(owner in contracts[name]["providers"], f"{owner}:{name}: unapproved provider")
                    provided.add(key)
                else:
                    key = (owner, declaration["provider"], name, declaration["version"])
                    require(key in edges, f"{owner}:{name}: unapproved consumer/provider contract")
                    required.add(key)
                require(key not in seen, f"{owner}: duplicate {role} declaration")
                seen.add(key)
        require(seen_bindings == set(files), f"{owner}: source not bound to a contract")
        for path, record in files.items():
            raw, mode = safe_read(os.fspath(roots[owner]) + "/" + path)
            require(sha256(raw) == record["sha256"], f"{owner}:{path}: source drift")
            require(mode == record["mode"], f"{owner}:{path}: executable mode drift")
        checked[owner] = len(files)
    expected_providers = {(p, c, v["version"]) for c, v in contracts.items() for p in v["providers"]}
    require(provided == expected_providers, "missing or extra provider declarations")
    require(required == edges, "missing or extra consumer requirements")
    require(all((p, c, v) in provided for _, p, c, v in required), "unfulfilled consumer contract")
    return {"status": "pass", "contract_set": reg["contract_set"], "registry_sha256": trusted_registry_sha256,
            "integration_baseline": BASELINE, "owners": checked, "contracts": len(contracts), "edges": len(edges),
            "source_files": sum(checked.values()), "scope": "reviewed source bytes and build-only compatibility metadata"}


def mappings(values, label):
    result = {}
    for raw in values:
        require("=" in raw, f"{label}: use OWNER=/absolute/path")
        owner, path = raw.split("=", 1)
        require(owner not in result, f"{label}: duplicate owner {owner}")
        result[owner] = path
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", required=True, help="absolute path to reviewed integration registry")
    p.add_argument("--registry-sha256", required=True, help="review-approved digest supplied OUTSIDE the candidate artifacts")
    p.add_argument("--owner", action="append", default=[], metavar="OWNER=/absolute/source/root", required=True)
    p.add_argument("--manifest", action="append", metavar="OWNER=/absolute/manifest.json",
                   help="use exactly seven manifests from owner checkouts instead of artifact-package paths")
    args = p.parse_args(argv)
    try:
        result = validate(args.registry, args.registry_sha256, mappings(args.owner, "source roots"),
                          mappings(args.manifest, "manifests") if args.manifest is not None else None)
    except (ContractError, OSError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
