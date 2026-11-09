"""Read-only CMS admission from the already hash-qualified native projection.

Trusted graph/helper snapshots are reviewed packaging source, never artifact code.
The caller has already required the exact native green run and artifact hashes.
"""
import argparse
import json
import io
from pathlib import Path
import tempfile
import zipfile
import preservation_cms_portable as cms

HERE = Path(__file__).resolve().parent

def verify(root, input_manifest):
    root = Path(root).resolve(strict=True)
    identity = {'workflow_commit': input_manifest['commit'],
                'run_id': str(input_manifest['run_id']),
                'run_attempt': str(input_manifest['run_attempt'])}
    cms.identity(identity)
    contract = cms.decode(cms.read_bytes(HERE/'cms-source-contract.json'))
    graph_bytes = cms.read_bytes(HERE/'cms-native-graph.json')
    graph = cms.decode(graph_bytes)
    helper = cms.read_bytes(HERE/'cms-native-helper.snapshot')
    cms.require(cms.sha(helper) == contract['native_cms_helper_sha256'], 'Reviewed CMS helper snapshot differs')
    for owner, pin in contract['owners'].items():
        cms.require(graph['owners'][owner][1] == pin['commit'] and
                    graph['reviewed_deltas'][owner]['tree'] == pin['tree'], 'Reviewed CMS owner snapshot differs')
    native_bytes = cms.read_bytes(root/'native-receipt.json')
    native = cms.decode(native_bytes)
    cms.require({key:native[key] for key in identity} == identity, 'Native receipt identity differs')
    cms.require(native['status'] == 'COMPILE_ONLY_PASS_REQUIRES_REVIEW' and
                native['requested_graph'] == graph['owners'], 'Native requested graph differs')
    projection_bytes = cms.read_bytes(root/'native-evidence-projection.json')
    projection = cms.decode(projection_bytes)
    archive = root/'native-evidence-projection.zip'
    raw_archive = cms.read_bytes(archive, 31*1024*1024)
    cms.require(projection['schema'] == 'exact-native-evidence-projection-v1' and
                projection['execution'] is False, 'Projection scope differs')
    cms.require(projection['native_run'] == {key:input_manifest[key] for key in ('run_id','run_attempt','commit')}, 'Projection native identity differs')
    cms.require(projection['native_receipt_sha256'] == cms.sha(native_bytes) and
                projection['projection_sha256'] == cms.sha(raw_archive) and
                projection['projection_bytes'] == len(raw_archive), 'Projection bytes differ')
    expected = native['evidence_sha256']
    cms.require(projection['verified_reference_sha256'] == expected and
                projection['verified_reference_count'] == len(expected), 'Projection reference closure differs')
    with zipfile.ZipFile(io.BytesIO(raw_archive)) as z, tempfile.TemporaryDirectory(prefix='cms-package-') as temp:
        names = z.namelist()
        cms.require(len(names) == len(set(names)) and set(names) == set(expected)|{'native-receipt.json'}, 'Projection member closure differs')
        cms.require(z.read('native-receipt.json') == native_bytes, 'Projected native receipt differs')
        for name in cms.FILES:
            info = z.getinfo(name)
            cms.require(not info.is_dir() and not info.flag_bits & 1 and
                        0 < info.file_size <= 2*1024*1024, 'Projected CMS member bounds')
            data = z.read(info)
            cms.require(cms.digest(expected[name]) and cms.sha(data) == expected[name], 'Projected CMS native reference differs')
            (Path(temp)/name).write_bytes(data)
        result = cms.verify(temp, identity)
    return {'schema':'preservation-cms-package-admission-v1','status':'PASS',
            'native_receipt_sha256':cms.sha(native_bytes),
            'projection_report_sha256':cms.sha(projection_bytes),
            'projection_sha256':cms.sha(raw_archive),
            'reviewed_graph_sha256':cms.sha(graph_bytes),
            'reviewed_native_helper_sha256':cms.sha(helper),
            'cms':result}

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);args=p.parse_args()
    manifest=cms.decode(cms.read_bytes(args.root/'input-manifest.json'))
    result=verify(args.root,manifest)
    (args.root/'preservation-cms-package-admission.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS exact native CMS projection; no device execution')

if __name__=='__main__':main()
