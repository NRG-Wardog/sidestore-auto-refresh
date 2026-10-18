#!/usr/bin/env python3
"""Two fixed-arm Form-direction observations. Collection never certifies pixels."""
from __future__ import annotations
import argparse, ast, hashlib, importlib.util, json, os, platform, plistlib, re, shutil, stat, sys, time, uuid, zipfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = 'eb10cedb772bc0496d67edd7a269679e24826b9f'
TREE = 'd6960a99a2f022a333c364fa4d28e264568a28ca'
APPROVED_INPUTS = 'f5e007f8e1534bad74b468cf4dc9cfcb2a356b465e3827b4b1b05e6d4f2b632f'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-26-5'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro'
METHODS = {'control': 'testControlRoundTrip', 'candidate': 'testCandidateRoundTrip'}
ANCHORS = ('private final class V3NativeAlertPresentation', 'extension UIViewController', 'enum LCNativeErrorSite', 'enum LCNativeErrorPresentation', 'public struct TextFieldAlertModifier')
LIMITS = ['Synthetic component composition, not complete LCSettingsView or actual host', 'One fixed-arm EN/HE/EN cycle per process; no repeated-state or cold-Hebrew gate yet', 'Production section boundary is measured; internal production header/picker environments are not introspected', 'Independent raster review and reproducing control required; AX correctness does not certify glyphs', 'No maintained product change, full-host build, IPA or device admission', 'Known genuine-host C11-L15 service failure remains unresolved', 'Existing OS-owned payload, all-four-language and composed-source gates remain open']

def require(value, message):
    if not value: raise ValueError(message)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n')
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); sys.modules[name] = value; spec.loader.exec_module(value)
    return value

def normalized(raw, path):
    if path == 'ci/p2/form_direction/run.py':
        raw, count = re.subn(rb"(?m)^APPROVED_INPUTS = '(?:[0-9a-f]{64}|TO_BE_SEALED)'", b"APPROVED_INPUTS = 'TO_BE_SEALED'", raw)
    elif path == '.github/workflows/p2-form-direction.yml':
        raw, count = re.subn(rb'APPROVED_FORM_INPUTS_SHA256: (?:[0-9a-f]{64}|TO_BE_SEALED)', b'APPROVED_FORM_INPUTS_SHA256: TO_BE_SEALED', raw)
    else: return raw
    require(count == 1, 'Invalid seal occurrence: ' + path)
    return raw

def verify_inputs(host, approved):
    require(approved == APPROVED_INPUTS and sha(HERE/'inputs.json') == approved, 'Unapproved Form inputs')
    data = json.loads((HERE/'inputs.json').read_text())
    require(data['sourceCommit'] == SOURCE and data['sourceTree'] == TREE and data['methods'] == METHODS, 'Identity drift')
    for base, key in [(ROOT, 'builderFiles'), (host, 'sourceFiles')]:
        for name, expected in data[key].items():
            path = base/name
            require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), 'Linked/missing input: ' + name)
            raw = path.read_bytes()
            if key == 'builderFiles': raw = normalized(raw, name)
            require(hashlib.sha256(raw).hexdigest() == expected, 'Changed input: ' + name)
    return data

def verify_hypothesis(host):
    manifest = json.loads((HERE/'hypothesis/manifest.json').read_text())
    path = 'LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift'
    original = (host/path).read_text()
    require(sha(host/path) == manifest['paths'][path]['before']['sha256'], 'Hypothesis baseline drift')
    patch = (HERE/'hypothesis/experimental-form-boundary.patch').read_bytes()
    require(hashlib.sha256(patch).hexdigest() == manifest['patch']['sha256'], 'Hypothesis patch drift')
    # Apply the reviewed five additions without any patch process or fuzzy match.
    replacements = [
        ('    @Environment(\\.locale) private var selectedDisplayLocale\n', '    @Environment(\\.locale) private var selectedDisplayLocale\n    @Environment(\\.layoutDirection) private var selectedDisplayDirection\n'),
        ('            Form {\n', '            Form {\n                Group {\n'),
    ]
    for old, new in replacements:
        require(original.count(old) == 1, 'Hypothesis anchor drift'); original = original.replace(old, new, 1)
    # Exact closing boundary is bound by the patch and candidate digest below.
    old = '            }\n            // V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1: a background is laid out'
    new = '                }\n                .environment(\\.layoutDirection, selectedDisplayDirection)\n            }\n            .environment(\\.layoutDirection, .leftToRight)\n            // V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1: a background is laid out'
    require(original.count(old) == 1, 'Hypothesis closing anchor drift'); original = original.replace(old, new, 1)
    require(hashlib.sha256(original.encode()).hexdigest() == manifest['paths'][path]['after']['sha256'], 'Candidate bytes differ')
    return original

