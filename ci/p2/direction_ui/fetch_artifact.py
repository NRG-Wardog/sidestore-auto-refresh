#!/usr/bin/env python3
"""Download one pinned historical build with redacted, bounded diagnostics.

The job grants actions:read, no persistent credential. No redirect carries the
GitHub token. Signed redirect URLs and headers are never included in evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = 'NRG-Wardog/sidestore-auto-refresh'
ARTIFACT_ID = 12150258964
RUN_ID = 39165480984
COMMIT = '365e64fadbc54655705d784c8c6efac2504ef3b3'
DIGEST = '8feb2f33e9d9cbeff3340d7453e38270a06dffb09fe1c0e69344d3f8a6a5c173'
SIZE = 13703955
# The exact name is pinned from the original artifact metadata, not a prefix.
ARTIFACT_NAME = 'p2-direction-host-365e64fadbc54655705d784c8c6efac2504ef3b3-39165480984-1'
REPOSITORY_ID = 1350360026
BRANCH = 'validation/p2-alert-lifecycle'
API = 'https://api.github.com/repos/' + REPOSITORY + '/actions/artifacts/' + str(ARTIFACT_ID)
MAX_METADATA_BYTES = 1024 * 1024
DEADLINE_SECONDS = 180


class DownloadContractError(ValueError):
    """Only static reviewed codes may be passed here; never external values."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(value, code):
    if not value:
        raise DownloadContractError(code)


class AbsoluteDeadline:
    """Interrupt synchronous socket/SSL reads with one process-local alarm.

    Socket timeouts bound inactivity only: a peer can drip bytes forever. This
    one-shot real-time alarm is independent of progress and raises out of a
    blocking Python socket read. The CLI runs on the main thread of POSIX CI;
    unsupported contexts fail closed, never silently lose the absolute bound.
    """
    def __init__(self, deadline):
        self.deadline = deadline
        self.armed = False
        self.previous_handler = None

    @staticmethod
    def expired(signum, frame):
        raise DownloadContractError('DOWNLOAD_DEADLINE_EXHAUSTED')

    def arm(self):
        require(threading.current_thread() is threading.main_thread(), 'DEADLINE_MAIN_THREAD_REQUIRED')
        require(all(hasattr(signal, name) for name in ('SIGALRM', 'ITIMER_REAL', 'setitimer', 'getitimer')),
                'DEADLINE_TIMER_UNAVAILABLE')
        # Never replace another component's active timer or recurring alarm.
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'DEADLINE_TIMER_ALREADY_ACTIVE')
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'DOWNLOAD_DEADLINE_EXHAUSTED')
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, self.expired)
        self.armed = True
        signal.setitimer(signal.ITIMER_REAL, remaining)

    def close(self):
        if self.armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous_handler)
            self.armed = False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'METADATA_DUPLICATE_KEY')
            value[key] = item
        return value
    def constant(value):
        raise DownloadContractError('METADATA_NONFINITE_NUMBER')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def validate_metadata(data):
    require(type(data) is dict, 'METADATA_NOT_OBJECT')
    require(type(data.get('id')) is int and data['id'] == ARTIFACT_ID, 'METADATA_ARTIFACT_ID_MISMATCH')
    require(data.get('name') == ARTIFACT_NAME, 'METADATA_ARTIFACT_NAME_MISMATCH')
    require(data.get('expired') is False, 'METADATA_ARTIFACT_EXPIRED')
    require(type(data.get('size_in_bytes')) is int and data['size_in_bytes'] == SIZE, 'METADATA_SIZE_MISMATCH')
    require(data.get('digest') == 'sha256:' + DIGEST, 'METADATA_DIGEST_MISMATCH')
    run = data.get('workflow_run')
    require(type(run) is dict and type(run.get('id')) is int and run['id'] == RUN_ID, 'METADATA_RUN_ID_MISMATCH')
    require(run.get('head_sha') == COMMIT, 'METADATA_COMMIT_MISMATCH')
    require(type(run.get('repository_id')) is int and run['repository_id'] == REPOSITORY_ID, 'METADATA_REPOSITORY_ID_MISMATCH')
    require(type(run.get('head_repository_id')) is int and run['head_repository_id'] == REPOSITORY_ID, 'METADATA_HEAD_REPOSITORY_ID_MISMATCH')
    require(run.get('head_branch') == BRANCH, 'METADATA_BRANCH_MISMATCH')
    require(data.get('archive_download_url') == API + '/zip', 'METADATA_DOWNLOAD_ENDPOINT_MISMATCH')


def validate_location(url):
    require(type(url) is str and 0 < len(url) <= 16384 and
            not any(ord(char) <= 32 or ord(char) == 127 for char in url) and '\\' not in url,
            'REDIRECT_URL_INVALID')
    try:
        parsed = urllib.parse.urlsplit(url)
        safe = (parsed.scheme == 'https' and parsed.hostname and parsed.port in (None, 443) and
                parsed.username is None and parsed.password is None and not parsed.fragment)
    except ValueError:
        raise DownloadContractError('REDIRECT_URL_INVALID') from None
    require(safe, 'REDIRECT_URL_UNSAFE')
    require(parsed.hostname.endswith('.blob.core.windows.net') or parsed.hostname.endswith('.actions.githubusercontent.com'),
            'REDIRECT_STORAGE_HOST_DENIED')


def error_category(error):
    # Never use str(error), repr(error), request headers, or unexpected class
    # names: urllib failures may embed the entire signed storage URL.
    for kind in (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError,
                 UnicodeDecodeError, TimeoutError, OSError, ValueError):
        if isinstance(error, kind):
            return kind.__name__
    return 'Exception'


