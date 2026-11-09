"""Read current product declarations from verified owner commits, never replay patches.

Names identify former input roles, not alternate source implementations. A range
contains the exact owner bytes between explicit unique declarations. Imports are
harness dependencies; declarations are not rewritten to fit old expectations.
"""
from tests.owner_source import owner_source

SOURCES = {'anisette_legacy_recovery.swift': ('SideStore',
                                    'SideStore/Core/Anisette/OnDeviceAnisetteManager.swift',
                                    'private enum LCAnisetteIsolatedProbe {',
                                    'extension OnDeviceAnisetteManager {',
                                    ''),
 'combined_failure.swift': ('LiveContainer',
                            'SideStoreSupport/SideStore.swift',
                            'public struct CombinedRefreshTargetPlan: Equatable {',
                            'public enum V3DiagnosticPresentation {',
                            'import Foundation\nimport CoreFoundation'),
 'combined_refresh_handler.swift': ('LiveContainer',
                                    'SideStoreSupport/SideStore.swift',
                                    'enum V3ServiceReadinessProbeState: Equatable {',
                                    'extension RefreshHandler { fileprivate var launchIDForCallbacks: UUID? '
                                    '{ launchID } }',
                                    ''),
 'combined_service_connection.swift': ('LiveContainer',
                                       'SideStoreSupport/SideStore.swift',
                                       '@MainActor\nfinal class CombinedServiceConnection {',
                                       '@MainActor\nfinal class CombinedServiceConnection {',
                                       'import Foundation'),
 'embedded_shared_keychain.swift': ('SideStore',
                                    'AltStore/Core/Components/Keychain.swift',
                                    'struct LCAnisetteStoredPair: Equatable, Sendable {',
                                    'fileprivate enum LCEmbeddedSharedKeychain {',
                                    ''),
 'livecontainer_app_layout_style.swift': ('LiveContainer',
                                          'LiveContainerSwiftUI/Models/AppLayoutStyle.swift',
                                          None,
                                          None,
                                          'import Foundation'),
 'livecontainer_grid_app_cell.swift': ('LiveContainer',
                                       'LiveContainerSwiftUI/Views/AppList/LCGridAppCell.swift',
                                       None,
                                       None,
                                       'import SwiftUI\nimport UIKit'),
 'livecontainer_network_preflight.swift': ('LiveContainer',
                                           'LiveContainerSwiftUI/App/AppDelegate.swift',
                                           '@MainActor\nfinal class LiveContainerVPNActivationWaiter {',
                                           '@MainActor\nenum LiveContainerNetworkPreflight {',
                                           'import Network\nimport Darwin'),
 'livecontainer_refresh_alarm.swift': ('LiveContainer',
                                       'LiveContainerSwiftUI/App/LiveContainerAutoRefreshAlarm.swift',
                                       None,
                                       None,
                                       'import Foundation\n'
                                       'import UserNotifications\n'
                                       'import AlarmKit\n'
                                       'import AppIntents\n'
                                       'import SwiftUI'),
 'livecontainer_refresh_policy.swift': ('LiveContainer',
                                        'LiveContainerSwiftUI/App/AppDelegate.swift',
                                        'struct LiveContainerRefreshTaskIdentifiers: Equatable {',
                                        'struct LiveContainerHostProfile {',
                                        'import Foundation'),
 'livecontainer_refresh_scheduler.swift': ('LiveContainer',
                                           'LiveContainerSwiftUI/App/AppDelegate.swift',
                                           '@MainActor\nenum LiveContainerAutoRefreshScheduler {',
                                           '@MainActor\nenum LiveContainerAutoRefreshScheduler {',
                                           ''),
 'livecontainer_refresh_settings.swift': ('LiveContainer',
                                          'LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift',
                                          'struct LiveContainerRefreshHistoryEntry: Identifiable, Equatable '
                                          '{',
                                          '@MainActor\nstruct LCEmbeddedSideStoreRefreshView: View {',
                                          'import SwiftUI\nimport Foundation\nimport SideStoreSupport'),
 'v3_behavioral_primitives.swift': ('SideStore',
                                    'AltStore/AppDelegate.swift',
                                    'enum V3SetupSnapshotOutcome: String, Equatable {',
                                    'struct V3RefreshExecutionGate {',
                                    'import Foundation\nimport CoreFoundation'),
 'v3_headless_runtime.swift': ('SideStore',
                               'AltStore/AppDelegate.swift',
                               'enum V3HeadlessPairingFailure {',
                               '@MainActor\nenum V3BackendCommands {',
                               'import Foundation\n'
                               'import CoreData\n'
                               'import CryptoKit\n'
                               'import UIKit\n'
                               'import SideSign\n'
                               'import AnisetteKit\n'
                               'import Minimuxer\n'
                               'import MinimuxerCommon'),
 'v3_headless_url_handler.swift': ('SideStore',
                                   'SideStore/DeepLinks/URLHandler.swift',
                                   None,
                                   None,
                                   'import Foundation'),
 'v3_ipa_staging.swift': ('LiveContainer',
                          'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
                          'enum V3IPAStaging {',
                          'enum V3IPAStaging {',
                          'import Foundation\nimport Darwin\nimport Glibc'),
 'v3_secret_handoff.swift': ('LiveContainer',
                             'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
                             'enum V3AppGroupProcessLock {',
                             'enum V3SecretHandoff {',
                             'import Foundation\nimport Security\nimport Darwin\nimport Glibc'),
 'v3_service_bridge.swift': ('LiveContainer',
                             'SideStoreSupport/SideStore.swift',
                             'private enum V3AuthSessionUnavailableReplyPolicy {',
                             '@MainActor\npublic final class V3ServiceBridge {',
                             ''),
 'v3_setup_intent.swift': ('LiveContainer',
                           'LiveContainerSwiftUI/App/V3SetupAssistantIntent.swift',
                           None,
                           None,
                           'import AppIntents\nimport Foundation'),
 'v3_shared_app_group.swift': ('LiveContainer',
                               'LiveContainerSwiftUI/Utilities/V3SharedAppGroup.swift',
                               None,
                               None,
                               'import Foundation\nimport Darwin'),
 'v3_sidestore_service.swift': ('SideStore',
                                'AltStore/AppDelegate.swift',
                                'enum V3CertificateCreateAdapter {',
                                'final class V3SideStoreService: NSObject {',
                                ''),
 'v3_unified_shell.swift': ('LiveContainer',
                            'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
                            'enum V3AppIdentity: Hashable {',
                            'private struct V3HomeView: View {',
                            'import SwiftUI\n'
                            'import Combine\n'
                            'import SideStoreSupport\n'
                            'import UniformTypeIdentifiers\n'
                            'import UIKit\n'
                            'import CoreFoundation\n'
                            'import CryptoKit\n'
                            'import Security'),
 'v3_wire_contract.swift': ('LiveContainer',
                            'SideStoreSupport/SideStore.swift',
                            'public struct V3AuthServiceSnapshot: Equatable {',
                            'struct V3ServiceReadinessFailure: Equatable {',
                            'import Foundation\nimport CoreFoundation\nimport CryptoKit')}

