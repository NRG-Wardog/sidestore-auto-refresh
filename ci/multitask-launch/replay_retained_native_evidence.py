#!/usr/bin/env python3
"""Replay real retained runtime evidence over an exact source checkout with a synthetic overlay.
This validates the complete focused replay call chain, NOT native OpenSSL recipe
admission: generated iOS binaries and host qualification inputs are not retained.
No app/compiler/runtime fixture or downloaded binary is executed here.
"""
import argparse,json,os,subprocess,sys,tempfile
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
import contract,run_completion,verify
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'issues-native'))
import record,multitask_receipt

@contextmanager
def temporary_source(source):
    """Owned detached worktree, no Git transport or network permission changes."""
    with tempfile.TemporaryDirectory(prefix='guest-native-replay-') as folder:
        root=Path(folder).resolve()/'LiveContainer'
        command=['git','-C',str(source),'-c','core.hooksPath=/dev/null','worktree']
        subprocess.run([*command,'add','--quiet','--detach',str(root),contract.manifest()['commit']],check=True)
        try:yield root
        finally:subprocess.run([*command,'remove','--force',str(root)],check=True)

def run(source,evidence):
    source=source.resolve(strict=True);evidence=evidence.resolve(strict=True)
    expected=contract.read(evidence/'source.json')['identity']
    with temporary_source(source) as root:
        folder=root.parent
        tracked=contract.inventory(root)
        overlay=root/'GeneratedDependencies/OpenSSL35';overlay.mkdir(parents=True)
        generated=overlay/'synthetic-test-input.bin';generated.write_bytes(b'SYNTHETIC OVERLAY NOT A NATIVE OPENSSL INPUT\n')
        fixture_evidence=Path(folder).resolve()/'synthetic-native-evidence';fixture_evidence.mkdir()
        snapshot={'LiveContainer':{'commit':contract.manifest()['commit'],'tree':record.git(root,'rev-parse','HEAD^{tree}'),'files':tracked}}
        generated_record={'relative_path':'LiveContainer/GeneratedDependencies/OpenSSL35','present':True,'files':record.generated_inventory(overlay)}
        snapshot['generated-openssl']=generated_record
        for name in ('acquired.json','compiler-inputs.json','after-build.json'):
            (fixture_evidence/name).write_text(json.dumps(snapshot,sort_keys=True)+'\n')
        # Only the unavailable native recipe/host admission dependency is doubled.
        # The real native inventory validator, Git checks and full retained
        # focused verifier execute; the generated fixture closure is still exact.
        def synthetic_sealed(**kwargs):
            contract.require(kwargs=={'required':True,'sealed':True},'Wrong synthetic seal invocation')
            contract.require(record.generated_inventory(overlay)==generated_record['files'],'Synthetic overlay closure changed')
            return generated_record
        def replay():
            env={v:expected[k] for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]}
            with patch.object(record,'S',root.parent),patch.object(record,'E',fixture_evidence),patch.object(record,'generated_openssl_snapshot',side_effect=synthetic_sealed),patch.dict(os.environ,env):
                # native_inventory uses the fixed owner directory name.
                validated=multitask_receipt.native_inventory(root.parent,fixture_evidence,expected)
            return verify.verify(root,evidence,expected,validated_native_inventory=validated)
        result=replay()
        multitask_receipt.verify_portable(multitask_receipt.read(evidence.parent/'multitask-launch-tests.json'),{k:list(v) for k,v in record.OWNERS.items()},expected)
        # Counterfactual of the actual omission: nested validation re-enters the
        # pristine check instead of consuming the caller's validated inventory.
        actual=run_completion.validate
        with patch.object(run_completion,'validate',side_effect=lambda data,path,**kw:actual(data,path)):
            try:replay()
            except ValueError as error:contract.require(str(error)=='Source worktree is not clean','Wrong nested counterfactual failure')
            else:raise ValueError('Nested inventory omission was not detected')
        # The override must not suppress real source-byte verification.
        target=root/'MultitaskSupport/AppSceneViewController.m';original=target.read_bytes();target.write_bytes(original+b'\n// controlled tamper\n')
        try:replay()
        except ValueError as error:contract.require(str(error)=='Native tracked LC source changed','Wrong actual source tamper failure')
        else:raise ValueError('Actual source tamper was not rejected')
        finally:target.write_bytes(original)
        unknown=root/'unknown.swift';unknown.write_text('controlled untracked input')
        try:replay()
        except ValueError as error:contract.require(str(error)=='Unknown or missing native untracked LC input','Wrong untracked failure')
        else:raise ValueError('Unknown untracked source accepted')
        finally:unknown.unlink()
        original_mode=target.stat().st_mode & 0o777;target.chmod(original_mode ^ 0o111)
        try:replay()
        except ValueError as error:contract.require(str(error)=='Native LC tracked mode/content changed','Wrong mode failure')
        else:raise ValueError('Tracked mode change accepted')
        finally:target.chmod(original_mode)
        target.write_bytes(original+b'\n// controlled staged tamper\n')
        subprocess.run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','add',str(target)],check=True)
        target.write_bytes(original)
        try:replay()
        except ValueError as error:contract.require(str(error)=='Native LC index changed','Wrong index failure')
        else:raise ValueError('Staged index change accepted')
        finally:subprocess.run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','reset','--quiet','HEAD','--',str(target)],check=True)
        # Default focused mode must still reject generated dirt.
        try:verify.verify(root,evidence,expected)
        except ValueError as error:contract.require(str(error)=='Source worktree is not clean','Wrong pristine failure')
        else:raise ValueError('Focused pristine policy was weakened')
        contract.require(replay()==result,'Restored replay differs')
        return {'status':'PASS_FULL_RETAINED_FOCUSED_CHAIN_SYNTHETIC_OVERLAY','identity':expected,'source':contract.manifest(),'retained_runtime_evidence':True,'synthetic_generated_overlay':True,'native_recipe_admission_executed':False,'controls':['full-retained-chain','nested-inventory-counterfactual','actual-source-tamper','unknown-untracked','tracked-mode','staged-index','focused-default-pristine','restored-full-chain']}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path);p.add_argument('--evidence',required=True,type=Path);a=p.parse_args();print(json.dumps(run(a.source,a.evidence),sort_keys=True,indent=2))
