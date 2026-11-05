#!/usr/bin/env python3
"""Publish an exact reviewed CI artifact; never rebuild, sign or execute its bytes."""
import argparse, hashlib, json, os, re, shutil, stat, subprocess, tempfile, zipfile
from pathlib import Path, PurePosixPath
ROOT = Path(__file__).resolve().parent
REPO = 'NRG-Wardog/sidestore-auto-refresh'
REPOSITORY_ID = 1350360026
from successor_contract import BASE_TREE, load_policy, verify_delta
TAG = 'v6.0.1'
BRANCH = 'refs/heads/validation/publication-v6.0.1'
TITLE = 'Unified LiveContainer + SideStore 6.0.1'
IPA = 'LiveContainer-SideStore-v6.0.1.ipa'
NOTES = 'RELEASE_NOTES_v6.0.1.md'
INVENTORY = 'publication-inventory.json'
PUBLIC_ASSETS = (IPA, 'SHA256SUMS', 'release-provenance.json')
SOURCE_FILES = {'SHA256SUMS','acquisition.json','cold-vpn-tests.json','database-recovery-package-admission.json','database-recovery-settings.json','database-recovery-tests.json','independent-audit.json','initial-rejection-package-admission.json','input-manifest.json','input-verification.json','issue56-startup-tests.json','multitask-launch-tests.json','owned-oda-package-admission.json','preservation-cms-package-admission.json','provenance.json','provider-ownership-package-admission.json','recompressed.ipa','refresh-truth-tests.json','result.json','startup-upgrade-tests.json'}
def require(ok, message):
    if not ok: raise ValueError(message)
def digest(raw): return hashlib.sha256(raw).hexdigest()
def json_bytes(value): return (json.dumps(value, indent=2, sort_keys=True)+'\n').encode()
def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key'); result[key] = value
    return result
def read_json(raw): return json.loads(raw, object_pairs_hook=strict_pairs)
def settings():
    c = read_json((ROOT/'reviewed-inputs.json').read_bytes())
    require(re.fullmatch('[0-9a-f]{40}', c['publication_target']) is not None, 'Unbound release target')
    require(c['repository'] == REPO and c['tag'] == TAG, 'Wrong release destination')
    policy = load_policy()
    require(c['publication_tree'] == policy['target_tree'] and c['publication_target'] == policy['target_commit'], 'Reviewed successor target differs')
    require(type(c['main_qualification']) is list and len(c['main_qualification']) == 3 and {r['id'] for r in c['main_qualification']} == {42011021356,42011021420,42011021439}, 'Main qualification run set differs')
    require(all(r['head_sha'] == policy['target_commit'] and r['tree_id'] == policy['target_tree'] and r['head_branch'] == 'main' for r in c['main_qualification']), 'Main qualification identity differs')
    require(c['approved_public_files'] == ['SHA256SUMS', NOTES, 'release-provenance.json'], 'Public metadata allowlist differs')
    require(set(c['public_file_sha256']) == set(c['approved_public_files']), 'Public metadata hashes differ')
    require(c['ipa_sha256'] == 'eaca3f28b32b4a14acc0b1bf16237348f528de18a6eed37552b80d65fb9f84d3' and c['ipa_size'] == 24409600, 'Reviewed IPA changed')
    for name in c['approved_public_files']:
        p = ROOT/name
        require(p.is_file() and not p.is_symlink(), 'Missing or linked reviewed metadata')
        require(digest(p.read_bytes()) == c['public_file_sha256'][name], 'Public metadata changed: '+name)
    provenance = read_json((ROOT/'release-provenance.json').read_bytes())
    require(provenance['release_target'] == c['publication_target'] and provenance['tag'] == TAG and provenance['release_tree'] == c['publication_tree'], 'Provenance target differs')
    return c
def context():
    require(os.environ.get('GITHUB_REPOSITORY') == REPO, 'Wrong repository')
    require(os.environ.get('GITHUB_REF') == BRANCH, 'Wrong publication branch')
    require(os.environ.get('GITHUB_EVENT_NAME') == 'push', 'Wrong event')
    sha = os.environ.get('GITHUB_SHA',''); require(re.fullmatch('[0-9a-f]{40}',sha), 'Invalid publication commit')
    return sha
def gh_json(endpoint, method='GET', data=None, optional=False):
    args = ['gh','api','--method',method,endpoint]
    if data is not None: args += ['--input','-']
    result = subprocess.run(args, input=json.dumps(data) if data is not None else None, capture_output=True, text=True)
    if result.returncode and optional and 'HTTP 404' in result.stderr: return None
    require(result.returncode == 0, 'GitHub operation failed: '+method+' '+endpoint+'; '+result.stderr.strip())
    return read_json(result.stdout)
