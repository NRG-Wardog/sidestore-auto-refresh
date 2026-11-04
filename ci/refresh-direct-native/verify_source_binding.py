#!/usr/bin/env python3
"""Bind native fixtures to exact composed production slices; never execute sources."""
import argparse
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent

def require(ok,message):
    if not ok: raise ValueError(message)

def block(source,anchor):
    require(source.count(anchor)==1,'Nonunique production anchor: '+anchor)
    start=source.index(anchor);end=source.index('{',start)+1;depth=1
    while depth:
        require(end<len(source),'Unterminated production block')
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]

def span(source,start,end):
    require(source.count(start)==1 and source.count(end)==1,'Nonunique source span')
    a=source.index(start);b=source.index(end,a)
    return source[a:b].rstrip()

def digest(data):return hashlib.sha256(data).hexdigest()

def bind(sources,builder,fixtures=HERE):
    paths={
      'intent':sources/'SideStore/AltStore/Intents/App Intents/RefreshAllAppsIntent.swift',
      'service':sources/'SideStore/AltStore/AppDelegate.swift',
      'xpc':sources/'LiveContainer/SideStoreSupport/XPCClient.m',
      'template':builder/'scripts/templates/v3_sidestore_service.swift',
      'primitives':builder/'scripts/templates/v3_behavioral_primitives.swift'}
    texts={k:p.read_text() for k,p in paths.items()}
    parts={'backend':block(texts['intent'],'enum V3RefreshIntentBackend {'),
           'ticket':block(texts['service'],'struct V3RefreshExecutionTicket:'),
           'gate':block(texts['service'],'struct V3RefreshExecutionGate {'),
           'lease':block(texts['service'],'struct V3RefreshAdmissionLease {'),
           'policy':block(texts['service'],'enum V3RefreshIntentStartPolicy {'),
           'methods':span(texts['service'],'    private var refreshExecutionGate =',
                          '    private var pendingRefreshAdmissionRequests:'),
           'protocol':span(texts['xpc'],'@protocol V3CommandService','@implementation SideStoreClient')}
    parts['protocol']=parts['protocol'].strip()
    for name,anchor in [('ticket','struct V3RefreshExecutionTicket:'),('gate','struct V3RefreshExecutionGate {'),('lease','struct V3RefreshAdmissionLease {'),('policy','enum V3RefreshIntentStartPolicy {')]:
        require(parts[name]==block(texts['primitives'],anchor),'Generated primitive differs from template: '+name)
    require(parts['methods']==span(texts['template'],'    private var refreshExecutionGate =','    private var pendingRefreshAdmissionRequests:'),'Generated service differs from template')
    mapping={'gate_fixture.swift':['ticket','gate'],
             'backend_fixture.swift':['ticket','gate','policy','backend'],
             'service_fixture.swift':['ticket','gate','lease','policy','backend','methods'],
             'service_objc_fixture.swift':['ticket','gate','lease','policy','backend','methods'],
             'bridge.m':['protocol']}
    rows=[]
    for name,keys in mapping.items():
        text=(fixtures/name).read_text()
        for key in keys:
            require(text.count(parts[key])==1,'Fixture does not contain exact production slice: '+name+':'+key)
        rows.append({'path':name,'sha256':digest(text.encode()),'production_slices':keys})
    return {'schema':'direct-refresh-source-binding-v1','status':'EXACT_SLICES_BOUND_NATIVE_NOT_EXECUTED',
            'inputs':{k:{'path':str(p),'sha256':digest(p.read_bytes())} for k,p in paths.items()},
            'slice_sha256':{k:digest(v.encode()) for k,v in parts.items()},'fixtures':rows,
            'real_xpc_transport_executed':False,'physical_device_validation':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sources',type=Path,required=True);p.add_argument('--builder',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=bind(a.sources.resolve(strict=True),a.builder.resolve(strict=True))
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(result['status'])
