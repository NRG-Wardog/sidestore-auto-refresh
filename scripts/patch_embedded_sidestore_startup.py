#!/usr/bin/env python3
"""Patch embedded SideStore startup identity and safe database retries."""
from __future__ import annotations

from pathlib import Path
import re
import sys


MARKER = "EMBEDDED_SIDESTORE_STARTUP_FIX_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    # Upstream contains whitespace-only separator lines. Preserve the source except
    # for the replacement while accepting formatting-only whitespace differences.
    pattern = r"[ \t]*\n".join(re.escape(line.rstrip()) for line in old.splitlines())
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise ValueError(f"Expected one upstream anchor for {label}; found {len(matches)}")
    match = matches[0]
    return text[:match.start()] + new + text[match.end():]


def patch_hooks(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    text = replace_once(
        text,
        "static NSMutableDictionary<NSString *, UIWindow *> *SSVersionWindows;\nstatic id SSSceneObserver;\n",
        "static NSMutableDictionary<NSString *, UIWindow *> *SSVersionWindows;\n"
        "static id SSSceneObserver;\n\n"
        "// EMBEDDED_SIDESTORE_STARTUP_FIX_V1: do not install before SideStore's runtime exists.\n",
        "hook state",
    )
    text = replace_once(
        text,
        "void installSideStoreHooks(void) {\n\n    swizzleClassMethod(NSBundle.class, @selector(appbundleIdentifier), @selector(hook_appbundleIdentifier));\n",
        "void installSideStoreHooks(void) {\n"
        "    if (PrivClass(Source) == nil) {\n"
        "        NSLog(@\"[SIDESTORE_STARTUP] EMBEDDED_SIDESTORE_STARTUP_FIX_V1 hooks_deferred reason=Source_class_unavailable\");\n"
        "        return;\n"
        "    }\n"
        "    static dispatch_once_t onceToken;\n"
        "    dispatch_once(&onceToken, ^{\n"
        "    swizzleClassMethod(NSBundle.class, @selector(appbundleIdentifier), @selector(hook_appbundleIdentifier));\n",
        "idempotent hook entry",
    )
    text = replace_once(
        text,
        "\n    \n\n}\n#pragma clang diagnostic pop\n",
        "\n"
        "        NSBundle *hostBundle = NSUserDefaults.lcMainBundle;\n"
        "        NSLog(@\"[SIDESTORE_STARTUP] EMBEDDED_SIDESTORE_STARTUP_FIX_V1 hooks_installed host_bundle=%@ host_path=%@ app_group=%@\",\n"
        "              hostBundle.bundleIdentifier, hostBundle.bundlePath, LCSharedUtils.appGroupID);\n"
        "    });\n"
        "}\n"
        "#pragma clang diagnostic pop\n",
        "idempotent hook completion",
    )
    path.write_text(text, encoding="utf-8")


def patch_bootstrap(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    old = '''    if(sideStoreExist) {
        if (!isLiveProcess && (isSideStore || ![guestAppInfo[@"dontInjectTweakLoader"] boolValue])) {
            dlopen([lcMainBundle.bundlePath stringByAppendingPathComponent:@"Frameworks/SideStoreSupport.framework/SideStoreSupport"].UTF8String, RTLD_LAZY);
        } else if (isLiveProcess && isSideStore) {
            dlopen([lcMainBundle.bundlePath stringByAppendingPathComponent:@"../../Frameworks/SideStoreSupport.framework/SideStoreSupport"].UTF8String, RTLD_LAZY);
        }
    }'''
    new = '''    void *sideStoreSupportHandle = NULL;
    if(sideStoreExist) {
        if (!isLiveProcess && (isSideStore || ![guestAppInfo[@"dontInjectTweakLoader"] boolValue])) {
            sideStoreSupportHandle = dlopen([lcMainBundle.bundlePath stringByAppendingPathComponent:@"Frameworks/SideStoreSupport.framework/SideStoreSupport"].UTF8String, RTLD_LAZY);
        } else if (isLiveProcess && isSideStore) {
            sideStoreSupportHandle = dlopen([lcMainBundle.bundlePath stringByAppendingPathComponent:@"../../Frameworks/SideStoreSupport.framework/SideStoreSupport"].UTF8String, RTLD_LAZY);
        }
    }

    // EMBEDDED_SIDESTORE_STARTUP_FIX_V1: SideStoreSupport can load before
    // isSideStore is known. Install after SideStore's classes are present.
    if (isSideStore) {
        if (!sideStoreSupportHandle) {
            return @"Unable to load SideStoreSupport before embedded SideStore startup.";
        }
        void (*installHooks)(void) = dlsym(sideStoreSupportHandle, "installSideStoreHooks");
        if (!installHooks) {
            return @"Unable to locate SideStore identity hooks before embedded SideStore startup.";
        }
        installHooks();
        NSLog(@"[SIDESTORE_STARTUP] EMBEDDED_SIDESTORE_STARTUP_FIX_V1 hooks_requested main_bundle=%@ host_bundle=%@", NSBundle.mainBundle.bundleIdentifier, lcMainBundle.bundleIdentifier);
    }'''
    text = replace_once(text, old, new, "explicit SideStore hook invocation")
    path.write_text(text, encoding="utf-8")


def patch_database(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    old = '''                case .success:
                    self.persistentContainer.loadPersistentStores { (description, error) in
                        guard error == nil else { return finish(error!) }

                        self.prepareDatabase() { (result) in
                            switch result
                            {
                            case .failure(let error): finish(error)
                            case .success: finish(nil)
                            }
                        }
                    }
'''
    new = '''                case .success:
                    // EMBEDDED_SIDESTORE_STARTUP_FIX_V1: a failed preparation
                    // leaves SQLite attached. Retrying must not attach it twice.
                    let prepareLoadedStore = {
                        self.prepareDatabase() { result in
                            switch result
                            {
                            case .failure(let error): finish(error)
                            case .success: finish(nil)
                            }
                        }
                    }
                    if self.persistentContainer.persistentStoreCoordinator.persistentStores.isEmpty {
                        debugLog("[SIDESTORE_STARTUP] loading_persistent_store")
                        self.persistentContainer.loadPersistentStores { (_, error) in
                            guard error == nil else { return finish(error!) }
                            prepareLoadedStore()
                        }
                    }
                    else {
                        debugLog("[SIDESTORE_STARTUP] reusing_attached_persistent_store_after_startup_failure")
                        prepareLoadedStore()
                    }
'''
    text = replace_once(text, old, new, "database retry")
    old = '''                guard let localAppBundle = ALTApplication(fileURL: Bundle.Info.activeBundleURL) else { return }

                #if !targetEnvironment(simulator)
                guard localAppBundle.provisioningProfile != nil else {
                    completionHandler(.failure(ALTError(.invalidApp)))
                    return
                }
                #endif
'''
    new = '''                let activeBundle = Bundle.Info.activeBundle
                let activeBundleURL = Bundle.Info.activeBundleURL
                let profileURL = activeBundle.provisioningProfileURL
                let requestedAppGroup = Bundle.main.altstoreAppGroup ?? "nil"
                let appGroupURL = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: requestedAppGroup)
                debugLog("[SIDESTORE_STARTUP] EMBEDDED_SIDESTORE_STARTUP_FIX_V1 main_bundle=\\(Bundle.main.bundleIdentifier ?? "nil") active_bundle=\\(activeBundle.bundleIdentifier ?? "nil") active_path=\\(activeBundleURL.path) store_bundle=\\(Bundle.Info.storeAppBundleIdentifier) app_bundle=\\(Bundle.Info.appbundleIdentifier) profile_path=\\(profileURL.path) profile_exists=\\(FileManager.default.fileExists(atPath: profileURL.path)) app_group=\\(requestedAppGroup) app_group_path=\\(appGroupURL?.path ?? "nil")")

                guard let localAppBundle = ALTApplication(fileURL: activeBundleURL) else {
                    completionHandler(.failure(ALTError.invalidApp(reason: "Unable to read the active LiveContainer application bundle at \\(activeBundleURL.path).")))
                    return
                }

                #if !targetEnvironment(simulator)
                guard localAppBundle.provisioningProfile != nil else {
                    completionHandler(.failure(ALTError.invalidApp(reason: "The active LiveContainer bundle has no readable provisioning profile at \\(profileURL.path).")))
                    return
                }
                #endif
'''
    text = replace_once(text, old, new, "startup identity diagnostics")
    path.write_text(text, encoding="utf-8")


def patch(live_root: Path, sidestore_root: Path) -> None:
    patch_hooks(live_root / "SideStoreSupport" / "SideStoreHooks.m")
    patch_bootstrap(live_root / "LiveContainer" / "LCBootstrap.m")
    patch_database(sidestore_root / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: patch_embedded_sidestore_startup.py LIVE_CONTAINER_ROOT SIDESTORE_ROOT")
    patch(Path(sys.argv[1]), Path(sys.argv[2]))