def verify_target(config, commit):
    policy = load_policy()
    target = config['publication_target']
    require(target == policy['target_commit'] and config['publication_tree'] == policy['target_tree'], 'Reviewed successor target differs')
    main = gh_json(f'repos/{REPO}/git/ref/heads/main')
    require(main['object']['type'] == 'commit' and main['object']['sha'] == target, 'Main moved or differs from reviewed target')
    target_commit = gh_json(f'repos/{REPO}/git/commits/{target}')
    require(target_commit.get('sha') == target and target_commit.get('tree',{}).get('sha') == policy['target_tree'], 'Merged target tree differs from exact tested tree')
    verify_delta(gh_json(f'repos/{REPO}/git/trees/{BASE_TREE}?recursive=1'), gh_json(f'repos/{REPO}/git/trees/{policy["target_tree"]}?recursive=1'), policy)
    comparison = gh_json(f'repos/{REPO}/compare/{target}...{commit}')
    require(comparison.get('status') in ('ahead','identical') and comparison['base_commit']['sha'] == target and comparison['merge_base_commit']['sha'] == target, 'Publication commit does not descend from merged main')
    candidate = gh_json(f'repos/{REPO}/compare/{config["package"]["head_sha"]}...{target}')
    require(candidate.get('status') in ('ahead','identical') and candidate['merge_base_commit']['sha'] == config['package']['head_sha'], 'Merged main does not contain exact tested candidate')
def verify_run(run, expected):
    require(run.get('repository',{}).get('full_name') == REPO and run.get('head_repository',{}).get('full_name') == REPO, 'Wrong run repository')
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'Exact CI has not passed')
    for key in ('id','head_sha','head_branch','workflow_id','path','run_attempt'):
        require(run.get(key) == expected[key], 'Run identity differs: '+key)
    require(run.get('head_commit',{}).get('tree_id') == expected['tree_id'], 'Run tree differs')
def verify_artifact(artifact, config):
    for key in ('id','name','size_in_bytes','digest'):
        require(artifact.get(key) == config['artifact'][key], 'Artifact identity differs: '+key)
    require(artifact.get('expired') is False, 'Artifact expired or unknown')
    run = artifact.get('workflow_run',{}); require(type(run) is dict, 'Artifact run missing')
    require(run.get('id') == config['package']['id'] and run.get('head_sha') == config['package']['head_sha'] and run.get('head_branch') == config['package']['head_branch'], 'Artifact run differs')
    require(run.get('repository_id') == REPOSITORY_ID and run.get('head_repository_id') == REPOSITORY_ID, 'Artifact owner differs')
def extract_ipa(archive, output, c):
    raw = Path(archive).read_bytes()
    require(len(raw) == c['artifact']['size_in_bytes'] and 'sha256:'+digest(raw) == c['artifact']['digest'], 'Artifact archive differs')
    with zipfile.ZipFile(archive) as z:
        rows = z.infolist(); names = [r.filename for r in rows]
        require(len(names) == len(set(names)) and set(names) == SOURCE_FILES, 'Artifact inventory differs')
        require(sum(r.file_size for r in rows) < 128*1024**2, 'Oversized artifact inventory')
        for row in rows:
            require(PurePosixPath(row.filename).name == row.filename and '\\' not in row.filename and row.filename not in ('.','..'), 'Unsafe artifact name')
            require(stat.S_IFMT(row.external_attr >> 16) in (0,stat.S_IFREG) and not row.flag_bits & 1 and row.file_size < 64*1024**2, 'Unsafe artifact member')
        require(z.testzip() is None, 'Artifact CRC failure')
        ipa = z.read('recompressed.ipa')
        require(len(ipa) == c['ipa_size'] and digest(ipa) == c['ipa_sha256'], 'Reviewed IPA bytes differ')
        acquisition = read_json(z.read('acquisition.json'))
        require(acquisition['packaging_identity'] == {'GITHUB_SHA':c['package']['head_sha'],'GITHUB_RUN_ID':str(c['package']['id']),'GITHUB_RUN_ATTEMPT':str(c['package']['run_attempt'])}, 'Package receipt identity differs')
        native = read_json(z.read('input-manifest.json'))
        require(native['commit'] == c['native']['head_sha'] and native['run_id'] == c['native']['id'] and native['run_attempt'] == c['native']['run_attempt'], 'Native input differs')
        require(native['ipa_sha256'] == c['native_ipa_sha256'] and native['ipa_bytes'] == c['native_ipa_size'] and native['entries'] == 274, 'Native IPA identity differs')
        provenance = read_json(z.read('provenance.json'))
        require(provenance['workflow_commit'] == c['native']['head_sha'] and str(provenance['native_run_id']) == str(c['native']['id']) and str(provenance['native_run_attempt']) == str(c['native']['run_attempt']), 'Native provenance differs')
        audit = read_json(z.read('independent-audit.json')); result = read_json(z.read('result.json'))
        require(audit['status'] == 'PASS_IDENTICAL_PAYLOAD_AND_ARCHIVE_METADATA', 'Package audit failed')
        for key in ('all_payload_bytes_identical','all_binary_signature_bytes_identical','all_archive_metadata_preserved_except_compressed_sizes_and_offsets'):
            require(audit[key] is True, 'Package audit incomplete: '+key)
        for receipt in (audit,result):
            require(receipt['final_bytes'] == len(ipa) and receipt['final_sha256'] == digest(ipa), 'Final package receipt differs')
        require(z.read('SHA256SUMS').decode().split() == [c['ipa_sha256'],'recompressed.ipa'], 'CI checksum differs')
        Path(output).write_bytes(ipa)
