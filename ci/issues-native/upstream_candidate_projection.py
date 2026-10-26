"""Finite reviewed current-source binding; immutable upstream contract remains intact.
No runtime assertion, original case, frozen baseline or success marker is changed.
"""
import copy,hashlib,importlib.util,json
from pathlib import Path
from project_candidate_contracts import project
from verify_candidate_graph import HERE,load_candidate_graph,require
MM='SideStore/Dependencies/minimuxer/'
ALLOWED_MM={MM+p for p in ('.ci/verify-upstream-sync.py','Sources/DeviceProvider.swift','Sources/MinimuxerApi.swift')}
ALLOWED_VPN={MM+p for p in ('Sources/MinimuxerApi.swift','Sources/MinimuxerImpl.swift','Sources/Services/NetworkObserverService.swift')}|{'SideStore/SideStore/Core/Operations/PipelineExecutor.swift','SideStore/tests/vpn_transport_recovery/generate_fixture.py'}
def sha(data):return hashlib.sha256(data).hexdigest()
def bind(inputs,allowed,graph,sources):
 result,changes=project({'input_sha256':inputs},graph,sources)
 require(set(changes)<=allowed,'Unreviewed upstream runtime input change')
 return result['input_sha256'],changes
def current_spec(sources,graph=None):
 sources=Path(sources).resolve(strict=True);graph=load_candidate_graph() if graph is None else graph
 frozen_path=HERE/'upstream-sync-contract.json';frozen=json.loads(frozen_path.read_text());result=copy.deepcopy(frozen)
 mm_inputs={MM+k:v for k,v in frozen['minimuxer_fake_tests']['source_inputs'].items()}
 mm,mm_changes=bind(mm_inputs,ALLOWED_MM,graph,sources)
 result['minimuxer_fake_tests']['source_inputs']={k.removeprefix(MM):v for k,v in mm.items()}
 vpn,vpn_changes=bind(frozen['vpn_transport_regression']['source_inputs'],ALLOWED_VPN,graph,sources)
 target=result['vpn_transport_regression'];target['source_inputs']=vpn
 generator=sources/'SideStore/tests/vpn_transport_recovery/generate_fixture.py'
 module_spec=importlib.util.spec_from_file_location('_reviewed_vpn_projection_generator',generator)
 module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
 generated,paths=module.generate_source(sources)
 # These fixture cases and platform doubles are NOT allowlisted for changes.
 generated+=(generator.parent/'policy_cases.swift').read_text()
 rows={k:{'path':p.relative_to(sources).as_posix(),'sha256':sha(p.read_bytes())} for k,p in paths.items()}
 original=frozen['vpn_transport_regression']['input_rows']
 require(set(rows)==set(original),'VPN fixture input role closure changed')
 for name,row in rows.items():
  require(row['path']==original[name]['path'],'VPN fixture role path changed')
  require(row['sha256']==vpn[row['path']],'VPN fixture input unbound')
 target['input_rows']=rows;target['generated_sha256']=sha(generated.encode())
 if not vpn_changes:require(target['generated_sha256']==frozen['vpn_transport_regression']['generated_sha256'],'Unchanged VPN source generated different bytes')
 # Changed sections have exactly the same shape and executable expectations.
 require({k:v for k,v in result['minimuxer_fake_tests'].items() if k!='source_inputs'}=={k:v for k,v in frozen['minimuxer_fake_tests'].items() if k!='source_inputs'},'MM runtime semantics changed')
 require({k:v for k,v in target.items() if k not in ('source_inputs','input_rows','generated_sha256')}=={k:v for k,v in frozen['vpn_transport_regression'].items() if k not in ('source_inputs','input_rows','generated_sha256')},'VPN runtime semantics changed')
 report={'schema':'reviewed-current-upstream-runtime-projection-v1','frozen_contract_sha256':sha(frozen_path.read_bytes()),'source_graph':graph['owners'],'minimuxer_rebound_inputs':mm_changes,'vpn_rebound_inputs':vpn_changes,'vpn_generated_before_sha256':frozen['vpn_transport_regression']['generated_sha256'],'vpn_generated_after_sha256':target['generated_sha256'],'original_cases_and_markers_unchanged':True,'runtime_executed':False,'projection_helper_sha256':sha(Path(__file__).read_bytes())}
 return result,report

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--sources',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 current,report=current_spec(args.sources);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 print('CURRENT_UPSTREAM_SOURCE_PROJECTION_PASS_RUNTIME_NOT_EXECUTED')
