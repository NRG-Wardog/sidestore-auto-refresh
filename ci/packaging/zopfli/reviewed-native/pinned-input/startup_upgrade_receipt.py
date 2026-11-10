"""Same-run Core Data evidence binding. Never opens or relocates a product database."""
import argparse, hashlib, importlib.util, json, os, re, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SCHEMA='native-startup-upgrade-binding-v1'
STATUS='PASS_SAME_RUN_REAL_COREDATA_NOT_PRODUCTION_RELOCATION'
MODEL='AltStore/Core/Model/AltStore.xcdatamodeld'
PERSISTENT='AltStore/Core/Model/DatabaseManager/PersistentContainer.swift'
CASES=['delete_actual_schema_migration','delete_destination_no_overwrite','wal_actual_schema_migration','wal_destination_no_overwrite','unsupported_mapping_or_required_attribute_rejected','unknown_model_refused','unwritable_destination_refused']
def require(ok,why):
    if not ok:raise ValueError(why)
def pairs(rows):
    out={}
    for key,value in rows:require(key not in out,'duplicate receipt key');out[key]=value
    return out
def read(path):
    require(path.is_file() and not path.is_symlink(),'missing or linked receipt');return json.loads(path.read_text(),object_pairs_hook=pairs)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def identity():return {k:os.environ[v] for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]}
def valid_identity(value):
    require(type(value) is dict and set(value)=={'workflow_commit','run_id','run_attempt'},'run identity closure')
    require(type(value['workflow_commit']) is str and re.fullmatch('[0-9a-f]{40}',value['workflow_commit']) is not None,'workflow commit')
    require(all(type(value[k]) is str and re.fullmatch('[1-9][0-9]*',value[k]) for k in ('run_id','run_attempt')),'run/attempt')
