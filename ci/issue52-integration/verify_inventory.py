#!/usr/bin/env python3
"""Bind maintenance inventory paths to the existing reviewed candidate graph.

Acquisition is the workflow's explicit HTTPS Git step. This checker never fetches
or executes any acquired source or test command.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from integration_contracts import git, load_registry, require, unique_pairs, validate_inventory


def source_plan(registry, graph):
    result = []
    require(isinstance(graph, dict) and isinstance(graph.get('owners'), dict), 'Missing candidate owners')
    for owner, metadata in registry['owners'].items():
        key = metadata['graph_key']
        if key is None:
            require(owner == 'sidestore-auto-refresh', 'Only builder may omit graph key')
            continue
        row = graph['owners'].get(key)
        require(isinstance(row, list) and len(row) == 2, 'Missing graph owner: ' + owner)
        repository, commit = row
        require(repository == metadata['repository'] and repository == 'NRG-Wardog/' + owner,
                'Candidate owner repository differs: ' + owner)
        require(isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit), 'Unpinned graph owner: ' + owner)
        result.append((owner, repository, commit))
    return sorted(result)


def verify(registry, graph, sources, builder):
    plan = source_plan(registry, graph)
    roots = {owner: Path(sources) / owner for owner, _, _ in plan}
    roots['sidestore-auto-refresh'] = Path(builder)
    identities = {}
    for owner, repository, commit in plan:
        require(git(roots[owner], 'rev-parse', 'HEAD').decode().strip() == commit, 'Owner commit differs: ' + owner)
        require(git(roots[owner], 'config', '--get', 'remote.origin.url').decode().strip() == 'https://github.com/' + repository + '.git',
                'Owner origin differs: ' + owner)
        identities[owner] = {'repository': repository, 'commit': commit,
                             'tree': git(roots[owner], 'rev-parse', commit + '^{tree}').decode().strip()}
    builder_commit = git(builder, 'rev-parse', 'HEAD').decode().strip()
    identities['sidestore-auto-refresh'] = {'repository': 'NRG-Wardog/sidestore-auto-refresh',
                                          'commit': builder_commit,
                                          'tree': git(builder, 'rev-parse', 'HEAD^{tree}').decode().strip()}
    result = validate_inventory(registry, roots)
    for row in result['files']:
        owner, path = row['owner'], row['path']
        commit = identities[owner]['commit']
        data = git(roots[owner], 'cat-file', 'blob', commit + ':' + path)
        require(hashlib.sha256(data).hexdigest() == row['sha256'], 'Inventory bytes differ from commit: ' + owner + ':' + path)
    result['identities'] = identities
    result['result'] = 'PINNED_INVENTORY_PASS_NOT_BEHAVIOR_VALIDATION'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=ROOT / 'ci/issue52-integration/contracts.json')
    parser.add_argument('--graph', type=Path, default=ROOT / 'ci/issues-native/candidate-graph.json')
    parser.add_argument('--emit-plan', action='store_true')
    parser.add_argument('--sources', type=Path)
    parser.add_argument('--builder', type=Path, default=ROOT)
    args = parser.parse_args()
    registry = load_registry(args.registry)
    graph = json.loads(args.graph.read_text(), object_pairs_hook=unique_pairs)
    if args.emit_plan:
        for row in source_plan(registry, graph):
            print('\t'.join(row))
    else:
        require(args.sources is not None, '--sources is required for verification')
        print(json.dumps(verify(registry, graph, args.sources, args.builder), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
