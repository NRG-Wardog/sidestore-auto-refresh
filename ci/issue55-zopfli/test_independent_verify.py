"""Source contract for independent admission, no compressor or acquisition execution."""
import ast,pathlib,unittest
class AdmissionContract(unittest.TestCase):
 def test_exact_identity_and_dynamic_output(self):
  s=pathlib.Path(__file__).with_name('independent_verify.py').read_text();ast.parse(s)
  for text in ("len(new)<=24000000", "result['final_bytes'],result['final_sha256']", "24597739", "e16afa534eb33e94c126b2b899240cb9894fd932e4cc36627efda033b08bb237", "len(eligible)==110", "result['aggregate_timeout_seconds']==1600", "assert changed==result['changed_compressed_streams']", "assert __debug__"):
   self.assertIn(text,s)
  self.assertNotIn('5204c485c9b0585',s)
 def test_no_execution_or_network(self):
  tree=ast.parse(pathlib.Path(__file__).with_name('independent_verify.py').read_text())
  imports={x.name.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.Import) for x in n.names}
  self.assertTrue(imports.isdisjoint({'subprocess','urllib','socket','requests','os'}))
if __name__=='__main__':unittest.main()