def prepare(host, output, builder):
    sys.path.insert(0, str(ROOT/'scripts'))
    from run_p0_signin_rendering import declaration, member
    sources = output/'Sources'; sources.mkdir(parents=True)
    paths = ['LiveContainerSwiftUI/Utilities/V3Localization.swift', 'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift',
             'LiveContainerSwiftUI/Utilities/ViewExtensions.swift', 'LiveContainerSwiftUI/App/AppDelegate.swift',
             'LiveContainerSwiftUI/Views/V3UnifiedShell.swift', 'LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift', 'Resources/Localizable.xcstrings']
    extension = (host/paths[2]).read_text()
    fragments = {a: declaration(extension, a) for a in ANCHORS}
    fragments['class SceneDelegate'] = declaration((host/paths[3]).read_text(), 'class SceneDelegate')
    fragments['View.textFieldAlert'] = member(extension, 'public func textFieldAlert')
    require(fragments[ANCHORS[0]].startswith('@MainActor\n'), 'Lost production isolation annotation')
    template = (HERE/'App.swift.in').read_text()
    tests = (HERE/'FormDirectionTests.swift.in').read_text()
    require(re.findall(r'^    func (test\w+)\(', tests, re.M) == list(METHODS.values()), 'Unexpected test methods')
    pieces = ['import UIKit\nimport SwiftUI\nimport ObjectiveC\n', (host/paths[0]).read_text(), (host/paths[1]).read_text(),
              *[fragments[a] for a in ANCHORS], fragments['class SceneDelegate'], 'extension View {\n'+fragments['View.textFieldAlert']+'}\n', template]
    (sources/'App.swift').write_text('\n\n'.join(pieces))
    (sources/'SettingsInteractionTests.swift').write_text(tests)
    catalog = json.loads((host/paths[-1]).read_text())
    values = {lang: catalog['strings']['Language']['localizations'][lang]['stringUnit']['value'] for lang in ['en', 'he']}
    def literal(s): return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: '\\u{'+m[1]+'}', json.dumps(s, ensure_ascii=False))
    (sources/'ExpectedStrings.swift').write_text('import Foundation\nenum ExpectedStrings {\n static func text(_ key: String, language: String) -> String {\n precondition(key == "Language")\n switch language {\n'+''.join(' case '+literal(k)+': return '+literal(v)+'\n' for k,v in values.items())+' default: preconditionFailure("Unbound expected language")\n }\n }\n}\n')
    shutil.copyfile(host/paths[-1], sources/'Localizable.xcstrings')
    project = output/'P2Settings.xcodeproj'; project.mkdir()
    baseline = HERE.parent/'settings/project'
    shutil.copyfile(baseline/'project.pbxproj', project/'project.pbxproj')
    info = plistlib.loads((baseline/'Info.plist').read_bytes())
    info['LCBuilderCommit'] = builder
    config = info['UIApplicationSceneManifest']['UISceneConfigurations']['UIWindowSceneSessionRoleApplication'][0]
    require(config['UISceneDelegateClassName'] == '$(PRODUCT_MODULE_NAME).SettingsFixtureWindowDelegate', 'Scene project drift')
    config['UISceneDelegateClassName'] = '$(PRODUCT_MODULE_NAME).FormDirectionWindowDelegate'
    config['UISceneConfigurationName'] = 'P2FormDirectionScene'
    (project/'Info.plist').write_bytes(plistlib.dumps(info, sort_keys=False))
    schemes = project/'xcshareddata/xcschemes'; schemes.mkdir(parents=True)
    shutil.copyfile(baseline/'P2Settings.xcscheme', schemes/'P2Settings.xcscheme')
    for name in paths:
        destination = output/'original-source'/name; destination.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(host/name, destination)
    candidate = verify_hypothesis(host)
    (output/'experimental-LCSettingsView.swift').write_text(candidate)
    identity = {'sourceCommit': SOURCE, 'sourceTree': TREE, 'builderCommit': builder,
                'sourceFiles': {p: sha(host/p) for p in paths}, 'extractedDeclarations': {k: hashlib.sha256(v.encode()).hexdigest() for k,v in fragments.items()},
                'generatedFiles': {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob('*')) if p.is_file()},
                'candidateCompiledAsWholeView': False, 'scope': LIMITS}
    write(output/'source-identity.json', identity)
    return project