def focused_module(name):
    path=ROOT/'ci/startup-upgrade'/f'{name}.py'
    spec=importlib.util.spec_from_file_location('_startup_upgrade_'+name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def verify_discovery(row,layout):
    require(row['schema']=='coredata-owned-framework-discovery-v3','discovery schema differs')
    truth=('main_bundle_is_isolated_host','owner_is_requested_framework','explicit_owner_source_model_found',
        'explicit_owner_model_compatible','explicit_supplied_source_model_found','explicit_supplied_model_compatible',
        'production_owned_source_model_found','production_owned_model_compatible','production_mapping_selected',
        'production_owned_model_urls_include_source_and_target','production_wrong_version_bundle_exposes_current_model')
    falsity=('source_model_found_before_framework_load','unknown_source_model_found_in_explicit_owner',
        'main_is_requested_framework','baseline_source_model_found','baseline_model_compatible',
        'production_nil_owner_source_model_found','production_unknown_source_model_found',
        'production_wrong_version_source_model_found','physical_lc_bootstrap_executed')
    require(all(row.get(k) is True for k in truth),'production discovery positive evidence missing')
    require(all(row.get(k) is False for k in falsity),'discovery counterfactual/negative evidence differs')
    for key in ('main_bundle_model_resources_before','main_bundle_model_resources_after'):
        require(type(row.get(key)) is int and row[key]==0,'isolated host contains model resources')
    require(row['owner_package_type']==layout.split('-')[0],'framework packaging type differs')
    require(all(type(row.get(k)) is bool for k in ('all_bundles_contains_owner','all_frameworks_contains_owner')),'bundle inventory observations missing')
    require(type(row.get('limitation')) is str and row['limitation'],'discovery scope limit missing')
    isolated=row.get('isolated_negative_processes')
    require(type(isolated) is dict and set(isolated)=={'nil-owner','wrong-version','unknown'},'isolated negative process closure differs')
    projection={'nil-owner':'production_nil_owner_source_model_found','wrong-version':'production_wrong_version_source_model_found','unknown':'production_unknown_source_model_found'}
    for mode,negative in isolated.items():
        require(negative.get('schema')=='coredata-isolated-helper-negative-v1' and negative.get('mode')==mode,'negative process mode/schema differs')
        require(all(negative.get(k) is True for k in ('main_bundle_is_isolated_host','owner_is_requested_framework','current_model_is_v17_7')),'negative process isolation missing')
        require(all(negative.get(k) is False for k in ('source_model_found_before_framework_load','baseline_source_model_found','current_model_matches_old_metadata','production_source_model_found','physical_lc_bootstrap_executed')),'negative process observed failure differs')
        require(negative['production_source_model_found'] is row[projection[mode]],'negative process projection differs')
        for key in ('main_bundle_model_resources_before','main_bundle_model_resources_after'):
            require(type(negative.get(key)) is int and negative[key]==0,'negative isolated host contains model resources')


def validate_report(report,graph,expected_identity):
    valid_identity(expected_identity)
    require(type(report) is dict and set(report)=={'schema','run_identity','acquired_owners','acquisition_sha256','source_before_sha256','source_after_sha256','other_graph_owners_acquired','working_directory_outside_checkouts','output_outside_checkouts','status','source_sha256','production_mapping_methods_sha256','cases','framework_discovery','physical_device_execution','production_relocation_enabled','scope_limits'},'schema report closure differs')
    require(report['schema']=='real-coredata-schema-qualification-v1' and report['status']=='PASS_REAL_MODELS_SQLITE_NOT_PRODUCTION_RELOCATION','schema qualification not passed')
    require(report['run_identity']==expected_identity,'schema run identity differs')
    owners=report['acquired_owners'];require(set(owners)=={'LiveContainer','SideStore'},'schema owner closure')
    for owner,row in owners.items():
        require(set(row)=={'repository','commit','tree'} and [row['repository'],row['commit']]==list(graph[owner]),'schema source pin differs')
        require(re.fullmatch('[0-9a-f]{40}',row['tree']) is not None,'schema source tree')
    require(report['physical_device_execution'] is False and report['production_relocation_enabled'] is False and report['other_graph_owners_acquired'] is False,'invalid qualification claim')
    require(report['working_directory_outside_checkouts'] is True and report['output_outside_checkouts'] is True,'qualification source isolation missing')
    cases=report['cases'];require(type(cases) is list and [v['case'] for v in cases]==CASES and all(v['status']=='PASS' for v in cases),'schema cases incomplete')
    require(set(report['framework_discovery'])=={'FMWK-resources','APPL-flat'},'framework layouts incomplete')
    # The source-owned validator defines the exact observed helper/counterfactual
    # contract. Its source digest is bound to this same workflow below.
    for layout,row in report['framework_discovery'].items():verify_discovery(row,layout)
    hashes=report['source_sha256'];require(type(hashes) is dict and PERSISTENT in hashes and len(hashes)>1,'schema source closure empty')
    for name,digest in hashes.items():
        require(type(name) is str and not Path(name).is_absolute() and '..' not in Path(name).parts and (name==PERSISTENT or name.startswith(MODEL+'/')),'unexpected schema source path')
        require(type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest) is not None,'invalid schema source digest')
    for key in ('acquisition_sha256','source_before_sha256','source_after_sha256','production_mapping_methods_sha256'):
        require(type(report[key]) is str and re.fullmatch('[0-9a-f]{64}',report[key]) is not None,'invalid schema evidence hash')
    require(type(report['scope_limits']) is list and len(report['scope_limits'])>=6 and all(type(v) is str and v for v in report['scope_limits']),'schema scope limits missing')
    return report

def verify_portable(value,graph,expected_identity):
    require(set(value)=={'schema','status','identity','source_graph','qualification','qualification_sha256','evidence_sha256','verifier_sha256','physical_device_execution','production_relocation_enabled'},'binding receipt closure')
    require(value['schema']==SCHEMA and value['status']==STATUS,'startup upgrade binding not passed')
    require(value['identity']==expected_identity and value['source_graph']==graph,'binding run/source graph differs')
    require(value['physical_device_execution'] is False and value['production_relocation_enabled'] is False,'binding scope differs')
    require(value['qualification_sha256']==canonical(value['qualification']),'embedded qualification changed')
    validate_report(value['qualification'],graph,expected_identity)
    required={'schema-acquisition.json','schema-source-immutability.json','startup-upgrade/report.json','startup-upgrade/source-before.json','startup-upgrade/source-after.json'}
    hashes=value['evidence_sha256'];require(type(hashes) is dict and required<=set(hashes),'binding evidence closure missing')
    for name,digest in hashes.items():
        p=Path(name);require(not p.is_absolute() and '..' not in p.parts and p.suffix in ('.json','.log'),'unsafe evidence path')
        require(type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest) is not None,'invalid evidence digest')
    for key,path in [('acquisition_sha256','schema-acquisition.json'),('source_before_sha256','startup-upgrade/source-before.json'),('source_after_sha256','startup-upgrade/source-after.json')]:
        require(value['qualification'][key]==hashes[path],'qualification evidence lineage differs')
    require(value['verifier_sha256']==sha(Path(__file__)),'startup binding verifier differs')
    return value

