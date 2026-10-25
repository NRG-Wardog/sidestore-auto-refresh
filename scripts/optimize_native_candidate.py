"""Same-run adapter for reviewed app and optional generated OpenSSL debug stripping."""
import json
import os
from pathlib import Path
import shutil
import stat
import zipfile

from experiment_strip_stabs import (DSYMS, TARGETS, OPENSSL_TARGETS, target_policy, digest, image_uuid, inspect,
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


def optimize_same_run_ipa(original, *, expected_sha, transfer_roots, receipt_path,
                          work, output, retained, run_identity, generated_openssl=None):
    require(original.is_file() and not original.is_symlink() and digest(original) == expected_sha,
            'same-run initial IPA differs')
    require(set(transfer_roots) == {'LiveContainer', 'SideStore'}, 'native transfer closure differs')
    for folder in (work, output, retained):
        require(not folder.exists() and not folder.is_symlink(), 'optimization directories must be fresh')
    receipt = json.loads(receipt_path.read_text())
    require(receipt['status'] == 'COMPILE_ONLY_PASS_REQUIRES_REVIEW', 'native receipt is not successful')
    require(all(receipt[key] == run_identity[key] for key in ('run_id','run_attempt','workflow_commit')),
            'native optimization run identity differs')
    receipt_hash = digest(receipt_path)
    inspect(original)
    paths = target_policy(generated_openssl is not None)
    with zipfile.ZipFile(original) as archive:
        targets = {key: archive.read(path) for key, path in paths.items()}
    diagnostics, symbol_inputs = {}, {}
    for key, (owner, name) in DSYMS.items():
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
        dwarf = bundle / 'Contents/Resources/DWARF' / key
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
                                  include_generated_openssl=generated_openssl is not None)
        require(digest(original) == expected_sha and digest(receipt_path) == receipt_hash,
                'immutable input IPA or receipt changed')
        for key, (bundle, inventory) in symbol_inputs.items():
            require(retained_inventory(bundle) == inventory, 'native symbols changed during optimization')
        for binary, binary_hash, payload_receipt, payload_hash in generated_checks.values():
            require(digest(binary) == binary_hash and digest(payload_receipt) == payload_hash, 'generated optimization input changed')
        retained.mkdir(parents=True)
        shutil.move(str(output / sidecar.name), retained / sidecar.name)
        require(digest(retained / sidecar.name) == report['retained_debug_sidecar_sha256'], 'retained sidecar copy differs')
        write(retained / 'provenance.json', report)
        return output / 'LiveContainer-SideStore-debug-stabs-experiment.ipa', report
    except Exception:
        report['status'] = 'FAILED'
        raise
    finally:
        write(output / 'experiment.json', report)
