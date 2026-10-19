"""Activated routing with synthetic filesystem/Git boundaries, never native proof."""
import copy
import configparser
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_p0_full_login_admission as fixture
from test_p0_full_login_admission import ROOT, login, gate
import prepare_candidate_inventory as inventory


class RouteTests(unittest.TestCase):
    def setUp(self):
        fixture.AdmissionTests.setUp(self)
        self.workspace = self.root/'workspace'; self.workspace.mkdir()
        self.roots = {owner:self.workspace/row['path'] for owner,row in self.pins['owners'].items()}
        self.by_path = {str(path):owner for owner,path in self.roots.items()}
        self.transitions = login.parse((self.stage/'source-transition.json').read_bytes())
        self.full = login.parse((self.stage/'source-inventory.json').read_bytes())
        for owner,path in login.LOCKS.items():
            target=self.roots[owner]/path;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((self.stage/'resolver-locks'/f'{owner}.Package.resolved.after').read_bytes())
        self.mutation = None

    def snapshot(self, source, before, after):
        owner=self.by_path[str(source)]
        if before == inventory.BASES[owner]:
            result=copy.deepcopy(self.full[owner])
            if self.mutation=='inventory':result['tree']='a'*40
        elif owner in login.CHANGED and before == login.CHAINS[owner][0]:
            result=copy.deepcopy(self.transitions[owner])
            if self.mutation=='transition':result['tree']='a'*40
        elif owner in login.LOCKS and before == login.CHAINS[owner][-2]:
            row={'mode':'100644','type':'blob','git_object':'a'*40,'sha256':'b'*64}
            result={'changes':{login.LOCKS[owner]:{'before':row,'after':dict(row)}}}
            if self.mutation=='lock-runtime':result['changes']['Unexpected.swift']={}
            if self.mutation=='lock-mode':result['changes'][login.LOCKS[owner]]['after']['mode']='120000'
        else:raise AssertionError((owner,before,after))
        return result

    def git(self, source, *args):
        owner=self.by_path[str(source)]
        if args[:2]==('rev-list','--parents'):
            child=args[-1];chain=login.CHAINS[owner];parent=chain[chain.index(child)-1]
            return (child+' '+('a'*40 if self.mutation=='parent' else parent)+'\n').encode()
        if args[0]=='show':
            raw=(self.stage/'resolver-locks'/f'{owner}.Package.resolved.before').read_bytes()
            return raw+b' ' if self.mutation=='before-lock' else raw
        raise AssertionError((owner,args))

    def origin(self,*args,**kwargs):
        if self.mutation=='origin':raise ValueError('synthetic origin rejection')
        return {'synthetic_boundary':True}

    def run_route(self, after=False):
        original=login.approval
        def selected(integration_root,metadata_root=None):return original(integration_root,self.stage if metadata_root is None else metadata_root)
        with patch.object(login,'REVIEW_SHA256',self.anchor),patch.object(login,'approval',side_effect=selected),\
             patch.object(inventory,'snapshot',side_effect=self.snapshot),patch.object(gate,'git',side_effect=self.git),\
             patch.object(gate,'verify_checkout',return_value={'synthetic_boundary':True}) as checkout,\
             patch.object(gate,'verify_origin',side_effect=self.origin) as origin,patch.object(gate,'verify_graph') as graph,\
             patch.object(login,'verify_children') as children,patch.object(gate,'verify_build_products',return_value={'synthetic_boundary':True}) as products,\
             patch.object(login.subprocess,'run',return_value=SimpleNamespace(stdout='{"synthetic_boundary":true}')) as validator:
            result=gate.verify_all(self.workspace,self.pins,after_build=after)
            self.assertEqual(checkout.call_count,7);self.assertEqual(origin.call_count,7)
            self.assertTrue(all(call.kwargs['require_full_history'] is True for call in checkout.call_args_list))
            self.assertTrue(all(call.kwargs['after_build'] is after for call in checkout.call_args_list))
            graph.assert_called_once();children.assert_called_once();validator.assert_called_once()
            self.assertEqual(validator.call_args.args[0].count('--owner'),7)
            self.assertEqual(products.call_count,int(after));self.assertFalse(result['runtime_rewrites'])
            return result

    def test_complete_synthetic_prebuild_route(self):self.run_route()
    def test_complete_synthetic_postbuild_route(self):self.run_route(True)
    def test_all_actual_source_boundaries_are_fail_closed(self):
        for mutation in ('inventory','transition','parent','before-lock','lock-runtime','lock-mode','origin'):
            self.mutation=mutation
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.run_route()
    def test_actual_lock_worktree_drift_fails(self):
        target=self.roots['SideStore']/login.LOCKS['SideStore'];target.write_bytes(target.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'final genuine lock'):self.run_route()
    def test_wrong_root_cannot_be_substituted(self):
        self.pins['owners']['AnisetteKit']['path']='work/LegacyCxxAnisetteKit'
        with self.assertRaisesRegex(ValueError,'caller pins'):self.run_route()


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        fixture.AdmissionTests.setUp(self)
        self.workspace=self.root/'fresh';self.workspace.mkdir()
        self.children=login.parse((self.stage/'submodules.json').read_bytes())
        self.by_path={self.workspace/row['path']:(owner,row) for owner,row in self.review['owners'].items()}
        for key,row in self.children.items():
            owner,relative=key.split('/',1)
            self.by_path[self.workspace/self.review['owners'][owner]['path']/relative]=(key,row)
        self.clones=[];self.mutation=None

    def command(self,argv,**kwargs):
        self.assertIn('clone',argv);target=Path(argv[-1]);prefix,row=self.by_path[target]
        self.assertEqual(argv[-2],row['repository']);self.assertEqual(kwargs['env']['GIT_ALLOW_PROTOCOL'],'https')
        self.clones.append(prefix);target.mkdir(parents=True,exist_ok=True)
        configs=configparser.ConfigParser(interpolation=None)
        for key,child in self.children.items():
            if key.rsplit('/',1)[0]==prefix or key.startswith(prefix+'/') and '/' not in key[len(prefix)+1:]:
                relative=key[len(prefix)+1:];configs['submodule "'+relative+'"']={'path':relative,'url':child['declared_repository']}
            elif prefix=='SideStore' and key.startswith('SideStore/Dependencies/'):
                relative=key[len(prefix)+1:];configs['submodule "'+relative+'"']={'path':relative,'url':child['declared_repository']}
            elif prefix=='idevice' and key=='idevice/cpp/plist_ffi':
                configs['submodule "cpp/plist_ffi"']={'path':'cpp/plist_ffi','url':child['declared_repository']}
        if configs:
            with (target/'.gitmodules').open('w') as stream:configs.write(stream)
        return SimpleNamespace(returncode=0)

    def entries(self,path):
        prefix,_=self.by_path[path];result={}
        for key,row in self.children.items():
            if key.startswith(prefix+'/'):
                result[key[len(prefix)+1:]]=('160000','commit',('a'*40 if self.mutation=='gitlink' else row['commit']))
        return result

    def git(self,path,*args):
        if args[0]=='rev-parse':return (('a'*40 if self.mutation=='tree' else self.by_path[path][1]['tree'])+'\n').encode()
        self.assertIn(args[0],('fetch','checkout'));return b''

    def acquire(self):
        original=login.approval
        def selected(integration_root,metadata_root=None):return original(integration_root,self.stage if metadata_root is None else metadata_root)
        with patch.object(login,'REVIEW_SHA256',self.anchor),patch.object(login,'approval',side_effect=selected),\
             patch.object(login.subprocess,'run',side_effect=self.command),patch.object(gate,'git',side_effect=self.git),\
             patch.object(gate,'entries',side_effect=self.entries),patch.object(gate,'verify_origin') as origin:
            gate.acquire(self.workspace,self.pins)
            self.assertEqual(len(self.clones),10);self.assertEqual(origin.call_count,10)

    def test_all_owners_and_five_children_acquired_without_fallback(self):self.acquire()
    def test_wrong_actual_tree_rejected(self):
        self.mutation='tree'
        with self.assertRaisesRegex(ValueError,'tree differs'):self.acquire()
    def test_wrong_actual_child_gitlink_rejected_before_child_fetch(self):
        self.mutation='gitlink'
        with self.assertRaisesRegex(ValueError,'gitlink differs'):self.acquire()
        self.assertEqual(self.clones,['LiveContainer'])
    def test_existing_owner_path_rejected_before_clone(self):
        (self.workspace/'work/LiveContainer').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'fresh target'):self.acquire()
        self.assertEqual(self.clones,[])


if __name__=='__main__':unittest.main()
