"""Verified classifier dependency closure; no authentication IO is simulated."""
from tests.owner_source import owner_source
from tests.support.product_sources import declaration_end


def _decl(path, owner, anchor):
    source = owner_source(path, owner)
    if source.count(anchor) != 1:
        raise AssertionError(f'non-unique owner declaration: {anchor}')
    start = source.index(anchor)
    return source[start:declaration_end(source, start)]


def operation_error_cases():
    source = owner_source('SideStore/Core/Operations/Errors/OperationError.swift', 'SideStore')
    start = source.index('public enum OperationError:')
    return 'enum OperationError: Error, CustomNSError {\n' + source[source.index('{', start)+1:source.index('    public static var cancelled:', start)] + '\n}'


def provisioning_diagnostic_helpers():
    path = 'SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift'
    return '\n'.join(_decl(path, 'SideStore', anchor) for anchor in (
        'private struct V3ProvisioningPortalMetadata:',
        'private struct V3ProvisioningCauseRecorder {',
        'private struct V3ProvisioningUnknownCause:',
        'func v3ProvisioningCauseContext(', 'func v3UnobservedProvisioningCause(',
    ))


def diagnostic_error_types():
    errors = 'Sources/Models/Errors.swift'
    constants = owner_source('Sources/Constants.swift', 'SideStore/Dependencies/SideSign')
    start = constants.index('    public static let defaultAccountRepairMessage')
    end = constants.index('\n\n', start)
    constant = constants[start:end]
    grand = next(line for line in constants.splitlines() if 'static let grandSlamAuth ' in line)
    chunks = ['import Foundation\nimport CoreFoundation', 'enum Constants {\n' + constant + '\nenum URLs {\n' + grand + '\n}\n}']
    for anchor in ('public enum DeveloperPortalError:', 'public struct AuthenticationResponseContext:', 'public enum ServerError:'):
        chunks.append(_decl(errors, 'SideStore/Dependencies/SideSign', anchor))
    chunks.append('enum SideSign {\nenum Archive {\n' + _decl('Sources/Archiver/Archive.swift', 'SideStore/Dependencies/SideSign', '    public enum Error:') + '\n}\n' + _decl('Sources/Anisette/AnisetteDataManager.swift', 'SideStore/Dependencies/SideSign', 'public enum AnisetteError:') + '\n}')
    chunks.append('enum AnisetteKit {\n' + _decl('Sources/AnisetteError.swift', 'AnisetteKit', 'public enum AnisetteError:') + '\n}')
    chunks.append(_decl('Sources/Anisette/ILoaderRemoteAnisette.swift', 'SideStore/Dependencies/SideSign', 'public enum ILoaderRemoteError:'))
    # This context is external IO, never instantiated by classifier scenarios.
    chunks.append('public final class OwnedODAAuthenticationContext: @unchecked Sendable { func checkActive() throws { fatalError("unexpected owned authentication IO") } }')
    chunks.append('enum ILoaderAuthenticationPolicy {\n' + _decl('Sources/DeveloperPortal/ILoaderAuthenticationPolicy.swift', 'SideStore/Dependencies/SideSign', '    static func signedInteger(') + '\n}')
    chunks.append(_decl('Sources/DeveloperPortal/Authentication.swift', 'SideStore/Dependencies/SideSign', 'public struct OwnedODAInitialProviderRejection:'))
    chunks.append(_decl('Common/MinimuxerConstants.swift', 'SideStore/Dependencies/minimuxer', 'public enum MinimuxerConstants {'))
    chunks.append(_decl('Common/PairingProtocol.swift', 'SideStore/Dependencies/minimuxer', 'public enum PairingProtocol:'))
    chunks.append(_decl('Sources/MinimuxerError.swift', 'SideStore/Dependencies/minimuxer', 'public enum MinimuxerError:'))
    chunks.append(_decl('Sources/MinimuxerApi.swift', 'SideStore/Dependencies/minimuxer', 'public enum MinimuxerComponent:'))
    chunks.append(_decl('Sources/MinimuxerError.swift', 'SideStore/Dependencies/minimuxer', 'public struct MinimuxerServiceError:'))
    chunks.append(owner_source('Shared/Errors/ALTWrappedError.swift', 'SideStore'))
    return '\n'.join(chunks)
