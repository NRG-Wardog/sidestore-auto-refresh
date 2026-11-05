import importlib.util
from pathlib import Path
import os
import stat
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('ipa_size_report', Path(__file__).parents[1] / 'scripts/ipa_size_report.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class SizeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ipa = Path(self.temp.name) / 'app.ipa'

    def make(self, compression=zipfile.ZIP_STORED):
        with zipfile.ZipFile(self.ipa, 'w', compression=compression, compresslevel=9 if compression else None) as z:
            z.comment = b'archive comment'
            z.writestr('Payload/App.app/Info.plist', b'plist')
            info = zipfile.ZipInfo('Payload/App.app/App')
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            info.comment = b'member comment'
            info.extra = b'\xfe\xca\x02\x00hi'
            z.writestr(info, b'mach-o signed bytes' * 1000)
            link = zipfile.ZipInfo('Payload/App.app/Frameworks/Test.framework/Versions/Current')
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(link, b'A')
            z.writestr('Payload/App.app/Resources/license.txt', b'license' * 500)
            z.writestr('Payload/App.app/Resources/copy.txt', b'license' * 500)

    def test_recompress_preserves_every_byte_and_metadata(self):
        self.make()
        old, comment = mod.inspect(self.ipa)
        result = mod.optimize(self.ipa)
        new, new_comment = mod.inspect(self.ipa)
        self.assertTrue(result['adopted'])
        self.assertGreater(result['saved_download_bytes'], 0)
        self.assertEqual(result['saved_uncompressed_bundle_bytes'], 0)
        self.assertEqual(mod.semantic_entries(old), mod.semantic_entries(new))
        self.assertEqual(comment, new_comment)
        self.assertTrue(result['content_and_preserved_metadata_verified'])

    def test_duplicates_are_only_candidates_not_deleted(self):
        self.make()
        before = mod.report(self.ipa)
        self.assertEqual(len(before['duplicate_candidates']), 1)
        count = before['entry_count']
        mod.optimize(self.ipa)
        self.assertEqual(mod.report(self.ipa)['entry_count'], count)

    def test_second_pass_never_grows_archive(self):
        self.make()
        mod.optimize(self.ipa)
        old = self.ipa.read_bytes()
        result = mod.optimize(self.ipa)
        self.assertFalse(result['adopted'])
        self.assertEqual(old, self.ipa.read_bytes())

    def test_permissions_preserved(self):
        self.make()
        self.ipa.chmod(0o640)
        mod.optimize(self.ipa)
        self.assertEqual(stat.S_IMODE(self.ipa.stat().st_mode), 0o640)

    def test_duplicate_names_rejected(self):
        self.make()
        with zipfile.ZipFile(self.ipa, 'a') as z:
            z.writestr('Payload/App.app/Info.plist', b'other')
        original = self.ipa.read_bytes()
        with self.assertRaises(ValueError): mod.optimize(self.ipa)
        self.assertEqual(self.ipa.read_bytes(), original)

    def test_path_traversal_and_backslash_rejected(self):
        for name in ('Payload/../outside', '../outside', '/Payload/App.app/file', 'Payload/App.app/..\\outside', 'Payload//App.app/file', 'Payload/App.app/./file'):
            self.make()
            with zipfile.ZipFile(self.ipa, 'a') as z: z.writestr(name, b'x')
            with self.assertRaises(ValueError): mod.inspect(self.ipa)

    def test_member_size_bound(self):
        self.make()
        old = mod.MAX_MEMBER
        try:
            mod.MAX_MEMBER = 10
            with self.assertRaises(ValueError): mod.inspect(self.ipa)
        finally: mod.MAX_MEMBER = old

    def test_entry_count_bound(self):
        self.make()
        old = mod.MAX_ENTRIES
        try:
            mod.MAX_ENTRIES = 1
            with self.assertRaises(ValueError): mod.inspect(self.ipa)
        finally: mod.MAX_ENTRIES = old

    def test_application_required(self):
        with zipfile.ZipFile(self.ipa, 'w') as z: z.writestr('Payload/App.app/file', b'x')
        with self.assertRaises(ValueError): mod.inspect(self.ipa)

    def test_component_size_totals(self):
        self.make()
        report = mod.report(self.ipa)
        self.assertEqual(sum(report['components'].values()), report['uncompressed_bundle_bytes'])
        self.assertIn('not', report['duplicate_note'])


if __name__ == '__main__': unittest.main()