def fetch(output, token, opener=None):
    require(type(token) is str and token, 'TOKEN_MISSING')
    output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink() and not any(p.is_symlink() for p in output.parents),
            'OUTPUT_NOT_FRESH_UNLINKED')
    output.mkdir(parents=True)
    report = {'schemaVersion': 1, 'status': 'BLOCKED', 'stage': 'initialization', 'reasonCode': None,
              'repository': REPOSITORY, 'artifactId': ARTIFACT_ID, 'artifactName': ARTIFACT_NAME,
              'runId': RUN_ID, 'commit': COMMIT, 'expectedSHA256': DIGEST, 'expectedBytes': SIZE,
              'deadlineSeconds': DEADLINE_SECONDS, 'deadlineMechanism': 'POSIX_ITIMER_REAL', 'tokenForwardedToStorage': False, 'errors': []}
    deadline = time.monotonic() + DEADLINE_SECONDS
    opener = opener or urllib.request.build_opener(NoRedirect())
    receipt_path = output / 'download.json'
    receipt_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')

    def check_deadline():
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'DOWNLOAD_DEADLINE_EXHAUSTED')
        return remaining

    def open_request(url, authorized):
        remaining = check_deadline()
        require(not authorized or url in (API, API + '/zip'), 'TOKEN_DESTINATION_DENIED')
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'p2-host-reuse-artifact-reader'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + token
        return opener.open(urllib.request.Request(url, headers=headers), timeout=min(20, remaining))

    absolute_deadline = AbsoluteDeadline(deadline)
    try:
        report['stage'] = 'metadata-request'
        absolute_deadline.arm()
        with open_request(API, True) as response:
            report['stage'] = 'metadata-read'
            raw_metadata = response.read(MAX_METADATA_BYTES + 1)
            check_deadline()
        require(len(raw_metadata) <= MAX_METADATA_BYTES, 'METADATA_TOO_LARGE')
        report['stage'] = 'metadata-decode'
        data = strict_json(raw_metadata)
        report['stage'] = 'metadata-validation'
        validate_metadata(data)
        # Retain only validated scalars. Do not copy unbounded workflow_run
        # dictionaries, unexpected values, URLs, or response headers.
        report['metadata'] = {'id': ARTIFACT_ID, 'name': ARTIFACT_NAME, 'size_in_bytes': SIZE,
                              'digest': 'sha256:' + DIGEST, 'expired': False,
                              'workflow_run': {'id': RUN_ID, 'head_sha': COMMIT, 'repository_id': REPOSITORY_ID,
                                               'head_repository_id': REPOSITORY_ID, 'head_branch': BRANCH}}
        report['stage'] = 'archive-redirect'
        try:
            response = open_request(API + '/zip', True)
        except urllib.error.HTTPError as error:
            require(error.code == 302, 'REDIRECT_STATUS_UNEXPECTED')
            report['stage'] = 'redirect-validation'
            location = error.headers.get('Location', '')
            validate_location(location)
            report['stage'] = 'storage-request'
            response = open_request(location, False)
        else:
            response.close()
            raise DownloadContractError('REDIRECT_REQUIRED')
        digest, size = hashlib.sha256(), 0
        report['stage'] = 'storage-read'
        with response, (output / 'artifact.zip.part').open('xb') as target:
            while True:
                check_deadline()
                chunk = response.read(1024 * 1024)
                # A read returning EOF can itself cross the deadline. Check
                # after every read, including EOF, before accepting any bytes.
                check_deadline()
                if not chunk:
                    break
                size += len(chunk)
                require(size <= SIZE, 'DOWNLOAD_SIZE_EXCEEDED')
                digest.update(chunk)
                target.write(chunk)
        report.update(observedBytes=size, observedSHA256=digest.hexdigest())
        report['stage'] = 'digest-validation'
        require(size == SIZE, 'DOWNLOAD_SIZE_MISMATCH')
        require(digest.hexdigest() == DIGEST, 'DOWNLOAD_DIGEST_MISMATCH')
        check_deadline()
        # Every blocking HTTP operation and byte/hash validation is finished.
        # Disarm before the local atomic publish so an alarm cannot interrupt
        # between renaming authenticated bytes and recording their success.
        absolute_deadline.close()
        check_deadline()
        report['stage'] = 'finalization'
        (output / 'artifact.zip.part').rename(output / 'artifact.zip')
        report.update(status='AUTHENTICATED_DOWNLOAD', stage='complete', reasonCode='PINNED_ARTIFACT_AUTHENTICATED')
    except Exception as error:
        absolute_deadline.close()
        report['status'] = 'BLOCKED'
        report['errors'].append(error_category(error))
        fallback = {'metadata-request': 'METADATA_REQUEST_FAILED', 'metadata-read': 'METADATA_READ_FAILED',
                    'metadata-decode': 'METADATA_INVALID_JSON', 'metadata-validation': 'METADATA_INVALID',
                    'archive-redirect': 'REDIRECT_REQUEST_FAILED', 'redirect-validation': 'REDIRECT_INVALID',
                    'storage-request': 'STORAGE_REQUEST_FAILED', 'storage-read': 'STORAGE_READ_FAILED',
                    'digest-validation': 'DIGEST_VALIDATION_FAILED', 'finalization': 'FINALIZATION_FAILED'}
        report['reasonCode'] = error.code if isinstance(error, DownloadContractError) else fallback.get(report['stage'], 'DOWNLOAD_FAILED')
    finally:
        absolute_deadline.close()
        receipt_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    result = fetch(args.output, os.environ.get('P2_ARTIFACT_READ_TOKEN', ''))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'AUTHENTICATED_DOWNLOAD' else 1


if __name__ == '__main__':
    raise SystemExit(main())
