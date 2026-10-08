#!/usr/bin/env python3
"""Acquire exact maintained owners and check compiler inputs without rewriting them.

The pin map is reviewed build policy. Pending production pins are deliberately
null: no command may substitute a source checkpoint, branch or local candidate.
The frozen contract validator remains the source-level compatibility authority.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee"
REGISTRY = "5cd17d665d9d13fde7bdc58abe3c44050b4b1618df89efbdf9815a39736d76be"
DIAGNOSTIC_BASIS = "maintained-adi-consumption-v1"
DIAGNOSTIC_REGISTRY = "97c9d0b81e9b59c8271ae1155393b2fcb534dc97ea367095d3adfcde8a3783ad"
DIAGNOSTIC_METADATA = {
    "provenance/accepted-to-diagnostic-delta.json": "4d55fdeed931af02f9d44865c0c712695b471d8a101aa1643c3770484f158106",
    "provenance/anisette-diagnostic-source-inventory.json": "4a982557122b6db282eaf7e8c32cb1bf4a4d23fa021f001ae181c2e0a643c027",
    "anisette-generated/maintained-source-manifest.json": "0813a3a636bac79caff934940ead90d6099a541d5862c866bca6a0e98be3fd7f",
}

OWNERS = {"LiveContainer", "SideStore", "SideSign", "AnisetteKit", "minimuxer", "idevice", "jktcp"}
ENV_KEYS = {"LiveContainer": "LIVE_CONTAINER_REF", "SideStore": "EMBEDDED_SIDESTORE_REF",
            "SideSign": "SIDESIGN_REF", "AnisetteKit": "ANISETTE_REF",
            "minimuxer": "MINIMUXER_REF", "idevice": "IDEVICE_REF", "jktcp": "JKTCP_REF"}
PATHS = {"LiveContainer": "work/LiveContainer", "SideStore": "work/EmbeddedSideStore",
         "AnisetteKit": "work/AnisetteKit", "SideSign": "work/EmbeddedSideStore/Dependencies/SideSign",
         "minimuxer": "work/EmbeddedSideStore/Dependencies/minimuxer", "idevice": "idevice", "jktcp": "jktcp"}
APP_LOCK = "AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
PRODUCTION_METADATA = {".ci/production-dependencies.py", ".ci/production-dependencies.json",
                       ".ci/test_production_dependencies.py", ".ci/PRODUCTION_DEPENDENCIES.md"}
ALLOWED_TRANSITIONS = {
    "SideSign": PRODUCTION_METADATA | {"Package.swift", "Package.resolved"},
    "SideStore": PRODUCTION_METADATA | {".gitmodules", APP_LOCK,
        "Dependencies/SideSign", "Dependencies/minimuxer", "tests/runtime_source/test_runtime_source.py"},
}
# Only untracked build products are tolerated after compilation. Tracked files
# are compared to immutable blobs even if an index flag hides their modifications.
BUILD_OUTPUTS = {"idevice": ("target/", "ffi/idevice.h", "cpp/include/idevice.h", "swift/include/idevice.h", "swift/IDevice.xcframework/"),
                 "jktcp": ("target/",), "minimuxer": ("DeviceGateway/LocalBinary/IDevice.xcframework/",),
                 # Frozen Makefile clean/copy/ipa-sidebackup targets write only these.
                 "SideStore": ("build/sidebackup.xcarchive/", "build/SideBackup.ipa")}


def require(value, message):
    if not value:
        raise ValueError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def contract_basis(pins, integration_root=None):
    """Select a reviewed immutable basis explicitly; never infer it from a hash."""
    root = Path(integration_root) if integration_root is not None else ROOT
    basis = pins.get("source_basis")
    if basis is None:
        expected, directory = REGISTRY, root / "migration/contracts"
    else:
        require(basis == DIAGNOSTIC_BASIS, "unsupported maintained diagnostic basis")
        expected, directory = DIAGNOSTIC_REGISTRY, root / "migration/diagnostics/adi-consumption-v1"
    require(pins.get("contract_registry_sha256") == expected, "unapproved contract registry")
    return directory, expected


def diagnostic_metadata(pins, name):
    directory, _ = contract_basis(pins)
    require(pins.get("source_basis") == DIAGNOSTIC_BASIS and name in DIAGNOSTIC_METADATA,
            "unapproved diagnostic metadata")
    path = directory
    require(not path.is_symlink(), "linked diagnostic metadata root")
    for part in Path(name).parts:
        path = path / part
        require(not path.is_symlink(), "linked diagnostic metadata path")
    require(path.is_file(), "missing diagnostic metadata")
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == DIAGNOSTIC_METADATA[name],
            "diagnostic metadata differs from reviewed hash: " + name)
    return json.loads(data, object_pairs_hook=unique_pairs)


def diagnostic_delta(pins):
    if pins.get("source_basis") is None:
        contract_basis(pins)
        return None
    value = diagnostic_metadata(pins, "provenance/accepted-to-diagnostic-delta.json")
    require(value["accepted_integration"]["commit"] == "a939e4c077a51734a73a86805d051b578cce3fa7" and
            value["frozen_registry_sha256"] == REGISTRY and set(value["accepted_graph"]) == OWNERS and
            set(value["diagnostic_source_tuple"]) == OWNERS, "diagnostic accepted graph differs")
    return value


def diagnostic_dependency_reference(pins, owner, kind):
    """Resolve only fixed reviewed receipt names, never a caller-supplied path."""
    require(pins.get("source_basis") == DIAGNOSTIC_BASIS and
            owner in {"SideSign", "SideStore"} and kind in {"basis", "resolver"},
            "unsupported diagnostic dependency reference")
    references = pins.get("diagnostic_dependencies", {})
    require(set(references) == {"SideSign", "SideStore"}, "both diagnostic dependency bases are required")
    item = references[owner]
    require(isinstance(item, dict) and set(item) == {"basis_sha256", "resolver_receipt_sha256"},
            "unexpected diagnostic dependency reference schema")
    digest = item["basis_sha256" if kind == "basis" else "resolver_receipt_sha256"]
    require(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest),
            "reviewed diagnostic dependency digest required")
    return "dependencies/" + owner + "-" + kind + ".json", digest


def read_diagnostic_reference(pins, name, digest):
    directory, _ = contract_basis(pins)
    # All names originate in the fixed owner/kind mapping above or the single
    # native receipt location. The independent pin digest approves the bytes.
    allowed = {"dependencies/" + owner + "-" + kind + ".json"
               for owner in ("SideSign", "SideStore") for kind in ("basis", "resolver")}
    allowed.add("provenance/diagnostic-native-readiness.json")
    allowed.add("provenance/adi-focused-native-verification.json")
    require(name in allowed and isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest),
            "unapproved diagnostic receipt reference")
    path = directory
    require(not path.is_symlink(), "linked diagnostic receipt root")
    for part in Path(name).parts:
        path = path / part
        require(not path.is_symlink(), "linked diagnostic receipt path")
    require(path.is_file() and path.stat().st_size <= 1024 * 1024,
            "missing or oversized diagnostic receipt")
    data = path.read_bytes()
    require(len(data) <= 1024 * 1024 and hashlib.sha256(data).hexdigest() == digest,
            "diagnostic receipt differs from reviewed hash")
    return path, json.loads(data, object_pairs_hook=unique_pairs)


def diagnostic_receipt_references(pins):
    result = dict(diagnostic_dependency_reference(pins, owner, kind)
                  for owner in ("SideSign", "SideStore") for kind in ("basis", "resolver"))
    digest = pins.get("native_validation", {}).get("receipt_sha256")
    require(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest),
            "reviewed diagnostic native receipt digest required")
    result["provenance/diagnostic-native-readiness.json"] = digest
    focused = pins.get("native_validation", {}).get("focused_receipt_sha256")
    require(isinstance(focused, str) and re.fullmatch("[0-9a-f]{64}", focused),
            "reviewed focused diagnostic receipt digest required")
    result["provenance/adi-focused-native-verification.json"] = focused
    return result


def verify_diagnostic_dependency_transition(root, owner, checkpoint, spec, pins):
    name, digest = diagnostic_dependency_reference(pins, owner, "basis")
    _, basis = read_diagnostic_reference(pins, name, digest)
    require(basis.get("schema_version") == 1 and basis.get("owner") == owner and
            basis.get("purpose") == "diagnostic_dependency_source_transition" and
            basis.get("source_registry_sha256") == DIAGNOSTIC_REGISTRY,
            owner + ": diagnostic dependency basis identity differs")
    if owner == "SideStore":
        child_name, child_digest = diagnostic_dependency_reference(pins, "SideSign", "basis")
        _, child_basis = read_diagnostic_reference(pins, child_name, child_digest)
        require(basis.get("sidesign") == {
            "repository": pins["owners"]["SideSign"]["repository"],
            "commit": pins["owners"]["SideSign"]["commit"],
            "tree": child_basis["candidate"]["tree"], "basis_sha256": child_digest} and
            child_basis["candidate"]["commit"] == pins["owners"]["SideSign"]["commit"],
            "SideStore: approved child dependency basis differs")
    source_base = basis.get("source_checkpoint") if owner == "SideStore" else basis.get("accepted")
    require(source_base == {"commit": checkpoint,
            "tree": git(root, "rev-parse", checkpoint + "^{tree}").decode().strip()} and
            basis.get("candidate") == {"commit": spec["commit"],
            "tree": git(root, "rev-parse", spec["commit"] + "^{tree}").decode().strip()},
            owner + ": diagnostic dependency source identity differs")
    git(root, "merge-base", "--is-ancestor", checkpoint, spec["commit"])
    before, after = entries(root, checkpoint), entries(root, spec["commit"])
    changes = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    rows = basis.get("changes")
    require(isinstance(rows, dict) and set(rows) == changes,
            owner + ": diagnostic dependency inventory differs")
    for name, row in rows.items():
        require(isinstance(row, dict) and set(row) == {"before", "after"},
                owner + ": malformed diagnostic dependency row")
        for label, inventory in (("before", before), ("after", after)):
            entry = inventory.get(name)
            actual = None
            if entry is not None:
                mode, kind, oid = entry
                require((mode == "160000" and kind == "commit") or
                        (mode in {"100644", "100755"} and kind == "blob"),
                        owner + ": unsupported dependency object type")
                actual = ({"mode": mode, "commit": oid} if kind == "commit" else
                          {"mode": mode, "blob": oid,
                           "sha256": hashlib.sha256(git(root, "cat-file", "blob", oid)).hexdigest()})
            require(row[label] == actual, owner + ": diagnostic dependency mode/blob/hash differs: " + name)


def verify_source_transition(root, owner, spec, pins):
    """Historical build-only transition, exact diagnostic delta, then build-only."""
    delta = diagnostic_delta(pins)
    checkpoint = spec["source_checkpoint"]
    if delta is not None:
        accepted = delta["accepted_graph"][owner]
        diagnostic = delta["diagnostic_source_tuple"][owner]
        require(checkpoint == accepted["source_checkpoint"] and spec["repository"] == accepted["repository"],
                owner + ": historical checkpoint or repository changed")
        git(root, "merge-base", "--is-ancestor", checkpoint, accepted["commit"])
        changes = set(git(root, "diff", "--name-only", checkpoint, accepted["commit"]).decode().splitlines())
        require(changes <= ALLOWED_TRANSITIONS.get(owner, set()), owner + ": accepted production lineage changed")
        for item in (accepted, diagnostic):
            require(git(root, "rev-parse", item["commit"] + "^{tree}").decode().strip() == item["tree"],
                    owner + ": reviewed source tree changed")
        git(root, "merge-base", "--is-ancestor", accepted["commit"], diagnostic["commit"])
        rows = [row for row in delta["runtime_delta"] + delta["nonruntime_delta"] if row["owner"] == owner]
        require(len({row["path"] for row in rows}) == len(rows), "duplicate diagnostic delta path")
        changes = set(git(root, "diff", "--no-renames", "--name-only", accepted["commit"], diagnostic["commit"]).decode().splitlines())
        require(changes == {row["path"] for row in rows}, owner + ": diagnostic delta inventory differs")
        before, after = entries(root, accepted["commit"]), entries(root, diagnostic["commit"])
        for row in rows:
            name = row["path"]
            for prefix, inventory in (("old", before), ("new", after)):
                entry = inventory.get(name)
                if row[prefix + "_git_blob"] is None:
                    require(entry is None and row[prefix + "_mode"] is None and row[prefix + "_sha256"] is None,
                            owner + ": unexpected diagnostic delta entry")
                else:
                    require(entry == (row[prefix + "_mode"], "blob", row[prefix + "_git_blob"]),
                            owner + ": diagnostic mode/blob differs: " + name)
                    data = git(root, "cat-file", "blob", entry[2])
                    require(hashlib.sha256(data).hexdigest() == row[prefix + "_sha256"],
                            owner + ": diagnostic source hash differs: " + name)
        checkpoint = diagnostic["commit"]
        if owner in {"SideSign", "SideStore"}:
            verify_diagnostic_dependency_transition(root, owner, checkpoint, spec, pins)
            return
    git(root, "merge-base", "--is-ancestor", checkpoint, spec["commit"])
    changes = set(git(root, "diff", "--name-only", checkpoint, spec["commit"]).decode().splitlines())
    require(changes <= ALLOWED_TRANSITIONS.get(owner, set()), owner + ": product source changed after approved checkpoint")


def expected_anisette_evidence(pins=None):
    if pins is not None and pins.get("source_basis") is not None:
        manifest = diagnostic_metadata(pins, "anisette-generated/maintained-source-manifest.json")
        require(pins["owners"]["AnisetteKit"]["commit"] == manifest["anisettekit_revision"],
                "Anisette source basis is bound to another owner revision")
        return manifest
    from patch_anisette_isolated_otp import expected_evidence
    return expected_evidence()


def anisette_source_hashes(manifest):
    key = "sha256" if manifest.get("source_basis") == DIAGNOSTIC_BASIS else "prepared_sha256"
    return {entry["path"]: entry[key] for entry in manifest["files"]}


def load_pins(path):
    document = json.loads(Path(path).read_bytes(), object_pairs_hook=unique_pairs)
    require(document.get("schema_version") == 1 and document.get("integration_baseline") == BASELINE,
            "unsupported maintained-source schema or baseline")
    contract_basis(document)
    require(set(document.get("owners", {})) == OWNERS, "exact seven-owner set required")
    native = document.get("native_validation", {})
    if document.get("source_basis") is not None:
        delta = diagnostic_delta(document)
        require(native.get("source_basis") == DIAGNOSTIC_BASIS and
                native.get("diagnostic_delta_sha256") == DIAGNOSTIC_METADATA["provenance/accepted-to-diagnostic-delta.json"],
                "new diagnostic graph requires its own reviewed native receipt")
        require(type(native.get("run_attempt")) is int and native["run_attempt"] > 0,
                "reviewed diagnostic native run attempt required")
        require(isinstance(native.get("approved_inputs_sha256"), str) and
                re.fullmatch("[0-9a-f]{64}", native["approved_inputs_sha256"]),
                "reviewed diagnostic native input digest required")
        for name, digest in diagnostic_receipt_references(document).items():
            read_diagnostic_reference(document, name, digest)
        for owner, item in document["owners"].items():
            require(item.get("source_checkpoint") == delta["accepted_graph"][owner]["source_checkpoint"],
                    owner + ": frozen source checkpoint changed")
            if owner not in ALLOWED_TRANSITIONS:
                require(item.get("commit") == delta["diagnostic_source_tuple"][owner]["commit"],
                        owner + ": mixed diagnostic source tuple")

    require(native.get("readiness_scope") == "eligible_for_gated_full_build" and
            set(native.get("tested_owners", {})) == {"SideSign", "SideStore"} and
            set(native.get("tested_children", {})) == {"SideSign", "SideStore"},
            "reviewed native validation identity required")
    require(re.fullmatch(r"https://github\.com/NRG-Wardog/LiveContainer/actions/runs/[1-9][0-9]*", native.get("run_url", "")) and
            re.fullmatch("[0-9a-f]{40}", native.get("host_commit", "")) and
            re.fullmatch("[0-9a-f]{40}", native.get("host_tree", "")) and
            re.fullmatch("[0-9a-f]{64}", native.get("artifact_sha256", "")) and
            type(native.get("artifact_id")) is int and native["artifact_id"] > 0,
            "invalid native run/artifact identity")
    for owner, tested in native["tested_owners"].items():
        require(set(tested) == {"commit", "tree"} and all(re.fullmatch("[0-9a-f]{40}", value) for value in tested.values()),
                owner + ": exact native-tested commit/tree required")
    require(native["tested_children"]["SideSign"] == {} and
            native["tested_children"]["SideStore"] == {
                "Dependencies/SideSign": native["tested_owners"]["SideSign"]["commit"],
                "Dependencies/minimuxer": document["owners"]["minimuxer"]["commit"]},
            "native-tested child graph differs from reviewed owners")
    for owner, item in document["owners"].items():
        require(item.get("repository") == "https://github.com/NRG-Wardog/" + owner + ".git",
                owner + ": unexpected repository")
        for field in ("commit", "source_checkpoint"):
            require(isinstance(item.get(field), str) and re.fullmatch("[0-9a-f]{40}", item[field]),
                    owner + ": final published " + field + " is required; pending pins cannot build")
        require(item.get("path") == PATHS[owner], owner + ": checkout must match the actual compiler input path")
    return document


def git(root, *arguments, bare=False):
    root = Path(root).resolve(strict=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1")
    location = ["--git-dir=" + str(root)] if bare else ["--git-dir=" + str(root / ".git"), "--work-tree=" + str(root)]
    return subprocess.check_output(["git", "--no-replace-objects", "-C", str(root), *location,
        "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false", *arguments], env=env, stderr=subprocess.PIPE)


def verify_origin(root, expected_url, commit, resolver_root=None):
    """Verify direct origins, or SwiftPM's bounded checkout -> bare mirror chain.

    Noneditable SwiftPM checkouts use `clone --shared --no-checkout`, retaining
    the local canonical repository as origin. Never rewrite it to appear remote.
    """
    root = Path(root).resolve(strict=True)
    require(git(root, "rev-parse", "HEAD").decode().strip() == commit, "origin proof checkout commit mismatch")
    origin = git(root, "remote", "get-url", "origin").decode().strip()
    if resolver_root is not None:
        resolver_root = Path(resolver_root).resolve(strict=True)
        checkouts = resolver_root / "checkouts"
        require(not checkouts.is_symlink() and root.parent == checkouts,
                "resolver checkout escaped approved storage")
    if origin.removesuffix(".git") == expected_url.removesuffix(".git"):
        return {"kind": "direct", "repository": expected_url}
    require(resolver_root is not None and Path(origin).is_absolute(), "wrong acquisition repository")
    resolver_root = Path(resolver_root).resolve(strict=True)
    checkouts, repositories = resolver_root / "checkouts", resolver_root / "repositories"
    require(not checkouts.is_symlink() and not repositories.is_symlink() and
            root.parent == checkouts, "resolver repository root escaped approved storage")
    mirror_input = Path(origin)
    mirror = mirror_input.resolve(strict=True)
    require(not mirror_input.is_symlink() and mirror.parent == repositories and
            not (mirror / "objects").is_symlink(), "resolver mirror escaped approved storage")
    require(git(mirror, "rev-parse", "--is-bare-repository", bare=True).strip() == b"true", "resolver mirror is not bare")
    upstream = git(mirror, "remote", "get-url", "origin", bare=True).decode().strip()
    require(upstream.removesuffix(".git") == expected_url.removesuffix(".git"), "resolver mirror upstream repository mismatch")
    require(git(mirror, "rev-parse", "--is-shallow-repository", bare=True).strip() == b"false", "shallow resolver mirror")
    # Permit exactly the object-sharing relationship created by clone --shared.
    # A second mirror/cache hop requires separately reviewed native evidence.
    require(not (mirror / "objects/info/alternates").exists(), "unapproved nested resolver object store")
    gitdir = Path(git(root, "rev-parse", "--absolute-git-dir").decode().strip()).resolve(strict=True)
    alternates = gitdir / "objects/info/alternates"
    if alternates.exists():
        require(not alternates.is_symlink(), "substituted resolver object store")
        paths = alternates.read_text().splitlines()
        require(len(paths) == 1 and Path(paths[0]).is_absolute() and
                Path(paths[0]).resolve(strict=True) == mirror / "objects", "resolver object store escaped approved mirror")
    require(git(mirror, "rev-parse", commit + "^{commit}", bare=True).decode().strip() == commit,
            "resolver mirror lacks exact commit")
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    require(git(mirror, "rev-parse", commit + "^{tree}", bare=True).decode().strip() == tree,
            "resolver mirror tree differs from checkout")
    return {"kind": "swiftpm-local-mirror", "repository": expected_url,
            "mirror": str(mirror), "commit": commit, "tree": tree}


def entries(root, revision="HEAD"):
    result = {}
    for row in git(root, "ls-tree", "-rz", revision).split(b"\0"):
        if row:
            head, name = row.split(b"\t", 1)
            result[os.fsdecode(name)] = tuple(head.decode().split())
    return result


def allowed_output(owner, name):
    return any(name.startswith(p) if p.endswith("/") else name == p for p in BUILD_OUTPUTS.get(owner, ()))


def verify_checkout(root, commit, *, owner="dependency", after_build=False, require_full_history=False):
    root = Path(root).resolve(strict=True)
    require((root / ".git").exists(), owner + ": missing actual checkout")
    require(git(root, "rev-parse", "HEAD").decode().strip() == commit, owner + ": wrong commit")
    if require_full_history:
        require(git(root, "rev-parse", "--is-shallow-repository").strip() == b"false", owner + ": shallow owner history")
    records = entries(root)
    children = []
    for name, (mode, kind, oid) in records.items():
        target = root / name
        for parent in target.parents:
            if parent == root:
                break
            require(not parent.is_symlink(), owner + ": substituted parent for " + name)
        if mode == "160000":
            children.append({"path": name, **verify_checkout(target, oid,
                owner=name.split("/")[-1], after_build=after_build)})
            continue
        require(kind == "blob", owner + ": unsupported object")
        if mode == "120000":
            require(target.is_symlink(), owner + ": missing symlink " + name)
            content = os.fsencode(os.readlink(target))
        else:
            require(target.is_file() and not target.is_symlink(), owner + ": missing regular file " + name)
            require(("100755" if target.stat().st_mode & 0o111 else "100644") == mode,
                    owner + ": mode drift " + name)
            content = target.read_bytes()
        require(hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == oid,
                owner + ": compiler input differs from commit: " + name)
    # Include ignored files: an ignored Swift file is still a compiler input.
    extras = git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    extras += git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").split(b"\0")
    for name in filter(None, extras):
        relative = os.fsdecode(name)
        require(after_build and allowed_output(owner, relative), owner + ": unexpected untracked input " + relative)
    require(not git(root, "diff", "--cached", "--name-only"), owner + ": index changed")
    return {"commit": commit, "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
            "tracked_entries": len(records), "submodules": children}


def pin_map(lock):
    result = {}
    for pin in lock["pins"]:
        identity = pin["identity"]
        require(identity not in result, "duplicate SwiftPM identity")
        result[identity] = pin
    return result


def verify_graph(roots, pins):
    owners = pins["owners"]
    side, sign = roots["SideStore"], roots["SideSign"]
    modules = configparser.ConfigParser()
    modules.read(side / ".gitmodules")
    links = entries(side)
    for child in ("SideSign", "minimuxer"):
        name = "Dependencies/" + child
        require(links[name] == ("160000", "commit", owners[child]["commit"]), "wrong SideStore child gitlink")
        require(dict(modules['submodule "' + name + '"']) == {"path": name, "url": owners[child]["repository"]},
                "wrong SideStore child repository or floating branch")
        require(roots[child].resolve() == (side / name).resolve(), "substituted SideStore child root")
    expected = {"identity": "anisettekit", "kind": "remoteSourceControl",
        "location": owners["AnisetteKit"]["repository"], "state": {"revision": owners["AnisetteKit"]["commit"]}}
    for owner, root, lock in (("SideSign", sign, "Package.resolved"), ("SideStore", side, APP_LOCK)):
        actual = json.loads((root / lock).read_bytes())
        original = json.loads(git(root, "show", owners[owner]["source_checkpoint"] + ":" + lock))
        wanted = pin_map(original)
        wanted["anisettekit"] = expected
        require(pin_map(actual) == wanted, owner + ": unrelated SwiftPM pins changed")
        require(actual.get("version") == original.get("version") == 3, "SwiftPM schema drift")
    package = (sign / "Package.swift").read_text()
    require('.package(url: "' + expected["location"] + '", revision: "' + expected["state"]["revision"] + '")' in package,
            "SideSign must use the exact real remote Anisette dependency")
    require('.package(name: "AnisetteKit", path:' not in package, "local Anisette overlay prohibited")
    require(roots["idevice"].parent.resolve() == roots["jktcp"].parent.resolve(), "Rust owners must remain siblings")
    require('path = "../../jktcp"' in (roots["idevice"] / "idevice/Cargo.toml").read_text(), "Rust sibling dependency drift")
    require('path: "LocalBinary/IDevice.xcframework"' in (roots["minimuxer"] / "DeviceGateway/Package.swift").read_text(),
            "minimuxer binary staging dependency drift")


def verify_build_products(roots):
    """Bind the staged iOS slice/header to the actual Rust build outputs."""
    framework = roots["minimuxer"] / "DeviceGateway/LocalBinary/IDevice.xcframework"
    document = plistlib.loads((framework / "Info.plist").read_bytes())
    libraries = document["AvailableLibraries"]
    require(len(libraries) == 1, "exactly one built idevice slice required")
    library = libraries[0]
    require(library.get("SupportedPlatform") == "ios" and not library.get("SupportedPlatformVariant") and
            library.get("SupportedArchitectures") == ["arm64"], "wrong idevice platform/architecture")
    def checked(relative):
        path = framework / relative
        require(path.resolve().is_relative_to(framework.resolve()) and path.is_file() and
                not path.is_symlink() and path.stat().st_size, "escaped/missing idevice build product")
        return path
    def digest(path):
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()
    base = library["LibraryIdentifier"]
    archived = checked(base + "/" + library["LibraryPath"])
    header = checked(base + "/" + library["HeadersPath"] + "/idevice.h")
    module = checked(base + "/" + library["HeadersPath"] + "/module.modulemap")
    expected_archive = roots["idevice"] / "target/aarch64-apple-ios/release/libidevice_ffi.a"
    require(digest(archived) == digest(expected_archive), "staged library differs from built Rust archive")
    for relative in ("ffi/idevice.h", "cpp/include/idevice.h", "swift/include/idevice.h"):
        require(digest(header) == digest(roots["idevice"] / relative), "generated idevice header mismatch")
    require(digest(module) == digest(roots["idevice"] / "swift/include/module.modulemap"), "staged module map mismatch")
    return {"library_sha256": digest(archived), "header_sha256": digest(header),
            "module_map_sha256": digest(module), "framework_info_sha256": digest(framework / "Info.plist")}


def verify_resolution(roots, anisette):
    """Check the real Xcode workspace graph, including every remote checkout."""
    state_path = Path(anisette).parent.parent / "workspace-state.json"
    state = json.loads(state_path.read_bytes())["object"]["dependencies"]
    expected = pin_map(json.loads((roots["SideStore"] / APP_LOCK).read_bytes()))
    local = {"sidesign": roots["SideSign"], "minimuxer": roots["minimuxer"],
             "common": roots["minimuxer"] / "Common", "devicegateway": roots["minimuxer"] / "DeviceGateway"}
    seen, report = set(), {}
    for dep in state:
        ref = dep["packageRef"]
        identity = ref["identity"].lower()
        require(identity not in seen, "duplicate resolved dependency")
        seen.add(identity)
        if identity in local:
            require(ref["kind"] == "fileSystem" and Path(ref["location"]).is_absolute() and
                    Path(ref["location"]).resolve() == local[identity].resolve(), "wrong local package: " + identity)
            report[identity] = {"kind": "fileSystem", "path": str(local[identity].resolve())}
        else:
            require(identity in expected, "unreviewed transitive package: " + identity)
            pin = expected[identity]
            require(ref["kind"] == "remoteSourceControl" and ref["location"] == pin["location"] and
                    dep["state"]["name"] == "sourceControlCheckout" and
                    dep["state"]["checkoutState"]["revision"] == pin["state"]["revision"], "resolved remote pin mismatch")
            checkout = state_path.parent / "checkouts" / dep["subpath"]
            require(checkout.resolve().is_relative_to((state_path.parent / "checkouts").resolve()), "escaped remote checkout")
            if identity == "anisettekit":
                require(checkout.resolve() == Path(anisette).resolve(), "effective Anisette checkout mismatch")
            report[identity] = verify_checkout(checkout, pin["state"]["revision"], owner=identity)
            report[identity]["origin"] = verify_origin(checkout, pin["location"],
                pin["state"]["revision"], resolver_root=state_path.parent)
    require(seen == set(expected) | set(local), "resolver omitted expected dependencies")
    return report


def verify_native_lock_descendant(root, tested, final, receipt):
    """Admit only the exact app lock observed during the ancestor's real build."""
    require(git(root, "rev-parse", tested["commit"] + "^{tree}").decode().strip() == tested["tree"] and
            git(root, "rev-parse", final["commit"] + "^{tree}").decode().strip() == final["tree"],
            "native-tested or final SideStore tree differs")
    git(root, "merge-base", "--is-ancestor", tested["commit"], final["commit"])
    before, after = entries(root, tested["commit"]), entries(root, final["commit"])
    changes = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    require(changes == {APP_LOCK}, "post-native SideStore transition is not exactly the observed app lock")
    for inventory, evidence_name in ((before, "provenance/Package.resolved.before"),
                                      (after, "provenance/Package.resolved.after")):
        entry = inventory.get(APP_LOCK)
        require(entry is not None and entry[:2] == ("100644", "blob"),
                "native app lock type or mode differs")
        digest = hashlib.sha256(git(root, "cat-file", "blob", entry[2])).hexdigest()
        require(digest == receipt["evidence_sha256"][evidence_name],
                "post-native app lock differs from actual resolver evidence")
    require(receipt["locks"]["SideStore"]["sha256"] ==
            receipt["evidence_sha256"]["provenance/Package.resolved.after"],
            "native-tested app lock differs from observed after bytes")


