"""Hash-bound read-only projection of current native receipt evidence, without execution."""
import json, os, sys, zipfile, stat
from pathlib import Path, PurePosixPath
from contract import require, SHA, manifest, read_json, REPO
from acquire import validate_metadata
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ci/issues-native'))
from recover_native_artifacts import API, sha
EVIDENCE_ID = 13008771710
EVIDENCE_BYTES = 408285162
EVIDENCE_SHA = '23b0a836fa459ae14bd790d2bf0d617a2fc8866f95b39bb509cc16db20b6055a'
EXPECTED_COUNT = 152

def validate_read_only_archive(archive):
    """Validate data indexes only; never restore archived permissions or execute entries."""
    modes = {}
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        require(0 < len(entries) <= 20000 and sum(x.file_size for x in entries) <= 2*1024**3, 'archive size bounds')
        seen = {}
        for item in entries:
            name = item.filename
            path = PurePosixPath(name.rstrip('/'))
            require(name and '\\' not in name and '\x00' not in name and ':' not in name and not path.is_absolute() and all(p not in ('', '.', '..') for p in path.parts) and path.as_posix() + ('/' if item.is_dir() else '') == name, 'unsafe archive path')
            require(not item.flag_bits & 1 and item.file_size <= 550*1024**2, 'encrypted or oversized archive member')
            mode = item.external_attr >> 16
            kind = stat.S_IFMT(mode)
            require(kind in (stat.S_IFREG, stat.S_IFDIR) and (kind == stat.S_IFDIR) == item.is_dir(), 'archive symlink/special/unspecified entry rejected')
            require(stat.S_IMODE(mode) & ~0o777 == 0, 'archive special permission bits rejected')
            require(not item.is_dir() or item.file_size == 0, 'nonempty directory member')
            key = path.as_posix().casefold()
            require(key not in seen, 'duplicate/case-colliding archive path')
            seen[key] = kind
            modes[oct(stat.S_IMODE(mode))] = modes.get(oct(stat.S_IMODE(mode)), 0) + 1
        for item in entries:
            for parent in PurePosixPath(item.filename.rstrip('/')).parents:
                if parent == PurePosixPath('.'): continue
                require(seen.get(parent.as_posix().casefold(), stat.S_IFDIR) == stat.S_IFDIR, 'archive file used as parent')
    return modes

def project(archive, receipt_bytes, output, *, expected_sha, expected_bytes, expected_count):
    require(archive.stat().st_size == expected_bytes and sha(archive) == expected_sha, 'native evidence archive bytes differ')
    archive_modes = validate_read_only_archive(archive)
    print("Read-only evidence permission counts:", json.dumps(archive_modes, sort_keys=True))
    receipt = json.loads(receipt_bytes)
    expected = receipt['evidence_sha256']
    require(len(expected) == expected_count and expected_count > 0, 'native evidence reference count differs')
    hashes = {}
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as dest:
        require(source.read('receipt.json') == receipt_bytes, 'native receipt differs between artifacts')
        dest.writestr('native-receipt.json', receipt_bytes)
        for name, digest in sorted(expected.items()):
            path = PurePosixPath(name)
            require(not path.is_absolute() and path.as_posix() == name and all(p not in ('', '.', '..') for p in path.parts), 'unsafe evidence reference')
            require(name != 'native-receipt.json' and source.getinfo(name).file_size <= 550*1024**2, 'invalid evidence member')
            data = source.read(name)
            require(SHA(data) == digest, 'native evidence hash differs: ' + name)
            hashes[name] = digest
            dest.writestr(name, data)
    require(output.stat().st_size <= 31*1024**2, 'projected evidence exceeds bounded download size')
    return {'schema': 'exact-native-evidence-projection-v1', 'source_artifact_id': EVIDENCE_ID,
            'source_archive_sha256': expected_sha, 'source_archive_bytes': expected_bytes,
            'native_receipt_sha256': SHA(receipt_bytes), 'verified_reference_count': len(hashes),
            'verified_reference_sha256': hashes, 'projection_sha256': sha(output),
            'projection_bytes': output.stat().st_size, 'source_archive_permission_counts': archive_modes, 'execution': False}

def main():
    require(len(sys.argv) == 1, 'arbitrary inputs forbidden')
    out = Path.cwd() / 'workspace'
    m = manifest(out / 'input-manifest.json')
    require(m['run_id'] == 41114358965 and m['commit'] == 'f9407ceda3b28f62be9c479a89e79f0acf30691d' and m['run_attempt'] == 1, 'projection candidate differs')
    api = API(os.environ['GH_TOKEN'])
    run = api.json(f"/repos/{REPO}/actions/runs/{m['run_id']}")
    ipa_artifact = api.json(f"/repos/{REPO}/actions/artifacts/{m['artifact_id']}")
    validate_metadata(m, run, ipa_artifact)
    meta = api.json(f'/repos/{REPO}/actions/artifacts/{EVIDENCE_ID}')
    require(meta['id'] == EVIDENCE_ID and meta['expired'] is False and meta['size_in_bytes'] == EVIDENCE_BYTES and meta['digest'] == 'sha256:' + EVIDENCE_SHA, 'native evidence metadata differs')
    require(meta['name'] == f"native-app-compile-{m['commit']}-{m['run_id']}-{m['run_attempt']}" and meta['workflow_run']['id'] == m['run_id'] and meta['workflow_run']['head_sha'] == m['commit'], 'native evidence provenance differs')
    archive = out / 'native-evidence-original.zip'
    api.download(EVIDENCE_ID, archive)
    report = project(archive, (out / 'native-receipt.json').read_bytes(), out / 'native-evidence-projection.zip', expected_sha=EVIDENCE_SHA, expected_bytes=EVIDENCE_BYTES, expected_count=EXPECTED_COUNT)
    report['native_run'] = {'run_id': m['run_id'], 'run_attempt': m['run_attempt'], 'commit': m['commit']}
    (out / 'native-evidence-projection.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Verified and projected all', report['verified_reference_count'], 'exact native evidence references')

if __name__ == '__main__':
    main()
