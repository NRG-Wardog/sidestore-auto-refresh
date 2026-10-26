"""Optional unsigned-widget true-local nlist experiment, never private externals.

Dependency imports include dynamic lookup, so every external/private-external
record and all runtime metadata/code are preserved; this is not runtime proof.
"""
import subprocess
from macho_stab_guard import require, sha, symbol_digest
from macho_local_symbol_guard import classify, verify_local_removal
from experiment_strip_stabs import write

POLICY = {'unsigned_executable':True,'preserve_private_externals':True}


def keep_list(data):
    shape,removable,protected=classify(data,**POLICY)
    require(sum(removable.values())>0,'no eligible widget true locals')
    names=sorted({row[0] for row in protected})
    require(all(name and not any(ch in name for ch in (b'\n',b'\r',b'\0')) for name in names),'unsafe widget keep-list name')
    content=b''.join(name+b'\n' for name in names)
    return content,{'policy':'preserve_all_external_private_external_undefined_and_indirect_records',
        'eligible_true_local_records':sum(removable.values()),'protected_records':sum(protected.values()),
        'protected_tuple_digest':symbol_digest(list(protected.elements())),
        'keep_name_count':len(names),'keep_list_sha256':sha(content),'runtime_proven':False}


def transform(data,work,report):
    content,policy=keep_list(data)
    keep=work/'widget-local-keep.log';keep.write_bytes(content)
    source=work/'widget-local-input';source.write_bytes(data)
    target=work/'widget-local-output'
    tool=report['tool']
    from pathlib import Path
    from experiment_strip_stabs import digest
    require(digest(Path(tool['path']))==tool['sha256'],'reviewed Apple tool changed')
    result=subprocess.run([tool['path'],'-x','-s',str(keep),'-o',str(target),str(source)],capture_output=True,timeout=120)
    (work/'widget-local-strip.log').write_bytes(result.stdout+result.stderr)
    require(result.returncode==0,'Apple true-local strip failed')
    changed=target.read_bytes()
    write(work/'widget-local-observed.json',{'approved':False,'snapshot':classify(changed,allow_apple_local_sentinel=True,**POLICY)[0]})
    qualification=verify_local_removal(data,changed,**POLICY)
    require(source.read_bytes()==data and keep.read_bytes()==content,'local tool changed immutable input/keep-list')
    report['true_local_stage']={'keep_policy':policy,'qualification':qualification,
        'input_sha256':sha(data),'output_sha256':sha(changed),'raw_bytes_saved':len(data)-len(changed),
        'tool_flags':['-x','-s'],'private_externals_removed':False}
    return changed
