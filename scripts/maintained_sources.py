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
REGISTRY = "8e7eba95b8bc69037ffed8931478cefd46984b458f767c2a067547e3cd60b467"
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


def load_pins(path):
    document = json.loads(Path(path).read_bytes(), object_pairs_hook=unique_pairs)
    require(document.get("schema_version") == 1 and document.get("integration_baseline") == BASELINE,
            "unsupported maintained-source schema or baseline")
    require(document.get("contract_registry_sha256") == REGISTRY, "unapproved contract registry")
    require(set(document.get("owners", {})) == OWNERS, "exact seven-owner set required")
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


def verify_all(workspace, pins, anisette=None, after_build=False):
    roots = {name: workspace / value["path"] for name, value in pins["owners"].items()}
    if anisette is not None:
        roots["AnisetteKit"] = Path(anisette)
    results = {}
    for owner, root in roots.items():
        spec = pins["owners"][owner]
        results[owner] = verify_checkout(root, spec["commit"], owner=owner, after_build=after_build,
                                         require_full_history=True)
        git(root, "merge-base", "--is-ancestor", spec["source_checkpoint"], spec["commit"])
        changes = set(filter(None, git(root, "diff", "--name-only", spec["source_checkpoint"], spec["commit"]).decode().splitlines()))
        require(changes <= ALLOWED_TRANSITIONS.get(owner, set()), owner + ": product source changed after approved parity checkpoint")
        resolver_root = Path(anisette).parent.parent if owner == "AnisetteKit" and anisette is not None else None
        results[owner]["origin"] = verify_origin(root, spec["repository"], spec["commit"], resolver_root)
    verify_graph(roots, pins)
    if not after_build:
        for owner in ("SideSign", "SideStore"):
            proof = subprocess.run([sys.executable, "-B", str(roots[owner] / ".ci/production-dependencies.py"),
                                    "--root", str(roots[owner])], check=True, capture_output=True, text=True)
            approved = json.loads(proof.stdout)
            require(approved.get("status") == "exact_dependency_transition_pass" and
                    approved.get("production_ready") is True,
                    owner + ": published native-resolved dependency proof is required")
    command = [sys.executable, "-B", str(ROOT / "migration/contracts/validate_contracts.py"),
        "--registry", str(ROOT / "migration/contracts/compatibility-registry.json"), "--registry-sha256", REGISTRY]
    for owner, root in roots.items():
        command += ["--owner", owner + "=" + str(root.resolve())]
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return {"schema_version": 1, "integration_baseline": BASELINE, "owners": results,
            "contracts": json.loads(result.stdout), "runtime_rewrites": False,
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
    from patch_anisette_isolated_otp import expected_evidence
    verify_checkout(root, pins["owners"]["AnisetteKit"]["commit"], owner="AnisetteKit", require_full_history=True)
    verify_origin(root, pins["owners"]["AnisetteKit"]["repository"], pins["owners"]["AnisetteKit"]["commit"],
                  resolver_root=root.parent.parent if root.parent.name == "checkouts" else None)
    expected = expected_evidence()
    for item in expected["files"]:
        require(hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["prepared_sha256"],
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