def verify_result(summary, tests, method):
    for key, expected in [('totalTestCount',1),('passedTests',1),('failedTests',0),('skippedTests',0)]:
        require(type(summary.get(key)) is int and summary[key] == expected, 'Wrong native count: '+key)
    require(summary.get('result') == 'Passed', 'Native result not passed')
    rows=[]
    def walk(node):
        if isinstance(node, dict):
            if node.get('nodeType') == 'Test Case': rows.append(node)
            for v in node.values(): walk(v)
        elif isinstance(node,list):
            for v in node: walk(v)
    walk(tests)
    require(len(rows)==1 and rows[0].get('result')=='Passed' and rows[0].get('name')==method+'()', 'Native method drift')
    require(re.fullmatch(r'(?:P2SettingsUITests/)?FormDirectionTests/'+method+r'\(\)', rows[0].get('nodeIdentifier','')) is not None, 'Foreign native method')
    return {'method':method,'passed':1,'skipped':0}

def verify_generated(directory, expected, receipt):
    actual={}; errors=[]
    for name,digest in expected.items():
        path=directory/name
        try:
            require(path.is_file() and not path.is_symlink(), 'Generated input missing or linked')
            actual[name]=sha(path)
        except Exception as error:
            actual[name]=None;errors.append(name+': '+str(error))
    changed={name:{'before':expected[name],'after':actual[name]} for name in expected if expected[name]!=actual[name]}
    write(receipt,{'expected':expected,'actual':actual,'changed':changed,'errors':errors,'stable':not changed and not errors})
    require(not changed and not errors,'Generated fixture input bytes changed; retained exact difference')
    return {'stable':True,'files':len(expected)}

MAX_ARCHIVE_ENTRIES = 25000
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_ARCHIVE_FILE_BYTES = 1024**3

def before_deadline(deadline):
    require(deadline is None or time.monotonic() < deadline, 'Retention deadline exhausted')
def file_digest(path, deadline=None):
    digest=hashlib.sha256(); total=0
    with path.open('rb') as stream:
        while True:
            before_deadline(deadline); chunk=stream.read(1024*1024)
            if not chunk: break
            total+=len(chunk);require(total<=MAX_ARCHIVE_FILE_BYTES,'Retained file exceeds byte bound')
            digest.update(chunk)
    before_deadline(deadline)
    return digest.hexdigest(),total

def inventory(root, deadline=None):
    rows={}; total=0
    for p in root.rglob('*'):
        before_deadline(deadline)
        require(len(rows)<MAX_ARCHIVE_ENTRIES,'Retained entry bound exceeded')
        mode=p.lstat().st_mode; name=str(p.relative_to(root))
        if stat.S_ISLNK(mode):
            target=os.readlink(p); require(not os.path.isabs(target) and p.resolve().is_relative_to(root.resolve()) and p.resolve().exists(), 'Escaping/broken archive link')
            rows[name]={'type':'symlink','mode':stat.S_IMODE(mode),'target':target}
        elif stat.S_ISDIR(mode): rows[name]={'type':'directory','mode':stat.S_IMODE(mode)}
        elif stat.S_ISREG(mode):
            require(p.stat().st_size<=MAX_ARCHIVE_FILE_BYTES,'Retained file exceeds byte bound')
            digest,size=file_digest(p,deadline);total+=size
            require(total<=MAX_ARCHIVE_BYTES,'Retained total byte bound exceeded')
            rows[name]={'type':'file','mode':stat.S_IMODE(mode),'bytes':size,'sha256':digest}
        else: raise ValueError('Unsupported retained file type')
    return dict(sorted(rows.items()))

