"""Exact settled command sequence and raw native-test/result bindings."""
import ast
import math
from pathlib import PurePosixPath
import re
from p2_archive import need, parse
from p2_sources import absolute, blob, PINS

TEST_NAMES = tuple(sorted(('test_actual_swift_parser_service_wire_and_host_copy',
    'test_bounded_primary_only_contract_and_no_recovery_mutation', 'test_real_native_producer_fixture',
    'test_shared_capture_validation_and_copy_contracts_match', 'test_trace_disabled_preserves_constructor_evidence')))
CONSUMER_RESULT = {'testsRun':5,'failures':0,'errors':0,'skipped':0,'successful':True}
RECORD_KEYS = {'command','budgetSeconds','startedUptime','finalization','primaryError','postSpawnRemainingSeconds',
               'ownedProcessGroup','terminationReturncode','returncode','finishedUptime','elapsedSeconds'}
GROUP_KEYS = {'pgid','quiescent','pipesJoined','signals','identityRetainedForSignals','reapedBeforeSignals','postReapPolicy','errors','leaderReaped'}


def verify_settings_output(settings_raw, settings_log, udid, run_id):
    expected = settings_raw.rstrip('\n')
    need(settings_log.startswith(expected) and settings_log.count(expected) == 1, 'raw build settings JSON differs')
    suffix = settings_log[len(expected):]
    warning = '\n\n--- xcodebuild: WARNING: Using the first of multiple matching destinations:\n'
    warning += ''.join('{ platform:iOS Simulator, arch:'+arch+', id:'+udid+', OS:26.5, name:P2-Compile-'+str(run_id)+' }\n'
                       for arch in ('arm64','x86_64'))
    need(not suffix.strip() or suffix == warning, 'unreviewed build settings stderr differs')


class Commands:
    def __init__(self, evidence):
        self.evidence = evidence
        self.rows = []
        numbers = []
        for name in evidence.files:
            if name.startswith('commands/'):
                match = re.fullmatch(r'commands/([0-9]{3,})\.(json|log)', name)
                need(match, 'unknown command evidence path')
                if match[2] == 'json': numbers.append(int(match[1]))
        need(sorted(numbers) == list(range(1,len(numbers)+1)) and numbers, 'command sequence missing/duplicated')
        self.number = 0; previous = 0
        for number in range(1,len(numbers)+1):
            stem = 'commands/'+f'{number:03d}'
            row = evidence.json(stem+'.json')
            need(set(row) == RECORD_KEYS and isinstance(row['command'],list) and row['command'] and
                 all(isinstance(x,str) and '\0' not in x for x in row['command']), 'command record schema differs')
            need(row['primaryError'] is None and row['finalization'] is False and type(row['returncode']) is int and
                 row['returncode'] == 0 and type(row['terminationReturncode']) is int and row['terminationReturncode'] == 0,
                 'failed/finalizing command cannot prove capture')
            for key in ('budgetSeconds','startedUptime','postSpawnRemainingSeconds','finishedUptime','elapsedSeconds'):
                need(type(row[key]) in (int,float) and math.isfinite(row[key]) and row[key] >= 0, 'invalid command timing')
            need(row['startedUptime'] >= previous and row['finishedUptime'] >= row['startedUptime'] and
                 abs(row['finishedUptime'] - row['startedUptime'] - row['elapsedSeconds']) < 1e-6 and
                 0 < row['postSpawnRemainingSeconds'] <= row['budgetSeconds'] <= 1500, 'command timing order/budget differs')
            previous = row['finishedUptime']
            group = row['ownedProcessGroup']
            need(set(group) == GROUP_KEYS and type(group['pgid']) is int and group['pgid'] > 0 and
                 all(group[k] is True for k in ('quiescent','pipesJoined','leaderReaped','reapedBeforeSignals')) and
                 group['identityRetainedForSignals'] is False and group['signals'] == [] and group['errors'] == [] and
                 group['postReapPolicy'] == 'read-only-group-disappearance', 'successful command writers not normally settled')
            log = evidence.raw(stem+'.log').decode('utf-8')
            self.rows.append((row,log))
        need({n for n in evidence.files if n.startswith('commands/')} ==
             {'commands/'+f'{i:03d}'+suffix for i in range(1,len(numbers)+1) for suffix in ('.json','.log')}, 'exact command file closure differs')

    def take(self, argv, *, output=None, build=False):
        need(self.number < len(self.rows), 'required command missing')
        row, log = self.rows[self.number]; self.number += 1
        need(row['command'] == argv, 'command context/sequence differs at '+str(self.number))
        need(row['budgetSeconds'] <= (1500 if build else 240), 'non-build command expanded budget')
        if output is not None:
            need(log == output+'\n', 'raw command output differs at '+str(self.number))
        return log

    def proof(self, value):
        root = value['root']
        for args, output in [(['rev-parse','HEAD'],value['commit']+'\n'),
                             (['rev-parse','HEAD^{tree}'],value['tree']+'\n'),
                             (['remote','get-url','origin'],value['origin']+'\n'),
                             (['status','--porcelain=v1','--untracked-files=all','--ignore-submodules=none'],'')]:
            self.take(['git','-C',root,*args],output=output)
        rows = value['files']
        tree = ''.join(row['mode']+' '+row['type']+' '+row['gitObject']+'\t'+name+'\0'
                       for name,row in sorted(rows.items(),key=lambda x:x[0].encode()))
        self.take(['git','-C',root,'ls-tree','-rz','--full-tree','HEAD'],output=tree)
        for name,row in sorted(rows.items(),key=lambda x:x[0].encode()):
            if row['mode'] in ('100644','100755'):
                self.take(['git','-C',root,'hash-object','--',root+'/'+name],output=row['gitObject']+'\n')


