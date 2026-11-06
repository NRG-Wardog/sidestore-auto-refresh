#!/usr/bin/env python3
"""Read-only feature inventory and upstream change impact review.

This is a maintenance map, not a replacement for pinned-source, ABI, native,
artifact or device gates. Listed regression commands are data and never executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / 'ci/issue52-integration/contracts.json'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON field: ' + key)
        result[key] = value
    return result


def text(value):
    return isinstance(value, str) and bool(value.strip())


def path_name(value):
    require(text(value), 'Source path must be nonempty text')
    path = PurePosixPath(value)
    require(bool(path.parts) and not path.is_absolute() and path.as_posix() == value and
            all(part not in {'.', '..', '.git'} for part in path.parts) and
            not any(char in value for char in ('\\', '\x00', '\n', '\r', '*', '?', '[')),
            'Unsafe or noncanonical source path: ' + repr(value))
    return value


def validate_registry(registry):
    require(isinstance(registry, dict) and set(registry) == {'schema_version', 'owners', 'features'},
            'Integration registry fields differ')
    require(type(registry['schema_version']) is int and registry['schema_version'] == 1,
            'Unsupported integration registry version')
    owners = registry['owners']
    require(isinstance(owners, dict) and bool(owners), 'Owners must be a nonempty object')
    for name, owner in owners.items():
        require(re.fullmatch(r'[A-Za-z0-9_-]+', name) is not None, 'Invalid owner name')
        require(isinstance(owner, dict) and set(owner) == {'repository', 'graph_key'}, 'Owner fields differ: ' + name)
        require(isinstance(owner['repository'], str) and
                re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', owner['repository']) is not None,
                'Invalid owner repository: ' + name)
        require(owner['graph_key'] is None or text(owner['graph_key']), 'Invalid graph key: ' + name)
    features = registry['features']
    require(isinstance(features, list) and bool(features), 'Features must be a nonempty list')
    ids = set()
    used_owners = set()
    for feature in features:
        require(isinstance(feature, dict) and set(feature) == {
            'id', 'contract', 'limitations', 'modules', 'integration_points', 'regressions'}, 'Feature fields differ')
        name = feature['id']
        require(isinstance(name, str) and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name) is not None and name not in ids,
                'Invalid or duplicate feature ID')
        ids.add(name)
        require(text(feature['contract']) and text(feature['limitations']), 'Feature contract or limits missing: ' + name)
        for kind in ('modules', 'integration_points', 'regressions'):
            rows = feature[kind]
            require(isinstance(rows, list) and bool(rows), 'Empty ' + kind + ': ' + name)
            seen = set()
            for row in rows:
                fields = {'owner', 'path', 'interface', 'rationale'} if kind != 'regressions' else {'owner', 'path', 'command', 'level'}
                require(isinstance(row, dict) and set(row) == fields, 'Reference fields differ: ' + name)
                require(isinstance(row['owner'], str) and row['owner'] in owners, 'Unknown owner: ' + name)
                path_name(row['path'])
                key = (row['owner'], row['path'])
                require(key not in seen, 'Duplicate reference: ' + name + ':' + row['path'])
                seen.add(key)
                used_owners.add(row['owner'])
                if kind == 'regressions':
                    require(text(row['command']) and isinstance(row['level'], str) and row['level'] in {'source-contract', 'executable-fixture', 'native'},
                            'Regression command or level invalid: ' + name)
                else:
                    require(text(row['interface']) and text(row['rationale']), 'Interface or rationale missing: ' + name)
    require(used_owners == set(owners), 'Owner without a documented boundary')
    return registry


def load_registry(path=DEFAULT_REGISTRY):
    data = Path(path).read_bytes()
    require(len(data) <= 1024 * 1024, 'Integration registry exceeds review bound')
    return validate_registry(json.loads(data, object_pairs_hook=unique_pairs))


def references(registry):
    rows = {}
    for feature in registry['features']:
        for kind in ('modules', 'integration_points', 'regressions'):
            for row in feature[kind]:
                rows.setdefault((row['owner'], row['path']), []).append((feature['id'], kind))
    return rows


def regular_file(root, name):
    root = Path(root)
    require(not root.is_symlink() and root.is_dir(), 'Missing or linked owner root: ' + str(root))
    path = root
    for part in PurePosixPath(path_name(name)).parts:
        path = path / part
        require(not path.is_symlink(), 'Linked inventory path: ' + name)
    require(path.is_file(), 'Missing inventory file: ' + name)
    return path


def validate_inventory(registry, roots):
    """Verify every documented path exists, without importing or running owners."""
    validate_registry(registry)
    require(set(roots) == set(registry['owners']), 'Exactly all registered owner roots are required')
    checked = []
    for (owner, name), usage in sorted(references(registry).items()):
        source = regular_file(roots[owner], name)
        checked.append({'owner': owner, 'path': name,
                        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'features': sorted({feature for feature, _ in usage})})
    return {'schema_version': 1, 'result': 'INVENTORY_PATHS_PASS_NOT_BEHAVIOR_VALIDATION',
            'owners': sorted(roots), 'files': checked, 'regressions_executed': False,
            'native_compilation': False, 'physical_device_validation': False}


def plan_changes(registry, owner, changes):
    """Exact-path classification only; unknown paths and removals require review."""
    validate_registry(registry)
    require(isinstance(owner, str) and owner in registry['owners'], 'Unknown changed owner')
    index = references(registry)
    affected = set()
    classified, unclassified, removed, gitlinks, mode_changes = [], [], [], [], []
    require(isinstance(changes, list), 'Changes must be a list')
    seen = set()
    for change in changes:
        require(isinstance(change, dict) and set(change) == {'path', 'status', 'old_mode', 'new_mode'}, 'Change fields differ')
        path = path_name(change['path'])
        require(path not in seen, 'Duplicate changed path')
        seen.add(path)
        require(isinstance(change['status'], str) and change['status'] in {'A', 'M', 'D', 'T'}, 'Unsupported change status')
        require(all(isinstance(change[key], str) and re.fullmatch(r'[0-7]{6}', change[key])
                    for key in ('old_mode', 'new_mode')), 'Invalid changed mode')
        old_zero, new_zero = change['old_mode'] == '000000', change['new_mode'] == '000000'
        require((change['status'] == 'A' and old_zero and not new_zero) or
                (change['status'] == 'D' and not old_zero and new_zero) or
                (change['status'] in {'M', 'T'} and not old_zero and not new_zero),
                'Change status contradicts file modes')
        matches = index.get((owner, path), [])
        if matches:
            affected.update(feature for feature, _ in matches)
            classified.append(dict(change, features=sorted({feature for feature, _ in matches}),
                                   roles=sorted({kind for _, kind in matches})))
        else:
            unclassified.append(path)
        if change['status'] == 'D':
            removed.append(path)
        if '160000' in (change['old_mode'], change['new_mode']):
            gitlinks.append(path)
        if change['status'] == 'T' or any(mode not in {'000000', '100644', '100755'} for mode in (change['old_mode'], change['new_mode'])):
            mode_changes.append(path)
    regressions = []
    for feature in registry['features']:
        if feature['id'] in affected:
            regressions.extend(dict(row, feature=feature['id']) for row in feature['regressions'])
    needs_review = bool(unclassified or removed or gitlinks or mode_changes)
    return {'schema_version': 1, 'owner': owner,
            'result': 'MANUAL_REVIEW_REQUIRED' if needs_review else 'IMPACT_CLASSIFIED_NOT_TESTED',
            'classified': classified, 'unclassified': sorted(unclassified),
            'removed_paths': sorted(removed), 'gitlink_changes': sorted(gitlinks),
            'structural_changes': sorted(mode_changes), 'affected_features': sorted(affected), 'required_regressions': regressions,
            'regressions_executed': False, 'native_compilation': False, 'physical_device_validation': False}


def git(repository, *args):
    # Clear inherited Git routing/config overrides; read local immutable objects
    # only, with no external diff, textconv, replacement refs, or lazy fetch.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='', GIT_TERMINAL_PROMPT='0')
    return subprocess.run(['git', '--no-pager', '-C', str(repository), *args], env=env,
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout


def compare_commits(registry, owner, repository, base, candidate):
    for commit in (base, candidate):
        require(isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit), 'Full immutable commit required')
        require(git(repository, 'cat-file', '-t', commit).strip() == b'commit', 'Input is not a commit')
    # --no-renames deliberately reports both old/deleted and new/added paths.
    raw = git(repository, 'diff', '--raw', '-z', '--no-renames', '--no-ext-diff', '--no-textconv', '--ignore-submodules=none', base, candidate, '--')
    fields = raw.split(b'\0')
    require(fields.pop() == b'' and len(fields) % 2 == 0, 'Malformed Git change list')
    changes = []
    for header, name in zip(fields[::2], fields[1::2]):
        match = re.fullmatch(rb':([0-7]{6}) ([0-7]{6}) [0-9a-f]+ [0-9a-f]+ ([AMDT])', header)
        require(match is not None, 'Unsupported Git change record')
        old, new, status = (value.decode('ascii') for value in match.groups())
        changes.append({'path': name.decode('utf-8'), 'status': status, 'old_mode': old, 'new_mode': new})
    result = plan_changes(registry, owner, changes)
    result.update(base_commit=base, candidate_commit=candidate,
                  base_tree=git(repository, 'rev-parse', base + '^{tree}').decode().strip(),
                  candidate_tree=git(repository, 'rev-parse', candidate + '^{tree}').decode().strip())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check-registry')
    validate = commands.add_parser('validate')
    validate.add_argument('--owner-root', action='append', default=[], metavar='OWNER=PATH')
    impact = commands.add_parser('plan')
    impact.add_argument('--owner', required=True)
    impact.add_argument('--repository', type=Path, required=True)
    impact.add_argument('--base', required=True)
    impact.add_argument('--candidate', required=True)
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry)
        if args.command == 'check-registry':
            result = {'schema_version': 1, 'result': 'REGISTRY_SCHEMA_PASS_NOT_SOURCE_VALIDATION',
                      'features': len(registry['features']), 'owners': sorted(registry['owners'])}
        elif args.command == 'validate':
            roots = {}
            for value in args.owner_root:
                owner, separator, path = value.partition('=')
                require(separator and owner not in roots and path, 'Invalid or duplicate owner root')
                roots[owner] = Path(path)
            result = validate_inventory(registry, roots)
        else:
            result = compare_commits(registry, args.owner, args.repository, args.base, args.candidate)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 3 if result['result'] == 'MANUAL_REVIEW_REQUIRED' else 0
    except (ValueError, OSError, subprocess.SubprocessError, UnicodeError) as error:
        print('Integration review failed: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
