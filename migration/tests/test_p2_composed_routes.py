"""Complete activated routing with explicit synthetic Git/filesystem boundaries.

These tests establish control flow and negative policy, not real acquisition or
native execution. The normal workflow must still acquire and verify actual Git.
"""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(Path(__file__).resolve().parent)]
import test_p2_composed_admission as fixtures
import prepare_candidate_inventory as inventory
import p2_composed_host as p2
import p0_cxx_allocation as prior
import maintained_sources as gate

class ActivatedRouteTests(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.SyntheticApprovalTests('test_synthetic_exact_approval_schema_and_package');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  self.tmp=self.fixture.tmp;self.stage=self.fixture.stage;self.review=self.fixture.review
  self.document=self.fixture.pins();self.workspace=Path(self.tmp.name)/'workspace'
  self.old_review=fixtures.load(ROOT/prior.DIRECTORY/'reviewed-approval.json')
  self.old_transitions=fixtures.load(ROOT/prior.DIRECTORY/'source-transition.json')
  self.final_inventory=fixtures.load(self.stage/'source-inventory.json')
  self.roots={name:self.workspace/row['path']for name,row in self.document['owners'].items()}
  self.by_path={str(path):name for name,path in self.roots.items()}
  for owner,name in prior.LOCK_FILES.items():
   path=self.roots[owner]/name;path.parent.mkdir(parents=True,exist_ok=True)
   path.write_bytes((ROOT/prior.DIRECTORY/'resolver-locks'/f'{owner}.Package.resolved.after').read_bytes())
  self.mutation=None
 def snapshot(self,source,base,commit):
  name=self.by_path[str(source)]
  if base==inventory.BASES[name]:value=copy.deepcopy(self.final_inventory[name])
  elif base==prior.PARENTS[name]:value=copy.deepcopy(self.old_transitions[name])
  elif name in prior.LOCK_FILES:
   value={'baseline_commit':base,'commit':commit,'tree':self.old_review['owners'][name]['tree'],'changes':{prior.LOCK_FILES[name]:{}}}
  else:raise AssertionError(('unexpected snapshot',name,base,commit))
  if self.mutation=='source-inventory' and name=='LiveContainer':value['tree']='a'*40
  if self.mutation=='resolver-runtime' and name=='SideStore' and base not in (inventory.BASES[name],prior.PARENTS[name]):value['changes']['Extra.swift']={}
  return value
 def git(self,source,*args):
  name=self.by_path[str(source)]
  if args[:2]==('rev-list','--parents'):
   if name=='LiveContainer':return (p2.LC_COMMIT+' '+('a'*40 if self.mutation=='parent' else p2.LC_PARENT)+'\n').encode()
   return (self.document['owners'][name]['commit']+' '+prior.PARENTS[name]+'\n').encode()
  if args[:2]==('merge-base','--is-ancestor'):return b''
  if args[0]=='rev-parse':
   if name=='LiveContainer':return (p2.LC_PARENT_TREE+'\n').encode()
   resolver=fixtures.load(ROOT/prior.DIRECTORY/(name+'-resolver.json'))
   return (('a'*40 if self.mutation=='resolver-tree' else resolver['resolver_tested_tree'])+'\n').encode()
  if args[0]=='show':
   raw=(ROOT/prior.DIRECTORY/'resolver-locks'/f'{name}.Package.resolved.before').read_bytes()
   return raw+b'\n' if self.mutation=='resolver-lock' else raw
  raise AssertionError(('unexpected Git',name,args))
 def delta(self,source,before,after):
  name='composition-parent-delta.json' if before==p2.LC_PARENT else 'reviewed-source-delta.json'
  value=fixtures.load(self.stage/name)
  if self.mutation=='delta':value['tree']='a'*40
  return value
 def origin(self,*args,**kwargs):
  if self.mutation=='origin':raise ValueError('synthetic boundary: actual origin failure')
  return {'synthetic_boundary':True}
 def run_route(self):
  original=p2.approval
  def approved(integration_root,metadata_root=None):return original(integration_root,self.stage if metadata_root is None else metadata_root)
  with patch.object(p2,'REVIEW_SHA256',self.document['native_validation']['review_sha256']),patch.object(p2,'approval',side_effect=approved),\
   patch.object(inventory,'snapshot',side_effect=self.snapshot),patch.object(p2,'git_delta',side_effect=self.delta),patch.object(gate,'git',side_effect=self.git),\
   patch.object(gate,'verify_checkout',return_value={'synthetic_boundary':True})as checkout,patch.object(gate,'verify_origin',side_effect=self.origin)as origin,\
   patch.object(gate,'verify_graph')as graph,patch.object(p2,'verify_children')as children,\
   patch.object(p2.subprocess,'run',return_value=SimpleNamespace(stdout='{"synthetic_boundary": true}'))as validator:
   result=gate.verify_all(self.workspace,self.document)
   self.assertEqual(checkout.call_count,7);self.assertEqual(origin.call_count,7)
   self.assertEqual({call.kwargs['owner']for call in checkout.call_args_list},set(prior.PARENTS))
   self.assertTrue(all(call.kwargs['require_full_history'] is True for call in checkout.call_args_list))
   graph.assert_called_once();children.assert_called_once();validator.assert_called_once()
   argv=validator.call_args.args[0];self.assertEqual(argv.count('--owner'),7)
   self.assertIn(self.review['files']['compatibility-registry.json'],argv)
   self.assertFalse(result['runtime_rewrites']);self.assertIsNone(result['resolved_dependencies']);self.assertIsNone(result['built_idevice'])
   return result
 def test_complete_synthetic_boundary_route(self):self.run_route()
 def test_each_source_and_lock_policy_rejects_mutation(self):
  for mutation in ('source-inventory','resolver-runtime','parent','resolver-tree','resolver-lock','delta'):
   self.mutation=mutation
   with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.run_route()
 def test_source_origin_failure_is_not_swallowed(self):
  self.mutation='origin'
  with self.assertRaisesRegex(ValueError,'actual origin failure'):self.run_route()
 def test_unapproved_checkout_path_rejected(self):
  original=self.document['owners']['LiveContainer']['path'];self.document['owners']['LiveContainer']['path']='unapproved/path'
  with self.assertRaisesRegex(ValueError,'caller pins'):self.run_route()
  self.document['owners']['LiveContainer']['path']=original

if __name__=='__main__':unittest.main()
