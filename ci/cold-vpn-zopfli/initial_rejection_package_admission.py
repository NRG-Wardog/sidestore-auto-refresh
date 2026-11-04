"""Read-only admission of source-owned fallback tests; no provider/account execution.

Use only reviewed source snapshots. Native artifacts are parsed as data, never code.
"""
import argparse
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import types
import zipfile
import owned_oda_package_admission as safe
from owned_oda_package_binding import artifact_from_source

HERE = Path(__file__).resolve().parent
FILES = ('issue-tests.json', 'owned-initial-provider-rejection.log')
REPORT = 'initial-rejection-package-admission.json'

def trusted():
    raw = safe.read(HERE/'fallback-source-contract.json')
    c = safe.decode(raw)
    safe.require(c['schema'] == 'reviewed-initial-provider-rejection-package-contract-v1', 'Fallback source contract schema')
    names = ('run_issue_tests.py', 'verify_candidate_graph.py', 'candidate-graph.json')
    safe.require(set(c['trusted_files_sha256']) == set(names), 'Fallback trusted helper closure')
    verified = {}
    for name in names:
        data = safe.read(HERE/'reviewed-native'/name)
        safe.require(safe.sha(data) == c['trusted_files_sha256'][name], 'Fallback trusted source differs: '+name)
        verified[name] = data
    def load(name, filename):
        module = types.ModuleType(name); module.__file__ = str(HERE/'reviewed-native'/filename)
        exec(compile(verified[filename], module.__file__, 'exec'), module.__dict__)
        return module
    graph = load('_fallback_package_graph', 'verify_candidate_graph.py')
    helper = load('_fallback_package_issue', 'run_issue_tests.py')
    data = graph.load_candidate_graph(HERE/'reviewed-native/candidate-graph.json')
    safe.require(c['required_markers'] == list(helper.INITIAL_REJECTION_MARKERS), 'Fallback marker source differs')
    expected_graph = {o: {'commit': data['owners'][o][1], 'tree': data['reviewed_deltas'][o]['tree']} for o in helper.OWNED_INPUTS}
    safe.require(c['source_provenance']['source_graph'] == expected_graph, 'Fallback expected source graph differs')
    return c, safe.sha(raw), graph, helper, data

