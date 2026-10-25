#!/usr/bin/env python3
"""Run only reviewed source-owned forwardport fixtures, never Apple/device operations."""
import hashlib,json,pathlib,subprocess,sys,shutil
root=pathlib.Path.cwd();ss=root/'SideSign';lc=root/'LiveContainer';sd=root/'SideStore';out=root/'forwardport-results';out.mkdir(exist_ok=True)
manifest=json.load(open(root/'.ci/branch-forwardports/inputs.json'))
for repo,entry in manifest.items():
 actual=subprocess.check_output(['git','-C',str(root/repo),'rev-parse','HEAD'],text=True).strip();assert actual==entry['commit'],(repo,actual)
 for path,digest in entry.get('files',{}).items():assert hashlib.sha256((root/repo/path).read_bytes()).hexdigest()==digest,(repo,path)
results=[]
def run(name,args,cwd=root):
 p=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 (out/(name+'.log')).write_text(p.stdout);print(p.stdout,flush=True);results.append({'name':name,'returncode':p.returncode});return p.returncode==0
run('guest-geometry',['python3','-B','tests/test_guest_safe_area_geometry.py'],lc)
run('recovery-banner',['python3','-B','tests/test_recovery_banner_layout.py'],lc)
generated=out/'iloader'
ok=run('auth-source',['python3','-B','.ci/iloader-auth-parity/test_source.py','--sidestore-root',str(sd)],ss)
ok=run('auth-generate',['python3','-B','.ci/iloader-auth-parity/generate.py','--sidestore-root',str(sd),'--output-dir',str(generated)],ss) and ok
if ok:
 ev=json.load(open(generated/'source-evidence.json'));files=[str(generated/n) for n in ev['output_closure']];assert len(files)==7
 if run('auth-compile',['swiftc','-swift-version','5','-parse-as-library',*files,'-o',str(out/'auth-fixture')]):run('auth-runtime',[str(out/'auth-fixture'),str(out/'auth-cases.json')])
# Compile the exact bounded collector and real enum in a dependency-free module.
# The test file parks Foundation tasks; no network task is ever resumed.
pkg=out/'foundation';(pkg/'Sources/SideSign').mkdir(parents=True);(pkg/'Tests/SideSignTests').mkdir(parents=True)
src=(ss/'Sources/Anisette/ILoaderRemoteAnisette.swift').read_text();start=src.index('public enum ILoaderRemoteError:');end=src.index('\npublic enum ILoaderRemoteAuthentication',start)
(pkg/'Sources/SideSign/Error.swift').write_text('import Foundation\n'+src[start:end])
shutil.copyfile(ss/'Sources/Anisette/ILoaderRemoteHTTPTransport.swift',pkg/'Sources/SideSign/ILoaderRemoteHTTPTransport.swift')
shutil.copyfile(ss/'Tests/SideSignTests/ILoaderRemoteHTTPTransportTests.swift',pkg/'Tests/SideSignTests/ILoaderRemoteHTTPTransportTests.swift')
(pkg/'Package.swift').write_text('''// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "BoundedHTTPChecks", platforms: [.macOS(.v12)], targets: [.target(name: "SideSign"), .testTarget(name: "SideSignTests", dependencies: ["SideSign"])], swiftLanguageModes: [.v6])
''')
run('foundation-tests',['swift','test','--filter','ILoaderRemoteHTTPTransportTests'],pkg)
# iOS SDK typecheck of the same changed production transport, without executing it.
sdk=subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip()
run('ios-transport-typecheck',['xcrun','swiftc','-typecheck','-swift-version','6','-target','arm64-apple-ios15.0','-sdk',sdk,str(pkg/'Sources/SideSign/Error.swift'),str(pkg/'Sources/SideSign/ILoaderRemoteHTTPTransport.swift')])
(out/'results.json').write_text(json.dumps({'inputs':manifest,'results':results,'apple_or_device_operations':False},indent=2)+'\n')
sys.exit(1 if any(x['returncode'] for x in results) else 0)
