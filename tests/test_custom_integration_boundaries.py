"""Representative upstream drift exercises against the real packaging adapter.

This is neither shell execution nor a device/real-upstream merge claim.
"""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from package_livecontainer_combined import adapt


def upstream_fixture():
    # Reuse the maintained synthetic fixture without duplicating either fixture
    # bytes or production adaptation logic in this regression suite.
    tree = ast.parse((ROOT / 'tests/test_combined_packaging.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'test_upstream_adapter_retains_transformations':
            for statement in node.body:
                if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'script' for t in statement.targets):
                    return ast.literal_eval(statement.value)
    raise AssertionError('maintained upstream packaging fixture not found')


class CustomIntegrationBoundaryTests(unittest.TestCase):
    def test_benign_upstream_changes_preserve_both_upstream_and_custom_behavior(self):
        baseline = upstream_fixture()
        before = '# representative new upstream comment\necho upstream-added-resource-step\n'
        after = '\necho upstream-final-step\n'
        candidate = before + baseline + after
        result = adapt(candidate)
        self.assertIn(before, result)
        self.assertTrue(result.endswith(after))
        for seam in ('cp "$VERIFIED_DYLIBIFY" dylibify',
                     'cp "$PATCHED_SIDESTORE_IPA" SideStore.ipa',
                     '--verify-dylibify', '--prepare-entitlements',
                     '16SideStoreSupport20RefreshAllAppsIntentV',
                     '16SideStoreSupport26RefreshAllAppsWidgetIntentV'):
            with self.subTest(seam=seam):
                self.assertEqual(result.count(seam), adapt(baseline).count(seam))
                self.assertGreater(result.count(seam), 0)
        self.assertLess(result.index('--prepare-entitlements'), result.index('zip output Payload'))
        self.assertNotIn('wget ', result)
        self.assertEqual(adapt(candidate), result, 'adapter must be deterministic and stateless')
        self.assertEqual(candidate, before + baseline + after, 'upstream input is immutable')

    def test_changed_required_upstream_anchor_rejects_instead_of_losing_custom_behavior(self):
        candidate = upstream_fixture().replace('brew install ldid', 'brew install different-signer')
        with self.assertRaisesRegex(ValueError, 'Upstream packaging anchor changed'):
            adapt(candidate)

    def test_duplicate_required_anchor_rejects_instead_of_patching_only_one_copy(self):
        candidate = upstream_fixture() + '\nbrew install ldid\n'
        with self.assertRaisesRegex(ValueError, 'Upstream packaging anchor changed'):
            adapt(candidate)

    def test_already_adapted_input_cannot_be_silently_double_patched(self):
        with self.assertRaisesRegex(ValueError, 'Upstream packaging anchor changed'):
            adapt(adapt(upstream_fixture()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
