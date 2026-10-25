"""Check consolidation boundaries, without executing historical build recipes."""
from pathlib import Path
import ast
import hashlib
import json
import subprocess
ROOT = Path(__file__).resolve().parents[2]
manifest = json.loads((Path(__file__).with_name('manifest.json')).read_text())
for category in ('protected_sha256', 'archived_workflows_sha256'):
    for name, expected in manifest[category].items():
        path = ROOT / name
        assert path.is_file() and not path.is_symlink(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
for name in manifest['excluded_research_paths']:
    assert not (ROOT / name).exists(), name
for name in manifest['history_heads'].values():
    subprocess.run(['git','merge-base','--is-ancestor',name,'HEAD'], cwd=ROOT, check=True)
original = {Path(p).name for p in manifest['protected_sha256'] if p.startswith('.github/workflows/')}
actual = {p.name for p in (ROOT/'.github/workflows').glob('*.yml')}
assert actual == original | {'validation-history-contract.yml'}, actual-original
count = 0
for folder in ('scripts', 'migration', 'tests', 'ci', '.ci/validation-history'):
    for path in (ROOT/folder).rglob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
        count += 1
print(f'PASS: both history heads retained; protected build bytes unchanged; research excluded; {count} Python sources parse.')