def archive(root, target, deadline=None):
    before=inventory(root,deadline)
    # Save the operand even if archive creation/readback later fails.
    write(target.with_suffix('.before-inventory.json'),before)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for name,row in before.items():
            before_deadline(deadline)
            p=root/name; info=zipfile.ZipInfo(name+('/' if row['type']=='directory' else ''))
            info.create_system=3
            kind={'file':stat.S_IFREG,'directory':stat.S_IFDIR,'symlink':stat.S_IFLNK}[row['type']]
            info.external_attr=(kind|row['mode'])<<16;info.compress_type=zipfile.ZIP_DEFLATED
            if row['type']=='file':
                size=0
                with p.open('rb') as src,z.open(info,'w',force_zip64=True) as dst:
                    while True:
                        before_deadline(deadline);chunk=src.read(1024*1024)
                        if not chunk:break
                        size+=len(chunk);require(size<=row['bytes'],'Retained file grew while copying');dst.write(chunk)
                require(size==row['bytes'],'Retained file shrank while copying')
            else:z.writestr(info,b'' if row['type']=='directory' else row['target'].encode())
    after=inventory(root,deadline);write(target.with_suffix('.after-inventory.json'),after)
    require(before==after,'Retained source changed during archive')
    with zipfile.ZipFile(target) as z:
        require(len(z.infolist())==len(before),'Archive entry count failure')
        for info in z.infolist():
            before_deadline(deadline);name=info.filename.rstrip('/'); row=before[name]
            kind={'file':stat.S_IFREG,'directory':stat.S_IFDIR,'symlink':stat.S_IFLNK}[row['type']]
            require(info.external_attr>>16==kind|row['mode'],'Archive full type/mode mismatch')
            if row['type']=='file':
                digest=hashlib.sha256();size=0
                with z.open(info) as stream:
                    while True:
                        before_deadline(deadline);chunk=stream.read(1024*1024)
                        if not chunk:break
                        size+=len(chunk);require(size<=row['bytes'],'Archive readback size overflow');digest.update(chunk)
                require(size==row['bytes'] and digest.hexdigest()==row['sha256'],'Archive content mismatch')
            elif row['type']=='symlink':require(z.read(info)==row['target'].encode(),'Archive link mismatch')
            else:require(z.read(info)==b'','Archive directory bytes')
    digest,size=file_digest(target,deadline)
    return {'sha256':digest,'bytes':size,'entries':len(before),'stable':True,'limits':{'entries':MAX_ARCHIVE_ENTRIES,'bytes':MAX_ARCHIVE_BYTES,'perFileBytes':MAX_ARCHIVE_FILE_BYTES}}

def publish_failure(publish, receipt, outputs):
    """Fallback bytes come only from bounded parent-owned memory, never evidence files."""
    write(publish/'failure.json', receipt)  # Preserve the primary result first.
    try:
        require(isinstance(outputs,list) and len(outputs)<=8,'Failed output receipt count exceeds bound')
        if outputs:
            payload={'schema':'p2-failed-command-output-v1',
                     'source':'immutable-parent-owned-Popen-capture',
                     'completeProcessOutputProven':False,
                     'selection':'first failed command plus latest seven failures',
                     'commands':outputs}
            encoded=json.dumps(payload,indent=2,sort_keys=True,allow_nan=False)+'\n'
            require(len(encoded.encode('utf-8'))<=128*1024,'Failed output receipt exceeds byte bound')
            (publish/'failure-command-output.json').write_text(encoded)
    except Exception as error:
        path=publish/'failure-command-output.json'
        if path.exists():path.unlink()
        receipt['outputRetentionError']=type(error).__name__+': '+str(error)[:256]
        write(publish/'failure.json',receipt)

