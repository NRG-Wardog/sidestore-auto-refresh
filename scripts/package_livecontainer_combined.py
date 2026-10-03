"""Run pinned upstream combined packaging using the locally patched SideStore."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess

from audit_ipa_signing import inventory

REQUIRED_SIDESTORE_INTENT_SYMBOLS = (
    b"9SideStore20RefreshAllAppsIntentV",
    b"9SideStore26RefreshAllAppsWidgetIntentV",
)
REQUIRED_HOST_INTENT_SYMBOLS = (
    b"16SideStoreSupport20RefreshAllAppsIntentV",
    b"16SideStoreSupport26RefreshAllAppsWidgetIntentV",
)
REQUIRED_SIDESTORE_AUTH_ANSWER_MARKERS = (
    b"V3SideStoreService",
    b"execute:reply:",
    b"authRespond",
)


def verify_side_store_intent_runtime_symbols(executable):
    missing = [symbol.decode("ascii") for symbol in REQUIRED_SIDESTORE_INTENT_SYMBOLS
               if symbol not in executable]
    if missing:
        raise ValueError("headless backend is missing host App Intent runtime adapters: " + ", ".join(missing))


def verify_host_intent_runtime_symbols(executable):
    missing = [symbol.decode("ascii") for symbol in REQUIRED_HOST_INTENT_SYMBOLS
               if symbol not in executable]
    if missing:
        raise ValueError("SideStoreSupport is missing metadata-targeted App Intent wrappers: " + ", ".join(missing))


def verify_auth_answer_transport(host_executable, side_executable, support_executable):
    """Require linked auth request route, service dispatcher, and XPC endpoint.

    These archive markers only establish that the expected entry points and
    operation route are present in the images. Source-backed contract tests
    verify that the route validates and forwards the bounded answer; marker
    presence alone is not runtime evidence.
    """
    if b"authRespond" not in host_executable:
        raise ValueError("host auth answer request route is missing")
    missing_service = [marker.decode("ascii") for marker in REQUIRED_SIDESTORE_AUTH_ANSWER_MARKERS
                       if marker not in side_executable]
    if missing_service:
        raise ValueError("embedded SideStore is missing current auth answer dispatcher markers: "
                         + ", ".join(missing_service))
    if b"v3Execute:reply:" not in support_executable:
        raise ValueError("XPC command endpoint is missing")


def verify_shared_secret_handoff_group(host_groups, live_process_groups):
    if not isinstance(host_groups, list) or not isinstance(live_process_groups, list):
        raise ValueError("host and LiveProcess Keychain access groups are missing")
    suffix = ".com.kdt.livecontainer.shared"
    shared = [group for group in host_groups
              if isinstance(group, str) and group.endswith(suffix) and group in live_process_groups]
    if len(shared) != 1:
        raise ValueError("host and LiveProcess must share the dedicated entitled Keychain group")
    return shared[0]


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Upstream packaging anchor changed: {old}')
    return text.replace(old, new, 1)


def adapt(text):
    text = replace_once(text, 'brew install ldid', 'command -v ldid >/dev/null')
    text = replace_once(text, 'wget https://github.com/LiveContainer/SideStore/releases/download/nightly/SideStore.ipa',
                        'cp "$PATCHED_SIDESTORE_IPA" SideStore.ipa')
    text = replace_once(text, 'rm -r .zsign_cache', '# No zsign cache exists in the fresh packaging workspace.')
    text = replace_once(text, 'find payloadlc/Payload -type d -name "_CodeSignature" -exec rm -r {} +',
                        'find Payload -type d -name "_CodeSignature" -prune -exec rm -r {} +')
    text = replace_once(text,
        '''# copy intents
cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition ./Payload/LiveContainer.app/
cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/ViewApp.intentdefinition ./Payload/LiveContainer.app/
cp -r ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents ./Payload/LiveContainer.app/Metadata.appintents
sed -i '' 's/9SideStore20RefreshAllAppsIntentV/16SideStoreSupport20RefreshAllAppsIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata
sed -i '' 's/9SideStore26RefreshAllAppsWidgetIntentV/16SideStoreSupport26RefreshAllAppsWidgetIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata
''',
        '''# Stage the host App Intents schemas/metadata from the headless service build,
# then remove these packaging inputs from the embedded backend framework.
cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition ./Payload/LiveContainer.app/
cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/ViewApp.intentdefinition ./Payload/LiveContainer.app/
cp -r ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents ./Payload/LiveContainer.app/Metadata.appintents
sed -i '' 's/9SideStore20RefreshAllAppsIntentV/16SideStoreSupport20RefreshAllAppsIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata
sed -i '' 's/9SideStore26RefreshAllAppsWidgetIntentV/16SideStoreSupport26RefreshAllAppsWidgetIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata
rm -f ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition
rm -f ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/ViewApp.intentdefinition
rm -rf ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents
''')
    text = replace_once(text, '# package\n',
                        'python3 "$COMBINED_PACKAGER" --prepare-entitlements . Payload/LiveContainer.app\n\n# package\n')
    return 'set -eu\n' + text


def prepare_entitlements(root, app):
    sources = {
        '': 'entitlements.xml',
        'PlugIns/LiveProcess.appex': 'LiveProcess/LiveProcess.entitlements',
        'PlugIns/ShareExtension.appex': 'ShareExtension/ShareExtension.entitlements',
        'PlugIns/LaunchAppExtension.appex': 'LaunchAppExtension/LaunchAppExtension.entitlements',
        'PlugIns/LiveWidgetExtension.appex': '.github/sidelc/LiveWidgetExtension_adhoc.xml',
    }
    for relative, source in sources.items():
        bundle = app / relative
        info = plistlib.loads((bundle / 'Info.plist').read_bytes())
        xml = (root / source).read_text()
        values = {'DEVELOPMENT_TEAM': 'AAAAA11111', 'AppIdentifierPrefix': 'AAAAA11111.',
                  'PRODUCT_BUNDLE_IDENTIFIER': info['CFBundleIdentifier'],
                  'APP_GROUP_SIDESTORE': 'group.com.SideStore.SideStore',
                  'APP_GROUP_ALTSTORE': 'group.com.rileytestut.AltStore'}
        for key, value in values.items():
            xml = xml.replace('$(' + key + ')', value)
        if '$(' in xml:
            raise ValueError(f'Unresolved entitlement setting: {source}')
        entitlement = root / 'tmp' / (info['CFBundleExecutable'] + '.entitlements')
        entitlement.write_bytes(plistlib.dumps(plistlib.loads(xml.encode())))
        subprocess.run(['ldid', '-S' + str(entitlement), str(bundle / info['CFBundleExecutable'])], check=True)
    share_packaged_app_groups(app)


def share_packaged_app_groups(app):
    """Give every extension the host's packaged App Group fallback list.

    The host and the embedded service each resolve the shared store from their
    OWN Bundle.main. When the host publishes its selection the service inherits
    it and the fallback never runs, but a launch that published nothing would
    have the host rank its Info.plist ALTAppGroups while the service, running in
    LiveProcess, ranked an empty list and reported no shared store at all. The
    same list in both processes makes the fallback agree, and it is only a
    preference: the ranking still requires the group to be entitled, which
    prepare_entitlements signs in.
    """
    host_info = plistlib.loads((app / 'Info.plist').read_bytes())
    packaged = host_info.get('ALTAppGroups')
    if not isinstance(packaged, list) or not packaged:
        raise ValueError('the host declares no packaged App Group fallback')
    for extension in sorted(p for p in app.glob('PlugIns/*.appex')
                            if (p / 'Info.plist').exists()):
        path = extension / 'Info.plist'
        info = plistlib.loads(path.read_bytes())
        if info.get('ALTAppGroups') == packaged:
            continue
        info['ALTAppGroups'] = packaged
        path.write_bytes(plistlib.dumps(info, sort_keys=False))


def verify(path, side_product=None):
    result = inventory(path)
    bundles = result['bundles']
    base = 'Payload/LiveContainer.app'
    host = bundles[base]['info']
    embedded = base + '/Frameworks/SideStoreApp.framework'
    assert bundles[embedded]['info']['CFBundleIdentifier'] == 'com.SideStore.SideStore', 'iLoader SideStoreLc recognition'
    assert bundles[embedded]['executable_present']
    live_process_path = base + '/PlugIns/LiveProcess.appex'
    shared_keychain_group = verify_shared_secret_handoff_group(
        bundles[base]['signing']['xml_entitlements'].get('keychain-access-groups'),
        bundles[live_process_path]['signing']['xml_entitlements'].get('keychain-access-groups'))
    import zipfile
    with zipfile.ZipFile(path) as archive:
        executable = archive.read(embedded + '/SideStore')
        assert executable[:4] == b'\xcf\xfa\xed\xfe', 'Expected arm64 Mach-O'
        assert struct.unpack_from('<I', executable, 12)[0] == 6, 'SideStore must be MH_DYLIB'
        verify_side_store_intent_runtime_symbols(executable)
        assert archive.read(embedded + '/LCAppInfo.plist')
        assert b'liveContainerAutoRefreshVerification' in executable, 'Patched embedded operation missing'
        host_code = archive.read(base + '/Frameworks/LiveContainerSwiftUI.framework/LiveContainerSwiftUI')
        assert b'liveContainerAutoRefresh' in host_code, 'Host automation missing'
        assert b'V3_UNIFIED_SHELL_V1' in host_code, 'Unified v3 host shell missing'
        assert b'v3SideStoreStatusSnapshot' not in executable, 'Retired status publisher remains'
        assert b'lcReturnToHost' in host_code, 'Guest return action missing from host binary'
        assert b'LCReturnControlPosition' in host_code, 'Movable return control missing'
        assert b'virtual_window_chrome' in host_code, 'Multitasking Return input-layer fix missing'
        bootstrap_code = archive.read(base + '/Frameworks/LiveContainerShared.framework/LiveContainerShared')
        support_code = archive.read(base + '/Frameworks/SideStoreSupport.framework/SideStoreSupport')
        verify_host_intent_runtime_symbols(support_code)
        verify_auth_answer_transport(host_code, executable, support_code)
        assert b'Import Pairing File' in host_code, 'Unified pairing setup missing'
        for code in (host_code, bootstrap_code):
            assert b'CONTROL_COLLAPSED' in code and b'CONTROL_RESTORED' in code, 'Restorable Return control missing'
        assert b'finishRefresh:runID:verification:' in support_code, 'XPC result receiver missing'
        assert b'refreshAllAppsWithIdentifier:mangledTypeName:refreshRunID:' in support_code, 'XPC run identity missing'
        assert b'RESULT_RECEIVED' in support_code, 'Host result persistence missing'
        assert b'installSideStoreHooks' in bootstrap_code, 'Embedded SideStore hook invocation missing'
        assert b'EMBEDDED_SIDESTORE_STARTUP_FIX_V1' in support_code, 'Embedded SideStore startup fix missing'
        for name in ('Intents.intentdefinition', 'ViewApp.intentdefinition', 'Metadata.appintents/extract.actionsdata'):
            assert archive.read(base + '/' + name), name
        metadata = archive.read(base + '/Metadata.appintents/extract.actionsdata')
        assert b'16SideStoreSupport20RefreshAllAppsIntentV' in metadata
        assert b'9SideStore20RefreshAllAppsIntentV' not in metadata
        assert b'16SideStoreSupport26RefreshAllAppsWidgetIntentV' in metadata
        assert b'InstallIPAIntent' not in metadata, 'host metadata still exposes SideStore-owned IPA installation'
        if side_product:
            for source in side_product.rglob('*'):
                if not source.is_file() or 'PlugIns' in source.relative_to(side_product).parts:
                    continue
                relative = source.relative_to(side_product).as_posix()
                if relative in {'Intents.intentdefinition', 'ViewApp.intentdefinition'} or \
                        relative.startswith('Metadata.appintents/'):
                    continue
                if relative == 'SideStore' or '_CodeSignature' in relative:
                    continue
                assert archive.read(embedded + '/' + relative) == source.read_bytes(), relative
    for extension, suffix in [('LiveProcess', 'LiveProcess'), ('ShareExtension', 'ShareExtension'),
                              ('LaunchAppExtension', 'LaunchAppExtension'), ('LiveWidgetExtension', 'LiveWidget')]:
        bundle = bundles[base + '/PlugIns/' + extension + '.appex']
        assert bundle['executable_present']
        assert bundle['info']['CFBundleIdentifier'] == 'com.kdt.livecontainer.' + suffix
        assert bundle['signing']['xml_entitlements'].get('com.apple.security.application-groups')
    assert bundles[base]['signing']['xml_entitlements'].get('keychain-access-groups')
    assert host['ALTAppGroups'] == ['group.com.SideStore.SideStore']
    for extension, suffix in [('LiveProcess', 'LiveProcess'), ('ShareExtension', 'ShareExtension'),
                              ('LaunchAppExtension', 'LaunchAppExtension'), ('LiveWidgetExtension', 'LiveWidget')]:
        # Every extension resolves the same packaged fallback list as the host,
        # so a launch that published no group cannot split the shared store.
        assert bundles[base + '/PlugIns/' + extension + '.appex']['info']['ALTAppGroups'] == host['ALTAppGroups'], \
            extension + ' packaged App Group fallback differs from the host'
    schemes = {s for entry in host['CFBundleURLTypes'] for s in entry['CFBundleURLSchemes']}
    assert {'livecontainer', 'sidestore', 'sidestore-com.kdt.livecontainer'} <= schemes
    assert {'RefreshAllIntent', 'ViewAppIntent'} <= set(host['INIntentsSupported'])
    assert {'RefreshAllIntent', 'ViewAppIntent'} <= set(host['NSUserActivityTypes'])
    assert len(host['BGTaskSchedulerPermittedIdentifiers']) == 2
    assert 'processing' in host['UIBackgroundModes']
    result['semantic_verification'] = 'passed; device signing and runtime unverified'
    result['iloader_special_app'] = 'SideStoreLc'
    result['registration_targets_before_reuse'] = 5
    return result


def package(root, host, side, output):
    for product in (host, side):
        if not (product / 'Info.plist').exists():
            raise ValueError(f'Build product missing: {product}')
    if (root / 'Payload').exists() or (root / 'tmp').exists():
        raise ValueError('Packaging requires a fresh upstream checkout')
    application_dir = root / 'combined.xcarchive/Products/Applications'
    application_dir.mkdir(parents=True)
    shutil.copytree(host, application_dir / 'LiveContainer.app', symlinks=True)
    side_stage = root / 'patched-side/Payload'
    side_stage.mkdir(parents=True)
    shutil.copytree(side, side_stage / 'SideStore.app', symlinks=True)
    side_ipa = root / 'PatchedSideStore.ipa'
    subprocess.run(['zip', '-qry', str(side_ipa), 'Payload'], cwd=side_stage.parent, check=True)
    upstream = (root / '.github/build_github.sh').read_text()
    script = root / 'combined-build.sh'
    script.write_text(adapt(upstream))
    env = dict(os.environ, archive_path='combined', scheme='LiveContainer',
               PATCHED_SIDESTORE_IPA=str(side_ipa), COMBINED_PACKAGER=str(Path(__file__).resolve()))
    subprocess.run(['bash', str(script)], cwd=root, env=env, check=True)
    ipa = root / 'LiveContainer+SideStore.ipa'
    result = verify(ipa, side)
    result['upstream_packaging_sha256'] = hashlib.sha256(upstream.encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ipa, output)
    output.with_suffix('.verification.json').write_text(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-entitlements', nargs=2, type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--host', type=Path)
    parser.add_argument('--side', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.prepare_entitlements:
        prepare_entitlements(*(p.resolve() for p in args.prepare_entitlements))
    else:
        package(*(p.resolve() for p in (args.root, args.host, args.side, args.output)))