def verify_commands(evidence, proofs, capture_commit, run_id, host_repo):
    commands = Commands(evidence)
    first = commands.rows[0][0]['command']
    need(len(first) == 5 and first[:2] == ['git','-C'] and first[3:] == ['rev-parse','HEAD'], 'first runner identity command differs')
    builder = absolute(first[2]); root = str(PurePosixPath(builder).parent)
    need(PurePosixPath(builder).name == 'builder' and proofs['LiveContainer']['root'] == root+'/source' and
         proofs['SideStore']['root'] == root+'/peer' and proofs['AnisetteKit']['root'] == root+'/producer', 'source roots differ from reviewed workflow')
    commands.take(first,output=capture_commit+'\n')
    commands.take(['git','-C',builder,'status','--porcelain','--untracked-files=all'],output='')
    for owner in ('LiveContainer','SideStore','AnisetteKit'): commands.proof(proofs[owner])
    toolchain = commands.take(['xcodebuild','-version'])
    need(toolchain.splitlines()[0] == 'Xcode 26.4.1', 'Xcode version differs')
    host = proofs['LiveContainer']['root']
    for name in ('OpenSSL','litehook'):
        pin = PINS[name]; directory = host+'/'+name
        need(proofs[name]['root'] == directory, 'dependency checkout root differs')
        for args in (['init'],['remote','add','origin',pin[2]],['fetch','--depth=1','--no-tags','--no-recurse-submodules','origin',pin[0]],['checkout','--detach',pin[0]]):
            commands.take(['git','-C',directory,*args])
        commands.proof(proofs[name])
    commands.proof(proofs['LiveContainer'])
    next_argv = commands.rows[commands.number][0]['command']
    interpreter = next_argv[0]
    need(interpreter.startswith('/') and re.fullmatch(r'python3(?:\.[0-9]+)?',PurePosixPath(interpreter).name), 'native Python interpreter differs')
    test_log = commands.take([interpreter,'-B',builder+'/ci/p2/composed_host/consumer_gate.py','--host',host,
        '--peer',proofs['SideStore']['root'],'--producer',proofs['AnisetteKit']['root'],'--output',root+'/p2-composed-evidence/consumer-tests.json'])
    source = ast.parse(blob(host_repo,PINS['LiveContainer'][0],'tests/init_diagnostics/test_contract.py'))
    classes = [n for n in source.body if isinstance(n,ast.ClassDef) and n.name == 'InitializerDiagnosticContract']
    need(len(classes) == 1 and tuple(sorted(n.name for n in classes[0].body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_'))) == TEST_NAMES,
         'exact source consumer test inventory differs')
    matches = re.findall(r'^(test_[A-Za-z0-9_]+) \(test_contract\.InitializerDiagnosticContract\.(test_[A-Za-z0-9_]+)\) \.\.\. ok$',test_log,re.M)
    need(sorted(matches) == [(n,n) for n in TEST_NAMES] and re.findall(r'^Ran ([0-9]+) tests? in ',test_log,re.M) == ['5'] and
         re.findall(r'^OK.*$',test_log,re.M) == ['OK'] and not re.search(r'^(?:FAILED|ERROR|FAIL|.*\.\.\. skipped)\b',test_log,re.M), 'raw native consumer output differs')
    receipt = evidence.json('consumer-tests.json')
    need(receipt == CONSUMER_RESULT and all(type(receipt[k]) is type(v) for k,v in CONSUMER_RESULT.items()), 'five-test receipt differs from raw execution')
    runtimes = parse(commands.take(['xcrun','simctl','list','runtimes','--json']))
    types = parse(commands.take(['xcrun','simctl','list','devicetypes','--json']))
    created = evidence.json('created-simulator.json')
    runtime_id = 'com.apple.CoreSimulator.SimRuntime.iOS-26-5'; device_type = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro'
    selected = [r for r in runtimes['runtimes'] if r.get('identifier') == runtime_id and r.get('isAvailable') is True]
    need(len(selected) == 1 and selected[0].get('buildversion') == '23F77' and
         sum(t.get('identifier') == device_type for t in types['devicetypes']) == 1, 'pinned simulator runtime/type differs')
    udid = created.get('udid'); need(isinstance(udid,str) and re.fullmatch(r'[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}',udid), 'invalid created simulator')
    need(created == {'udid':udid,'runtime':selected[0],'deviceType':device_type,'bootRequested':False}, 'created simulator binding differs')
    commands.take(['xcrun','simctl','create','P2-Compile-'+str(run_id),device_type,runtime_id],output=udid+'\n')
    derived = root+'/p2-composed-work/DerivedData'
    base = ['xcodebuild','-project',host+'/LiveContainer.xcodeproj','-scheme','LiveContainer','-configuration','Release',
            '-sdk','iphonesimulator','-destination','platform=iOS Simulator,id='+udid,'-derivedDataPath',derived,
            '-disableAutomaticPackageResolution','-onlyUsePackageVersionsFromResolvedFile']
    settings_log = commands.take(base+['-showBuildSettings','-json'])
    settings_raw = evidence.raw('build-settings.json').decode()
    # The exact JSON is stdout; the sole accepted stderr warning lists the
    # same created simulator twice, once per architecture. No other suffix.
    verify_settings_output(settings_raw,settings_log,udid,run_id)
    rows = parse(settings_raw); values = [row['buildSettings'] for row in rows if row.get('target') == 'LiveContainer']
    need(len(values) == 1, 'ambiguous actual product target'); settings = values[0]
    expected = {'TARGET_NAME':'LiveContainer','PRODUCT_NAME':'LiveContainer','CONFIGURATION':'Release',
        'PRODUCT_BUNDLE_IDENTIFIER':'com.kdt.livecontainer','WRAPPER_EXTENSION':'app','MACH_O_TYPE':'mh_execute',
        'EFFECTIVE_PLATFORM_NAME':'-iphonesimulator','FULL_PRODUCT_NAME':'LiveContainer.app','EXECUTABLE_NAME':'LiveContainer',
        'PROJECT_FILE_PATH':host+'/LiveContainer.xcodeproj','SRCROOT':host}
    need(all(settings.get(k) == v for k,v in expected.items()) and settings.get('SDK_NAME','').startswith('iphonesimulator'), 'genuine Release simulator settings differ')
    target = absolute(settings['TARGET_BUILD_DIR']); need(PurePosixPath(target).is_relative_to(PurePosixPath(derived)), 'product escapes dedicated derived root')
    build_log = commands.take(base+['-resultBundlePath',root+'/p2-composed-work/build.xcresult','build'],build=True)
    need(re.findall(r'^\*\* BUILD SUCCEEDED \*\*$',build_log,re.M) == ['** BUILD SUCCEEDED **'], 'raw native build success marker missing/ambiguous')
    platform_log = commands.take(['xcrun','vtool','-show-build',target+'/LiveContainer.app/LiveContainer'])
    platforms = re.findall(r'^\s*platform\s+(\S+)\s*$',platform_log,re.M)
    need(platforms and all(v in ('IOSSIMULATOR','7') for v in platforms), 'raw vtool platform output differs')
    for owner in ('LiveContainer','SideStore','AnisetteKit'): commands.proof(proofs[owner])
    need(commands.number == len(commands.rows), 'extra commands beyond reviewed capture sequence')
    need(evidence.json('result.json')['machOPlatforms'] == platforms, 'raw vtool/result binding differs')
    return {'count':commands.number,'test_ids':['test_contract.InitializerDiagnosticContract.'+n for n in TEST_NAMES],
            'product_path':target+'/LiveContainer.app','xcode':toolchain.strip(),'simulator_booted':False}
