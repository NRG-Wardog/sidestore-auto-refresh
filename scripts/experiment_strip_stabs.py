"""Packaging-only measured derivative of one exact green candidate. No rebuild.

Only Apple strip -S is admitted. SideStore receives the existing empty-entitlement
ldid reseal; it must pass Apple's strict signature verification. This is not
provisioning, a production release, or proof of device/runtime compatibility.
"""
import argparse
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from audit_ipa_signing import inventory as signing_inventory, signing
from ipa_size_report import inspect, report as size_report
from macho_stab_guard import image_uuid, require, sha, snapshot, verify, signature_metadata
from package_livecontainer_combined import verify as verify_combined

RUN = '40213898605'
GREEN_COMMIT = 'eb1b05b94dee5170b5720c665c806be84e442f96'
IPA_NAME = 'LiveContainer-SideStore-issues-experimental.ipa'
IPA_SHA = 'd4b13b62be5e19ce0bbc637ed9beff23a3ebe6a9eee5e5de70ee086239f786a2'
ARTIFACTS = {
    'candidate': ('12606968779', '8b598ca6f9932a2a47c0bfcb986c6ebab66abcb43f22e5e3fdce2d2a44cd9451'),
    'LiveContainer': ('12606789250', '98e2771d9df6f192a7586de27ee5dfe686987034c422114f2e1514b146ce1e6b'),
    'SideStore': ('12607103217', '355cb01a44cb5948b4e88411804a88a3b3d85b9282dc03099142a459045ccac9'),
}
TARGETS = {
    'LiveContainerSwiftUI': 'Payload/LiveContainer.app/Frameworks/LiveContainerSwiftUI.framework/LiveContainerSwiftUI',
    'SideStore': 'Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore',
}
DSYMS = {
    'LiveContainerSwiftUI': ('LiveContainer', 'LiveContainerSwiftUI.framework.dSYM'),
    'SideStore': ('SideStore', 'SideStore.app.dSYM'),
}


def digest(path):
    return sha(path.read_bytes())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')


def unique_zip(data):
    archive = zipfile.ZipFile(data)
    names = archive.namelist()
    require(len(names) == len(set(names)), 'duplicate artifact ZIP entry')
    require(len(names) <= 100000 and sum(i.file_size for i in archive.infolist()) <= 2 * 1024**3,
            'artifact ZIP exceeds bounded inventory')
    return archive


