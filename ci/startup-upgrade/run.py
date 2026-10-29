#!/usr/bin/env python3
"""Compile exact CoreData models and real SQLite fixtures; no production relocation."""
import argparse, hashlib, json, os, pathlib, plistlib, shutil, subprocess, sys, time
ROOT=pathlib.Path(__file__).resolve().parent
MODEL='AltStore/Core/Model/AltStore.xcdatamodeld'
PERSISTENT='AltStore/Core/Model/DatabaseManager/PersistentContainer.swift'
METHOD_START='    private func sourceModel('
METHOD_END='    @objc private func managedObjectContextWillSave('
def require(ok,message):
    if not ok: raise ValueError(message)
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def validate_layout(source,output):
    require(not source.is_symlink() and not output.is_symlink(),'linked source/output root')
    source=source.resolve(strict=True);output=output.resolve()
    require(source.is_dir(),'source directory required')
    require(not output.exists(),'fresh output required')
    require(not output.is_relative_to(source) and not source.is_relative_to(output),'source/output overlap')
    for path in (source/MODEL).rglob('*'):require(not path.is_symlink(),'linked model resource')
    require(not (source/PERSISTENT).is_symlink(),'linked production mapping source')
    return source,output
def mapping_methods(path):
    text=path.read_text();require(text.count(METHOD_START)==1 and text.count(METHOD_END)==1,'production mapping boundaries differ')
    return text[text.index(METHOD_START):text.index(METHOD_END)]
def schema_delta(source):
    import xml.etree.ElementTree as ET
    old=ET.parse(source/MODEL/'AltStore 17_6.xcdatamodel/contents').getroot()
    new=ET.parse(source/MODEL/'AltStore 17_7.xcdatamodel/contents').getroot()
    old.attrib.pop('userDefinedModelVersionIdentifier');new.attrib.pop('userDefinedModelVersionIdentifier')
    matches=[e for e in new.findall('entity') if e.get('name')=='InstalledApp'];require(len(matches)==1,'InstalledApp entity missing')
    attrs=[a for a in matches[0].findall('attribute') if a.get('name')=='appBundleFingerprint'];require(len(attrs)==1,'new attribute missing')
    require(attrs[0].attrib=={'name':'appBundleFingerprint','optional':'YES','attributeType':'String'},'new attribute not optional String')
    matches[0].remove(attrs[0])
    def canonical(e): return (e.tag,sorted(e.attrib.items()),[canonical(c) for c in e])
    require(canonical(old)==canonical(new),'unexpected schema difference')
def inventory(root):
    return {str(p.relative_to(root)):{'bytes':p.stat().st_size,'sha256':sha(p),'mode':p.stat().st_mode&0o777} for p in sorted(root.rglob('*')) if p.is_file()}
