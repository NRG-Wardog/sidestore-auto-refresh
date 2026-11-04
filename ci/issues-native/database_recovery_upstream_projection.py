"""Finite source-owned recovery successor; frozen prior descriptor is unchanged."""
import json
from pathlib import Path
import preservation_upstream_tests_projection as prior
HERE=Path(__file__).resolve().parent
MANIFEST='database-recovery-upstream-tests-extension.json'
MANIFEST_SHA='e41a2caa9cfb63a0e73fee88187316e31990fe0ecd2af510f3c75c2e222f3520'
TEST=prior.TEST
OLD_CASE='test_maintenance_preservation.SwiftSourceContractTests.test_database_selection_never_copies_or_moves_database'
NEW_CASE='test_maintenance_preservation.SwiftSourceContractTests.test_database_selection_delegates_to_preserving_recovery'
CHANGED={TEST,'SideStore/SideStore/MaintenanceManager.swift'}
ADDED={'SideStore/'+p for p in ('SideStore/DatabaseRecovery.swift','SideStore/DatabaseRecoverySQLite.c','AltStore/Core/Model/DatabaseManager/DatabaseManager.swift','AltStore/Core/Model/DatabaseManager/PersistentContainer.swift','AltStore/Core/Model/InstalledApp.swift')}
require=prior.require
digest=prior.digest
test_ids=prior.test_ids

def specification(sources=None):
 old=prior.specification()
 if sources is not None and digest(sources/TEST) in (prior.PRIOR_TEST_SHA,old['inputs'][TEST]):return old
 require(digest(HERE/MANIFEST)==MANIFEST_SHA,'Unknown recovery successor descriptor')
 value=json.loads((HERE/MANIFEST).read_text())
 require(set(value)=={'schema','prior_descriptor_sha256','count','case_map','case_names','inputs'},'Recovery descriptor closure')
 require(value['schema']=='reviewed-database-recovery-upstream-tests-v1' and value['prior_descriptor_sha256']==prior.MANIFEST_SHA,'Recovery predecessor differs')
 require(type(value['count']) is int and value['count']==164,'Recovery case count differs')
 require(value['case_map']=={OLD_CASE:NEW_CASE},'Recovery case substitution differs')
 require(value['case_names']==sorted(NEW_CASE if n==OLD_CASE else n for n in old['case_names']),'Recovery case inventory differs')
 require(set(value['inputs'])==set(old['inputs'])|ADDED,'Recovery source closure differs')
 require(all(value['inputs'][p]==v for p,v in old['inputs'].items() if p not in CHANGED),'Historical unrelated input changed')
 return value

def bind(sources,case_names,prior_count):
 value=specification(sources)
 if value['schema']!= 'reviewed-database-recovery-upstream-tests-v1':return prior.bind(sources,case_names,prior_count)
 require(prior_count==158,'Frozen baseline count differs')
 require(sorted(case_names)==value['case_names'],'Recovery cases missing, duplicate or changed')
 for name,expected in value['inputs'].items():require(digest(sources/name)==expected,'Unbound recovery input: '+name)
 folder=sources/'SideStore/tests/upstream_sync'
 require({p.relative_to(sources).as_posix() for p in folder.glob('test_*.py')}=={p for p in value['inputs'] if '/tests/upstream_sync/' in p},'Unexpected recovery fixture file')
 return {'schema':value['schema'],'descriptor_sha256':MANIFEST_SHA,'prior_descriptor_sha256':prior.MANIFEST_SHA,'count':164,'case_names':list(value['case_names']),'inputs':dict(value['inputs']),'case_map':dict(value['case_map'])}

def verify_report(sources,report):
 value=specification(sources)
 if value['schema']!='reviewed-database-recovery-upstream-tests-v1':return prior.verify_report(sources,report)
 proof=bind(sources,value['case_names'],158)
 require(set(report)=={'reviewed_preservation_successor','status','tests_run','failures','errors','skipped','test_sources_sha256','target_native_executed','authentication_executed','scope'},'Recovery report closure differs')
 require(report['reviewed_preservation_successor']==proof,'Recovery source proof differs')
 require(report['status']=='PASS' and type(report['tests_run']) is int and report['tests_run']==164,'Recovery source suite failed')
 require(all(type(report[k]) is int and report[k]==0 for k in ('failures','errors','skipped')),'Recovery source suite failed/skipped')
 require(report['target_native_executed'] is False and report['authentication_executed'] is False,'Invalid recovery source claim')
 require(report['scope']=='source-owned fake-data/model and source tests only','Recovery source scope differs')
 require(report['test_sources_sha256']=={p.removeprefix('SideStore/'):v for p,v in value['inputs'].items() if '/tests/upstream_sync/' in p},'Recovery test source hashes differ')
 return proof

def strict_pairs(rows):
 result={}
 for key,value in rows:
  require(key not in result,'Duplicate report key');result[key]=value
 return result

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--sources',type=Path,required=True);p.add_argument('--report',type=Path,required=True);args=p.parse_args()
 verify_report(args.sources.resolve(strict=True),json.loads(args.report.read_text(),object_pairs_hook=lambda rows: strict_pairs(rows)))
 print('DATABASE_RECOVERY_UPSTREAM_REPORT_PASS cases=164 native_executed=false')