def verify_focused_diagnostic_receipt(pins):
    _, focused = read_diagnostic_reference(pins, "provenance/adi-focused-native-verification.json",
                                           pins["native_validation"].get("focused_receipt_sha256"))
    require(focused.get("status") == "PASS" and focused.get("source_snapshots_byte_identical") is True and
            focused.get("tests") == {"AnisetteKit": 7, "SideStore_with_LiveContainer_peer": 5,
                                      "failures": 0, "skips": 0} and
            all(type(value) is int for value in focused["tests"].values()),
            "focused diagnostic native proof is missing or incomplete")
    delta = diagnostic_delta(pins)
    sources = focused.get("owner_sources", {})
    require(set(sources) == {"AnisetteKit", "SideStore", "LiveContainer"} and
            all(sources[owner].get("commit") == delta["diagnostic_source_tuple"][owner]["commit"] and
                sources[owner].get("tree") == delta["diagnostic_source_tuple"][owner]["tree"] and
                sources[owner].get("repository") == pins["owners"][owner]["repository"] for owner in sources),
            "focused diagnostic proof belongs to another producer/decoder tuple")


def verify_diagnostic_readiness(proofs, pins, verified_owners, roots=None):
    native = pins["native_validation"]
    verify_focused_diagnostic_receipt(pins)
    _, receipt = read_diagnostic_reference(pins, "provenance/diagnostic-native-readiness.json",
                                           native.get("receipt_sha256"))
    keys = {"schema_version", "status", "source_basis", "diagnostic_delta_sha256", "phase",
            "run_url", "run_id", "run_attempt", "validation_host_commit", "validation_host_tree",
            "artifact_id", "artifact_zip_sha256", "approved_inputs_sha256", "tested_owners",
            "tested_children", "locks", "native_builds", "readiness_scope",
            "final_exact_ref_ipa_build_required", "evidence_sha256"}
    require(set(receipt) == keys and type(receipt["schema_version"]) is int and
            receipt["schema_version"] == 1 and receipt["status"] == "PASS" and
            receipt["source_basis"] == DIAGNOSTIC_BASIS and
            receipt["diagnostic_delta_sha256"] == native["diagnostic_delta_sha256"] and
            receipt["phase"] == "diagnostic_permanent_graph_iphoneos" and
            receipt["readiness_scope"] == "eligible_for_gated_full_build" and
            receipt["final_exact_ref_ipa_build_required"] is True,
            "diagnostic native receipt identity or eligibility differs")
    require(type(receipt["run_id"]) is int and receipt["run_id"] > 0 and
            type(receipt["run_attempt"]) is int and receipt["run_attempt"] > 0 and
            type(receipt["artifact_id"]) is int and receipt["artifact_id"] > 0 and
            receipt["run_url"] == "https://github.com/NRG-Wardog/LiveContainer/actions/runs/" + str(receipt["run_id"]),
            "diagnostic native run identity differs")
    for observed, approved in (("run_url", "run_url"), ("validation_host_commit", "host_commit"),
                              ("validation_host_tree", "host_tree"), ("artifact_id", "artifact_id"),
                              ("artifact_zip_sha256", "artifact_sha256"), ("run_attempt", "run_attempt")):
        require(receipt[observed] == native[approved], "diagnostic native run/artifact binding differs")
    require(isinstance(verified_owners, dict) and set(verified_owners) == OWNERS and
            isinstance(receipt["tested_owners"], dict) and set(receipt["tested_owners"]) == OWNERS,
            "diagnostic native receipt requires all seven acquired owners")
    for owner, tested in receipt["tested_owners"].items():
        require(isinstance(tested, dict) and set(tested) == {"commit", "tree"} and
                all(isinstance(v, str) and re.fullmatch("[0-9a-f]{40}", v) for v in tested.values()),
                "invalid diagnostic native-tested source identity")
        final = {field: verified_owners[owner][field] for field in ("commit", "tree")}
        require(final["commit"] == pins["owners"][owner]["commit"], "seven acquired owners differ from final pins")
        if tested != final:
            require(owner == "SideStore" and roots is not None and owner in roots,
                    "diagnostic native receipt is not for the exact seven acquired owners")
            verify_native_lock_descendant(roots[owner], tested, final, receipt)
    require(receipt["tested_children"] == native["tested_children"] and
            all(receipt["tested_owners"][owner] == native["tested_owners"][owner] for owner in proofs),
            "diagnostic native tested owner/child graph differs")
    require(receipt["native_builds"] == {owner: "PASS_UNSIGNED_RELEASE_IPHONEOS26.4"
            for owner in ("LiveContainer", "SideStore")}, "both diagnostic native app builds are required")
    evidence = {"provenance/" + name for name in (
        "production-phase-status.json", "reviewed-production-inputs.json", "reviewed-toolchain.json",
        "Package.resolved.before", "Package.resolved.after", "source-proof-before.json",
        "source-proof-after-resolution.json", "source-proof-after-build.json", "resolution.json",
        "resolution-after-build.json", "compiler-input-proof.json", "generated-sources-proof.json",
        "local-framework-before-build.json", "local-framework-after-build.json", "local-ffi-link-hashes.json",
        "binary-artifacts.json", "sidestore-native-tests.json", "sidestore-historical-native-tests.json",
        "sidestore-historical-source-before.json", "sidestore-historical-source-after.json")}
    evidence |= {"logs/" + owner + "-production-build" + suffix
                 for owner in ("sidestore", "livecontainer") for suffix in (".log", ".exit-code.txt")}
    require(isinstance(receipt["evidence_sha256"], dict) and set(receipt["evidence_sha256"]) == evidence and
            all(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)
                for value in [receipt["approved_inputs_sha256"], *receipt["evidence_sha256"].values()]),
            "diagnostic native evidence inventory or digest differs")
    require(receipt["approved_inputs_sha256"] == native.get("approved_inputs_sha256") ==
            receipt["evidence_sha256"]["provenance/reviewed-production-inputs.json"],
            "diagnostic native approved inputs differ from retained evidence")
    require(set(receipt["locks"]) == set(proofs), "both native-tested dependency locks required")
    for owner, proof in proofs.items():
        references = pins["diagnostic_dependencies"][owner]
        require(proof.get("owner") == owner and proof.get("commit") == pins["owners"][owner]["commit"] and
                proof.get("tree") == verified_owners[owner]["tree"] and
                proof.get("status") == "diagnostic_dependency_transition_pass" and
                proof.get("production_ready") is False and
                proof.get("readiness_scope") == "source_transition_only_requires_separate_native_receipt" and
                proof.get("source_registry_sha256") == DIAGNOSTIC_REGISTRY and
                proof.get("diagnostic_basis_sha256") == references["basis_sha256"] and
                proof.get("resolver_receipt_sha256") == references["resolver_receipt_sha256"] and
                proof.get("native_resolver_status") == "reviewed_diagnostic_resolution" and
                proof.get("lock_status") == "reviewed_resolver_observed_lock",
                owner + ": exact diagnostic source and actual resolver proof required")
        lock = receipt["locks"][owner]
        require(isinstance(lock, dict) and set(lock) == {"sha256", "origin_hash"} and
                isinstance(lock["sha256"], str) and re.fullmatch("[0-9a-f]{64}", lock["sha256"]) and
                lock["sha256"] == proof.get("lock_sha256") and lock["origin_hash"] == proof.get("origin_hash"),
                owner + ": native-tested lock differs from owner resolver proof")
        origin = lock["origin_hash"]
        require(isinstance(origin, dict) and set(origin) == {"present", "value"} and
                type(origin["present"]) is bool and
                ((origin["present"] and isinstance(origin["value"], str) and re.fullmatch("[0-9a-f]{64}", origin["value"])) or
                 (not origin["present"] and origin["value"] is None)), "invalid observed originHash state")


