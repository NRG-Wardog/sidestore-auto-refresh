    public func resolveDeviceIdentifier() -> UUID {
        if let storedId = anisetteIdentifier, !storedId.isEmpty {
            if let parsed = UUID(uuidString: storedId) {
                return parsed
            }
            if let data = Data(base64Encoded: storedId), data.count == 16 {
                let uuid = data.withUnsafeBytes { UUID(uuid: $0.load(as: uuid_t.self)) }
                return uuid
            }
        }
        let generated = UUID()
        anisetteIdentifier = generated.uuidString
        return generated
    }