"""Opt-in third-stage adapter. Not invoked by the production packager by default."""
import copy
import io
import json
import stat
import zipfile
from experiment_sidebackup_stabs import OUTER, BINARY, transform_bound_nested
from experiment_strip_stabs import digest, unique_zip, write
from macho_stab_guard import require, sha, image_uuid
from optimize_native_candidate import retained_inventory, admitted_packaging_identity


def bind_same_run(ipa, prior_report, transfer, receipt_path, run_identity, expected_manifest_sha):
    require(prior_report['status']=='TWO_STAGE_STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED', 'qualified local stage required')
    require(prior_report['same_run_identity']==run_identity,'prior stage identity differs')
    stages=prior_report['stages']
    require(set(stages)=={'debug_stabs','local_symbols'},'exact prior two-stage closure required')
    require(stages['debug_stabs']['input_ipa_sha256']==prior_report['original_ipa_sha256'] and
            stages['debug_stabs']['final_ipa_sha256']==stages['local_symbols']['input_ipa_sha256'] and
            stages['local_symbols']['final_ipa_sha256']==prior_report['final_ipa_sha256'],'prior stage hash chain differs')
    require(all(type(stages[k]['archive_bytes_saved']) is int and stages[k]['archive_bytes_saved']>0 for k in stages) and
            sum(stages[k]['archive_bytes_saved'] for k in stages)==prior_report['archive_bytes_saved'],'prior stage savings chain differs')
    require(ipa.is_file() and not ipa.is_symlink() and digest(ipa)==prior_report['final_ipa_sha256'],'prior stage IPA differs')
    require(transfer.is_dir() and not transfer.is_symlink(),'native transfer must be real')
    root=transfer.resolve(strict=True)
    def checked(path):
        require(not path.is_symlink() and path.resolve(strict=True).is_relative_to(root),'native input escapes transfer')
        return path
    require(receipt_path.is_file() and not receipt_path.is_symlink(),'native receipt must be a real file')
    require(ipa.stat().st_size==prior_report['final_ipa_bytes'],'prior stage measured bytes differ')
    receipt=json.loads(receipt_path.read_text());receipt_hash=digest(receipt_path)
    require(receipt['status']=='COMPILE_ONLY_PASS_REQUIRES_REVIEW' and
            all(receipt[k]==run_identity[k] for k in ('run_id','run_attempt','workflow_commit')),'native receipt identity differs')
    require(prior_report['native_receipt_sha256']==receipt_hash,'prior native receipt differs')
    require(digest(checked(transfer/'evidence/receipt.json'))==receipt_hash,'transferred native receipt differs')
    products=checked(transfer/'evidence/product-hashes.json')
    require(digest(products)==receipt['evidence_sha256']['product-hashes.json'],'sealed native product inventory differs')
    manifest_path=checked(transfer/'retained-files.json')
    require(digest(manifest_path)==expected_manifest_sha,'pre-packaging sealed retention manifest differs')
    manifest=json.loads(manifest_path.read_text())
    native=checked(transfer/'Products/SideStore.app/SideBackup.ipa')
    native_hash=digest(native)
    require(native_hash==json.loads(products.read_text())['SideStore']['SideBackup.ipa'],'compiled nested IPA differs')
    require(manifest['app']['SideBackup.ipa']=={'kind':'file','mode':stat.S_IMODE(native.stat().st_mode),'sha256':native_hash},'retained nested inventory differs')
    nested=native.read_bytes()
    with unique_zip(ipa) as archive:require(archive.read(OUTER)==nested,'outer/natively compiled nested IPA differs')
    with unique_zip(io.BytesIO(nested)) as archive:binary=archive.read(BINARY)
    bundle=checked(transfer/'Symbols/SideBackup.app.dSYM');inventory=retained_inventory(bundle)
    require(inventory==manifest['symbols'][bundle.name],'full native dSYM inventory differs')
    require('Contents/Info.plist' in inventory,'incomplete native dSYM')
    dwarf=checked(bundle/'Contents/Resources/DWARF/SideBackup')
    require(image_uuid(dwarf.read_bytes())==image_uuid(binary),'native binary/dSYM UUID differs')
    diagnostics={'uuid':image_uuid(binary),'dwarf_sha256':digest(dwarf),'nested_ipa_sha256':native_hash,
                 'native_receipt_sha256':receipt_hash,'retained_manifest_sha256':digest(manifest_path)}
    seals={path:digest(path) for path in (ipa,receipt_path,native,products,manifest_path,transfer/'evidence/receipt.json')}
    return nested,bundle,inventory,diagnostics,seals


