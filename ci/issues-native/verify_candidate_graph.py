"""Verify reviewed candidate commits/trees and unchanged historical source baselines."""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
# The original native harness's exact acquisition closure. New repositories or
# owner paths require an explicit code review, not only a manifest edit.
BASELINE_OWNERS = {
    'LiveContainer': ('NRG-Wardog/LiveContainer', '8808211d340c6e9e2a6274c0eaa863d7ab09474f'),
    'SideStore': ('NRG-Wardog/SideStore', '5afdedc3e50be60082ac1d405a456274920b8eab'),
    'AnisetteKit': ('NRG-Wardog/AnisetteKit', '3e670c2e1b72ff328d3a64353d8de2418df87e73'),
    'idevice': ('NRG-Wardog/idevice', '774f02a6d29777c85400f4ba7fa69c12b4227fb3'),
    'jktcp': ('NRG-Wardog/jktcp', 'a46594f6af56b66b6d9342ed9f5ee6d1b7fb9cc9'),
    'SideStore/Dependencies/SideSign': ('NRG-Wardog/SideSign', '483c626d6e5004b45298518352307eb492fe1ccd'),
    'SideStore/Dependencies/minimuxer': ('NRG-Wardog/minimuxer', '4ca3af5270de8aa48e8bc100eadf1b3550260ea2'),
    'LiveContainer/OpenSSL': ('krzyzanowskim/OpenSSL', '623c84da314e85363236507ca38a4bde65df21c3'),
    'idevice/cpp/plist_ffi': ('jkcoxson/plist_ffi', '66e6e6362b58bc361725c3e1b0a34af498aa2b3a'),
    'LiveContainer/litehook': ('LiveContainerMirror/litehook', 'a7e9f599d7f0b3d77008ad62bf10cb10dab6eca9'),
}
REQUIRED_DELTAS = {'LiveContainer', 'SideStore', 'SideStore/Dependencies/SideSign', 'idevice'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate manifest key: ' + key)
        result[key] = value
    return result


def oid(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) is not None


def valid_path(value):
    return (isinstance(value, str) and bool(value) and not value.startswith('/')
            and '\\' not in value and not any(ord(c) < 32 for c in value)
            and all(part not in ('', '.', '..', '.git') for part in value.split('/'))
            and PurePosixPath(value).as_posix() == value)


def load_candidate_graph(path=HERE / 'candidate-graph.json'):
    graph = json.loads(Path(path).read_text(), object_pairs_hook=unique_pairs)
    require(isinstance(graph, dict) and set(graph) == {'schema_version', 'owners', 'reviewed_deltas'}, 'candidate graph schema fields differ')
    require(type(graph['schema_version']) is int and graph['schema_version'] == 1, 'candidate graph schema version differs')
    owners, deltas = graph['owners'], graph['reviewed_deltas']
    require(isinstance(owners, dict) and set(owners) == set(BASELINE_OWNERS), 'candidate owner closure differs')
    require(isinstance(deltas, dict) and REQUIRED_DELTAS <= set(deltas) <= set(owners), 'reviewed delta owner closure differs')
    for name, value in owners.items():
        require(isinstance(value, list) and len(value) == 2 and value[0] == BASELINE_OWNERS[name][0] and oid(value[1]), 'invalid candidate repository or pin: ' + name)
        require(value[1] == BASELINE_OWNERS[name][1] or name in deltas, 'changed owner lacks reviewed delta: ' + name)
    for name, delta in deltas.items():
        require(isinstance(delta, dict) and set(delta) == {'baseline', 'tree', 'files'}, 'delta schema differs: ' + name)
        require(oid(delta['baseline']) and oid(delta['tree']), 'invalid delta baseline/tree: ' + name)
        require(isinstance(delta['files'], dict), 'delta files must be a mapping: ' + name)
        for path, value in delta['files'].items():
            require(valid_path(path) and (value is None or oid(value)), 'invalid delta path/blob: ' + name)
    return graph


def git(root, *args):
    return subprocess.check_output(['git', '--no-replace-objects', '--literal-pathspecs', '-C', str(root),
                                   '-c', 'core.hooksPath=/dev/null', '-c', 'core.quotePath=false', *args], text=True).strip()


def main():
    graph = load_candidate_graph()
    sources = Path('sources').resolve()
    rows = {}
    for name, (repo, commit) in graph['owners'].items():
        root = sources / name
        require(git(root, 'rev-parse', 'HEAD') == commit, 'candidate commit differs: ' + name)
        expected = graph['reviewed_deltas'].get(name)
        if expected is not None:
            require(git(root, 'rev-parse', 'HEAD^{tree}') == expected['tree'], 'candidate tree differs: ' + name)
            git(root, 'merge-base', '--is-ancestor', expected['baseline'], commit)
            paths = git(root, 'diff', '--name-only', expected['baseline'], commit).splitlines()
            require(set(paths) == set(expected['files']), 'unreviewed candidate file delta: ' + name)
            for path, expected_oid in expected['files'].items():
                rowspec = git(root, 'ls-tree', commit, '--', path)
                actual = rowspec.split()[2] if rowspec else None
                require(actual == expected_oid, 'candidate blob differs: ' + name + '/' + path)
            rows[name] = expected

    # Historical parity proves only the unchanged original integration baseline.
    spec = json.loads((HERE / 'upstream-sync-contract.json').read_text())
    baselines = Path('historical-parity-sources').resolve()
    for name, expected in spec['native_sources'].items():
        require(name in graph['owners'], 'unknown historical owner')
        target = baselines / name
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', str(sources / name), 'worktree', 'add', '--detach',
                        str(target), expected['commit']], check=True)
    subprocess.run([sys.executable, '-B', str(HERE / 'verify_upstream_sync.py'), 'source-parity',
                    '--sources', str(baselines), '--output', 'evidence/historical-source-parity.json'], check=True)
    Path('evidence/candidate-source-delta.json').write_text(json.dumps({
        'status': 'EXACT_REVIEWED_CANDIDATE_DELTA_PASS', 'reviewed_deltas': rows,
        'device_execution': False, 'historical_parity_is_not_candidate_validation': True}, indent=2))


if __name__ == '__main__':
    main()
