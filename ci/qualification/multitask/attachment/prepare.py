#!/usr/bin/env python3
"""Extract actual candidate helper and pinned historical attachment counterfactual."""
import argparse,hashlib,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--expected-commit',required=True);p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
root=a.source.resolve(strict=True);out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
expected=a.expected_commit
assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()==expected
settings=root/'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift'
text=settings.read_text();start=text.index('struct V3HostLanguageUIKitBridge:');end=text.index('// V3_LIVE_HOST_LANGUAGE_END',start)
bridge=text[start:end];assert bridge.count('struct V3HostLanguageUIKitBridge:')==1
virtual=root/'MultitaskSupport/VirtualWindowsHostView.m';header=root/'MultitaskSupport/VirtualWindowsHostView.h'
body=virtual.read_text();assert body.count('#import "DecoratedAppSceneViewController.h"')==1 and body.count('#import "VirtualWindowsHostView.h"')==1
assert '- (BOOL)attachToHostView:' in body and 'NS_SWIFT_NAME(attach(toHostView:))' in header.read_text()
body=body.replace('#import "DecoratedAppSceneViewController.h"','#import "GuestBoundaryStubs.h"')
dock=root/'MultitaskSupport/MultitaskDockView.swift';dock_text=dock.read_text()
start_marker='// LC_GUEST_HOST_WINDOW_SELECTION_BEGIN';end_marker='// LC_GUEST_HOST_WINDOW_SELECTION_END'
assert dock_text.count(start_marker)==1 and dock_text.count(end_marker)==1
selector=dock_text.split(start_marker,1)[1].split(end_marker,1)[0]
key_start=dock_text.index('public var keyWindow: UIWindow? {');key_open=dock_text.index('{',key_start);depth=1
for index in range(key_open+1,len(dock_text)):
    depth+=(dock_text[index]=='{')-(dock_text[index]=='}')
    if depth==0:break
key_body=dock_text[key_open+1:index]
assert key_body.count('UIApplication.shared.connectedScenes')==1 and key_body.count('MultitaskWindowManager.mainSceneSession')==1
projected=key_body.replace('UIApplication.shared.connectedScenes','inputScenes').replace('UIWindowScene','FixtureWindowScene').replace('MultitaskWindowManager.mainSceneSession','registeredSession')
selector_source='import UIKit\n'+selector+'\nstruct FixtureSession { let persistentIdentifier:String }\nfinal class FixtureWindowScene { let session:FixtureSession; let activationState:UIScene.ActivationState; let windows:[UIWindow]; init(_ name:String,_ state:UIScene.ActivationState,_ windows:[UIWindow]) { self.session=FixtureSession(persistentIdentifier:name);self.activationState=state;self.windows=windows } }\nfunc projectedProductionHostWindow(inputScenes:[Any],registeredSession:FixtureSession?)->UIWindow? {'+projected+'\n}\n'
(out/'ProductionHostSelection.swift').write_text(selector_source)
here=Path(__file__).resolve().parent
baseline=here/'baseline/VirtualWindowsHostView.m';historical=baseline.read_text()
assert hashlib.sha256(baseline.read_bytes()).hexdigest()=='3b31573f5ef5cf7d8d7ceb0ae72869eca97f52609957997174a4d820863bd785'
assert historical.count('@implementation VirtualWindowsHostView')==1
counterfactual=historical.replace('#import "DecoratedAppSceneViewController.h"','#import "GuestBoundaryStubs.h"').replace('@implementation VirtualWindowsHostView','@implementation BaselineVirtualWindowsHostView')
(out/'ProductionVirtualWindowsHostView.m').write_text(body)
(out/'VirtualWindowsHostView.h').write_bytes(header.read_bytes())
(out/'BaselineVirtualWindowsHostView.m').write_text(counterfactual)
(out/'ProductionLanguageBridge.swift').write_text('import SwiftUI\nimport UIKit\n'+bridge)
for name in ['GuestBoundaryStubs.h','GuestBoundaryStubs.m','AttachmentFixture.swift']:(out/name).write_bytes((here/name).read_bytes())
files=[settings,virtual,header,dock,here/'GuestBoundaryStubs.h',here/'GuestBoundaryStubs.m',here/'AttachmentFixture.swift',Path(__file__).resolve(),baseline]
report={'schema':'multitask-attachment-source-v2','source_commit':expected,'source_files':{str(f.relative_to(root) if f.is_relative_to(root) else f.relative_to(here)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},'counterfactual_baseline_commit':'dcb98b6eeff937cedffd5e0c30aa286ada14a618','attachment_projection':{'original':'keyWindow!.rootViewController!.view.subviews.first!.addSubview(self.windowHostingView)','counterfactual':'child?.addSubview(overlay)','corrected':'overlay.attach(toHostView: host.view)','difference':'Counterfactual renames the historical class as a subclass so both implementations coexist; imports replaced only for declared guest boundary stubs. Production helper and header are otherwise exact.'},'scene_input_projection':'Exact keyWindow property body, replacing only UIApplication.shared.connectedScenes with inputScenes, UIWindowScene type with FixtureWindowScene and mainSceneSession with registeredSession. Real UIKit windows/controllers and exact selection enum are executed; OS scene discovery/registration is not simulated as device proof.','boundary_stubs':'Guest AppScene/Decorated types only; no private extension execution. _viewDelegate declaration only for compilation, status-bar path is not exercised.'}
(out/'source-receipt.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
print(out/'source-receipt.json')
