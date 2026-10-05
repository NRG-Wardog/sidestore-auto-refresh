"""Source-built converter regressions; native execution is required on macOS."""
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('packager', ROOT / 'scripts/package_livecontainer_combined.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


FIXUPS_OFFSET = 8192
STARTS_OFFSET = FIXUPS_OFFSET + 28
SEGMENT_STARTS_OFFSET = STARTS_OFFSET + 20


def fixture(converted=False):
    # Structurally valid starts-in-segment records (Apple fixup-chains.h), with
    # one file-backed DATA page whose first pointer terminates a rebase chain.
    # __TEXT, __DATA and __LINKEDIT keep their original addresses/file ranges.
    def segment(name, address, fileoff, filesize, protection):
        return struct.pack('<2I16s4Q4I', 0x19, 72, name, address, 4096,
                           fileoff, filesize, protection, protection, 0, 0)
    pagezero = struct.pack('<2I16s4Q4I', 0x19, 72, b'__PAGEZERO', 0, 0x100000000, 0, 0, 0, 0, 0, 0)
    segments = [segment(b'__TEXT', 0x100000000, 0, 4096, 5),
                segment(b'__DATA', 0x100001000, 4096, 4096, 3),
                segment(b'__LINKEDIT', 0x100002000, 8192, 4096, 1)]
    fixups = struct.pack('<4I', 0x80000034, 16, FIXUPS_OFFSET, 72)
    header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 6 if converted else 2,
                         5, 304, 0x100000 if converted else 0, 0)
    fixup_header = struct.pack('<7I', 0, 28, 72, 72, 0, 1, 0)
    starts = struct.pack('<5I', *(3, 0, 20, 0, 0) if converted else (4, 0, 0, 20, 0))
    segment_starts = struct.pack('<IHHQIHH', 24, 4096, 6, 4096, 0, 1, 0)
    if converted:
        identity = struct.pack('<6I', 0xd, 72, 24, 1, 0, 0) + b'@executable_path/SideStore.dylib\0'
        pagezero = identity.ljust(72, b'\0')
    result = (header + pagezero + b''.join(segments) + fixups).ljust(FIXUPS_OFFSET, b'\0')
    return (result + fixup_header + starts + segment_starts).ljust(12288, b'\0')


