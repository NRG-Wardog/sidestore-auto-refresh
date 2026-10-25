"""Same-run adapter for reviewed app and optional generated OpenSSL debug stripping."""
import json
import os
from pathlib import Path
import shutil
import stat
import zipfile

from experiment_strip_stabs import (DSYMS, TARGETS, OPENSSL_TARGETS, UNSIGNED_HELPER_TARGETS, UNSIGNED_HELPER_DSYMS, target_policy, digest, image_uuid, inspect,
    require, sha, transform_verified_inputs, unique_zip, write)


def retained_inventory(root):
    require(root.is_dir() and not root.is_symlink(), 'native symbol root must be a real directory')
    anchor = root.resolve(strict=True)
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        mode = path.lstat().st_mode
        row = {'mode': stat.S_IMODE(mode)}
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            require(not os.path.isabs(target) and path.resolve(strict=True).is_relative_to(anchor),
                    'native symbol link escapes owned bundle')
            row.update(kind='symlink', target=target)
        elif stat.S_ISDIR(mode): row['kind'] = 'directory'
        elif stat.S_ISREG(mode): row.update(kind='file', sha256=digest(path))
        else: raise ValueError('unsupported native symbol entry')
        result[path.relative_to(root).as_posix()] = row
    return result


def run_local_symbol_stage(ipa, sidecar, output):
    # Lazy import keeps the already-reviewed first stage independent. Promotion
    # requires the caller's explicit opt-in after native diagnostic qualification.
    from experiment_local_symbols import experiment
    return experiment(ipa, digest(ipa), sidecar, digest(sidecar), output, qualify_package=True)


