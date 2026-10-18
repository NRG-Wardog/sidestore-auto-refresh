"""Genuine candidate Release simulator compilation, not host UI acceptance."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, platform, re, shutil, sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SOURCE='f46a3e14c0752714144aa076355663c79a54f1a9'
TREE='f18b17b69ef3bdd061328c4bf4d14e8b0cc5f330'
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
    if name=='.github/workflows/p2-direction-host.yml':
        raw=re.sub(rb'APPROVED_HOST_INPUTS: [0-9a-f]{64}',b'APPROVED_HOST_INPUTS: TO_BE_SEALED',raw)
    return hashlib.sha256(raw).hexdigest()
def verify_inputs(expected):
    require(sha(HERE/'inputs.json')==expected,'Input seal mismatch')
    data=json.loads((HERE/'inputs.json').read_text())
    require((data['sourceCommit'],data['sourceTree'])==(SOURCE,TREE),'Candidate identity mismatch')
    for name,digest in data['files'].items(): require(input_hash(name)==digest,'Changed input: '+name)
    return data
def build_command(host,derived):
    return ['xcodebuild','-project',str(host/'LiveContainer.xcodeproj'),'-scheme','LiveContainer','-configuration','Release','-sdk','iphonesimulator','-destination','generic/platform=iOS Simulator','-derivedDataPath',str(derived),'-disableAutomaticPackageResolution','-onlyUsePackageVersionsFromResolvedFile']
def execute(host,work,evidence,publish,approved):
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
    report={'status':'failed','scope':'Genuine Release simulator compile only; no host UI, device authentication or IPA acceptance','sourceCommit':SOURCE,'sourceTree':TREE,'errors':[]}
    try:
        require(not [key for key in ['XCODE_XCCONFIG_FILE','SDKROOT','TOOLCHAINS'] if os.environ.get(key)],'Ambient build overrides forbidden')
        require(cmd(['git','-C',ROOT,'rev-parse','HEAD'])==os.environ['GITHUB_SHA'],'Builder checkout mismatch')
        require(not cmd(['git','-C',ROOT,'status','--porcelain','--untracked-files=all']),'Dirty builder')
        before=prior.checkout_proof(host,SOURCE,TREE,ORIGIN,dependencies=True)
        prior.verify_declared_dependencies(host)
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
        derived=work/'DerivedData';base=build_command(host,derived)
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
    for n in ['host','work','evidence','publish']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--approved',required=True);a=p.parse_args()
    execute(a.host.resolve(),a.work.resolve(),a.evidence.resolve(),a.publish.resolve(),a.approved)
