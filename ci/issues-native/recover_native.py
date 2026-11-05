"""Finite packaging recovery of reviewed native run 40245615437, never a build replay.

The original fixed receipt authenticates historical qualification evidence. This
path does not claim to re-execute the compiler or reconstruct vanished build roots.
"""
import hashlib
import json
import os
from pathlib import Path

IDENTITY = {'workflow_commit': '0205f33ff50a024d4e125b6b404e989911ac5eb2',
            'run_id': '40245615437', 'run_attempt': '1'}
RECEIPT_SHA = '7f6c7ce3444ba14cf964dfc98102181f7e798aa90200c7b98d74c14263f79d83'
SEAL_SHA = 'f08ec04ce6ebd4361d51c3be984eb29da147836365739f16aa1ac62ac1ad1d43'


def require(ok, message):
    if not ok: raise ValueError(message)


def digest(path):
    require(path.is_file() and not path.is_symlink(), 'Expected retained regular file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    digest(path)
    return json.loads(path.read_text())


def verify_evidence(root, mapping):
    require(root.is_dir() and not root.is_symlink(), 'Expected real evidence root')
    for name, expected in mapping.items():
        p = Path(name)
        require(not p.is_absolute() and '..' not in p.parts, 'Unsafe historical evidence path')
        require(not any((root / Path(*p.parts[:i])).is_symlink() for i in range(1, len(p.parts)+1)),
                'Linked historical evidence path')
        require(digest(root / p) == expected, 'Historical evidence changed: ' + name)



def verify_qualification_sources(root, mapping, packaging_identity, host_receipt=None):
    """Authenticate original sources separately from the reviewed current host-only parser."""
    path = 'scripts/macho_stab_guard.py'
    old_sha = '918a2a2938297850c47c3ece6a79292cafd85ab6749ebab6f461f16bee48b8bb'
    current_sha = '0e5affdfe37ddd49adfc004692349fa2a1c1aae06e638445a478170f2e819178'
    historical = 'ci/issues-native/historical/' + IDENTITY['workflow_commit'] + '/macho_stab_guard.py.txt'
    expected = {'schema':'fixed-native-historical-source-map-v1', 'native_commit':IDENTITY['workflow_commit'],
        'sources':{path:{'historical_file':historical,'historical_sha256':old_sha,'current_sha256':current_sha}}}
    require(read(root / 'ci/issues-native/historical-source-map.json') == expected, 'Historical source mapping closure differs')
    require(mapping.get(path) == old_sha, 'Original qualification parser hash differs')
    verify_evidence(root, {name:sha for name,sha in mapping.items() if name != path})
    verify_evidence(root, {historical:old_sha, path:current_sha})
    from acquire_openssl_host_qualification import manifest, qualification_module
    pin = manifest()
    require(host_receipt is not None or os.environ.get('OPENSSL_LOCAL_HOST_QUALIFICATION_RECEIPT'),
            'Verified current host qualification receipt required')
    receipt_path = Path(host_receipt if host_receipt is not None else os.environ['OPENSSL_LOCAL_HOST_QUALIFICATION_RECEIPT'])
    require(digest(receipt_path) == pin['receipt_sha256'], 'Current host qualification receipt hash differs')
    receipt = read(receipt_path)
    require(receipt.get('qualification_identity') == {'workflow_commit':pin['head_sha'],'run_id':str(pin['run_id']),'run_attempt':str(pin['run_attempt'])}, 'Current host qualification identity differs')
    module = qualification_module()
    require(len(module.SOURCES)==29 and len(set(module.SOURCES))==29 and 'ci/issue55-openssl-local/framework_seal.py' in module.SOURCES,
            'Current host source closure differs')
    require(receipt.get('source_sha256',{}).get(path)==current_sha, 'Current parser not bound to host qualification')
    proof = module.verify_host_local_qualification(receipt_path,pin['receipt_sha256'],packaging_identity,module.NATIVE)
    return {'schema':'historical-and-current-parser-provenance-v1','historical_source_map':expected,
        'all_original_qualification_sources_authenticated':True,'current_parser_is_historical_parser':False,
        'current_host_qualification':proof,'historical_parser_executed':False}

def verify_retained_transfers(transfers):
    from optimize_native_candidate import retained_inventory
    for owner, transfer in transfers.items():
        retained = read(transfer / 'retained-files.json')
        require(set(retained) == {'app', 'symbols'}, 'Unexpected retained manifest closure')
        require(retained_inventory(transfer / 'Products' / (owner + '.app')) == retained['app'],
                'Retained native app bytes/modes/links differ')
        symbols = transfer / 'Symbols'
        require(symbols.is_dir() and not symbols.is_symlink() and
                {p.name for p in symbols.iterdir()} == set(retained['symbols']), 'Retained symbol closure differs')
        for name, expected in retained['symbols'].items():
            require(Path(name).name == name, 'Unsafe retained symbol name')
            require(retained_inventory(symbols / name) == expected, 'Retained symbol bytes/modes/links differ')

def verify(evidence, transfers, packaging_identity, *, host_qualification_receipt=None):
    """Revalidate immutable original evidence and exact retained generated bytes."""
    from native_transfer_seal import verify as verify_seal, _identity
    _identity(packaging_identity)
    require(packaging_identity != IDENTITY, 'Recovery must identify its new packaging run')
    require(digest(evidence / 'receipt.json') == RECEIPT_SHA, 'Wrong fixed native receipt')
    receipt = read(evidence / 'receipt.json')
    require(all(receipt[k] == v for k, v in IDENTITY.items()), 'Wrong original native identity')
    require(receipt['status'] == 'COMPILE_ONLY_PASS_REQUIRES_REVIEW', 'Native compilation did not pass')
    require(receipt['native_builds'] == {o:'PASS_UNSIGNED_RELEASE_IPHONEOS' for o in transfers}, 'Native build closure differs')
    verify_evidence(evidence, receipt['evidence_sha256'])
    manifests = verify_seal(evidence / 'native-transfer-seal.json', evidence / 'receipt.json', transfers,
                            IDENTITY, expected_seal_sha=SEAL_SHA)
    verify_retained_transfers(transfers)
    qualified = read(evidence / 'lts-qualification.json')
    require(qualified['status'] == 'PASS_MACOS_SYNTHETIC_ONLY' and qualified['failures'] == [], 'Historical crypto qualification failed')
    verify_evidence(evidence, qualified['evidence_sha256'])
    generated = {}
    source_provenance = {}
    for owner, key, prefix in [('LiveContainer', 'OpenSSLHost', 'generated-openssl'),
                               ('SideStore', 'OpenSSLEmbedded', 'generated-embedded-openssl')]:
        payload_path = evidence / (prefix + '-payload.json')
        payload = read(payload_path)
        stage_path = evidence / (prefix + '-stage.json')
        require(payload['generated_stage_receipt_sha256'] == digest(stage_path), 'Original generated stage differs')
        stage = read(stage_path)
        qualification = payload['size_qualification']
        source_provenance[owner] = verify_qualification_sources(Path(__file__).resolve().parents[2],
            qualification['qualification_sources_sha256'], packaging_identity, host_qualification_receipt)
        require(all(qualification[k] == v for k,v in IDENTITY.items()) and qualification['optimization'] == 'Os', 'Original Os qualification identity differs')
        for field, name in [('host_qualification','lts-qualification.json'), ('host_receipt','openssl-host-build-receipt.json'),
                            ('ios_receipt','openssl-ios-build-receipt.json'), ('measurement','size-measurement.json')]:
            require(qualification['receipt_sha256'][field] == digest(evidence / name), 'Original qualification receipt differs')
        binary = transfers[owner] / 'Products' / (owner + '.app') / 'Frameworks/OpenSSL.framework/OpenSSL'
        dsym = transfers[owner] / 'Symbols/OpenSSL.framework.dSYM'
        dwarf = dsym / 'Contents/Resources/DWARF/OpenSSL'
        base = ('GeneratedDependencies/OpenSSL35/' if owner == 'SideStore' else '') + 'OpenSSL.xcframework/ios-arm64/'
        files = stage['generated']['files']
        require(digest(binary) == files[base+'OpenSSL.framework/OpenSSL']['sha256'] == payload['native_binary_sha256'] == payload['generated_binary_sha256'] == qualification['qualified_device_binary_sha256'], 'Retained generated binary differs')
        # Every dSYM file, not merely UUID, is bound to the original generated stage.
        expected = {n[len(base+'dSYMs/OpenSSL.framework.dSYM/'):]: row['sha256']
                    for n,row in files.items() if n.startswith(base+'dSYMs/OpenSSL.framework.dSYM/') and row['kind']=='file'}
        observed = {p.relative_to(dsym).as_posix():digest(p) for p in dsym.rglob('*') if p.is_file()}
        require(observed == expected and expected, 'Retained full generated dSYM differs')
        from macho_stab_guard import image_uuid
        require(image_uuid(binary.read_bytes()) == image_uuid(dwarf.read_bytes()) == payload['identity']['uuid'], 'Retained generated UUID differs')
        generated[key] = dict(binary=binary, dsym=dsym, payload_receipt=payload_path)
    return generated, dict(schema='fixed-native-packaging-recovery-v1', native_identity=IDENTITY,
        packaging_identity=packaging_identity, native_receipt_sha256=RECEIPT_SHA,
        qualification_source_provenance=source_provenance,
        native_transfer_seal_sha256=SEAL_SHA, historical_qualification_reexecuted=False,
        original_native_products_recompiled=False, retained_manifests=dict(manifests))


def verify_acquisition(evidence, path, packaging_identity):
    """Bind downloaded artifact identities and the immutable full evidence tree."""
    from recover_native_artifacts import ARTIFACTS, inventory
    acquisition = read(path)
    require(acquisition.get('schema') == 'fixed-native-artifact-restoration-v1', 'Wrong acquisition schema')
    require(acquisition.get('original_native') == IDENTITY and acquisition.get('artifacts') == ARTIFACTS,
            'Original acquired artifacts differ')
    require(acquisition.get('recovery_identity') == {
        'GITHUB_SHA':packaging_identity['workflow_commit'], 'GITHUB_RUN_ID':packaging_identity['run_id'],
        'GITHUB_RUN_ATTEMPT':packaging_identity['run_attempt']}, 'Acquisition packaging identity differs')
    require(acquisition.get('native_execution_performed') is False and acquisition.get('original_evidence_modified') is False,
            'Acquisition execution or mutation claim differs')
    current = hashlib.sha256(json.dumps(inventory(evidence), sort_keys=True).encode()).hexdigest()
    require(acquisition.get('original_evidence_inventory_sha256') == current, 'Original evidence tree changed')
    return digest(path)
