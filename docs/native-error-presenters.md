# Native presenter error IDs

The pinned LiveContainer source is `12377cf3b91d51739a33f14a302e5f522b238593`.
`patch_native_error_presenters.py` adds fixed presentation-site IDs. These IDs
identify an unknown failure at that site, not a diagnosed root cause. Existing
messages, actions, lifecycle and error-state ownership remain unchanged.

The finite registry in the generator covers app list/import/launch, banner
errors, app settings, containers, storage analysis, data management, settings,
signing diagnostics, sources, tweaks, browser downloads and the legacy root
error alert. TweakLoader has its own Objective-C site ID. Storage is annotated
at its optional error presenter, not on success/loading state. The source alert
preserves nil as empty. The legacy crash-report body and copy action are not
relabeled. Legacy root/source coverage is retained even when V3 replaces or
excludes those views.

Guest launch is the fourteenth inventory row and remains owned by
`patch_guest_return.py`: `SS-GUEST-EXIT` and `SS-GUEST-UNKNOWN` retain the existing
safe prose and copy diagnostics. This patch does not restore raw guest errors.

The existing native Copy actions include the same visible site ID plus a
`builder_commit` value, accepted only as exactly 40 ASCII hex characters.
Unknown or malformed build values become `unknown`. No new Copy actions or raw
provider fields are introduced. Other existing provider prose remains unchanged
for the separate P1 wording/privacy review. IDs themselves contain no user data.

The helper lives in the existing host `Utilities/ViewExtensions.swift` source,
with no dependency on the embedded headless service or a new Xcode file. The
TweakLoader helper is local to its existing Objective-C source. CI applies the
generator twice after the host shell/service patches; all touched sources are
included in required candidate provenance. V2 provenance retains its previous
inventory.

Validation includes exact pristine Git-object fixtures, byte-identical second
application, atomic rejection of changed anchors, generated-marker drift,
optional error-state boundaries and unchanged success/crash presenters. Native
Swift/UIKit compilation still requires the macOS CI build.
