"""Check consolidation boundaries, without executing historical build recipes."""
from pathlib import Path
import ast
import hashlib
import json
import subprocess
ROOT = Path(__file__).resolve().parents[2]
from source_contract import verify_sources, ARCHIVED_WORKFLOWS, REVIEWED_ADDITIONAL_WORKFLOWS
manifest, successor = verify_sources(ROOT)
for name in manifest['excluded_research_paths']:
    assert not (ROOT / name).exists(), name
for name in manifest['history_heads'].values():
    subprocess.run(['git','merge-base','--is-ancestor',name,'HEAD'], cwd=ROOT, check=True)
original = {Path(p).name for p in manifest['protected_sha256'] if p.startswith('.github/workflows/')}
actual = {p.name for p in (ROOT/'.github/workflows').glob('*.yml')}
assert actual == (original | {'validation-history-contract.yml'} | {Path(p).name for p in successor['added_workflows_sha256']} | {Path(p).name for p in REVIEWED_ADDITIONAL_WORKFLOWS}) - ARCHIVED_WORKFLOWS, actual-original
count = 0
for folder in ('scripts', 'migration', 'tests', 'ci'):
    for path in (ROOT/folder).rglob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
        count += 1
print(f'PASS: both history heads retained; frozen historical and reviewed successor source bytes verified; research excluded; {count} Python sources parse.')
