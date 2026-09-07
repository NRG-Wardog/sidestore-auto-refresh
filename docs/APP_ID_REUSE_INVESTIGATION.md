# App ID Reuse Investigation

## Verified result

On September 7, 2026, live Apple server inventory and same-session A/B/C signing
attempts established **exhausted registration quota** as the current blocker.
This was not inferred from device logs or the number of installed apps.

Apple returned 10 registered IDs, `max_quantity=10`, and
`available_quantity=0`. None matched the expected LiveContainer identifiers.

| Input | Targets | Reused | New IDs required | Available before attempt | Actual signing result |
| --- | --- | --- | --- | --- | --- |
| Official plain LiveContainer 3.8.0 | 4 | 0 | 4 | 0 | Quota failure: 4 required, 0 available |
| Official LiveContainer + SideStore 3.8.0 | 5 | 0 | 5 | 0 | Quota failure: 5 required, 0 available |
| Corrected combined AutoRefresh | 5 | 0 | 5 | 0 | Quota failure: 5 required, 0 available |

All three attempts used one authenticated developer session and the same
diagnostic executable. Inventory was captured before every attempt, including
an initial read-only capture before signing. No IDs were registered by these
failed attempts. The helper invoked upstream `Sideloader.sign_app`; it did not
install applications or revoke certificates. The exact previously working IPA
was not located; case A is the official reference, not that old file.

## Identifier matching

Installed iLoader was version 2.3.1. The diagnostic CLI used isideload
`3d42025ecac97a2548d5b88aefc8028307e369c1`, the revision in the reported error.
`T` replaces the actual selected team identifier in this public report.

| Original bundle ID | Expected rewritten ID | Type |
| --- | --- | --- |
| com.kdt.livecontainer | com.kdt.livecontainer.T | Main app |
| com.kdt.livecontainer.LiveProcess | com.kdt.livecontainer.T.LiveProcess | Extension |
| com.kdt.livecontainer.ShareExtension | com.kdt.livecontainer.T.ShareExtension | Extension |
| com.kdt.livecontainer.LaunchAppExtension | com.kdt.livecontainer.T.LaunchAppExtension | Extension |
| com.kdt.livecontainer.LiveWidget | com.kdt.livecontainer.T.LiveWidget | Extension |

Official combined and corrected combined produced identical rewritten targets.
Plain LiveContainer lacks the widget target. Frameworks are not counted.

`Application.register_app_ids` uses exact string equality with Apple's inventory.
There is no wildcard expansion, case folding, random session suffix, or legacy
identifier matching. `update_bundle_id` inserts the team suffix after the main
identifier and preserves extension suffixes. App-group setup occurs later.

The inventory contained six Spotify IDs, two standalone SideStore IDs, one
EscapeOS ID and one earlier diagnostic-app ID. Those are different base IDs,
not reusable LiveContainer registrations. Only one team was returned in this
session. No alternate team or duplicate-qualified LiveContainer IDs were found
in that inventory; other accounts were not inspected.

## Deletion follow-up

After the comparison, the user explicitly requested deletion of all ten IDs.
Apple accepted all requests. A fresh inventory confirmed zero registered IDs,
but only **two available registrations**. A later read-only query agreed.

Deleting IDs did not immediately restore the full quota in this account.
The evidence does not explain why exactly eight registrations remained
unavailable. The response exposed no quota-reset timestamp. Pre-deletion
expiration dates are not a guaranteed post-deletion capacity schedule.

Do not recommend deleting working identifiers as a quota workaround: deletion
removes reuse opportunities and may interfere with future refreshes.

## Diagnostic implementation and privacy

A separate Windows CLI was compiled locally with Rust 1.98.1 and the existing
Visual Studio C++ tools. The pinned isideload lockfile was preserved apart from
the CLI's hidden-input helper dependencies. Installed iLoader and IPA packaging
remained unchanged.

The CLI used iLoader's saved credentials through its existing keyring storage,
without printing credentials. Upstream authentication tracing was disabled.
Explicit output recorded identifiers, expiration dates, quota counts, selected
team, exact matches, and signing outcomes. Raw account logs, personal team IDs,
the local deletion helper and executable remain in ignored diagnostic storage.

[`isideload-app-id-diagnostics.patch`](isideload-app-id-diagnostics.patch) is an
optional logging-only patch for the pinned upstream revision. Apply with
`git apply --ignore-space-change <patch>` and opt in with
`ISIDELOAD_APP_ID_DIAGNOSTICS=1`. A suitably filtered tracing subscriber is also
required. It adds `APP_ID_REWRITE`, `APP_ID_INVENTORY`, `APP_ID_REGISTERED` and
`APP_ID_DECISION` without changing registration behavior. The live CLI comparison
used its own explicit output, not these subscriber-dependent logs.

Local assertions checked identical B/C targets, three real quota failures,
consistent pre-attempt inventory and an unchanged corrected IPA hash.
No IPA build or GitHub Actions run was triggered for this signing investigation.

## Remaining validation

Five registration targets fit the documented free-team maximum of ten. This is
separate from the three-installed-app limit. Later reuse requires exact matching
registered identifiers.

Re-query Apple before retrying. Once at least five registrations are available,
test signing and installation with the unchanged combined IPA. Passing the quota
check alone will not prove provisioning, installation or auto-refresh success.
Do not change identifiers or remove extensions to work around this account state.

## Sources

- [Pinned registration implementation](https://github.com/nab138/isideload/blob/3d42025ecac97a2548d5b88aefc8028307e369c1/isideload/src/sideload/application.rs)
- [Pinned signing flow](https://github.com/nab138/isideload/blob/3d42025ecac97a2548d5b88aefc8028307e369c1/isideload/src/sideload/sideloader.rs)
- [iLoader 2.3.1 entry point](https://github.com/nab138/iloader/blob/v2.3.1/src-tauri/src/sideload.rs)
- [Apple's free development limits](https://developer.apple.com/support/compare-memberships/)
- [Official combined installation](https://github.com/LiveContainer/livecontainer.github.io/blob/main/docs/installation/lc_sidestore.md)
