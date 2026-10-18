"""Genuine candidate Settings UI driver; awaiting authenticated artifact pins."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,re,shutil,sys,types
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
SOURCE='f46a3e14c0752714144aa076355663c79a54f1a9';TREE='f18b17b69ef3bdd061328c4bf4d14e8b0cc5f330'
def require(value,message):
 if not value:raise ValueError(message)
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def input_hash(name):
 raw=(ROOT/name).read_bytes()
 if name=='.github/workflows/p2-direction-ui.yml':raw=re.sub(rb'APPROVED_UI_INPUTS: [0-9a-f]{64}',b'APPROVED_UI_INPUTS: TO_BE_SEALED',raw)
 return hashlib.sha256(raw).hexdigest()
def sealed(approved):
 require(digest(HERE/'inputs.json')==approved,'UI input seal differs')
 data=json.loads((HERE/'inputs.json').read_text())
 for name,value in data['files'].items():require(input_hash(name)==value,'Changed UI input: '+name)
 return data

def source_checked_execution(proof,write,operation):
 before=proof();write('fresh-source-before.json',before)
 value=operation()
 after=proof();write('fresh-source-after.json',after)
 require(before==after,'Fresh source changed during UI execution')
 return value

def execute(args):
 binding=sealed(args.approved)
 reuse=load('candidate_ui_reuse',ROOT/'ci/p2/host_reuse/run.py')
 obs=load('candidate_ui_observations',HERE/'observations.py')
 artifact=load('candidate_ui_artifact',HERE/'artifact.py')
 helpers=load('candidate_ui_archive_helpers',ROOT/'ci/p2/host_reuse/artifact_contract.py')
 runtime=load('candidate_ui_runtime',HERE/'runtime.py')
 transport=load('candidate_ui_transport',ROOT/'ci/p2/direction_rebuild/run.py')
 reuse.RC=load('candidate_ui_runner_contract',ROOT/'ci/p2/host_reuse/runner_contract.py')
 reuse.FD=load('candidate_ui_failure_diagnostics',ROOT/'ci/p2/host_reuse/failure_diagnostics.py')
 reuse.SC=load('candidate_ui_startup_contract',ROOT/'ci/p2/host_reuse/startup_contract.py')
 reuse.HP.SOURCE=SOURCE;reuse.HP.TREE=TREE
 obs.attach_helpers(reuse.HP);reuse.HP.attachment_map=obs.attachment_map
 artifact.PINS=binding['artifact']
 for p in [args.work,args.evidence]:
  require(not p.exists() and not any(x.is_symlink() for x in (p,*p.parents)),'Fresh unlinked output required')
  p.mkdir(parents=True)
 commands=runtime.Commands(args.evidence/'commands',seconds=18*60)
 def checked(argv,timeout=60):return commands.run(list(argv),timeout)[0].strip()
 reuse.HP.git=lambda root,*argv:checked(['git','-C',root,*argv])
 reuse.AC=types.SimpleNamespace(authenticate_artifact=lambda path,work,evidence:artifact.authenticate(path,work,args.source,helpers,reuse.HP))
 def verify_observations(attachments,identity,builder,expected):
  observation=obs.verify_artifacts(attachments,identity,builder,reuse.HP,reuse.SC)
  return reuse.RC.verify_launch_policy(observation['report'],expected)
 reuse.verify_observations_with_policy=verify_observations
 class Probe(reuse.ReuseProbe):
  def execute(self):
   proof=lambda:reuse.HP.checkout_proof(args.source,SOURCE,TREE,'https://github.com/NRG-Wardog/LiveContainer.git',dependencies=True)
   write=lambda name,value:reuse.CAP.write(args.evidence/name,value)
   return source_checked_execution(proof,write,super().execute)
  def verify_inputs(self):
   sealed(args.approved);self.binding=binding
   self.report.update(scope='Genuine four-language Settings UI and persisted preference observation',productBuildRunId=artifact.PINS['run'],productBuilderCommit=artifact.PINS['builder'])
  def guard_inputs(self):
   sealed(args.approved)
   require(digest(args.source/'Resources/Localizable.xcstrings')==artifact.PINS['catalogSHA256'],'Fresh source catalog changed')
   if self.runner_snapshot is not None:require(reuse.CAP.snapshot(self.work/'runner')==self.runner_snapshot,'Generated runner changed')
  def command(self,argv,label,timeout=300):
   require(self.allowed(list(map(str,argv))),'Command outside inherited exact allowlist')
   requested=690 if label=='native-settings-test' else timeout
   out,record=commands.run(list(argv),requested,checked=False)
   self.number=commands.number
   return {'stdout':out,'stderr':'','returncode':record.get('returncode'),'error':record.get('primaryError'),'quiescenceError':record.get('quiescenceError')}
  def harvest_results(self):
   if commands.all_settled:super().harvest_results()
  def native_command(self):
   return ['xcodebuild','test-without-building','-xctestrun',str(self.generated),'-destination','id='+self.device,'-resultBundlePath',str(self.native_result),'-parallel-testing-enabled','NO','-maximum-concurrent-test-simulator-destinations','1','-test-timeouts-enabled','YES','-default-test-execution-time-allowance','300','-maximum-test-execution-time-allowance','300','-only-testing:HostSettingsProbeUITests/HostSettingsProbeTests/testUnchangedHostSettingsLanguageRoundTrip']
  def prepare_runner(self,source):
   project=super().prepare_runner(source)
   template=(HERE/'HostSettingsProbeTests.swift.in').read_text()
   catalog=json.loads((source/'Resources/Localizable.xcstrings').read_text())['strings']
   replacements={'__HOST_BUNDLE_ID__':self.identity['bundleIdentifier'],'__HOST_EXECUTABLE_SHA256__':self.identity['executableSHA256'],'__SOURCE_COMMIT__':SOURCE,'__SOURCE_TREE__':TREE,'__BUILDER_COMMIT__':self.report['probeBuilderCommit']}
   for code,name in [('en','EN'),('he','HE'),('zh-Hans','HANS'),('zh-Hant','HANT')]:
    for token,key in [('SETTINGS','Settings'),('COLLAPSED','Start Collapsed')]:replacements['__'+token+'_'+name+'__']=catalog[key]['localizations'][code]['stringUnit']['value']
   for name,key in [('TITLE',reuse.SC.NOTICE_TITLE),('BODY',reuse.SC.NOTICE_BODY),('LATER','Later'),('ALLOW','Allow Notifications')]:replacements['__NOTICE_'+name+'__']=key
   for token,value in replacements.items():template=template.replace(token,reuse.HP.swift_literal(value))
   literal='['+', '.join(reuse.HP.swift_literal(k)+': '+reuse.HP.swift_literal(v)for k,v in sorted(self.generated_environment.items()))+']'
   template=template.replace('__GENERATED_TARGET_ENVIRONMENT__',literal)
   require(not re.search(r'__[_A-Z0-9]+__',template),'Unresolved Swift token')
   (self.work/'runner/Sources/HostSettingsProbeTests.swift').write_text(template)
   self.runner_snapshot=reuse.CAP.snapshot(self.work/'runner')
   return project
 probe=Probe(args.artifact,args.work,args.evidence,args.approved)
 probe.report.update(scope='Genuine four-language Settings UI and persisted preference observation',productBuildRunId=artifact.PINS['run'],productBuilderCommit=artifact.PINS['builder'])
 try:probe.execute()
 except Exception as error:probe.report['errors'].append(str(error))
 finally:
  commands.begin_finalization();probe.finalizing=True
  if commands.all_settled:
   probe.harvest_results()
   if probe.device:
    try:
     checked(['xcrun','simctl','shutdown',probe.device],60)
     devices=json.loads(checked(['xcrun','simctl','list','devices','--json']))
     matches=[d for rows in devices['devices'].values() for d in rows if d.get('udid')==probe.device]
     require(len(matches)==1 and matches[0].get('state')=='Shutdown','Owned simulator shutdown not confirmed')
    except Exception as error:probe.report['errors'].append('Simulator shutdown: '+str(error))
  if probe.report['errors'] or probe.report['retentionErrors'] or probe.report['diagnosticCaptureErrors']:probe.report['status']='BLOCKED'
  reuse.CAP.write(args.evidence/'ui-result.json',probe.report)
  if commands.all_settled and probe.attachments.is_dir():shutil.copytree(probe.attachments,args.evidence/'attachments')
  retained=transport.publish_evidence(args.evidence,args.publish,commands.all_settled and not any('Simulator shutdown:' in x for x in probe.report['errors']),commands.deadline,context=probe.report,outputs=commands.failed_output_receipts)
  if not retained:probe.report['status']='BLOCKED'
 require(probe.report['status']=='BOUNDED_SETTINGS_ROUTE_OBSERVED','Genuine candidate UI observations incomplete')

if __name__=='__main__':
 parser=argparse.ArgumentParser()
 for name in ['artifact','source','work','evidence','publish']:parser.add_argument('--'+name,type=Path,required=True)
 parser.add_argument('--approved',required=True)
 execute(parser.parse_args())