def publish_evidence(evidence, publish, settled, deadline=None, reason=None, context=None, outputs=None):
    """Only verified quiescent evidence reaches the workflow upload directory."""
    publish.mkdir(parents=True, exist_ok=False)
    context = context or {}
    require(len(json.dumps(context, allow_nan=False).encode()) <= 64 * 1024, 'Failure context exceeds bound')
    if not settled:
        publish_failure(publish, {'status':'failed','fullEvidenceTransported':False,
              'ownedWritersSettled':False,'reason':reason or 'Owned writer or simulator quiescence unresolved',
              'primaryOutcome':context}, outputs or [])
        return False
    staging=publish.parent/(publish.name+'-staging')
    try:
        staging.mkdir(parents=True,exist_ok=False)
        before_deadline(deadline)
        indexed=inventory(evidence,deadline)
        write(evidence/'evidence-index.json',{name:row['sha256'] for name,row in indexed.items() if row['type']=='file' and name!='evidence-index.json'})
        receipt=archive(evidence,staging/'evidence.zip',deadline)
        # Only a fully verified, closed archive crosses the transport boundary.
        os.replace(staging/'evidence.zip',publish/'evidence.zip')
        write(publish/'transport.json',{'status':'stable-evidence-retained','ownedWritersSettled':True,'archive':receipt})
        return True
    except Exception as error:
        for name in ['evidence.zip','transport.json']:
            path=publish/name
            if path.exists():path.unlink()
        publish_failure(publish,{'status':'failed','fullEvidenceTransported':False,
              'ownedWritersSettled':True,'reason':'Evidence retention: '+type(error).__name__+': '+str(error),
              'primaryOutcome':context}, outputs or [])
        return False

