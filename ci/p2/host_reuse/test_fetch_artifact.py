#!/usr/bin/env python3
"""Portable downloader boundary tests. All HTTP responses are local mocks."""
from contextlib import ExitStack
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import signal
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

try:
    import fetch_artifact as f
except ModuleNotFoundError:
    # Allows testing the staging deliverable before it is copied alongside the
    # canonical downloader. The fallback is this checkout only, never a fetch.
    path = Path(__file__).resolve().parents[1] / 'builder/ci/p2/host_reuse/fetch_artifact.py'
    spec = importlib.util.spec_from_file_location('fetch_artifact', path)
    f = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f)


TOKEN = 'synthetic-ephemeral-token-DO-NOT-RETAIN'
SIGNED_URL = 'https://artifact-test.blob.core.windows.net/container/artifact.zip?sig=synthetic-secret-signature'


class Response(io.BytesIO):
    def __init__(self, raw=b'', on_read=None):
        super().__init__(raw)
        self.on_read = on_read
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        value = super().read(size)
        if self.on_read:
            self.on_read(value)
        return value


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if not self.responses:
            raise AssertionError('Unexpected HTTP request')
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def redirect(location=SIGNED_URL, code=302):
    return urllib.error.HTTPError(f.API + '/zip', code, 'redirect', {'Location': location}, None)


def metadata():
    return {'id': f.ARTIFACT_ID, 'name': f.ARTIFACT_NAME, 'expired': False,
            'size_in_bytes': f.SIZE, 'digest': 'sha256:' + f.DIGEST,
            'workflow_run': {'id': f.RUN_ID, 'head_sha': f.COMMIT, 'repository_id': f.REPOSITORY_ID,
                             'head_repository_id': f.REPOSITORY_ID, 'head_branch': f.BRANCH}, 'archive_download_url': f.API + '/zip'}


class FetchArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'download'
        self.raw = b'synthetic artifact payload: never executed\n'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(f, 'SIZE', len(self.raw)))
        self.stack.enter_context(patch.object(f, 'DIGEST', hashlib.sha256(self.raw).hexdigest()))
        # A test must never accidentally fall back to the real network.
        self.network_guard = self.stack.enter_context(patch.object(f.urllib.request, 'urlopen', side_effect=AssertionError('Real network forbidden')))

    def opener(self, *, data=None, location=SIGNED_URL, storage=None):
        return Opener([Response(json.dumps(metadata() if data is None else data).encode()),
                       redirect(location), Response(self.raw) if storage is None else storage])

    def fetch(self, opener):
        result = f.fetch(self.output, TOKEN, opener)
        self.assertEqual(json.loads((self.output / 'download.json').read_text()), result)
        report = (self.output / 'download.json').read_text()
        self.assertNotIn(TOKEN, report)
        self.assertNotIn('synthetic-secret-signature', report)
        self.assertNotIn(SIGNED_URL, report)
        return result

    def assert_blocked(self, opener):
        report = self.fetch(opener)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertTrue(report['errors'])
        self.assertFalse((self.output / 'artifact.zip').exists())
        self.assertRegex(report['reasonCode'], '^[A-Z_]+$')
        self.assertRegex(report['stage'], '^[a-z-]+$')
        self.assertLess(len(json.dumps(report)), 4096)
        return report

    def test_success_exact_bytes_and_api_only_credentials(self):
        opener = self.opener()
        report = self.fetch(opener)
        self.assertEqual(report['status'], 'AUTHENTICATED_DOWNLOAD')
        self.assertEqual(report['observedBytes'], len(self.raw))
        self.assertEqual(report['observedSHA256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual((self.output / 'artifact.zip').read_bytes(), self.raw)
        self.assertFalse((self.output / 'artifact.zip.part').exists())
        self.assertFalse(report['tokenForwardedToStorage'])
        self.assertEqual([request.full_url for request, _ in opener.calls], [f.API, f.API + '/zip', SIGNED_URL])
        self.assertEqual([request.get_header('Authorization') for request, _ in opener.calls], ['Bearer ' + TOKEN, 'Bearer ' + TOKEN, None])
        self.assertTrue(all(0 < timeout <= 20 for _, timeout in opener.calls))
        self.assertTrue(all(TOKEN not in request.full_url for request, _ in opener.calls))
        self.assertFalse(opener.responses)

    def test_default_opener_uses_no_redirect_handler(self):
        opener = self.opener()
        with patch.object(f.urllib.request, 'build_opener', return_value=opener) as build:
            result = f.fetch(self.output, TOKEN)
        self.assertEqual(result['status'], 'AUTHENTICATED_DOWNLOAD')
        self.assertIsInstance(build.call_args.args[0], f.NoRedirect)

    def test_redirect_handler_never_automatically_follows(self):
        handler = f.NoRedirect()
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                self.assertIsNone(handler.redirect_request(None, None, code, 'redirect', {}, SIGNED_URL))

    def test_metadata_identity_fields_reject_overrides(self):
        mutations = [lambda d: d.update(id=f.ARTIFACT_ID + 1), lambda d: d.update(id=str(f.ARTIFACT_ID)), lambda d: d.update(name='wrong-name'),
                     lambda d: d.update(size_in_bytes=float(f.SIZE)), lambda d: d['workflow_run'].update(id=float(f.RUN_ID)),
                     lambda d: d.update(expired=True), lambda d: d.update(expired=0),
                     lambda d: d.update(size_in_bytes=f.SIZE + 1), lambda d: d.update(digest='sha256:' + '0' * 64),
                     lambda d: d['workflow_run'].update(id=f.RUN_ID + 1),
                     lambda d: d['workflow_run'].update(head_sha='0' * 40),
                     lambda d: d['workflow_run'].update(repository_id=f.REPOSITORY_ID + 1),
                     lambda d: d['workflow_run'].update(head_repository_id=f.REPOSITORY_ID + 1),
                     lambda d: d['workflow_run'].update(repository_id=float(f.REPOSITORY_ID)),
                     lambda d: d['workflow_run'].update(head_branch='other-branch'),
                     lambda d: d.update(archive_download_url='https://api.github.com/repos/other/actions/artifacts/1/zip')]
        for index, mutate in enumerate(mutations):
            data = metadata()
            mutate(data)
            with self.subTest(index=index), self.assertRaises(ValueError):
                f.validate_metadata(data)

    def test_metadata_missing_and_nonobject_rejected(self):
        for data in (None, [], {}, {'id': True}, {'id': f.ARTIFACT_ID}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                f.validate_metadata(data)

    def test_bad_metadata_stops_before_archive_request(self):
        data = metadata()
        data['workflow_run']['head_sha'] = '0' * 40
        opener = self.opener(data=data)
        self.assert_blocked(opener)
        self.assertEqual(len(opener.calls), 1)
        self.assertFalse((self.output / 'artifact.zip.part').exists())

    def test_invalid_metadata_json_retains_category_only(self):
        opener = Opener([Response(('invalid json ' + TOKEN + ' ' + SIGNED_URL).encode())])
        report = self.assert_blocked(opener)
        self.assertEqual(report['errors'], ['JSONDecodeError'])
        self.assertEqual(report['reasonCode'], 'METADATA_INVALID_JSON')
        self.assertEqual(report['stage'], 'metadata-decode')

    def test_metadata_read_is_bounded(self):
        response = Response(json.dumps(metadata()).encode())
        opener = Opener([response, redirect(), Response(self.raw)])
        self.fetch(opener)
        self.assertEqual(response.read_sizes, [1024 * 1024 + 1])

    def test_allowed_redirect_hosts_https_and_port(self):
        for url in (SIGNED_URL, 'https://tenant.actions.githubusercontent.com/a?token=synthetic',
                    'https://tenant.blob.core.windows.net:443/a'):
            with self.subTest(url=url):
                f.validate_location(url)

    def test_unsafe_redirect_urls_rejected(self):
        values = ['http://tenant.blob.core.windows.net/a', 'file:///etc/passwd',
                  'https://tenant.blob.core.windows.net.evil.invalid/a', 'https://blob.core.windows.net/a',
                  'https://actions.githubusercontent.com/a', 'https://evil.invalid/a',
                  'https://tenant.blob.core.windows.net:8443/a', 'https://user@tenant.blob.core.windows.net/a',
                  'https://user:pass@tenant.blob.core.windows.net/a', 'https://@tenant.blob.core.windows.net/a',
                  'https://tenant.blob.core.windows.net/a\n', 'https://tenant.blob.core.windows.net\\@evil.invalid/a', 'https://tenant.blob.core.windows.net/a#fragment',
                  'https://tenant.blob.core.windows.net:notaport/a', '//tenant.blob.core.windows.net/a', '']
        for url in values:
            with self.subTest(url=url), self.assertRaises(ValueError):
                f.validate_location(url)

    def test_unsafe_redirect_never_receives_token_or_request(self):
        opener = self.opener(location='https://evil.invalid/?token=' + TOKEN)
        self.assert_blocked(opener)
        self.assertEqual(len(opener.calls), 2)
        self.assertTrue(all(request.full_url.startswith(f.API) for request, _ in opener.calls))

    def test_direct_api_download_is_rejected(self):
        direct = Response(self.raw)
        opener = Opener([Response(json.dumps(metadata()).encode()), direct])
        self.assert_blocked(opener)
        self.assertTrue(direct.closed)
        self.assertEqual(len(opener.calls), 2)

    def test_only_initial_302_redirect_accepted(self):
        opener = Opener([Response(json.dumps(metadata()).encode()), redirect(code=307)])
        self.assert_blocked(opener)
        self.assertEqual(len(opener.calls), 2)

    def test_storage_redirect_is_not_followed(self):
        storage = urllib.error.HTTPError(SIGNED_URL, 302, 'redirect ' + TOKEN, {'Location': 'https://evil.invalid/?secret=' + TOKEN}, None)
        opener = self.opener(storage=storage)
        report = self.assert_blocked(opener)
        self.assertEqual(report['errors'], ['HTTPError'])
        self.assertEqual(len(opener.calls), 3)
        self.assertIsNone(opener.calls[-1][0].get_header('Authorization'))

    def test_storage_errors_do_not_leak_signed_url_or_token(self):
        storage = urllib.error.URLError('failed URL ' + SIGNED_URL + ' with credential ' + TOKEN)
        report = self.assert_blocked(self.opener(storage=storage))
        self.assertEqual(report['errors'], ['URLError'])

    def test_api_error_does_not_leak_token(self):
        opener = Opener([urllib.error.HTTPError(f.API + '?token=' + TOKEN, 403, 'bad credential ' + TOKEN, {}, None)])
        report = self.assert_blocked(opener)
        self.assertEqual(report['errors'], ['HTTPError'])

    def test_digest_failure_retains_receipt_and_never_finalizes(self):
        storage = Response(b'x' * len(self.raw))
        report = self.assert_blocked(self.opener(storage=storage))
        self.assertEqual(report['observedBytes'], len(self.raw))
        self.assertNotEqual(report['observedSHA256'], f.DIGEST)
        self.assertTrue((self.output / 'artifact.zip.part').exists())

    def test_short_size_failure_retains_receipt(self):
        report = self.assert_blocked(self.opener(storage=Response(self.raw[:-1])))
        self.assertEqual(report['observedBytes'], len(self.raw) - 1)

    def test_oversize_failure_stops_before_writing_overbound_chunk(self):
        report = self.assert_blocked(self.opener(storage=Response(self.raw + b'overflow')))
        self.assertEqual(report['errors'], ['ValueError'])
        self.assertEqual((self.output / 'artifact.zip.part').read_bytes(), b'')

    def test_deadline_before_first_request_retains_receipt(self):
        opener = self.opener()
        with patch.object(f.time, 'monotonic', side_effect=[0, 181]):
            report = self.assert_blocked(opener)
        self.assertEqual(report['errors'], ['ValueError'])
        self.assertEqual(opener.calls, [])
        self.assertEqual(report['reasonCode'], 'DOWNLOAD_DEADLINE_EXHAUSTED')
        self.assertEqual(report['stage'], 'metadata-request')

    def test_deadline_after_data_read_retains_receipt(self):
        clock = [0]
        storage = Response(self.raw, on_read=lambda data: clock.__setitem__(0, 181))
        with patch.object(f.time, 'monotonic', side_effect=lambda: clock[0]):
            self.assert_blocked(self.opener(storage=storage))

    def test_deadline_crossed_by_final_eof_read_must_not_authenticate(self):
        clock = [0]
        def delayed_eof(data):
            if not data:
                clock[0] = 181
        storage = Response(self.raw, on_read=delayed_eof)
        with patch.object(f.time, 'monotonic', side_effect=lambda: clock[0]):
            self.assert_blocked(self.opener(storage=storage))


    def test_extra_metadata_is_not_retained_in_receipt(self):
        data = metadata()
        data['workflow_run']['untrusted_signed_url'] = SIGNED_URL
        data['workflow_run']['untrusted_token'] = TOKEN
        data['untrusted_extra'] = TOKEN * 500
        result = self.fetch(self.opener(data=data))
        self.assertEqual(result['status'], 'AUTHENTICATED_DOWNLOAD')
        self.assertEqual(set(result['metadata']['workflow_run']), {'id', 'head_sha', 'repository_id', 'head_repository_id', 'head_branch'})
        self.assertLess(len(json.dumps(result)), 4096)

    def test_duplicate_metadata_keys_are_rejected(self):
        raw = json.dumps(metadata())[:-1] + ', "id": ' + str(f.ARTIFACT_ID) + '}'
        report = self.assert_blocked(Opener([Response(raw.encode())]))
        self.assertEqual(report['reasonCode'], 'METADATA_DUPLICATE_KEY')
        self.assertEqual(report['stage'], 'metadata-decode')

    def test_metadata_byte_limit_failure_is_redacted(self):
        raw = b'x' * (f.MAX_METADATA_BYTES + 1)
        report = self.assert_blocked(Opener([Response(raw)]))
        self.assertEqual(report['reasonCode'], 'METADATA_TOO_LARGE')
        self.assertEqual(report['stage'], 'metadata-read')

    def test_failure_codes_distinguish_metadata_redirect_storage_digest(self):
        cases = []
        data = metadata()
        data['name'] = TOKEN
        cases.append((self.opener(data=data), 'metadata-validation', 'METADATA_ARTIFACT_NAME_MISMATCH'))
        cases.append((self.opener(location='https://evil.invalid/?token=' + TOKEN), 'redirect-validation', 'REDIRECT_STORAGE_HOST_DENIED'))
        cases.append((self.opener(storage=urllib.error.URLError(SIGNED_URL)), 'storage-request', 'STORAGE_REQUEST_FAILED'))
        cases.append((self.opener(storage=Response(b'x' * len(self.raw))), 'digest-validation', 'DOWNLOAD_DIGEST_MISMATCH'))
        for index, (opener, stage, code) in enumerate(cases):
            self.output = self.root / ('case-%d' % index)
            with self.subTest(stage=stage):
                report = self.assert_blocked(opener)
                self.assertEqual(report['stage'], stage)
                self.assertEqual(report['reasonCode'], code)

    def test_metadata_read_crossing_deadline_cannot_continue(self):
        clock = [0]
        response = Response(json.dumps(metadata()).encode(), on_read=lambda data: clock.__setitem__(0, 181))
        opener = Opener([response])
        with patch.object(f.time, 'monotonic', side_effect=lambda: clock[0]):
            report = self.assert_blocked(opener)
        self.assertEqual(report['stage'], 'metadata-read')
        self.assertEqual(report['reasonCode'], 'DOWNLOAD_DEADLINE_EXHAUSTED')
        self.assertEqual(len(opener.calls), 1)


    def slow_drip_response(self, raw, groups):
        """A real HTTPResponse over socketpair, with progress every 15 ms.

        The socket inactivity timeout is deliberately longer than each drip;
        only an independent absolute deadline can interrupt its read promptly.
        """
        reader, writer = socket.socketpair()
        reader.settimeout(0.15)
        writer.sendall(('HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n' % len(raw)).encode())
        response = http.client.HTTPResponse(reader)
        response.begin()
        stopped = threading.Event()
        width = max(1, len(raw) // groups)
        def drip():
            try:
                for start in range(0, len(raw), width):
                    if stopped.wait(0.015):
                        break
                    writer.sendall(raw[start:start + width])
            except OSError:
                pass  # The deadline closes the receiving end deliberately.
            finally:
                writer.close()
        thread = threading.Thread(target=drip, daemon=True)
        thread.start()
        def cleanup():
            stopped.set()
            response.close()
            reader.close()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive(), 'Local drip worker did not stop')
        self.addCleanup(cleanup)
        return response

    def test_real_socket_slow_drip_storage_is_interrupted_by_absolute_deadline(self):
        handler = signal.getsignal(signal.SIGALRM)
        response = self.slow_drip_response(self.raw, groups=len(self.raw))
        started = time.monotonic()
        with patch.object(f, 'DEADLINE_SECONDS', 0.05):
            report = self.assert_blocked(self.opener(storage=response))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.30, 'Slow-drip response defeated absolute deadline')
        self.assertGreaterEqual(elapsed, 0.025)
        self.assertEqual(report['stage'], 'storage-read')
        self.assertEqual(report['reasonCode'], 'DOWNLOAD_DEADLINE_EXHAUSTED')
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(signal.getsignal(signal.SIGALRM), handler)

    def test_real_socket_slow_drip_metadata_is_interrupted_by_absolute_deadline(self):
        response = self.slow_drip_response(json.dumps(metadata()).encode(), groups=50)
        started = time.monotonic()
        with patch.object(f, 'DEADLINE_SECONDS', 0.05):
            report = self.assert_blocked(Opener([response]))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.30, 'Slow-drip metadata defeated absolute deadline')
        self.assertGreaterEqual(elapsed, 0.025)
        self.assertEqual(report['stage'], 'metadata-read')
        self.assertEqual(report['reasonCode'], 'DOWNLOAD_DEADLINE_EXHAUSTED')

    def test_absolute_deadline_restores_handler_on_success_and_failure(self):
        previous = signal.getsignal(signal.SIGALRM)
        handler = lambda signum, frame: None
        signal.signal(signal.SIGALRM, handler)
        try:
            self.fetch(self.opener())
            self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            self.output = self.root / 'failure'
            self.assert_blocked(self.opener(storage=urllib.error.URLError(SIGNED_URL)))
            self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        finally:
            signal.signal(signal.SIGALRM, previous)

    def test_existing_timer_is_preserved_and_fails_closed(self):
        previous = signal.getsignal(signal.SIGALRM)
        handler = lambda signum, frame: None
        signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, 5)
        opener = self.opener()
        try:
            report = self.assert_blocked(opener)
            self.assertEqual(report['reasonCode'], 'DEADLINE_TIMER_ALREADY_ACTIVE')
            self.assertEqual(opener.calls, [])
            remaining, interval = signal.getitimer(signal.ITIMER_REAL)
            self.assertGreater(remaining, 4)
            self.assertEqual(interval, 0)
            self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def test_non_main_thread_fails_closed_before_http(self):
        opener = self.opener()
        results = []
        worker = threading.Thread(target=lambda: results.append(f.fetch(self.output, TOKEN, opener)))
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'BLOCKED')
        self.assertEqual(results[0]['reasonCode'], 'DEADLINE_MAIN_THREAD_REQUIRED')
        self.assertEqual(opener.calls, [])
        self.assertEqual(json.loads((self.output / 'download.json').read_text()), results[0])

    def test_existing_output_is_not_overwritten(self):
        self.output.mkdir()
        marker = self.output / 'existing'
        marker.write_bytes(b'keep')
        opener = self.opener()
        with self.assertRaisesRegex(ValueError, 'OUTPUT_NOT_FRESH_UNLINKED'):
            f.fetch(self.output, TOKEN, opener)
        self.assertEqual(marker.read_bytes(), b'keep')
        self.assertEqual(opener.calls, [])

    def test_symlinked_output_parent_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'link'
        link.symlink_to(outside, target_is_directory=True)
        opener = self.opener()
        with self.assertRaisesRegex(ValueError, 'OUTPUT_NOT_FRESH_UNLINKED'):
            f.fetch(link / 'download', TOKEN, opener)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(opener.calls, [])

    def test_missing_token_never_makes_request(self):
        opener = self.opener()
        with self.assertRaisesRegex(ValueError, 'TOKEN_MISSING'):
            f.fetch(self.output, '', opener)
        self.assertEqual(opener.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