def verify_native_readiness(proofs, pins, verified_owners=None, roots=None):
    """Bind final metadata-only owner transitions to the same reviewed native run."""
    require(set(proofs) == {"SideSign", "SideStore"}, "both native readiness proofs are required")
    if pins.get("source_basis") is not None:
        return verify_diagnostic_readiness(proofs, pins, verified_owners, roots)
    native = pins["native_validation"]
    for owner, proof in proofs.items():
        require(proof.get("owner") == owner and proof.get("commit") == pins["owners"][owner]["commit"],
                owner + ": proof is not for the final pinned owner")
        require(proof.get("status") == "exact_dependency_transition_pass" and
                proof.get("production_ready") is True and
                proof.get("readiness_scope") == native["readiness_scope"],
                owner + ": verified native-tested lineage is required for the gated full build")
        expected = native["tested_owners"][owner]
        require(proof.get("native_tested_commit") == expected["commit"] and
                proof.get("native_tested_tree") == expected["tree"],
                owner + ": native-tested identity differs from approved evidence")
        require(proof.get("native_run_url") == native["run_url"] and
                proof.get("native_validation_host_commit") == native["host_commit"] and
                proof.get("native_artifact_sha256") == native["artifact_sha256"],
                owner + ": native run/host/artifact differs from approved evidence")
        require(re.fullmatch("[0-9a-f]{64}", proof.get("native_receipt_sha256", "")),
                owner + ": verified native receipt digest is missing")
        require(proof.get("native_tested_children") == native["tested_children"][owner],
                owner + ": native-tested child graph differs from approved evidence")
    require(proofs["SideStore"]["native_run_url"] == proofs["SideSign"]["native_run_url"] and
            proofs["SideStore"]["native_tested_children"]["Dependencies/SideSign"] == proofs["SideSign"]["native_tested_commit"],
            "SideStore and SideSign native receipt lineage is not linked")


