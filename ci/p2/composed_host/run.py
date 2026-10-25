"""Composed P2 host compilation and P0 initializer consumer verification."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, platform, re, shutil, sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SOURCE='e22de6df412a0977f7430b2218960ca3ee9585d5'
TREE='4b973e2bbf482953e298c41c7a7227cb60b30718'
ORIGIN='https://github.com/NRG-Wardog/LiveContainer.git'
def require(value,message):
    if not value: raise ValueError(message)
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
def input_hash(name):
    raw=(ROOT/name).read_bytes()
    if name=='.github/workflows/p2-composed-host.yml':
        raw=re.sub(rb'APPROVED_HOST_INPUTS: [0-9a-f]{64}',b'APPROVED_HOST_INPUTS: TO_BE_SEALED',raw)
    return hashlib.sha256(raw).hexdigest()
def verify_inputs(expected):
    require(sha(HERE/'inputs.json')==expected,'Input seal mismatch')
    data=json.loads((HERE/'inputs.json').read_text())
    require((data['sourceCommit'],data['sourceTree'])==(SOURCE,TREE),'Candidate identity mismatch')
    for name,digest in data['files'].items(): require(input_hash(name)==digest,'Changed input: '+name)
    return data
def build_command(host,derived,udid):
    return ['xcodebuild','-project',str(host/'LiveContainer.xcodeproj'),'-scheme','LiveContainer','-configuration','Release','-sdk','iphonesimulator','-destination','platform=iOS Simulator,id='+udid,'-derivedDataPath',str(derived),'-disableAutomaticPackageResolution','-onlyUsePackageVersionsFromResolvedFile']
PEER={'commit':'59fa3b5cd8c9ea4a88716cbaaa08bacd9f22e218','tree':'c58017caf7e392e238f47a5e0fa9511fa6f74ca0','origin':'https://github.com/NRG-Wardog/SideStore.git','children':{'Dependencies/SideSign':'c55f53ee6d3a65ab9b49d1528eb6c668fdde6c0e','Dependencies/minimuxer':'efbcab05d7d636aa37c6bf6c7f364d122c5610f6'}}
PRODUCER={'commit':'161435bcc0344e1a9853669455c1ca8d9dda2bf1','tree':'0df50020ddd774a5b7ab941944501f1ecd591a4a','origin':'https://github.com/NRG-Wardog/AnisetteKit.git','children':{}}
def peer_link_destination(prior,root,name,entries,fallback):
    special='AltStore/Resources/SideBackup.ipa'
    if name!=special:return fallback(root,name,entries)
    row=entries.get(name,{})
    require(row.get('mode')=='120000' and row.get('type')=='blob' and row.get('gitObject')=='54a080c4e8a5029ad8d22842c16acde4d6d01b43','Generated resource link identity differs')
    target,raw=prior.source_link_target(root,name,row)
    require(target=='../../build/SideBackup.ipa' and raw==b'../../build/SideBackup.ipa','Generated resource target differs')
    for relative in ['AltStore','AltStore/Resources','build']:
        path=root/relative
        require(not path.is_symlink(),'Generated resource ancestor linked')
    require(not any(n=='build' or n.startswith('build/') for n in entries),'Generated resource unexpectedly tracked')
    require(not os.path.lexists(root/'build/SideBackup.ipa'),'Generated resource unexpectedly present')
    # This exact committed generated IPA link is not read by the consumer tests.
    # Preserve its bytes and absence; never follow, create or substitute it.
    return {'classification':'committed-dangling-generated-resource','target':'build/SideBackup.ipa','consumed':False}

def owner_proof(prior,root,pin):
    original=prior.DEPENDENCIES
    resolver=prior.source_link_destination
    try:
        prior.DEPENDENCIES={name:{'commit':commit}for name,commit in pin['children'].items()}
        if pin==PEER:
            prior.source_link_destination=lambda source,name,entries:peer_link_destination(prior,source,name,entries,resolver)
        return prior.checkout_proof(root,pin['commit'],pin['tree'],pin['origin'],dependencies=True)
    finally:
        prior.DEPENDENCIES=original
        prior.source_link_destination=resolver

def execute(host,peer,producer,work,evidence,publish,approved):
    inputs=verify_inputs(approved)
    require(platform.system()=='Darwin','Native macOS required')
    evidence.mkdir(parents=True,exist_ok=False);work.mkdir(parents=True,exist_ok=False)
    runtime=load('direction_host_runtime',HERE/'runtime.py')
    transport=load('direction_host_transport',ROOT/'ci/p2/direction_rebuild/run.py')
    prior=load('direction_host_contract',ROOT/'ci/p2/host_probe/run.py')
    commands=runtime.Commands(evidence/'commands',seconds=32*60)
    cmd=lambda argv,timeout=60:commands.run(argv,timeout)[0].strip()
    # Keep reused read-only Git provenance checks under the same command owner.
    prior.git=lambda root,*args:cmd(['git','-C',root,*args])
    report={'status':'failed','scope':'Composed Release simulator compile and five native initializer consumer tests; no new UI, device authentication or IPA acceptance','sourceCommit':SOURCE,'sourceTree':TREE,'errors':[]}
    try:
        require(not [key for key in ['XCODE_XCCONFIG_FILE','SDKROOT','TOOLCHAINS'] if os.environ.get(key)],'Ambient build overrides forbidden')
        require(cmd(['git','-C',ROOT,'rev-parse','HEAD'])==os.environ['GITHUB_SHA'],'Builder checkout mismatch')
        require(not cmd(['git','-C',ROOT,'status','--porcelain','--untracked-files=all']),'Dirty builder')
        before=prior.checkout_proof(host,SOURCE,TREE,ORIGIN,dependencies=True)
        prior.verify_declared_dependencies(host)
        peers_before={'SideStore':owner_proof(prior,peer,PEER),'AnisetteKit':owner_proof(prior,producer,PRODUCER)}
        write(evidence/'consumer-sources-before.json',peers_before)
        write(evidence/'source-before.json',before);write(evidence/'inputs.json',inputs)
        require(cmd(['xcodebuild','-version']).splitlines()[0]=='Xcode 26.4.1','Xcode drift')
        for name,row in prior.DEPENDENCIES.items():
            directory=host/name
            require(directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()),'Dependency must start empty')
            cmd(['git','-C',directory,'init']);cmd(['git','-C',directory,'remote','add','origin',row['fetch_url']])
            cmd(['git','-C',directory,'fetch','--depth=1','--no-tags','--no-recurse-submodules','origin',row['commit']],240)
            cmd(['git','-C',directory,'checkout','--detach',row['commit']])
            write(evidence/(name+'-provenance.json'),prior.checkout_proof(directory,row['commit'],row['tree'],row['fetch_url']))
        require(prior.checkout_proof(host,SOURCE,TREE,ORIGIN,dependencies=True)==before,'Source changed after dependency setup')
        cmd([sys.executable,'-B',HERE/'consumer_gate.py','--host',host,'--peer',peer,'--producer',producer,'--output',evidence/'consumer-tests.json'],240)
        consumer=json.loads((evidence/'consumer-tests.json').read_text())
        require(consumer=={'testsRun':5,'failures':0,'errors':0,'skipped':0,'successful':True},'Native consumer test receipt differs')
        report['nativeConsumerTests']=consumer
        runtimes=json.loads(cmd(['xcrun','simctl','list','runtimes','--json']))
        types=json.loads(cmd(['xcrun','simctl','list','devicetypes','--json']))
        runtime_id='com.apple.CoreSimulator.SimRuntime.iOS-26-5'
        device_type='com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro'
        matches=[r for r in runtimes['runtimes'] if r.get('identifier')==runtime_id and r.get('isAvailable') is True]
        require(len(matches)==1 and matches[0].get('buildversion')=='23F77','Pinned native runtime unavailable')
        require(sum(t.get('identifier')==device_type for t in types['devicetypes'])==1,'Pinned device type unavailable')
        udid=cmd(['xcrun','simctl','create','P2-Compile-'+os.environ['GITHUB_RUN_ID'],device_type,runtime_id])
        require(re.fullmatch(r'[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}',udid),'Invalid created simulator identity')
        write(evidence/'created-simulator.json',{'udid':udid,'runtime':matches[0],'deviceType':device_type,'bootRequested':False})
        derived=work/'DerivedData';base=build_command(host,derived,udid)
        settings_raw=cmd(base+['-showBuildSettings','-json'],180)
        (evidence/'build-settings.json').write_text(settings_raw+'\n')
        settings,app=prior.host_build_settings(settings_raw,host,derived)
        cmd(base+['-resultBundlePath',work/'build.xcresult','build'],1500)
        require(commands.all_settled,'Build process not settled')
        write(evidence/'product-identity.json',prior.verify_product(app,settings))
        report['machOPlatforms']=prior.simulator_platform(cmd(['xcrun','vtool','-show-build',app/'LiveContainer']))
        resources=prior.compiled_resource_contracts(app,host)
        resources['scope']='Genuine candidate simulator product compiled resources; no runtime acceptance'
        write(evidence/'compiled-resources.json',resources)
        require(all(resources[key]['status']=='passed' for key in ['localization','exactPluralValues','permissions']),'Compiled resource failure')
        after=prior.checkout_proof(host,SOURCE,TREE,ORIGIN,dependencies=True)
        write(evidence/'source-after.json',after);require(before==after,'Source changed during build')
        peers_after={'SideStore':owner_proof(prior,peer,PEER),'AnisetteKit':owner_proof(prior,producer,PRODUCER)}
        write(evidence/'consumer-sources-after.json',peers_after);require(peers_before==peers_after,'Consumer source changed during execution')
        # Archive only settled build outputs with before/after inventory and readback.
        report['appArchive']=transport.archive(app,evidence/'LiveContainer.app.zip',commands.deadline)
        report['status']='compile-passed'
    except Exception as error: report['errors'].append(str(error))
    finally:
        commands.begin_finalization()
        write(evidence/'result.json',report)
        context={**report,'failedCommands':commands.failed_receipts}
        if not transport.publish_evidence(evidence,publish,commands.all_settled,commands.deadline,reason='Genuine build writer not settled',context=context,outputs=commands.failed_output_receipts):report['status']='failed'
    require(report['status']=='compile-passed','Genuine product compilation gate failed')
if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ['host','peer','producer','work','evidence','publish']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--approved',required=True);a=p.parse_args()
    execute(a.host.resolve(),a.peer.resolve(),a.producer.resolve(),a.work.resolve(),a.evidence.resolve(),a.publish.resolve(),a.approved)
