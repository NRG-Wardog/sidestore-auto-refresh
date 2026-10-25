"""One-time review aid, never a CI input. Reconstruct baseline metadata from OLD evidence.

Inputs are explicit checkout roots; only the output directory is written. This is
not an updater and refuses to overwrite an existing registry. A generated digest
is a review candidate, not authority to accept the newly generated set.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

BASELINE = '141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee'
SET = 'runtime-source-parity-141776ba.v1'
VER = '1.0.0'
BASES = {
 'LiveContainer': ('LiveContainer/LiveContainer', '12377cf3b91d51739a33f14a302e5f522b238593'),
 'SideStore': ('SideStore/SideStore', 'ff25922e5c13ccfafd83bda5092910d848ebd409'),
 'AnisetteKit': ('mahee96/AnisetteKit', '1f5a7e36553cc865b873f222b87a6486c0bcc7bf'),
 'SideSign': ('SideStore/SideSign', 'a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d'),
 'minimuxer': ('SideStore/minimuxer', '98c3c79982f813878e922ab42f9545314a700f0c'),
 'idevice': ('SideStore/idevice', 'ebd7dadfc55d1c4facee3d11ecf5b28e20548b57'),
 'jktcp': ('SideStore/jktcp', 'e674e1eee6d5943e13b1eba0bd24a9dd0b2fa020'),
}
# These are build metadata identities, not new wire constants or protocol fields.
CONTRACTS = {
 'app-group-identity': ('Shared App Group selection, fail-closed storage and cross-process handoff; duplicated runtime implementations remain frozen.', ['LiveContainer','SideStore']),
 'xpc-service': ('RefreshServer/RefreshClient selectors, primitive NSData command transport, launch/readiness and embedded service dispatch.', ['LiveContainer','SideStore']),
 'refresh-results': ('Refresh run correlation, result verification, persistent evidence and host/service refresh completion.', ['LiveContainer','SideStore']),
 'auth-service-messages': ('Versioned command envelope, auth sessions, prompt/secret handoff, cancellation and provisioning resume state.', ['LiveContainer','SideStore']),
 'diagnostics': ('Frozen finite error, trace and diagnostic producers/consumers; no claim that preserved logging or dormant paths are newly repaired.', ['LiveContainer','SideStore','AnisetteKit','SideSign','minimuxer','idevice','jktcp']),
 'anisette-provider': ('Anisette Swift/native API, data/header conversion and preserved checked-staging/native-trace behavior; automatic recovery remains disabled at its caller.', ['AnisetteKit']),
 'sidesign-auth': ('DeveloperPortal authentication, verification callback, auth models, anisette conversion and preserved account/provisioning error semantics.', ['SideSign']),
 'transport-capabilities': ('Refresh transport selection, pairing/connection modes, backend capability and CoreDevice initialization/lease behavior.', ['minimuxer']),
 'idevice-ffi': ('CoreDevice/AFC/tunnel C FFI exports, feature gates and lifecycle surface consumed by the Swift gateway.', ['idevice']),
 'jktcp-stream': ('Adapter/handle TCP lifecycle, bounded zero-window persistence and error propagation consumed by idevice.', ['jktcp']),
}
BINDINGS = {o:{} for o in BASES}
def bind(owner, contract, *paths):
 BINDINGS[owner].setdefault(contract, set()).update(paths)

lc='SideStoreSupport/SideStore.swift'
ss='AltStore/AppDelegate.swift'
for c in ['app-group-identity','xpc-service','refresh-results','auth-service-messages','diagnostics']:
 bind('LiveContainer',c,lc)
 bind('SideStore',c,ss)
bind('LiveContainer','app-group-identity','LiveContainer/LCAppGroupIdentityRules.h','LiveContainer/LCAppGroupSelectionPolicy.h','LiveContainer/LCSharedUtils.m','LiveContainerSwiftUI/Utilities/V3SharedAppGroup.swift','LiveProcess/main.m','SideStoreSupport/XPCClient.m','SideStoreSupport/SideStoreHooks.m','LiveContainer/LCContainerStorage.h')
bind('SideStore','app-group-identity','AltStore/Core/Components/Keychain.swift','AltStore/Managing Apps/AppManager.swift','SideStore/Core/Pairing/PairingFileManager.swift','SideStore/Core/Anisette/AnisetteProvider.swift','SideStore/Core/Anisette/OnDeviceAnisetteManager.swift')
bind('LiveContainer','xpc-service','SideStoreSupport/XPCServer.h','SideStoreSupport/XPCServer.m','SideStoreSupport/XPCClient.m','SideStoreSupport/SideStoreClient.swift','LiveProcess/main.m','LaunchAppExtension/LaunchAppExtension.swift')
bind('SideStore','xpc-service','SideStore/AppBootManager.swift','AltStore/Intents/App Intents/RefreshAllAppsIntent.swift')
bind('LiveContainer','refresh-results','SideStoreSupport/XPCServer.h','SideStoreSupport/XPCClient.m','SideStoreSupport/SideStoreClient.swift','LiveContainerSwiftUI/App/AppDelegate.swift','LiveContainerSwiftUI/App/LiveContainerAutoRefreshAlarm.swift','LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift')
bind('SideStore','refresh-results','AltStore/Core/Model/RefreshAttempt.swift','AltStore/Intents/App Intents/RefreshAllAppsIntent.swift','AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift','AltStore/Managing Apps/AppManager.swift','SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift','SideStore/Core/Operations/PipelineExecutor.swift')
bind('SideStore','auth-service-messages','SideStore/Core/Auth/AuthManager.swift','SideStore/Core/Auth/DeveloperPortalProxy.swift','SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift','AltStore/Core/Components/Keychain.swift')
bind('LiveContainer','auth-service-messages','SideStoreSupport/XPCServer.h','SideStoreSupport/XPCClient.m')
bind('SideStore','diagnostics','SideStore/Core/Logging/OperationLogging.swift','SideStore/Core/Logging/SideStoreLogging.swift','SideStore/Utils/iostreams/ConsoleLog.swift','SideStore/Core/Anisette/OnDeviceAnisetteManager.swift','SideStore/Core/Auth/AuthManager.swift','SideStore/Core/Auth/DeveloperPortalProxy.swift')
bind('SideStore','anisette-provider','SideStore/Core/Anisette/AnisetteProvider.swift','SideStore/Core/Anisette/OnDeviceAnisetteManager.swift','SideStore/Core/Anisette/AnisetteConfigManager.swift')
bind('SideStore','sidesign-auth','SideStore/Core/Auth/AuthManager.swift','SideStore/Core/Auth/DeveloperPortalProxy.swift','SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift')
bind('SideStore','transport-capabilities','SideStore/Core/DeviceApi/MinimuxerWrapper.swift','SideStore/Core/DeviceApi/ConnectionConfig.swift','SideStore/Core/Pairing/PairingFileManager.swift','SideStore/Core/Operations/PipelineExecutor.swift','SideStore/Views/Settings/Advanced/Connection/ConnectionConfig.swift')
for c in ['sidesign-auth','diagnostics']:
 bind('SideSign',c,'Sources/DeveloperPortal/Authentication.swift','Sources/DeveloperPortal/DeveloperPortalAPI.swift','Sources/DeveloperPortal/AuthModels.swift','Sources/DeveloperPortal/AppIDs.swift','Sources/Logging.swift','Sources/Models/AnisetteData.swift')
bind('SideSign','anisette-provider','Sources/Anisette/AnisetteDataManager.swift','Sources/Anisette/RemoteAnisetteDataProvider.swift','Sources/Models/AnisetteData.swift','Sources/Constants.swift')
for c in ['transport-capabilities','idevice-ffi','diagnostics']:
 bind('minimuxer',c,'Common/PairingFile.swift','DeviceGateway/BaseDeviceGateway.swift','DeviceGateway/DeviceGatewayAPI.swift','DeviceGateway/idevice/IdeviceGateway.swift','Sources/MinimuxerApi.swift','Sources/MinimuxerImpl.swift','Sources/RefreshTransportPolicy.swift','Sources/Services/HeartbeatService.swift','Sources/Services/NetworkObserverService.swift')
bind('minimuxer','idevice-ffi','DeviceGateway/Package.swift')
for c in ['idevice-ffi','diagnostics']:
 bind('idevice',c,'ffi/src/afc.rs','ffi/src/core_device_proxy.rs','ffi/src/lib.rs','ffi/src/tunnel_provider.rs','ffi/Cargo.toml','idevice/Cargo.toml','idevice/src/tunnel.rs')
bind('idevice','jktcp-stream','idevice/src/tunnel.rs','idevice/Cargo.toml','ffi/src/tunnel_provider.rs','ffi/src/core_device_proxy.rs')
for c in ['jktcp-stream','diagnostics']:
 bind('jktcp',c,'src/adapter.rs','src/handle.rs','src/lib.rs','src/stream.rs','Cargo.toml')
# Edges are exact approvals, not semver ranges or runtime negotiation.
EDGES=[]
def edge(consumer,provider,contract):
 EDGES.append({'consumer':consumer,'provider':provider,'contract':contract,'version':VER})
for c in ['app-group-identity','xpc-service','refresh-results','auth-service-messages','diagnostics']:
 edge('LiveContainer','SideStore',c)
 edge('SideStore','LiveContainer',c)
for consumer in ['SideStore','SideSign']:
 edge(consumer,'AnisetteKit','anisette-provider')
 edge(consumer,'AnisetteKit','diagnostics')
edge('SideStore','SideSign','sidesign-auth')
edge('SideStore','SideSign','diagnostics')
edge('SideStore','minimuxer','transport-capabilities')
edge('SideStore','minimuxer','diagnostics')
edge('minimuxer','idevice','idevice-ffi')
edge('minimuxer','idevice','diagnostics')
edge('idevice','jktcp','jktcp-stream')
edge('idevice','jktcp','diagnostics')

def sha(data): return hashlib.sha256(data).hexdigest()
def write(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True)
 raw=(json.dumps(obj,indent=2,sort_keys=True)+'\n').encode()
 path.write_bytes(raw)
 return sha(raw)

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--old-root',required=True,type=Path)
 p.add_argument('--fork-root',required=True,type=Path)
 p.add_argument('--inventory-root',required=True,type=Path)
 p.add_argument('--output',required=True,type=Path)
 a=p.parse_args()
 if (a.output/'compatibility-registry.json').exists():
  raise SystemExit('Refusing to replace existing registry; use a new review directory.')
 inventories={name:json.loads((a.inventory_root/name).read_text()) for name in ['combined-generated-source-inventory.json','transport-generated-source-inventory.json','anisette-source-parity.json']}
 changed={}
 for name in ['combined-generated-source-inventory.json','transport-generated-source-inventory.json']:
  assert inventories[name]['baseline']==BASELINE
  for record in inventories[name]['final_changes']:
   changed[record['file']]=(record['after'],name)
 ani=inventories['anisette-source-parity.json']
 assert ani['integration_baseline']==BASELINE
 ani_files={r['path']:r for r in ani['files']}
 for c in ['anisette-provider','diagnostics']:
  for path in ani_files:
   if path.startswith(('Sources/','Native/')):bind('AnisetteKit',c,path)
 roots={o:a.old_root/o for o in BASES}
 roots.update({'AnisetteKit':a.fork_root/'AnisetteKit','SideSign':a.old_root/'SideStore/Dependencies/SideSign','minimuxer':a.old_root/'SideStore/Dependencies/minimuxer'})
 evidence={'format_version':1,'integration_baseline':BASELINE,'inventory_digests':{n:sha((a.inventory_root/n).read_bytes()) for n in inventories},'owners':{}}
 reg={'format_version':1,'contract_set':SET,'integration_baseline':BASELINE,'owners':{},'contracts':{c:{'version':VER,'description':d,'providers':sorted(owners)} for c,(d,owners) in CONTRACTS.items()},'edges':sorted(EDGES,key=lambda e:(e['consumer'],e['provider'],e['contract']))}
 for owner,(repo,commit) in BASES.items():
  paths=sorted(set().union(*BINDINGS[owner].values()))
  records=[]
  for path in paths:
   raw=(roots[owner]/path).read_bytes()
   h=sha(raw)
   actual_mode='100755' if (roots[owner]/path).stat().st_mode&0o111 else '100644'
   key=owner+':'+path
   if owner=='AnisetteKit':
    ref=ani_files[path]; assert h==ref['maintained_sha256'],key
    mode=ref['mode'];basis='anisette-source-parity.json:files:'+path
   elif key in changed:
    ref,name=changed[key];assert h==ref['sha256'],key
    mode='100755' if int(ref['mode'],8)&0o111 else '100644';basis=name+':final_changes:'+key
   else:
    upstream=subprocess.check_output(['git','-C',str(a.fork_root/owner),'show',commit+':'+path])
    assert upstream==raw,key
    mode=subprocess.check_output(['git','-C',str(a.fork_root/owner),'ls-tree',commit,'--',path],text=True).split()[0]
    basis=repo+'@'+commit+':'+path+' (unchanged upstream source)'
   assert actual_mode==mode,key
   records.append({'path':path,'sha256':h,'mode':mode,'basis':basis})
  prov={'integration_baseline':BASELINE,'upstream_repository':repo,'upstream_commit':commit,'source_basis':('Reviewed AnisetteKit parity source report; runtime files only.' if owner=='AnisetteKit' else 'Immutable OLD prepared source; changed files checked against baseline generation inventories; unchanged contract surfaces checked against exact upstream Git blobs.')}
  manifest={'format_version':1,'owner':owner,'contract_set':SET,'version':VER,'provenance':prov,'sources':records,'provides':[],'requires':[]}
  for c,(_,providers) in CONTRACTS.items():
   if owner in providers:
    manifest['provides'].append({'contract':c,'version':VER,'source_paths':sorted(BINDINGS[owner][c])})
  for e in EDGES:
   if e['consumer']==owner:
    manifest['requires'].append({'provider':e['provider'],'contract':e['contract'],'version':VER,'source_paths':sorted(BINDINGS[owner][e['contract']])})
  manifest['provides'].sort(key=lambda r:r['contract'])
  manifest['requires'].sort(key=lambda r:(r['provider'],r['contract']))
  evidence['owners'][owner]={'provenance':prov,'sources':records}
  rel='owners/'+owner+'/runtime-contract.json'
  reg['owners'][owner]={'manifest_path':rel,'manifest_sha256':write(a.output/rel,manifest),'version':VER}
 evpath='provenance/source-evidence.json'
 reg['evidence']={'path':evpath,'sha256':write(a.output/evpath,evidence)}
 h=write(a.output/'compatibility-registry.json',reg)
 print(json.dumps({'candidate_registry_sha256':h,'owners':{o:len(e['sources']) for o,e in evidence['owners'].items()}},indent=2))

if __name__=='__main__':main()
