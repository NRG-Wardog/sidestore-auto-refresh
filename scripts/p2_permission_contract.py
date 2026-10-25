"""OS-managed permission resources. Independent of the in-app language selection."""
import plistlib, zipfile
LANGUAGES = ("en", "zh-Hans", "zh-Hant", "he", "zh_CN", "zh_TW", "zh_HK")
PURPOSE_KEYS = ('NFCReaderUsageDescription', 'NSAlarmKitUsageDescription', 'NSAppleMusicUsageDescription', 'NSBluetoothAlwaysUsageDescription', 'NSBluetoothPeripheralUsageDescription', 'NSCalendarsFullAccessUsageDescription', 'NSCalendarsWriteOnlyAccessUsageDescription', 'NSCameraUsageDescription', 'NSContactsUsageDescription', 'NSFaceIDUsageDescription', 'NSFallDetectionUsageDescription', 'NSGKFriendListUsageDescription', 'NSHealthClinicalHealthRecordsShareUsageDescription', 'NSHealthShareUsageDescription', 'NSHealthUpdateUsageDescription', 'NSHomeKitUsageDescription', 'NSIdentityUsageDescription', 'NSLocalNetworkUsageDescription', 'NSLocationAlwaysAndWhenInUseUsageDescription', 'NSLocationUsageDescription', 'NSLocationWhenInUseUsageDescription', 'NSMicrophoneUsageDescription', 'NSMotionUsageDescription', 'NSNearbyInteractionAllowOnceUsageDescription', 'NSNearbyInteractionUsageDescription', 'NSPhotoLibraryAddUsageDescription', 'NSPhotoLibraryUsageDescription', 'NSRemindersFullAccessUsageDescription', 'NSSensorKitUsageDescription', 'NSSiriUsageDescription', 'NSSpeechRecognitionUsageDescription', 'NSUserTrackingUsageDescription')
EXPECTED = {"en": "The guest app is requesting this permission.", "he": "היישום המתארח מבקש הרשאה זו.", "zh-Hans": "客体应用正在请求此权限。", "zh-Hant": "客體應用程式正在請求此權限。"}

# Preserve the inherited Simplified-Chinese bundle alias with identical purposes.
EXPECTED["zh_CN"] = EXPECTED["zh-Hans"]
EXPECTED["zh_TW"] = EXPECTED["zh-Hant"]
EXPECTED["zh_HK"] = EXPECTED["zh-Hant"]

def verify_compiled_permissions(read, names):
    failures=[]
    for language in LANGUAGES:
        name=language+".lproj/InfoPlist.strings"
        if name not in names:
            failures.append("Missing OS permission resource: "+name);continue
        try:
            values=plistlib.loads(read(name))
            if set(values) != set(PURPOSE_KEYS) | {"CFBundleName"}:raise ValueError("Unexpected or missing permission resource keys")
            if values.get("CFBundleName")!="LiveContainer":raise ValueError("Product name changed")
            if any(values.get(key)!=EXPECTED[language] for key in PURPOSE_KEYS):raise ValueError("Missing or incorrect scalar purpose")
            if "NSLocationTemporaryUsageDescriptionDictionary" in values:raise ValueError("Temporary precise-location purposes cannot be invented as a scalar string")
        except Exception as error:failures.append(name+": "+str(error))
    return {"status":"passed" if not failures else "failed", "failures":failures,
            "selection":"OS-managed; independent of in-app language preference",
            "inheritedTemporaryLocationDictionary":"malformed/unconfigured entry unchanged; requires separate reviewed change"}

def verify_ipa(path, *, capability_required):
    if type(capability_required) is not bool:raise ValueError("An authenticated explicit capability boolean is required")
    if not capability_required:return {"status":"not_applicable", "reason":"pre-P2 candidate"}
    with zipfile.ZipFile(path) as archive:
        roots={n.split('/')[1] for n in archive.namelist() if n.startswith('Payload/') and len(n.split('/'))>2 and n.split('/')[1].endswith('.app')}
        if len(roots)!=1:return {"status":"failed", "failures":["Ambiguous IPA host app"]}
        prefix='Payload/'+next(iter(roots))+'/'
        names={n[len(prefix):] for n in archive.namelist() if n.startswith(prefix)}
        return verify_compiled_permissions(lambda name:archive.read(prefix+name),names)
