# On-device Anisette failure: observed boundary and remaining evidence

The delivered candidate `68a560295726e3cf60b86b5409049d1cc18e1c35`
failed on a physical device before the verification-code prompt. Its safe
diagnostic reports `SS-AUTH-C11-S02-T19-A06`, `source_step=anisetteFetch`, and
`typed_error=anisetteKitADIError`. This is an actual failed acceptance result.
The successful build, simulator UI matrix and package verification did not
exercise this device's Anisette provisioning or establish real-account login.

## What the error establishes

The pinned AnisetteKit revision is
`1f5a7e36553cc865b873f222b87a6486c0bcc7bf`. Its three response parsers in
`Sources/AnisetteDataProvider.swift` construct `adiError(code:description:)`
whenever the native response contains an error. This includes local setup,
symbol and file failures as well as ADI operation errors. The iOS provider is
the Unicorn implementation. The remote SideSign provider does not construct
this AnisetteKit error.

The previous diagnostic retained the enum case but discarded its associated
Int32. Therefore `underlying=redacted/0` is not the native ADI return value.
The current record does not distinguish header generation, provisioning start,
provisioning finish, or common setup. It does not establish rejected credentials,
an Apple outage, corrupt saved provisioning, or a particular filesystem error.

The structural identity/blob guard was passed. That excludes its detected
missing/invalid-field states, but does not establish that a well-formed blob
cryptographically belongs to a well-formed identifier.

## Pinned native failure paths

In `Native/anisette_core_uc.cpp`, common setup collapses failures to outer -2.
The originating operation and inner result may exist only in a fixed native
description. Setup includes library directory checks, ELF loading,
ADILoadLibraryWithPath, ADISetProvisioningPath and ADISetAndroidID. Missing
operation symbols return -3; inability to read the completed provisioning file
returns -4. Emulator execution can return `-1000 - uc_error`. ADI calls return
their own numeric result.

Two source-supported mechanisms require discrimination before a repair:

- Provisioning start succeeds, then an awaited network request fails or is
  cancelled. `AnisetteClient.runProvisioningFlow` has no failure cleanup calling
  the existing native `cancel_provision_uc`. A pending session is possible.
- Native setup ignores the UUID directory's mkdir result. Header generation
  ignores failed fopen or fwrite while staging the supplied blob, then invokes
  ADIOTPRequest. A staging failure can surface as an ADI error.

Other uncertainties include existing library validity and the process-global
native VM. Existence checks do not validate ELF bytes, and an initialized VM
does not reload for a changed library directory. None of these mechanisms has
been attributed to this device.

## Comparison and evidence limits

The delivered ODA manager retains the pinned base-directory calculation,
provider choice, library/provisioning arguments and native parameters. The
identity patch uses an immutable validated keychain snapshot and guarded blob
commit. Original interactive execution and the headless LiveProcess execution
have different process/sandbox contexts; equal source does not prove equal
runtime path access or initialization state.

The newer original SideStore revision
`12a496ca1c766a102193634879823d16610bf1cd` retains the same Anisette directory
and shared-directory extension. Its `Bundle.appGroups` switches from plist
metadata to signed entitlements, but LiveContainer's installed dynamic
`altstoreAppGroup` hook returns `LCSharedUtils.appGroupID` directly. That change
therefore does not alter the normal embedded ODA root after hooks are installed.

Upstream reports describe OTP -45061 in SideStore issues 1501 and 1537,
foreground versus cold/widget behavior in SideStore issue 1600, and provisioning
start -45063 in LiveContainer issue 1583. These are corroborating reports,
not controlled evidence of this device's cause. The newer AnisetteKit commit
db8b410 changes logging and a Unicorn logging build; the inspected source
comparison does not supply a native correctness fix to adopt blindly.

The bounded evidence correction retains typed native numeric results and a
closed classification of exact pinned native producer messages. Arbitrary
descriptions, paths, identifiers, provisioning bytes, session handles, request
payloads and credentials must never enter the copied diagnostics. Unrecognized
messages remain unknown. No identity reset, deletion, provider switch,
automatic retry or ADI algorithm change is justified by the current record.
Dynamic path-bearing native descriptions intentionally retain their outer code
with unknown phase; unknown phase must not be interpreted as excluding a
directory or library-read failure.

The `39c474ee` candidate's purpose was to identify the failing native boundary.
It must not be presented as a confirmed repair. The subsequent confirmed OTP
`-45061` result and bounded recovery design are recorded in
`P0_VERIFIED_ANISETTE_RECOVERY.md`.
