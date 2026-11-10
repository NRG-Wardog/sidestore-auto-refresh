"""Report the committed source graph, never resolve moving refs or change pins."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate source-graph key: ' + key)
        result[key] = value
    return result


def selection(graph, identity):
    data = Path(graph).read_bytes()
    value = json.loads(data, object_pairs_hook=pairs)
    owners = value.get('owners')
    if not isinstance(owners, dict) or not owners:
        raise ValueError('Missing source owners')
    for owner, row in owners.items():
        if (not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', owner)
                or not isinstance(row, list) or len(row) != 2
                or not isinstance(row[0], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', row[0])
                or not isinstance(row[1], str) or not re.fullmatch(r'[0-9a-f]{40}', row[1])):
            raise ValueError('Unpinned or malformed source owner')
    if (set(identity) != {'builder_commit', 'run_id', 'run_attempt'}
            or not re.fullmatch(r'[0-9a-f]{40}', identity['builder_commit'])
            or any(not re.fullmatch(r'[1-9][0-9]*', identity[key]) for key in ('run_id', 'run_attempt'))):
        raise ValueError('Invalid workflow identity')
    return {'schema': 'committed-maintained-source-selection-v1', 'identity': identity,
            'source_graph_sha256': hashlib.sha256(data).hexdigest(), 'owners': owners,
            'selection': 'exact committed pins, not latest remote branches',
            'production_admission': False, 'device_validation': False}


def summary(report):
    lines = ['## Exact source selection', '',
             'This job builds the committed maintained graph, not the newest remote branches.',
             'The initial graph is the v6.0.1 source basis. This run is a new candidate, not a new release.',
             'Packaging uses this run\'s compiled products. Optional Zopfli recompression is not part of this job.',
             '', '| Component | Repository | Commit |', '|---|---|---|']
    for owner, (repository, commit) in sorted(report['owners'].items()):
        lines.append(f'| {owner} | {repository} | `{commit}` |')
    lines += ['', 'Graph SHA256: `' + report['source_graph_sha256'] + '`',
              'Builder commit: `' + report['identity']['builder_commit'] + '`',
              '', 'A green run does not prove on-device compatibility or authorize publication.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args()
    report = selection(args.graph, {'builder_commit': os.environ['GITHUB_SHA'],
                                  'run_id': os.environ['GITHUB_RUN_ID'],
                                  'run_attempt': os.environ['GITHUB_RUN_ATTEMPT']})
    with args.output.open('x') as handle:
        json.dump(report, handle, indent=2)
        handle.write('\n')
    with args.summary.open('a') as handle:
        handle.write(summary(report))
    print(summary(report))


if __name__ == '__main__':
    main()
