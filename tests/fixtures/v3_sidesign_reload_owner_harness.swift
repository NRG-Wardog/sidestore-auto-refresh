import Foundation

@main
struct V3SideSignReloadOwnerHarness {
    static func main() {
        editSaveThenLateGetCannotReplaceSavedValue()
        dismissalRejectsLateGet()
        resetAndImportOwnTheirResultsUntilAUserEdit()
        print("V3_SIDESIGN_RELOAD_OWNER_PASS")
    }

    private static func editSaveThenLateGetCannotReplaceSavedValue() {
        var owners = V3AsyncRequestOwnerState()
        var editorRevision: UInt64 = 0
        let getA = owners.begin(bindingID: "sidesign-config-editor")

        // The editor's onChange invalidates the load when the user types.
        editorRevision &+= 1
        owners.invalidate()
        var config = "new user draft"
        let saveB = owners.begin(bindingID: "sidesign-config-editor")
        let saveRevision = editorRevision

        precondition(mayApply(saveB, owners: owners, captured: saveRevision, current: editorRevision))
        config = "saved canonical config"
        editorRevision &+= 1
        owners.invalidate()

        precondition(!mayApply(getA, owners: owners, captured: 0, current: editorRevision),
            "the initial GET cannot replace the saved result")
        precondition(config == "saved canonical config")
    }

    private static func dismissalRejectsLateGet() {
        var owners = V3AsyncRequestOwnerState()
        let getA = owners.begin(bindingID: "sidesign-config-editor")
        owners.invalidate() // .onDisappear
        precondition(!owners.owns(getA, bindingID: "sidesign-config-editor"),
            "a dismissed editor rejects its late load")
    }

    private static func resetAndImportOwnTheirResultsUntilAUserEdit() {
        for operation in ["reset", "import"] {
            var owners = V3AsyncRequestOwnerState()
            var editorRevision: UInt64 = 0
            let getA = owners.begin(bindingID: "sidesign-config-editor")
            let mutation = owners.begin(bindingID: "sidesign-config-editor")
            precondition(!owners.owns(getA, bindingID: "sidesign-config-editor"),
                "\(operation) invalidates a pending GET")
            precondition(mayApply(mutation, owners: owners, captured: editorRevision,
                current: editorRevision),
                "\(operation) owns its service result while the editor is unchanged")

            editorRevision &+= 1
            owners.invalidate()
            precondition(!mayApply(mutation, owners: owners, captured: 0, current: editorRevision),
                "a later edit remains visible over a late \(operation) result")
        }
    }

    private static func mayApply(_ owner: V3AsyncRequestOwner,
                                 owners: V3AsyncRequestOwnerState,
                                 captured: UInt64,
                                 current: UInt64) -> Bool {
        owners.owns(owner, bindingID: "sidesign-config-editor") && captured == current
    }
}