def add_local_symbol_stage(initial, first, sidecar, output, retained, report, run_identity):
    first_hash, sidecar_hash = digest(first), digest(sidecar)
    require(digest(initial) == report['original_ipa_sha256'], 'original initial IPA binding differs')
    require(report['status'] == 'STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED',
            'first-stage package is not qualified')
    require(first_hash == report['final_ipa_sha256'] and sidecar_hash == report['retained_debug_sidecar_sha256'],
            'first-stage product or same-run diagnostics differ')
    stage1_receipt = output / 'stab-stage-receipt.json'
    write(stage1_receipt, report)
    stage1_hash = digest(stage1_receipt)
    local_root = output / 'local-symbol-stage'
    local = run_local_symbol_stage(first, sidecar, local_root)
    require(local_root.is_dir() and not local_root.is_symlink(), 'local-stage directory is linked/missing')
    final = local_root / 'local-symbol-qualified.ipa'
    local_receipt = local_root / 'diagnostic.json'
    require(final.is_file() and not final.is_symlink() and local_receipt.is_file() and not local_receipt.is_symlink(),
            'local-stage package or receipt is missing/linked')
    require(json.loads(local_receipt.read_text()) == local, 'local-stage returned and persisted receipts differ')
    require(local['schema'] == 'local-symbol-diagnostic-v1' and
            local['status'] == 'LOCAL_SYMBOL_PACKAGE_QUALIFIED_DEVICE_AND_RELEASE_UNQUALIFIED' and
            local['packaging_approved'] is True and local['signature_verified'] is True and
            local['ipa_emitted'] is True and local['runtime_proven'] is False,
            'local-stage qualification is incomplete')
    require(all(local[key] == run_identity[key] for key in ('run_id', 'run_attempt', 'workflow_commit')),
            'local-stage run identity differs')
    require(local['input_ipa_sha256'] == first_hash and local['retained_symbols_sha256'] == sidecar_hash,
            'local-stage input binding differs')
    require(set(local['targets']) == set(TARGETS) and set(local['diagnostics']) == set(TARGETS),
            'local-stage target closure differs')
    for key in TARGETS:
        require(local['diagnostics'][key]['uuid'] == report['diagnostics'][key]['uuid'] and
                local['diagnostics'][key]['dwarf_sha256'] == report['diagnostics'][key]['dwarf_sha256'],
                'local-stage retained dSYM binding differs')
    require(digest(final) == local['final_ipa_sha256'] and final.stat().st_size == local['final_ipa_bytes'] and
            first.stat().st_size == local['original_ipa_bytes'] and
            first.stat().st_size-final.stat().st_size == local['archive_bytes_saved'] > 0,
            'local-stage measured output differs')
    require(digest(first) == first_hash and digest(sidecar) == sidecar_hash and digest(stage1_receipt) == stage1_hash,
            'local-stage mutated immutable first-stage inputs')
    retained_stage = local_root/'retained'
    require(retained_stage.is_dir() and not retained_stage.is_symlink() and
            {p.name for p in retained_stage.iterdir()} == {'original.ipa', 'matching-debug-diagnostics.zip'} and
            all(p.is_file() and not p.is_symlink() for p in retained_stage.iterdir()),
            'local-stage retained archive closure differs')
    require(digest(local_root/'retained/original.ipa') == first_hash and
            digest(local_root/'retained/matching-debug-diagnostics.zip') == sidecar_hash,
            'local-stage retained originals differ')
    stage2_hash = digest(local_receipt)
    # Keep both complete receipts and the stage-one IPA, in addition to the
    # existing full original native IPA/binary/dSYM archive. No file is discarded.
    shutil.move(str(local_root/'retained'), retained/'local-symbol-stage-inputs')
    for source, name in ((stage1_receipt, 'stab-stage-receipt.json'), (local_receipt, 'local-stage-receipt.json')):
        shutil.copyfile(source, retained/name)
        require(digest(source) == digest(retained/name), 'retained stage receipt differs')
    report['stages'] = {
        'debug_stabs': {'input_ipa_sha256': report['original_ipa_sha256'], 'final_ipa_sha256': first_hash,
                       'archive_bytes_saved': initial.stat().st_size-first.stat().st_size,
                       'receipt_sha256': stage1_hash},
        'local_symbols': {'input_ipa_sha256': first_hash, 'final_ipa_sha256': digest(final),
                          'archive_bytes_saved': first.stat().st_size-final.stat().st_size,
                          'receipt_sha256': stage2_hash, 'retained_symbols_sha256': sidecar_hash}}
    report.update(schema='same-run-two-stage-symbol-optimization-v1',
                  final_ipa_sha256=digest(final), final_ipa_bytes=final.stat().st_size,
                  archive_bytes_saved=initial.stat().st_size-final.stat().st_size,
                  target_24_decimal_MB_met=final.stat().st_size <= 24_000_000,
                  status='TWO_STAGE_STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED')
    # The old first-stage size inventory stays explicitly named, never mislabeled
    # as the second-stage final inventory.
    report['debug_stabs_after_size'] = report.pop('after_size')
    report['debug_stabs_transformations'] = report.pop('transformations')
    report['local_symbol_targets'] = local['targets']
    from ipa_size_report import report as size_report
    report['after_size'] = size_report(final)
    report['uncompressed_bytes_saved'] = report['before_size']['uncompressed_bundle_bytes']-report['after_size']['uncompressed_bundle_bytes']
    return final


