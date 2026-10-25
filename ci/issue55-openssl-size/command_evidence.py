"""Fail-closed effective OpenSSL compilation evidence for the size-only experiment."""
import hashlib
from pathlib import Path
import re
import shlex

MACROS={'OPENSSL_PIC','OPENSSLDIR','ENGINESDIR','MODULESDIR','_REENTRANT',
        'OPENSSL_BUILDING_OPENSSL','NDEBUG','STATIC_LEGACY','L_ENDIAN'}
COVERAGE={'-fprofile-instr-generate','-fcoverage-mapping','-profile-generate',
          '-profile-coverage-mapping','-fprofile-arcs','-ftest-coverage'}

def check_flags(tokens):
    optimizations=[token for token in tokens if re.fullmatch(r'-O(?:[0-9]+|[szg]|fast)?',token)]
    if not optimizations or optimizations[-1]!='-Os':raise ValueError('Effective final optimization is not -Os')
    if any(token.split('=',1)[0] in COVERAGE for token in tokens):raise ValueError('Coverage flag drift')
    for token in tokens:
        if token.startswith('-U'):raise ValueError('Feature macro undefinition drift')
        if token.startswith('-D'):
            macro=token[2:].split('=',1)[0]
            if macro not in MACROS or ('=' in token and macro not in {'OPENSSLDIR','ENGINESDIR','MODULESDIR'}):
                raise ValueError('Feature macro definition drift')


def verify(log,configs,compiler,triples):
    """triples maps slice labels to a target triple; None denotes native macOS."""
    log=Path(log);data=log.read_bytes();rows={key:[] for key in triples}
    for line in data.decode().splitlines():
        stripped=line.strip()
        if not stripped.startswith('/') or ' -c ' not in stripped:continue
        tokens=shlex.split(stripped)
        if Path(tokens[0]).name!='clang':continue
        if tokens[0]!=str(compiler):raise ValueError('Actual compiler invocation differs')
        target=tokens[tokens.index('-target')+1] if '-target' in tokens else None
        matching=[key for key,triple in triples.items() if triple==target]
        if len(matching)!=1:raise ValueError('Actual compiler architecture scope differs')
        check_flags(tokens);rows[matching[0]].append(tokens)
    result={}
    for key,commands in rows.items():
        if not commands:raise ValueError('Missing actual native compilation commands: '+key)
        for provider in ('defltprov.c','legacyprov.c'):
            if not any(any(token.endswith('/'+provider) for token in command) for command in commands):
                raise ValueError('Provider compilation missing: '+provider)
        if not any('-DSTATIC_LEGACY' in command for command in commands):raise ValueError('Built-in legacy compile marker missing')
        config=Path(configs[key]);content=config.read_text()
        values=re.findall(r'^[ \t]*CFLAGS[ \t]*=(?!>)[ \t]*(.+)$',content,re.MULTILINE)
        if not values:raise ValueError('Effective CFLAGS missing from configdata')
        for flags in values:check_flags(shlex.split(flags))
        result[key]={'actual_compile_commands':len(commands),'effective_optimization':'-Os',
            'default_and_legacy_provider_sources_compiled':True,'static_legacy_marker_present':True,
            'configdata_sha256':hashlib.sha256(config.read_bytes()).hexdigest()}
    return {'build_log_sha256':hashlib.sha256(data).hexdigest(),'compiler_invocation':str(compiler),'slices':result}
