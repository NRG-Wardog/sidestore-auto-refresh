#!/usr/bin/env python3
"""Explicit source-bound RemotePairingKit overlay in user-owned minimuxer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import build
import install_ios

HERE=Path(__file__).resolve().parent
PIN='e3f70d16c0c551540a533a39d540e78e5b0a60a8'
TREE='007ba009bb6dbb4f2149da8e918a0dcd02b1a4e2'
MANIFEST_BLOB='0176e1cd70b13481939eb282d081167a43a5bf17'
OLD='''            url: "https://github.com/krzyzanowskim/OpenSSL/releases/download/3.6.2000/OpenSSL.xcframework.zip#RemotePairingKit",
            checksum: "37846a8bd302cb2443eff47f1045ab844d0cd40bf82cc6159cfad9aa5c3eff9e"'''
NEW='''            path: "GeneratedDependencies/OpenSSL35/OpenSSL.xcframework"'''

RESOLVER_SCAFFOLD={'.swiftpm': {'mode':0o755,'children':['xcode']}, '.swiftpm/xcode': {'mode':0o755,'children':[]}}

def sha(data):return hashlib.sha256(data).hexdigest()
def git(root,*args):
    env=dict(os.environ);env.update(GIT_NO_LAZY_FETCH='1',GIT_NO_REPLACE_OBJECTS='1',GIT_TERMINAL_PROMPT='0',GIT_ALLOW_PROTOCOL='')
    return subprocess.check_output(['git','-C',str(root),*args],env=env,timeout=30)

def object_hash(kind,data):return hashlib.sha1(kind.encode()+b' '+str(len(data)).encode()+b'\0'+data).hexdigest()

def tree_hash(entries):
    tree={}
    for name,entry in entries.items():
        parts=Path(name).parts
        if not parts or Path(name).is_absolute() or '..' in parts:raise ValueError('Unsafe source inventory path')
        cursor=tree
        for part in parts[:-1]:
            cursor=cursor.setdefault(part,{})
            if not isinstance(cursor,dict):raise ValueError('Source file/directory collision')
        if parts[-1] in cursor:raise ValueError('Duplicate source inventory path')
        if entry['mode'] not in ('100644','100755') or len(entry['git_blob'])!=40:raise ValueError('Unexpected source inventory entry')
        cursor[parts[-1]]=(entry['mode'],entry['git_blob'])
    def encode(node):
        rows=[]
        for name,value in node.items():
            if isinstance(value,dict):mode,oid='40000',encode(value)
            else:mode,oid=value
            rows.append((name+('/' if mode=='40000' else ''),mode.encode()+b' '+name.encode()+b'\0'+bytes.fromhex(oid)))
        return object_hash('tree',b''.join(raw for _,raw in sorted(rows)))
    return encode(tree)

def patch_manifest(original):
    # Exact pinned blob plus exact single delta, not a best-effort text rewrite.
    blob=hashlib.sha1(b'blob '+str(len(original)).encode()+b'\0'+original).hexdigest()
    if blob!=MANIFEST_BLOB:raise ValueError('Pinned RemotePairingKit manifest differs')
    text=original.decode()
    if text.count(OLD)!=1:raise ValueError('Expected exactly one binary target delta')
    return text.replace(OLD,NEW).encode()

def source_entries(upstream):
    if git(upstream,'rev-parse','HEAD').decode().strip()!=PIN:raise ValueError('RemotePairingKit source commit differs')
    tree=git(upstream,'rev-parse','HEAD^{tree}').decode().strip();entries={}
    if tree!=TREE:raise ValueError('RemotePairingKit source tree differs')
    for row in git(upstream,'ls-tree','-r','-z','HEAD').split(b'\0'):
        if not row:continue
        metadata,rawname=row.split(b'\t',1);mode,kind,oid=metadata.decode().split();name=rawname.decode()
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or name.startswith('GeneratedDependencies/') or name=='generated-provenance.json':raise ValueError('Unsafe or colliding upstream source path')
        if kind!='blob' or mode not in ('100644','100755'):raise ValueError('Upstream special entry requires review')
        entries[name]={'git_blob':oid,'mode':mode}
    if entries.get('Package.swift',{}).get('git_blob')!=MANIFEST_BLOB:raise ValueError('Upstream manifest blob differs')
    return tree,entries

def prepare_resolver_scaffold(overlay):
    # The exact app resolver was observed to create these two empty directories.
    # Create them with restrictive, stable modes BEFORE sealing; never normalize
    # an observed post-resolution mutation or admit arbitrary package caches.
    for name in RESOLVER_SCAFFOLD:
        path=overlay/name
        path.mkdir(mode=0o755)
        path.chmod(0o755)
    validate_resolver_scaffold(overlay)

def validate_resolver_scaffold(overlay):
    for name,expected in RESOLVER_SCAFFOLD.items():
        path=overlay/name
        if not path.exists() or path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError('Resolver scaffold missing or linked')
        if stat.S_IMODE(path.lstat().st_mode)!=expected['mode']:
            raise ValueError('Resolver scaffold mode changed')
        if sorted(child.name for child in path.iterdir())!=expected['children']:
            raise ValueError('Resolver scaffold contents changed')

def validate_embedded(minimuxer):
    root=Path(minimuxer).resolve(strict=True);overlay=root/'GeneratedDependencies/RemotePairingKit'
    if overlay.is_symlink() or overlay.parent.is_symlink():raise ValueError('Symlinked embedded overlay')
    receipt_path=overlay/'generated-provenance.json'
    if receipt_path.is_symlink():raise ValueError('Symlinked overlay provenance')
    receipt=json.loads(receipt_path.read_text())
    if receipt.get('schema')!='issue53-embedded-source-overlay-v1' or receipt.get('source_commit')!=PIN or receipt.get('source_tree')!=TREE or receipt.get('upstream_manifest_blob')!=MANIFEST_BLOB:
        raise ValueError('Unexpected embedded overlay identity')
    if receipt.get('adapter_sha256')!=build.digest(Path(__file__)):raise ValueError('Embedded adapter recipe differs')
    if receipt.get('resolver_scaffold')!=RESOLVER_SCAFFOLD:raise ValueError('Resolver scaffold policy differs')
    validate_resolver_scaffold(overlay)
    if receipt.get('source_repository')!='https://github.com/mahee96/RemotePairingKit.git' or any(receipt.get(key) is not False for key in ['upstream_source_modified_except_manifest','host_library_deduplicated','runtime_loader_isolation_proven']):
        raise ValueError('Unexpected upstream origin or qualification claim')
    manifest=root/'DeviceGateway/Package.swift'
    if '.package(path: "../GeneratedDependencies/RemotePairingKit")' not in manifest.read_text() or build.digest(manifest)!=receipt.get('consumer_manifest_sha256'):
        raise ValueError('Consumer has not selected exact local pairing overlay')
    nested=receipt['generated_build_receipt'];install_ios.validate_receipt(nested)
    if sha((json.dumps(nested,indent=2)+'\n').encode())!=receipt.get('generated_build_receipt_sha256'):raise ValueError('Embedded build receipt identity differs')
    actual=build.inventory(overlay);actual.pop('generated-provenance.json',None)
    if actual!=receipt.get('overlay_inventory'):raise ValueError('Embedded overlay bytes differ')
    generated=overlay/'GeneratedDependencies/OpenSSL35'
    if build.inventory(generated)!=nested['generated_inventory']:raise ValueError('Embedded generated library bytes differ')
    originals=receipt['upstream_files']
    commit_object=receipt['upstream_commit_object'].encode()
    if object_hash('commit',commit_object)!=PIN or commit_object.splitlines()[0]!=('tree '+tree_hash(originals)).encode() or receipt.get('source_tree')!=tree_hash(originals):
        raise ValueError('Upstream source inventory does not reconstruct pinned commit tree')
    if receipt.get('manifest_delta')!={'before':OLD,'after':NEW,'replacements':1}:
        raise ValueError('Unexpected manifest overlay delta')
    after=(overlay/'Package.swift').read_bytes()
    if after.count(NEW.encode())!=1:raise ValueError('Ambiguous overlay manifest')
    before=after.replace(NEW.encode(),OLD.encode())
    if patch_manifest(before)!=after or sha(before)!=receipt.get('manifest_before_sha256'):
        raise ValueError('Manifest delta does not reconstruct original blob')
    if receipt.get('manifest_after_sha256')!=build.digest(overlay/'Package.swift'):raise ValueError('Overlay manifest identity differs')
    for name,entry in originals.items():
        path=overlay/name
        original=before if name=='Package.swift' else path.read_bytes()
        if sha(original)!=entry['sha256'] or object_hash('blob',original)!=entry['git_blob']:raise ValueError('Unmodified pairing source differs')
        if path.stat().st_mode&0o777!=int(entry['mode'],8)&0o777:raise ValueError('Pairing source mode differs')
    if set(actual)!=(set(originals)|{'GeneratedDependencies/OpenSSL35/'+name for name in nested['generated_inventory']}):raise ValueError('Overlay source/generated closure differs')
    return generated,receipt

def stage(built,upstream,minimuxer):
    generated,build_receipt=install_ios.validate(built)
    upstream=Path(upstream).resolve(strict=True);root=Path(minimuxer).resolve(strict=True)
    manifest=root/'DeviceGateway/Package.swift'
    if '.package(path: "../GeneratedDependencies/RemotePairingKit")' not in manifest.read_text():raise ValueError('Explicit local dependency selection required')
    tree,entries=source_entries(upstream)
    overlay=root/'GeneratedDependencies/RemotePairingKit'
    if overlay.exists() or overlay.is_symlink() or overlay.parent.is_symlink():raise ValueError('Refusing existing/symlinked embedded overlay')
    overlay.mkdir(parents=True)
    total=0;original_manifest=None
    for name,entry in entries.items():
        data=git(upstream,'cat-file','blob',entry['git_blob']);total+=len(data)
        if total>300_000_000:raise ValueError('Reviewed source staging size bound exceeded')
        entry['sha256']=sha(data);path=overlay/name;path.parent.mkdir(parents=True,exist_ok=True)
        if name=='Package.swift':original_manifest=data;data=patch_manifest(data)
        path.write_bytes(data);path.chmod(int(entry['mode'],8)&0o777)
    if original_manifest is None:raise ValueError('Missing pairing manifest')
    shutil.copytree(generated,overlay/'GeneratedDependencies/OpenSSL35')
    prepare_resolver_scaffold(overlay)
    receipt={'schema':'issue53-embedded-source-overlay-v1','source_repository':'https://github.com/mahee96/RemotePairingKit.git',
        'source_commit':PIN,'source_tree':tree,'upstream_commit_object':git(upstream,'cat-file','commit',PIN).decode(),'upstream_manifest_blob':MANIFEST_BLOB,
        'adapter_sha256':build.digest(Path(__file__)),'consumer_manifest_sha256':build.digest(manifest),
        'upstream_files':entries,'manifest_before_sha256':sha(original_manifest),'manifest_after_sha256':build.digest(overlay/'Package.swift'),
        'manifest_delta':{'before':OLD,'after':NEW,'replacements':1},
        'generated_build_receipt':build_receipt,'generated_build_receipt_sha256':build.digest(Path(built).resolve()/'ios-build-receipt.json'),
        'overlay_inventory':build.inventory(overlay),'resolver_scaffold':RESOLVER_SCAFFOLD,'upstream_source_modified_except_manifest':False,
        'host_library_deduplicated':False,'runtime_loader_isolation_proven':False}
    (overlay/'generated-provenance.json').write_text(json.dumps(receipt,indent=2)+'\n')
    validate_embedded(root)
    return overlay

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--built',type=Path,required=True);parser.add_argument('--upstream',type=Path,required=True);parser.add_argument('--minimuxer',type=Path,required=True)
    args=parser.parse_args();print(stage(args.built,args.upstream,args.minimuxer))
