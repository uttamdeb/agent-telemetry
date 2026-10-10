import Foundation

/// Local files let the web Settings switch observe and close an installed app,
/// including one the user opened from Finder. They never carry usage or codes.
@MainActor
final class NativeControl: ObservableObject {
    private var task: Task<Void, Never>?
    private var generation: String?
    private let root: URL
    let requestedFromDashboard: Bool

    init() {
        root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("AgentTelemetryNative", isDirectory: true)
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: "--native-generation"), arguments.indices.contains(index + 1) {
            generation = arguments[index + 1]
            requestedFromDashboard = true
        } else {
            requestedFromDashboard = false
        }
    }

    func start(quit: @escaping () -> Void) {
        guard task == nil else { return }
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                                     attributes: [.posixPermissions: 0o700])
            if Bundle.main.bundleURL.pathExtension == "app" {
                let path = Bundle.main.bundleURL.path
                try write("native-install.json", ["platform": "darwin", "entry": path,
                    "command": ["/usr/bin/open", "-a", path, "--args"],
                    "version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "development"])
            }
            if generation == nil {
                generation = UUID().uuidString
                try write("native-control.json", ["enabled": true, "generation": generation!])
            }
        } catch {
            // A broken control directory must not pretend the app can be managed.
            quit()
            return
        }
        task = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                guard let generation = self.generation,
                      let control = self.read("native-control.json"),
                      control["enabled"] as? Bool == true,
                      control["generation"] as? String == generation else {
                    quit()
                    return
                }
                do {
                    try self.write("native-status.json", ["generation": generation,
                        "pid": ProcessInfo.processInfo.processIdentifier,
                        "time": Date().timeIntervalSince1970])
                } catch {
                    quit()
                    return
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }

    func finish() {
        task?.cancel()
        guard let generation, let control = read("native-control.json"),
              control["generation"] as? String == generation else { return }
        do {
            try write("native-control.json", ["enabled": false, "generation": generation])
            try write("native-status.json", [:])
        } catch {
            // Its last heartbeat will expire even if the disk stopped being writable.
            fputs("[native] Could not clear native app status\n", stderr)
        }
    }

    private func read(_ name: String) -> [String: Any]? {
        guard let data = try? Data(contentsOf: root.appendingPathComponent(name)) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    private func write(_ name: String, _ value: [String: Any]) throws {
        let url = root.appendingPathComponent(name)
        try JSONSerialization.data(withJSONObject: value).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}
