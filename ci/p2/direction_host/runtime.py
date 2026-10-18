"""Bounded synchronous command ownership; no signals after child reaping."""
import base64, hashlib, json, os, re, signal, subprocess, time
from pathlib import Path

def require(value, message):
    if not value: raise ValueError(message)

def output_snapshot(value, origin='parent-owned-value'):
    """Bounded immutable bytes already owned by this Popen caller, never a file read."""
    if value is None: value = ''
    require(isinstance(value, (str, bytes)), 'Unexpected owned output type')
    raw = value.encode('utf-8') if isinstance(value, str) else value
    head = raw[:1024]
    tail = raw[max(len(head), len(raw)-3072):]
    return {'captureOrigin':origin,
            'representation': 'utf8-of-decoded-popen-text' if isinstance(value,str) else 'popen-partial-bytes',
            'capturedBytes': len(raw), 'capturedSHA256': hashlib.sha256(raw).hexdigest(),
            'headBase64': base64.b64encode(head).decode('ascii'),
            'tailBase64': base64.b64encode(tail).decode('ascii'),
            'truncated': len(head)+len(tail) < len(raw),
            'completeProcessOutputProven': False}

def output_text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value,bytes) else value or ''

def bounded_receipts(rows):
    # Keep the initiating failure even if subsequent exporter/cleanup calls fail.
    return rows if len(rows) <= 8 else rows[:1] + rows[-7:]

def finish_owned_child(process):
    """Signal only a retained, unreaped child identity. Never a reaped PGID."""
    state = {'pgid': process.pid, 'quiescent': False, 'pipesJoined': False,
             'signals': [], 'identityRetainedForSignals': False, 'reapedBeforeSignals': False,
             'postReapPolicy': 'read-only-group-disappearance', 'errors': []}
    result = {'ownedProcessGroup': state}
    try:
        # SIG_IGN/custom handlers could reap asynchronously and invalidate PID
        # reservation. This driver is single-threaded and owns all child waits.
        require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, 'SIGCHLD identity reservation unavailable')
        if process.returncode is None:
            process.poll()  # Last possible reap before the nonzero-signal phase.
        state['reapedBeforeSignals'] = process.returncode is not None
        if process.returncode is None:
            require(os.getpgid(process.pid) == process.pid, 'Owned child process group differs')
            state['identityRetainedForSignals'] = True
            # No poll/wait/communicate is allowed between this reservation and
            # the last signal. An exited child remains an unreaped zombie, so
            # its PID/PGID cannot be reused by an unrelated process group.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                require(process.returncode is None and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL,
                        'Owned child identity lost before signal')
                try:
                    os.killpg(process.pid, sig)
                    state['signals'].append(int(sig))
                except ProcessLookupError:
                    break
                if sig == signal.SIGTERM:
                    time.sleep(0.5)  # Bounded grace, deliberately without reaping.
    except Exception as error:
        state['errors'].append(type(error).__name__ + ': ' + str(error))
    # The signal phase has irrevocably ended. No nonzero signal below here,
    # even if a descendant retains pipes or a numeric group is reused.
    try:
        stdout, stderr = process.communicate(timeout=3)
        result.update(stdout=stdout, stderr=stderr)
        state['pipesJoined'] = True
    except Exception as error:
        state['errors'].append('Pipe join: ' + type(error).__name__)
        try:
            process.wait(timeout=1)
        except Exception as wait_error:
            state['errors'].append('Owned child reap: ' + type(wait_error).__name__)
    result['terminationReturncode'] = process.returncode
    state['leaderReaped'] = process.returncode is not None
    until = time.monotonic() + 1
    while True:
        try:
            os.killpg(process.pid, 0)  # Read-only observation, never ownership proof.
        except ProcessLookupError:
            state['quiescent'] = state['leaderReaped']
            break
        except OSError as error:
            state['errors'].append('Group observation: ' + type(error).__name__)
            break
        if time.monotonic() >= until:
            state['errors'].append('Surviving or reused group; no post-reap signal permitted')
            break
        time.sleep(0.05)
    if state['errors'] or not state['quiescent'] or not state['pipesJoined']:
        result['quiescenceError'] = 'Owned child cleanup/observation incomplete; fail closed'
    return result


BUILD_SHAPE=['xcodebuild','-project','<project>','-scheme','LiveContainer','-configuration','Release','-sdk','iphonesimulator','-destination','<destination>','-derivedDataPath','<derived>','-disableAutomaticPackageResolution','-onlyUsePackageVersionsFromResolvedFile','-resultBundlePath','<result>','build']
def reviewed_build(argv):
    args=list(map(str,argv))
    if len(args)!=len(BUILD_SHAPE):return False
    if not (Path(args[2]).is_absolute() and args[2].endswith('/LiveContainer.xcodeproj') and Path(args[12]).is_absolute() and Path(args[16]).is_absolute() and args[16].endswith('.xcresult')):return False
    if not re.fullmatch(r'platform=iOS Simulator,id=[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}',args[10]):return False
    args[2],args[10],args[12],args[16]='<project>','<destination>','<derived>','<result>'
    return args==BUILD_SHAPE

