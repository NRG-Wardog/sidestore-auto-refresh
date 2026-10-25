#!/usr/bin/env python3
"""Read-only local Git inventory. Never publishes, resolves or grants readiness."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

BASELINE = '767234eb704745ce915c0750eee2c31061ac4f78'
BASES = {
 'AnisetteKit': 'f494494ede88890555df345054f7fbb87b53aea5',
 'SideSign': '45039f2c16348e098a69f4421719798b00f00429',
 'SideStore': '4625e29929aa52fe33327c07076bfae0358a7bf0',
 'LiveContainer': '6b128686477b569a936915abaa4bd2ec51b40c14',
 'minimuxer': 'efbcab05d7d636aa37c6bf6c7f364d122c5610f6',
 'idevice': '803f9368e6b27a43d8a9f22dc0ec281c87883113',
 'jktcp': '32c0210f286b4fd16efe05be4f57167a9d94459b',
}
LOCK = 'AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved'

def require(value, message):
    if not value:
        raise ValueError(message)

def unique(pairs):
    result = {}
    for k, v in pairs:
        require(k not in result, 'duplicate JSON key: ' + k)
        result[k] = v
    return result

def full_sha(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) and value != '0'*40,
            'full real commit SHA required; null/placeholder/ref forbidden')
    return value

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

def entries(root, commit):
    result = {}
    for row in git(root, 'ls-tree', '-r', '-z', full_sha(commit)).split(b'\0'):
        if not row:
            continue
        meta, path = row.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        entry = {'mode': mode, 'type': kind, 'git_object': oid}
        if kind == 'blob':
            entry['sha256'] = hashlib.sha256(git(root, 'cat-file', 'blob', oid)).hexdigest()
        result[path.decode()] = entry
    return result

def snapshot(root, base, commit):
    for revision in (base, commit):
        full_sha(revision)
        require(git(root, 'cat-file', '-t', revision).strip() == b'commit', 'not a Git commit')
    git(root, 'merge-base', '--is-ancestor', base, commit)
    old, new = entries(root, base), entries(root, commit)
    return {
      'commit': commit, 'tree': git(root, 'rev-parse', commit+'^{tree}').decode().strip(),
      'baseline_commit': base,
      'changes': {p: {'before': old.get(p), 'after': new.get(p)}
                  for p in sorted(old.keys() | new.keys()) if old.get(p) != new.get(p)}
    }

def read_blob(root, commit, path):
    return git(root, 'show', commit + ':' + path)

def lock_pin(root, commit, path, expected):
    raw = read_blob(root, commit, path)
    lock = json.loads(raw, object_pairs_hook=unique)
    pins = [p for p in lock['pins'] if p.get('identity') == 'anisettekit']
    require(len(pins) == 1, 'exactly one AnisetteKit pin required')
    pin = pins[0]
    require(pin.get('location') == 'https://github.com/NRG-Wardog/AnisetteKit.git', 'wrong AnisetteKit origin')
    require(pin.get('state', {}).get('revision') == expected, 'AnisetteKit lock revision not candidate')
    return {'sha256': hashlib.sha256(raw).hexdigest(),
            'origin_hash': {'present': 'originHash' in lock, 'value': lock.get('originHash')}}

def prepare(config):
    require(set(config) == {'integration_baseline', 'owners'}, 'unexpected input keys')
    require(config['integration_baseline'] == BASELINE, 'wrong integration baseline')
    require(set(config['owners']) == set(BASES), 'exact seven-owner graph required')
    result = {}
    for name, base in BASES.items():
        item = config['owners'][name]
        require(set(item) == {'local_repository', 'commit'}, name + ': unexpected owner input')
        root, commit = Path(item['local_repository']), full_sha(item['commit'])
        require(root.is_dir(), name + ': local repository absent')
        origin = git(root, 'remote', 'get-url', 'origin').decode().strip()
        require(origin == 'https://github.com/NRG-Wardog/'+name+'.git', name + ': unexpected remote')
        result[name] = snapshot(root, base, commit)
        result[name]['repository'] = origin
    for name in ('AnisetteKit', 'SideSign', 'SideStore', 'LiveContainer'):
        require(result[name]['commit'] != BASES[name], name + ': historical owner cannot stand in for P0 candidate')
    for name in ('minimuxer', 'idevice', 'jktcp'):
        require(result[name]['commit'] == BASES[name], name + ': unexpected unrelated transport change')
    roots = {n: Path(v['local_repository']) for n, v in config['owners'].items()}
    ak = result['AnisetteKit']['commit']
    manifest = read_blob(roots['SideSign'], result['SideSign']['commit'], 'Package.swift').decode()
    require(re.search(r'\.package\(url:\s*"https://github.com/NRG-Wardog/AnisetteKit.git",\s*revision:\s*"'+ak+r'"\)', manifest), 'SideSign manifest must pin actual candidate')
    ss = entries(roots['SideStore'], result['SideStore']['commit'])
    for name in ('SideSign', 'minimuxer'):
        require(ss.get('Dependencies/'+name) == {'mode': '160000', 'type': 'commit', 'git_object': result[name]['commit']}, 'SideStore child gitlink mismatch: '+name)
    locks = {name: lock_pin(roots[name], result[name]['commit'], path, ak)
             for name, path in (('SideSign', 'Package.resolved'), ('SideStore', LOCK))}
    # This proves byte identities ONLY. Genuine resolution, publication and native
    # execution require independent reviewed records, never this generated JSON.
    return {'schema_version': 1, 'purpose': 'p0_candidate_local_source_inventory_for_review',
            'integration_baseline': BASELINE, 'owners': result, 'locks': locks,
            'publication_verified': False, 'resolver_execution_verified': False,
            'native_validation': None, 'ipa_checkpoint': None, 'production_ready': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.input.read_text(), object_pairs_hook=unique)
        print(json.dumps(prepare(config), indent=2, sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, 'BLOCKED: '+str(error)+'\n')

if __name__ == '__main__':
    main()