def make_bundle(archive, folder, c, commit):
    folder = Path(folder); require(not folder.exists(), 'Bundle path already exists'); folder.mkdir(parents=True)
    extract_ipa(archive, folder/IPA, c)
    for name in c['approved_public_files']: shutil.copyfile(ROOT/name,folder/name)
    require((folder/'SHA256SUMS').read_text().split() == [c['ipa_sha256'],IPA], 'Public checksum differs')
    inventory = {'repository':REPO,'tag':TAG,'publication_commit':commit,'release_target':c['publication_target'],'files':{name:{'sha256':digest((folder/name).read_bytes()),'size':(folder/name).stat().st_size} for name in (*PUBLIC_ASSETS,NOTES)}}
    (folder/INVENTORY).write_bytes(json_bytes(inventory)); return digest((folder/INVENTORY).read_bytes())
def verify_bundle(folder, expected_hash, c, commit):
    folder = Path(folder); require({p.name for p in folder.iterdir()} == set((*PUBLIC_ASSETS,NOTES,INVENTORY)), 'Bundle inventory differs')
    require(all(p.is_file() and not p.is_symlink() for p in folder.iterdir()), 'Bundle contains linked or nonregular file')
    require(digest((folder/INVENTORY).read_bytes()) == expected_hash, 'Bundle inventory hash differs')
    inv = read_json((folder/INVENTORY).read_bytes())
    require(set(inv) == {'repository','tag','publication_commit','release_target','files'}, 'Inventory keys differ')
    require(inv['repository'] == REPO and inv['tag'] == TAG and inv['publication_commit'] == commit and inv['release_target'] == c['publication_target'], 'Bundle binding differs')
    require(set(inv['files']) == set((*PUBLIC_ASSETS,NOTES)), 'Public asset allowlist differs')
    for name, info in inv['files'].items():
        raw = (folder/name).read_bytes(); require(info == {'sha256':digest(raw),'size':len(raw)}, 'Bundle file changed')
    require(inv['files'][IPA] == {'sha256':c['ipa_sha256'],'size':c['ipa_size']}, 'Bundle IPA differs')
    for name in c['approved_public_files']: require(inv['files'][name]['sha256'] == c['public_file_sha256'][name], 'Public metadata unreviewed')
    return inv
def lookup_release():
    matches = []
    for page in range(1,11):
        rows = gh_json(f'repos/{REPO}/releases?per_page=100&page={page}')
        require(type(rows) is list, 'Invalid releases collection')
        matches += [r for r in rows if r.get('tag_name') == TAG]
        if len(rows) < 100: break
    else: raise ValueError('Release lookup limit exceeded')
    require(len(matches) <= 1, 'Ambiguous existing release')
    return matches[0] if matches else None
def tag_commit():
    ref = gh_json(f'repos/{REPO}/git/ref/tags/{TAG}',optional=True)
    if ref is None: return None
    obj = ref['object']
    for _ in range(4):
        if obj['type'] == 'commit': return obj['sha']
        require(obj['type'] == 'tag', 'Wrong tag object type'); obj = gh_json(f'repos/{REPO}/git/tags/{obj["sha"]}')['object']
    raise ValueError('Tag nesting exceeded')
