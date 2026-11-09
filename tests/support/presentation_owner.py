"""Materialize only verified owner inputs for disposable rendering mutations."""
from tests.owner_source import owner_source

PATHS = (
    "LiveContainerSwiftUI/Views/V3UnifiedShell.swift",
    "SideStoreSupport/SideStore.swift",
    "LiveContainerSwiftUI/Utilities/Shared.swift",
    "LiveContainerSwiftUI/Utilities/V3Localization.swift",
    "LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift",
    "Resources/Localizable.xcstrings",
)


def materialize_host(root):
    for relative in PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(owner_source(relative))
    return root / PATHS[0]
