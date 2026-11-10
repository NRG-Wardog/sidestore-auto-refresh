"""An explicit product inventory must match mandatory recursive discovery."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('product_inventory', ROOT/'ci/contracts/verify_product_tests.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

class ProductInventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root/'ci/contracts').mkdir(parents=True)
        (self.root/'tests/ci/native').mkdir(parents=True)
        (self.root/'tests/test_product.py').write_text('pass\n')
        (self.root/'tests/__init__.py').touch()
        (self.root/'tests/ci/native/test_cli_separate.py').write_text('pass\n')
        self.path = self.root/'ci/contracts/product-tests.json'
        self.path.write_text(json.dumps({'schema':'current-product-test-inventory-v1','files':['tests/test_product.py']}))
    def test_current_inventory_and_separate_ci_scope(self):
        self.assertTrue(v.verify())
        self.assertEqual(v.verify(self.root), ['tests/test_product.py'])
    def test_new_deleted_and_duplicate_modules_reject(self):
        (self.root/'tests/test_new.py').write_text('pass\n')
        with self.assertRaises(ValueError):v.verify(self.root)
        (self.root/'tests/test_new.py').unlink()
        (self.root/'tests/test_product.py').unlink()
        with self.assertRaises(ValueError):v.verify(self.root)
        (self.root/'tests/test_product.py').write_text('pass\n')
        data=json.loads(self.path.read_text());data['files']*=2;self.path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):v.verify(self.root)
    def test_linked_source_rejects(self):
        (self.root/'tests/test_product.py').unlink()
        (self.root/'other.py').write_text('pass\n')
        (self.root/'tests/test_product.py').symlink_to(self.root/'other.py')
        with self.assertRaises(ValueError):v.verify(self.root)

    def test_missing_nested_initializer_cannot_hide_a_registered_suite(self):
        domain = self.root/'tests/domain'; domain.mkdir()
        (domain/'test_nested.py').write_text('pass\n')
        data = json.loads(self.path.read_text())
        data['files'] = ['tests/domain/test_nested.py', 'tests/test_product.py']
        self.path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):v.verify(self.root)
        (domain/'__init__.py').touch()
        self.assertEqual(v.verify(self.root), data['files'])
        (self.root/'tests/__init__.py').unlink()
        with self.assertRaises(ValueError):v.verify(self.root)

    def test_linked_initializer_rejects(self):
        (self.root/'tests/__init__.py').unlink()
        (self.root/'other.py').write_text('pass\n')
        (self.root/'tests/__init__.py').symlink_to(self.root/'other.py')
        with self.assertRaises(ValueError):v.verify(self.root)
