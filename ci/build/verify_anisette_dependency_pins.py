"""Early graph-bound consumer pin check; no resolution, build or authentication."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import argparse
import hashlib
import json
from pathlib import Path
import re

from ci.build.verify_candidate_graph import git, load_candidate_graph, require, unique_pairs

SIGN = 'SideStore/Dependencies/SideSign'
DECLARATION = SIGN + '/Package.swift'
LOCKS = (SIGN + '/Package.resolved', 'SideStore/AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved')
URL = 'https://github.com/NRG-Wardog/AnisetteKit.git'


def regular(root, relative):
    path = root
    for part in Path(relative).parts:
        path = path / part
        require(not path.is_symlink(), 'linked dependency input: ' + relative)
    require(path.is_file(), 'missing dependency input: ' + relative)
    return path


def declaration_revision(text):
    # Keep quoted URLs intact while discarding Swift comments. This accepts only
    # the reviewed literal revision declaration, never a computed dependency.
    token = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
    text = token.sub(lambda m: m.group() if m.group().startswith('"') else ' ', text)
    calls = []
    for start in re.finditer(r'\.package\s*\(', text):
        depth, end = 1, start.end()
        # Balance calls without treating parentheses inside string literals as
        # syntax. Computed/nested Anisette declarations must fail, not vanish.
        pieces = re.finditer(r'"(?:\\.|[^"\\])*"|[()]', text[end:])
        for piece in pieces:
            if piece.group() == '(': depth += 1
            elif piece.group() == ')': depth -= 1
            if depth == 0:
                calls.append(text[end:end + piece.start()])
                break
        require(depth == 0, 'unterminated package declaration')
    candidates = [c for c in calls if re.search('anisettekit', c, re.I)]
    require(len(candidates) == 1, 'AnisetteKit declaration missing/duplicate')
    match = re.fullmatch(r'\s*url\s*:\s*"' + re.escape(URL) + r'"\s*,\s*revision\s*:\s*"([0-9a-f]{40})"\s*', candidates[0])
    require(match is not None, 'unexpected AnisetteKit declaration identity or requirement')
    return match[1]


def lock_revision(data):
    value = json.loads(data, object_pairs_hook=unique_pairs)
    require(type(value) is dict and value.get('version') in (2, 3) and type(value.get('pins')) is list, 'unexpected lock schema')
    identities = set()
    matches = []
    for row in value['pins']:
        require(type(row) is dict and type(row.get('identity')) is str, 'invalid package identity')
        identity = row['identity'].lower()
        require(identity not in identities, 'duplicate package identity')
        identities.add(identity)
        if identity == 'anisettekit' or 'anisettekit' in str(row.get('location', '')).lower():
            matches.append(row)
    require(len(matches) == 1, 'AnisetteKit lock missing/duplicate')
    row = matches[0]
    require(set(row) == {'identity', 'kind', 'location', 'state'} and row['identity'] == 'anisettekit' and row['kind'] == 'remoteSourceControl' and row['location'] == URL, 'unexpected AnisetteKit lock identity')
    state = row['state']
    require(type(state) is dict and set(state) == {'revision'} and re.fullmatch('[0-9a-f]{40}', str(state['revision'])), 'unexpected AnisetteKit lock requirement')
    return state['revision']


def verify(sources, graph_path):
    sources = Path(sources).absolute()
    require(not any(p.is_symlink() for p in (sources, *sources.parents)), 'linked sources root')
    graph_path = Path(graph_path).absolute()
    require(not any(p.is_symlink() for p in (graph_path, *graph_path.parents)), 'linked graph input')
    graph = load_candidate_graph(graph_path)
    expected = graph['owners']['AnisetteKit'][1]
    for owner in ('AnisetteKit', 'SideStore', SIGN):
        root = sources / owner
        for parent in (root, *root.parents):
            require(not parent.is_symlink(), 'linked source owner')
        require(Path(git(root, 'rev-parse', '--show-toplevel')).resolve() == root.resolve(), 'inherited source Git root: ' + owner)
        require(git(root, 'rev-parse', 'HEAD') == graph['owners'][owner][1], 'source head differs: ' + owner)
    rows = git(sources / 'SideStore', 'ls-tree', 'HEAD', '--', 'Dependencies/SideSign').splitlines()
    require(rows == ['160000 commit ' + graph['owners'][SIGN][1] + '\tDependencies/SideSign'], 'SideStore SideSign gitlink differs')
    hashes = {}
    for relative in (DECLARATION, *LOCKS):
        path = regular(sources, relative)
        data = path.read_bytes()
        owner = SIGN if relative.startswith(SIGN + '/') else 'SideStore'
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(git(sources / owner, 'rev-parse', 'HEAD:' + relative[len(owner)+1:]) == blob, 'uncommitted dependency input: ' + relative)
        revision = declaration_revision(data.decode()) if relative == DECLARATION else lock_revision(data)
        require(revision == expected, 'AnisetteKit dependency pin differs: ' + relative)
        hashes[relative] = hashlib.sha256(data).hexdigest()
    return {'schema': 'anisette-dependency-pins-v1', 'status': 'PASS', 'source_graph': graph['owners'], 'input_sha256': hashes,
            'graph_sha256': hashlib.sha256(graph_path.read_bytes()).hexdigest(), 'sidestore_sidesign_gitlink': graph['owners'][SIGN][1],
            'native_execution': False, 'dependency_resolution_executed': False, 'authentication_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--graph', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.sources, args.graph)
    require(not any(p.is_symlink() for p in (args.output.absolute(), *args.output.absolute().parents)), 'linked output')
    with args.output.open('x') as stream:
        stream.write(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print('ANISETTE_DEPENDENCY_PINS_PASS_NATIVE_NOT_EXECUTED')


if __name__ == '__main__':
    main()
