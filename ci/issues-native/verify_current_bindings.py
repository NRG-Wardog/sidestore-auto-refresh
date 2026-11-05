"""Fail early when reviewed current-source manifests disagree before native tests.

This does not rewrite historical contracts; exact reviewed inverse deltas
may prove current successor bindings. Candidate tree/delta
verification remains mandatory and is performed by verify_candidate_graph.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

import direct_host_projection as direct_host
import direct_refresh_projection as direct_refresh

from verify_candidate_graph import load_candidate_graph, unique_pairs, valid_path

HERE = Path(__file__).resolve().parent
HASH_MANIFESTS = ('russian-pairing-extension.json', 'russian-setup-extension.json', 'refresh-truth-setup-extension.json', 'refresh-truth-flow-extension.json', 'refresh-observation-setup-extension.json')


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(), object_pairs_hook=unique_pairs)


def verify(sources, *, harness=HERE.parents[1], graph=None):
    sources = Path(sources).resolve(strict=True)
    harness = Path(harness).resolve(strict=True)
    graph = load_candidate_graph(harness / 'ci/issues-native/candidate-graph.json') if graph is None else graph
    multitask = read(harness / 'ci/multitask-launch/source.json')
    require(set(multitask) == {'schema', 'repository', 'commit', 'baseline_app_builder'},
            'multitask source closure differs')
    require(multitask['schema'] == 'multitask-launch-source-v1', 'multitask source schema differs')
    require([multitask['repository'], multitask['commit']] == graph['owners']['LiveContainer'],
            'multitask/current LiveContainer source pin differs')
    checked = {}
    normalized = {}
    for name in HASH_MANIFESTS:
        value = read(harness / 'ci/issues-native' / name)
        inputs = value.get('candidate_inputs')
        require(isinstance(inputs, dict) and inputs, 'missing current-source bindings: ' + name)
        for relative, expected in inputs.items():
            require(valid_path(relative) and isinstance(expected, str)
                    and re.fullmatch('[0-9a-f]{64}', expected) is not None,
                    'invalid current-source binding: ' + name)
            # These finite Russian extensions only bind the LC owner. An
            # unexpected owner must be reviewed, not silently read elsewhere.
            require(relative.startswith('LiveContainer/'), 'unexpected binding owner: ' + relative)
            path = sources / relative
            require(not any((sources / parent).is_symlink() for parent in Path(relative).parents),
                    'linked current-source ancestor: ' + relative)
            require(path.is_file() and not path.is_symlink(), 'missing/linked current-source input: ' + relative)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                # Historical manifests stay immutable. Only exact reviewed
                # successors with independently proved inverses are admitted.
                spec = direct_host.specification()
                if (relative == direct_host.PATH and expected == spec['before_sha256']
                        and actual == spec['after_sha256']):
                    require(direct_host.validate_bytes(path.read_bytes()),
                            'direct-host inverse validation did not admit successor')
                    proof = 'exact_reviewed_direct_host_inverse'
                elif (relative == direct_refresh.PATH and expected == direct_refresh.BEFORE
                        and actual == direct_refresh.AFTER):
                    require(direct_refresh.validate_source(sources),
                            'direct-refresh inverse validation did not admit successor')
                    proof = 'exact_reviewed_direct_refresh_inverse'
                else:
                    raise ValueError('stale current-source input: ' + name + ':' + relative)
                normalized[relative] = {'actual_sha256': actual, 'historical_sha256': expected,
                                        'proof': proof}
            if relative in checked:
                require(checked[relative] == actual, 'cross-manifest input disagreement: ' + relative)
            checked[relative] = actual
    return {'schema': 'current-source-bindings-v1', 'status': 'PASS',
            'source_graph': graph['owners'], 'multitask_source': multitask,
            'current_source_sha256': checked, 'reviewed_successor_bindings': normalized,
            'historical_contracts_rewritten': False, 'native_execution': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.sources)
    require(not args.output.exists() and not args.output.is_symlink(), 'fresh binding report required')
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
