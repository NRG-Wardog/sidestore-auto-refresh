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
    text = replace_once(text, 'wget https://github.com/LiveContainer/dylibify/releases/download/1.0/dylibify',
                        'cp "$VERIFIED_DYLIBIFY" dylibify')
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
    conversion = './dylibify ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore.dylib'
    text = replace_once(text, conversion, 'python3 "$COMBINED_PACKAGER" --validate-dylibify-input ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore\n' + conversion + '\npython3 "$COMBINED_PACKAGER" --verify-dylibify ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore.dylib')
    return 'set -eu\n' + text


def build_dylibify(output):
    """Compile reviewed, repository-owned source; never run a downloaded binary."""
    source = Path(__file__).resolve().parent / 'vendor/dylibify/main.m'
    expected = '25af0f03181177f1833247ec2385e92ebb82f0f6a9f0e3af43279ec5eb3b03e7'
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise ValueError('reviewed dylibify source hash differs')
    subprocess.run(['xcrun', 'clang', str(source), '-framework', 'Foundation',
                    '-fobjc-arc', '-o', str(output)], check=True)
    return {'source_sha256': expected,
            'executable_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


def conversion_commands(data, filetype):
    if len(data) < 32 or data[:4] != b'\xcf\xfa\xed\xfe':
        raise ValueError('dylibify requires thin 64-bit Mach-O')
    cpu, _, observed, count, size = struct.unpack_from('<5I', data, 4)
    if cpu != 0x0100000c or observed != filetype or size > len(data) - 32:
        raise ValueError('unexpected conversion architecture, type, or commands size')
    commands = []
    offset = 32
    for _ in range(count):
        if offset + 8 > 32 + size:
            raise ValueError('truncated load command')
        command, length = struct.unpack_from('<2I', data, offset)
        if length < 8 or length % 8 or offset + length > 32 + size:
            raise ValueError('invalid load command size')
        payload = data[offset:offset + length]
        if command == 0x19:
            if length < 72 or b'\0' not in payload[8:24]:
                raise ValueError('invalid 64-bit segment command')
            vmaddr, vmsize, fileoff, filesize, _, _, nsects, _ = struct.unpack_from('<4Q4I', payload, 24)
            if length != 72 + 80 * nsects or fileoff > len(data) or filesize > len(data) - fileoff:
                raise ValueError('invalid segment sections or file range')
            if filesize > vmsize:
                raise ValueError('segment file range exceeds virtual range')
        if command in (0x22, 0x80000022):
            if length != 48:
                raise ValueError('invalid legacy dyld-info command')
            # Inherited converter does not parse all ULEB/SLEB opcodes or adjust
            # every segment reference. Only its modern chained route is supported.
            if any(struct.unpack_from('<8I', payload, 8)):
                raise ValueError('nonempty legacy dyld-info streams are unsupported')
        commands.append((command, offset, payload))
        offset += length
    if offset != 32 + size:
        raise ValueError('inconsistent load command count')
    return commands


def chained_starts(data, command, segments=None):
    _, _, payload = command
    if len(payload) != 16:
        raise ValueError('invalid chained-fixups command')
    start, size = struct.unpack_from('<2I', payload, 8)
    commands_end = 32 + struct.unpack_from('<I', data, 20)[0]
    if start < commands_end or start > len(data) or size < 28 or size > len(data) - start:
        raise ValueError('invalid chained-fixups data range')
    version, relative, imports, symbols, import_count, import_format, symbol_format = struct.unpack_from('<7I', data, start)
    if version != 0 or symbol_format != 0:
        raise ValueError('unsupported chained-fixups format')
    if relative < 28 or relative + 4 > size:
        raise ValueError('invalid chained starts offset')
    if imports > size or symbols > size:
        raise ValueError('invalid chained imports or symbols offset')
    if import_count:
        width = {1: 4, 2: 8, 3: 16}.get(import_format)
        if width is None or imports < 28 or symbols < imports or import_count > (symbols - imports) // width:
            raise ValueError('invalid chained imports table')
    count = struct.unpack_from('<I', data, start + relative)[0]
    if not count or count > (size - relative - 4) // 4:
        raise ValueError('invalid chained starts table')
    offsets = struct.unpack_from('<' + 'I' * count, data, start + relative + 4)
    starts_end = min([size] + [value for value in (imports, symbols) if value >= relative])
    if relative + 4 + 4 * count > starts_end:
        raise ValueError('chained starts overlap imports or symbols')
    if segments is None:
        filetype = struct.unpack_from('<I', data, 12)[0]
        segments = [c for c in conversion_commands(data, filetype) if c[0] == 0x19]
    linkedit = [c for c in segments if c[2][8:24].split(b'\0')[0] == b'__LINKEDIT']
    if len(linkedit) != 1:
        raise ValueError('chained fixups require one LINKEDIT segment')
    fileoff, filesize = struct.unpack_from('<2Q', linkedit[0][2], 40)
    if start < fileoff or start + size > fileoff + filesize:
        raise ValueError('chained fixups lie outside LINKEDIT')
    if count != len(segments):
        raise ValueError('chained starts count does not match segments')
    bases = [struct.unpack_from('<Q', c[2], 24)[0] for c in segments
             if c[2][8:24].split(b'\0')[0] == b'__TEXT']
    if len(bases) != 1:
        raise ValueError('chained fixups require one TEXT base')
    used = []
    for index, offset in enumerate(offsets):
        if not offset:
            continue
        record = relative + offset
        if offset < 4 + 4 * count or record + 22 > starts_end:
            raise ValueError('invalid chained starts segment offset')
        record_size, page_size, pointer_format, segment_offset, _, pages = struct.unpack_from('<IHHQIH', data, start + record)
        if record_size < 22 + pages * 2 or record_size > starts_end - record:
            raise ValueError('invalid chained starts segment size')
        if page_size not in (0x1000, 0x4000) or pointer_format not in (1, 2, 6, 7, 9, 12):
            raise ValueError('unsupported chained page or pointer format')
        vmaddr, vmsize = struct.unpack_from('<2Q', segments[index][2], 24)
        if vmaddr < bases[0] or segment_offset != vmaddr - bases[0] or pages > (vmsize + page_size - 1) // page_size:
            raise ValueError('chained starts segment mapping differs')
        if any(a < record + record_size and record < b for a, b in used):
            raise ValueError('overlapping chained starts segments')
        used.append((record, record + record_size))
        for page_start in struct.unpack_from('<' + 'H' * pages, data, start + record + 22):
            if page_start != 0xffff and (page_start >= page_size or page_start & 0x8000):
                raise ValueError('invalid or unsupported chained page start')
    return offsets


def dylibify_input(data):
    """Reject unsupported/malformed input before invoking the native converter."""
    commands = conversion_commands(data, 2)
    segments = [c for c in commands if c[0] == 0x19]
    pagezero = [c for c in segments if c[2][8:24].split(b'\0')[0] == b'__PAGEZERO']
    if len(pagezero) != 1 or not segments or segments[0] != pagezero[0] or any(c[0] == 0xd for c in commands):
        raise ValueError('PAGEZERO must be the first and only zero segment, without dylib identity')
    zero = pagezero[0][2]
    vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, _ = struct.unpack_from('<4Q4I', zero, 24)
    if len(zero) != 72 or vmaddr or not vmsize or fileoff or filesize or maxprot or initprot or nsects:
        raise ValueError('unsupported PAGEZERO layout')
    fixups = [c for c in commands if c[0] == 0x80000034]
    if len(fixups) > 1:
        raise ValueError('duplicate chained-fixups commands')
    for command in fixups:
        starts = chained_starts(data, command, segments)
        if len(starts) <= 1 or starts[0] != 0:
            raise ValueError('PAGEZERO has unexpected chained fixups')
    return commands, pagezero[0], fixups


def verify_dylibify(source, output):
    original, converted = source.read_bytes(), output.read_bytes()
    before, pagezero, fixups = dylibify_input(original)
    after = conversion_commands(converted, 6)
    if len(original) != len(converted) or len(before) != len(after):
        raise ValueError('conversion changed Mach-O layout')
    expected = bytearray(original)
    struct.pack_into('<I', expected, 12, 6)
    struct.pack_into('<I', expected, 24, struct.unpack_from('<I', original, 24)[0] | 0x100000)
    name = ('@executable_path/' + output.name).encode() + b'\0'
    identity = struct.pack('<6I', 0xd, 72, 24, 1, 0, 0) + name
    if len(identity) > 72:
        raise ValueError('dylib install name does not fit PAGEZERO command')
    expected[pagezero[1]:pagezero[1] + 72] = identity.ljust(72, b'\0')
    for command in fixups:
        starts = chained_starts(original, command)
        start = struct.unpack_from('<I', command[2], 8)[0]
        relative = struct.unpack_from('<I', original, start + 4)[0]
        new_command = next(c for c in after if c[1] == command[1])
        if chained_starts(converted, new_command) != starts[1:]:
            raise ValueError('chained fixups do not match removed PAGEZERO segment')
        struct.pack_into('<' + 'I' * (len(starts) + 1), expected, start + relative,
                         len(starts) - 1, *starts[1:], 0)
    if bytes(expected) != converted:
        raise ValueError('conversion changed bytes outside the exact header, padded identity, or chained fixups transformation')


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
    converter = root / 'verified-dylibify'
    converter_evidence = build_dylibify(converter)
    script = root / 'combined-build.sh'
    script.write_text(adapt(upstream))
    env = dict(os.environ, archive_path='combined', scheme='LiveContainer',
               PATCHED_SIDESTORE_IPA=str(side_ipa), COMBINED_PACKAGER=str(Path(__file__).resolve()),
               VERIFIED_DYLIBIFY=str(converter))
    subprocess.run(['bash', str(script)], cwd=root, env=env, check=True)
    ipa = root / 'LiveContainer+SideStore.ipa'
    result = verify(ipa, side)
    result['upstream_packaging_sha256'] = hashlib.sha256(upstream.encode()).hexdigest()
    result['dylibify'] = converter_evidence
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ipa, output)
    output.with_suffix('.verification.json').write_text(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-entitlements', nargs=2, type=Path)
    parser.add_argument('--verify-dylibify', nargs=2, type=Path)
    parser.add_argument('--validate-dylibify-input', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--host', type=Path)
    parser.add_argument('--side', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.validate_dylibify_input:
        dylibify_input(args.validate_dylibify_input.read_bytes())
    elif args.verify_dylibify:
        verify_dylibify(*args.verify_dylibify)
    elif args.prepare_entitlements:
        prepare_entitlements(*(p.resolve() for p in args.prepare_entitlements))
    else:
        package(*(p.resolve() for p in (args.root, args.host, args.side, args.output)))