def verify_all(workspace, pins, anisette=None, after_build=False):
    roots = {name: workspace / value["path"] for name, value in pins["owners"].items()}
    if anisette is not None:
        roots["AnisetteKit"] = Path(anisette)
    results = {}
    for owner, root in roots.items():
        spec = pins["owners"][owner]
        results[owner] = verify_checkout(root, spec["commit"], owner=owner, after_build=after_build,
                                         require_full_history=True)
        verify_source_transition(root, owner, spec, pins)
        resolver_root = Path(anisette).parent.parent if owner == "AnisetteKit" and anisette is not None else None
        results[owner]["origin"] = verify_origin(root, spec["repository"], spec["commit"], resolver_root)
    verify_graph(roots, pins)
    if not after_build:
        proofs = {}
        for owner in ("SideSign", "SideStore"):
            command = [sys.executable, "-B", str(roots[owner] / ".ci/production-dependencies.py"),
                       "--root", str(roots[owner])]
            if pins.get("source_basis") is not None:
                for kind, argument in (("basis", "--diagnostic-basis"), ("resolver", "--diagnostic-resolver-receipt")):
                    name, digest = diagnostic_dependency_reference(pins, owner, kind)
                    path, _ = read_diagnostic_reference(pins, name, digest)
                    command += [argument, str(path), argument + "-sha256", digest]
            proof = subprocess.run(command, check=True, capture_output=True, text=True)
            proofs[owner] = json.loads(proof.stdout, object_pairs_hook=unique_pairs)
        verify_native_readiness(proofs, pins, results, roots)
    directory, registry = contract_basis(pins)
    command = [sys.executable, "-B", str(ROOT / "migration/contracts/validate_contracts.py"),
        "--registry", str(directory / "compatibility-registry.json"), "--registry-sha256", registry]
    for owner, root in roots.items():
        command += ["--owner", owner + "=" + str(root.resolve())]
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return {"schema_version": 1, "integration_baseline": BASELINE, "owners": results,
            "contracts": json.loads(result.stdout), "runtime_rewrites": False,
            "approved_native_validation": pins["native_validation"],
            "resolved_dependencies": verify_resolution(roots, anisette) if anisette is not None else None,
            "built_idevice": verify_build_products(roots) if after_build else None}


