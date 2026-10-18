"""Bounded synchronous command ownership; no signals after child reaping."""
import json, os, signal, subprocess, time
from pathlib import Path

def require(value, message):
    if not value: raise ValueError(message)

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

    def begin_finalization(self):
        self.finalizing = True
        self.deadline = min(self.deadline + 6 * 60, time.monotonic() + 6 * 60)

    def run(self, argv, timeout=60, checked=True):
        require(isinstance(argv, list) and argv, 'Empty command')
        budget = min(float(timeout), 240.0, self.deadline - time.monotonic())
        require(budget > 0, 'Shared experiment/finalization deadline exhausted')
        self.number += 1
        stem = self.directory / ('%03d' % self.number)
        argv = list(map(str, argv))
        started = time.monotonic()
        record = {'command': argv, 'budgetSeconds': budget, 'startedUptime': started,
                  'finalization': self.finalizing, 'primaryError': None}
        process = None
        stdout = stderr = ''
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
            except subprocess.TimeoutExpired:
                record['primaryError'] = 'Command deadline exceeded'
            except Exception as error:
                record['primaryError'] = 'Command collection failed: ' + type(error).__name__
            cleanup = finish_owned_child(process)
            stdout = cleanup.pop('stdout', stdout)
            stderr = cleanup.pop('stderr', stderr)
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
                self.failed_receipts = self.failed_receipts[-8:]
            stem.with_suffix('.log').write_text((stdout or '') + '\n' + (stderr or ''))
            stem.with_suffix('.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        if checked:
            require(not record['primaryError'] and not record.get('quiescenceError'),
                    'Command failed: ' + stem.name + ': ' + str(record['primaryError'] or record.get('quiescenceError')))
        return stdout, record
