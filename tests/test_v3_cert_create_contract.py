"""Execute the production certificate-create adapter and host outcome mapping."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def swift_declaration(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated Swift declaration: {signature}")


class CertificateCreateContractTests(unittest.TestCase):
    def test_service_wires_upstream_storage_readback_and_no_activation(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text()
        block_start = service.index('        case "certCreate":')
        block_end = service.index('        case "devTeams":', block_start)
        block = service[block_start:block_end]

        self.assertIn("V3CertificateCreateAdapter.createAndPersist(", block)
        self.assertIn("DeveloperPortalProxy.shared.createCertificate(", block)
        self.assertIn("CertificateManager.shared.saveCertificate(certificate)", block)
        self.assertIn("Keychain.shared[certificateSerial: certificate.serialNumber]", block)
        self.assertIn("CertificateManager.parse(", block)
        self.assertIn("matchesCreatedSerial(", block)
        self.assertNotIn("setActiveCertificate", block)
        self.assertIn('return ["outcome": outcome.rawValue]', block)
        self.assertNotIn("serialNumber:", block[block.index('return ["outcome"'):])

    def test_production_adapter_and_host_mapping_execute_behaviorally(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; this executable harness runs in macOS CI")

        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text()
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text()
        declarations = "\n\n".join((
            swift_declaration(service, "enum V3CertificateCreateAdapter"),
            swift_declaration(host, "enum V3CertificateCreatePresentation"),
        ))
        fixture = (ROOT / "tests/fixtures/v3_certificate_create_contract_harness.swift").read_text()
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "certificate_create_contract.swift"
            executable = Path(directory) / "certificate_create_contract"
            program.write_text(declarations + "\n\n" + fixture)
            subprocess.run([compiler, str(program), "-o", str(executable)], check=True,
                           capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("certificate create contract passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