SOURCES.update({
    'host_behavioral_primitives.swift': ('LiveContainer', 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
        'enum V3SetupSnapshotOutcome: String, Equatable {', 'enum V3SharedKeychainAccessGroupPolicy {', 'import Foundation'),
    'livecontainer_vpn_recovery.swift': ('LiveContainer', 'LiveContainerSwiftUI/App/AppDelegate.swift',
        'enum LiveContainerVPNRefreshRecoveryPolicy {', 'enum LiveContainerVPNPostActivationOwnership {', ''),
    'LCAppGroupIdentityRules.h': ('LiveContainer', 'LiveContainer/LCAppGroupIdentityRules.h', None, None, ''),
    'LCAppGroupSelectionPolicy.h': ('LiveContainer', 'LiveContainer/LCAppGroupSelectionPolicy.h', None, None, ''),
    'combined_container_storage.h': ('LiveContainer', 'LiveContainer/LCContainerStorage.h', None, None, ''),
})


def _comment_end(source, start):
    if source.startswith('//', start):
        end = source.find('\n', start)
        return len(source) if end < 0 else end + 1
    depth, i = 1, start + 2
    while i < len(source):
        if source.startswith('/*', i): depth += 1; i += 2
        elif source.startswith('*/', i):
            depth -= 1; i += 2
            if not depth: return i
        else: i += 1
    raise ValueError('Unterminated owner comment')


