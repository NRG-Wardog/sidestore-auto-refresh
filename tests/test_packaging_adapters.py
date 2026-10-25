"""Reusable named adapter behavior and immutable real-upstream parity witnesses."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from packaging_adapters import COMBINED_PACKAGING_RULES, IntegrationRule, adapt_combined_packaging, apply_rules, configure_anchors, patch_surface
from package_livecontainer_combined import adapt
from rehearse_packaging_integration import rehearse, rehearse_bytes

FIXTURES = ROOT / 'tests/fixtures/upstream-packaging'


class PackagingAdapterTests(unittest.TestCase):
    def fixture(self):
        return (FIXTURES / '433dd73-build_github.sh.txt').read_text()

    def test_real_revision_byte_parity_with_pre_refactor_adapter(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        for row in manifest['revisions']:
            with self.subTest(commit=row['source_commit']):
                source = (FIXTURES / row['fixture']).read_bytes()
                self.assertEqual(hashlib.sha256(source).hexdigest(), row['source_sha256'])
                profile = None
                if 'anchor_profile' in row:
                    with self.assertRaisesRegex(ValueError, 'compiled-sidestore'):
                        adapt(source.decode())
                    profile = json.loads((FIXTURES / row['anchor_profile']).read_text())
                    rules = configure_anchors(COMBINED_PACKAGING_RULES, profile['anchor_overrides'])
                    result = ('set -eu\n' + apply_rules(source.decode(), rules)).encode()
                    self.assertIn(row['preserved_upstream_delta'].encode(), result)
                    self.assertEqual(result.replace(row['preserved_upstream_delta'].encode(), b''),
                                     adapt(self.fixture()).encode())
                else:
                    result = adapt(source.decode()).encode()
                    self.assertEqual(adapt_combined_packaging(source.decode()).encode(), result)
                self.assertEqual(hashlib.sha256(result).hexdigest(), row['adapted_sha256'])
                receipt = rehearse_bytes(source, row['source_commit'], profile)
                self.assertEqual(receipt['adapted_sha256'], row['adapted_sha256'])
                self.assertFalse(receipt['source_shell_executed'])

    def test_reviewed_anchor_profile_rejects_drift_wrong_revision_and_unknown_seams(self):
        source = (FIXTURES / '2d092098-build_github.sh.txt').read_bytes()
        profile = json.loads((FIXTURES / '2d092098-anchor-profile.json').read_text())
        commit = profile['source_commit']
        for candidate, revision, configuration in (
                (source + b'\n', commit, profile),
                (source, 'a' * 40, profile),
                (source, commit, dict(profile, anchor_overrides={'unknown': 'no'})),
                (source, commit, dict(profile, anchor_overrides={'compiled-sidestore': ''})),
                (source, commit, dict(profile, replacement='unreviewed code'))):
            with self.assertRaises(ValueError):
                rehearse_bytes(candidate, revision, configuration)
        duplicate = source + profile['anchor_overrides']['compiled-sidestore'].encode()
        matching = dict(profile, source_sha256=hashlib.sha256(duplicate).hexdigest())
        with self.assertRaisesRegex(ValueError, 'compiled-sidestore'):
            rehearse_bytes(duplicate, commit, matching)

    def test_minimal_surface_is_documented_exactly(self):
        documented = json.loads((ROOT / 'docs/PACKAGING_PATCH_SURFACE.json').read_text())
        self.assertEqual(documented['rules'], patch_surface(COMBINED_PACKAGING_RULES))
        self.assertEqual(len(documented['rules']), 8)
        self.assertTrue(all(row['owner'] and row['rationale'] and row['exact_occurrences'] == 1
                            for row in documented['rules']))

    def test_every_missing_or_duplicated_required_seam_rejects_with_name(self):
        source = self.fixture()
        for rule in COMBINED_PACKAGING_RULES:
            for candidate in (source.replace(rule.anchor, '', 1), source + '\n' + rule.anchor):
                with self.subTest(rule=rule.name):
                    with self.assertRaisesRegex(ValueError, rule.name):
                        adapt(candidate)

    def test_unrelated_upstream_changes_survive_without_new_rules(self):
        source = self.fixture()
        additions = ('# newer upstream resource preparation\n', '\n# newer upstream final metadata\n')
        result = adapt(additions[0] + source + additions[1])
        self.assertEqual(result, 'set -eu\n' + additions[0] + adapt(source)[len('set -eu\n'):] + additions[1])

    def test_engine_reusable_without_combined_packaging_knowledge(self):
        rule = IntegrationRule('stable-hook', 'test-owner', 'test rationale', 'UPSTREAM', 'CUSTOM')
        self.assertEqual(apply_rules('before UPSTREAM after', (rule,)), 'before CUSTOM after')
        self.assertEqual(apply_rules('unchanged', ()), 'unchanged')
        for rules in ((rule, rule), (replace(rule, name=''),), (replace(rule, anchor=''),),
                      (replace(rule, owner=''),), (replace(rule, rationale=''),)):
            with self.assertRaises(ValueError):
                apply_rules('UPSTREAM', rules)

    def test_pure_adapter_cannot_execute_shell(self):
        source = self.fixture() + '\n$(touch /tmp/never-run-issue52)\n'
        self.assertIn('$(touch /tmp/never-run-issue52)', adapt(source))
        # No shell/subprocess exists in the pure module. Rehearsal uses only Git data reads.
        module = (ROOT / 'scripts/packaging_adapters.py').read_text()
        self.assertNotIn('subprocess', module)
        self.assertNotIn('os.system', module)

    def test_rehearsal_requires_immutable_commit_not_branch_or_shell_input(self):
        for invalid in ('HEAD', 'main', 'a'*39, 'a'*41, '; touch /tmp/no', 'A'*40):
            with self.assertRaises(ValueError):
                rehearse(ROOT, invalid)

    def test_rehearsal_reads_real_local_git_blob_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / 'repo'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()
            git('init', '-q')
            (repo / '.github').mkdir()
            (repo / '.github/build_github.sh').write_text(self.fixture() + '\nexit 77\n')
            git('add', '.github/build_github.sh')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '-qm', 'Immutable fixture revision')
            commit = git('rev-parse', 'HEAD')
            result = rehearse(repo, commit)
            self.assertEqual(result['source_commit'], commit)
            self.assertFalse(result['source_shell_executed'])
            self.assertFalse(result['native_compilation'])
            self.assertEqual(len(result['rules']), 8)


if __name__ == '__main__':
    unittest.main(verbosity=2)
