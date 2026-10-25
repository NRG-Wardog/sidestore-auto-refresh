"""Read-only Git/source-proof checks; no checkout, package resolver or code import."""
import hashlib
import os
from pathlib import PurePosixPath
import re
import subprocess
from p2_archive import need, relative, sha

PINS = {
    'LiveContainer': ('e22de6df412a0977f7430b2218960ca3ee9585d5', '4b973e2bbf482953e298c41c7a7227cb60b30718', 'https://github.com/NRG-Wardog/LiveContainer.git'),
    'SideStore': ('59fa3b5cd8c9ea4a88716cbaaa08bacd9f22e218', 'c58017caf7e392e238f47a5e0fa9511fa6f74ca0', 'https://github.com/NRG-Wardog/SideStore.git'),
    'AnisetteKit': ('161435bcc0344e1a9853669455c1ca8d9dda2bf1', '0df50020ddd774a5b7ab941944501f1ecd591a4a', 'https://github.com/NRG-Wardog/AnisetteKit.git'),
    'OpenSSL': ('623c84da314e85363236507ca38a4bde65df21c3', '21bc09387e7f91a46eb8bd26587ce1d052277801', 'https://github.com/krzyzanowskim/OpenSSL'),
    'litehook': ('8025e0c8ebdf5cdd1d2a4f45025813234bf9dc55', '62bc06a17a1c12d805aec1ada25910bfa9f89781', 'https://github.com/LiveContainerMirror/litehook.git'),
}
CHILDREN = {'LiveContainer': {'OpenSSL': PINS['OpenSSL'][0], 'litehook': PINS['litehook'][0]},
            'SideStore': {'Dependencies/SideSign': 'c55f53ee6d3a65ab9b49d1528eb6c668fdde6c0e',
                          'Dependencies/minimuxer': 'efbcab05d7d636aa37c6bf6c7f364d122c5610f6'},
            'AnisetteKit': {}, 'OpenSSL': {}, 'litehook': {}}


def oid(value):
    need(isinstance(value, str) and re.fullmatch('[a-f0-9]{40}', value) and value != '0' * 40, 'immutable Git object required')
    return value


def absolute(value):
    need(isinstance(value, str) and value.startswith('/') and '\0' not in value and '\\' not in value and
         all(p not in ('', '.', '..') for p in value[1:].split('/')), 'invalid captured absolute path')
    return value


def environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1',
               GIT_GRAFT_FILE=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1')
    return env


def git_command(repo, *args):
    return ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-C', str(repo), *args]


def git(repo, *args):
    return subprocess.check_output(git_command(repo, *args), env=environment(), stderr=subprocess.PIPE)


def blob(repo, commit, path):
    return git(repo, 'show', oid(commit) + ':' + relative(path))


def entries(repo, commit):
    result = {}
    for raw in git(repo, 'ls-tree', '-rz', '--full-tree', oid(commit)).split(b'\0'):
        if not raw:
            continue
        meta, name = raw.split(b'\t', 1)
        mode, kind, object_id = meta.decode().split()
        result[relative(name.decode())] = {'mode': mode, 'type': kind, 'gitObject': oid(object_id)}
    return result


