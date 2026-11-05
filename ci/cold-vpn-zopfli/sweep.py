"""Bounded best-of Zopfli sweep; originals and earlier verified improvements survive timeouts."""
import concurrent.futures as cf
import json,pathlib,struct,subprocess,sys,time,zipfile,zlib
from contract import require,manifest,SHA,PIN,BUDGET,ITERATIONS,TARGET_BYTES
from zip32 import parse
ROOT=pathlib.Path(__file__).resolve().parent

def inflate(stream,original):
    dec=zlib.decompressobj(-15)
    raw=dec.decompress(stream,len(original)+1)
    require(raw==original and dec.eof and not dec.unused_data and not dec.unconsumed_tail,'compressed stream payload mismatch')

def execute_one(root,row,iterations,deadline,command=None):
    start=time.monotonic();remaining=deadline-start
    if remaining<=3: return {'index':row['index'],'iterations':iterations,'status':'budget_exhausted','elapsed_seconds':0}
    destination=root/'streams'/f"{row['index']}-{iterations}.deflate"
    argv=command or [sys.executable,str(root/'limited_exec.py'),str(row['index']),str(iterations)]
    try:
        with destination.open('xb') as handle:
            completed=subprocess.run(argv,stdout=handle,stderr=subprocess.DEVNULL,
                timeout=min(600,remaining-2),cwd=root,env={'PATH':'/usr/bin:/bin','LANG':'C'})
        if completed.returncode != 0:
            # A signal/nonzero exit may be a resource limit, but do not guess.
            # Never promote a partial stream; retain the previously verified best.
            destination.unlink(missing_ok=True)
            return {'index':row['index'],'iterations':iterations,'status':'compressor_exit',
                    'exit_code':completed.returncode,'elapsed_seconds':time.monotonic()-start}
        stream=destination.read_bytes();inflate(stream,row['raw'])
        return {'index':row['index'],'iterations':iterations,'status':'verified','bytes':len(stream),'sha256':SHA(stream),'elapsed_seconds':time.monotonic()-start}
    except subprocess.TimeoutExpired:
        # subprocess.run kills and waits for the exec-replaced sole compressor.
        destination.unlink(missing_ok=True)
        return {'index':row['index'],'iterations':iterations,'status':'bounded_timeout','elapsed_seconds':time.monotonic()-start}

def select_best(previous,result):
    if result['status']=='verified' and result['bytes']<previous['bytes']:
        return {'bytes':result['bytes'],'sha256':result['sha256'],'iterations':result['iterations']}
    return previous

def assemble(rows,end,best,root):
    body=bytearray();central=bytearray()
    for row in rows:
        choice=best[row['index']]
        stream=(root/'streams'/f"{row['index']}-{choice['iterations']}.deflate").read_bytes() if choice['iterations'] else row['stream']
        require(len(stream)==choice['bytes'] and SHA(stream)==choice['sha256'],'selected stream changed')
        inflate(stream,row['raw'])
        local=bytearray(row['local']);header=bytearray(row['central'])
        struct.pack_into('<I',local,18,len(stream));struct.pack_into('<I',header,20,len(stream));struct.pack_into('<I',header,42,len(body))
        body+=local+stream;central+=header
    ending=bytearray(end);struct.pack_into('<II',ending,12,len(central),len(body))
    return bytes(body+central+ending)