def run(command,log,expect=0,timeout=90,required_token=None):
    result=subprocess.run([str(x) for x in command],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
    log.write_text(result.stdout)
    require((result.returncode==0) if expect==0 else (result.returncode!=0),'unexpected command result: '+log.name)
    if required_token is not None:require(required_token in result.stdout,'expected rejection phase/code absent: '+log.name)
    return result.stdout

def verify_negative(result,mode):
    require(result['schema']=='coredata-isolated-helper-negative-v1' and result['mode']==mode,'negative process identity differs')
    for key in ('main_bundle_is_isolated_host','owner_is_requested_framework','current_model_is_v17_7'):
        require(result[key] is True,'negative fixture invalid: '+key)
    for key in ('source_model_found_before_framework_load','baseline_source_model_found',
                'current_model_matches_old_metadata','production_source_model_found','physical_lc_bootstrap_executed'):
        require(result[key] is False,'isolated negative failed: '+key)
    for key in ('main_bundle_model_resources_before','main_bundle_model_resources_after'):
        require(type(result[key]) is int and result[key]==0,'negative main resource contamination')

def verify_discovery(result):
    require(result['schema']=='coredata-owned-framework-discovery-v3','discovery schema differs')
    require(set(result['isolated_negative_processes'])=={'nil-owner','wrong-version','unknown'},'missing independent negative processes')
    for mode,row in result['isolated_negative_processes'].items():verify_negative(row,mode)
    for mode,field in [('nil-owner','production_nil_owner_source_model_found'),('wrong-version','production_wrong_version_source_model_found'),('unknown','production_unknown_source_model_found')]:
        require(result[field] is result['isolated_negative_processes'][mode]['production_source_model_found'],'negative projection differs')
    require(result['main_bundle_is_isolated_host'] is True,'main bundle is not isolated host')
    for field in ('main_bundle_model_resources_before','main_bundle_model_resources_after'):
        require(type(result[field]) is int and result[field]==0,'main bundle contains model resources')
    require(result['source_model_found_before_framework_load'] is False,'baseline contaminated before framework load')
    require(result['unknown_source_model_found_in_explicit_owner'] is False,'unknown metadata accepted')
    require(result['owner_is_requested_framework'] is True and result['main_is_requested_framework'] is False,'loaded framework ownership not modeled')
    for field in ('explicit_owner_source_model_found','explicit_owner_model_compatible',
                  'explicit_supplied_source_model_found','explicit_supplied_model_compatible'):
        require(result[field] is True,'explicit bundle does not expose compatible compiled source model')
    # The isolated pre-fix failure was observed in run 40884677384. Preserve it as a regression counterfactual.
    for field in ('baseline_source_model_found','baseline_model_compatible',
                  'production_nil_owner_source_model_found','production_unknown_source_model_found',
                  'production_wrong_version_source_model_found'):
        require(result[field] is False,'old-path or model-negative counterfactual differs: '+field)
    for field in ('production_owned_source_model_found','production_owned_model_compatible',
                  'production_mapping_selected','production_owned_model_urls_include_source_and_target',
                  'production_wrong_version_bundle_exposes_current_model'):
        require(result[field] is True,'actual production helper qualification failed: '+field)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=pathlib.Path,required=True);parser.add_argument('--output',type=pathlib.Path,required=True);parser.add_argument('--acquisition',type=pathlib.Path,required=True);args=parser.parse_args()
    source,output=validate_layout(args.source,args.output)
    harness=ROOT.parents[1].resolve();cwd=pathlib.Path.cwd().resolve()
    require(not cwd.is_relative_to(harness) and not cwd.is_relative_to(source),'driver must execute outside source and harness checkouts')
    require(not output.is_relative_to(harness),'schema output must be outside harness')
    output.mkdir(parents=True)
    require(sys.platform=='darwin','Apple CoreData/macOS required; no simulator or stub substitute')
    require(os.geteuid()!=0,'permissions fixture requires unprivileged runner')
    from acquire import verify,identity,selected_graph
    verify(source.parent,args.acquisition,output/'source-before.json')
    schema_delta(source)
    source_hashes={str(p.relative_to(source)):sha(p) for p in sorted((source/MODEL).rglob('*')) if p.is_file()}
    source_hashes[PERSISTENT]=sha(source/PERSISTENT)
    methods=mapping_methods(source/PERSISTENT)
    swift=(ROOT/'fixture.swift').read_text();require(swift.count('// EXACT_PRODUCTION_MAPPING_METHODS')==1,'fixture marker differs')
    (output/'fixture.swift').write_text(swift.replace('// EXACT_PRODUCTION_MAPPING_METHODS',methods))
    models=output/'AltStore.momd'
    run(['xcrun','momc','--macosx-deployment-target','13.0',source/MODEL,models],output/'momc.log',timeout=180)
    binary=output/'fixture'
    run(['xcrun','swiftc',output/'fixture.swift','-o',binary],output/'swiftc.log',timeout=180)
    host=output/'isolated-discovery-host'/'DiscoveryHost.app'
    (host/'Contents/MacOS').mkdir(parents=True);(host/'Contents/Resources').mkdir()
    (host/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.example.schema-discovery-host','CFBundleName':'DiscoveryHost','CFBundleExecutable':'discovery','CFBundlePackageType':'APPL','CFBundleVersion':'1'}))
    discovery=host/'Contents/MacOS/discovery'
    discovery_source=(ROOT/'discovery.swift').read_text();require(discovery_source.count('// EXACT_PRODUCTION_MAPPING_METHODS')==1,'discovery marker differs')
    (output/'discovery.swift').write_text(discovery_source.replace('// EXACT_PRODUCTION_MAPPING_METHODS',methods))
    run(['xcrun','swiftc',output/'discovery.swift','-o',discovery],output/'discovery-compile.log',timeout=180)
    run([binary,'seed-delete',models,output/'discovery-original.sqlite'],output/'discovery-seed.log')
    run([binary,'seed-unknown',models,output/'discovery-unknown.sqlite'],output/'discovery-unknown-seed.log')
    wrong=output/'WrongVersion.bundle';wrong_resources=wrong/'Contents/Resources';wrong_resources.mkdir(parents=True)
    (wrong/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.example.schema-wrong-version','CFBundlePackageType':'BNDL','CFBundleVersion':'1'}))
    shutil.copy2(models/'AltStore 17_7.mom',wrong_resources/'Current.mom')
    discovery_results={}
    for package,layout in [('FMWK','resources'),('APPL','flat')]:
        framework=output/(package+'-'+layout)/'SchemaFixture.framework';framework.mkdir(parents=True)
        resource=framework/'Resources' if layout=='resources' else framework;resource.mkdir(exist_ok=True)
        shutil.copytree(models,resource/'AltStore.momd')
        (resource/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.example.schema-qualification.'+package,'CFBundleName':'SchemaFixture','CFBundleExecutable':'SchemaFixture','CFBundlePackageType':package,'CFBundleVersion':'1'}))
        executable=framework/'SchemaFixture'
        run(['xcrun','swiftc','-emit-library',ROOT/'anchor.swift','-o',executable],output/(package+'-anchor-compile.log'),timeout=180)
        text=run([discovery,framework,executable,output/'discovery-original.sqlite',host,output/'discovery-unknown.sqlite',wrong,'owned'],output/(package+'-discovery.log'))
        result=json.loads(text);negatives={}
        for mode,field in [('nil-owner','production_nil_owner_source_model_found'),('wrong-version','production_wrong_version_source_model_found'),('unknown','production_unknown_source_model_found')]:
            negative_text=run([discovery,framework,executable,output/'discovery-original.sqlite',host,output/'discovery-unknown.sqlite',wrong,mode],output/(package+'-'+mode+'-discovery.log'))
            negative=json.loads(negative_text);verify_negative(negative,mode)
            negatives[mode]=negative;result[field]=negative['production_source_model_found']
        result['isolated_negative_processes']=negatives
        verify_discovery(result)
        discovery_results[package+'-'+layout]=result
    cases=[]
    for journal in ('delete','wal'):
        original=output/('original-'+journal);original.mkdir();store=original/'AltStore.sqlite'
        run([binary,'seed-'+journal,models,store],output/(journal+'-seed.log'))
        if journal=='wal':require(pathlib.Path(str(store)+'-wal').stat().st_size>32,'real nonempty committed WAL required')
        before=inventory(original)
        scratch=output/('scratch-'+journal);shutil.copytree(original,scratch)
        destination=output/('migrated-'+journal+'.sqlite')
        run([binary,'migrate',models,scratch/'AltStore.sqlite',destination],output/(journal+'-migrate.log'))
        require(inventory(original)==before,'original store/WAL/SHM changed')
        cases.append({'case':journal+'_actual_schema_migration','status':'PASS','original_inventory':before})
        conflict_before={p.name:sha(p) for p in output.glob('migrated-'+journal+'.sqlite*')}
        run([binary,'migrate',models,scratch/'AltStore.sqlite',destination],output/(journal+'-conflict.log'),expect=1,required_token='SCHEMA_QUALIFICATION_REJECTED SchemaQualification 11')
        require({p.name:sha(p) for p in output.glob('migrated-'+journal+'.sqlite*')}==conflict_before,'conflicting target overwritten')
        require(inventory(original)==before,'original changed after conflict rejection')
        cases.append({'case':journal+'_destination_no_overwrite','status':'PASS'})
    run([binary,'mapping-negative',models,output/'scratch-delete/AltStore.sqlite',output/'unsupported-target.sqlite'],output/'mapping-negative.log')
    cases.append({'case':'unsupported_mapping_or_required_attribute_rejected','status':'PASS'})
    unknown=output/'unknown';unknown.mkdir()
    run([binary,'seed-unknown',models,unknown/'AltStore.sqlite'],output/'unknown-seed.log')
    unknown_before=inventory(unknown)
    run([binary,'migrate',models,unknown/'AltStore.sqlite',output/'unknown-result.sqlite'],output/'unknown-rejection.log',expect=1,required_token='SCHEMA_QUALIFICATION_REJECTED SchemaQualification 12')
    require(not (output/'unknown-result.sqlite').exists(),'unknown model created output')
    require(inventory(unknown)==unknown_before,'unknown model original changed')
    cases.append({'case':'unknown_model_refused','status':'PASS'})
    denied=output/'denied';denied.mkdir();denied.chmod(0o500)
    require(not os.access(denied,os.W_OK),'destination permission precondition absent')
    try:run([binary,'migrate',models,output/'scratch-delete/AltStore.sqlite',denied/'SideStore.sqlite'],output/'permission-rejection.log',expect=1,required_token='SOURCE_COMPATIBLE_MAPPING_SELECTED_MIGRATION_STARTED')
    finally:denied.chmod(0o700)
    require(not list(denied.iterdir()),'denied staging left output')
    cases.append({'case':'unwritable_destination_refused','status':'PASS'})
    require(source_hashes=={name:sha(source/name) for name in source_hashes},'source changed during qualification')
    verify(source.parent,args.acquisition,output/'source-after.json')
    report={'schema':'real-coredata-schema-qualification-v1','run_identity':identity(),'acquired_owners':selected_graph(),'acquisition_sha256':sha(args.acquisition),'source_before_sha256':sha(output/'source-before.json'),'source_after_sha256':sha(output/'source-after.json'),'other_graph_owners_acquired':False,'working_directory_outside_checkouts':True,'output_outside_checkouts':True,'status':'PASS_REAL_MODELS_SQLITE_NOT_PRODUCTION_RELOCATION','source_sha256':source_hashes,'production_mapping_methods_sha256':hashlib.sha256(methods.encode()).hexdigest(),'cases':cases,'framework_discovery':discovery_results,'physical_device_execution':False,'production_relocation_enabled':False,'scope_limits':['Fixture writer exited before original copy; live source ownership is not qualified.','No publication into app-group paths or original store replacement.','No legacy-writer lock exclusion proof.','Generic managed-object fixture classes, not application/keychain/signing behavior.','Framework discovery does not execute LC private guest bootstrap.','Interruption and concurrent promotion are not claimed qualified.']}
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps(report,indent=2,sort_keys=True))
if __name__=='__main__':main()