def command_budget(argv,timeout,remaining,finalizing=False):
    return min(float(timeout),1500.0 if reviewed_build(argv) and not finalizing else 240.0,remaining)


class Commands:
    """One synchronous owner, a shared deadline, and failure-first command receipts."""
    def __init__(self, directory, seconds=18 * 60):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.deadline = time.monotonic() + seconds
        self.number = 0
        self.all_settled = True
        self.finalizing = False
        self.failed_receipts = []
        self.failed_output_receipts = []

    def begin_finalization(self):
        self.finalizing = True
        self.deadline = min(self.deadline + 6 * 60, time.monotonic() + 6 * 60)

    def run(self, argv, timeout=60, checked=True):
        require(isinstance(argv, list) and argv, 'Empty command')
        budget = command_budget(argv, timeout, self.deadline - time.monotonic(), self.finalizing)
        require(budget > 0, 'Shared experiment/finalization deadline exhausted')
        self.number += 1
        stem = self.directory / ('%03d' % self.number)
        argv = list(map(str, argv))
        started = time.monotonic()
        record = {'command': argv, 'budgetSeconds': budget, 'startedUptime': started,
                  'finalization': self.finalizing, 'primaryError': None}
        process = None
        stdout = stderr = ''
        stdout_origin = stderr_origin = 'no-output-returned'
        try:
            require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL,
                    'Ordinary owned-child reservation unavailable')
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
            # Popen itself is not interruptible here. Never grant its elapsed
            # time again after it returns, or infer target readiness from return.
            remaining = min(started + budget, self.deadline) - time.monotonic()
            record['postSpawnRemainingSeconds'] = max(0, remaining)
            try:
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, budget)
                stdout, stderr = process.communicate(timeout=remaining)
                stdout_origin = stderr_origin = 'communicate-return'
            except subprocess.TimeoutExpired as error:
                record['primaryError'] = 'Command deadline exceeded'
                stdout, stderr = error.stdout or '', error.stderr or ''
                stdout_origin = 'timeout-partial' if error.stdout is not None else 'no-output-returned'
                stderr_origin = 'timeout-partial' if error.stderr is not None else 'no-output-returned'
            except Exception as error:
                record['primaryError'] = 'Command collection failed: ' + type(error).__name__
            cleanup = finish_owned_child(process)
            captured_stdout = cleanup.pop('stdout', None)
            captured_stderr = cleanup.pop('stderr', None)
            # communicate returns cumulative output. Replace, never concatenate
            # with TimeoutExpired's partial prefix, and keep streams independent.
            if captured_stdout is not None: stdout, stdout_origin = captured_stdout, 'cleanup-communicate-return'
            if captured_stderr is not None: stderr, stderr_origin = captured_stderr, 'cleanup-communicate-return'
            record.update(cleanup)
            record['returncode'] = process.returncode
            if cleanup.get('quiescenceError'):
                self.all_settled = False
            if process.returncode != 0 and not record['primaryError']:
                record['primaryError'] = 'Command returned nonzero'
        except Exception as error:
            record['primaryError'] = record['primaryError'] or type(error).__name__ + ': ' + str(error)
            if process is not None and 'ownedProcessGroup' not in record:
                self.all_settled = False
        finally:
            record['finishedUptime'] = time.monotonic()
            record['elapsedSeconds'] = record['finishedUptime'] - started
            if record['primaryError'] or record.get('quiescenceError'):
                group = record.get('ownedProcessGroup', {})
                # Parent-owned memory only. No later read from child-writable
                # evidence is needed to diagnose an unresolved export process.
                receipt = {'number': self.number, 'phase': ' '.join(argv[:4])[:256],
                           'commandIdentity': ' '.join(argv)[:1024],
                           'primaryError': str(record['primaryError'])[:1024],
                           'returncode': record.get('returncode'),
                           'budgetSeconds': budget, 'elapsedSeconds': record['elapsedSeconds'],
                           'finalization': self.finalizing,
                           'quiescenceError': record.get('quiescenceError'),
                           'ownedProcessGroup': {k: group.get(k) for k in
                              ['pgid','quiescent','pipesJoined','signals','identityRetainedForSignals',
                               'reapedBeforeSignals','postReapPolicy','leaderReaped']},
                           'cleanupErrors': [str(x)[:250] for x in group.get('errors', [])[:4]]}
                self.failed_receipts.append(receipt)
                self.failed_receipts = bounded_receipts(self.failed_receipts)
                self.failed_output_receipts = bounded_receipts(self.failed_output_receipts + [{
                    'number': self.number, 'phase': receipt['phase'],
                    'commandIdentity': receipt['commandIdentity'],
                    'primaryError': receipt['primaryError'],
                    'source': 'immutable-parent-owned-Popen-capture',
                    'stdout': output_snapshot(stdout,stdout_origin), 'stderr': output_snapshot(stderr,stderr_origin)}])
            stem.with_suffix('.log').write_text(output_text(stdout) + '\n' + output_text(stderr))
            stem.with_suffix('.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        if checked:
            require(not record['primaryError'] and not record.get('quiescenceError'),
                    'Command failed: ' + stem.name + ': ' + str(record['primaryError'] or record.get('quiescenceError')))
        return output_text(stdout), record
