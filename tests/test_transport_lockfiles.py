import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('locks', ROOT / 'scripts/patch_transport_lockfiles.py')
locks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locks)


class TransportLockfileTests(unittest.TestCase):
    registry = '[[package]]\nname = "unrelated"\nversion = "1.2.3"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\nchecksum = "unchanged"\n\n'

    def test_standalone_only_corrects_local_declared_version_and_replays(self):
        old = self.registry + '[[package]]\nname = "jktcp"\nversion = "0.1.5"\ndependencies = [\n "tokio",\n]\n'
        result = locks.migrate_standalone(old)
        self.assertEqual(result, old.replace('version = "0.1.5"', 'version = "0.1.6"'))
        self.assertEqual(locks.migrate_standalone(result), result)
        self.assertIn(self.registry, result)

    def test_workspace_only_removes_the_verified_git_locator_and_replays(self):
        source = 'source = "git+https://github.com/SideStore/jktcp?branch=master#e674e1eee6d5943e13b1eba0bd24a9dd0b2fa020"\n'
        old = self.registry + '[[package]]\nname = "jktcp"\nversion = "0.1.6"\n' + source + 'dependencies = [\n "tokio",\n]\n'
        result = locks.migrate_workspace(old)
        self.assertEqual(result, old.replace(source, ''))
        self.assertEqual(locks.migrate_workspace(result), result)
        self.assertIn(self.registry, result)

    def test_unexpected_versions_sources_or_duplicate_entries_fail_closed(self):
        good = '[[package]]\nname = "jktcp"\nversion = "0.1.5"\ndependencies = [\n]\n'
        for value in (good.replace('0.1.5', '0.2.0'), good + good,
                      good.replace('dependencies', 'source = "unreviewed"\ndependencies')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                locks.migrate_standalone(value)
        with self.assertRaises(ValueError):
            locks.migrate_workspace(good)

    def test_workflow_keeps_locked_cargo_and_records_precise_migration(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        self.assertNotIn('patch_transport_lockfiles.py jktcp idevice', workflow)
        self.assertIn('transport-source-parity.json', workflow)
        for action in ('test', 'check', 'build'):
            self.assertIn('cargo ' + action + ' --locked', workflow)


if __name__ == '__main__':
    unittest.main()
