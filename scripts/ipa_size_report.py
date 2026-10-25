"""Lossless IPA recompression and bundle size inventory.

No resource, binary slice, signature, entitlement or license is removed.
Uncompressed ZIP bytes are a bundle-size proxy, not on-device allocated storage.
"""
import argparse
from collections import defaultdict
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

MAX_TOTAL = 2 * 1024**3
MAX_MEMBER = 512 * 1024**2
MAX_ENTRIES = 100000


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def inspect(path):
    entries, seen, total = [], set(), 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError('archive has too many members')
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            if (not parts or parts[0] != 'Payload' or name.startswith('/')
                    or '..' in parts or '\\' in name or '\x00' in name or name in seen
                    or PurePosixPath(name).as_posix() != name.rstrip('/')):
                raise ValueError('unsafe or duplicate archive member')
            if info.flag_bits & 1:
                raise ValueError('encrypted archive member')
            total += info.file_size
            if info.file_size > MAX_MEMBER or total > MAX_TOTAL:
                raise ValueError('archive exceeds size inspection limits')
            seen.add(name)
            h = hashlib.sha256()
            count = 0
            with archive.open(info) as member:
                for chunk in iter(lambda: member.read(1024 * 1024), b''):
                    h.update(chunk)
                    count += len(chunk)
            if count != info.file_size:
                raise ValueError('archive member size differs')
            entries.append(dict(path=name, bytes=count, compressed_bytes=info.compress_size,
                                sha256=h.hexdigest(), crc=info.CRC, external_attr=info.external_attr,
                                internal_attr=info.internal_attr, date_time=list(info.date_time),
                                comment=info.comment.hex(), extra=info.extra.hex(),
                                create_system=info.create_system))
        if not any(e['path'].endswith('/Info.plist') for e in entries):
            raise ValueError('archive has no application Info.plist')
        return entries, archive.comment


def report(path):
    entries, _ = inspect(path)
    groups = defaultdict(list)
    components = defaultdict(int)
    for entry in entries:
        if entry['bytes']:
            groups[(entry['sha256'], entry['bytes'])].append(entry['path'])
        parts = PurePosixPath(entry['path']).parts
        component = '/'.join(parts[:4]) if len(parts) > 3 and parts[2] in ('Frameworks', 'PlugIns') else '/'.join(parts[:2])
        components[component] += entry['bytes']
    duplicates = [dict(sha256=sha, bytes_per_copy=size, paths=paths,
                       repeated_bytes=size * (len(paths) - 1))
                  for (sha, size), paths in groups.items() if len(paths) > 1]
    return dict(schema_version=1, ipa_bytes=path.stat().st_size, ipa_sha256=digest(path),
                uncompressed_bundle_bytes=sum(e['bytes'] for e in entries),
                size_note='Uncompressed bundle bytes exclude device allocation, app data and caches.',
                entry_count=len(entries), components=dict(sorted(components.items())),
                largest_files=sorted(entries, key=lambda e: e['bytes'], reverse=True)[:30],
                duplicate_candidates=sorted(duplicates, key=lambda e: e['repeated_bytes'], reverse=True),
                duplicate_note='Identical bytes do not prove a file can be removed; runtime paths and signatures may require every copy.')


def semantic_entries(entries):
    return [{k: v for k, v in entry.items() if k != 'compressed_bytes'} for entry in entries]


def optimize(path):
    path = Path(path)
    before = report(path)
    expected, comment = inspect(path)
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    os.close(fd)
    candidate = Path(temp)
    try:
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(candidate, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            output.comment = comment
            for info in source.infolist():
                target = copy.copy(info)
                target.compress_type = zipfile.ZIP_DEFLATED
                target._compresslevel = 9
                with source.open(info) as inp, output.open(target, 'w') as out:
                    for chunk in iter(lambda: inp.read(1024 * 1024), b''):
                        out.write(chunk)
        actual, actual_comment = inspect(candidate)
        if semantic_entries(actual) != semantic_entries(expected) or actual_comment != comment:
            raise ValueError('lossless recompression changed archive content or metadata')
        candidate_bytes = candidate.stat().st_size
        adopted = candidate_bytes < before['ipa_bytes']
        if adopted:
            os.chmod(candidate, path.stat().st_mode & 0o777)
            os.replace(candidate, path)
        after = report(path)
        return dict(schema_version=1, optimization='lossless ZIP deflate level 9', adopted=adopted,
                    before=before, after=after, candidate_ipa_bytes=candidate_bytes,
                    saved_download_bytes=before['ipa_bytes'] - after['ipa_bytes'],
                    saved_uncompressed_bundle_bytes=0, content_and_preserved_metadata_verified=True,
                    preserved_metadata='Entry order/names, bytes, CRC, timestamps, Unix mode/symlink attributes, member/archive comments and extra fields; ZIP compression headers may differ.',
                    device_validation='not performed; recompression does not establish device compatibility')
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ipa', type=Path)
    parser.add_argument('--optimize', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = optimize(args.ipa) if args.optimize else report(args.ipa)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
