"""Controls use only owned Python fake emitters, never execute downloaded software."""
import io,json,pathlib,struct,sys,tempfile,time,unittest,zipfile,zlib,shutil,subprocess
from contract import manifest,require,SHA,BUDGET,ITERATIONS,strict_pairs
from sweep import parse,assemble,select_best,execute_one,inflate
ROOT=pathlib.Path(__file__).resolve().parent

def sample():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('Payload/App.app/code',b'A'*8000+b'B'*2000)
        archive.writestr('Payload/App.app/resource',b'C'*100)
    return stream.getvalue()

class Controls(unittest.TestCase):
    def test_manifest_failclosed_and_duplicatekeys(self):
        with tempfile.TemporaryDirectory() as directory:
            p=pathlib.Path(directory)/'manifest.json';p.write_text(json.dumps({'schema':'cold-vpn-green-native-input-v1','status':'PENDING_MATCHING_GREEN_NATIVE_INPUT'}))
            # Production placeholder deliberately cannot authorize acquisition.
            with self.assertRaises(ValueError):manifest(p)
            value={'schema':'cold-vpn-green-native-input-v1'};value.update(run_id=1,run_attempt=1,artifact_id=2,artifact_bytes=100,ipa_bytes=50,entries=2,commit='a'*40,artifact_sha256='b'*64,ipa_sha256='c'*64)
            p.write_text(json.dumps(value));self.assertEqual(manifest(p),value)
            value['run_id']=True;p.write_text(json.dumps(value))
            with self.assertRaises(ValueError):manifest(p)
            with self.assertRaises(ValueError):json.loads('{"a":1,"a":2}',object_pairs_hook=strict_pairs)
    def test_exact_copy_and_archive_metadata(self):
        data=sample();rows,end=parse(data)
        best={r['index']:{'bytes':len(r['stream']),'sha256':SHA(r['stream']),'iterations':0} for r in rows}
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(assemble(rows,end,best,pathlib.Path(directory)),data)
    def test_improvement_only_selection(self):
        before={'bytes':100,'sha256':'a','iterations':5}
        for status in ('bounded_timeout','budget_exhausted','compressor_exit'):
            self.assertIs(select_best(before,{'status':status}),before)
        self.assertIs(select_best(before,{'status':'verified','bytes':101}),before)
        self.assertEqual(select_best(before,{'status':'verified','bytes':90,'sha256':'b','iterations':50}),{'bytes':90,'sha256':'b','iterations':50})
    def test_fake_emitter_exact_and_timeout_preserves_prior(self):
        row=parse(sample())[0][0]
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve();(root/'streams').mkdir()
            compressed=zlib.compress(row['raw'])[2:-4]
            script=root/'fake.py';script.write_text('import sys\nsys.stdout.buffer.write('+repr(compressed)+')\n')
            result=execute_one(root,row,5,time.monotonic()+10,[sys.executable,str(script)])
            self.assertEqual(result['status'],'verified')
            before={'bytes':result['bytes'],'sha256':result['sha256'],'iterations':5}
            script.write_text('import time\ntime.sleep(5)\n')
            result=execute_one(root,row,15,time.monotonic()+3.1,[sys.executable,str(script)])
            self.assertEqual(result['status'],'bounded_timeout')
            self.assertIs(select_best(before,result),before)
            self.assertFalse((root/'streams'/f"{row['index']}-15.deflate").exists())
            self.assertTrue((root/'streams'/f"{row['index']}-5.deflate").exists())
    def test_nonzero_and_signal_discard_partial_preserve_best(self):
        row=parse(sample())[0][0]
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve();(root/'streams').mkdir()
            before={'bytes':10,'sha256':'previous_verified','iterations':5}
            for iterations,body,expected in [(15,'import sys\nsys.stdout.write("partial")\nsys.exit(7)\n',7),
                    (50,'import os,signal\nos.kill(os.getpid(),signal.SIGTERM)\n',-15)]:
                script=root/'fake.py';script.write_text(body)
                result=execute_one(root,row,iterations,time.monotonic()+10,[sys.executable,str(script)])
                self.assertEqual(result['status'],'compressor_exit');self.assertEqual(result['exit_code'],expected)
                self.assertIs(select_best(before,result),before)
                self.assertFalse((root/'streams'/f"{row['index']}-{iterations}.deflate").exists())
    def test_success_exit_with_invalid_stream_still_fails_closed(self):
        row=parse(sample())[0][0]
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve();(root/'streams').mkdir()
            script=root/'fake.py';script.write_text('print("not deflate")\n')
            with self.assertRaises((ValueError,zlib.error)):
                execute_one(root,row,5,time.monotonic()+10,[sys.executable,str(script)])
    def test_entire_owned_fake_quick_pass_keeps_best_and_stops_after_five_iterations(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve()
            for name in ('sweep.py','contract.py','zip32.py','limited_exec.py'):
                shutil.copyfile(ROOT/name,root/name)
            data=sample();(root/'original.ipa').write_bytes(data)
            value={'schema':'cold-vpn-green-native-input-v1','run_id':1,'run_attempt':1,'artifact_id':2,'artifact_bytes':100,'ipa_bytes':len(data),'entries':2,'commit':'a'*40,'artifact_sha256':'b'*64,'ipa_sha256':SHA(data)}
            (root/'input-manifest.json').write_text(json.dumps(value))
            fake=root/'zopfli'
            fake.write_text('#!'+sys.executable+'\nimport sys,zlib\ndata=open(sys.argv[-1],"rb").read()\nx=zlib.compressobj(9,zlib.DEFLATED,-15)\nsys.stdout.buffer.write(x.compress(data)+x.flush())\n')
            fake.chmod(0o700)
            result=subprocess.run([sys.executable,'-B',str(root/'sweep.py')],text=True,capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            receipt=json.loads((root/'result.json').read_text())
            self.assertEqual(receipt['stop_reason'],'iteration_schedule_exhausted')
            self.assertEqual(len(receipt['rounds']),1)
            self.assertEqual(receipt['rounds'][0]['iterations'],5)
            self.assertEqual(receipt['rounds'][0]['attempts'],1)
            self.assertEqual((root/'original.ipa').read_bytes(),data)
            rows,_=parse((root/'recompressed.ipa').read_bytes())
            self.assertEqual([x['raw'] for x in rows],[x['raw'] for x in parse(data)[0]])
            self.assertLessEqual(receipt['final_bytes'],len(data))
    def test_entire_sweep_finalizes_after_nonzero_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve()
            for name in ('sweep.py','contract.py','zip32.py','limited_exec.py'):
                shutil.copyfile(ROOT/name,root/name)
            data=sample();(root/'original.ipa').write_bytes(data)
            value={'schema':'cold-vpn-green-native-input-v1','run_id':1,'run_attempt':1,'artifact_id':2,'artifact_bytes':100,'ipa_bytes':len(data),'entries':2,'commit':'a'*40,'artifact_sha256':'b'*64,'ipa_sha256':SHA(data)}
            (root/'input-manifest.json').write_text(json.dumps(value))
            fake=root/'zopfli'
            fake.write_text('#!'+sys.executable+'\nimport sys,zlib\nif "--i5" in sys.argv: sys.exit(7)\ndata=open(sys.argv[-1],"rb").read()\nx=zlib.compressobj(9,zlib.DEFLATED,-15)\nsys.stdout.buffer.write(x.compress(data)+x.flush())\n')
            fake.chmod(0o700)
            result=subprocess.run([sys.executable,'-B',str(root/'sweep.py')],text=True,capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            receipt=json.loads((root/'result.json').read_text())
            self.assertEqual([x['status'] for x in receipt['results']],['compressor_exit'])
            self.assertEqual(receipt['results'][0]['exit_code'],7)
            self.assertEqual(receipt['results'][0]['saved_vs_previous'],0)
            self.assertLessEqual(receipt['final_bytes'],len(data))
            self.assertEqual((root/'original.ipa').read_bytes(),data)
            self.assertEqual([x['raw'] for x in parse((root/'recompressed.ipa').read_bytes())[0]],
                             [x['raw'] for x in parse(data)[0]])
    def test_wrong_or_trailing_payload_rejected(self):
        with self.assertRaises(ValueError):inflate(zlib.compress(b'wrong')[2:-4],b'right')
        with self.assertRaises(ValueError):inflate(zlib.compress(b'right')[2:-4]+b'extra',b'right')
    def test_crc_and_duplicate_entry_rejected(self):
        data=bytearray(sample());rows,_=parse(bytes(data));offset=len(rows[0]['local']);data[offset]^=255
        with self.assertRaises((ValueError,zlib.error)):parse(bytes(data))
    def test_approved_bounds_and_owned_limiter(self):
        self.assertEqual(BUDGET,1500);self.assertEqual(ITERATIONS,(5,))
        limiter=(ROOT/'limited_exec.py').read_text()
        for text in ('RLIMIT_AS,(2*1024**3,2*1024**3)','RLIMIT_CPU,(600,600)','RLIMIT_CORE,(0,0)','os.execve','--deflate'):
            self.assertIn(text,limiter)
        self.assertNotIn('shell=True',(ROOT/'sweep.py').read_text())
    def test_workflow_green_before_compile_and_no_install(self):
        workflow=(ROOT.parents[1]/'.github/workflows/cold-vpn-zopfli.yml').read_text()
        self.assertLess(workflow.index('ci/cold-vpn-zopfli/acquire.py'),workflow.index('/usr/bin/cc workspace/source'))
        self.assertLess(workflow.index('workspace/verify_source.py'),workflow.index('/usr/bin/cc workspace/source'))
        self.assertIn('if: success()',workflow)
        self.assertIn('contents: read',workflow)
        for forbidden in ('pip install','apt install','make install','curl |','always()'):
            self.assertNotIn(forbidden,workflow)
        self.assertIn('180s python3 -B workspace/independent_verify.py',workflow)
if __name__=='__main__':unittest.main()
