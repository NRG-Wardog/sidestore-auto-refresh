"""Read exact maintained owner bytes; no historical fixture fallback."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def owner_pin(owner="LiveContainer"):
    return json.loads((ROOT/"ci/issues-native/candidate-graph.json").read_text())["owners"][owner][1]


def owner_root(owner="LiveContainer"):
    variable = {"LiveContainer": "MAINTAINED_LIVE_CONTAINER_TEST_SOURCE",
                "idevice": "MAINTAINED_IDEVICE_TEST_SOURCE",
                "SideStore": "MAINTAINED_SIDESTORE_TEST_SOURCE",
                "SideStore/Dependencies/SideSign": "MAINTAINED_SIDESIGN_TEST_SOURCE",
                "SideStore/Dependencies/minimuxer": "MAINTAINED_MINIMUXER_TEST_SOURCE"}[owner]
    return Path(os.environ.get(variable, ROOT/"sources"/owner))


def owner_source(source_path, owner="LiveContainer"):
    expected = owner_pin(owner)
    root = owner_root(owner)
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != expected:
        raise ValueError(f'{owner} checkout is not the current pinned owner')
    committed = subprocess.check_output(['git', '-C', str(root), 'show', f'{expected}:{source_path}'])
    path = root/source_path
    if path.is_symlink() or path.read_bytes() != committed:
        raise ValueError(f'{owner} source differs from its pinned Git blob')
    return committed.decode('utf-8')