def optimize_sidebackup_same_run(ipa, *, prior_report, transfer, receipt_path, run_identity, output, retained, expected_manifest_sha, packaging_identity=None):
    """Explicit callable only; caller must opt in after native qualification."""
    for folder in (output,retained):require(not folder.exists() and not folder.is_symlink(),'fresh third-stage directories required')
    packaging_identity = admitted_packaging_identity(run_identity, packaging_identity)
    require(prior_report.get('packaging_run_identity', run_identity) == packaging_identity, 'prior packaging identity differs')
    caller_prior,caller_identity=prior_report,run_identity
    admitted_prior=copy.deepcopy(prior_report);admitted_identity=copy.deepcopy(run_identity)
    prior_report,run_identity=admitted_prior,admitted_identity
    admission_hash=sha(json.dumps({'prior':admitted_prior,'identity':admitted_identity},sort_keys=True,separators=(',',':')).encode())
    nested,bundle,inventory,diagnostics,seals=bind_same_run(ipa,prior_report,transfer,receipt_path,run_identity,expected_manifest_sha)
    output.mkdir(parents=True);retained.mkdir(parents=True)
    report={'schema':'same-run-sidebackup-debug-stab-v1','status':'FAILED','ipa_emitted':False,
            'runtime_proven':False,'same_run_identity':run_identity,
            'native_run_identity':dict(run_identity),'packaging_run_identity':dict(packaging_identity),'input_ipa_sha256':digest(ipa),
            'diagnostics':diagnostics, 'native_receipt_sha256':digest(receipt_path)}
    final=output/'SideBackup-debug-qualified.ipa'
    try:
        write(output/'prior-stage-receipt.json',prior_report)
        report['prior_stage_receipt_sha256']=digest(output/'prior-stage-receipt.json')
        sidecar=output/'retained-original-and-symbols.zip'
        with zipfile.ZipFile(sidecar,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            archive.writestr('Original/SideBackup.ipa',nested)
            for relative,row in inventory.items():
                path=bundle if relative=='.' else bundle/relative
                name='Symbols/'+bundle.name+('/' if relative=='.' else '/'+relative)
                if row['kind']=='symlink':
                    info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=(stat.S_IFLNK|row['mode'])<<16
                    archive.writestr(info,row['target'].encode())
                else:archive.write(path,name)
        with unique_zip(sidecar) as archive:
            require(archive.read('Original/SideBackup.ipa')==nested,'retained nested bytes differ')
            for relative,row in inventory.items():
                if row['kind']=='file':require(sha(archive.read('Symbols/'+bundle.name+'/'+relative))==row['sha256'],'retained dSYM bytes differ')
        sidecar_hash=digest(sidecar)
        immutable_keys=('schema','runtime_proven','same_run_identity','native_run_identity','packaging_run_identity','input_ipa_sha256','diagnostics',
                        'native_receipt_sha256','prior_stage_receipt_sha256')
        immutable_fields=copy.deepcopy({key:report[key] for key in immutable_keys})
        admitted_diagnostics=copy.deepcopy(report['diagnostics'])
        diagnostics_hash=sha(json.dumps(admitted_diagnostics,sort_keys=True,separators=(',',':')).encode())
        transform_bound_nested(ipa,digest(ipa),nested,output,report)
        require(digest(sidecar)==sidecar_hash,'retained diagnostics changed during transformation')
        require({key:report[key] for key in immutable_keys}==immutable_fields and report['runtime_proven'] is False,
                'immutable third-stage receipt fields changed')
        require(digest(output/'prior-stage-receipt.json')==immutable_fields['prior_stage_receipt_sha256'],
                'persisted admitted prior receipt changed')
        require(sha(json.dumps({'prior':caller_prior,'identity':caller_identity},sort_keys=True,separators=(',',':')).encode())==admission_hash and
                sha(json.dumps({'prior':admitted_prior,'identity':admitted_identity},sort_keys=True,separators=(',',':')).encode())==admission_hash and
                sha(json.dumps(report['diagnostics'],sort_keys=True,separators=(',',':')).encode())==diagnostics_hash and
                report['diagnostics']==admitted_diagnostics and report['same_run_identity']==admitted_identity,
                'caller or admitted provenance mutated during transformation')
        require(all(digest(path)==value for path,value in seals.items()) and retained_inventory(bundle)==inventory,'same-run input changed during third stage')
        require(report['status']=='STRUCTURAL_PACKAGE_PASS_DEVICE_UNVERIFIED' and report['ipa_emitted'] is True and
                digest(final)==report['final_ipa_sha256'] and report['final_ipa_bytes']==final.stat().st_size and
                report['original_ipa_bytes']==ipa.stat().st_size and
                report['archive_bytes_saved']==ipa.stat().st_size-final.stat().st_size>0,'third-stage qualification differs')
        write(output/'third-stage-receipt.json',report)
        cumulative=copy.deepcopy(prior_report)
        cumulative['stages']['sidebackup_debug_stabs']={'input_ipa_sha256':report['input_ipa_sha256'],
            'final_ipa_sha256':report['final_ipa_sha256'],'archive_bytes_saved':report['archive_bytes_saved'],
            'receipt_sha256':digest(output/'third-stage-receipt.json'),'retained_diagnostics_sha256':digest(sidecar)}
        cumulative.update(schema='same-run-three-stage-symbol-optimization-v1',
            native_run_identity=dict(run_identity),packaging_run_identity=dict(packaging_identity),
            status='THREE_STAGE_STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED',
            final_ipa_sha256=report['final_ipa_sha256'],final_ipa_bytes=final.stat().st_size,
            archive_bytes_saved=prior_report['archive_bytes_saved']+report['archive_bytes_saved'],
            target_24_decimal_MB_met=final.stat().st_size<=24_000_000)
        from ipa_size_report import report as size_report
        cumulative['local_symbols_after_size']=cumulative['after_size'];cumulative['after_size']=size_report(final)
        cumulative['uncompressed_bytes_saved']=cumulative['before_size']['uncompressed_bundle_bytes']-cumulative['after_size']['uncompressed_bundle_bytes']
        for source in (sidecar,output/'third-stage-receipt.json'):
            destination=retained/source.name;destination.write_bytes(source.read_bytes())
            require(digest(destination)==digest(source),'retained third-stage copy differs')
        write(retained/'prior-stage-receipt.json',prior_report)
        require(digest(retained/'prior-stage-receipt.json')==report['prior_stage_receipt_sha256'],'retained prior-stage receipt differs')
        write(retained/'cumulative-receipt.json',cumulative)
        return final,cumulative
    except Exception as error:
        final.unlink(missing_ok=True);report.update(status='FAILED',ipa_emitted=False,error=str(error));raise
    finally:write(output/'experiment.json',report)
