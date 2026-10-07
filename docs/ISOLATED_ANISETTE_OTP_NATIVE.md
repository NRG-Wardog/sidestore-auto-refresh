# Isolated existing-blob OTP boundary

This is an OTP-only native primitive for a separately reviewed recovery policy.
It does not identify a recovery candidate, write Keychain, replace a UUID/blob,
select another Android-ID format, reprovision, or make network requests.

## Interfaces and reproducible input

`scripts/patch_anisette_isolated_otp.py CHECKOUT` patches exactly AnisetteKit
`1f5a7e36553cc865b873f222b87a6486c0bcc7bf`. All five original Git blobs are read
before any writes; only original or exactly transformed working bytes are
accepted. `--verify` requires already-prepared bytes. `--evidence-output FILE`
emits the pin, source/prepared SHA-256 for all five files, retained marker,
native symbol, and Swift API. `expected_evidence()` derives the same expectations
from bundled source fixtures validated against fixed hashes, without Git/network.
Verified current-user-owned source files are replaced atomically through an
exclusive same-directory temporary file. Original permissions, including 0444
SwiftPM read-only files and executable bits, are preserved. Parent-directory
permissions are not changed; an unwritable directory fails closed.

- C: `get_anisette_headers_isolated_uc(lib_dir, provisioning_dir, identifier,
  adi_pb, adi_pb_len, out_json)`
- Swift: `IsolatedAnisetteOTPProvider.getExistingHeaders(libDir:provisioningDir:
  identifier:existingBlob:headers:) async throws -> [String: String]`
- Retained native literal: `V3_ISOLATED_ANISETTE_OTP_V1`
- Native symbol: `get_anisette_headers_isolated_uc`

The public Swift method creates an exclusive 0700 `mkdtemp` child under the
caller-owned temporary directory. It uses the original AnisetteClient memory
path with a nonempty existing blob and a private provider whose start/end
provisioning methods always throw. This retains the original header construction
without granting its unconditional memory cleanup ownership over caller files.
The result must contain no new blob. Cancellation is checked before and after
the synchronous native probe.

## Native ownership and containment

The C API requires a private, current-user-owned root, a nonempty blob no larger
than 1 MiB, and the usual 16-byte UUID pointer contract. It exclusively creates
the UUID child, stages bytes through an exclusive 0600 temporary file, checks
write/flush/close and exact readback, then atomically renames the owned file.
Staging precedes all library initialization and uses the unchanged compact
Android-ID derivation.

The existing native mutex covers staging, context substitution, setup, OTP,
destruction and cleanup. A fresh VM temporarily replaces the shared VM and
cached path/ID/library state; RAII restores those globals on returnable success,
error and exception paths. `run_vm_procedure` restores its active-VM fallback,
and VM destruction closes remaining guest-owned host descriptors and Unicorn.
Library input files and newly opened guest descriptors retain scoped ownership
until allocation/map insertion succeeds. Probe-native logging is suppressed by
a thread-local guard, restored on returnable exits; the normal logging setting
and unrelated threads are unchanged.

Probe guest filesystem access is strictly read-only. Guest opens with writable,
create, truncate, append or temporary-file flags are rejected. Guest write,
ftruncate, mkdir, chmod and umask callbacks return EPERM before touching host
state. Other callback routes do not invoke host mutation APIs. Normal VMs retain
their original writable behavior. If opaque native OTP/setup requires one of
these mutations, the probe fails closed; compatibility is not assumed.

Every procedure, including library constructors, receives probe defaults of
five seconds and 50 million instructions when no explicit limits were supplied.
OTP output addresses/read results and nonzero lengths are checked; MID and OTP
are capped at 4096 bytes each. Fixed staging/cleanup errors use -6; fixed
initialization/allocation errors use -7. No path/blob appears in those messages.
Returned JSON remains malloc-owned and uses the original Swift response parser.

## Validation and limits

Offline tests compile the actual transformed C boundary and actual extracted
loader functions against synthetic I/O/Unicorn doubles. They exercise checked
staging faults, exact-byte readback, concurrent calls, unchanged regular state,
VM lifecycle, active-VM restoration, every mutation callback, retained marker,
and unchanged normal writable hooks. Original fixture files and license are
copied verbatim from the declared AnisetteKit pin; no Apple binary, provisioning
blob, credentials or authentication payload is included.

These tests establish containment and control flow. They do not model Apple's
cache behavior or demonstrate a successful OTP against an Apple library.
Swift/native Apple SDK compilation and real-device acceptance remain separate
requirements. Native probing temporarily consumes an additional VM's resources.

Upstream fatal `abort()` paths, such as virtual heap exhaustion, remain unchanged.
Fatal termination or OS process death cannot run in-memory RAII cleanup. The
calling recovery policy must never commit persisted state before a valid probe
result, so process death leaves the original stored identity/blob unchanged.
No claim is made that these fatal paths are recoverable.