class Probe:
    def __init__(self, host, work, evidence, publish, approved):
        self.host,self.work,self.evidence,self.publish,self.approved=host,work,evidence,publish,approved
        self.device=None; self.report={'status':'failed','visualAcceptance':False,'visualVerdict':'pending-independent-review','errors':[], 'arms':{},'scope':LIMITS}
        self.commands=None
        self.generated_expected=None
    def run(self):
        self.evidence.mkdir(parents=True,exist_ok=False)
        try: inputs=verify_inputs(self.host,self.approved)
        except Exception as error:
            self.report['errors'].append('Input gate: '+str(error));write(self.evidence/'result.json',self.report)
            publish_evidence(self.evidence,self.publish,True,time.monotonic()+60)
            return self.report
        runtime=load('p2_form_runtime',HERE/'runtime.py')
        verifier=load('p2_form_verifier',HERE/'verify.py')
        self.work.mkdir(parents=True,exist_ok=False)
        self.commands=runtime.Commands(self.evidence/'commands')
        cmd=lambda argv,timeout=60:self.commands.run(argv,timeout)[0].strip()
        try:
            write(self.evidence/'inputs.json',inputs)
            for name in inputs['builderFiles']:
                dst=self.evidence/'builder-inputs'/name; dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dst)
            # upload-artifact omits dot-directories; retain the indexed workflow visibly.
            shutil.copyfile(ROOT/'.github/workflows/p2-form-direction.yml',self.evidence/'workflow.yml')
            hidden=self.evidence/'builder-inputs/.github'; shutil.rmtree(hidden)
            require(platform.system()=='Darwin','Native Darwin execution required')
            builder=cmd(['git','-C',ROOT,'rev-parse','HEAD'])
            require(builder==os.environ.get('GITHUB_SHA'),'Builder checkout does not match dispatched SHA')
            require(not cmd(['git','-C',ROOT,'status','--porcelain','--untracked-files=all']),'Dirty builder checkout')
            require(cmd(['git','-C',self.host,'rev-parse','HEAD'])==SOURCE and cmd(['git','-C',self.host,'rev-parse','HEAD^{tree}'])==TREE,'Source Git identity differs')
            require(not cmd(['git','-C',self.host,'status','--porcelain','--untracked-files=all']),'Dirty source checkout')
            xcode=cmd(['xcodebuild','-version']);require(xcode.splitlines()[0]=='Xcode 26.4.1','Unexpected Xcode')
            runtimes=json.loads(cmd(['xcrun','simctl','list','runtimes','--json']))
            rows=[r for r in runtimes['runtimes'] if r.get('identifier')==RUNTIME]
            require(len(rows)==1 and rows[0].get('isAvailable') is True and rows[0].get('buildversion')=='23F77','Pinned runtime unavailable')
            require(platform.machine()=='arm64','Pinned architecture differs')
            write(self.evidence/'environment.json',{'xcode':xcode,'runtime':rows[0],'architecture':platform.machine(),'developerDir':os.environ.get('DEVELOPER_DIR')})
            project=prepare(self.host,self.evidence/'fixture',builder)
            self.generated_expected=json.loads((self.evidence/'fixture/source-identity.json').read_text())['generatedFiles']
            derived=self.work/'DerivedData'
            cmd(['xcodebuild','build-for-testing','-project',project,'-scheme','P2Settings','-sdk','iphonesimulator','-destination','generic/platform=iOS Simulator','-derivedDataPath',derived,'-jobs','2','CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO'],240)
            sys.path.insert(0,str(ROOT/'scripts'))
            import p2_localization_contract as resources
            plural=load('p2_form_plural',ROOT/'ci/p2/preflight.py')
            app=derived/'Build/Products/Debug-iphonesimulator/P2SettingsHost.app'
            resource_report={'catalog':resources.verify_app(app),'plurals':plural.verify_exact_plural_values(app,json.loads((self.host/'Resources/Localizable.xcstrings').read_text()))}
            write(self.evidence/'compiled-resources.json',resource_report)
            require(all(v['status']=='passed' for v in resource_report.values()),'Actual compiled resources failed')
            runs=list((derived/'Build/Products').glob('*.xctestrun'));require(len(runs)==1,'Ambiguous xctestrun')
            original_run=sha(runs[0]);shutil.copyfile(runs[0],self.evidence/'original.xctestrun')
            self.device=cmd(['xcrun','simctl','create','P2-Form-'+os.environ.get('GITHUB_RUN_ID','unknown')+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','unknown'),DEVICE_TYPE,RUNTIME])
            require(re.fullmatch('[A-Fa-f0-9-]{36}',self.device) is not None,'Malformed created simulator ID')
            write(self.evidence/'device.json',{'udid':self.device,'runtime':RUNTIME,'type':DEVICE_TYPE,'fresh':True})
            cmd(['xcrun','simctl','boot',self.device]);cmd(['xcrun','simctl','bootstatus',self.device,'-b'],180)
            for arm,method in METHODS.items():
                outcome={'status':'failed','errors':[]}; self.report['arms'][arm]=outcome
                bundle=self.work/(arm+'.xcresult'); target=self.evidence/arm;target.mkdir()
                try:
                    require(sha(runs[0])==original_run,'xctestrun changed before arm')
                    _,record=self.commands.run(['xcodebuild','test-without-building','-xctestrun',runs[0],'-destination','id='+self.device,'-resultBundlePath',bundle,'-parallel-testing-enabled','NO','-maximum-concurrent-test-simulator-destinations','1','-test-timeouts-enabled','YES','-default-test-execution-time-allowance','150','-maximum-test-execution-time-allowance','150','-only-testing:P2SettingsUITests/FormDirectionTests/'+method],240,checked=False)
                    outcome['command']=record
                    if record['primaryError'] or record.get('quiescenceError'):outcome['errors'].append('Native command failed')
                    for kind in ['summary','tests']:
                        try:write(target/(kind+'.json'),json.loads(cmd(['xcrun','xcresulttool','get','test-results',kind,'--path',bundle],30)))
                        except Exception as error:outcome['errors'].append(kind+': '+str(error))
                    try:cmd(['xcrun','xcresulttool','export','attachments','--path',bundle,'--output-path',target/'attachments'],60)
                    except Exception as error:outcome['errors'].append('attachments: '+str(error))
                    try:outcome['xctest']=verify_result(json.loads((target/'summary.json').read_text()),json.loads((target/'tests.json').read_text()),method)
                    except Exception as error:outcome['errors'].append('XCTest gate: '+str(error))
                    try:
                        from run_p0_signin_rendering import valid_png
                        outcome['observations']=verifier.verify_arm(target/'attachments',arm,lambda path: valid_png(path.read_bytes()))
                    except Exception as error:outcome['errors'].append('Observation gate: '+str(error))
                    require(sha(runs[0])==original_run,'xctestrun changed after arm')
                    if not outcome['errors']:outcome['status']='collection-complete'
                except Exception as error:outcome['errors'].append(str(error))
                write(target/'result.json',outcome)
                if not self.commands.all_settled:raise ValueError('Unsettled writer prevents next arm')
            require(all(r['status']=='collection-complete' for r in self.report['arms'].values()),'One or both arms failed collection')
            self.report['comparison']=verifier.verify_pair(self.report['arms']['control']['observations'],self.report['arms']['candidate']['observations'])
            verify_inputs(self.host,self.approved)
            require(not cmd(['git','-C',self.host,'status','--porcelain','--untracked-files=all']),'Source changed during experiment')
            self.report['status']='collection-complete'
        except Exception as error:self.report['errors'].append(str(error))
        finally:
            self.commands.begin_finalization()
            simulator_settled = self.device is None
            if self.device:
                for action in [['terminate',self.device,'org.sidestore.fixture.p2settings'],['shutdown',self.device]]:
                    try:
                        _,r=self.commands.run(['xcrun','simctl',*action],30,checked=False)
                        # A stopped fixture may already have exited; retain every raw response.
                        if action[0]=='shutdown':
                            simulator_settled=not r['primaryError'] and not r.get('quiescenceError')
                            if not simulator_settled:self.report['errors'].append('Simulator shutdown failed')
                    except Exception as error:self.report['errors'].append('Finalization: '+str(error))
            if self.generated_expected is not None and self.commands.all_settled:
                try:self.report['generatedInputRecheck']=verify_generated(self.evidence/'fixture',self.generated_expected,self.evidence/'generated-inputs-after.json')
                except Exception as error:self.report['errors'].append(str(error))
            for name,path in [('compiled-products',self.work/'DerivedData/Build/Products'),*[(a+'.xcresult',self.work/(a+'.xcresult')) for a in METHODS]]:
                if path.exists():
                    try:
                        require(self.commands.all_settled,'Unsettled owned command writer; archive stability not established')
                        self.report.setdefault('archives',{})[name]=archive(path,self.evidence/(name+'.zip'),self.commands.deadline)
                    except Exception as error:self.report['errors'].append('Retain '+name+': '+str(error))
            if self.report['errors']:self.report['status']='failed'
            write(self.evidence/'result.json',self.report)
            context={'status':self.report['status'],'errors':[str(x)[:1000] for x in self.report['errors'][:6]],
                     'failedCommands':getattr(self.commands,'failed_receipts',[]),
                     'arms':{arm:{'status':row['status'],'errors':[str(x)[:1000] for x in row['errors'][:4]],
                                  'primaryError':row.get('command',{}).get('primaryError'),
                                  'ownedProcessGroup':row.get('command',{}).get('ownedProcessGroup')}
                             for arm,row in self.report['arms'].items()}}
            transported=publish_evidence(self.evidence,self.publish,self.commands.all_settled and simulator_settled,self.commands.deadline,reason='Owned command/simulator quiescence gate',context=context,outputs=getattr(self.commands,'failed_output_receipts',[]))
            if not transported:
                self.report['status']='failed';self.report['errors'].append('Full evidence transport blocked; inspect stable failure receipt')
        return self.report

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--publish',type=Path,required=True);p.add_argument('--approved-inputs-sha256',required=True);a=p.parse_args()
    result=Probe(a.host.resolve(),a.work.resolve(),a.evidence.resolve(),a.publish.resolve(),a.approved_inputs_sha256).run()
    require(result['status']=='collection-complete','Paired Form observation failed; retained evidence is authoritative')
if __name__=='__main__':main()
