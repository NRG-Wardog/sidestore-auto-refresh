# Unchanged real-host Settings feasibility probe

Status: the first native attempt, run `39052489875` on builder `8d189028`,
stopped during dependency verification before product compilation or simulator
launch. The verifier rejected legitimate committed OpenSSL framework symlinks.
This test-only correction supports authenticated relative links; it changes no
product, dependency, project, or UI test bytes. Fresh native feasibility remains
unproved. Independent review precedes publication and the authorized retry.

## Question and scope

Can the **unchanged genuine LiveContainer Release project/scheme** at isolated P2
commit `eb10cedb772bc0496d67edd7a269679e24826b9f`, tree
`d6960a99a2f022a333c364fa4d28e264568a28ca`, compile and start on a fresh simulator,
reach its ordinary Settings tab, and show the actual picker selection across
English → Hebrew → English in one launch attempt?

The product's existing route is `WindowGroup → V3UnifiedShell →
V3ApplicationRoot → V3UnifiedTabs/TabView → LCSettingsView →
V3LanguageSettingsSection`. There is no extracted Form host and no product source,
project, transform, identity, authentication, startup, or preference patch.
The isolated source is **not** the final composed P0/P1/P2 successor. A successful
probe means bounded feasibility, not full-app or glyph/layout acceptance.

## Exact inputs

- Builder preparation parent: `4fe6738d3223fe632e179642d52d429866b052d1`.
  Native runs require a clean descendant builder, record its actual commit/tree,
  and match `GITHUB_SHA` when present. This does not claim a fixed future commit.
- `inputs.json` seals this lane's six adapter/document files and the four existing
  resource-contract inputs. Its externally reviewed SHA-256 is required on the
  command line and in the workflow. The workflow is retained with its own hash.
- The entire actual main source tree is byte/mode checked against Git objects,
  source commit/tree and exact upstream origin before and after the run.
- The two real gitlinks are OpenSSL `623c84da314e85363236507ca38a4bde65df21c3`
  (tree `21bc09387e7f91a46eb8bd26587ce1d052277801`) and litehook
  `8025e0c8ebdf5cdd1d2a4f45025813234bf9dc55`
  (tree `62bc06a17a1c12d805aec1ada25910bfa9f89781`).
  OpenSSL uses its declared repository. litehook uses the retained source recipe's
  `LiveContainerMirror/litehook.git`, authenticated by the identical pinned
  commit/tree, while separately checking the original declared URL.
  The stale fishhook `.gitmodules` declaration is recorded and never initialized.
  New/nested dependencies fail closed. Missing Git LFS/framework bytes are not
  replaced; they remain a dependency/build failure.
- Committed mode-`120000` links are authenticated using the raw link-target bytes
  and their Git blob hash, without dereferencing to hash the destination. The
  target SHA-256, literal target and resolved tracked destination are retained.
  Resolution follows only authenticated relative links and tracked tree paths,
  stays inside the checkout, and rejects absolute, escaping, missing, cyclic,
  excessive, untracked or substituted links. Regular source files retain their
  existing exact byte/mode checks. The pinned OpenSSL tree contains 20 such links.

## Native stages and failure boundaries

1. Verify sealed inputs, real source and empty child checkout directories; acquire
   only the two exact dependency commits and verify their trees and working bytes.
2. Create one dedicated fresh iPhone simulator using an available iOS runtime.
   Do not erase an existing device, seed preferences, or alter system language.
3. Retain `xcodebuild -showBuildSettings -json` from the genuine project, scheme
   `LiveContainer`, configuration `Release`, SDK `iphonesimulator`, and exact
   simulator destination. Build with `build`, **not** the genuine scheme's
   unrelated `test` action. There are no product `CODE_SIGN*`, team, architecture,
   framework search path, linker, entitlement, bundle-ID, or source overrides.
4. Authenticate the actual app settings, Info.plist, simulator Mach-O platform and
   full bundle file/link inventory. Preserve its archive and native build result.
   Run the existing compiled localization contract (2,088 catalog keys), exact
   six-simple-plural values across four languages, compound plural structures and
   values, and all seven permission-resource locales/aliases against that exact
   `LiveContainer.app`. No fixture results are inherited. The reused preflight
   function is only `verify_exact_plural_values`; no fixture builder is called.
5. Verify the genuine product's signature as built. Install onto the newly created
   simulator, convert raw `simctl listapps` through `plutil` rather than assuming
   OpenStep output is an XML plist, and compare every installed file/link with
   the authenticated product. Verify again immediately before and after UI tests.
6. Generate a **separate UI-test-only project**, Target Application=None, outside
   the source checkout. It has no application target, product dependency,
   `TEST_TARGET_NAME`, `TEST_HOST`, or product module import. Build it for testing,
   retain the generated xctestrun and build settings, and validate its generated
   runner package/paths/schema. Never modify generated xctestrun bytes. Unknown
   wiring, target-app substitution, injected target-app launch settings or a
   missing test host fails closed. v1 schema was observed in retained Xcode 26.4
   fixture artifacts; that observation is not proof this external recipe works.
7. The runner uses only `XCUIApplication(bundleIdentifier: actualBuiltBundleID)`.
   It supplies no launch arguments/environment. One launch and one native test
   attempts ordinary Settings navigation and the genuine `settings.language`
   picker: default English, Hebrew, then English. Exactly one real matching
   hittable choice is required. No coordinates, direct preference writes,
   accessibilityActivate shortcuts, or root relaunch are used.
8. Retain screenshots, bounded AX, raw picker labels/value/children/frames and the
   translated actual Settings tab label. Verify them against source/product/
   builder identity and the exact native XCTest result. Screenshot pixels still
   require review; this probe does not calculate or certify glyph direction.

Every failed stage yields `BLOCKED`, an exact stage/error, command stdout/stderr,
any native result bundles and partial products. The success string is deliberately
`FEASIBLE_SETTINGS_PICKER`, with full-app/final-composition acceptance always false.
The app/package and UI results are separate receipts. A package failure blocks
later navigation; a later startup failure does not erase successful package proof.

## Narrow startup cancellation

The pinned product defines a known app-owned explanation:

- Title: `Stay Informed About Refreshes`
- Body: `LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.`
- Exactly two actions: `Allow Notifications` and `Later`

Only a unique exact UTF-8 title/body match and the exact action inventory permit a
real, hittable **Later** tap. Its source closure is empty with role `.cancel`.
The permission-request action is never tapped. Capture before/after screenshots
and AX, plus the actual observed label inventory and action receipt. This is
ordinary cancellation, not a seeded preference or disabled startup path.
All unknown alerts, entitlement errors, sign-in/authentication prompts and OS
permission dialogs remain blockers. No generic dismissal or interruption handler
is installed. Unrecognized accessibility exposure is itself a feasibility blocker.

## Commands and retention

Portable tests, including exact local source verification, without Xcode:

```sh
P2_HOST_PROBE_SOURCE=/absolute/path/to/maintained-source \
  python3 -B -m unittest discover -s ci/p2/host_probe -p test_probe.py -v
