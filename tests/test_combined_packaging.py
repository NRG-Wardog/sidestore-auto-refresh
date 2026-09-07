from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from package_livecontainer_combined import adapt


class CombinedPackagingTests(unittest.TestCase):
    def test_upstream_adapter_retains_transformations(self):
        script = '''brew install ldid
wget https://github.com/LiveContainer/SideStore/releases/download/nightly/SideStore.ipa
./dylibify input output
mv widget destination
rm -r .zsign_cache
find payloadlc/Payload -type d -name "_CodeSignature" -exec rm -r {} +
# package
zip output Payload
'''
        result = adapt(script)
        self.assertIn('cp "$PATCHED_SIDESTORE_IPA" SideStore.ipa', result)
        self.assertIn('./dylibify input output\nmv widget destination', result)
        self.assertLess(result.index('--prepare-entitlements'), result.index('zip output'))
        self.assertTrue(result.startswith('set -eu\n'))
        self.assertNotIn('find payloadlc/', result)

    def test_adapter_fails_closed_on_changed_upstream(self):
        with self.assertRaises(ValueError):
            adapt('echo upstream changed')

    def test_adapter_rejects_duplicate_download_anchor(self):
        with self.assertRaises(ValueError):
            adapt('brew install ldid\nbrew install ldid\n')

    def test_semantic_verifier_requires_embedded_startup_hook_contract(self):
        source = (Path(__file__).resolve().parents[1] / 'scripts' / 'package_livecontainer_combined.py').read_text(encoding='utf-8')
        self.assertIn("LiveContainerShared.framework/LiveContainerShared", source)
        self.assertIn("b'installSideStoreHooks' in bootstrap_code", source)
        self.assertIn("b'EMBEDDED_SIDESTORE_STARTUP_FIX_V1'", source)
