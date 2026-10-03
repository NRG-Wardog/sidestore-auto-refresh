# Install provisioning investigation — confirmed candidate 06fbefc1

## Evidence and scope

Installed LCBuilderCommit=06fbefc148684b3f13b6601763669d6994dab92c; CI37129397485, confirmed by user Build Diagnostics. User reports one Refresh All success, source-add success, source-download success only. These do not establish scheduled refresh, relaunch persistence or installation.

Failed install correlation7B4EEE14-5104-4A2D-BA75-BC5B7F5F51F9: signing/provisioningProfileFetch/developerPortalRejectedRequest; underlying code was lost. Exact input locally inspected: EeveeSpotify9.1.0, com.spotify.client, SHA256 f830089f81001296f46210718c5f11da116dff5c6ad237b8f9c0d37c51d75fea, five extensions. User confirms this same file installs with iLoader, same account/team, all five extensions retained, free Personal Team.

## Model before implementation

- Execution: root picker/staging token -> opStart -> AnyApp -> canonical SS AppOperation.install -> PipelineExecutor -> FetchProvisioningProfilesOperation -> App ID lookup/create -> feature update -> app-group lookup/create/assignment -> team-profile download. The label provisioningProfileFetch covers all of these.
- Owners: SideStore owns requests/business rules; AuthManager owns session/account/team; operation context snapshots signing certificate; LC owns presentation. Existing binding/cancellation checks remain. No new provisioning engine or certificate manipulation is authorized.
- First confirmed diagnostic violation: typed SideSign ServerError.underlyingError associated code is discarded before NSError/property-list capture. An NSError enum ordinal is not the server code.
- Second violation: broad phase and generic signing errors select Open Certificates without certificate evidence.
- Identity invariant: diagnostics must describe the failed invocation, not a shared last-step variable or a newer UI selection. Per-call immutable error annotations survive reordered extension tasks.
- Envelope invariant: specific upstream call context and typed code outrank the coarse pipeline label; raw provider bodies, credentials, tokens, keys and device identifiers never cross diagnostics.
- Business equivalence: original upstream call and arguments execute exactly once; existing cancellation and typed outcomes retain meaning. No automatic retry, certificate revoke/create, sign-out or data deletion.

## Root-cause status

No wrong argument or omitted upstream provisioning call has been proven. The broad rejection category supports a reported Developer Services error, not a particular certificate/profile problem. Exact failing API and numeric response are not recoverable from the supplied old envelope. Do not claim the install is fixed until that evidence identifies its cause.

Current upstream SideSign Profiles/DeveloperPortalAPI/Errors files match the pinned blobs. Current SideStore has additional profile changes (manual overrides, extension-parent mapping, coalesced group creation); assess relevance, do not backport unrelated changes blindly. The test IPA's main groups contain both groups shared by its extensions; that limits the simple duplicate-new-group hypothesis.

## Focused test contract

1. Actual production error producer -> capture -> plist round trip -> decode retains typed safe associated code and precise substep, not NSError ordinal.
2. Generic provisioning/signing errors do not select Certificates. Specific certificate validation/missing-certificate evidence still can. No speculative certificate creation advice.
3. Source/refresh/install parity remains through existing upstream operations. Input classification and pipeline call arguments are unchanged.
4. For added per-call annotations, test concurrent/reordered failures, cancellation, unknown errors and no private body leakage; annotations belong to their invocation.

Device install remains open. No publication or issue closure.

## Implemented candidate changes

- Narrow upstream backport from SideStore develop 0dd743f75afc358b0ba4a002feb5f19474492371: a preferred *parent* identifier never replaces an extension identifier; extension suffixes are preserved and non-descendant identifiers fail before registration. This is a proven conditional code defect. The reported device's matching saved/custom identifier is not yet established, so this is not claimed as its confirmed root cause.
- The user clarified that Spotify had previously been SideStore-managed but was not installed at the failing attempt. That does not establish whether the exact lookup predicate matched a retained record.
- The actual earlier working builder f3dd4538 uses the same LC/SS/SideSign pins as06fbefc1; its makeInstallDriver also calls the same canonical .install pipeline. No pin change is blamed.
- Bound DeveloperPortalProxy calls now annotate only ServerError from the actual upstream API body, after account/session/team admission. App ID lookup/register/capabilities, group lookup/register/assignment and profile retrieval/create/update retain their own source steps. Other upstream error types and requests remain unchanged.
- A small task-scoped scalar observer in pinned SideSign retains actual HTTP status and only finite-allowlisted machine error codes. It does not alter request construction, response parsing or original error types. Each invocation owns its observation; parallel extension requests cannot share a last-error slot.
- The associated integer is preserved separately as server_code; SideSign's -1 sentinel becomes unknown. NSError ordinals are not misrepresented. Typed raw provider strings cannot override structured stages or manufacture HTTP/errno diagnostics.
- Context contains only checked-binding flags, generation, hashes, counts, known capability names, role and preferred-parent-match evidence. It excludes account names, tokens, private keys, device identifiers and raw bodies. The pipeline records the operation's certificate serial hash, not a later UI selection; team-profile requests explicitly leave device registration unobserved.
- Generic provisioning/signing failures have no certificate-specific recovery route. Explicit missing-certificate or certificate-validation evidence retains inspection navigation. No speculative revoke/create/sign-out/data-deletion action was added.
- New production-code regressions replace the fabricated-NSError test. The provenance collector now behaviorally proves it leaves prepared inputs unchanged instead of banning read-only source references.

This is a corrective/diagnostic candidate. Actual install acceptance still requires the same f830089f...d75fea IPA attempt and its resulting exact request/code, or successful installation. Do not claim a primary device cause solely from a code-level conditional defect.

## Later App ID capacity evidence

The user subsequently reported iLoader refusing a new attempt with one App ID required and zero available. UnifiedLCSS had not exposed that explanation. This establishes current iLoader capacity evidence, not the exact cause of the earlier lossy SideStore failure. The adapter now preserves pinned SideSign `DeveloperPortalError.maximumAppIDLimitReached` as `appIDLimitReached`. It neither infers quota from provider text/bridged ordinals nor invents available/required counts. Immediate retries are blocked; guidance concerns team App ID capacity, without certificate replacement or destructive troubleshooting. The source/registration/provisioning operations remain upstream-owned.

A final same-contract check confirmed another adapter defect: `DeveloperPortalError` associated provider text could be read as legacy `lc_stage`, HTTP or errno tokens after NSError descent. All real typed portal errors now carry the same fixed SideSign body-guard marker as server errors. Provider descriptions cannot alter structured stage or numeric evidence. The actual pinned quota producer is tested with misleading embedded tokens, and deleting the production typed marker must fail the harness. This is an adapter integrity fix, not an upstream provisioning change.