def inputs(folder, work):
    require(not work.exists() and not work.is_symlink(), 'fresh experiment workspace required')
    for name, (_, expected) in ARTIFACTS.items():
        path = folder / (name + '.zip')
        require(path.is_file() and not path.is_symlink() and digest(path) == expected, 'pinned original artifact differs: ' + name)
    work.mkdir(parents=True)
    original = work / 'original.ipa'
    with unique_zip(folder / 'candidate.zip') as archive:
        data = archive.read(IPA_NAME)
        require(sha(data) == IPA_SHA, 'green IPA hash differs')
        original.write_bytes(data)
        receipt = json.loads(archive.read('native-receipt.json'))
        provenance = json.loads(archive.read('provenance.json'))
        require(receipt['run_id'] == RUN and receipt['run_attempt'] == '1' and receipt['workflow_commit'] == GREEN_COMMIT
                and receipt['status'] == 'COMPILE_ONLY_PASS_REQUIRES_REVIEW'
                and receipt['issue_regression_tests_executed'] is True, 'green compile/test receipt differs')
        require(provenance['native_run_id'] == RUN and provenance['ipa_sha256'] == IPA_SHA
                and provenance['workflow_commit'] == GREEN_COMMIT, 'green IPA provenance differs')
        write(work / 'original-provenance.json', provenance)
        write(work / 'original-native-receipt.json', receipt)
    inspect(original)
    with zipfile.ZipFile(original) as archive:
        targets = {key: archive.read(path) for key, path in TARGETS.items()}
        debug_entries = [(copy.copy(archive.getinfo(path)), targets[key], 'OriginalBinaries/' + key)
                         for key, path in TARGETS.items()]
    diagnostics = {}
    for key, (owner, dsym) in DSYMS.items():
        with unique_zip(folder / (owner + '.zip')) as outer:
            native_zip = outer.read(owner + '-unsigned.zip')
            checksum = outer.read('SHA256SUMS').decode().split()
            require(len(checksum) == 2 and checksum[0] == sha(native_zip) and checksum[1] == owner + '-unsigned.zip', 'native diagnostics inner archive hash differs')
        with unique_zip(io.BytesIO(native_zip)) as native:
            prefix = 'native-transfer-' + owner + '/'
            retained = json.loads(native.read(prefix + 'retained-files.json'))
            require(dsym in retained['symbols'], 'requested original dSYM not retained')
            dwarf_path = prefix + 'Symbols/' + dsym + '/Contents/Resources/DWARF/' + key
            dwarf = native.read(dwarf_path)
            uuid = image_uuid(dwarf)
            require(uuid == image_uuid(targets[key]), 'original dSYM does not match candidate UUID')
            bundle_prefix = prefix + 'Symbols/' + dsym + '/'
            for info in native.infolist():
                if info.filename.startswith(bundle_prefix):
                    relative = info.filename[len(prefix):]
                    require('..' not in Path(relative).parts and not relative.startswith('/'), 'unsafe retained dSYM path')
                    debug_entries.append((copy.copy(info), native.read(info), relative))
            diagnostics[key] = {'artifact_id': ARTIFACTS[owner][0], 'artifact_zip_sha256': ARTIFACTS[owner][1],
                                'dwarf_path': dwarf_path, 'dwarf_sha256': sha(dwarf), 'uuid': uuid,
                                'retained_files_manifest_sha256': sha(native.read(prefix + 'retained-files.json'))}
    sidecar = work / 'retained-debug-diagnostics.zip'
    with zipfile.ZipFile(sidecar, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info, data, name in debug_entries:
            info.filename = name
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        archive.writestr('diagnostics.json', json.dumps(diagnostics, indent=2, sort_keys=True))
    with unique_zip(sidecar) as archive:
        for _, data, name in debug_entries:
            require(sha(archive.read(name)) == sha(data), 'retained debug sidecar bytes differ')
    return original, targets, diagnostics, sidecar


def run(command, log):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    log.write_bytes(result.stdout)
    require(result.returncode == 0, 'trusted tool failed: ' + command[0] + '; see ' + str(log))


def repackage(original, output, replacements):
    require(set(replacements) == set(TARGETS.values()), 'binary replacement allowlist differs')
    require(not output.exists() and not output.is_symlink(), 'fresh derivative IPA required')
    old, comment = inspect(original)
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        target.comment = comment
        for info in source.infolist():
            data = replacements.get(info.filename)
            if data is None:
                data = source.read(info)
            target.writestr(copy.copy(info), data, compress_type=info.compress_type, compresslevel=9)
    new, new_comment = inspect(output)
    require(comment == new_comment and len(old) == len(new), 'IPA member closure/comment differs')
    for before, after in zip(old, new):
        ignored = {'compressed_bytes'}
        if before['path'] in replacements:
            ignored |= {'sha256', 'crc', 'bytes'}
            require(after['sha256'] == sha(replacements[before['path']]), 'output target bytes differ')
        require({k: v for k, v in before.items() if k not in ignored} ==
                {k: v for k, v in after.items() if k not in ignored}, 'unapproved IPA member/metadata change: ' + before['path'])
    require(signing_inventory(original)['bundles'] == signing_inventory(output)['bundles'], 'bundle metadata or entitlement inventory changed')
    return verify_combined(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inspect-only', action='store_true')
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.is_symlink(), 'output artifact directory must be fresh')
    args.output.mkdir(parents=True)
    report = {'schema': 'debug-stab-strip-experiment-v1', 'status': 'FAILED', 'source_run_id': RUN,
              'green_workflow_commit': GREEN_COMMIT, 'source_artifacts': ARTIFACTS,
              'experiment_workflow_commit': os.environ.get('GITHUB_SHA'),
              'experiment_run_id': os.environ.get('GITHUB_RUN_ID'),
              'experiment_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
              'original_ipa_sha256': IPA_SHA, 'physical_device_execution': False,
              'production_admission': False, 'native_app_rebuild': False, 'transformations': {}}
    try:
        original, targets, diagnostics, sidecar = inputs(args.artifacts, args.work)
        report['diagnostics'] = diagnostics
        report['retained_debug_sidecar_sha256'] = digest(sidecar)
        report['before_size'] = size_report(original)
        for key, data in targets.items():
            write(args.output / (key + '-before.json'), snapshot(data))
        write(args.output / 'SideStore-original-signing-metadata.json', signature_metadata(targets['SideStore']))
        if args.inspect_only:
            report['status'] = 'INPUTS_AND_ORIGINAL_SYMBOL_LAYOUT_VERIFIED_NO_TRANSFORMATION'
            return
        require(sys.platform == 'darwin', 'Apple strip execution requires macOS')
        require(os.environ.get('DEVELOPER_DIR') == '/Applications/Xcode_26.4.1.app/Contents/Developer', 'pinned Xcode environment required')
        replacements = {}
        for key, data in targets.items():
            source = args.work / 'original-binaries' / key
            candidate = args.work / 'transformed-binaries' / key
            source.parent.mkdir(exist_ok=True)
            candidate.parent.mkdir(exist_ok=True)
            source.write_bytes(data)
            if key == 'SideStore':
                baseline_check = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', '--verbose=4', str(source)],
                                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
                (args.output / 'SideStore-original-codesign-verify.log').write_bytes(baseline_check.stdout)
                report['original_sidestore_codesign_exit_code'] = baseline_check.returncode
            run(['xcrun', '--toolchain', 'default', 'strip', '-S', '-o', str(candidate), str(source)], args.output / (key + '-strip.log'))
            require(digest(source) == sha(data), 'strip modified original copy')
            stripped = candidate.read_bytes()
            strip_evidence = verify(data, stripped)
            write(args.output / (key + '-strip-structural.json'), strip_evidence)
            if key == 'SideStore':
                initial = signing(data)
                original_signing_metadata = signature_metadata(data)
                require(initial.get('signature_present') is True and initial.get('xml_entitlements') is None
                        and not initial.get('der_entitlements_present'), 'original SideStore empty entitlement assumption differs')
                run(['ldid', '-S', str(candidate)], args.output / (key + '-reseal.log'))
                after_signing = signing(candidate.read_bytes())
                require(signature_metadata(candidate.read_bytes()) == original_signing_metadata,
                        'CodeDirectory identity/policy or requirements changed')
                report['sidestore_signing_metadata'] = original_signing_metadata
                require(after_signing.get('signature_present') is True and after_signing.get('xml_entitlements') is None
                        and not after_signing.get('der_entitlements_present'), 'reseal changed empty entitlement inventory')
                run(['/usr/bin/codesign', '--verify', '--strict', '--verbose=4', str(candidate)], args.output / (key + '-codesign-verify.log'))
                report['sidestore_signature'] = 'existing_empty_entitlement_ldid_recipe_resealed_and_apple_strict_verified'
            else:
                require(signing(data).get('signature_present') is False and signing(stripped).get('signature_present') is False,
                        'unsigned UI framework signing status changed')
                report['swiftui_signature'] = 'remains_unsigned_as_in_green_candidate'
            final = candidate.read_bytes()
            report['transformations'][key] = verify(data, final)
            require(image_uuid(final) == diagnostics[key]['uuid'], 'final dSYM UUID association changed')
            replacements[TARGETS[key]] = final
        ipa = args.output / 'LiveContainer-SideStore-debug-stabs-experiment.ipa'
        verification = repackage(original, ipa, replacements)
        report['after_size'] = size_report(ipa)
        require(ipa.stat().st_size < original.stat().st_size, 'derivative archive is not smaller')
        require(digest(original) == IPA_SHA, 'original IPA changed')
        report['final_ipa_sha256'] = digest(ipa)
        report['final_ipa_bytes'] = ipa.stat().st_size
        report['archive_bytes_saved'] = original.stat().st_size - ipa.stat().st_size
        report['uncompressed_bytes_saved'] = report['before_size']['uncompressed_bundle_bytes'] - report['after_size']['uncompressed_bundle_bytes']
        shutil.copy2(sidecar, args.output / sidecar.name)
        require(digest(args.output / sidecar.name) == report['retained_debug_sidecar_sha256'], 'retained diagnostics copy hash mismatch')
        report['matching_dsyms_and_original_target_binaries_retained_in_separate_sidecar'] = True
        report['status'] = 'STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED'
        write(args.output / 'package-verification.json', verification)
        (args.output / 'SHA256SUMS').write_text(digest(ipa) + '  ' + ipa.name + '\n')
    finally:
        write(args.output / 'experiment.json', report)


if __name__ == '__main__':
    main()
