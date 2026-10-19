"""Shared genuine localization closure for bounded simulator rendering fixtures."""
from pathlib import Path
import hashlib
import json
import re

LOCALIZATION_SOURCES = (
    "LiveContainerSwiftUI/Utilities/V3Localization.swift",
    "LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift",
)
LOCALIZATION_CATALOG = "Resources/Localizable.xcstrings"
LOCALIZATION_LANGUAGES = ("en", "he", "zh-Hans", "zh-Hant")
LOCALIZATION_KEYS = (
    "lc.appBanner.run", "lc.appBanner.noDataFolder", "Connected", "Reloading Status...",
    "Reload Status", "Updated %@", "Status last updated %@", "SideStore status: %@, %@",
    "Loading apps...", "No sideloaded apps",
)


def localization_sources(live: Path, consumers: list[Path]) -> list[Path]:
    """Close genuine production dependencies; retain the older unlocalized fixture."""
    sources = [live / relative for relative in LOCALIZATION_SOURCES]
    required = any(re.search(r"\bV3(?:AppLanguage|Localization|LocalizedText|LocalizedLabel)\b",
                             path.read_text()) for path in consumers)
    if not required and not any(path.exists() for path in sources):
        return []
    for path in [*sources, live / LOCALIZATION_CATALOG]:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Production localization dependency missing or linked: {path}")
    return sources


def compile_localization(bundle: Path, live: Path, command) -> dict[str, str]:
    """Use Xcode's real catalog compiler, preserving plural/substitution semantics."""
    catalog = live / LOCALIZATION_CATALOG
    command("xcrun", "xcstringstool", "compile", "--output-directory", str(bundle), str(catalog))
    return verify_localization(bundle, live, command)


def verify_localization(bundle: Path, live: Path, command,
                        keys=LOCALIZATION_KEYS) -> dict[str, str]:
    """Verify actual compiled resources against the exact production catalog."""
    catalog = live / LOCALIZATION_CATALOG
    resources = sorted(bundle.glob("*.lproj/Localizable.*"))
    entries = json.loads(catalog.read_text())["strings"]
    for language in LOCALIZATION_LANGUAGES:
        for name in ("Localizable.strings", "Localizable.stringsdict"):
            path = bundle / (language + ".lproj") / name
            if path not in resources or path.is_symlink() or path.parent.is_symlink() or not path.is_file() or not path.stat().st_size:
                raise RuntimeError(f"Compiled production localization resource missing: {path}")
        strings = json.loads(command("plutil", "-convert", "json", "-o", "-",
                                     str(bundle / (language + ".lproj") / "Localizable.strings")))
        for key in keys:
            expected = entries[key]["localizations"][language]["stringUnit"]["value"]
            if not isinstance(expected, str) or not expected or strings.get(key) != expected:
                raise RuntimeError(f"Compiled localization label differs: {language}/{key}")
    hashes = {LOCALIZATION_CATALOG: hashlib.sha256(catalog.read_bytes()).hexdigest()}
    for path in resources:
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise RuntimeError(f"Compiled localization resource is not a regular file: {path}")
        hashes["compiled-localization/" + str(path.relative_to(bundle))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