def acquire(workspace, pins):
    for owner in ("LiveContainer", "SideStore", "AnisetteKit", "idevice", "jktcp"):
        spec = pins["owners"][owner]
        root = workspace / spec["path"]
        require(not root.exists(), "refusing to overwrite checkout: " + str(root))
        root.parent.mkdir(parents=True, exist_ok=True)
        # Acquire full objects/history now; proof commands disable lazy fetching.
        subprocess.run(["git", "clone", "--no-checkout", spec["repository"], str(root)], check=True)
        git(root, "fetch", "--no-tags", "origin", spec["commit"])
        git(root, "checkout", "--detach", spec["commit"])
        if owner == "LiveContainer":
            git(root, "-c", "submodule.litehook.url=https://github.com/LiveContainerMirror/litehook.git",
                "submodule", "update", "--init", "--recursive")
        else:
            git(root, "submodule", "update", "--init", "--recursive")


def anisette_evidence(root, pins):
    verify_checkout(root, pins["owners"]["AnisetteKit"]["commit"], owner="AnisetteKit", require_full_history=True)
    verify_origin(root, pins["owners"]["AnisetteKit"]["repository"], pins["owners"]["AnisetteKit"]["commit"],
                  resolver_root=root.parent.parent if root.parent.name == "checkouts" else None)
    expected = expected_anisette_evidence(pins)
    for name, digest in anisette_source_hashes(expected).items():
        require(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest,
                "Anisette prepared-source hash mismatch")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("env", "acquire", "verify", "anisette"))
    parser.add_argument("--pins", type=Path, default=ROOT / "migration/maintained-sources.json")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--anisette-source", type=Path)
    parser.add_argument("--after-build", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    pins = load_pins(args.pins)
    if args.action == "env":
        print("\n".join(ENV_KEYS[owner] + "=" + pins["owners"][owner]["commit"] for owner in sorted(OWNERS)))
        return
    if args.action == "acquire":
        acquire(args.workspace, pins)
    if args.action == "anisette":
        require(args.anisette_source is not None, "effective Anisette source required")
        result = anisette_evidence(args.anisette_source, pins)
    else:
        result = verify_all(args.workspace, pins, args.anisette_source, args.after_build)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload)
    print(payload)


if __name__ == "__main__":
    main()
