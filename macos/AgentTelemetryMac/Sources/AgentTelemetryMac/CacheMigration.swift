import Foundation

enum CacheMigrationError: LocalizedError {
    case cacheMissing
    case cacheUnsupported
    case destinationHasData
    case importFailed

    var errorDescription: String? {
        switch self {
        case .cacheMissing:
            return "No AgentTelemetry usage cache was found in that folder."
        case .cacheUnsupported:
            return "The usage cache or device-sharing data could not be read."
        case .destinationHasData:
            return "This app already has local data. Import did not overwrite it."
        case .importFailed:
            return "Import could not be completed. The original files were left unchanged."
        }
    }
}

enum CacheMigration {
    static func importData(from source: URL, to destination: URL) throws {
        let fileManager = FileManager.default
        let source = source.standardizedFileURL
        let sourceValues = try source.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard sourceValues.isDirectory == true, sourceValues.isSymbolicLink != true else {
            throw CacheMigrationError.cacheUnsupported
        }
        let cacheURL = source.appendingPathComponent(".usage_cache.json")
        guard fileManager.fileExists(atPath: cacheURL.path) else {
            throw CacheMigrationError.cacheMissing
        }

        do {
            try validateRegularFile(cacheURL)
            let cacheData = try Data(contentsOf: cacheURL, options: .mappedIfSafe)
            try validateLedger(cacheData)

            let peerManifest = source.appendingPathComponent(".peers.json")
            let peerDirectory = source.appendingPathComponent(".peers", isDirectory: true)
            if fileManager.fileExists(atPath: peerManifest.path) {
                try validateRegularFile(peerManifest)
                let peerData = try Data(contentsOf: peerManifest, options: .mappedIfSafe)
                guard (try JSONSerialization.jsonObject(with: peerData)) is [String: Any] else {
                    throw CacheMigrationError.cacheUnsupported
                }
            }
            if fileManager.fileExists(atPath: peerDirectory.path) {
                try validateTree(peerDirectory)
            }

            let destination = destination.standardizedFileURL
            if fileManager.fileExists(atPath: destination.path) {
                let existing = try fileManager.contentsOfDirectory(atPath: destination.path)
                guard existing.isEmpty else { throw CacheMigrationError.destinationHasData }
            }

            let parent = destination.deletingLastPathComponent()
            try fileManager.createDirectory(at: parent, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
            let staging = parent.appendingPathComponent(
                ".AgentTelemetry-import-\(UUID().uuidString)", isDirectory: true)
            try fileManager.createDirectory(at: staging, withIntermediateDirectories: false,
                                            attributes: [.posixPermissions: 0o700])
            do {
                try copySecureFile(cacheURL, to: staging.appendingPathComponent(".usage_cache.json"))
                if fileManager.fileExists(atPath: peerManifest.path) {
                    try copySecureFile(peerManifest, to: staging.appendingPathComponent(".peers.json"))
                }
                if fileManager.fileExists(atPath: peerDirectory.path) {
                    try copySecureTree(peerDirectory, to: staging.appendingPathComponent(".peers", isDirectory: true))
                }

                if fileManager.fileExists(atPath: destination.path) {
                    let existing = try fileManager.contentsOfDirectory(atPath: destination.path)
                    guard existing.isEmpty else { throw CacheMigrationError.destinationHasData }
                    try fileManager.removeItem(at: destination)
                }
                try fileManager.moveItem(at: staging, to: destination)
            } catch {
                try? fileManager.removeItem(at: staging)
                if error is CacheMigrationError { throw error }
                throw CacheMigrationError.importFailed
            }
        } catch let error as CacheMigrationError {
            throw error
        } catch {
            throw CacheMigrationError.cacheUnsupported
        }
    }

    private static func validateLedger(_ data: Data) throws {
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let files = root["files"] as? [String: Any],
              files.values.allSatisfy({ $0 is [String: Any] }) else {
            throw CacheMigrationError.cacheUnsupported
        }
    }

    private static func validateRegularFile(_ file: URL) throws {
        let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else {
            throw CacheMigrationError.cacheUnsupported
        }
    }

    private static func validateTree(_ directory: URL) throws {
        let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else {
            throw CacheMigrationError.cacheUnsupported
        }
        for child in try FileManager.default.contentsOfDirectory(at: directory,
                                                                 includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey, .isRegularFileKey]) {
            let childValues = try child.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey, .isRegularFileKey])
            guard childValues.isSymbolicLink != true else { throw CacheMigrationError.cacheUnsupported }
            if childValues.isDirectory == true { try validateTree(child) }
            else {
                guard childValues.isRegularFile == true else { throw CacheMigrationError.cacheUnsupported }
            }
        }
    }

    private static func copySecureFile(_ source: URL, to destination: URL) throws {
        try validateRegularFile(source)
        try FileManager.default.copyItem(at: source, to: destination)
        try FileManager.default.setAttributes([.posixPermissions: 0o600],
                                               ofItemAtPath: destination.path)
    }

    private static func copySecureTree(_ source: URL, to destination: URL) throws {
        let fileManager = FileManager.default
        try fileManager.createDirectory(at: destination, withIntermediateDirectories: false,
                                        attributes: [.posixPermissions: 0o700])
        for child in try fileManager.contentsOfDirectory(at: source,
                                                         includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey, .isRegularFileKey]) {
            let target = destination.appendingPathComponent(child.lastPathComponent)
            let values = try child.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey, .isRegularFileKey])
            guard values.isSymbolicLink != true else { throw CacheMigrationError.cacheUnsupported }
            if values.isDirectory == true {
                try copySecureTree(child, to: target)
            } else {
                guard values.isRegularFile == true else { throw CacheMigrationError.cacheUnsupported }
                try copySecureFile(child, to: target)
            }
        }
    }
}