def main():
    require(__debug__ and len(sys.argv)==1,'no optimized Python or arbitrary sweep arguments')
    m=manifest(ROOT/'input-manifest.json');original=ROOT/'original.ipa';data=original.read_bytes()
    require((len(data),SHA(data))==(m['ipa_bytes'],m['ipa_sha256']),'exact native input required')
    rows,end=parse(data);require(len(rows)==m['entries'],'native member closure')
    require(not (ROOT/'recompressed.ipa').exists() and not (ROOT/'recompressed.ipa').is_symlink(),'exclusive final output')
    (ROOT/'inputs').mkdir();(ROOT/'streams').mkdir()
    eligible=[row for row in rows if len(row['raw'])>=1024]
    for row in eligible:
        with (ROOT/'inputs'/str(row['index'])).open('xb') as handle:handle.write(row['raw'])
    best={row['index']:{'bytes':len(row['stream']),'sha256':SHA(row['stream']),'iterations':0} for row in rows}
    start=time.monotonic();deadline=start+BUDGET;results=[];rounds=[];history={row['index']:[] for row in eligible}
    binary_hash=SHA((ROOT/'zopfli').read_bytes());compiler_hash=SHA(pathlib.Path('/usr/bin/cc').resolve().read_bytes())
    stop='iteration_schedule_exhausted'
    for iterations in ITERATIONS:
        candidates=[]
        for row in eligible:
            previous=history[row['index']]
            if iterations>50 and len(previous)>=2 and all(x.get('improved') is False for x in previous[-2:]):continue
            if previous and previous[-1]['status']!='verified':continue
            estimate=previous[-1]['elapsed_seconds']*iterations/previous[-1]['iterations'] if previous else 0
            if estimate>max(0,deadline-time.monotonic()-3) or estimate>600:continue
            # Favor large measured marginal gains per observed runtime; baseline covers all.
            benefit=previous[-1].get('saved_vs_previous',0) if previous else len(row['stream'])
            priority=benefit/max(0.01,previous[-1]['elapsed_seconds']) if previous else benefit
            candidates.append((priority,row))
        candidates.sort(key=lambda item:(item[0],len(item[1]['stream'])),reverse=True)
        round_start=time.monotonic();round_count=0
        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            pending={};todo=iter(candidates)
            def submit():
                if time.monotonic()>=deadline-3:return
                item=next(todo,None)
                if item is not None:pending[pool.submit(execute_one,ROOT,item[1],iterations,deadline)]=item[1]
            submit();submit()
            while pending:
                completed,_=cf.wait(pending,return_when=cf.FIRST_COMPLETED)
                for future in completed:
                    row=pending.pop(future);result=future.result();old=best[row['index']]
                    selected=select_best(old,result);best[row['index']]=selected
                    result['improved']=selected is not old;result['saved_vs_previous']=old['bytes']-selected['bytes']
                    results.append(result);history[row['index']].append(result);round_count+=1
                    progress={'elapsed_seconds':time.monotonic()-start,'iteration':iterations,'attempts':len(results),'saved_bytes':sum(len(r['stream'])-best[r['index']]['bytes'] for r in rows),'best':best,'results':results}
                    (ROOT/'progress.json').write_text(json.dumps(progress,indent=2)+'\n')
                    print(json.dumps({'event':'attempt_complete',**result}),flush=True)
                    submit()
        rounds.append({'iterations':iterations,'attempts':round_count,'elapsed_seconds':time.monotonic()-round_start})
        # ZIP framing is unchanged; only selected stream lengths alter total size.
        selected_bytes=len(data)-sum(len(r['stream'])-best[r['index']]['bytes'] for r in rows)
        if selected_bytes<=TARGET_BYTES:stop='size_target_met';break
        if time.monotonic()>=deadline-3:stop='aggregate_budget_reached';break
        if not candidates:stop='remaining_candidates_plateau_or_estimated_cost_exceeds_bounds';break
    elapsed=time.monotonic()-start
    require(elapsed<=BUDGET+5,'compression aggregate bound exceeded')
    output=assemble(rows,end,best,ROOT)
    require(len(output)<=len(data),'copy grew')
    with (ROOT/'recompressed.ipa').open('xb') as handle:handle.write(output)
    with zipfile.ZipFile(ROOT/'recompressed.ipa') as archive:require(archive.testzip() is None,'final CRC')
    require(SHA(original.read_bytes())==m['ipa_sha256'] and SHA((ROOT/'zopfli').read_bytes())==binary_hash,'input or compressor modified')
    report={'schema':'cold-vpn-zopfli-quick-v1','source_commit':PIN,'binary_sha256':binary_hash,'compiler_sha256':compiler_hash,'original_bytes':len(data),'original_sha256':SHA(data),'final_bytes':len(output),'final_sha256':SHA(output),'saved_bytes':len(data)-len(output),'entries':len(rows),'eligible_entries':len(eligible),'workers':2,'process_memory_limit':2*1024**3,'entry_timeout_seconds':600,'aggregate_timeout_seconds':BUDGET,'elapsed_seconds':elapsed,'approved_total_budget_seconds':1800,'iterations_schedule':list(ITERATIONS),'rounds':rounds,'stop_reason':stop,'best':best,'results':results,'device_executed':False,'target_met':len(output)<=TARGET_BYTES}
    (ROOT/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:value for key,value in report.items() if key not in ('best','results')}),flush=True)
if __name__=='__main__':main()