def current_evidence(evidence):
    require(evidence.is_dir() and not evidence.is_symlink(),'evidence root missing/linked')
    root=evidence.resolve(strict=True);paths=[root/'schema-acquisition.json',root/'schema-source-immutability.json']
    folder=root/'startup-upgrade';require(folder.is_dir() and not folder.is_symlink(),'schema evidence folder missing/linked')
    paths += [root/name for name in ('schema-acquisition.log','schema-controls.log','schema-driver.log')]
    paths += sorted(p for p in folder.iterdir() if p.suffix in ('.json','.log'))
    result={}
    for path in paths:
        require(path.is_file() and not path.is_symlink() and path.resolve(strict=True).is_relative_to(root),'schema evidence missing/linked')
        result[path.relative_to(root).as_posix()]=sha(path)
    return result

def verify_sources(report,native_sources):
    source=native_sources/'SideStore';require(source.is_dir() and not source.is_symlink(),'native SideStore source missing/linked')
    actual={p.relative_to(source).as_posix():sha(p) for p in sorted((source/MODEL).rglob('*')) if p.is_file()}
    actual[PERSISTENT]=sha(source/PERSISTENT)
    for name in actual:
        path=source/name;require(not path.is_symlink() and not any((source/parent).is_symlink() for parent in Path(name).parents) and path.resolve(strict=True).is_relative_to(source.resolve(strict=True)),'schema source linked/outside')
    require(actual==report['source_sha256'],'native compiled schema sources differ from qualification')

def verify_local(path,native_sources,schema_sources,evidence,graph,expected_identity):
    require(path.parent.resolve(strict=True)==evidence.resolve(strict=True) and path.name=='startup-upgrade-tests.json','binding receipt location differs')
    value=verify_portable(read(path),graph,expected_identity)
    require(value['evidence_sha256']==current_evidence(evidence),'retained schema evidence changed')
    require(value['qualification']==read(evidence/'startup-upgrade/report.json'),'raw qualification differs')
    acquirer=focused_module('acquire')
    # Only the dedicated two-owner checkout is required pristine. The native
    # checkout retains its existing exact generated-input/compiler seals.
    with tempfile.TemporaryDirectory(prefix='startup-schema-recheck-') as temp:
        output=Path(temp).resolve()/'source-verification.json'
        acquirer.verify(schema_sources,evidence/'schema-acquisition.json',output)
        require(read(output)==read(evidence/'schema-source-immutability.json'),'schema final source seal differs')
    verify_sources(value['qualification'],native_sources)
    return value

def seal(output,native_sources,schema_sources,evidence,graph,expected_identity):
    require(output.parent.resolve(strict=True)==evidence.resolve(strict=True) and output.name=='startup-upgrade-tests.json','binding output location differs')
    require(not output.exists() and not output.is_symlink(),'fresh binding receipt required')
    report=validate_report(read(evidence/'startup-upgrade/report.json'),graph,expected_identity)
    value={'schema':SCHEMA,'status':STATUS,'identity':expected_identity,'source_graph':graph,'qualification':report,
        'qualification_sha256':canonical(report),'evidence_sha256':current_evidence(evidence),'verifier_sha256':sha(Path(__file__)),
        'physical_device_execution':False,'production_relocation_enabled':False}
    output.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
    try:verify_local(output,native_sources,schema_sources,evidence,graph,expected_identity)
    except Exception:
        output.unlink();raise
    return value

if __name__=='__main__':
    from verify_candidate_graph import load_candidate_graph
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['seal','verify']);parser.add_argument('--native-sources',type=Path,required=True);parser.add_argument('--schema-sources',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    fn=seal if args.mode=='seal' else verify_local
    fn(args.output,args.native_sources,args.schema_sources,args.evidence,load_candidate_graph()['owners'],identity())
