"""Validate the explicit current product test inventory before mandatory discovery."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def verify(root=ROOT):
    root = Path(root)
    manifest = json.loads((root/'ci/contracts/product-tests.json').read_text())
    if set(manifest) != {'schema', 'files'} or manifest['schema'] != 'current-product-test-inventory-v1':
        raise ValueError('Product test inventory schema differs')
    expected = sorted(p.relative_to(root).as_posix() for p in (root/'tests').rglob('test_*.py')
                      if not p.is_relative_to(root/'tests/ci'))
    if not expected or manifest['files'] != expected:
        raise ValueError('Product test discovery inventory differs')
    for name in expected:
        path = root/name
        package = path.parent
        while package.is_relative_to(root/'tests'):
            initializer = package/'__init__.py'
            if not initializer.is_file() or initializer.is_symlink():
                raise ValueError('Product discovery package initializer missing or linked: '+str(initializer))
            package = package.parent
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Linked product test path: '+name)
    return expected

if __name__ == '__main__':
    print('Verified',len(verify()),'product test modules; execution is required separately')
