"""Parser-only tests: no captured module, fixture, executable or build is run."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'migration'), str(ROOT)]
import login_capture_parser as parser


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def make_zip(files, index=None, entries=None):
    if index is None:
        index = {name: parser.sha(raw) for name, raw in files.items()}
    index_raw = encode(index)
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            sequence = [(name, raw, stat.S_IFREG | 0o644) for name, raw in files.items()]
            if entries is not None:
                sequence = entries
            for name, raw, mode in sequence + [('evidence-index.json', index_raw, stat.S_IFREG | 0o644)]:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, raw)
    raw = stream.getvalue()
    return raw, parser.sha(raw), len(raw), parser.sha(index_raw), len(index)


class StrictParserTests(unittest.TestCase):
    def test_valid_json(self):
        self.assertEqual(parser.parse(b'{"integer":1,"list":[true,null,0.5]}'),
                         {'integer': 1, 'list': [True, None, .5]})

    def test_duplicate_keys_rejected_at_any_depth(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":{"b":0,"b":0}}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'duplicate'):
                parser.parse(raw)

    def test_nonfinite_and_overflow_rejected(self):
        for raw in (b'NaN', b'Infinity', b'-Infinity', b'1e999', b'{"x":[1e999]}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'nonfinite'):
                parser.parse(raw)

    def test_invalid_utf8_depth_and_size_rejected(self):
        for raw in (b'"\xff"', b'[' * 70 + b'0' + b']' * 70, b' ' * (parser.MAX_JSON_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                parser.parse(raw)

    def test_unsafe_paths_rejected(self):
        for name in ('', '/absolute', '../parent', 'a/../b', 'a/./b', 'a//b', 'a/',
                     'a\\b', 'C:drive', 'a\x00b', 'a\nb'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                parser.relative(name)


class ArchiveMutationTests(unittest.TestCase):
    def test_valid_same_buffer_archive(self):
        archive = parser.Archive(*make_zip({'data.json': b'{"ok":true}'}))
        self.assertEqual(archive.json('data.json'), {'ok': True})

    def test_original_bytes_hash_and_length_are_mandatory(self):
        args = list(make_zip({'data.json': b'{}'}))
        for index, replacement in ((1, '0' * 64), (2, args[2] + 1), (3, '0' * 64), (4, 2)):
            mutated = args[:]
            mutated[index] = replacement
            with self.subTest(index=index), self.assertRaises(ValueError):
                parser.Archive(*mutated)

    def test_index_exact_path_set(self):
        for index in ({}, {'extra': parser.sha(b'')}, {'data.json': parser.sha(b'{}'), 'extra': parser.sha(b'')}):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'file set'):
                parser.Archive(*make_zip({'data.json': b'{}'}, index=index))

    def test_index_digest_and_duplicate_key(self):
        with self.assertRaisesRegex(ValueError, 'indexed bytes'):
            parser.Archive(*make_zip({'data.json': b'{}'}, index={'data.json': '0' * 64}))
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            parser.Archive(*make_zip({'data.json': b'{"x":0,"x":1}'}))

    def test_duplicate_and_case_aliased_members(self):
        for names in (('a', 'a'), ('a', 'A')):
            entries = [(n, b'', stat.S_IFREG | 0o644) for n in names]
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, 'duplicate'):
                parser.Archive(*make_zip({'a': b''}, entries=entries))

    def test_link_special_and_unsafe_members(self):
        for name, mode in (('a', stat.S_IFLNK | 0o777), ('a', stat.S_IFIFO | 0o600),
                           ('a', stat.S_IFDIR | 0o755), ('../a', stat.S_IFREG | 0o644),
                           ('/a', stat.S_IFREG | 0o644), ('a\\b', stat.S_IFREG | 0o644)):
            with self.subTest(name=name, mode=mode), self.assertRaises(ValueError):
                parser.Archive(*make_zip({name: b''}, entries=[(name, b'', mode)]))

    def test_encrypted_member_rejected(self):
        args = list(make_zip({'a': b''}))
        raw = bytearray(args[0])
        central = raw.index(b'PK\x01\x02')
        flags = struct.unpack_from('<H', raw, central + 8)[0]
        struct.pack_into('<H', raw, central + 8, flags | 1)
        args[:3] = [bytes(raw), parser.sha(raw), len(raw)]
        with self.assertRaisesRegex(ValueError, 'encrypted'):
            parser.Archive(*args)

    def test_bounds_rejected_before_decompression(self):
        args = make_zip({'data.json': b'{}'})
        for constant, limit in (('MAX_ENTRIES', 1), ('MAX_MEMBER_BYTES', 1),
                                ('MAX_EXPANDED_BYTES', 1), ('MAX_ARCHIVE_BYTES', 1)):
            with self.subTest(constant=constant), mock.patch.object(parser, constant, limit), self.assertRaises(ValueError):
                parser.Archive(*args)

    def test_missing_index_and_duplicate_index(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            info = zipfile.ZipInfo('a')
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, b'')
        raw = stream.getvalue()
        with self.assertRaisesRegex(ValueError, 'missing archive index'):
            parser.Archive(raw, parser.sha(raw), len(raw), '0' * 64, 1)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            parser.Archive(*make_zip({'evidence-index.json': b'{}'}))

    def test_code_member_is_only_hashed_never_imported(self):
        source = b'raise RuntimeError("MUST NEVER EXECUTE")\n'
        archive = parser.Archive(*make_zip({'payload.py': source}))
        self.assertEqual(archive.raw('payload.py'), source)


class CaptureBindingTests(unittest.TestCase):
    def test_explicit_success_status_and_phase_b_scope(self):
        phase_a = {'status': 'CAPTURED_FOR_REVIEW'}
        phase_b = {'status': 'CAPTURED_FOR_INDEPENDENT_REVIEW', 'app_typechecked': True,
                   'distribution_ipa_built': False}
        parser._capture_status('phase-a', phase_a)
        parser._capture_status('phase-b', phase_b)
        for phase, original, changes in (
                ('phase-a', phase_a, {'status': 'FAILED'}),
                ('phase-a', phase_a, {'primary_error': 'failed'}),
                ('phase-b', phase_b, {'status': 'CAPTURED_FOR_REVIEW'}),
                ('phase-b', phase_b, {'app_typechecked': False}),
                ('phase-b', phase_b, {'app_typechecked': 1}),
                ('phase-b', phase_b, {'distribution_ipa_built': True}),
                ('phase-b', phase_b, {'primary_error': 'failed'})):
            with self.subTest(phase=phase, changes=changes), self.assertRaises(ValueError):
                parser._capture_status(phase, original | changes)

    def test_nested_phase_a_archive_and_review_are_exact_raw_bytes(self):
        phase_a = mock.Mock(original_bytes=b'original phase A archive')
        originals = {'prior-phase-a/artifact.zip': phase_a.original_bytes,
                     'prior-phase-a/independent-review.json': b'{"status":"reviewed"}'}
        files = {'evidence/phase-a/independent-review.json': originals['prior-phase-a/independent-review.json']}
        phase_b = mock.Mock(raw=lambda name: originals[name])
        parser._retained_prior_bytes(phase_a, phase_b, files)
        for member in originals:
            mutated = dict(originals)
            mutated[member] += b'\n'
            with self.subTest(member=member), self.assertRaisesRegex(ValueError, 'nested'):
                parser._retained_prior_bytes(phase_a, mock.Mock(raw=lambda name: mutated[name]), files)


class FileBoundaryTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT.parent / 'p0-login-builder-checks'
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='parser-files-', dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_regular_file_and_limits(self):
        path = self.root / 'data'
        path.write_bytes(b'abc')
        self.assertEqual(parser.read_regular(path, 3), b'abc')
        with self.assertRaisesRegex(ValueError, 'size'):
            parser.read_regular(path, 2)

    def test_final_symlink_rejected(self):
        (self.root / 'data').write_bytes(b'{}')
        (self.root / 'link').symlink_to(self.root / 'data')
        with self.assertRaises(OSError):
            parser.read_regular(self.root / 'link')

    def test_parent_symlink_rejected(self):
        (self.root / 'real').mkdir()
        (self.root / 'real' / 'data').write_bytes(b'{}')
        (self.root / 'alias').symlink_to(self.root / 'real', target_is_directory=True)
        with self.assertRaises(OSError):
            parser.read_regular(self.root / 'alias' / 'data')

    def test_fifo_is_rejected_without_blocking(self):
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'not a regular file'):
            parser.read_regular(fifo)

    def test_directory_is_not_evidence(self):
        with self.assertRaises((OSError, ValueError)):
            parser.read_regular(self.root)

    def test_replay_rejects_rehashed_original_receipt(self):
        for output, relative in parser.INPUT_FILES.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'fixed original evidence differs'):
            parser.replay(self.root, {})


class LockReplayTests(unittest.TestCase):
    def setUp(self):
        self.old = {'version': 3, 'originHash': '1' * 64, 'pins': [
            {'identity': 'anisettekit', 'kind': 'remoteSourceControl',
             'location': 'https://github.com/NRG-Wardog/AnisetteKit.git',
             'state': {'revision': '161435bcc0344e1a9853669455c1ca8d9dda2bf1'}},
            {'identity': 'other', 'state': {'revision': '2' * 40}}]}
        self.new = copy.deepcopy(self.old)
        self.new['originHash'] = '3' * 64
        self.new['pins'][0]['state']['revision'] = parser.EXPECTED_SUMMARY['final_source_owners']['AnisetteKit']['commit']

    def test_exact_anisette_revision_and_origin_only(self):
        parser._lock_semantics(encode(self.old), encode(self.new))

    def test_lock_rejects_duplicates_stale_origin_and_unrelated_changes(self):
        mutations = []
        value = copy.deepcopy(self.new); value['pins'].append(value['pins'][0]); mutations.append(value)
        value = copy.deepcopy(self.new); value['originHash'] = self.old['originHash']; mutations.append(value)
        value = copy.deepcopy(self.new); value['pins'][1]['state']['revision'] = '4' * 40; mutations.append(value)
        value = copy.deepcopy(self.new); value['pins'][0]['state']['revision'] = '5' * 40; mutations.append(value)
        value = copy.deepcopy(self.new); value['pins'][0]['location'] = 'https://attacker.invalid/AnisetteKit'; mutations.append(value)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parser._lock_semantics(encode(self.old), encode(value))

    def test_complete_child_raw_bytes_and_mode(self):
        before, after = encode(self.old), encode(self.new)
        blob = hashlib.sha1(b'blob ' + str(len(after)).encode() + b'\0' + after).hexdigest()
        old_tree = {'Package.resolved': {'mode': '100644', 'type': 'blob', 'git_object': '1' * 40}}
        new_tree = {'Package.resolved': {'mode': '100644', 'type': 'blob', 'git_object': blob}}
        def run(old=old_tree, new=new_tree, terminal=after, captured_before=before):
            with mock.patch.object(parser, 'git', side_effect=[after, before]), mock.patch.object(parser, '_tree', side_effect=[old, new]):
                parser._lock_child(Path('/unused'), 'parent', 'final', 'Package.resolved', captured_before, after, terminal, 1)
        run()
        for case in ('terminal', 'before', 'mode', 'unrelated'):
            with self.subTest(case=case), self.assertRaises(ValueError):
                if case == 'terminal': run(terminal=after + b'\n')
                elif case == 'before': run(captured_before=before + b'\n')
                elif case == 'mode':
                    changed = copy.deepcopy(new_tree); changed['Package.resolved']['mode'] = '100755'; run(new=changed)
                else:
                    changed = copy.deepcopy(new_tree); changed['unexpected'] = changed['Package.resolved']; run(new=changed)

    def test_raw_commit_hash_and_single_parent(self):
        tree, parent = '1' * 40, '2' * 40
        raw = ('tree ' + tree + '\nparent ' + parent + '\n\nmessage\n').encode()
        oid = hashlib.sha1(b'commit ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        with mock.patch.object(parser, 'git', return_value=raw):
            self.assertEqual(parser._commit(Path('/unused'), oid, tree, [parent]), raw)
            for bad_oid, bad_tree, parents in (('3' * 40, tree, [parent]), (oid, '3' * 40, [parent]),
                                               (oid, tree, []), (oid, tree, [parent, parent])):
                with self.subTest(parents=parents), self.assertRaises(ValueError):
                    parser._commit(Path('/unused'), bad_oid, bad_tree, parents)


if __name__ == '__main__':
    unittest.main()
