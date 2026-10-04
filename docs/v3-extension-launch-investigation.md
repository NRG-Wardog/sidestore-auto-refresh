# LiveProcess activation investigation

## Evidence and scope

The report concerns Unified LC+SS v3.0.2, an iPhone 17 Pro Max on iOS 27.0.1, and a build re-signed with reported iLoader 2.5.4. Reinstalling the same source IPA did not resolve it. One copied failure has Cocoa code 3587; different attempts in history have Cocoa code 3. No installed signed package or same-attempt system error chain is available. These observations do not establish an OS, signer, or executable defect.

This is separate from the confirmed Developer Portal App ID-limit result 9120. Authentication, pairing and VPN are not prerequisites for reaching this launch boundary.

## Production path

The v3.0.2 builder tag is d6607cd379136c0906a71c35cec9e457e32d68a3. Its LiveContainer pin, also used by this candidate, is 12377cf3b91d51739a33f14a302e5f522b238593.

1. `CombinedServiceConnection.begin` resolves the host's launch inputs.
2. `RefreshHandler.discoverExtension` checks LiveProcess.appex's bundle identifier and executable, then constructs the private `NSExtension` object.
3. The host creates an anonymous `NSXPCListener`, placing its endpoint and the selected built-in SideStore launch payload in an extension item.
4. Generated `SideStoreSupport/XPCServer.m` calls `beginExtensionRequestWithInputItems`. Its completion provides a request UUID; the host queries the corresponding process identifier.
5. LiveProcess's `com.apple.ar.viewer` extension point and `LiveProcessHandler` enter `NSExtensionMain`. The handler receives the payload and hands off to `LiveContainerMain`.
6. Host XPC acceptance, SideStore application-ready notification and the service-readiness probe establish progressively stronger startup evidence. XPC acceptance alone is not service readiness.

Actual extension identifiers come from the embedded bundle's metadata, not an assumed original unsigned identifier. Re-signing can change that metadata and its matching provisioning/entitlements.

## Proven diagnostic defect

The old bridge manufactured `NSCocoaErrorDomain/NSExecutableLoadError` (3587) whenever its callback returned no UUID. It attached no underlying error. Thus that reported 3587 was not necessarily an error emitted by iOS and cannot establish dyld or code-signature rejection. The project does not wrap this synthetic error in Cocoa code 3. An actual OS cancellation error could contain either code in a nested chain, but the supplied separate-attempt diagnostics cannot prove that relationship.

The bridge now uses a project-specific no-request-identifier error. Actual NSError domain/code chains are retained, bounded to five nodes with cycle detection and a domain allowlist. Descriptions, userInfo, filesystem paths and payloads are excluded. Host-owned diagnostics report the target role, host OS and architecture, exact callback/guard substage, and observed request/PID/XPC/application-ready/peer-rejection facts. Unobserved facts are `unknown`, never inferred from a stage label. These observations remain host-only and do not expand the service wire protocol.

This repairs the misleading diagnostic producer; it does not claim to repair the unexplained device activation failure.

## Upstream and signer comparison

- LiveContainer's iOS 27 DB1 support commit 5c8b9d4e0b2ed646a3e8f485c93135974973640c is already an ancestor of the pin. Its guest/dyld changes are not a missing activation backport.
- Inspected post-pin commit 4dbe0f9a626de801184a42c0be8d2cb105058e3d advances litehook to a7e9f599d7f0b3d77008ad62bf10cb10dab6eca9, adding the A20 `_arm64e_x1` cache suffix. No evidence identifies that cache layout or lookup failure on this reported device. It is not backported speculatively. This inspected commit is not asserted to be today's upstream HEAD.
- The inspected iLoader/isideload implementation rewrites parent/child bundle identifiers, obtains individual provisioning profiles and signs nested components. A pre-sign CI IPA cannot prove the installed package's effective signatures or entitlements. The reported version 2.5.4 was not matched to the inspected official release/source, so its exact implementation remains unverified.

No exact upstream compatibility fix, missing framework, invalid slice or signer defect has been proven for this report. Replacing the launch mechanism or advancing the entire pin would exceed the evidence.

## Verification and remaining uncertainty

The existing executable startup harness exercises production discovery failure, pre-launch stop, PID-observed/XPC timeout, owner-context enrichment, nested 3/3587 errors in both orders, bounded redaction and wire exclusion. Generic failures and service decoding must not fabricate host launch observations. The existing package verifier checks component identities, target entitlements, resources and Mach-O slices; its result applies to the built artifact, not a later re-sign.

The most precise supported boundary is extension activation or very early LiveProcess execution before confirmed XPC/service readiness. Signature/profile rejection, loader failure and early bootstrap termination remain alternatives. One future same-attempt system LiveProcess denial/termination record, correlated with the new launch diagnostics, would distinguish those alternatives. No further user logs or reinstallations are requested during this investigation. Device acceptance remains open; do not publish or close the issue.