def verify(root, manifest, projector):
    root = Path(root).resolve(strict=True)
    c, contract_sha, graph_module, helper, graph = trusted()
    identity = safe.native_identity(manifest, c)
    expected_artifact, projector_sha = artifact_from_source(projector)
    raw = safe.read(root/'native-receipt.json')
    native = safe.decode(raw)
    safe.require(all(type(native.get(k)) is str and native[k] == v for k,v in identity.items()), 'Fallback native identity differs')
    safe.require(native.get('status') == 'COMPILE_ONLY_PASS_REQUIRES_REVIEW' and native.get('requested_graph') == graph['owners'], 'Fallback native graph/status differs')
    refs = native.get('evidence_sha256')
    safe.require(type(refs) is dict and all(type(k) is str and safe.digest(v) for k,v in refs.items()), 'Fallback native reference map')
    safe.require(set(FILES) <= set(refs), 'Fallback mandatory evidence absent')
    report = safe.decode(safe.read(root/'native-evidence-projection.json'))
    archive = safe.read(root/'native-evidence-projection.zip', 31*1024*1024)
    safe.require(report.get('schema') == 'exact-native-evidence-projection-v1' and report.get('execution') is False, 'Fallback projection scope')
    projected_identity = report.get('native_run')
    safe.require(type(projected_identity) is dict and projected_identity == {k:manifest[k] for k in ('run_id','run_attempt','commit')} and all(type(projected_identity.get(k)) is int for k in ('run_id','run_attempt')), 'Fallback projection native identity')
    projected_artifact = {'artifact_id':report.get('source_artifact_id'), 'bytes':report.get('source_archive_bytes'), 'sha256':report.get('source_archive_sha256')}
    safe.archive_identity(projected_artifact)
    safe.require(projected_artifact == expected_artifact, 'Fallback native archive binding')
    safe.require(report.get('native_receipt_sha256') == safe.sha(raw) and report.get('projection_bytes') == len(archive) and report.get('projection_sha256') == safe.sha(archive), 'Fallback projection bytes binding')
    safe.require(report.get('verified_reference_sha256') == refs and type(report.get('verified_reference_count')) is int and report['verified_reference_count'] == len(refs), 'Fallback projection reference closure')
    selected = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        rows = z.infolist(); names = [i.filename for i in rows]
        safe.require(len(names) == len(set(names)) and set(names) == set(refs)|{'native-receipt.json'}, 'Fallback projection member closure')
        for info in rows:
            p=PurePosixPath(info.filename);mode=info.external_attr >> 16
            safe.require(not p.is_absolute() and p.as_posix() == info.filename and all(x not in ('','.','..') for x in p.parts) and '\\' not in info.filename, 'Fallback unsafe member path')
            safe.require(not info.is_dir() and not info.flag_bits & 1 and stat.S_IFMT(mode) in (0,stat.S_IFREG) and not mode & 0o7000, 'Fallback unsafe member type')
            if info.filename in (*FILES,'native-receipt.json'):
                safe.require(0 < info.file_size <= 8*1024*1024, 'Fallback member size')
                selected[info.filename] = z.read(info)
    safe.require(selected.pop('native-receipt.json') == raw, 'Fallback native receipt differs in projection')
    for name in FILES: safe.require(safe.sha(selected[name]) == refs[name], 'Fallback evidence reference hash differs')
    value = safe.decode(selected['issue-tests.json'])
    safe.require(set(value) == {'suites','owned_authentication','registry_runtime_double','signer_policy_and_production_body_double','target_device_execution','harness_suites','all_suites_passed_no_skips'}, 'Fallback issue receipt closure')
    previous=sys.modules.get('verify_candidate_graph');sys.modules['verify_candidate_graph']=graph_module
    try: helper.verify_owned_receipt(value['owned_authentication'], c['source_provenance'])
    finally:
        if previous is None:sys.modules.pop('verify_candidate_graph',None)
        else:sys.modules['verify_candidate_graph']=previous
    safe.require(value['suites'] == [{'owner':o,'suite':f+'/'+p,'status':'PASS_NO_SKIPS'} for o,f,p in helper.SUITES] and value['harness_suites'] == list(helper.HARNESS_SUITES) and value['registry_runtime_double'] == 'PASS' and value['signer_policy_and_production_body_double'] == 'PASS' and value['all_suites_passed_no_skips'] is True and value['target_device_execution'] is False, 'Fallback issue suite fields differ')
    lines=selected['owned-initial-provider-rejection.log'].decode('utf-8').splitlines()
    for marker in helper.INITIAL_REJECTION_MARKERS:
        safe.require([line for line in lines if line.startswith(marker.split(' ',1)[0])] == [marker], 'Fallback required marker missing/duplicate/unknown')
    return {'schema':'initial-provider-rejection-package-admission-v1','status':'PASS_EXACT_SOURCE_OWNED_AUTHENTICATION_FIXTURES_NOT_DEVICE','native_identity':identity,'trusted_contract_sha256':contract_sha,'trusted_projector_sha256':projector_sha,'evidence_sha256':{k:refs[k] for k in FILES},'required_markers':list(helper.INITIAL_REJECTION_MARKERS),'live_authentication_executed':False,'device_executed':False,'adapter_native_execution':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--projector',type=Path,required=True);a=p.parse_args()
    result=verify(a.root,safe.decode(safe.read(a.root/'input-manifest.json')),a.projector)
    (a.root/REPORT).write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print('PASS exact initial provider rejection evidence; no live authentication or device execution')
if __name__=='__main__':main()