```

After review and explicit authorization, on the selected macOS/Xcode runner:

```sh
python3 -B ci/p2/host_probe/run.py \
  --host /absolute/path/to/maintained-source \
  --work /absolute/path/to/new-host-probe-work \
  --evidence /absolute/path/to/new-host-probe-evidence \
  --approved-inputs-sha256 REVIEWED_SHA256
```

Both output directories must be new and disjoint from the source and each other.
The workflow uses the same source/Xcode pins as the existing isolated lane, but
owns only its new paths and evidence. Publishing changes under these workflow
paths to `validation/p2-alert-lifecycle` triggers this new native lane, so do not
publish before the native-run authorization is resolved.

The final receipt includes source/dependency proofs, original source archive,
sealed adapter and contract bytes, workflow and toolchain logs, complete product
and runner settings, generated runner sources/xctestrun, product/installed hashes,
compiled-resource outputs, `.xcresult` bundles, exported test summary/tree and
attachments, final simulator screenshot, a three-minute process-filtered log,
partial products even on failure, and a SHA-256 inventory. Only the created
simulator is shut down; no device is erased or reused.

## Independent-review questions and limits

- Does this Xcode support an external UI-test-only target with no Target
  Application, and does its unchanged generated xctestrun satisfy the retained
  schema? If not, record the precise wiring failure. No reviewed proven recipe
  is claimed and no synthetic host fallback is authorized.
- Does the genuine Release dependency graph support this simulator architecture,
  the device-oriented OpenSSL/search-path inputs, and the product's real startup
  and entitlement checks? No architecture or constructor workaround is supplied.
- Do actual picker/menu AX nodes expose the expected unique identifiers, values
  and options? Unsupported exposure is not treated as a passed transition.
- Does a returned English screenshot visibly reproduce the fixture's mirrored
  glyphs? Public source or AX matches cannot answer this without pixel review.
- Selection is observed through real UI. No preference-file scrape, private
  runtime instrumentation, process/root identity injection, or relaunch
  persistence proof is claimed. The runner makes no deliberate restart between
  choices, but does not assert uninstrumented internal model lifetimes.
- OS permission language, actual plural selection, Chinese transitions, Dynamic
  Type matrices, physical devices, backend/authentication and guest execution
  remain out of this bounded feasibility probe.
