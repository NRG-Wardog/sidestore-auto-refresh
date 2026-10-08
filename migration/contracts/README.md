# Seven-owner runtime contract gate

Status: **build-only candidate for review**, based on integration commit
`141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee`. No active dependency pins, workflows,
fork files, runtime constants, IPC implementation, signing settings or remotes
were changed by this artifact. Nothing here claims the real-device
`nativeOTP/-45061` incident, existing cleanup race or preserved logging is fixed.

## What this checks

The compatibility set is `runtime-source-parity-141776ba.v1`. Runtime contracts
remain version `1.0.0`. The formatting-updated jktcp and idevice owner manifests
are revision `1.0.1`; other owner manifests remain `1.0.0`. All use **exact equality**.
These versions are build metadata; it is unrelated to product v3.1.0, package
release numbers or the existing version fields in runtime messages. There is
no runtime negotiation, generated constants file or replacement shared module.

The gate checks exactly these seven owners: LiveContainer, SideStore,
AnisetteKit, SideSign, minimuxer, idevice and jktcp. It locks 88 complete,
contract-bearing source files to ten explicit contract identities and 22
consumer/provider edges:

| Build contract | Approved source providers | Approved consumers |
|---|---|---|
| app-group-identity | LiveContainer, SideStore | Each consumes the other |
| xpc-service | LiveContainer, SideStore | Each consumes the other |
| refresh-results | LiveContainer, SideStore | Each consumes the other |
| auth-service-messages | LiveContainer, SideStore | Each consumes the other |
| diagnostics | All seven | LiveContainer ↔ SideStore; AnisetteKit → SideStore/SideSign; SideSign/minimuxer → SideStore; idevice → minimuxer; jktcp → idevice |
| anisette-provider | AnisetteKit | SideStore, SideSign |
| sidesign-auth | SideSign | SideStore |
| transport-capabilities | minimuxer | SideStore |
| idevice-ffi | idevice | minimuxer |
| jktcp-stream | jktcp | idevice |

Provider here means the owner of a source implementation or contract surface,
not a claim about which executable process loads the file. For example, the
LiveContainer-owned XPC protocol header/client source also participates in the
embedded service's interface. SideStore owns the dispatch and application-level
service implementation. The bidirectional edges capture their compatibility
obligation without relocating their code.

The registry is the single explicit approval of the combination. Each owner
manifest lists what it provides and the exact provider contracts it requires.
Declarations bind to complete source file hashes, so preserved copies of App
Group rules, wire handling and diagnostics cannot change independently while
continuing to pass this approved set. The gate does not normalize, repair,
regenerate, or rewrite any source. See [COVERAGE.md](COVERAGE.md) for every path.

## Files

- `compatibility-registry.json`: one integration compatibility registry
- `owners/<Owner>/runtime-contract.json`: seven separately distributable owner
  manifests; these are not yet copied into their forks
- `provenance/source-evidence.json`: portable reviewed source facts, upstream
  commits and original inventory digests, separately pinned by the registry
- `validate_contracts.py`: read-only standard-library Python validator
- `tests/test_contract_gate.py`: isolated synthetic tests; no fork/runtime edits
- `provenance/assemble.py`: one-time review aid, excluded from the build gate;
  creates a fresh metadata candidate and refuses to overwrite an existing one
- `evidence/`: actual verification results for this artifact

## Invocation and trust anchor

Requires Python 3.10+ on POSIX with `O_NOFOLLOW`, directory descriptor access
and regular filesystem files. It was tested on Linux; macOS execution remains
unverified here. Run with `-B` to prevent Python bytecode output. Use physical
absolute paths with no symlinked ancestors; relative source paths stay inside
their independently supplied owner roots. Hardlinked source/metadata files
are rejected, even if their current bytes match. Use ordinary copied checkouts
or exports rather than symlink/hardlink-based source deduplication.

The reviewed registry candidate SHA-256 is:

`8e7eba95b8bc69037ffed8931478cefd46984b458f767c2a067547e3cd60b467`

This value is **an identification of this candidate, not evidence of user or
reviewer approval**. At the coordinated pin-switch, place the approved digest
in the independently reviewed/protected integration build policy alongside
the trusted validator. Do not calculate `--registry-sha256` from the candidate
registry in CI and do not read it from an adjacent candidate checksum file.
That would let an unreviewed metadata change authorize itself.

