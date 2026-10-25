"""Compare pinned LC settings invocations only. Never executes build or test actions."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    entries = subprocess.check_output(['git', 'ls-tree', '-rz', 'HEAD'], cwd=root).split(b'\0')
    result = {}
    for entry in entries:
        if not entry: continue
        header, name = entry.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        name = os.fsdecode(name)
        path = root/name
        if mode == '160000': result[name] = {'mode': mode, 'gitlink': oid}
        elif mode == '120000':
            data = os.fsencode(os.readlink(path))
            blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            if blob != oid: raise ValueError('Tracked symlink differs from HEAD')
            result[name] = {'mode': mode, 'symlink_sha256': digest(data)}
        else:
            if path.is_symlink() or not path.is_file(): raise ValueError('Invalid tracked source')
            data = path.read_bytes()
            blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            actual_mode = '100755' if path.stat().st_mode & 0o111 else '100644'
            if blob != oid or actual_mode != mode: raise ValueError('Tracked source differs from HEAD blob/mode')
            result[name] = {'mode': mode, 'sha256': digest(data)}
    return result


def duplicate_rows(data):
    rows = json.loads(data)
    originals = {}
    duplicates = 0
    conflicts = []
    for row in rows:
        original = json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
        target = row['target']
        if target in originals:
            duplicates += 1
            if originals[target] != original: conflicts.append(target)
        else: originals[target] = original
    return {'input_rows':len(rows),'unique_targets':len(originals),'duplicate_rows':duplicates,
            'all_full_original_duplicates_identical':not conflicts,'conflicting_targets':conflicts}


def command(root, temp, name):
    args = ['xcodebuild', '-project', str(root/'LiveContainer.xcodeproj')]
    if name == 'alltargets': args += ['-alltargets']
    else: args += ['-scheme', 'LiveContainer']
    args += ['-sdk', 'iphoneos', '-configuration', 'Release']
    if name != 'alltargets':
        args += ['-destination', 'generic/platform=iOS', '-derivedDataPath', str(temp/name/'native-LiveContainer')]
        if name != 'scheme': args += ['-resultBundlePath', str(temp/name/'native-LiveContainer-build.xcresult')]
    args += ['-onlyUsePackageVersionsFromResolvedFile', '-disableAutomaticPackageResolution']
    if name != 'alltargets': args += ['CODE_SIGNING_ALLOWED=NO', 'CODE_SIGNING_REQUIRED=NO']
    if name == 'scheme-result-action-no': args += ['-enableCodeCoverage', 'NO']
    if name == 'scheme-result-setting-no': args += ['CLANG_COVERAGE_MAPPING=NO']
    args += ['-showBuildSettings', '-json']
    return args


def run_settings_query(cmd, raw):
    with raw.open('wb') as output:
        try:
            return subprocess.run(cmd,stdout=output,stderr=subprocess.PIPE,timeout=120)
        except subprocess.TimeoutExpired as error:
            return subprocess.CompletedProcess(cmd,124,stderr=error.stderr or b'')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--temp', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if args.temp.exists() or args.evidence.exists(): raise ValueError('Fresh probe outputs required')
    args.temp.mkdir(parents=True); args.evidence.mkdir(parents=True)
    harness = Path(__file__).resolve().parents[2]
    graph = json.loads((harness/'ci/issues-native/candidate-graph.json').read_text())
    owner, commit = graph['owners']['LiveContainer']
    if owner != 'NRG-Wardog/LiveContainer': raise ValueError('Wrong source owner')
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip() != commit:
        raise ValueError('Wrong pinned source')
    subprocess.run(['git','diff','--exit-code','HEAD','--'],cwd=source,check=True)
    before = inventory(source)
    (args.evidence/'source-before.json').write_text(json.dumps(before,sort_keys=True,indent=2)+'\n')
    capture_path = harness/'ci/issues-native/capture_release_settings.py'
    spec = importlib.util.spec_from_file_location('capture_settings',capture_path)
    capture = importlib.util.module_from_spec(spec); spec.loader.exec_module(capture)
    tool = (Path(os.environ['DEVELOPER_DIR'])/'usr/bin/xcodebuild').resolve(strict=True)
    version = subprocess.run([str(tool),'-version'],capture_output=True,text=True,timeout=30,check=True)
    if version.stdout.splitlines()[:2] != ['Xcode 26.4.1','Build version 17E202']:
        raise ValueError('Unexpected Xcode tool version')
    identity = {'tool_path':str(tool),'tool_sha256':digest(tool.read_bytes()),'version':version.stdout.strip(),
        'source_tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=source,text=True).strip(),
        'helper_sha256':{'probe.py':digest(Path(__file__).read_bytes()),
                         'capture_release_settings.py':digest(capture_path.read_bytes()),
                         'candidate-graph.json':digest((harness/'ci/issues-native/candidate-graph.json').read_bytes())}}
    (args.evidence/'tool-source-identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    try:
        help_run = subprocess.run([str(tool),'-help'],capture_output=True,text=True,timeout=30)
    except subprocess.TimeoutExpired:
        help_run = subprocess.CompletedProcess([str(tool),'-help'],124,stdout='',stderr='help query timed out')
    (args.evidence/'xcode-help-options.txt').write_text('\n'.join(line for line in (help_run.stdout+'\n'+help_run.stderr).splitlines()
        if any(key in line for key in ('enableCodeCoverage','showBuildSettings','resultBundlePath','clonedSourcePackagesDirPath')))+'\n')
    reports = {}
    for name in ('alltargets','scheme','scheme-result','scheme-result-action-no','scheme-result-setting-no'):
        (args.temp/name).mkdir()
        cmd = command(source,args.temp,name)
        cmd[0] = str(tool)
        raw = args.temp/(name+'.json')
        run = run_settings_query(cmd,raw)
        # Error bodies stay in the ephemeral runner temp; no environment dump is uploaded.
        (args.temp/(name+'.stderr')).write_bytes(run.stderr)
        report = capture.capture(raw,run.returncode)
        if report.get('parse_status') == 'PARSED':
            report['duplicate_normalization'] = duplicate_rows(raw.read_bytes())
        report.update(command=cmd,timed_out=run.returncode == 124,timeout_seconds=120,stderr_sha256=digest(run.stderr),stderr_bytes=len(run.stderr))
        reports[name] = report
        (args.evidence/(name+'.json')).write_text(json.dumps(report,indent=2)+'\n')
        after = inventory(source)
        if after != before: raise ValueError('Tracked source changed during '+name)
    (args.evidence/'source-after.json').write_text(json.dumps(inventory(source),sort_keys=True,indent=2)+'\n')
    summary = {'schema':'coverage-invocation-probe-v1','workflow_commit':os.environ['GITHUB_SHA'],
        'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],
        'source_commit':commit,'source_unchanged':True,'settings_queries_only':True,
        'native_compile_or_link_executed':False,'production_admission':False,
        'variants':{name:{'exit_code':report['xcodebuild_exit_code'],'parse_status':report.get('parse_status')} for name,report in reports.items()}}
    (args.evidence/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if any(report['xcodebuild_exit_code'] != 0 or report.get('parse_status') != 'PARSED' for report in reports.values()):
        raise ValueError('One or more settings queries failed; inspect retained diagnostics')

if __name__ == '__main__': main()
