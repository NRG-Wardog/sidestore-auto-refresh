"""Bind portable ODA admission to the exact trusted projector source constants.

The source path is copied from the reviewed packaging checkout, never from a
native artifact. Parse its constants as data; do not execute that source here.
"""
import argparse
import ast
import json
from pathlib import Path
import owned_oda_package_admission as oda

ADMISSION = 'owned-oda-package-admission.json'
PROJECTOR_SNAPSHOT = 'owned-oda-projector.snapshot'


def artifact_from_source(path):
    raw = oda.read(path)
    tree = ast.parse(raw, filename=str(path))
    selected = {}
    names = {'EVIDENCE_ID': 'artifact_id', 'EVIDENCE_BYTES': 'bytes', 'EVIDENCE_SHA': 'sha256'}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [target.id for target in node.targets if isinstance(target, ast.Name) and target.id in names]
        if not targets:
            continue
        oda.require(len(node.targets) == 1 and len(targets) == 1 and isinstance(node.value, ast.Constant), 'ODA projector pin must be one literal assignment')
        key = names[targets[0]]
        oda.require(key not in selected, 'Duplicate ODA projector pin')
        selected[key] = node.value.value
    oda.archive_identity(selected)
    return selected, oda.sha(raw)


def wrapped(admission, source_sha):
    return {'schema': 'source-bound-owned-oda-package-admission-v1',
            'trusted_projector_sha256': source_sha, 'admission': admission}


def project(archive, root, manifest, source_path):
    expected, source_sha = artifact_from_source(source_path)
    result = wrapped(oda.project(archive, root, manifest, expected), source_sha)
    with (Path(root) / ADMISSION).open('x') as output:
        output.write(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return verify(root, manifest, source_path)


def verify(root, manifest, source_path):
    expected, source_sha = artifact_from_source(source_path)
    result = wrapped(oda.verify(root, manifest, expected), source_sha)
    oda.require(oda.decode(oda.read(Path(root) / ADMISSION)) == result, 'ODA saved admission or trusted source binding differs')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--projector', type=Path, required=True)
    args = parser.parse_args()
    manifest = oda.decode(oda.read(args.root / 'input-manifest.json'))
    verify(args.root, manifest, args.projector)
    print('PASS exact source-bound owned ODA capsule; no native/device/authentication execution by adapter')


if __name__ == '__main__': main()
