"""Bounded read-only ZIP and JSON primitives. No extraction or artifact execution."""
import hashlib
import io
import json
import math
from pathlib import PurePosixPath
import stat
import tempfile
import zipfile

MAX_ENTRIES = 25000
MAX_BYTES = 2 * 1024**3
MAX_FILE = 1024**3


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    def finite(value):
        result = float(value)
        need(math.isfinite(result), 'nonfinite JSON number')
        return result
    def constant(value):
        raise ValueError('nonfinite JSON constant: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_float=finite, parse_constant=constant)


def relative(name):
    need(isinstance(name, str) and name and '\\' not in name and '\0' not in name, 'invalid ZIP path')
    need(not name.startswith('/') and all(part not in ('', '.', '..') for part in name.split('/')), 'unsafe ZIP path')
    return name


def stream_digest(stream, expected=None):
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        need(total <= MAX_FILE, 'ZIP member exceeds byte bound')
        digest.update(chunk)
    need(expected is None or total == expected, 'ZIP member size differs')
    return digest.hexdigest(), total


class Archive:
    def __init__(self, source, *, links=False, owned=None):
        self.owned = owned
        self.archive = zipfile.ZipFile(source)
        try:
            entries = self.archive.infolist()
            need(0 < len(entries) <= MAX_ENTRIES, 'ZIP entry bound')
            need(sum(i.file_size for i in entries) <= MAX_BYTES, 'ZIP expanded byte bound')
            self.entries = {}
            self.files = {}
            self.inventory = {}
            for item in entries:
                name = relative(item.filename[:-1] if item.is_dir() else item.filename)
                need(name not in self.entries, 'duplicate/aliased ZIP path')
                need(not item.flag_bits & 1 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'unsupported ZIP encoding')
                need(item.file_size <= MAX_FILE, 'ZIP member byte bound')
                mode = item.external_attr >> 16
                kind = stat.S_IFMT(mode)
                need(kind in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK), 'unsupported ZIP file type')
                if item.is_dir():
                    need(kind in (0, stat.S_IFDIR) and item.file_size == 0, 'malformed ZIP directory')
                    row = {'type': 'directory', 'mode': stat.S_IMODE(mode)}
                elif kind == stat.S_IFLNK:
                    need(links and item.file_size <= 4096, 'ZIP link forbidden')
                    raw = self.archive.read(item)
                    target = raw.decode('utf-8')
                    need(target and '\0' not in target and '\\' not in target and not target.startswith('/'), 'invalid ZIP link target')
                    row = {'type': 'symlink', 'mode': stat.S_IMODE(mode), 'target': target}
                else:
                    need(kind in (0, stat.S_IFREG), 'ZIP type/name mismatch')
                    with self.archive.open(item) as stream:
                        digest, count = stream_digest(stream, item.file_size)
                    row = {'type': 'file', 'mode': stat.S_IMODE(mode), 'bytes': count, 'sha256': digest}
                    self.files[name] = digest
                self.entries[name] = item
                self.inventory[name] = row
            for name, row in self.inventory.items():
                for parent in PurePosixPath(name).parents:
                    if str(parent) != '.' and str(parent) in self.inventory:
                        need(self.inventory[str(parent)]['type'] == 'directory', 'ZIP non-directory ancestor')
                if row['type'] == 'symlink':
                    self.resolve(name)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.archive.close()
        if self.owned is not None:
            self.owned.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def resolve(self, name):
        parts = relative(name).split('/')
        resolved, seen = [], set()
        while parts:
            part = parts.pop(0)
            if part in ('', '.'):
                continue
            if part == '..':
                need(resolved, 'ZIP link escapes root')
                resolved.pop()
                continue
            path = '/'.join([*resolved, part])
            row = self.inventory.get(path)
            need(row is not None, 'dangling ZIP link')
            if row['type'] == 'symlink':
                need(path not in seen and len(seen) < 128, 'cyclic/excessive ZIP links')
                seen.add(path)
                parts = row['target'].split('/') + parts
            else:
                need(not parts or row['type'] == 'directory', 'ZIP file used as directory')
                resolved.append(part)
        need(resolved, 'ZIP link resolves to root')
        return '/'.join(resolved)

    def raw(self, name, limit=32 * 1024**2):
        relative(name)
        need(name in self.files, 'missing/nonregular ZIP member: ' + name)
        need(self.entries[name].file_size <= limit, 'structured member exceeds parser bound')
        return self.archive.read(self.entries[name])

    def json(self, name):
        return parse(self.raw(name))

    def nested(self, name, *, links=False):
        need(name in self.files, 'missing nested ZIP')
        stream = tempfile.TemporaryFile()
        try:
            with self.archive.open(self.entries[name]) as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
            stream.seek(0)
            return Archive(stream, links=links, owned=stream)
        except BaseException:
            stream.close()
            raise

    def index(self):
        raw = self.raw('evidence-index.json', 8 * 1024**2)
        value = parse(raw)
        need(isinstance(value, dict) and value == {k: v for k, v in self.files.items() if k != 'evidence-index.json'}, 'ZIP exact index/content binding differs')
        return raw, value


def archive_receipt(value, archive, digest, count):
    need(value == {'sha256': digest, 'bytes': count, 'entries': len(archive.entries), 'stable': True,
                  'limits': {'entries': MAX_ENTRIES, 'bytes': MAX_BYTES, 'perFileBytes': MAX_FILE}}, 'archive receipt differs')