Example after review, with `CONTRACTS` pointing to this artifact and `SOURCES`
pointing to a directory containing the seven prepared owner roots:

```sh
python3 -B "$CONTRACTS/validate_contracts.py" \
  --registry "$CONTRACTS/compatibility-registry.json" \
  --registry-sha256 "$REVIEW_APPROVED_REGISTRY_SHA256" \
  --owner "LiveContainer=$SOURCES/LiveContainer" \
  --owner "SideStore=$SOURCES/SideStore" \
  --owner "AnisetteKit=$SOURCES/AnisetteKit" \
  --owner "SideSign=$SOURCES/SideSign" \
  --owner "minimuxer=$SOURCES/minimuxer" \
  --owner "idevice=$SOURCES/idevice" \
  --owner "jktcp=$SOURCES/jktcp"
```

Output is one JSON result on stdout with exit status 0, or a failure on stderr
with a nonzero status. There is no `--update`, autofix, network resolution or
fallback to a less strict version. `--registry-sha256` is mandatory.

For owner-committed manifests, add **all seven** `--manifest OWNER=/absolute/path`
arguments, using the exact files from the owner checkouts. They must have the
registry-approved raw bytes. Without these flags, manifests are read from the
artifact paths recorded in the registry. Moving a manifest into a fork does
not require changing its content. Updating the registry paths later is itself
a reviewable registry change. The source root flags are still mandatory and
must point to the code that will actually be compiled, including the effective
AnisetteKit package and child dependency checkouts.

Tests:

```sh
python3 -B -m unittest discover -s "$CONTRACTS/tests" -v
```

Tests construct temporary, synthetic seven-owner trees underneath this
artifact's `evidence` directory, then remove them. They never modify fork
checkouts or use OLD source directories. Some negative tests deliberately
simulate approving a new registry in order to exercise structural checks
beyond the digest layer. This test helper is not a production reapproval path.

## Fail-closed checks

- Exact owner set in roots, registry, source evidence and explicit manifest
  mappings; duplicate CLI owner entries are also rejected
- Authenticated raw registry bytes against the caller's trusted digest;
  authenticated raw owner-manifest and source-evidence bytes against registry
  hashes, including changed whitespace
- Strict schemas, unique JSON keys, supported integer format version, the
  frozen integration baseline, owner identity, set identity and exact versions
- Exact approved provider declarations and consumer/provider edges; missing,
  extra, duplicate, mismatched or unfulfilled declarations fail
- Source lists/provenance identical to reviewed evidence; each declared
  contract references hashed source files, and every listed source is bound
- Every listed source's SHA-256 and Git executable/non-executable mode
- No path traversal, absolute metadata-relative paths, `.git` traversal,
  alternate separator/drive syntax, symlinks at any traversed component,
  hardlinks, special files, special mode bits, oversized files or a file that
  changes while it is read

The complete file is hashed even when only a small part defines a protocol.
A comment, formatting or unrelated implementation change in a listed file
therefore fails intentionally. Exact byte parity is the current migration
invariant. This avoids fragile substring extraction becoming the approval
boundary and preserves duplicated runtime constants as requested.

## Provenance

The six non-Anisette owner hashes originate in the immutable OLD prepared
trees, not the actively edited source forks. Changed paths were checked against
the recorded `final_changes` hashes/modes in the combined or transport source
generation inventories. Additional unchanged contract surfaces were compared
to the exact upstream Git blobs. AnisetteKit runtime hashes/modes were checked
against the existing `anisette-source-parity.json` report. Tests, docs and fork
metadata were not treated as runtime sources.

The evidence records the original inventories' SHA-256 identifiers and, for
each source, its inventory key or upstream Git blob basis. Inventory filenames
are provenance references, not paths opened at build time. No absolute working
directory, credentials, signing keys or private device data are in the
manifests/registry/source evidence. `provenance/assemble.py` reconstructs this
candidate from explicitly supplied OLD, fork-object and inventory roots in a
fresh output directory; its output still requires review and an independently
approved anchor. Original pipeline execution and its whole-tree parity report
remain separate evidence.

## Limits: a pass is not a release or device proof

