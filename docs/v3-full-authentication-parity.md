# Full authentication continuation parity audit

Audited against original LC-distributed SideStore `12a496ca1c766a102193634879823d16610bf1cd`, original SideSign `df2b8e4257454f0c7629276d409d6e9d7953fdf6`, pinned SideStore `ff25922e5c13ccfafd83bda5092910d848ebd409`, and pinned SideSign `a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d`.

## Findings and narrow corrections

Three source-proven UI parity differences were corrected without changing Apple network logic:

- Original `SignInFlowHandler.swift:342-396` always offers SMS and voice, including an empty phone list. SideSign `Authentication.swift:687-692` resolves the empty target to its existing default `1`. The v3 adapter had hidden these methods. They remain available now, including trusted-device → change-method, with an empty target passed unchanged. No phone number is invented.
- Original handler `:287-301` offers manual Resend SMS / Call Again using the active phone ID. The v3 code prompt now exposes the corresponding explicit action and passes the actual active target. It cannot resend automatically. Trusted-device prompts have no resend action and reject forged resend answers.
- Original handler `:257-271` enables submission only for six characters. Both v3 UI and handler now enforce that count, preserving the original lack of trimming or digit-only transformation. Invalid-length input publishes a new prompt without submitting a code to SideSign.

Original preferred-delivery highlighting (`:353-408`) remains a cosmetic difference: v3 presents all methods without a recommended badge. Neither handler automatically dispatches the preferred method.

## Continuation matrix

| Stage | Evidence | Result |
| --- | --- | --- |
| Manual credentials | Original `SignInOperation.authenticationLoop/signIn`, `:122-164`; generated operation `startAuthentication` and `v3RequireInteractiveCredentials` | Interactive sessions ask for credentials first. Default/background token/password recovery remains separate. |
| Anisette | Original operation `getAnisetteData`, `:65-68`, and `signIn:148-151`; service patch `getAnisetteData` phase wrapper | Existing provider call and Xcode-version resolution retained. Local orphaned Anisette state fails closed in the separately reviewed precondition fix. |
| SRP/GSA, app-token crypto | SideSign `Authentication.swift:14-291,294-452` | Entire original/pinned authentication file has identical Git blob `f60d4c67dcca0f093dc3ac949fdd0492ef7431a2` before patches. All DeveloperPortal files are unchanged between those upstream revisions. Privacy patch and typed retry patch do not replace handshake or request mechanics. |
| Initial 2FA challenge | SideSign `:189-221,571-597` | Same six supported auth-type values; callback receives method selection before any delivery request; successful verification re-enters original authentication. |
| Delivery and phone ID | SideSign `:599-615,656-778`; v3 `verificationCode/chooseDeliveryMethod` | Trusted-device, SMS, voice, existing phone selection, and original empty-list fallback are preserved after this correction. |
| Code validation/retry | SideSign `:617-647,828-880`; `patch_sidesign_2fa_state.py` | Active channel and phone ID remain in SideSign. Incorrect-code responses retry the same channel; rate limits and fatal errors retain upstream throws. Expiry has no separate upstream UI state: handling depends on Apple's actual error code/status. Typed safe messages replace provider text. |
| Resend/cancel | Original handler `:287-335`; v3 `enterVerificationCode`; SideSign `:599-651` | Explicit SMS/voice resend uses active ID; cancellation does not dispatch delivery. No new timer or automatic resend. |
| Prompt ownership | v3 `ask`, `V3PromptCenter`, `V3AuthCenter.respond`, host revision policy | Fresh UUID per prompt, deferred retirement before next callback, one settled continuation, session/current-prompt admission, accepted duplicate answers return current state. |
| Account lookup and session | SideSign `:279-291`; generated `signIn` | Same fetched auth token, account lookup, session output. Cancellation/identity checks occur before local credential commit. |
| Credential storage | Generated `signIn`, service patch credential transaction | Intentional stronger behavior: credential route written transactionally with read-back and identity ownership. Local commit failure does not replay Apple login. Failed login does not silently erase the saved account. |
| Teams | Original `fetchTeam:329-346`; generated `fetchTeam` | Zero teams fails, one is selected, multiple use handler selection. Same portal request. |
| Certificate/device | Original `provision:175-210`; generated `provisioningLoop` | Same outcome stages, adapted pinned implementation and resumable state. Successful remote stages retained across local retry. Not byte-for-byte implementation parity with original's extracted flow objects. |
| Completion | Generated `finalizeAuthentication`; v3 auth-center `run` | Local account activation and final readiness are checked before terminal completion. Account-authentication success alone is not full provisioning success. |
| App IDs | Portal proxy and install/provisioning operations | App-ID work belongs to downstream signing/install, not the original password/2FA exchange. No claim that this focused patch verifies every downstream signing path on device. |
| Same-account recovery | Generated `v3ValidateReauthenticationIdentity`, resume flags | Existing owner, identity stamp and returned DSID guards retained. Saved-session provisioning retry does not ask for credentials; explicit reauthentication remains same-owner. |

## Regression evidence and limits

The existing fixture extracts actual production prompt center, handler methods, answer/poll methods and host revision policy. Added cases cover empty-list SMS/voice, trusted-device fallback, second-phone selection, rejected-code continuation, active-target resend, cancel, invalid code lengths, unowned prompt IDs, duplicate replies, and forged trusted-device resend. Provider enums and results are IO doubles; wrong/expired fixture labels are not an Apple response-parser test. The existing credential-transition cases retain cancellation before/after the first 2FA prompt.

No live Apple requests, credentials, certificate reset or revocation were used. Linux can assemble these extracted fixtures and run static Python regressions, but has no Swift compiler or Apple SDK. Native fixture execution, the iOS build, and actual device login remain separate required evidence. These 2FA UI defects do not establish the cause of the separately reported failure before the first 2FA callback.

Local validation on 2026-10-06 with all three pinned source paths: 1,082 tests ran, 913 passed, 169 skipped, no failures. The extended native fixture is included in the skipped Swift tests; its production-source assembly succeeded. `git diff --check` passed. After adding the six-character no-normalization fixture cases, the targeted source-assembly test was rerun successfully with its two native executions still skipped.
