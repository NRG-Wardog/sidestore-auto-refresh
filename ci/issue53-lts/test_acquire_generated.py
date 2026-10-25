import io
from pathlib import Path
import tarfile
import tempfile
import stat
import zipfile
import unittest
from unittest.mock import patch
import acquire_generated

class ArtifactTests(unittest.TestCase):
    def archive(self,name,kind=tarfile.REGTYPE):
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as tar:
            entry=tarfile.TarInfo(name);entry.type=kind;entry.mode=0o644
            if kind==tarfile.REGTYPE:entry.size=4;tar.addfile(entry,io.BytesIO(b'data'))
            else:entry.linkname='/outside';tar.addfile(entry)
        return data.getvalue()

    def test_traversal_links_and_special_entries_rejected_before_writes(self):
        for name,kind in [('GeneratedDependencies/../outside',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),
                          ('GeneratedDependencies/link',tarfile.SYMTYPE),('GeneratedDependencies/hard',tarfile.LNKTYPE),
                          ('GeneratedDependencies/fifo',tarfile.FIFOTYPE)]:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp).resolve()
                with self.assertRaises(ValueError):acquire_generated.extract_tar(self.archive(name,kind),root)
                self.assertEqual(list(root.iterdir()),[])

    def test_regular_bytes_materialize_without_existing_file_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();raw=self.archive('GeneratedDependencies/OpenSSL35/file')
            acquire_generated.extract_tar(raw,root)
            self.assertEqual((root/'GeneratedDependencies/OpenSSL35/file').read_bytes(),b'data')
            with self.assertRaises(FileExistsError):acquire_generated.extract_tar(raw,root)

    def test_outer_zip_rejects_all_special_file_types(self):
        entry=zipfile.ZipInfo('known');entry.file_size=4
        for kind in [0,stat.S_IFREG]:
            entry.external_attr=(kind|0o644)<<16
            acquire_generated.validate_zip_entries([entry],{'known'})
        for kind in [stat.S_IFLNK,stat.S_IFIFO,stat.S_IFSOCK,stat.S_IFCHR,stat.S_IFBLK,stat.S_IFDIR]:
            entry.external_attr=(kind|0o644)<<16
            with self.assertRaisesRegex(ValueError,'special entry'):
                acquire_generated.validate_zip_entries([entry],{'known'})

    def test_outer_hash_failure_precedes_unpack_and_dangling_link_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();archive=root/'fake.zip';archive.write_bytes(b'not expected artifact')
            with patch.object(acquire_generated,'extract_tar') as extraction:
                with self.assertRaisesRegex(ValueError,'outer SHA'):acquire_generated.acquire(archive,root/'out')
                extraction.assert_not_called();self.assertFalse((root/'out').exists())
            (root/'link').symlink_to(root/'missing')
            with self.assertRaisesRegex(ValueError,'symlinked'):acquire_generated.acquire(archive,root/'link')

if __name__=='__main__':unittest.main()
