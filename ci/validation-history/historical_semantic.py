"""Run the retained semantic unit suite in its exact historical layout.

No historical object is executed directly: only the current, retained local files
are copied after byte-for-byte comparison with the pinned baseline objects.
The copied suite uses disposable local Git fixtures and mocked remote calls.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tempfile

from relocation import BASELINE, BASELINE_SHA256, RELOCATED, read, require
from source_contract import ROOT, git_read

INPUTS = (
    '.ci/branch-semantic-review/README.md',
    '.ci/branch-semantic-review/inventory.json',
    '.ci/branch-semantic-review/inventory.py',
    '.ci/branch-semantic-review/source-receipt.json',
    '.ci/branch-semantic-review/test_inventory.py',
    '.github/workflows/reviewed-branch-semantic-inventory.yml',
)


def prepare_fixture(root, destination, object_read=git_read):
    """Verify every input before writing any test fixture file."""
    checked = {}
    for original in INPUTS:
        raw = read(Path(root)/RELOCATED[original])
        baseline = object_read(root, BASELINE, original)
        require(hashlib.sha256(baseline).hexdigest() == BASELINE_SHA256[original],
                'Historical fixture baseline differs: '+original)
        require(raw == baseline, 'Historical fixture input differs: '+original)
        checked[original] = raw
    for original, raw in checked.items():
        path = Path(destination)/original
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def run_tests(root=ROOT):
    with tempfile.TemporaryDirectory(prefix='semantic-history-') as folder:
        fixture = Path(folder)
        prepare_fixture(root, fixture)
        env = {
            'PATH': os.environ['PATH'],
            'HOME': folder,
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'PYTHONDONTWRITEBYTECODE': '1',
        }
        subprocess.run(
            [sys.executable, '-B', '-m', 'unittest', 'discover',
             '-s', str(fixture/'.ci/branch-semantic-review'),
             '-p', 'test_inventory.py', '-v'],
            cwd=fixture, env=env, check=True, timeout=120,
        )


if __name__ == '__main__':
    run_tests()
