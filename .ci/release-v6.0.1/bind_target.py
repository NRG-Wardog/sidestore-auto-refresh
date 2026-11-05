"""Offline binding of only the hash-pinned, reviewed CI successor after verified merge."""
import hashlib,json,re,sys
from pathlib import Path
from successor_contract import load_policy,admit_rebind
p=Path(__file__).resolve().parent
if len(sys.argv)!=2 or re.fullmatch('[0-9a-f]{40}',sys.argv[1]) is None:raise SystemExit('Pass one verified merged-main SHA')
policy=load_policy()
c=json.loads((p/'reviewed-inputs.json').read_text());pro=json.loads((p/'release-provenance.json').read_text())
admit_rebind(c['publication_target'],sys.argv[1],policy)
c['publication_target']=pro['release_target']=policy['target_commit']
c['publication_tree']=pro['release_tree']=policy['target_tree']
pro['reviewed_ci_successor']={'baseline_commit':policy['base_commit'],'baseline_tree':policy['base_tree'],'reviewed_replacements':policy['replacements'],'production_payload_unchanged':True}
(p/'release-provenance.json').write_text(json.dumps(pro,indent=2,sort_keys=True)+'\n')
c['public_file_sha256']={n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in c['approved_public_files']}
(p/'reviewed-inputs.json').write_text(json.dumps(c,indent=2,sort_keys=True)+'\n')
print('Bound exact reviewed CI successor',sys.argv[1])
