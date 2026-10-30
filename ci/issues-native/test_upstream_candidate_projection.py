import copy,hashlib,json,subprocess,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
import upstream_candidate_projection as P
class ProjectionTests(unittest.TestCase):
 def test_exact_reviewed_blob_only_and_original_hash_preserved(self):
  with tempfile.TemporaryDirectory() as tmp:
   sources=Path(tmp).resolve();root=sources/'Owner';root.mkdir()
   def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
   git('init','-q');git('config','user.name','fixture');git('config','user.email','fixture@example.invalid')
   p=root/'code.swift';p.write_text('original\n');old=P.sha(p.read_bytes());git('add','.');git('commit','-qm','baseline')
   p.write_text('reviewed candidate\n');git('add','.');git('commit','-qm','candidate');head=git('rev-parse','HEAD');blob=git('rev-parse','HEAD:code.swift')
   graph={'owners':{'Owner':['owner/repo',head]},'reviewed_deltas':{'Owner':{'files':{'code.swift':blob}}}}
   original={'Owner/code.swift':old};expected=copy.deepcopy(original)
   projected,changes=P.bind(original,{'Owner/code.swift'},graph,sources)
   self.assertEqual(original,expected);self.assertEqual(changes['Owner/code.swift'],{'before_sha256':old,'after_sha256':P.sha(p.read_bytes()),'reviewed_blob':blob})
   with self.assertRaises(ValueError):P.bind(original,set(),graph,sources)
   wrong=copy.deepcopy(graph);wrong['reviewed_deltas']['Owner']['files']['code.swift']='a'*40
   with self.assertRaises(ValueError):P.bind(original,{'Owner/code.swift'},wrong,sources)
   p.write_text('unreviewed mutation\n')
   with self.assertRaises(ValueError):P.bind(original,{'Owner/code.swift'},graph,sources)
 def test_no_case_or_marker_inputs_can_be_rebound(self):
  self.assertEqual(len(P.ALLOWED_MM),3);self.assertEqual(len(P.ALLOWED_VPN),7)
  for path in P.ALLOWED_MM|P.ALLOWED_VPN:
   self.assertNotIn('policy_cases.swift',path);self.assertNotIn('Original',path);self.assertNotIn('baseline-receipt',path);self.assertNotIn('interface_doubles',path)
 def test_exact_additive_wifi_role_preserves_original_roles(self):
  original={'producer':{'path':'fixed/producer.swift','sha256':'1'*64}}
  value=P.reviewed_vpn_rows(original)
  self.assertEqual(value['producer'],original['producer'])
  self.assertEqual(set(value),set(original)|{P.WIFI_ROLE,P.SELECTION_ROLE})
  self.assertEqual(value[P.WIFI_ROLE],{'path':P.WIFI_PATH,'sha256':P.WIFI_SHA})
  self.assertEqual(value[P.SELECTION_ROLE],{'path':P.SELECTION_PATH,'sha256':P.SELECTION_SHA})
  with self.assertRaises(ValueError):P.reviewed_vpn_rows({'other':{'path':P.SELECTION_PATH,'sha256':P.SELECTION_SHA}})
  with self.assertRaises(ValueError):P.reviewed_vpn_rows({P.SELECTION_ROLE:{'path':'unexpected','sha256':'a'*64}})
  with self.assertRaises(ValueError):P.reviewed_vpn_rows(value)
  with self.assertRaises(ValueError):P.reviewed_vpn_rows({'other':{'path':P.WIFI_PATH,'sha256':P.WIFI_SHA}})
 def test_exact_observation_dependency_bytes_reject_tamper(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();selection=root/P.SELECTION_PATH;mapper=root/P.OBSERVATION_MAPPER
   selection.parent.mkdir(parents=True);mapper.parent.mkdir(parents=True)
   selection.write_bytes(b'reviewed selection');mapper.write_bytes(b'reviewed mapper')
   with patch.object(P,'SELECTION_SHA',P.sha(selection.read_bytes())),patch.object(P,'OBSERVATION_MAPPER_SHA',P.sha(mapper.read_bytes())):
    P.validate_observation_dependencies(root)
    for path in (selection,mapper):
     original=path.read_bytes();path.write_bytes(b'unreviewed change')
     with self.assertRaises(ValueError):P.validate_observation_dependencies(root)
     path.write_bytes(original)
 def test_cli_and_native_receipt_use_current_projection(self):
  verify=(P.HERE/'verify_upstream_sync.py').read_text();record=(P.HERE/'record.py').read_text()
  self.assertIn("current['minimuxer_fake_tests']",verify)
  self.assertIn("current['vpn_transport_regression']",record)
  self.assertIn("write('upstream-runtime-projection.json', projection)",record)
  helper=Path(P.__file__).read_text()
  self.assertIn("if not vpn_changes:require(target['generated_sha256']==frozen",helper)
  self.assertIn("row['path']==original[name]['path']",helper)
  self.assertIn("row['sha256']==vpn[row['path']]",helper)
  self.assertIn("sha((sources/WIFI_TEST_PATH).read_bytes())==WIFI_TEST_SHA",helper)
  self.assertEqual(P.WIFI_TEST_PATH,'SideStore/tests/vpn_transport_recovery/test_contract.py')
  for workflow in ('refresh-truth-preflight.yml','cold-vpn-native.yml'):
   text=(P.HERE.parents[1]/'.github/workflows'/workflow).read_text()
   for command in ('upstream_candidate_projection.py --sources sources --output evidence/upstream-runtime-projection-preflight.json','preflight_comprehensive_pairing.py --sources sources --output evidence/comprehensive-pairing-preflight'):
    self.assertIn(command,text)
if __name__=='__main__':unittest.main(verbosity=2)
