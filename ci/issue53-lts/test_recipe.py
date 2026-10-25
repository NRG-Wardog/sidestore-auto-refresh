import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('lts_build',HERE/'build.py')
recipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(recipe)

class RecipeTests(unittest.TestCase):
    def archive(self,root,name='openssl-3.5.9/README.md',link=None):
        path=root/'source.tar.gz'
        with tarfile.open(path,'w:gz') as tar:
            entry=tarfile.TarInfo(name)
            if link:
                entry.type=tarfile.SYMTYPE;entry.linkname=link;tar.addfile(entry)
            else:
                value=b'synthetic archive fixture';entry.size=len(value);tar.addfile(entry,io.BytesIO(value))
        return path

    def test_checksum_precedes_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=self.archive(root);out=root/'unpack'
            with self.assertRaisesRegex(ValueError,'checksum'):recipe.unpack(archive,out,'0'*64)
            self.assertFalse(out.exists())

    def test_verified_regular_source_materializes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=self.archive(root)
            source=recipe.unpack(archive,root/'unpack',recipe.digest(archive))
            self.assertEqual((source/'README.md').read_bytes(),b'synthetic archive fixture')

    def test_traversal_and_symlinks_are_rejected(self):
        for name,link in [('openssl-3.5.9/../../escape',None),('/absolute',None),('other/README',None),('openssl-3.5.9/link','/outside')]:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=self.archive(root,name,link)
                with self.assertRaises(ValueError):recipe.unpack(archive,root/'unpack',recipe.digest(archive))

    def test_ambient_build_injection_is_removed(self):
        keys=['CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','LIBRARY_PATH','PERL5LIB','PERL5OPT',
              'MAKEFLAGS','CONFIG_SITE','CFLAGS','LDFLAGS','OPENSSL_MODULES','DYLD_INSERT_LIBRARIES',
              'LD_PRELOAD','AR','RANLIB','CROSS_COMPILE']
        with patch.dict(recipe.os.environ,{**{key:'untrusted-fixture' for key in keys},'SAFE_FIXTURE':'retained'},clear=True):
            env=recipe.sanitized_environment()
            self.assertFalse(set(keys)&set(env))
            self.assertEqual(env['SAFE_FIXTURE'],'retained')

    def test_paths_stay_absolute_across_build_chdir(self):
        import os
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'child').mkdir()
            try:
                os.chdir(root)
                output,archive=recipe.normalized_paths(Path('out'),Path('source.tar.gz'))
                os.chdir(root/'child')
                self.assertEqual(output,(root/'out').resolve())
                self.assertEqual(archive,(root/'source.tar.gz').resolve())
                self.assertTrue(output.is_absolute() and archive.is_absolute())
            finally:os.chdir(previous)

    def test_symlink_parent_paths_remain_canonical_across_chdir(self):
        import os
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();real=root/'real';real.mkdir()
            alias=root/'alias';alias.symlink_to(real,target_is_directory=True)
            (real/'child').mkdir();(real/'source.tar.gz').write_bytes(b'path-only fixture')
            try:
                os.chdir(alias)
                output,archive=recipe.normalized_paths(alias/'out',alias/'source.tar.gz')
                os.chdir(real/'child')
                self.assertEqual(output,real/'out')
                self.assertEqual(archive,real/'source.tar.gz')
                self.assertNotEqual(output,alias/'out')
                self.assertTrue(output.is_absolute() and archive.is_absolute())
                self.assertEqual(archive.read_bytes(),b'path-only fixture')
            finally:os.chdir(previous)

    def test_output_and_platform_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(recipe.platform,'system',return_value='Linux'),patch.object(recipe.subprocess,'run') as process:
                with self.assertRaisesRegex(ValueError,'native macOS'):recipe.build(Path(temp)/'out')
                process.assert_not_called()
            with patch.object(recipe.platform,'system',return_value='Darwin'),patch.object(recipe.platform,'machine',return_value='arm64'),patch.object(recipe.subprocess,'run') as process:
                with self.assertRaisesRegex(ValueError,'must not exist'):recipe.build(Path(temp))
                process.assert_not_called()

    def test_generated_receipt_cannot_hide_source_or_output_drift(self):
        import json,sys
        sys.path.insert(0,str(HERE))
        import qualify
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'generated').mkdir();(root/'generated/file').write_bytes(b'known')
            receipt={'schema':'issue53-lts-generated-dependency-v1','status':'BUILT_NOT_QUALIFIED_NOT_APP_READY',
                     'source':json.loads((HERE/'source.json').read_text()),'recipe_sha256':recipe.digest(HERE/'build.py'),
                     'product_pins_changed':False,'production_admission':False,'generated_inventory':{'file':'0'*64}}
            (root/'build-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError,'framework/dSYM bytes changed'):qualify.validate_generated(root)
            receipt['source']['sha256']='0'*64
            (root/'build-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError,'recipe/source'):qualify.validate_generated(root)

if __name__=='__main__':unittest.main()
