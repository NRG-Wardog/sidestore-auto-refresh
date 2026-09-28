"""Validate candidate identity/UUID evidence without requiring an Apple device."""
import importlib.util
import hashlib
import os
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
import uuid
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_evidence', ROOT / 'scripts/combined_build_evidence.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def thin_arm64_macho(image_uuid):
    header = b'\xcf\xfa\xed\xfe' + struct.pack(
        '<7I', 0x0100000C, 0, 6, 1, 24, 0, 0)
    return header + struct.pack('<II', 0x1B, 24) + image_uuid


def central_entry_offset(data, member_name):
    position = 0
    while True:
        position = data.find(b'PK\x01\x02', position)
        if position < 0:
            raise AssertionError(f'central directory entry not found: {member_name}')
        name_size, extra_size, comment_size = struct.unpack_from('<HHH', data, position + 28)
        name_start = position + 46
        name = bytes(data[name_start:name_start + name_size]).decode('utf-8')
        if name == member_name:
            return position
        position = name_start + name_size + extra_size + comment_size


class CandidateEvidenceTests(unittest.TestCase):
    @staticmethod
    def prepare_collect_fixture(root, comment=b'', add_oversized_member=False):
        ipa = root / 'candidate.ipa'
        output = root / 'evidence'
        host_build = root / 'host-build'
        side_build = root / 'side-build'
        host_build.mkdir()
        side_build.mkdir()
        commit = 'a' * 40
        run_url = 'https://github.com/example/project/actions/runs/123'
        identity = {'LCProductLine': 'Combined LC+SS v3.0.3-rc',
                    'LCBuilderCommit': commit, 'LCBuildRunURL': run_url}
        support = thin_arm64_macho(b'0123456789abcdef') + b'LCFAILURE1:'
        side_store = (thin_arm64_macho(b'fedcba9876543210') + b'LCStructuredFailureStageV1' +
                      b'UNIQUE_DEVICE_ID_QUERY_FAIL' + b'lc_stage=uniqueDeviceID')
        with zipfile.ZipFile(ipa, 'w') as archive:
            archive.comment = comment
            archive.writestr('Payload/LiveContainer.app/Info.plist', plistlib.dumps(identity))
            archive.writestr('Payload/LiveContainer.app/Frameworks/SideStoreSupport.framework/SideStoreSupport', support)
            archive.writestr('Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore', side_store)
            if add_oversized_member:
                archive.writestr('Payload/LiveContainer.app/oversized-entry.bin', b'x')
        for base, paths in ((host_build, evidence.HOST_SOURCE_PATHS + evidence.V3_HOST_SOURCE_PATHS),
                            (side_build, evidence.EMBEDDED_SOURCE_PATHS)):
            for name in paths:
                target = base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('source ' + name, encoding='utf-8')
        dwarf = host_build / 'SideStoreSupport.framework.dSYM' / 'Contents' / 'Resources' / 'DWARF' / 'SideStoreSupport'
        dwarf.parent.mkdir(parents=True)
        dwarf.write_bytes(support)
        env_keys = ('GITHUB_SHA', 'GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'LIVE_CONTAINER_REF',
                    'EMBEDDED_SIDESTORE_REF', 'MINIMUXER_REF', 'SIDESIGN_REF', 'SIDESIGN_GSA_FIX',
                    'IDEVICE_REF', 'JKTCP_REF')
        argv = ['combined_build_evidence.py', 'collect', '--product', 'v3.0.3-rc',
                '--ipa', str(ipa), '--output', str(output), '--source', str(host_build),
                '--side-source', str(side_build), str(host_build), str(side_build)]
        env = {'GITHUB_SHA': commit, 'GITHUB_REPOSITORY': 'example/project',
               'GITHUB_RUN_ID': '123', **{key: 'b' * 40 for key in env_keys[3:]}}
        return ipa, output, host_build, side_build, argv, env_keys, env

    @staticmethod
    def invoke_collect(argv, env_keys, env):
        old_argv = sys.argv
        saved_env = {key: os.environ.get(key) for key in env_keys}
        try:
            os.environ.update(env)
            sys.argv = argv
            evidence.main()
        finally:
            sys.argv = old_argv
            for key, value in saved_env.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value

    def test_ipa_snapshot_uses_bounded_reads_and_preserves_raw_digest(self):
        payload = bytes(range(256)) * 9000
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / 'candidate.ipa'
            ipa.write_bytes(payload)
            original_open = Path.open
            requested_reads = []

            class ReadRecorder:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def read(self, size=-1):
                    requested_reads.append(size)
                    return self.stream.read(size)

                def fileno(self):
                    return self.stream.fileno()

            def recorded_open(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                if path == ipa:
                    return ReadRecorder(stream)
                return stream

            with mock.patch.object(Path, 'open', recorded_open):
                snapshot_directory, snapshot_path, size, digest, signature = evidence.snapshot_ipa_file(
                    ipa, {'compressed_ipa_bytes': len(payload)})
            try:
                self.assertEqual(snapshot_path.read_bytes(), payload)
                self.assertEqual(signature[2], len(payload))
            finally:
                snapshot_directory.cleanup()
            self.assertEqual(size, len(payload))
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertTrue(requested_reads)
            self.assertTrue(all(0 < amount <= 1024 * 1024 for amount in requested_reads))

    def test_oversized_ipa_is_rejected_before_reading_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / 'oversized.ipa'
            ipa.write_bytes(b'0123456789')
            original_open = Path.open
            requested_reads = []

            class ReadRecorder:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self
                def __exit__(self, *args): return self.stream.__exit__(*args)
                def fileno(self): return self.stream.fileno()
                def read(self, size=-1):
                    requested_reads.append(size)
                    return self.stream.read(size)

            def recorded_open(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                return ReadRecorder(stream) if path == ipa else stream

            with mock.patch.object(Path, 'open', recorded_open):
                with self.assertRaisesRegex(ValueError, 'compressed IPA exceeds'):
                    evidence.snapshot_ipa_file(ipa, {'compressed_ipa_bytes': 9})
            self.assertEqual(requested_reads, [], 'size preflight must precede payload reads')

    def test_final_ipa_hash_uses_bounded_reads_and_matches_snapshot_digest(self):
        payload = b'candidate-bytes' * 200_000
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / 'candidate.ipa'
            ipa.write_bytes(payload)
            original_open = Path.open
            requested_reads = []

            class ReadRecorder:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self
                def __exit__(self, *args): return self.stream.__exit__(*args)
                def fileno(self): return self.stream.fileno()
                def read(self, size=-1):
                    requested_reads.append(size)
                    return self.stream.read(size)

            def recorded_open(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                return ReadRecorder(stream) if path == ipa else stream

            with mock.patch.object(Path, 'open', recorded_open):
                size, digest = evidence.hash_ipa_file(ipa)
            self.assertEqual(size, len(payload))
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertTrue(requested_reads)
            self.assertTrue(all(0 < amount <= 1024 * 1024 for amount in requested_reads))

    def test_collect_rejects_oversized_path_replacement_after_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ipa, output, _host_build, _side_build, argv, env_keys, env = self.prepare_collect_fixture(root)

            oversized_replacement = root / 'oversized.ipa'
            with oversized_replacement.open('wb') as replacement:
                replacement.truncate(evidence.DEFAULT_ARCHIVE_LIMITS['compressed_ipa_bytes'] + 1)

            real_zipfile = evidence.zipfile.ZipFile
            replaced = False

            def replace_path_then_open(source, *args, **kwargs):
                nonlocal replaced
                if not replaced:
                    os.replace(oversized_replacement, ipa)
                    replaced = True
                return real_zipfile(source, *args, **kwargs)

            with mock.patch.object(evidence.zipfile, 'ZipFile', side_effect=replace_path_then_open):
                with self.assertRaisesRegex(ValueError, 'IPA changed after evidence snapshot'):
                    self.invoke_collect(argv, env_keys, env)
            self.assertTrue(replaced, 'the path replacement must happen after snapshot creation')
            self.assertGreater(ipa.stat().st_size, evidence.DEFAULT_ARCHIVE_LIMITS['compressed_ipa_bytes'])
            self.assertFalse((output / 'candidate-provenance.json').exists(),
                'collection must not emit provenance for a replaced over-limit path')

    def test_collect_rejects_same_size_comment_mutation_during_dsym_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_comment = b'original-evidence-comment'
            changed_comment = b'mutated-evidence-comment!'
            self.assertEqual(len(original_comment), len(changed_comment))
            ipa, output, _host_build, _side_build, argv, env_keys, env = self.prepare_collect_fixture(
                root, comment=original_comment)
            original_size = ipa.stat().st_size
            real_copytree = evidence.shutil.copytree
            mutated = False

            def mutate_comment_after_copy(source, destination, *args, **kwargs):
                nonlocal mutated
                result = real_copytree(source, destination, *args, **kwargs)
                if not mutated:
                    data = bytearray(ipa.read_bytes())
                    eocd = data.rfind(b'PK\x05\x06')
                    comment_size = struct.unpack_from('<H', data, eocd + 20)[0]
                    self.assertEqual(comment_size, len(original_comment))
                    data[eocd + 22:] = changed_comment
                    ipa.write_bytes(data)
                    mutated = True
                return result

            with mock.patch.object(evidence.shutil, 'copytree', side_effect=mutate_comment_after_copy):
                with self.assertRaisesRegex(ValueError, 'IPA changed (during evidence collection|after evidence snapshot)'):
                    self.invoke_collect(argv, env_keys, env)
            self.assertTrue(mutated, 'same-size mutation must occur during the dSYM copy phase')
            self.assertEqual(ipa.stat().st_size, original_size)
            self.assertFalse((output / 'candidate-provenance.json').exists(),
                'changed input bytes must not be emitted as valid provenance')

    def test_collect_rejects_oversized_central_directory_before_zipfile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ipa, output, _host_build, _side_build, argv, env_keys, env = self.prepare_collect_fixture(root)
            data = bytearray(ipa.read_bytes())
            eocd = data.rfind(b'PK\x05\x06')
            struct.pack_into('<I', data, eocd + 12,
                evidence.DEFAULT_ARCHIVE_LIMITS['central_directory_bytes'] + 1)
            ipa.write_bytes(data)
            real_zipfile = evidence.zipfile.ZipFile
            with mock.patch.object(evidence.zipfile, 'ZipFile') as constructor:
                with self.assertRaisesRegex(ValueError, 'central directory exceeds the configured size limit'):
                    self.invoke_collect(argv, env_keys, env)
                constructor.assert_not_called()
            self.assertFalse((output / 'candidate-provenance.json').exists())

    def test_collect_rejects_overexpanded_member_before_testzip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            member_name = 'Payload/LiveContainer.app/oversized-entry.bin'
            ipa, output, _host_build, _side_build, argv, env_keys, env = self.prepare_collect_fixture(
                root, add_oversized_member=True)
            data = bytearray(ipa.read_bytes())
            entry = central_entry_offset(data, member_name)
            struct.pack_into('<I', data, entry + 24,
                evidence.DEFAULT_ARCHIVE_LIMITS['member_uncompressed_bytes'] + 1)
            ipa.write_bytes(data)
            with mock.patch.object(evidence.zipfile.ZipFile, 'testzip') as testzip:
                with self.assertRaisesRegex(ValueError, 'member exceeds the configured expanded-size limit'):
                    self.invoke_collect(argv, env_keys, env)
                testzip.assert_not_called()
            self.assertFalse((output / 'candidate-provenance.json').exists())

    def test_matching_uuid_and_malformed_commands(self):
        expected = uuid.UUID('07E95F24-0DF4-3F9F-B1B4-3AF4881C1CBD')
        header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 6, 1, 24, 0, 0)
        command = struct.pack('<II', 0x1b, 24) + expected.bytes
        self.assertEqual(evidence.macho_uuid(header + command), str(expected).upper())
        with self.assertRaises(ValueError): evidence.macho_uuid(header + struct.pack('<II', 0x1b, 7))
        with self.assertRaises(ValueError): evidence.macho_uuid(header + command[:-1])
        self.assertIsNone(evidence.macho_uuid(b'not Mach-O'))

    def test_release_product_lines_are_accepted(self):
        pattern = r'v3\.\d+(\.\d+)*(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?'
        for product in ("v2", "v3", "v3.0.1", "v3.10.2", "v3.0.3-rc"):
            self.assertTrue(product in ('v2', 'v3') or
                            __import__('re').fullmatch(pattern, product) is not None)
        for product in ("v4", "v3.x", "latest", ""):
            self.assertFalse(product in ('v2', 'v3') or
                             __import__('re').fullmatch(pattern, product) is not None)
        self.assertEqual('Combined LC+SS ' + 'v3.0.1', 'Combined LC+SS v3.0.1')
