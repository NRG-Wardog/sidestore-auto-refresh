"""Exact additive migration test inventory; never changes the frozen 158-case spec."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import hashlib,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
MANIFEST='preservation-upstream-tests-extension.json'
MANIFEST_SHA='c832e45fc86632a11714f08f76fc1beccb628dbb74cc4a7915e06ad9dbdc15e3'
TEST='SideStore/tests/upstream_sync/test_maintenance_preservation.py'
PRIOR_TEST_SHA='8f52f398bb89b2d9e6424291083fb48d1462bb3ea257b0c5bc4df77e4ce4ef5a'
def require(ok,why):
 if not ok:raise ValueError(why)
def digest(path):
 require(path.is_file() and not path.is_symlink(),'Missing or linked upstream successor input')
 return hashlib.sha256(path.read_bytes()).hexdigest()
def specification():
 path=HERE/MANIFEST
 require(digest(path)==MANIFEST_SHA,'Unknown upstream test successor descriptor')
 value=json.loads(path.read_text())
 require(value['schema']=='reviewed-preservation-upstream-tests-v1' and value['prior_count']==158 and value['count']==164,'Upstream successor count/schema differs')
 names=value['case_names'];extra=value['additional_cases']
 require(names==sorted(set(names)) and len(names)==164 and len(extra)==6 and set(extra)<=set(names),'Upstream successor exact case closure differs')
 return value
def bind(sources,case_names,prior_count):
 require(prior_count==158,'Historical SideStore test count changed')
 value=specification();actual=digest(sources/TEST)
 if actual==PRIOR_TEST_SHA:return None
 require(actual==value['inputs'][TEST],'Unreviewed migration test source')
 for path,expected in value['inputs'].items():
  require(digest(sources/path)==expected,'Unbound upstream successor input: '+path)
 folder=sources/'SideStore/tests/upstream_sync'
 expected_paths={p for p in value['inputs'] if p.startswith('SideStore/tests/upstream_sync/')}
 require({p.relative_to(sources).as_posix() for p in folder.glob('test_*.py')}==expected_paths,'Unexpected upstream fixture file')
 require(sorted(case_names)==value['case_names'],'Unexpected/missing/duplicate upstream test identity')
 return {'schema':value['schema'],'descriptor_sha256':MANIFEST_SHA,'count':value['count'],'case_names':list(value['case_names']),'inputs':dict(value['inputs'])}
def test_ids(suite):
 import unittest
 result=[]
 for item in suite:
  result.extend(test_ids(item) if isinstance(item,unittest.TestSuite) else [item.id()])
 return result
def verify_report(sources,report):
 value=specification()
 successor=bind(sources,value['case_names'],158)
 require(successor is not None,'Report successor requires reviewed current sources')
 require(report.get('reviewed_preservation_successor')==successor,'Unbound upstream report successor')
 require(set(report)=={'reviewed_preservation_successor','status','tests_run','failures','errors','skipped','test_sources_sha256','target_native_executed','authentication_executed','scope'},'Upstream report field closure differs')
 require(report.get('scope')=='source-owned fake-data/model and source tests only','Upstream report scope differs')
 require(report.get('status')=='PASS' and type(report.get('tests_run')) is int and report['tests_run']==value['count'],'Upstream report did not pass exact inventory')
 require(all(type(report.get(k)) is int and report[k]==0 for k in ('failures','errors','skipped')),'Upstream report contains failures or skips')
 require(report.get('target_native_executed') is False and report.get('authentication_executed') is False,'Forbidden upstream execution claim')
 expected={p.removeprefix('SideStore/'):h for p,h in value['inputs'].items() if '/tests/upstream_sync/' in p}
 require(report.get('test_sources_sha256')==expected,'Upstream report test hashes differ')
 return successor
if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--sources',type=Path,required=True);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
 verify_report(args.sources.resolve(strict=True),json.loads(args.report.read_text()))
 print('PRESERVATION_UPSTREAM_REPORT_PASS cases=164 native_executed=false')