def validate_release(release, inv, notes, draft, complete):
    require(release.get('tag_name') == TAG and release.get('target_commitish') == inv['release_target'] and release.get('name') == TITLE, 'Existing release identity differs')
    require(type(release.get('body')) is str and release['body'].strip() == notes.strip() and release.get('prerelease') is False and release.get('draft') is draft, 'Existing release notes/state differs')
    rows = release.get('assets',[]); names = [a['name'] for a in rows]
    require(len(names) == len(set(names)) and set(names) <= set(PUBLIC_ASSETS), 'Existing asset allowlist differs')
    if complete: require(set(names) == set(PUBLIC_ASSETS), 'Release incomplete')
    for row in rows:
        info = inv['files'][row['name']]
        require(row.get('state') == 'uploaded' and row.get('size') == info['size'] and row.get('digest') == 'sha256:'+info['sha256'], 'Existing asset bytes differ')
    return set(names)
def prepare(folder):
    commit,c = context(),settings(); verify_target(c,commit)
    for name in ('native','package','maintenance'): verify_run(gh_json(f'repos/{REPO}/actions/runs/{c[name]["id"]}'),c[name])
    for expected in c['main_qualification']: verify_run(gh_json(f'repos/{REPO}/actions/runs/{expected["id"]}'),expected)
    verify_artifact(gh_json(f'repos/{REPO}/actions/artifacts/{c["artifact"]["id"]}'),c)
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp)/'artifact.zip'
        with archive.open('wb') as out: subprocess.run(['gh','api',f'repos/{REPO}/actions/artifacts/{c["artifact"]["id"]}/zip'],stdout=out,check=True)
        hash_ = make_bundle(archive,folder,c,commit)
    verify_bundle(folder,hash_,c,commit)
    with open(os.environ['GITHUB_OUTPUT'],'a') as out: out.write('inventory_sha256='+hash_+'\n')
    print('Verified exact native/package/maintenance and three post-merge main runs; unchanged reviewed IPA.')
def publish(folder, inventory_sha):
    commit,c = context(),settings(); verify_target(c,commit); inv = verify_bundle(folder,inventory_sha,c,commit); notes = (Path(folder)/NOTES).read_text()
    require(tag_commit() in (None,c['publication_target']), 'Existing tag targets different source')
    release = lookup_release()
    if release is None:
        # One create attempt only. An uncertain response is reconciled via authenticated listing.
        try: release = gh_json(f'repos/{REPO}/releases','POST',{'tag_name':TAG,'target_commitish':c['publication_target'],'name':TITLE,'body':notes,'draft':True,'prerelease':False,'make_latest':'false'})
        except ValueError:
            release = lookup_release()
            if release is None: raise
    if release.get('draft') is False:
        validate_release(release,inv,notes,False,True)
        require(tag_commit() == c['publication_target'], 'Published tag differs')
        require(gh_json(f'repos/{REPO}/releases/latest')['id'] == release['id'], 'Published release is not latest')
        print(release['html_url']); return
    present = validate_release(release,inv,notes,True,False); rid = release['id']
    for name in PUBLIC_ASSETS:
        if name in present: continue
        result = subprocess.run(['gh','api','--method','POST',f'https://uploads.github.com/repos/{REPO}/releases/{rid}/assets?name={name}','--header','Content-Type: '+('application/json' if name.endswith('.json') else 'application/octet-stream'),'--input',str(Path(folder)/name)],capture_output=True,text=True)
        # Always observe after upload, including an uncertain response. Never replace/retry an asset.
        release = gh_json(f'repos/{REPO}/releases/{rid}')
        present = validate_release(release,inv,notes,True,False)
        require(name in present, 'Upload not verified; stopped without retry: '+name)
    verify_target(c,commit)
    validate_release(gh_json(f'repos/{REPO}/releases/{rid}'),inv,notes,True,True)
    require(tag_commit() in (None,c['publication_target']), 'Tag changed before publication')
    try: gh_json(f'repos/{REPO}/releases/{rid}','PATCH',{'draft':False,'prerelease':False,'make_latest':'true'})
    except ValueError:
        # Only read reconciliation; never repeat an uncertain publication mutation.
        observed = gh_json(f'repos/{REPO}/releases/{rid}')
        validate_release(observed,inv,notes,False,True)
    published = gh_json(f'repos/{REPO}/releases/{rid}'); validate_release(published,inv,notes,False,True)
    require(tag_commit() == c['publication_target'], 'Published tag differs')
    require(gh_json(f'repos/{REPO}/releases/latest')['id'] == rid, 'Corrected release not latest')
    print(published['html_url'])
if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('operation',choices=('prepare','publish'));p.add_argument('folder');p.add_argument('--inventory-sha256');a=p.parse_args()
    if a.operation == 'prepare': prepare(a.folder)
    else:
        require(re.fullmatch('[0-9a-f]{64}',a.inventory_sha256 or ''),'Missing inventory digest');publish(a.folder,a.inventory_sha256)
