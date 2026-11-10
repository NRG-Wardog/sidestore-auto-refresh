"""Finite early-rejection controls; no build or dependency resolution."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
from pathlib import Path as _CIPath
import sys as _workflow_sys
from pathlib import Path as _WorkflowPath
_workflow_sys.path.insert(0, str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci.build import verify_anisette_dependency_pins as M

A, S, T = 'a'*40, 'b'*40, 'c'*40


def lock(revision=A):
    return {'version': 3, 'originHash': 'unchanged', 'pins': [
        {'identity': 'anisettekit', 'kind': 'remoteSourceControl', 'location': M.URL, 'state': {'revision': revision}},
        {'identity': 'other', 'kind': 'remoteSourceControl', 'location': 'https://example.test/other.git', 'state': {'revision': 'd'*40}}]}


class Pins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(strict=True)
        self.sources = self.root/'sources'
        self.graph_path = self.root/'graph.json'
        self.graph = {'owners': {'AnisetteKit': ['NRG-Wardog/AnisetteKit', A], 'SideStore': ['NRG-Wardog/SideStore', S], M.SIGN: ['NRG-Wardog/SideSign', T]}}
        self.graph_path.write_text(json.dumps(self.graph))
        for owner in self.graph['owners']:
            (self.sources/owner).mkdir(parents=True, exist_ok=True)
        self.write(M.DECLARATION, '.package(url: "'+M.URL+'", revision: "'+A+'"),\n// .package(name: "AnisetteKit", path: "local")')
        for path in M.LOCKS:self.write(path, json.dumps(lock()))
        self.gitlink = T
        self.dirty = False
        self.heads = {o: p[1] for o,p in self.graph['owners'].items()}
        self.addCleanup(patch.stopall)
        patch.object(M, 'load_candidate_graph', return_value=self.graph).start()
        patch.object(M, 'git', side_effect=self.git).start()

    def write(self, path, text):
        p=self.sources/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)

    def git(self, root, *args):
        owner = Path(root).relative_to(self.sources).as_posix()
        if args == ('rev-parse','--show-toplevel'):return str(root)
        if args == ('rev-parse','HEAD'):return self.heads[owner]
        if args[0]=='ls-tree':return '160000 commit '+self.gitlink+'\tDependencies/SideSign'
        if args[0]=='rev-parse' and args[1].startswith('HEAD:'):
            data=(Path(root)/args[1][5:]).read_bytes()
            return '0'*40 if self.dirty else hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        raise AssertionError(args)

    def verify(self):return M.verify(self.sources,self.graph_path)

    def test_pass_is_read_only_and_finite(self):
        before={p:(self.sources/p).read_bytes() for p in (M.DECLARATION,*M.LOCKS)}
        result=self.verify()
        self.assertEqual(result['status'],'PASS')
        self.assertFalse(result['native_execution'])
        self.assertFalse(result['dependency_resolution_executed'])
        self.assertEqual(before,{p:(self.sources/p).read_bytes() for p in before})

    def test_each_consumer_stale_rejected(self):
        for path in (M.DECLARATION,*M.LOCKS):
            with self.subTest(path=path):
                original=(self.sources/path).read_text()
                self.write(path,original.replace(A,'e'*40))
                with self.assertRaisesRegex(ValueError,'dependency pin differs'):self.verify()
                self.write(path,original)

    def test_gitlink_mismatch(self):
        self.gitlink='e'*40
        with self.assertRaisesRegex(ValueError,'gitlink differs'):self.verify()

    def test_each_owner_head_mismatch(self):
        for owner in self.heads:
            old=self.heads[owner];self.heads[owner]='e'*40
            with self.assertRaisesRegex(ValueError,'source head differs'):self.verify()
            self.heads[owner]=old

    def test_uncommitted_dependency_rejected(self):
        self.dirty=True
        with self.assertRaisesRegex(ValueError,'uncommitted'):self.verify()

    def test_symlink_file_and_parent_rejected(self):
        p=self.sources/M.DECLARATION;target=self.root/'target';p.rename(target);p.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'linked'):self.verify()
        p.unlink();target.rename(p)
        owner=self.sources/M.SIGN;other=self.root/'owner';owner.rename(other);owner.symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'linked'):self.verify()

    def test_duplicate_json_key(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):M.lock_revision('{"version":3,"version":3,"pins":[]}')

    def test_duplicate_any_pin(self):
        value=lock();value['pins'].append(copy.deepcopy(value['pins'][1]))
        with self.assertRaisesRegex(ValueError,'duplicate'):M.lock_revision(json.dumps(value))

    def test_lock_identity_location_kind_and_requirement(self):
        for key,value in [('identity','renamed'),('location','https://evil.test/AnisetteKit.git'),('kind','localSourceControl'),('state',{'revision':A,'branch':'main'})]:
            d=lock();d['pins'][0][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):M.lock_revision(json.dumps(d))

    def test_missing_lock_pin(self):
        d=lock();d['pins']=d['pins'][1:]
        with self.assertRaises(ValueError):M.lock_revision(json.dumps(d))

    def test_declaration_duplicate_wrong_url_branch_computed(self):
        valid='.package(url: "'+M.URL+'", revision: "'+A+'")'
        for text in [valid+'\n'+valid,valid+'\n'+valid.replace('revision: \"'+A+'\"','revision: computed())'),valid.replace('NRG-Wardog','wrong'),valid.replace('revision:', 'branch:'),'.package(url: anisettekitURL, revision: commit)']:
            with self.subTest(text=text),self.assertRaises(ValueError):M.declaration_revision(text)

    def test_acquisition_gate_precedes_lock_copies(self):
        text = ((_CIPath(__file__).resolve().parents[3]/'ci/build/record.py')).read_text()
        acquire = text.split('def acquire():', 1)[1].split('def seal_resolution():', 1)[0]
        call = "write('anisette-dependency-pins.json', verify_anisette_dependency_pins(S, (Path(__file__).parent / 'candidate-graph.json')))"
        self.assertEqual(acquire.count(call), 1)
        self.assertLess(acquire.index('Unexpected child gitlink:'), acquire.index(call))
        self.assertLess(acquire.index(call), acquire.index('for owner, path in LOCKS.items():'))
        # Keep the later resolver seal independent and mandatory.
        self.assertIn('Unexpected starting AK lock: source must match the exact candidate graph', text)

    def test_focused_and_full_register_controls(self):
        root = (_CIPath(__file__).resolve().parents[3])
        for workflow, job in (('focused-diagnostics.yml', 'refresh_truth'), ('native-build.yml', None)):
            text = workflow_job(root/'.github/workflows'/workflow, job)
            self.assertIn('python3 -B harness/tests/ci/native/test_anisette_dependency_pins.py', text)

    def test_aliased_source_root_rejected(self):
        alias = self.root/'source-alias'
        alias.symlink_to(self.sources, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'linked sources root'):
            M.verify(alias, self.graph_path)

    def test_graph_symlink_rejected(self):
        actual=self.root/'actual.json';self.graph_path.rename(actual);self.graph_path.symlink_to(actual)
        with self.assertRaisesRegex(ValueError,'linked graph'):self.verify()


if __name__=='__main__':unittest.main()
