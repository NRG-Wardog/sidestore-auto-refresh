#!/usr/bin/env python3
"""Materialize exact read-only baseline source, without executing/building it."""
import argparse,hashlib,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--expected-commit',required=True);p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
root=a.source.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
expected=a.expected_commit
assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()==expected
settings=root/'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift'
text=settings.read_text();start=text.index('struct V3HostLanguageUIKitBridge:');end=text.index('// V3_LIVE_HOST_LANGUAGE_END',start)
bridge=text[start:end];assert bridge.count('struct V3HostLanguageUIKitBridge:')==1
virtual=root/'MultitaskSupport/VirtualWindowsHostView.m';body=virtual.read_text();assert body.count('#import "DecoratedAppSceneViewController.h"')==1 and body.count('#import "VirtualWindowsHostView.h"')==1
body=body.replace('#import "DecoratedAppSceneViewController.h"','#import "GuestBoundaryStubs.h"').replace('#import "VirtualWindowsHostView.h"','// Interface supplied by reviewed boundary-only fixture header.')
dock=root/'MultitaskSupport/MultitaskDockView.swift';needle='keyWindow!.rootViewController!.view.subviews.first!.addSubview(self.windowHostingView)';assert dock.read_text().count(needle)==1
here=Path(__file__).resolve().parent
(out/'ProductionVirtualWindowsHostView.m').write_text(body)
(out/'ProductionLanguageBridge.swift').write_text('import SwiftUI\nimport UIKit\n'+bridge)
for name in ['GuestBoundaryStubs.h','GuestBoundaryStubs.m','AttachmentFixture.swift']:(out/name).write_bytes((here/name).read_bytes())
files=[settings,virtual,dock,here/'GuestBoundaryStubs.h',here/'GuestBoundaryStubs.m',here/'AttachmentFixture.swift',Path(__file__).resolve()]
report={'schema':'multitask-attachment-source-v1','source_commit':expected,'source_files':{str(f.relative_to(root) if f.is_relative_to(root) else f.name):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},'attachment_projection':{'original':needle,'fixture':'child?.addSubview(overlay)','difference':'Nullable first-child binding permits recording absence instead of crashing; target selection unchanged.'},'boundary_stubs':'Guest AppScene/Decorated types only; no private extension execution. _viewDelegate declaration only for compilation, status-bar path is not exercised.'}
(out/'source-receipt.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
print(out/'source-receipt.json')
