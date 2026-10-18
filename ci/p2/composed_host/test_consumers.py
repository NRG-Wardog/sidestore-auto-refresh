import unittest,types
from consumer_gate import result_contract
from run import owner_proof,PEER,PRODUCER
class ConsumerTests(unittest.TestCase):
 def test_no_skips_missing_or_failures(self):
  for count,skip,fail,error,expected in [(5,0,0,0,True),(4,0,0,0,False),(5,1,0,0,False),(5,0,1,0,False),(5,0,0,1,False)]:
   r=unittest.TestResult();r.testsRun=count;r.skipped=[None]*skip;r.failures=[None]*fail;r.errors=[None]*error
   self.assertEqual(result_contract(r)['successful'],expected)
 def test_exact_peer_children_and_restoration(self):
  original={'original':{'commit':'original'}};seen=[]
  p=types.SimpleNamespace(DEPENDENCIES=original)
  def proof(root,commit,tree,origin,dependencies):
   seen.append(dict(p.DEPENDENCIES));return {'commit':commit,'tree':tree}
  p.checkout_proof=proof
  self.assertEqual(owner_proof(p,'peer',PEER),{'commit':PEER['commit'],'tree':PEER['tree']})
  self.assertEqual(seen,[{k:{'commit':v}for k,v in PEER['children'].items()}]);self.assertIs(p.DEPENDENCIES,original)
  p.checkout_proof=lambda *a,**k:(_ for _ in ()).throw(ValueError('test failure'))
  with self.assertRaises(ValueError):owner_proof(p,'producer',PRODUCER)
  self.assertIs(p.DEPENDENCIES,original)
if __name__=='__main__':unittest.main()
