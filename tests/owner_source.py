"""Read exact maintained LiveContainer owner bytes; no historical fixture fallback."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def owner_pin():
    return json.loads((ROOT/"ci/issues-native/candidate-graph.json").read_text())["owners"]["LiveContainer"][1]


def owner_root():
    return Path(os.environ.get("MAINTAINED_LIVE_CONTAINER_TEST_SOURCE", ROOT/"sources/LiveContainer"))


def owner_source(source_path):
    expected = owner_pin()
    root = owner_root()
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != expected:
        raise ValueError('LiveContainer checkout is not the current pinned owner')
    committed = subprocess.check_output(['git', '-C', str(root), 'show', f'{expected}:{source_path}'])
    path = root/source_path
    if path.is_symlink() or path.read_bytes() != committed:
        raise ValueError('LiveContainer source differs from its pinned Git blob')
    return committed.decode('utf-8')