class DylibifyValidationTests(unittest.TestCase):
    def verify(self, original, converted):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'SideStore'
            output = Path(directory) / 'SideStore.dylib'
            source.write_bytes(original)
            output.write_bytes(converted)
            packager.verify_dylibify(source, output)

    def test_valid_conversion(self):
        self.verify(fixture(), fixture(True))

    def test_rejects_missing_conversion(self):
        with self.assertRaises(ValueError):
            self.verify(fixture(), fixture())

    def test_rejects_unshifted_chained_starts(self):
        result = bytearray(fixture(True))
        result[STARTS_OFFSET:STARTS_OFFSET + 20] = fixture()[STARTS_OFFSET:STARTS_OFFSET + 20]
        with self.assertRaisesRegex(ValueError, 'chained'):
            self.verify(fixture(), result)

    def test_rejects_uninitialized_id_padding(self):
        result = bytearray(fixture(True))
        result[103] = 42
        with self.assertRaisesRegex(ValueError, 'padded'):
            self.verify(fixture(), result)

    def test_rejects_truncated_commands(self):
        with self.assertRaises(ValueError):
            self.verify(fixture(), fixture(True)[:70])

    def test_only_exact_header_and_payload_changes_are_allowed(self):
        for offset, value in ((8, 0xef), (24, 0xff), (220, 0xaa), (4096, 0xbb),
                              (SEGMENT_STARTS_OFFSET + 4, 0x01), (STARTS_OFFSET + 16, 0xcc)):
            with self.subTest(offset=offset):
                result = bytearray(fixture(True))
                result[offset] = value
                with self.assertRaises(ValueError):
                    self.verify(fixture(), result)

    def test_rejects_missing_required_header_flag(self):
        result = bytearray(fixture(True))
        struct.pack_into('<I', result, 24, 0)
        with self.assertRaisesRegex(ValueError, 'exact header'):
            self.verify(fixture(), result)

    def test_rejects_segment_start_pointing_into_offset_table(self):
        original = bytearray(fixture())
        struct.pack_into('<I', original, STARTS_OFFSET + 12, 4)
        with self.assertRaisesRegex(ValueError, 'segment offset'):
            packager.dylibify_input(original)

    def test_rejects_bad_segment_record_mapping_and_bounds(self):
        for position, format, value in ((SEGMENT_STARTS_OFFSET, '<I', 0xffff),
                                        (SEGMENT_STARTS_OFFSET + 8, '<Q', 8192),
                                        (SEGMENT_STARTS_OFFSET + 20, '<H', 8192),
                                        (SEGMENT_STARTS_OFFSET + 22, '<H', 0x8000)):
            original = bytearray(fixture())
            struct.pack_into(format, original, position, value)
            with self.subTest(position=position), self.assertRaises(ValueError):
                packager.dylibify_input(original)

    def test_rejects_nonempty_legacy_stream_before_conversion(self):
        original = bytearray(fixture())
        # Replace the existing fixups command with a bounded legacy command;
        # its export trie fields may exist, but rebase/bind streams may not.
        legacy = struct.pack('<12I', 0x80000022, 48, 4096, 1, 0, 0, 0, 0, 0, 0, 0, 0)
        original[320:368] = legacy
        struct.pack_into('<I', original, 20, 336)
        with self.assertRaisesRegex(ValueError, 'legacy dyld-info streams'):
            packager.dylibify_input(original)

    def test_rejects_inconsistent_segment_command_before_execution(self):
        original = bytearray(fixture())
        struct.pack_into('<I', original, 32 + 64, 1)  # PAGEZERO now claims a missing section
        with self.assertRaisesRegex(ValueError, 'segment sections'):
            packager.dylibify_input(original)

    def test_rejects_fixups_outside_linkedit_before_execution(self):
        original = bytearray(fixture())
        original[4096:4096 + 72] = original[FIXUPS_OFFSET:FIXUPS_OFFSET + 72]
        struct.pack_into('<I', original, 320 + 8, 4096)
        with self.assertRaisesRegex(ValueError, 'outside LINKEDIT'):
            packager.dylibify_input(original)

    def test_valid_native_fixture_contains_a_real_segment_start(self):
        original = fixture()
        commands, _, fixups = packager.dylibify_input(original)
        self.assertEqual(packager.chained_starts(original, fixups[0]), (0, 0, 20, 0))
        self.assertEqual(struct.unpack_from('<IHHQIHH', original, SEGMENT_STARTS_OFFSET),
                         (24, 4096, 6, 4096, 0, 1, 0))
        self.assertEqual(len([c for c in commands if c[0] == 0x19]), 4)

    def test_packager_downloads_no_converter(self):
        source = (ROOT / 'scripts/package_livecontainer_combined.py').read_text()
        self.assertIn('VERIFIED_DYLIBIFY', source)
        self.assertIn('source hash differs', source)
        self.assertIn('--validate-dylibify-input', source)
        self.assertIn("conversion, 'python3", source)


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS compiler required')
class NativeDylibifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.converter = cls.root / 'dylibify'
        packager.build_dylibify(cls.converter)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_actual_source_converts_chained_fixture(self):
        source = self.root / 'SideStore'
        output = self.root / 'SideStore.dylib'
        source.write_bytes(fixture())
        subprocess.run([str(self.converter), str(source), str(output)], check=True)
        packager.verify_dylibify(source, output)

    def test_actual_source_rejects_invalid_chained_table(self):
        for label, location, value in (('empty', STARTS_OFFSET, 0),
                                       ('pagezero', STARTS_OFFSET + 4, 20)):
            source = self.root / label
            output = self.root / (label + '.dylib')
            data = bytearray(fixture())
            struct.pack_into('<I', data, location, value)
            source.write_bytes(data)
            result = subprocess.run([str(self.converter), str(source), str(output)])
            self.assertNotEqual(result.returncode, 0)

    def test_actual_source_returns_failure_for_missing_input(self):
        result = subprocess.run([str(self.converter), str(self.root / 'missing'), str(self.root / 'missing.dylib')])
        self.assertNotEqual(result.returncode, 0)

    def test_actual_source_returns_failure_for_truncated_input(self):
        source = self.root / 'truncated'
        source.write_bytes(b'\xcf\xfa\xed\xfe')
        result = subprocess.run([str(self.converter), str(source), str(self.root / 'truncated.dylib')])
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
