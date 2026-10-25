"""Named upstream integration seams for combined packaging.

Pure text transformations only. This module does not download, execute, sign or
package anything. Rules retain exact-once anchors and fail closed on drift.
"""
from dataclasses import dataclass, replace
import hashlib


@dataclass(frozen=True)
class IntegrationRule:
    name: str
    owner: str
    rationale: str
    anchor: str
    replacement: str


def apply_rules(text, rules):
    """Apply an explicitly supplied immutable rule sequence, never fuzzy matching."""
    rules = tuple(rules)
    names = [rule.name for rule in rules]
    if len(names) != len(set(names)) or any(not name for name in names):
        raise ValueError('Integration rule names must be nonempty and unique')
    for rule in rules:
        if not rule.owner or not rule.rationale or not rule.anchor:
            raise ValueError('Integration rule contract is incomplete: ' + rule.name)
        if text.count(rule.anchor) != 1:
            raise ValueError('Upstream packaging anchor changed [' + rule.name + ']: ' + rule.anchor)
        text = text.replace(rule.anchor, rule.replacement, 1)
    return text


def configure_anchors(rules, overrides):
    """Explicit compatibility configuration, without copying custom replacements."""
    rules = tuple(rules)
    names = {rule.name for rule in rules}
    if not isinstance(overrides, dict) or not set(overrides) <= names:
        raise ValueError('Unknown integration anchor override')
    if any(not isinstance(value, str) or not value for value in overrides.values()):
        raise ValueError('Anchor overrides must be nonempty exact strings')
    return tuple(replace(rule, anchor=overrides[rule.name]) if rule.name in overrides else rule
                 for rule in rules)


def patch_surface(rules):
    """Describe the upstream-facing patch surface without depending on a source SHA."""
    return [dict(name=rule.name, owner=rule.owner, rationale=rule.rationale,
                 exact_occurrences=1,
                 anchor_sha256=hashlib.sha256(rule.anchor.encode()).hexdigest(),
                 replacement_sha256=hashlib.sha256(rule.replacement.encode()).hexdigest(),
                 upstream_lines=len(rule.anchor.splitlines()),
                 replacement_lines=len(rule.replacement.splitlines())) for rule in rules]


COMBINED_PACKAGING_RULES = (
    IntegrationRule(
        'reviewed-converter',
        "NRG-Wardog/sidestore-auto-refresh",
        'Use only the locally built source-hash-verified converter.',
        (
            'wget https://github.com/LiveContainer/dylibify/releases/download/1.0/dylibify'
        ),
        (
            'cp "$VERIFIED_DYLIBIFY" dylibify'
        ),
    ),
    IntegrationRule(
        'reviewed-signer',
        "NRG-Wardog/sidestore-auto-refresh",
        'Use the independently qualified signing tool; never install from the upstream script.',
        (
            'brew install ldid'
        ),
        (
            'command -v ldid >/dev/null'
        ),
    ),
    IntegrationRule(
        'compiled-sidestore',
        "NRG-Wardog/sidestore-auto-refresh",
        'Use the exact same-run compiled service rather than downloading another build.',
        (
            'wget https://github.com/LiveContainer/SideStore/releases/download/nightly/SideStore.ipa'
        ),
        (
            'cp "$PATCHED_SIDESTORE_IPA" SideStore.ipa'
        ),
    ),
    IntegrationRule(
        'fresh-signing-cache',
        "NRG-Wardog/sidestore-auto-refresh",
        'The isolated workspace has no historical signing cache.',
        (
            'rm -r .zsign_cache'
        ),
        (
            '# No zsign cache exists in the fresh packaging workspace.'
        ),
    ),
    IntegrationRule(
        'signature-cleanup-root',
        "NRG-Wardog/sidestore-auto-refresh",
        'Limit signature cleanup to the actual staged Payload tree.',
        (
            'find payloadlc/Payload -type d -name "_CodeSignature" -exec rm -r {} +'
        ),
        (
            'find Payload -type d -name "_CodeSignature" -prune -exec rm -r {} +'
        ),
    ),
    IntegrationRule(
        'headless-intent-ownership',
        "NRG-Wardog/sidestore-auto-refresh",
        'Retain host intent metadata and remove duplicate embedded metadata ownership.',
        (
            '# copy intents\n'
            'cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition ./Payload/LiveContainer.app/\n'
            'cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/ViewApp.intentdefinition ./Payload/LiveContainer.app/\n'
            'cp -r ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents ./Payload/LiveContainer.app/Metadata.appintents\n'
            "sed -i '' 's/9SideStore20RefreshAllAppsIntentV/16SideStoreSupport20RefreshAllAppsIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata\n"
            "sed -i '' 's/9SideStore26RefreshAllAppsWidgetIntentV/16SideStoreSupport26RefreshAllAppsWidgetIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata\n"
        ),
        (
            '# Stage the host App Intents schemas/metadata from the headless service build,\n'
            '# then remove these packaging inputs from the embedded backend framework.\n'
            'cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition ./Payload/LiveContainer.app/\n'
            'cp ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/ViewApp.intentdefinition ./Payload/LiveContainer.app/\n'
            'cp -r ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents ./Payload/LiveContainer.app/Metadata.appintents\n'
            "sed -i '' 's/9SideStore20RefreshAllAppsIntentV/16SideStoreSupport20RefreshAllAppsIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata\n"
            "sed -i '' 's/9SideStore26RefreshAllAppsWidgetIntentV/16SideStoreSupport26RefreshAllAppsWidgetIntentV/g' ./Payload/LiveContainer.app/Metadata.appintents/extract.actionsdata\n"
            'rm -f ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Intents.intentdefinition\n'
            'rm -f ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/ViewApp.intentdefinition\n'
            'rm -rf ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Metadata.appintents\n'
        ),
    ),
    IntegrationRule(
        'shared-entitlement-preparation',
        "NRG-Wardog/sidestore-auto-refresh",
        'Prepare the shared secret handoff and extension entitlements before packaging.',
        (
            '# package\n'
        ),
        (
            'python3 "$COMBINED_PACKAGER" --prepare-entitlements . Payload/LiveContainer.app\n'
            '\n'
            '# package\n'
        ),
    ),
    IntegrationRule(
        'converter-preconditions-postconditions',
        "NRG-Wardog/sidestore-auto-refresh",
        'Check the converted Mach-O shape and chained-fixup invariants before and after conversion.',
        (
            './dylibify ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore.dylib'
        ),
        (
            'python3 "$COMBINED_PACKAGER" --validate-dylibify-input ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore\n'
            './dylibify ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore.dylib\n'
            'python3 "$COMBINED_PACKAGER" --verify-dylibify ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore ./Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore.dylib'
        ),
    ),
)


def adapt_combined_packaging(text):
    return "set -eu\n" + apply_rules(text, COMBINED_PACKAGING_RULES)
