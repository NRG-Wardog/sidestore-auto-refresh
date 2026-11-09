"""Only the maintained entry replaces the retired unsigned 6.0.0 routes."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
RETIRED = ('build-current.yml', 'livecontainer-build.yml', 'release-dispatch.yml',
           'maintained-entry-contract.yml', 'livecontainer-source-audit.yml',
           'maintained-packaging-contract.yml', 'current-build-contract.yml',
           'issue53-lts-source.yml', 'issue53-pinned-crypto.yml',
           'issue56-zopfli.yml', 'language-switch-zopfli.yml', 'zopfli-package.yml')


class RetiredEntryTests(unittest.TestCase):
    def test_old_entrypoints_and_their_private_contract_are_absent(self):
        for name in RETIRED:
            with self.subTest(name=name):
                self.assertFalse((ROOT/'.github/workflows'/name).exists())
        for folder in ('maintained-build', 'issue55-zopfli', 'issue56-zopfli', 'language-switch-zopfli'):
            self.assertFalse((ROOT/'ci'/folder).exists())

    def test_no_live_workflow_dispatches_or_checks_retired_routes(self):
        for path in (ROOT/'.github/workflows').glob('*.yml'):
            text = path.read_text()
            for name in RETIRED:
                self.assertNotIn(name, text, path.name)
            self.assertNotIn('ci/maintained-build/', text, path.name)
            self.assertNotIn('c7111b580254190a0c037037036aaed49bfce8c6', text, path.name)

    def test_bounded_ci_checks_current_entry_not_old_remote_implementation(self):
        text = (ROOT/'.github/workflows/source-integrity-contract.yml').read_text()
        self.assertIn("-m unittest discover -s ci/current-build -p 'test_*.py' -v", text)
        self.assertIn('needs: source-integrity', text)
        self.assertNotIn('continue-on-error:', text)
        self.assertNotIn('MAINTAINED_IMPLEMENTATION_ROOT', text)


if __name__ == '__main__':
    unittest.main()
