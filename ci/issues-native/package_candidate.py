"""Package only the exact same-run compiled products, never publish a release."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()



def inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Product root must be a real directory')
    anchor = root.resolve(strict=True)
    shape = {'.': {'type': 'directory', 'mode': root.stat().st_mode & 0o7777}}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            if os.path.isabs(target) or not path.resolve(strict=True).is_relative_to(anchor):
                raise ValueError('Product link escapes copied bundle: ' + name)
            shape[name] = {'link': target}
        elif path.is_file() or path.is_dir():
            shape[name] = {'mode': path.stat().st_mode & 0o7777,
                           'type': 'file' if path.is_file() else 'directory'}
        else:
            raise ValueError('Unsupported product entry: ' + name)
    files = {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}
    return files, shape


def copy_size_reports(source, destination):
    """Only non-secret JSON/log evidence crosses from binary staging."""
    if not source.exists(): return
    if source.is_symlink() or not source.is_dir(): raise ValueError('Invalid size report source')
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob('*')):
        if path.suffix not in {'.json', '.log'}: continue
        if path.is_symlink() or not path.is_file(): raise ValueError('Unsafe size report file')
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if sha(target) != sha(path): raise ValueError('Size report copy differs')


def admit_openssl_local_qualification(enabled, native_identity, packaging_identity):
    """Explicit trusted acquisition inputs, checked before any packaging tool."""
    if not enabled:
        return None
    import re
    raw_path = os.environ.get('OPENSSL_LOCAL_HOST_QUALIFICATION_RECEIPT', '')
    expected = os.environ.get('OPENSSL_LOCAL_HOST_QUALIFICATION_SHA256', '')
    if not raw_path or re.fullmatch('[0-9a-f]{64}', expected) is None:
        raise ValueError('Explicit verified OpenSSL host qualification path and trusted SHA256 required')
    path = Path(raw_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('OpenSSL host qualification must be a regular receipt')
    from optimize_openssl_local_candidate import verify_host_qualification
    binding = verify_host_qualification(path, expected, packaging_identity, native_identity)
    return path, expected, binding


def append_openssl_local_stage(ipa, report, qualification, *, transfer_roots, receipt_path,
                              seal_path, seal_sha, native_identity, packaging_identity):
    """One narrow caller for the independently guarded fourth-stage adapter."""
    from optimize_openssl_local_candidate import optimize_openssl_local_candidate
    from optimize_native_candidate import verify_persisted_json_receipt
    prior_path = Path('size-diagnostics/sidebackup/cumulative-receipt.json')
    verify_persisted_json_receipt(prior_path, report)
    qualification_path, qualification_sha, _ = qualification
    return optimize_openssl_local_candidate(ipa, prior_report=report, prior_receipt_path=prior_path,
        expected_prior_receipt_sha=sha(prior_path), transfer_roots=transfer_roots,
        receipt_path=receipt_path, seal_path=seal_path, expected_seal_sha=seal_sha,
        native_identity=native_identity, packaging_identity=packaging_identity,
        original_sidecar=Path('size-diagnostics/retained-debug-diagnostics.zip'),
        host_qualification_path=qualification_path, expected_host_qualification_sha=qualification_sha,
        output=Path('packaging-openssl-local-products'), retained=Path('size-diagnostics/openssl-local'))


def main(*, recover_fixed_native=False, strip_unsigned_widget=False, strip_widget_true_locals=False, strip_openssl_true_locals=False, same_run_qualified=False, language_switch=False, cold_vpn=False, multitask=False):
    if same_run_qualified and recover_fixed_native:
        raise ValueError('Same-run qualification cannot use recovery identity')
    if same_run_qualified and not (strip_unsigned_widget and strip_widget_true_locals and strip_openssl_true_locals):
        raise ValueError('Same-run qualified mode requires the complete reviewed size pipeline')
    qualified_context = recover_fixed_native or same_run_qualified
    if strip_openssl_true_locals and not (qualified_context and strip_unsigned_widget and strip_widget_true_locals):
        raise ValueError('OpenSSL local experiment requires explicit current fixed-native widget qualification path')
    if strip_widget_true_locals and not (qualified_context and strip_unsigned_widget):
        raise ValueError('Widget true-local experiment requires fixed-native recovery and unsigned widget debug stripping')
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
    receipt = json.loads(Path('evidence/receipt.json').read_text())
    packaging_identity = dict(workflow_commit=os.environ['GITHUB_SHA'], run_id=os.environ['GITHUB_RUN_ID'],
                              run_attempt=os.environ['GITHUB_RUN_ATTEMPT'])
    native_identity = packaging_identity
    if recover_fixed_native:
        from recover_native import IDENTITY, SEAL_SHA
        native_identity = IDENTITY
    openssl_qualification = admit_openssl_local_qualification(strip_openssl_true_locals, native_identity, packaging_identity)
    if same_run_qualified:
        if openssl_qualification[2]['schema'] != 'qualified-openssl-local-same-run-host-binding-v1':
            raise ValueError('Same-run size mode requires fresh same-run host qualification')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'issue56-startup'))
        from run_startup_tests import verify_receipt as verify_startup_receipt
        startup_receipt = Path('evidence/issue56-startup-tests.json')
        verify_startup_receipt(startup_receipt, Path('sources'), native_identity)
        if receipt.get('evidence_sha256', {}).get(startup_receipt.name) != sha(startup_receipt):
            raise ValueError('Startup regression receipt differs from native receipt')
    if language_switch:
        if not same_run_qualified:
            raise ValueError('Language qualification requires the same-run native size pipeline')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'language-switch'))
        from run_language_switch_tests import verify_receipt as verify_language_receipt
        language_receipt = Path('evidence/language-switch-tests.json')
        verify_language_receipt(language_receipt, Path('sources'), native_identity)
        if receipt.get('evidence_sha256', {}).get(language_receipt.name) != sha(language_receipt):
            raise ValueError('Language runtime receipt differs from native receipt')
    if cold_vpn:
        if not (same_run_qualified and language_switch):
            raise ValueError('Cold VPN qualification requires same-run size and language qualification')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cold-vpn'))
        from run_cold_vpn_tests import verify_receipt as verify_cold_vpn_receipt
        cold_vpn_receipt = Path('evidence/cold-vpn-tests.json')
        verify_cold_vpn_receipt(cold_vpn_receipt, Path('sources'), native_identity)
        if receipt.get('evidence_sha256', {}).get(cold_vpn_receipt.name) != sha(cold_vpn_receipt):
            raise ValueError('Cold VPN regression receipt differs from native receipt')
    if multitask:
        if not (same_run_qualified and language_switch and cold_vpn):
            raise ValueError('Guest qualification requires same-run size, language and cold VPN qualification')
        from multitask_receipt import verify_local as verify_multitask
        from verify_candidate_graph import load_candidate_graph
        multitask_receipt = Path('evidence/multitask-launch-tests.json')
        verify_multitask(multitask_receipt, Path('sources'), Path('evidence/multitask-launch'), load_candidate_graph()['owners'], native_identity)
        if receipt.get('evidence_sha256', {}).get(multitask_receipt.name) != sha(multitask_receipt):
            raise ValueError('Guest qualification receipt differs from native receipt')
        from startup_upgrade_receipt import verify_local as verify_startup_upgrade
        schema_receipt = Path('evidence/startup-upgrade-tests.json')
        verify_startup_upgrade(schema_receipt, Path('sources'), Path('schema-sources'), Path('evidence'), load_candidate_graph()['owners'], native_identity)
        if receipt.get('evidence_sha256', {}).get(schema_receipt.name) != sha(schema_receipt):
            raise ValueError('Core Data qualification receipt differs from native receipt')
    recovery_provenance = None
    for key, expected in [('status', 'COMPILE_ONLY_PASS_REQUIRES_REVIEW'),
                          ('workflow_commit', native_identity['workflow_commit']),
                          ('run_id', native_identity['run_id']),
                          ('run_attempt', native_identity['run_attempt'])]:
        if receipt[key] != expected: raise ValueError('Same-run compile receipt differs: ' + key)
    if receipt.get('evidence_sha256', {}).get('product-hashes.json') != sha(Path('evidence/product-hashes.json')):
        raise ValueError('Product manifest differs from compile receipt')
    from verify_candidate_graph import load_candidate_graph
    if receipt.get('requested_graph') != load_candidate_graph()['owners']:
        raise ValueError('Compile receipt graph differs from reviewed candidate')
    from native_transfer_seal import verify as verify_transfer_seal
    transfer_roots = {owner: Path(os.environ['RUNNER_TEMP']) / ('native-transfer-' + owner)
                      for owner in ('LiveContainer', 'SideStore')}
    run_identity = {key: receipt[key] for key in ('workflow_commit', 'run_id', 'run_attempt')}
    seal_path = Path('evidence/native-transfer-seal.json')
    seal_sha = SEAL_SHA if recover_fixed_native else os.environ['NATIVE_TRANSFER_SEAL_SHA256']
    if recover_fixed_native:
        from recover_native import verify as verify_recovery, verify_acquisition
        acquisition_sha = verify_acquisition(Path('evidence'), Path('recovery-evidence/acquisition.json'), packaging_identity)
        _, recovery_provenance = verify_recovery(Path('evidence'), transfer_roots, packaging_identity)
    sealed_manifests = verify_transfer_seal(seal_path, Path('evidence/receipt.json'), transfer_roots,
                                          run_identity, expected_seal_sha=seal_sha)
    expected = json.loads(Path('evidence/product-hashes.json').read_text())
    if set(expected) != {'LiveContainer', 'SideStore'}: raise ValueError('Unexpected product set')
    root = Path('packaging-inputs')
    if root.exists() or root.is_symlink(): raise ValueError('Packaging inputs must be fresh')
    records = []
    widget_report = None
    for owner in expected:
        source = Path(os.environ['RUNNER_TEMP']) / ('native-' + owner) / 'Build/Products/Release-iphoneos' / (owner + '.app')
        observed, source_shape = inventory(source)
        if observed != expected[owner]: raise ValueError('Compiled product bytes changed: ' + owner)
        if recover_fixed_native:
            from optimize_native_candidate import retained_inventory
            retained_manifest = json.loads((transfer_roots[owner] / 'retained-files.json').read_text())
            if retained_inventory(source) != retained_manifest['app']:
                raise ValueError('Recovered product modes/links differ from original seal: ' + owner)
        target = root / (owner + '.app')
        shutil.copytree(source, target, symlinks=True)
        copied, copied_shape = inventory(target)
        if copied_shape != source_shape: raise ValueError('Packaging copy modes/links differ: ' + owner)
        if copied != expected[owner]: raise ValueError('Packaging input copy differs: ' + owner)
        if strip_unsigned_widget and owner == 'SideStore':
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
            from optimize_unsigned_widget import strip_unsigned_widget as optimize_widget
            try:
                widget_report = optimize_widget(target, transfer=transfer_roots['SideStore'],
                    receipt_path=Path('evidence/receipt.json'), expected_manifest_sha=sealed_manifests['SideStore'],
                    native_identity=native_identity, packaging_identity=packaging_identity,
                    work=Path('packaging-widget-work'), retained=Path('widget-diagnostics'),
                    include_true_locals=strip_widget_true_locals)
            finally:
                copy_size_reports(Path('packaging-widget-work'), Path('package-evidence/widget-reports'))
        for path in [target / 'Info.plist', *sorted(target.glob('PlugIns/*.appex/Info.plist'))]:
            if path.is_symlink(): raise ValueError('Linked application metadata')
            before = path.read_bytes()
            value = plistlib.loads(before)
            value['LCBuilderCommit'] = os.environ['GITHUB_SHA']
            after = plistlib.dumps(value, fmt=plistlib.FMT_BINARY if before.startswith(b'bplist00') else plistlib.FMT_XML, sort_keys=False)
            path.write_bytes(after)
            records.append(dict(path=path.relative_to(root).as_posix(), before_sha256=hashlib.sha256(before).hexdigest(),
                                after_sha256=sha(path), metadata_change='LCBuilderCommit only'))
    Path('package-evidence/builder-stamping.json').write_text(json.dumps(records, indent=2))
    output = Path('diagnostic/LiveContainer-SideStore-issues-experimental.ipa')
    initial_root = Path('packaging-size-inputs')
    if initial_root.exists() or initial_root.is_symlink(): raise ValueError('Size inputs must be fresh')
    initial_root.mkdir()
    initial = initial_root / 'initial-combined.ipa'
    if initial.exists() or initial.is_symlink(): raise ValueError('Initial IPA must be fresh')
    if output.exists() or output.is_symlink(): raise ValueError('IPA output must be fresh')
    from packaging_workspace import run as run_isolated_packager
    workspace_receipt = run_isolated_packager(Path('sources/LiveContainer'), root / 'LiveContainer.app',
        root / 'SideStore.app', initial, Path('packaging-upstream-work'),
        Path('harness/scripts/package_livecontainer_combined.py'))
    Path('package-evidence/packaging-workspace.json').write_text(json.dumps(workspace_receipt, indent=2))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
    from optimize_native_candidate import optimize_same_run_ipa
    # Revalidate sealed generated inputs and exact unstripped native products before packaging-only stripping.
    if recover_fixed_native:
        generated_openssl, recovery_provenance = verify_recovery(Path('evidence'), transfer_roots, packaging_identity)
    else:
        import record as native_record
        generated_openssl = {}
        for embedded, key in ((False, 'OpenSSLHost'), (True, 'OpenSSLEmbedded')):
            native_record.verify_generated_openssl_payload(embedded=embedded)
            snapshot = (native_record.generated_embedded_snapshot if embedded else native_record.generated_openssl_snapshot)(required=True, sealed=True)
            generated_root = native_record.S / snapshot['relative_path']
            if embedded: generated_root = generated_root / 'GeneratedDependencies/OpenSSL35'
            framework = native_record.generated_device_framework(generated_root)
            generated_openssl[key] = {'binary': framework / 'OpenSSL',
                'dsym': framework.parent / 'dSYMs/OpenSSL.framework.dSYM',
                'payload_receipt': native_record.E / ('generated-embedded-openssl-payload.json' if embedded else 'generated-openssl-payload.json')}
    initial_hash = sha(initial)
    try:
        optimized, size_report = optimize_same_run_ipa(initial, expected_sha=initial_hash,
            transfer_roots=transfer_roots,
            receipt_path=Path('evidence/receipt.json'), work=Path('packaging-size-work'),
            output=Path('packaging-size-products'), retained=Path('size-diagnostics'),
            run_identity=run_identity, generated_openssl=generated_openssl, include_local_symbols=True,
            expected_transfer_manifests=sealed_manifests,
            **({'packaging_identity': packaging_identity} if recover_fixed_native else {}))
        size_report['native_transfer_seal_sha256'] = seal_sha
        from optimize_sidebackup_candidate import optimize_sidebackup_same_run
        optimized, size_report = optimize_sidebackup_same_run(optimized, prior_report=size_report,
            transfer=transfer_roots['SideStore'], receipt_path=Path('evidence/receipt.json'),
            run_identity=run_identity, output=Path('packaging-sidebackup-products'),
            retained=Path('size-diagnostics/sidebackup'), expected_manifest_sha=sealed_manifests['SideStore'],
            **({'packaging_identity': packaging_identity} if recover_fixed_native else {}))
        if openssl_qualification is not None:
            optimized, size_report = append_openssl_local_stage(optimized, size_report, openssl_qualification,
                transfer_roots=transfer_roots, receipt_path=Path('evidence/receipt.json'), seal_path=seal_path,
                seal_sha=seal_sha, native_identity=native_identity, packaging_identity=packaging_identity)
        verify_transfer_seal(seal_path, Path('evidence/receipt.json'), transfer_roots,
                             run_identity, expected_seal_sha=seal_sha)
        shutil.copyfile(seal_path, Path('size-diagnostics/native-transfer-seal.json'))
        if sha(Path('size-diagnostics/native-transfer-seal.json')) != seal_sha:
            raise ValueError('Retained transfer seal differs')
    finally:
        copy_size_reports(Path('packaging-size-products'), Path('package-evidence/size-reports'))
        copy_size_reports(Path('packaging-sidebackup-products'), Path('package-evidence/sidebackup-reports'))
        copy_size_reports(Path('packaging-openssl-local-products'), Path('package-evidence/openssl-local-reports'))
    if recover_fixed_native:
        verify_recovery(Path('evidence'), transfer_roots, packaging_identity)
    else:
        native_record.verify_generated_openssl_payload()
        native_record.verify_generated_openssl_payload(embedded=True)
    # The transform never edits compiled products, receipts, or the initial IPA.
    for owner in expected:
        source = Path(os.environ['RUNNER_TEMP']) / ('native-' + owner) / 'Build/Products/Release-iphoneos' / (owner + '.app')
        if inventory(source)[0] != expected[owner]: raise ValueError('Compiled input changed during packaging: ' + owner)
    if sha(initial) != initial_hash: raise ValueError('Initial combined IPA changed')
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(optimized, output)
    if sha(output) != size_report['final_ipa_sha256']: raise ValueError('Final optimized IPA copy differs')
    from verify_russian_localization import verify_receipt as verify_russian_receipt, verify_ipa as verify_russian_ipa
    russian_receipt = Path('evidence/russian-localization/receipt.json')
    verify_russian_receipt(russian_receipt, Path('sources/LiveContainer'))
    if receipt.get('russian_localization_report_sha256') != sha(russian_receipt):
        raise ValueError('Russian runtime receipt differs from same-run compile receipt')
    russian = verify_russian_ipa(output, Path('sources/LiveContainer'), expected_permissions=plistlib.loads(
        Path('evidence/russian-localization/Russian.bundle/ru.lproj/InfoPlist.strings').read_bytes()))
    Path('diagnostic/russian-localization.json').write_text(json.dumps(russian, indent=2))
    if widget_report is not None:
        from optimize_unsigned_widget import verify_final_widget
        widget_receipt = Path('widget-diagnostics/unsigned-widget-receipt.json')
        size_report['prepackaging_unsigned_widget'] = verify_final_widget(initial,output,widget_receipt,widget_report,
            Path('widget-diagnostics/unsigned-widget-original-and-symbols.zip'))
        size_report['prepackaging_unsigned_widget']['raw_stage_savings'] = {
            'debug_stabs': widget_report['debug_only_stage']['raw_bytes_saved'],
            'true_locals': widget_report.get('true_local_stage', {}).get('raw_bytes_saved', 0)}
        size_report['prepackaging_unsigned_widget']['true_local_experiment_enabled'] = strip_widget_true_locals
        shutil.copyfile(widget_receipt, 'diagnostic/unsigned-widget-receipt.json')
        if sha(Path('diagnostic/unsigned-widget-receipt.json')) != sha(widget_receipt):
            raise ValueError('Retained widget receipt differs')
    Path('diagnostic/size-optimization.json').write_text(json.dumps(size_report, indent=2))
    provenance = dict(schema_version=1, native_run_id=native_identity['run_id'], native_run_attempt=native_identity['run_attempt'],
                      workflow_commit=os.environ['GITHUB_SHA'], source_graph=receipt['requested_graph'],
                      russian_localization_sha256=sha(Path('diagnostic/russian-localization.json')),
                      ipa_bytes=output.stat().st_size, ipa_sha256=sha(output), channel='experimental',
                      public_release_modified=False, physical_device_validation=False,
                      metadata_changes=records, initial_ipa_sha256=initial_hash,
                      size_optimization_receipt_sha256=sha(Path('diagnostic/size-optimization.json')),
                      native_transfer_seal_sha256=seal_sha,
                      retained_original_ipa_and_symbols_sha256=size_report['retained_debug_sidecar_sha256'],
                      signing_policy='reviewed Apple ad-hoc; original designated requirement replaced; installer resigning required')
    if openssl_qualification is not None:
        stage_receipt = Path('size-diagnostics/openssl-local/fourth-stage-receipt.json')
        expected_stage_sha = size_report['stages']['openssl_local_symbols']['receipt_sha256']
        if sha(stage_receipt) != expected_stage_sha:
            raise ValueError('OpenSSL fourth-stage receipt changed')
        shutil.copyfile(stage_receipt, 'diagnostic/openssl-local-stage.json')
        if sha(Path('diagnostic/openssl-local-stage.json')) != expected_stage_sha:
            raise ValueError('Retained OpenSSL fourth-stage receipt differs')
        provenance['openssl_local_qualification'] = dict(stage_receipt_sha256=expected_stage_sha,
            host_qualification_receipt_sha256=openssl_qualification[1], ios_runtime_qualified=False)
    if widget_report is not None:
        provenance['unsigned_widget_prepackaging'] = size_report['prepackaging_unsigned_widget']
    if recover_fixed_native:
        if verify_acquisition(Path('evidence'), Path('recovery-evidence/acquisition.json'), packaging_identity) != acquisition_sha:
            raise ValueError('Acquisition receipt changed during packaging')
        shutil.copyfile('recovery-evidence/acquisition.json', 'diagnostic/native-artifact-acquisition.json')
        if sha(Path('diagnostic/native-artifact-acquisition.json')) != acquisition_sha:
            raise ValueError('Retained acquisition receipt differs')
        recovery_provenance['artifact_acquisition_receipt_sha256'] = acquisition_sha
        provenance['native_workflow_commit'] = native_identity['workflow_commit']
        provenance['packaging_recovery'] = recovery_provenance
    if same_run_qualified:
        verify_startup_receipt(startup_receipt, Path('sources'), native_identity)
        shutil.copyfile(startup_receipt, 'diagnostic/issue56-startup-tests.json')
        if sha(Path('diagnostic/issue56-startup-tests.json')) != sha(startup_receipt):
            raise ValueError('Retained startup regression receipt differs')
        provenance['issue56_startup_regression_sha256'] = sha(startup_receipt)
    if language_switch:
        verify_language_receipt(language_receipt, Path('sources'), native_identity)
        shutil.copyfile(language_receipt, 'diagnostic/language-switch-tests.json')
        if sha(Path('diagnostic/language-switch-tests.json')) != sha(language_receipt):
            raise ValueError('Retained language runtime receipt differs')
        provenance['language_switch_regression_sha256'] = sha(language_receipt)
    if cold_vpn:
        verify_cold_vpn_receipt(cold_vpn_receipt, Path('sources'), native_identity)
        shutil.copyfile(cold_vpn_receipt, 'diagnostic/cold-vpn-tests.json')
        if sha(Path('diagnostic/cold-vpn-tests.json')) != sha(cold_vpn_receipt):
            raise ValueError('Retained cold VPN regression receipt differs')
        provenance['cold_vpn_regression_sha256'] = sha(cold_vpn_receipt)
    if multitask:
        verify_multitask(multitask_receipt, Path('sources'), Path('evidence/multitask-launch'), load_candidate_graph()['owners'], native_identity)
        shutil.copyfile(multitask_receipt, 'diagnostic/multitask-launch-tests.json')
        if sha(Path('diagnostic/multitask-launch-tests.json')) != sha(multitask_receipt):
            raise ValueError('Retained guest qualification receipt differs')
        provenance['multitask_launch_regression_sha256'] = sha(multitask_receipt)
        verify_startup_upgrade(schema_receipt, Path('sources'), Path('schema-sources'), Path('evidence'), load_candidate_graph()['owners'], native_identity)
        shutil.copyfile(schema_receipt, 'diagnostic/startup-upgrade-tests.json')
        if sha(Path('diagnostic/startup-upgrade-tests.json')) != sha(schema_receipt):
            raise ValueError('Retained Core Data qualification receipt differs')
        provenance['startup_upgrade_regression_sha256'] = sha(schema_receipt)
    Path('diagnostic/provenance.json').write_text(json.dumps(provenance, indent=2))
    Path('diagnostic/SHA256SUMS').write_text(sha(output) + '  ' + output.name + '\n')
    shutil.copyfile('evidence/receipt.json', 'diagnostic/native-receipt.json')
    if output.stat().st_size >= 524288000: raise ValueError('IPA exceeds supported artifact transfer size')


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / 'Product.app'
        root.mkdir()
        binary = root / 'binary'
        binary.write_bytes(b'not executable test fixture')
        binary.chmod(0o755)
        link = root / 'link'
        link.symlink_to('binary')
        files, shape = inventory(root)
        assert files['binary'] == files['link']
        copied = Path(temp) / 'Copy.app'
        shutil.copytree(root, copied, symlinks=True)
        assert inventory(copied) == (files, shape)
        (copied / 'binary').chmod(0o644)
        assert inventory(copied)[1] != shape
        for target in ('/etc/hosts', '../outside', 'missing'):
            link.unlink()
            link.symlink_to(target)
            try:
                inventory(root)
            except (ValueError, FileNotFoundError):
                pass
            else:
                raise AssertionError('unsafe link admitted: ' + target)
        root_link = Path(temp) / 'root-link'
        root_link.symlink_to(root)
        try:
            inventory(root_link)
        except ValueError:
            pass
        else:
            raise AssertionError('linked root admitted')
    print('PASS packaging inventory: copy bytes/modes, internal links, external/dangling/root-link rejection')


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        self_test()
    elif sys.argv[1:] == ['--same-run-qualified-size', '--language-switch', '--cold-vpn', '--multitask']:
        main(same_run_qualified=True, strip_unsigned_widget=True, strip_widget_true_locals=True, strip_openssl_true_locals=True, language_switch=True, cold_vpn=True, multitask=True)
    elif sys.argv[1:] == ['--same-run-qualified-size', '--language-switch', '--cold-vpn']:
        main(same_run_qualified=True, strip_unsigned_widget=True, strip_widget_true_locals=True, strip_openssl_true_locals=True, language_switch=True, cold_vpn=True)
    elif sys.argv[1:] == ['--same-run-qualified-size', '--language-switch']:
        main(same_run_qualified=True, strip_unsigned_widget=True, strip_widget_true_locals=True, strip_openssl_true_locals=True, language_switch=True)
    elif sys.argv[1:] == ['--same-run-qualified-size']:
        main(same_run_qualified=True, strip_unsigned_widget=True, strip_widget_true_locals=True, strip_openssl_true_locals=True)
    elif sys.argv[1:] == ['--recover-fixed-native', '--strip-unsigned-widget', '--strip-widget-true-locals', '--strip-openssl-true-locals']:
        main(recover_fixed_native=True, strip_unsigned_widget=True, strip_widget_true_locals=True, strip_openssl_true_locals=True)
    elif sys.argv[1:] == ['--recover-fixed-native', '--strip-unsigned-widget', '--strip-widget-true-locals']:
        main(recover_fixed_native=True, strip_unsigned_widget=True, strip_widget_true_locals=True)
    elif sys.argv[1:] == ['--recover-fixed-native', '--strip-unsigned-widget']:
        main(recover_fixed_native=True, strip_unsigned_widget=True)
    elif sys.argv[1:] == ['--recover-fixed-native']:
        main(recover_fixed_native=True)
    elif sys.argv[1:]:
        raise SystemExit('unknown arguments')
    else:
        main()