def optimize_same_run_ipa(original, *, expected_sha, transfer_roots, receipt_path,
                          work, output, retained, run_identity, generated_openssl=None, include_local_symbols=False,
                          expected_transfer_manifests=None):
    require(original.is_file() and not original.is_symlink() and digest(original) == expected_sha,
            'same-run initial IPA differs')
    require(set(transfer_roots) == {'LiveContainer', 'SideStore'}, 'native transfer closure differs')
    sealed_manifests = None if expected_transfer_manifests is None else dict(expected_transfer_manifests)
    if sealed_manifests is not None:
        require(set(sealed_manifests) == set(transfer_roots), 'sealed transfer owner closure differs')
        for owner, root in transfer_roots.items():
            path = root / 'retained-files.json'
            require(path.is_file() and not path.is_symlink() and digest(path) == sealed_manifests[owner],
                    'trusted pre-packaging retention manifest differs')
    for folder in (work, output, retained):
        require(not folder.exists() and not folder.is_symlink(), 'optimization directories must be fresh')
    receipt = json.loads(receipt_path.read_text())
    require(receipt['status'] == 'COMPILE_ONLY_PASS_REQUIRES_REVIEW', 'native receipt is not successful')
    require(all(receipt[key] == run_identity[key] for key in ('run_id','run_attempt','workflow_commit')),
            'native optimization run identity differs')
    receipt_hash = digest(receipt_path)
    inspect(original)
    paths = target_policy(generated_openssl is not None, include_unsigned_helpers=True)
    with zipfile.ZipFile(original) as archive:
        targets = {key: archive.read(path) for key, path in paths.items()}
    diagnostics, symbol_inputs = {}, {}
    for key, (owner, name) in {**DSYMS, **UNSIGNED_HELPER_DSYMS}.items():
        root = transfer_roots[owner]
        require(root.is_dir() and not root.is_symlink(), 'native transfer root must be real')
        require(not (root / 'evidence/receipt.json').is_symlink(), 'linked native symbol receipt')
        require(digest(root / 'evidence/receipt.json') == receipt_hash, 'native symbol receipt differs')
        manifest_path = root / 'retained-files.json'
        require(not manifest_path.is_symlink(), 'linked native symbol manifest')
        manifest = json.loads(manifest_path.read_text())
        bundle = root / 'Symbols' / name
        require(bundle.resolve(strict=True).is_relative_to(root.resolve(strict=True)), 'native symbol bundle escapes transfer')
        inventory = retained_inventory(bundle)
        require(inventory == manifest['symbols'][name], 'native dSYM manifest differs')
        dwarf_name = 'ZSign.dylib' if key == 'ZSign' else key
        dwarf = bundle / 'Contents/Resources/DWARF' / dwarf_name
        uuid = image_uuid(dwarf.read_bytes())
        require(uuid == image_uuid(targets[key]), 'native dSYM UUID does not match initial IPA')
        diagnostics[key] = {'owner': owner, 'uuid': uuid, 'dwarf_sha256': digest(dwarf),
                            'retained_manifest_sha256': digest(manifest_path),
                            'native_receipt_sha256': receipt_hash}
        symbol_inputs[key] = (bundle, inventory)
    generated_checks = {}
    if generated_openssl is not None:
        require(set(generated_openssl) == set(OPENSSL_TARGETS), 'generated OpenSSL input closure differs')
        for key, row in generated_openssl.items():
            binary, bundle, payload_receipt = row['binary'], row['dsym'], row['payload_receipt']
            for path in (binary, bundle, payload_receipt):
                require(not path.is_symlink(), 'linked generated optimization input')
            receipt_key = ('generated_embedded_openssl_payload_receipt_sha256' if key == 'OpenSSLEmbedded'
                           else 'generated_openssl_payload_receipt_sha256')
            require(digest(payload_receipt) == receipt[receipt_key], 'generated payload receipt is not same-run bound')
            payload = json.loads(payload_receipt.read_text())
            require(payload['status'] == 'EXACT_GENERATED_DEVICE_BYTES_LINKED_NOT_RUNTIME_PROVEN' and
                    payload['generated_inputs_unchanged'] is True, 'generated input qualification differs')
            require(digest(binary) == payload['generated_binary_sha256'] == payload['native_binary_sha256'] == sha(targets[key]),
                    'generated original binary differs from compiled and packaged input')
            inventory = retained_inventory(bundle)
            dwarf = bundle / 'Contents/Resources/DWARF/OpenSSL'
            uuid = image_uuid(dwarf.read_bytes())
            require(uuid == image_uuid(targets[key]), 'generated OpenSSL dSYM UUID differs')
            diagnostics[key] = {'owner': key, 'uuid': uuid, 'dwarf_sha256': digest(dwarf),
                                'native_receipt_sha256': receipt_hash, 'generated_payload_receipt_sha256': digest(payload_receipt)}
            symbol_inputs[key] = (bundle, inventory)
            generated_checks[key] = (binary, digest(binary), payload_receipt, digest(payload_receipt))
        require(targets['OpenSSLHost'] == targets['OpenSSLEmbedded'], 'generated OpenSSL copies differ')
    work.mkdir(parents=True); output.mkdir(parents=True)
    sidecar = work / 'retained-debug-diagnostics.zip'
    report = {'schema': 'same-run-debug-stab-optimization-v1', 'status': 'FAILED',
              'original_ipa_sha256': expected_sha, 'same_run_identity': run_identity,
              'native_receipt_sha256': receipt_hash, 'physical_device_execution': False,
              'production_admission': False, 'native_app_rebuild': False, 'transformations': {}}
    try:
        retained_hashes = {}
        with zipfile.ZipFile(sidecar, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(original, 'OriginalIPA/initial-combined.ipa')
            retained_hashes['OriginalIPA/initial-combined.ipa'] = expected_sha
            for key, data in targets.items():
                name = 'OriginalBinaries/' + key
                archive.writestr(name, data); retained_hashes[name] = sha(data)
            for key, (bundle, inventory) in symbol_inputs.items():
                for relative, row in inventory.items():
                    name = 'Symbols/' + (key + '/' if key in OPENSSL_TARGETS else '') + bundle.name + ('/' + relative if relative != '.' else '/')
                    path = bundle if relative == '.' else bundle / relative
                    if row['kind'] == 'symlink':
                        info = zipfile.ZipInfo(name); info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | row['mode']) << 16
                        archive.writestr(info, row['target'].encode())
                        retained_hashes[name] = sha(row['target'].encode())
                    else:
                        archive.write(path, name)
                        if row['kind'] == 'file': retained_hashes[name] = row['sha256']
            for key, (_, _, payload_receipt, payload_hash) in generated_checks.items():
                name = 'GeneratedProvenance/' + key + '-payload.json'
                archive.writestr(name, payload_receipt.read_bytes()); retained_hashes[name] = payload_hash
            archive.writestr('diagnostics.json', json.dumps(diagnostics, sort_keys=True))
            archive.writestr('original-native-receipt.json', receipt_path.read_bytes())
        with unique_zip(sidecar) as archive:
            require(all(sha(archive.read(name)) == value for name,value in retained_hashes.items()),
                    'retained diagnostic readback differs')
        transform_verified_inputs(original, targets, diagnostics, sidecar, work=work, output=output,
                                  report=report, expected_original_sha=expected_sha, apple_adhoc_package=True,
                                  include_generated_openssl=generated_openssl is not None, include_unsigned_helpers=True)
        require(digest(original) == expected_sha and digest(receipt_path) == receipt_hash,
                'immutable input IPA or receipt changed')
        if sealed_manifests is not None:
            require(all(digest(transfer_roots[owner] / 'retained-files.json') == value
                        for owner, value in sealed_manifests.items()), 'sealed native retention manifest changed')
            report['native_transfer_manifest_sha256'] = sealed_manifests
        for key, (bundle, inventory) in symbol_inputs.items():
            require(retained_inventory(bundle) == inventory, 'native symbols changed during optimization')
        for binary, binary_hash, payload_receipt, payload_hash in generated_checks.values():
            require(digest(binary) == binary_hash and digest(payload_receipt) == payload_hash, 'generated optimization input changed')
        retained.mkdir(parents=True)
        final = output / 'LiveContainer-SideStore-debug-stabs-experiment.ipa'
        if include_local_symbols:
            final = add_local_symbol_stage(original, final, output/sidecar.name, output, retained, report, run_identity)
        shutil.move(str(output / sidecar.name), retained / sidecar.name)
        require(digest(retained / sidecar.name) == report['retained_debug_sidecar_sha256'], 'retained sidecar copy differs')
        require(digest(original) == expected_sha and digest(receipt_path) == receipt_hash,
                'immutable input IPA or receipt changed')
        for key, (bundle, inventory) in symbol_inputs.items():
            require(retained_inventory(bundle) == inventory, 'native symbols changed during optimization')
        for binary, binary_hash, payload_receipt, payload_hash in generated_checks.values():
            require(digest(binary) == binary_hash and digest(payload_receipt) == payload_hash, 'generated optimization input changed')
        write(retained / 'provenance.json', report)
        return final, report
    except Exception:
        report['status'] = 'FAILED'
        raise
    finally:
        write(output / 'experiment.json', report)
