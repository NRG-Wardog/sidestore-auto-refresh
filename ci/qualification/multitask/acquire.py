#!/usr/bin/env python3
"""Acquire only the immutable user-owned LC source required by these fixtures."""
import argparse,json
from pathlib import Path
from contract import command,identity,inventory,manifest,require

def run(output,evidence):
    source=manifest();require(not output.exists(),'Fresh source directory required')
    output.mkdir(parents=True)
    command(['git','init',output])
    command(['git','-C',output,'-c','core.hooksPath=/dev/null','fetch','--depth=1','--no-tags','https://github.com/'+source['repository']+'.git',source['commit']],timeout=180)
    command(['git','-C',output,'-c','core.hooksPath=/dev/null','checkout','--detach','FETCH_HEAD'])
    data={'schema':'multitask-source-acquisition-v1','source':source,'identity':identity(),'inventory':inventory(output)}
    require(evidence.parent.is_dir() and not evidence.exists(),'Fresh evidence path required')
    evidence.write_text(json.dumps(data,sort_keys=True,indent=2)+'\n')
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True);args=parser.parse_args();run(args.output,args.evidence)
