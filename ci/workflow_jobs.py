"""Read one explicit workflow job for source-level contracts, not runtime proof.

Only the repository's deliberately flat two-space job mapping is supported.
Shared top-level policy is returned with one job; other jobs cannot accidentally
satisfy a suite's command, ordering or failure-evidence assertions.
"""
from pathlib import Path
import re


def workflow_job(path, job=None):
    text = Path(path).read_text()
    if job is None:
        return text
    if not re.fullmatch(r'[a-z][a-z0-9_]*', job):
        raise ValueError('Invalid workflow job selector')
    if text.count('\njobs:\n') != 1:
        raise ValueError('Expected one flat jobs mapping')
    header, body = text.split('\njobs:\n')
    matches = list(re.finditer(r'^  ([a-z][a-z0-9_]*):\n', body, re.M))
    names = [m.group(1) for m in matches]
    if not matches or matches[0].start() != 0 or len(names) != len(set(names)):
        raise ValueError('Malformed or duplicate workflow jobs')
    for line in body.splitlines():
        if '\t' in line:
            raise ValueError('Tabs are not supported in workflow jobs')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line.startswith('  '):
            raise ValueError('Unsupported workflow jobs layout')
        if not line.startswith('    ') and not re.fullmatch(r'  [a-z][a-z0-9_]*:', line):
            raise ValueError('Unsupported workflow job header')
    if names.count(job) != 1:
        raise ValueError('Workflow job missing')
    i = names.index(job)
    end = matches[i+1].start() if i+1 < len(matches) else len(body)
    return header + '\njobs:\n' + body[matches[i].start():end]
