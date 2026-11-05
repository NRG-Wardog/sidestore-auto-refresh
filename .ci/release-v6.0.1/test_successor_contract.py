import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import successor_contract as s

def policy():
    return {'schema':'exact-seven-file-ci-successor-v1','base_commit':s.BASE_COMMIT,'base_tree':s.BASE_TREE,'replaces_target':s.REPLACES_TARGET,'target_commit':'a'*40,'target_tree':'b'*40,'replacements':[{'path':p,'mode':'100644','type':'blob','sha':f'{i+10:040x}'} for i,p in enumerate(sorted(s.PATHS))]}
def trees(p):
    dirs={str(parent) for name in s.PATHS for parent in Path(name).parents if str(parent)!='.'}
    base={'sha':s.BASE_TREE,'truncated':False,'tree':[{'path':d,'mode':'040000','type':'tree','sha':'c'*40} for d in sorted(dirs)]+[{'path':r['path'],'mode':'100644','type':'blob','sha':'d'*40} for r in p['replacements']]+[{'path':'application.swift','mode':'100644','type':'blob','sha':'e'*40}]}
    target=copy.deepcopy(base);target['sha']=p['target_tree']
    for row in target['tree']:
        if row['path'] in s.PATHS:row.update(next(r for r in p['replacements'] if r['path']==row['path']))
    return base,target
class ExactSuccessor(unittest.TestCase):
    def test_exact_seven_blob_delta_passes(self):
        p=policy();a,b=trees(p);self.assertTrue(s.verify_delta(a,b,p)['production_tree_preserved'])
    def test_wrong_policy_path_rejected(self):
        p=policy();p['replacements'][0]['path']='application.swift'
        with self.assertRaises(ValueError):s.validate_policy(p)
    def test_policy_duplicate_or_missing_path_rejected(self):
        p=policy();p['replacements'][0]=p['replacements'][1]
        with self.assertRaises(ValueError):s.validate_policy(p)
    def test_policy_wrong_mode_or_type_rejected(self):
        for key,value in [('mode','100755'),('type','tree'),('sha','not-a-sha')]:
            p=policy();p['replacements'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):s.validate_policy(p)
    def test_unknown_policy_fields_rejected(self):
        p=policy();p['allow_additional']=True
        with self.assertRaises(ValueError):s.validate_policy(p)
    def test_unbound_policy_rejected(self):
        p=policy();p['target_tree']='UNBOUND'
        with self.assertRaises(ValueError):s.validate_policy(p)
    def test_wrong_target_root_rejected(self):
        p=policy();a,b=trees(p);b['sha']='f'*40
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_wrong_base_root_rejected(self):
        p=policy();a,b=trees(p);a['sha']='f'*40
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_wrong_reviewed_blob_rejected(self):
        p=policy();a,b=trees(p);next(r for r in b['tree'] if r['path'] in s.PATHS)['sha']='f'*40
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_extra_production_edit_rejected(self):
        p=policy();a,b=trees(p);next(r for r in b['tree'] if r['path']=='application.swift')['sha']='f'*40
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_extra_file_addition_rejected(self):
        p=policy();a,b=trees(p);b['tree'].append({'path':'extra.txt','type':'blob','mode':'100644','sha':'f'*40})
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_extra_deletion_rejected(self):
        p=policy();a,b=trees(p);b['tree']=[r for r in b['tree'] if r['path']!='application.swift']
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_reviewed_path_deletion_rejected(self):
        p=policy();a,b=trees(p);b['tree']=[r for r in b['tree'] if r['path']!=p['replacements'][0]['path']]
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_missing_reviewed_change_rejected(self):
        p=policy();a,b=trees(p);next(r for r in b['tree'] if r['path'] in s.PATHS)['sha']='d'*40
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_extra_empty_directory_rejected(self):
        p=policy();a,b=trees(p);b['tree'].append({'path':'empty','type':'tree','mode':'040000','sha':'f'*40})
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_truncated_tree_rejected(self):
        p=policy();a,b=trees(p);b['truncated']=True
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_duplicate_tree_path_rejected(self):
        p=policy();a,b=trees(p);b['tree'].append(b['tree'][0])
        with self.assertRaises(ValueError):s.verify_delta(a,b,p)
    def test_rebind_exact_old_or_idempotent_only(self):
        p=policy();s.admit_rebind(s.REPLACES_TARGET,p['target_commit'],p);s.admit_rebind(p['target_commit'],p['target_commit'],p)
        for old,new in [('f'*40,p['target_commit']),(s.REPLACES_TARGET,'f'*40)]:
            with self.subTest(old=old,new=new),self.assertRaises(ValueError):s.admit_rebind(old,new,p)
    def test_policy_hash_and_links_fail_closed(self):
        raw=(json.dumps(policy())+'\n').encode()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'main-successor.json').write_bytes(raw)
            with patch.object(s,'ROOT',root),patch.object(s,'POLICY_SHA256',hashlib.sha256(raw).hexdigest()):self.assertEqual(s.load_policy(),policy())
            with patch.object(s,'ROOT',root),patch.object(s,'POLICY_SHA256','0'*64),self.assertRaises(ValueError):s.load_policy()
            (root/'main-successor.json').rename(root/'target.json');(root/'main-successor.json').symlink_to(root/'target.json')
            with patch.object(s,'ROOT',root),patch.object(s,'POLICY_SHA256',hashlib.sha256(raw).hexdigest()),self.assertRaises(ValueError):s.load_policy()
if __name__=='__main__':unittest.main()