def _string_end(source, start):
    """Skip a Swift normal/raw/multiline string, including nested interpolation."""
    i = start
    while i < len(source) and source[i] == '#': i += 1
    hashes = source[start:i]
    if i >= len(source) or source[i] != '"': return None
    quote = '"""' if source.startswith('"""', i) else '"'
    i += len(quote)
    closing, escape = quote + hashes, '\\' + hashes
    while i < len(source):
        if source.startswith(closing, i): return i + len(closing)
        if source.startswith(escape, i):
            i += len(escape)
            if i < len(source) and source[i] == '(':
                i = _interpolation_end(source, i)
            else: i += 1
        else: i += 1
    raise ValueError('Unterminated owner string')


def _reject_raw_regex(source, start):
    # Regex literals are not a supported slicing input. Their grammar differs
    # from strings (notably unescaped braces); fail rather than guess a boundary.
    if source[start] != '#': return
    end = start
    while end < len(source) and source[end] == '#': end += 1
    if end < len(source) and source[end] == '/':
        raise ValueError('Swift raw regex literals require a reviewed source slicer')


def _declaration_positions(source, markers):
    positions = {marker: [] for marker in markers}
    depth, i = 0, 0
    while i < len(source):
        if source.startswith(('//', '/*'), i):
            i = _comment_end(source, i); continue
        if source[i] in ('"', '#'):
            end = _string_end(source, i)
            if end is not None: i = end; continue
        _reject_raw_regex(source, i)
        if depth == 0:
            for marker in positions:
                if source.startswith(marker, i): positions[marker].append(i)
        if source[i] == '{': depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth < 0: raise ValueError('Unbalanced owner source')
        i += 1
    if depth: raise ValueError('Unbalanced owner source')
    return positions


def _interpolation_end(source, start):
    depth, i = 1, start + 1
    while i < len(source):
        if source.startswith(('//', '/*'), i):
            i = _comment_end(source, i); continue
        if source[i] in ('"', '#'):
            end = _string_end(source, i)
            if end is not None: i = end; continue
        _reject_raw_regex(source, i)
        if source[i] == '(': depth += 1
        elif source[i] == ')':
            depth -= 1
            if not depth: return i + 1
        i += 1
    raise ValueError('Unterminated owner interpolation')


def declaration_end(source, start):
    """Find the closing brace without treating literal/comment contents as code."""
    i = source.index('{', start)
    depth = 0
    while i < len(source):
        if source.startswith(('//', '/*'), i):
            i = _comment_end(source, i); continue
        if source[i] in ('"', '#'):
            end = _string_end(source, i)
            if end is not None: i = end; continue
        _reject_raw_regex(source, i)
        if source[i] == '{': depth += 1
        elif source[i] == '}':
            depth -= 1
            if not depth: return i + 1
        i += 1
    raise ValueError('Unterminated owner declaration')


def read_product_source(name):
    owner, path, first, last, imports = SOURCES[name]
    source = owner_source(path, owner)
    if first is None:
        return source
    positions = _declaration_positions(source, (first, last))
    if len(positions[first]) != 1 or len(positions[last]) != 1:
        raise ValueError('Owner declaration boundary missing or ambiguous: ' + name)
    start, final = positions[first][0], positions[last][0]
    if final < start:
        raise ValueError('Owner declaration boundaries are reversed: ' + name)
    return (imports + '\n\n' if imports else '') + source[start:declaration_end(source, final)] + '\n'


class ProductSource:
    """A read-only test input role, deliberately not a filesystem path."""
    def __init__(self, name):
        if name not in SOURCES:
            raise ValueError('Unknown product input role: ' + name)
        self.name = name

    def read_text(self, encoding='utf-8'):
        if encoding not in ('utf-8', 'utf8'):
            raise ValueError('Owner source is UTF-8')
        return read_product_source(self.name)

    def read_bytes(self):
        return self.read_text().encode('utf-8')
