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
