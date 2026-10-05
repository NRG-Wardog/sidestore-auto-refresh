"""Migrate only the two local jktcp lock identities, never registry resolution."""
import argparse
import hashlib
import json
from pathlib import Path


def replace_identity(text, original, replacement):
    if text.count(original) == 1 and text.count(replacement) == 0:
        return text.replace(original, replacement, 1)
    if text.count(original) == 0 and text.count(replacement) == 1:
        return text
    raise ValueError('Pinned local jktcp lock identity changed or is ambiguous')


def migrate_standalone(text):
    # The pinned source already declares 0.1.6; its own lock was left at 0.1.5.
    before = '[[package]]\nname = "jktcp"\nversion = "0.1.5"\ndependencies = [\n'
    after = before.replace('0.1.5', '0.1.6')
    return replace_identity(text, before, after)


def migrate_workspace(text):
    header = '[[package]]\nname = "jktcp"\nversion = "0.1.6"\n'
    source = 'source = "git+https://github.com/SideStore/jktcp?branch=master#e674e1eee6d5943e13b1eba0bd24a9dd0b2fa020"\n'
    return replace_identity(text, header + source + 'dependencies = [\n',
                            header + 'dependencies = [\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jktcp', type=Path)
    parser.add_argument('idevice', type=Path)
    args = parser.parse_args()
    changes = []
    # Validate both before writing either file.
    for root, transform in [(args.jktcp, migrate_standalone), (args.idevice, migrate_workspace)]:
        path = root / 'Cargo.lock'
        original = path.read_text()
        changes.append((path, original, transform(original)))
    evidence = []
    for path, original, updated in changes:
        path.write_text(updated)
        evidence.append({'path': str(path), 'before_sha256': hashlib.sha256(original.encode()).hexdigest(),
                         'after_sha256': hashlib.sha256(updated.encode()).hexdigest()})
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
