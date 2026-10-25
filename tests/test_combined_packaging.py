from pathlib import Path
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from package_livecontainer_combined import (
    adapt,
    share_packaged_app_groups,
    verify_host_intent_runtime_symbols,
    verify_auth_answer_transport,
    verify_shared_secret_handoff_group,
    verify_side_store_intent_runtime_symbols,
)


class CombinedPackagingTests(unittest.TestCase):
    def test_auth_answer_route_and_both_xpc_endpoints_are_required(self):
        host = b"authRespond"
        service = b"V3SideStoreService\x00execute:reply:\x00authRespond"
        endpoint = b"v3Execute:reply:"
        verify_auth_answer_transport(host, service, endpoint)
        for missing in (b"V3SideStoreService", b"execute:reply:", b"authRespond"):
            with self.subTest(missing=missing), self.assertRaisesRegex(
                    ValueError, "current auth answer dispatcher markers"):
                verify_auth_answer_transport(host, service.replace(missing, b""), endpoint)
        with self.assertRaisesRegex(ValueError, "host auth answer request route"):
            verify_auth_answer_transport(b"", service, endpoint)
        with self.assertRaisesRegex(ValueError, "XPC command endpoint"):
            verify_auth_answer_transport(host, service, b"")

    def test_backend_keeps_only_the_runtime_symbols_required_by_host_intents(self):
        verify_side_store_intent_runtime_symbols(
            b"9SideStore20RefreshAllAppsIntentV\x009SideStore26RefreshAllAppsWidgetIntentV")
        with self.assertRaisesRegex(ValueError, "RefreshAllAppsWidgetIntent"):
            verify_side_store_intent_runtime_symbols(b"9SideStore20RefreshAllAppsIntentV")

    def test_packaged_support_contains_every_metadata_targeted_intent_wrapper(self):
        verify_host_intent_runtime_symbols(
            b"16SideStoreSupport20RefreshAllAppsIntentV\x0016SideStoreSupport26RefreshAllAppsWidgetIntentV")
        with self.assertRaisesRegex(ValueError, "RefreshAllAppsWidgetIntent"):
            verify_host_intent_runtime_symbols(b"16SideStoreSupport20RefreshAllAppsIntentV")

    def test_host_and_liveprocess_share_the_entitled_secret_handoff_keychain_group(self):
        group = "AAAAA11111.com.kdt.livecontainer.shared"
        self.assertEqual(verify_shared_secret_handoff_group([group], [group]), group)
        with self.assertRaisesRegex(ValueError, "dedicated entitled Keychain group"):
            verify_shared_secret_handoff_group(["group.com.SideStore.SideStore"], [group])

    def test_every_extension_gets_the_hosts_packaged_app_group_fallback(self):
        # The service resolves the shared store from Bundle.main, which inside
        # LiveProcess is the extension. Only the host declares ALTAppGroups, so
        # a launch that published no group would have ranked an empty list there
        # while the host ranked its own: two different stores, silently.
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'LiveContainer.app'
            (app / 'PlugIns' / 'LiveProcess.appex').mkdir(parents=True)
            (app / 'PlugIns' / 'ShareExtension.appex').mkdir(parents=True)
            (app / 'PlugIns' / 'Broken.appex').mkdir(parents=True)
            packaged = ['group.com.SideStore.SideStore']
            (app / 'Info.plist').write_bytes(plistlib.dumps({'ALTAppGroups': packaged}))
            for extension in ('LiveProcess', 'ShareExtension'):
                (app / 'PlugIns' / (extension + '.appex') / 'Info.plist').write_bytes(
                    plistlib.dumps({'CFBundleIdentifier': extension}))
            share_packaged_app_groups(app)
            for extension in ('LiveProcess', 'ShareExtension'):
                info = plistlib.loads((app / 'PlugIns' / (extension + '.appex') / 'Info.plist').read_bytes())
                self.assertEqual(info['ALTAppGroups'], packaged, extension)
                self.assertEqual(info['CFBundleIdentifier'], extension, 'the plist must be preserved')
            # It runs again on every packaging pass and must be idempotent.
            before = (app / 'PlugIns' / 'LiveProcess.appex' / 'Info.plist').read_bytes()
            share_packaged_app_groups(app)
            self.assertEqual((app / 'PlugIns' / 'LiveProcess.appex' / 'Info.plist').read_bytes(), before)
            # An extension with no plist is left for the build, not invented.
            self.assertFalse((app / 'PlugIns' / 'Broken.appex' / 'Info.plist').exists())
            # A host with no fallback has nothing to share, which must fail.
            (app / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'host'}))
            with self.assertRaisesRegex(ValueError, "declares no packaged App Group fallback"):
                share_packaged_app_groups(app)

    def test_upstream_adapter_retains_transformations(self):
        script = '''wget https://github.com/LiveContainer/dylibify/releases/download/1.0/dylibify
brew install ldid
wget https://github.com/LiveContainer/SideStore/releases/download/nightly/SideStore.ipa
./dylibify ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore.dylib
mv widget destination
rm -r .zsign_cache
find payloadlc/Payload -type d -name "_CodeSignature" -exec rm -r {} +
# copy intents
cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition ./Payload/LiveContainer.app/
cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/ViewApp.intentdefinition ./Payload/LiveContainer.app/
cp -r ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents ./Payload/LiveContainer.app/Metadata.appintents
sed -i '' 's/9SideStore20RefreshAllAppsIntentV/16SideStoreSupport20RefreshAllAppsIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata
sed -i '' 's/9SideStore26RefreshAllAppsWidgetIntentV/16SideStoreSupport26RefreshAllAppsWidgetIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata
# package
zip output Payload
'''
        result = adapt(script)
        self.assertIn('cp "$PATCHED_SIDESTORE_IPA" SideStore.ipa', result)
        self.assertIn('--verify-dylibify', result)
        self.assertNotIn('wget ', result)
        self.assertIn('cp "$VERIFIED_DYLIBIFY" dylibify', result)
        self.assertIn('rm -rf ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents', result)
        self.assertLess(result.index('--prepare-entitlements'), result.index('zip output'))
        self.assertTrue(result.startswith('set -eu\n'))
        self.assertNotIn('find payloadlc/', result)

    def test_adapter_fails_closed_on_changed_upstream(self):
        with self.assertRaises(ValueError):
            adapt('echo upstream changed')

    def test_adapter_rejects_duplicate_download_anchor(self):
        with self.assertRaises(ValueError):
            adapt('brew install ldid\nbrew install ldid\n')

    def test_adapter_stages_host_intents_then_removes_backend_metadata_inputs(self):
        script = '''wget https://github.com/LiveContainer/dylibify/releases/download/1.0/dylibify
brew install ldid
wget https://github.com/LiveContainer/SideStore/releases/download/nightly/SideStore.ipa
./dylibify ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore.dylib
rm -r .zsign_cache
find payloadlc/Payload -type d -name "_CodeSignature" -exec rm -r {} +
# copy intents
cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition ./Payload/LiveContainer.app/
cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/ViewApp.intentdefinition ./Payload/LiveContainer.app/
cp -r ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents ./Payload/LiveContainer.app/Metadata.appintents
sed -i '' 's/9SideStore20RefreshAllAppsIntentV/16SideStoreSupport20RefreshAllAppsIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata
sed -i '' 's/9SideStore26RefreshAllAppsWidgetIntentV/16SideStoreSupport26RefreshAllAppsWidgetIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata
# package
zip output Payload
'''
        result = adapt(script)
        self.assertLess(result.index('cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition'),
                        result.index('rm -f ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition'))
        self.assertIn('rm -rf ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents', result)
        self.assertIn('cp -r ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents ./Payload/LiveContainer.app/Metadata.appintents', result)

    def test_semantic_verifier_requires_embedded_startup_hook_contract(self):
        source = (Path(__file__).resolve().parents[1] / 'scripts' / 'package_livecontainer_combined.py').read_text(encoding='utf-8')
        self.assertIn("LiveContainerShared.framework/LiveContainerShared", source)
        self.assertIn("b'installSideStoreHooks' in bootstrap_code", source)
        self.assertIn("b'EMBEDDED_SIDESTORE_STARTUP_FIX_V1'", source)
