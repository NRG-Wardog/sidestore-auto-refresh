"""Exact Swift bridge compilation/execution on Darwin; never source-only native admission."""
import os,platform,shutil,subprocess,tempfile,unittest
from pathlib import Path
HOST=Path(os.environ['MAINTAINED_LIVE_CONTAINER_TEST_SOURCE']) if os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE') else None
AVAILABLE=bool(HOST and (HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').is_file())
NATIVE=platform.system()=='Darwin' and bool(shutil.which('swiftc'))
@unittest.skipUnless(AVAILABLE,'not_applicable: selected source has no P2 localization')
class HostCaptureBridgeTests(unittest.TestCase):
 @unittest.skipUnless(NATIVE,'Darwin Objective-C Swift bridge compiler unavailable; captured host context not validated natively')
 def test_present_host_selectors_keep_preference_out_of_standard_domain_and_nil_fails_closed(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   helper=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
   # The real app supplies class selectors through its Objective-C category.
   # This fixture provides the same ABI without importing app headers or changing
   # the candidate helper. A separate process exercises each static capture.
   harness=r'''
let suiteName = "org.fixture.p2-host-context." + UUID().uuidString
let capturedStore = UserDefaults(suiteName: suiteName)!
defer { capturedStore.removePersistentDomain(forName: suiteName) }
let oldStandard = UserDefaults.standard.object(forKey: V3AppLanguage.storageKey)
defer {
    if let oldStandard { UserDefaults.standard.set(oldStandard, forKey: V3AppLanguage.storageKey) }
    else { UserDefaults.standard.removeObject(forKey: V3AppLanguage.storageKey) }
}
extension UserDefaults {
    @objc(lcUserDefaults) class func fixtureHostDefaults() -> UserDefaults? {
        CommandLine.arguments.contains("nil-defaults") ? nil : capturedStore
    }
    @objc(lcMainBundle) class func fixtureHostBundle() -> Bundle? {
        CommandLine.arguments.contains("nil-bundle") ? nil : Bundle.main
    }
}
UserDefaults.standard.set("guest-sentinel", forKey: V3AppLanguage.storageKey)
capturedStore.set("he", forKey: V3AppLanguage.storageKey)
precondition(V3Localization.hostDefaults === capturedStore)
precondition(V3Localization.hostBundle === Bundle.main)
precondition(V3AppLanguage.selected == .hebrew)
for language in V3AppLanguage.allCases {
    V3AppLanguage.persist(language)
    precondition(V3AppLanguage.selected == language)
    precondition(capturedStore.string(forKey: V3AppLanguage.storageKey) == language.rawValue)
    precondition(UserDefaults.standard.string(forKey: V3AppLanguage.storageKey) == "guest-sentinel")
}
print("native-host-capture-passed")
'''
   source=root/'main.swift';source.write_text(helper+'\n'+harness)
   executable=root/'capture-test'
   subprocess.run(['swiftc',str(source),'-o',str(executable)],check=True,capture_output=True,text=True,timeout=120)
   result=subprocess.run([str(executable)],check=True,capture_output=True,text=True,timeout=30)
   self.assertIn('native-host-capture-passed',result.stdout)
   for selector in ['nil-defaults','nil-bundle']:
    result=subprocess.run([str(executable),selector],capture_output=True,text=True,timeout=30)
    self.assertNotEqual(result.returncode,0,selector)
    self.assertIn('Captured host localization context is unavailable',result.stderr,selector)
if __name__=='__main__':unittest.main()