def blob_digests(repo, rows):
    process = subprocess.Popen(git_command(repo, 'cat-file', '--batch'), env=environment(), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result = {}
    try:
        for row in rows.values():
            object_id = row['gitObject']
            if row['type'] != 'blob' or object_id in result:
                continue
            process.stdin.write((oid(object_id) + '\n').encode())
            process.stdin.flush()
            header = process.stdout.readline().decode().split()
            need(len(header) == 3 and header[:2] == [object_id, 'blob'], 'local source object differs')
            size = int(header[2]); need(0 <= size <= 1024**3, 'source object byte bound')
            digest = hashlib.sha256(); remaining = size
            while remaining:
                chunk = process.stdout.read(min(remaining, 1024 * 1024))
                need(chunk, 'truncated local Git object')
                digest.update(chunk); remaining -= len(chunk)
            need(process.stdout.read(1) == b'\n', 'local Git object delimiter differs')
            result[object_id] = digest.hexdigest()
        process.stdin.close()
        need(process.wait(timeout=30) == 0, 'local Git object reader failed')
        return result
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        process.stdout.close(); process.stderr.close()


def tree_oid(rows):
    root = {}
    for path, row in rows.items():
        node = root
        parts = relative(path).split('/')
        for part in parts[:-1]:
            need(part not in node or isinstance(node[part], dict), 'source path collision')
            node = node.setdefault(part, {})
        need(parts[-1] not in node, 'source entry collision')
        node[parts[-1]] = (row['mode'], oid(row['gitObject']))
    def build(node):
        pieces = []
        for name, value in sorted(node.items(), key=lambda x: (x[0] + ('/' if isinstance(x[1], dict) else '')).encode()):
            mode, object_id = ('40000', build(value)) if isinstance(value, dict) else value
            pieces.append(mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(object_id))
        raw = b''.join(pieces)
        return hashlib.sha1(b'tree ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    return build(root)


def link_destination(name, rows):
    directories = {str(parent) for key in rows for parent in PurePosixPath(key).parents if str(parent) != '.'}
    pending, resolved, seen = name.split('/'), [], set()
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            need(resolved, 'source link escapes checkout'); resolved.pop(); continue
        path = '/'.join([*resolved, part]); row = rows.get(path)
        if row and row['mode'] == '120000':
            need(path not in seen and len(seen) < 128, 'cyclic/excessive source link')
            seen.add(path); pending = row['linkTarget'].split('/') + pending; continue
        need(path in directories or row and row['type'] == 'blob', 'missing source link target')
        need(path in directories or not pending, 'source file used as directory')
        resolved.append(part)
    need(resolved, 'source link resolves to root')
    return '/'.join(resolved)


def source_proof(proof, owner, repo=None, *, identity=None):
    # With a local repository every blob SHA256 is recomputed. Dependency
    # receipts without local blobs bind only Git tree/object identities and
    # link bytes here; their regular-blob SHA256 fields remain observations.
    # The main replay additionally binds every raw hash-object command and
    # explicitly reports those redundant SHA256 fields as unverified.
    commit, tree, origin = identity or PINS[owner]
    need(set(proof) == {'root', 'commit', 'tree', 'origin', 'files'}, 'source proof schema differs')
    absolute(proof['root'])
    need(proof['commit'] == commit and proof['tree'] == tree and proof['origin'] in
         (origin, origin[:-4] if origin.endswith('.git') else origin + '.git'), 'source proof identity differs')
    rows = proof['files']; need(isinstance(rows, dict) and 0 < len(rows) <= 25000, 'source entry count')
    local = entries(repo, commit) if repo is not None else None
    if local is not None:
        need(git(repo, 'rev-parse', commit + '^{tree}').decode().strip() == tree and set(local) == set(rows), 'actual Git source tree differs')
        digests = blob_digests(repo, local)
    for name, row in rows.items():
        relative(name)
        need((row.get('mode'), row.get('type')) in {('100644','blob'),('100755','blob'),('120000','blob'),('160000','commit')}, 'source entry type differs')
        oid(row.get('gitObject'))
        expected_keys = {'mode', 'type', 'gitObject'} | (set() if row['type'] == 'commit' else {'sha256'})
        if row['mode'] == '120000':
            expected_keys |= {'linkTarget', 'resolvedTarget'}
        need(set(row) == expected_keys, 'source entry schema differs')
        if row['type'] == 'blob':
            need(re.fullmatch('[a-f0-9]{64}', row['sha256']), 'source SHA256 differs')
        if local is not None:
            need({k: row[k] for k in ('mode', 'type', 'gitObject')} == local[name], 'local source entry differs')
            if row['type'] == 'blob':
                need(row['sha256'] == digests[row['gitObject']], 'local source SHA256 differs')
        if row['mode'] == '120000':
            target = row['linkTarget']; need(isinstance(target, str) and target and not target.startswith('/') and '\0' not in target, 'invalid source link')
            raw = target.encode(); need(hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['gitObject'] and sha(raw) == row['sha256'], 'source link raw bytes differ')
            if owner == 'SideStore' and name == 'AltStore/Resources/SideBackup.ipa':
                need(row['gitObject'] == '54a080c4e8a5029ad8d22842c16acde4d6d01b43' and target == '../../build/SideBackup.ipa' and
                     row['resolvedTarget'] == {'classification':'committed-dangling-generated-resource','target':'build/SideBackup.ipa','consumed':False}, 'unconsumed generated resource proof differs')
                need(not any(p == 'build' or p.startswith('build/') for p in rows), 'generated resource became tracked')
            else:
                need(row['resolvedTarget'] == link_destination(name, rows), 'source link resolution differs')
    need({k: v['gitObject'] for k, v in rows.items() if v['type'] == 'commit'} == CHILDREN[owner], 'source gitlinks differ')
    need(tree_oid(rows) == tree, 'source proof does not reconstruct pinned Git tree')
    return proof
