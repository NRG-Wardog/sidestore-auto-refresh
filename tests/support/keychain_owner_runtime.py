"""Exact owner keychain fragments for transaction harnesses.

Security storage and certificate proof remain injected platform IO. Record
validation, remote route fencing, hashing and credential transactions are real
owner code. Requires the macOS CryptoKit SDK in the native gate.
"""
from tests.owner_source import owner_source
from tests.support.product_sources import read_product_source, declaration_end


def keychain_runtime_source():
    source = owner_source('AltStore/Core/Components/Keychain.swift', 'SideStore')
    error_source = read_product_source('combined_failure.swift')
    error_start = error_source.index('enum LCAnisettePairError:')
    error_definition = error_source[error_start:declaration_end(error_source, error_start)]
    def region(name):
        begin = '// ' + name + '_BEGIN'
        end = '// ' + name + '_END'
        if source.count(begin) != 1 or source.count(end) != 1:
            raise AssertionError(f'Owner region markers must be unique: {name}')
        start = source.index(begin) + len(begin)
        finish = source.index(end)
        if finish <= start:
            raise AssertionError(f'Owner region markers out of order: {name}')
        return source[start:finish]
    adapter_anchor = 'extension Keychain {\n    func resolveAnisetteSnapshot'
    if source.count(adapter_anchor) != 1:
        raise AssertionError('Owner keychain adapter must be unique')
    start = source.index(adapter_anchor)
    adapter = source[start:declaration_end(source, start)]
    return '\n\n'.join((
        'import CryptoKit', error_definition,
        read_product_source('embedded_shared_keychain.swift'), adapter,
        region('V3_PENDING_CERTIFICATE_CORE'),
        region('V3_REMOTE_IDENTITY_RECORD_CORE'),
        region('V3_REMOTE_IDENTITY_KEYCHAIN_ADAPTER'),
        PLATFORM_DOUBLES,
    )).rstrip("\n")


# These are IO seams, not replacement transaction/validation decisions. A
# pending certificate proof is outside these migration/auth transaction cases:
# fail loudly if a future case invokes it instead of pretending to verify DER.
PLATFORM_DOUBLES = r'''
struct CertificateRequest {
    let machineName: String
    let csrData: Data
    let privateKey: Data
}
enum CertificateType: String { case development }
enum V3PendingCertificateKeyProof {
    static func matches(certificateDER: Data, privateKeyPEM: Data) -> Bool {
        fatalError("This harness does not provide native certificate proof IO")
    }
}
struct V3PendingCertificateKeychainClient {
    let service: String
    let accessGroup: String
    private var client: KeychainAccess.Keychain {
        // Keep the service namespace independent from normal credentials.
        KeychainAccess.Keychain(service: service, accessGroup: accessGroup + ":" + service)
    }
    func getData(_ key: String) throws -> Data? { try client.getData(key) }
    func set(_ data: Data, key: String) throws { try client.set(data, key: key) }
    func remove(_ key: String) throws { try client.remove(key) }
}
'''