1. This is source-level compatibility approval, not a compiler, ABI checker,
   parser of message semantics, XPC integration test or runtime handshake.
   Review established that these frozen bytes belong to one accepted baseline;
   the gate enforces that statement, it does not rediscover it.
2. Scope is the 88 listed files. New files, unlisted file changes, directory
   contents, repo remotes/history, complete Git trees, dirty state, gitlinks,
   resolved dependencies, lockfiles and binaries are not checked here. Run the
   separate whole-tree OLD/NEW parity, owner/revision/ancestry, recursive gitlink,
   lock/pin, no-rewrite and artifact provenance gates as well. In particular,
   a pass does not prove that idevice links the supplied jktcp source or that
   Xcode selects the supplied AnisetteKit. Effective resolver inputs and actual
   linked output must be verified independently.
3. The validator never fetches from a remote or executes source-checkout code.
   Its trust root is the externally reviewed digest **and the validator/build
   policy itself**. A SHA-256 pin is an integrity control, not a digital
   signature, author identity, review audit trail or protection against an
   attacker who can replace both build policy and validator. No signing key is
   necessary for this model.
4. The gate must run against stable, isolated checkout/export roots before and
   after compilation. Descriptor traversal and per-file read checks do not
   create an atomic multi-repository snapshot and cannot protect a later build
   from another process rewriting files after validation.
5. Executable mode matches Git's binary distinction (`100644`/`100755`); ordinary
   read/write permission bits, ownership and ACLs are not a full OS security
   audit. Symlinked system prefixes and hardlink-based caches are unsupported
   on purpose; resolve the physical checkout location before invocation.
6. Full Xcode/iOS builds, generated FFI headers, symbol/linkage checks,
   entitlements, signing, macOS behavior, Apple compatibility and real-device
   authentication remain independent, and were not performed by this gate.
   Dormant code, automatic-recovery policy and known defects remain frozen.

## Reviewing source-contract evolution

Never “fix” a failing gate by generating new checksums in the build job.

1. Identify the owned implementation and every consumer affected by the source
   change. Review the semantic/wire/FFI effect plus error, cancellation, secret
   handoff, fail-closed identity and diagnostics behavior as appropriate.
2. Land runtime changes separately from build metadata. Preserve provenance and
   prepare OLD/NEW source diff and appropriate native/Swift/XPC/transport tests.
   Revisit this coverage inventory if the code moves or new defining files are
   introduced; do not merely delete the old binding to make validation pass.
3. Make a new owner-manifest version for any changed approved source bytes.
   Bump the affected contract version for a behavior/schema/ABI change; a
   breaking change requires a new major contract version. For proven
   non-semantic changes, contract versions may remain unchanged but the owner
   manifest version, source evidence, manifest digest and registry-set approval
   must still change. This gate accepts no semantic-version ranges implicitly.
4. Review all affected consumer requirements and provider declarations as one
   compatible set. Produce updated source evidence and a new registry identity
   with exact manifest and evidence hashes. A new build matrix requires a new
   explicit approval, even if a maintainer believes versions are compatible.
5. Review the registry diff, run all positive/negative gate tests, run it against
   the actual resolved seven checkouts, and independently validate pins,
   whole-tree provenance, resolver and build/artifact evidence. Only then update
   the externally maintained trusted registry digest in the coordinated
   integration change.

Practical next step: include the seven manifests in their respective forks as
separate build-metadata commits, commit the registry/validator to integration,
and wire this gate before and after the build **only in the reviewed coordinated
pin-switch**. Keep the frozen runtime parity commits intact. Do not retire the
old rewriting path until the complete migration gates pass.

The current Rust formatting approval follows failed full run 37708955202.
`provenance/rustfmt-1.98.1.json` preserves the exact preformat hashes, pinned
formatter identity and complete independently formatted OLD/maintained parity.
Only three source hashes change. jktcp and idevice source-manifest revisions are
1.0.1; all runtime contract versions remain 1.0.0 and all 22 edges are unchanged.
The current reviewed registry SHA-256 is `5cd17d665d9d13fde7bdc58abe3c44050b4b1618df89efbdf9815a39736d76be`.
Historical evidence and `provenance/assemble.py` retain the original preformat
meaning; the build never regenerates or relaxes source hashes.
